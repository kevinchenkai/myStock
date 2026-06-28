"""ML 数据采集（独立管线，不并入 scripts/update.sh）。

抓取：
  1. 5 年日线（ml_quotes_1d）
  2. 2 年 1 小时线（ml_quotes_1h）
  3. 生产库 deals/orders/positions 只读快照（ml_deals/ml_orders/ml_positions）

仅 docs/ML_PLAN.md §1.5 锁定的 3 支美股：US.NVDA / US.TSLA / US.PDD。
运行：python -m mystock.ml.fetch  或  bash scripts/ml_fetch.sh
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from ..code_map import futu_market_of, futu_to_yf
from . import config as mlcfg
from . import db as mldb

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None


def _require_yf() -> None:
    if yf is None:
        raise RuntimeError(
            "未安装 yfinance。请 `conda activate mystock-ml`（H20）或 `mk`（本机）后安装。"
        )


# ---------------------------------------------------------------------------
# 1) 日线
# ---------------------------------------------------------------------------
def fetch_daily(futu_code: str, now: str, max_retries: int = 3,
                period: str | None = None) -> list[dict]:
    """抓单标的日线 → ml_quotes_1d 行。period 默认 5 年，增量时传短窗（如 '1mo'）。"""
    _require_yf()
    sym = futu_to_yf(futu_code)
    df = _yf_history(sym, period=period or mlcfg.DAILY_PERIOD, interval="1d",
                     max_retries=max_retries)
    if df is None or df.empty:
        return []
    df = df.reset_index()
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    rows = []
    for _, r in df.iterrows():
        row = {
            "symbol": sym,
            "futu_code": futu_code,
            "date": pd.to_datetime(r[date_col]).strftime("%Y-%m-%d"),
            "open": _f(r, "Open"),
            "high": _f(r, "High"),
            "low": _f(r, "Low"),
            "close": _f(r, "Close"),
            "adj_close": _f(r, "Adj Close"),
            "volume": _f(r, "Volume"),
            "dividends": _f(r, "Dividends"),
            "splits": _f(r, "Stock Splits"),
            "synced_at": now,
        }
        if _ohlc_ok(row):   # 丢弃 NaN/不完整行（防脏数据进库污染回测）
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 2) 1 小时线
# ---------------------------------------------------------------------------
def fetch_hourly(futu_code: str, now: str, max_retries: int = 3,
                 period: str | None = None) -> list[dict]:
    """抓单标的 1h → ml_quotes_1h 行。period 默认约 2 年，增量时传短窗（如 '5d'）。"""
    _require_yf()
    sym = futu_to_yf(futu_code)
    df = _yf_history(sym, period=period or mlcfg.HOURLY_PERIOD, interval="60m",
                     max_retries=max_retries)
    if df is None or df.empty:
        return []
    df = df.reset_index()
    ts_col = "Datetime" if "Datetime" in df.columns else df.columns[0]
    # 交易所本地时区：港股 Asia/Hong_Kong，美股 America/New_York。
    # ts_et 存交易所本地时间 → 模拟器按 ts_et[:10] 分交易日才正确（HK 09:30≠NY 日期）。
    local_tz = "Asia/Hong_Kong" if futu_market_of(futu_code) == "HK" else "America/New_York"
    rows = []
    for _, r in df.iterrows():
        ts = pd.to_datetime(r[ts_col])
        ts_utc = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
        ts_local = ts.tz_convert(local_tz) if ts.tzinfo else ts
        row = {
            "symbol": sym,
            "futu_code": futu_code,
            "ts_utc": ts_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "ts_et": ts_local.strftime("%Y-%m-%d %H:%M:%S"),
            "open": _f(r, "Open"),
            "high": _f(r, "High"),
            "low": _f(r, "Low"),
            "close": _f(r, "Close"),
            "volume": _f(r, "Volume"),
            "synced_at": now,
        }
        if _ohlc_ok(row):   # 丢弃 NaN/不完整行（同上，1h 撮合 bars 也须干净）
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 3) 生产库交易事实只读快照
# ---------------------------------------------------------------------------
def snapshot_prod_facts(conn, now: str) -> dict[str, int]:
    """从生产库（只读）拷贝 3 支标的的 deals/orders/positions 进 ML 库。"""
    codes = mlcfg.TARGETS
    placeholders = ", ".join("?" for _ in codes)
    counts: dict[str, int] = {}

    with mldb.get_prod_connection_readonly() as prod:
        # deals
        deals = [dict(r) for r in prod.execute(
            f"SELECT deal_id, order_id, market, code, name, trd_side, price, qty, create_time "
            f"FROM deals WHERE code IN ({placeholders})", codes)]
        for d in deals:
            d["snapshot_taken_at"] = now
        counts["ml_deals"] = mldb.upsert(conn, "ml_deals", deals)

        # orders
        orders = [dict(r) for r in prod.execute(
            f"SELECT order_id, market, code, name, trd_side, order_status, price, qty, "
            f"dealt_qty, dealt_avg_price, create_time, updated_time "
            f"FROM orders WHERE code IN ({placeholders})", codes)]
        for o in orders:
            o["snapshot_taken_at"] = now
        counts["ml_orders"] = mldb.upsert(conn, "ml_orders", orders)

        # positions
        positions = [dict(r) for r in prod.execute(
            f"SELECT snapshot_date, market, code, name, qty, can_sell_qty, cost_price, "
            f"nominal_price, pl_ratio FROM positions WHERE code IN ({placeholders})", codes)]
        for p in positions:
            p["snapshot_taken_at"] = now
        counts["ml_positions"] = mldb.upsert(conn, "ml_positions", positions)

    for src, n in (("prod_deals", counts["ml_deals"]),
                   ("prod_orders", counts["ml_orders"]),
                   ("prod_positions", counts["ml_positions"])):
        mldb.log_sync(conn, src, row_count=n, message=f"{n} rows snapshotted")
    return counts


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _yf_history(symbol: str, *, period: str, interval: str, max_retries: int):
    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            return yf.Ticker(symbol).history(
                period=period, interval=interval, auto_adjust=False
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < max_retries:
                time.sleep(1.0 * attempt)
    raise RuntimeError(f"抓取 {symbol} {interval}/{period} 失败: {last_err}")


def _f(row, col):
    if col in row and pd.notna(row[col]):
        return float(row[col])
    return None


def _ohlc_ok(row: dict) -> bool:
    """OHLC 四价齐全且为正才算可用行。

    yfinance 偶发返回 NaN 行（最常见：盘后/周末重拉时撞上当日尚未结算的最新
    bar，美股收盘 16:00 ET ≈ 次日北京清晨，周末 cron 易踩）。这类行 _f() 会转成
    None；若写进库，回测 mark_next 取到 NaN → 净值曲线整条被污染 → 报告总览显示
    nan。故在采集层就地丢弃——宁可当天少一根，也不让脏行进库（DATA.md §4）。
    """
    for k in ("open", "high", "low", "close"):
        v = row.get(k)
        if v is None or v != v or v <= 0:   # None / NaN / 非正
            return False
    return True


def _latest_date(conn, sym: str) -> str | None:
    """ML 库中该标的日线的最新日期（无则 None）。用于判断增量。"""
    cur = conn.execute("SELECT MAX(date) FROM ml_quotes_1d WHERE symbol=?", (sym,))
    r = cur.fetchone()
    return r[0] if r and r[0] else None


def _is_fresh(latest: str | None, max_gap_days: int = 5) -> bool:
    """库中最新日期距今 ≤ max_gap_days（含周末缓冲）→ 视为有数据、走增量。"""
    if not latest:
        return False
    from datetime import date
    try:
        y, m, d = map(int, latest.split("-"))
        return (date.today() - date(y, m, d)).days <= max_gap_days
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(full: bool = False) -> None:
    """采集。默认增量：库中已有近期数据则只抓短窗（省网络）；首次或 full=True 抓全量。
    UPSERT 幂等，增量短窗 + 全量历史在库内自然合并。
    强制全量：python -m mystock.ml.fetch --full  或  run(full=True)。
    """
    print("== myStock ML fetch ==" + ("（全量）" if full else "（增量优先）"))
    mldb.init_ml_db()
    conn = mldb.get_ml_connection()
    now = mldb.now_str()
    try:
        for code in mlcfg.TARGETS:
            sym = futu_to_yf(code)
            incremental = (not full) and _is_fresh(_latest_date(conn, sym))
            d_period = "1mo" if incremental else None   # None=全量(5y/2y)
            h_period = "5d" if incremental else None
            tag = "增量" if incremental else "全量"
            # 日线
            try:
                rows = fetch_daily(code, now, period=d_period)
                n = mldb.upsert(conn, "ml_quotes_1d", rows)
                rng = (rows[0]["date"], rows[-1]["date"]) if rows else ("", "")
                mldb.log_sync(conn, "yf_1d", symbol=sym, range_start=rng[0],
                              range_end=rng[1], row_count=n, message=f"{tag} {n} daily rows")
                print(f"  [1d/{tag}] {sym}: {n} rows {rng[0]}..{rng[1]}")
            except Exception as e:  # noqa: BLE001
                mldb.log_sync(conn, "yf_1d", symbol=sym, status="error", message=str(e))
                print(f"  [1d] {sym}: ERROR {e}")

            # 1h
            try:
                rows = fetch_hourly(code, now, period=h_period)
                n = mldb.upsert(conn, "ml_quotes_1h", rows)
                rng = (rows[0]["ts_utc"], rows[-1]["ts_utc"]) if rows else ("", "")
                mldb.log_sync(conn, "yf_1h", symbol=sym, range_start=rng[0],
                              range_end=rng[1], row_count=n, message=f"{tag} {n} hourly rows")
                print(f"  [1h/{tag}] {sym}: {n} rows {rng[0]}..{rng[1]}")
            except Exception as e:  # noqa: BLE001
                mldb.log_sync(conn, "yf_1h", symbol=sym, status="error", message=str(e))
                print(f"  [1h] {sym}: ERROR {e}")

        # 交易事实快照（只读生产库）
        if mlcfg.PROD_DB_PATH.exists():
            counts = snapshot_prod_facts(conn, now)
            print(f"  [prod] deals={counts['ml_deals']} orders={counts['ml_orders']} "
                  f"positions={counts['ml_positions']}")
        else:
            print(f"  [prod] 跳过：生产库 {mlcfg.PROD_DB_PATH} 不存在（H20 上无需）")
    finally:
        conn.close()
    print(f"完成 → {mlcfg.ML_DB_PATH}")


if __name__ == "__main__":
    import sys
    run(full="--full" in sys.argv)
