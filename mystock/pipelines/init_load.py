"""全量初始化入口（init.sh 调用）。

流程：
  1) 建库建表。
  2) 富途：持仓 / 历史订单 / 历史成交（start_date 至今）。
  3) yfinance：持仓 + 交易出现过的全部代码的日线（start_date 至今）。
  4) 写 sync_log。

幂等：可重复执行。单标的行情失败不中断整体。
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

from .. import db
from ..config import CONFIG
from ..collectors import futu_client as fc
from ..collectors import yf_client as yc

# 批量抓取 yfinance 时，标的之间的节流间隔（秒）。
# yfinance 按 IP 限频，密集连发易触发「Too Many Requests」。这里在每次
# 请求前小睡一下，把请求摊开，显著降低撞限频的概率。第一个标的不睡。
YF_THROTTLE_SEC = 0.5

# 富途资金流向的历史深度：日频只提供近 1 年（实测 HK 237 / US 243 个交易日）。
# 回补起点早于此没有意义，统一抬到这个边界。留 5 天余量。
CAPITAL_FLOW_MAX_DAYS = 370


def _now() -> str:
    return db.now_str()


def collect_positions(conn) -> None:
    snapshot_date = db.today_str()
    now = _now()
    total = 0
    try:
        all_rows = []
        for market in CONFIG.markets:
            with fc.FutuClient(market) as client:
                dfp = client.query_positions()
            if not dfp.empty:
                all_rows.extend(
                    fc.position_rows(dfp, market, snapshot_date, now)
                )
        db.replace_position_snapshot(conn, snapshot_date, all_rows)
        total = len(all_rows)
        db.write_sync_log(conn, "futu_position", snapshot_date, snapshot_date, total, "ok")
        print(f"[positions] 写入 {total} 条（快照 {snapshot_date}）")
    except Exception as e:  # noqa: BLE001
        db.write_sync_log(conn, "futu_position", snapshot_date, snapshot_date, total, "error", str(e))
        print(f"[positions] 失败: {e}", file=sys.stderr)


def collect_account_funds(conn) -> None:
    """账户资金每日快照（accinfo_query）。

    账户为 HK+US 综合账户，只查一次（用首个配置市场的上下文即可），
    与 positions 一样每天覆盖一条快照。OpenD 未开 / 查询失败不中断整体。
    """
    snapshot_date = db.today_str()
    now = _now()
    market = CONFIG.markets[0] if CONFIG.markets else "HK"
    try:
        with fc.FutuClient(market) as client:
            dff = client.query_funds()
        row = fc.fund_row(dff, snapshot_date, now)
        if row is None:
            db.write_sync_log(conn, "futu_funds", snapshot_date, snapshot_date, 0, "ok", "no data")
            print("[funds] 无账户资金数据，跳过")
            return
        db.upsert_account_funds(conn, [row])
        db.write_sync_log(conn, "futu_funds", snapshot_date, snapshot_date, 1, "ok")
        ta = row.get("total_assets")
        cur = row.get("report_currency") or ""
        print(f"[funds] 写入账户快照（{snapshot_date}）总资产 {ta:,.2f} {cur}"
              if ta is not None else f"[funds] 写入账户快照（{snapshot_date}）")
    except Exception as e:  # noqa: BLE001
        db.write_sync_log(conn, "futu_funds", snapshot_date, snapshot_date, 0, "error", str(e))
        print(f"[funds] 失败: {e}", file=sys.stderr)


def collect_orders(conn, start: str, end: str) -> None:
    now = _now()
    total = 0
    try:
        for market in CONFIG.markets:
            with fc.FutuClient(market) as client:
                dfo = client.query_orders(start, end)
            if not dfo.empty:
                rows = fc.order_rows(dfo, market, now)
                total += db.upsert_orders(conn, rows)
        db.write_sync_log(conn, "futu_order", start, end, total, "ok")
        print(f"[orders] UPSERT {total} 条")
    except Exception as e:  # noqa: BLE001
        db.write_sync_log(conn, "futu_order", start, end, total, "error", str(e))
        print(f"[orders] 失败: {e}", file=sys.stderr)


def collect_deals(conn, start: str, end: str) -> None:
    now = _now()
    total = 0
    try:
        for market in CONFIG.markets:
            with fc.FutuClient(market) as client:
                dfd = client.query_deals(start, end)
            if not dfd.empty:
                rows = fc.deal_rows(dfd, market, now)
                total += db.upsert_deals(conn, rows)
        db.write_sync_log(conn, "futu_deal", start, end, total, "ok")
        print(f"[deals] UPSERT {total} 条")
    except Exception as e:  # noqa: BLE001
        db.write_sync_log(conn, "futu_deal", start, end, total, "error", str(e))
        print(f"[deals] 失败: {e}", file=sys.stderr)


def collect_quotes(conn, start: str, end: str) -> None:
    now = _now()
    all_codes = db.all_traded_codes(conn)
    if not all_codes:
        print("[quotes] 无可抓取的代码（positions/orders/deals 均为空），跳过")
        db.write_sync_log(conn, "yfinance", start, end, 0, "ok", "no codes")
        return

    # 跳过名单：连续多次抓空（退市/无数据）的代码不再请求
    skip = db.get_quote_skiplist(conn)
    codes = [c for c in all_codes if c not in skip]
    if skip:
        print(f"[quotes] 跳过 {len(skip)} 个已知无行情代码: {', '.join(sorted(skip))}")

    grand_total = 0
    ok_codes = 0
    empty_codes = 0
    err_codes = 0
    print(f"[quotes] 准备抓取 {len(codes)} 个标的的日线…")
    for i, code in enumerate(codes):
        if i:
            time.sleep(YF_THROTTLE_SEC)  # 标的间节流，缓解 yfinance 限频
        try:
            rows = yc.fetch_daily(code, start=start, end=end, now=now)
            if not rows:
                # 抓到空数据：计数 +1，达阈值后自动进入跳过名单
                cnt = db.record_quote_empty(conn, code, yc.futu_to_yf(code))
                empty_codes += 1
                hint = "（已加入跳过名单）" if cnt >= db.SKIP_THRESHOLD else f"（空 {cnt} 次）"
                print(f"  · {code}: 无数据{hint}")
                continue
            n = db.upsert_quotes(conn, rows)
            grand_total += n
            ok_codes += 1
            db.clear_quote_skip(conn, code)  # 重新有数据则移出名单
            print(f"  ✓ {code}: {n} 条")
        except Exception as e:  # noqa: BLE001
            # 真正的异常（网络/解析等）才记 error，单标的失败不中断整体
            err_codes += 1
            db.write_sync_log(conn, "yfinance", start, end, 0, "error", f"{code}: {e}")
            print(f"  ✗ {code}: {e}", file=sys.stderr)

    msg = f"{ok_codes} ok / {empty_codes} empty / {err_codes} err / {len(skip)} skipped"
    db.write_sync_log(conn, "yfinance", start, end, grand_total, "ok", msg)
    print(f"[quotes] 完成：{msg}，共 {grand_total} 条")


def collect_profiles(conn) -> None:
    """抓取全部持仓/交易代码的通用信息（公司/估值），UPSERT 入库。

    随每日 update 刷新。复用行情跳过名单（退市/无数据代码不再请求）。
    单标的失败不中断整体。
    """
    now = _now()
    all_codes = db.all_traded_codes(conn)
    if not all_codes:
        db.write_sync_log(conn, "yf_profile", None, None, 0, "ok", "no codes")
        print("[profiles] 无可抓取的代码，跳过")
        return

    skip = db.get_quote_skiplist(conn)
    codes = [c for c in all_codes if c not in skip]

    ok_codes = 0
    empty_codes = 0
    err_codes = 0
    print(f"[profiles] 准备抓取 {len(codes)} 个标的的通用信息…")
    for i, code in enumerate(codes):
        if i:
            time.sleep(YF_THROTTLE_SEC)  # 标的间节流，缓解 yfinance 限频
        try:
            row = yc.fetch_profile(code, now=now)
            if not row:
                empty_codes += 1
                print(f"  · {code}: 无资料")
                continue
            db.upsert_profiles(conn, [row])
            ok_codes += 1
            print(f"  ✓ {code}: {row.get('long_name') or ''}")
        except Exception as e:  # noqa: BLE001
            err_codes += 1
            print(f"  ✗ {code}: {e}", file=sys.stderr)

    msg = f"{ok_codes} ok / {empty_codes} empty / {err_codes} err / {len(skip)} skipped"
    db.write_sync_log(conn, "yf_profile", None, None, ok_codes, "ok", msg)
    print(f"[profiles] 完成：{msg}")


def collect_market_snapshot(conn) -> None:
    """富途行情快照的盘面增量字段（换手率/振幅/52 周高低），合并进 stock_profiles。

    一次批量取全（标的数 << 400 上限）。OpenD 未开 / 查询失败不中断整体，
    仅记 sync_log。仅更新盘面列，不影响 yfinance 写的公司/估值字段。
    """
    now = _now()
    all_codes = db.all_traded_codes(conn)
    if not all_codes:
        db.write_sync_log(conn, "futu_snapshot", None, None, 0, "ok", "no codes")
        print("[snapshot] 无可抓取的代码，跳过")
        return
    try:
        df = fc.fetch_snapshots(all_codes)
        rows = fc.snapshot_fields(df, now)
        n = db.upsert_profiles(conn, rows) if rows else 0
        db.write_sync_log(conn, "futu_snapshot", None, None, n, "ok")
        print(f"[snapshot] 盘面字段 UPSERT {n} 条（换手率/振幅/52 周高低）")
    except Exception as e:  # noqa: BLE001
        db.write_sync_log(conn, "futu_snapshot", None, None, 0, "error", str(e))
        print(f"[snapshot] 失败: {e}", file=sys.stderr)


def collect_capital_flow(conn, start: str, end: str) -> None:
    """富途日频资金流向（主力/超大/大/中/小单净流入），逐只抓取入库。

    富途只提供近 1 年日频历史：早于 CAPITAL_FLOW_MAX_DAYS 的 start 会被抬到
    该边界（再早也拿不到，白跑一趟）。限频 30s/30 次 → 标的间垫间隔。
    单只失败不中断整体；OpenD 未开时整体跳过并记 sync_log。
    """
    now = _now()
    all_codes = db.all_traded_codes(conn)
    if not all_codes:
        db.write_sync_log(conn, "futu_capflow", start, end, 0, "ok", "no codes")
        print("[capflow] 无可抓取的代码，跳过")
        return

    # 抬到富途能给的最早日期，避免请求注定为空的区间
    floor = (datetime.now() - timedelta(days=CAPITAL_FLOW_MAX_DAYS)).strftime("%Y-%m-%d")
    eff_start = max(start[:10], floor)

    grand_total = 0
    ok_codes = 0
    empty_codes = 0
    err_codes = 0
    print(f"[capflow] 准备抓取 {len(all_codes)} 个标的的资金流向（{eff_start} ~ {end}）…")
    try:
        with fc.quote_ctx() as ctx:
            for i, code in enumerate(all_codes):
                if i:
                    time.sleep(fc.CAPITAL_FLOW_INTERVAL)  # 限频 30s/30 次
                try:
                    df = fc.fetch_capital_flow(ctx, code, eff_start, end)
                    rows = fc.capital_flow_rows(df, code, now)
                    if not rows:
                        empty_codes += 1
                        print(f"  · {code}: 无数据")
                        continue
                    n = db.upsert_capital_flow(conn, rows)
                    grand_total += n
                    ok_codes += 1
                    print(f"  ✓ {code}: {n} 条")
                except Exception as e:  # noqa: BLE001
                    err_codes += 1
                    print(f"  ✗ {code}: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        # 连不上 OpenD（行情上下文都开不了）→ 整体跳过
        db.write_sync_log(conn, "futu_capflow", eff_start, end, grand_total, "error", str(e))
        print(f"[capflow] 失败: {e}", file=sys.stderr)
        return

    msg = f"{ok_codes} ok / {empty_codes} empty / {err_codes} err"
    db.write_sync_log(conn, "futu_capflow", eff_start, end, grand_total, "ok", msg)
    print(f"[capflow] 完成：{msg}，共 {grand_total} 条")


def collect_fx(conn, start: str, end: str, pair: str = "USDCNY",
               yf_symbol: str = "CNY=X") -> None:
    """抓取美元-人民币（或其它）汇率日线，UPSERT 入库。

    随每日 update 刷新（当天覆盖）。失败不中断整体。
    """
    now = _now()
    source = f"fx_{pair.lower()}"
    try:
        rows = yc.fetch_fx(yf_symbol=yf_symbol, pair=pair, start=start, end=end, now=now)
        n = db.upsert_fx_rates(conn, rows)
        db.write_sync_log(conn, source, start, end, n, "ok")
        print(f"[fx] {pair} UPSERT {n} 条（{start} ~ {end}）")
    except Exception as e:  # noqa: BLE001
        db.write_sync_log(conn, source, start, end, 0, "error", str(e))
        print(f"[fx] {pair} 失败: {e}", file=sys.stderr)


def run() -> int:
    start = CONFIG.start_date
    end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    end_date = datetime.now().strftime("%Y-%m-%d")

    print("=" * 50)
    print(f"myStock 全量初始化  区间 {start} ~ {end_date}")
    print(f"市场: {', '.join(CONFIG.markets)}  数据库: {CONFIG.db_path}")
    print("=" * 50)

    db.init_db()
    conn = db.get_connection()
    try:
        collect_positions(conn)
        collect_account_funds(conn)
        collect_orders(conn, start, end)
        collect_deals(conn, start, end)
        # yfinance 用日期粒度
        collect_quotes(conn, start, end_date)
        # 美元-人民币汇率日线
        collect_fx(conn, start, end_date)
        # 通用信息（依赖 quotes 已更新的跳过名单，故放在最后）
        collect_profiles(conn)
        # 富途盘面增量字段，合并进 stock_profiles（yfinance 缺项）
        collect_market_snapshot(conn)
        # 富途日频资金流向（近 1 年一次性回补）
        collect_capital_flow(conn, start, end_date)
    finally:
        conn.close()

    print("初始化完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
