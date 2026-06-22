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
from datetime import datetime

from .. import db
from ..config import CONFIG
from ..collectors import futu_client as fc
from ..collectors import yf_client as yc


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
    for code in codes:
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
    for code in codes:
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
        collect_orders(conn, start, end)
        collect_deals(conn, start, end)
        # yfinance 用日期粒度
        collect_quotes(conn, start, end_date)
        # 通用信息（依赖 quotes 已更新的跳过名单，故放在最后）
        collect_profiles(conn)
    finally:
        conn.close()

    print("初始化完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
