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

from ..code_map import futu_to_yf
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
def fetch_daily(futu_code: str, now: str, max_retries: int = 3) -> list[dict]:
    """抓单标的 5 年日线 → ml_quotes_1d 行。"""
    _require_yf()
    sym = futu_to_yf(futu_code)
    df = _yf_history(sym, period=mlcfg.DAILY_PERIOD, interval="1d", max_retries=max_retries)
    if df is None or df.empty:
        return []
    df = df.reset_index()
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    rows = []
    for _, r in df.iterrows():
        rows.append({
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
        })
    return rows


# ---------------------------------------------------------------------------
# 2) 1 小时线
# ---------------------------------------------------------------------------
def fetch_hourly(futu_code: str, now: str, max_retries: int = 3) -> list[dict]:
    """抓单标的约 2 年 1h → ml_quotes_1h 行。时间戳转 UTC 存。"""
    _require_yf()
    sym = futu_to_yf(futu_code)
    df = _yf_history(sym, period=mlcfg.HOURLY_PERIOD, interval="60m", max_retries=max_retries)
    if df is None or df.empty:
        return []
    df = df.reset_index()
    ts_col = "Datetime" if "Datetime" in df.columns else df.columns[0]
    rows = []
    for _, r in df.iterrows():
        ts = pd.to_datetime(r[ts_col])
        # yfinance intraday 带时区（美东）；统一转 UTC 存，另存美东本地便于核对
        ts_utc = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
        ts_et = ts.tz_convert("America/New_York") if ts.tzinfo else ts
        rows.append({
            "symbol": sym,
            "futu_code": futu_code,
            "ts_utc": ts_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "ts_et": ts_et.strftime("%Y-%m-%d %H:%M:%S"),
            "open": _f(r, "Open"),
            "high": _f(r, "High"),
            "low": _f(r, "Low"),
            "close": _f(r, "Close"),
            "volume": _f(r, "Volume"),
            "synced_at": now,
        })
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


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run() -> None:
    print("== myStock ML fetch ==")
    mldb.init_ml_db()
    conn = mldb.get_ml_connection()
    now = mldb.now_str()
    try:
        for code in mlcfg.TARGETS:
            sym = futu_to_yf(code)
            # 日线
            try:
                rows = fetch_daily(code, now)
                n = mldb.upsert(conn, "ml_quotes_1d", rows)
                rng = (rows[0]["date"], rows[-1]["date"]) if rows else ("", "")
                mldb.log_sync(conn, "yf_1d", symbol=sym, range_start=rng[0],
                              range_end=rng[1], row_count=n, message=f"{n} daily rows")
                print(f"  [1d] {sym}: {n} rows {rng[0]}..{rng[1]}")
            except Exception as e:  # noqa: BLE001
                mldb.log_sync(conn, "yf_1d", symbol=sym, status="error", message=str(e))
                print(f"  [1d] {sym}: ERROR {e}")

            # 1h
            try:
                rows = fetch_hourly(code, now)
                n = mldb.upsert(conn, "ml_quotes_1h", rows)
                rng = (rows[0]["ts_utc"], rows[-1]["ts_utc"]) if rows else ("", "")
                mldb.log_sync(conn, "yf_1h", symbol=sym, range_start=rng[0],
                              range_end=rng[1], row_count=n, message=f"{n} hourly rows")
                print(f"  [1h] {sym}: {n} rows {rng[0]}..{rng[1]}")
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
    run()
