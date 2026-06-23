"""ML 库读取与对齐（纯读，供 P1/P2 复用）。

提供：
  - load_daily(symbol): 日线 DataFrame（按 date 升序）
  - load_hourly(symbol): 1h DataFrame（ts_et 升序，附 day 列）
  - intraday_bars_by_day(symbol): {date -> [bar dict ...]}（盘中顺序）
  - load_deals(code): 真实成交（按时间升序）

所有价格保持 yfinance 原值；收益率/技术指标在 features.py 用 adj_close 计算。
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd

from . import config as mlcfg
from ..code_map import futu_to_yf


def _conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = str(db_path or mlcfg.ML_DB_PATH)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_daily(symbol_or_code: str, db_path: Optional[str] = None) -> pd.DataFrame:
    """日线 DataFrame，按 date 升序。symbol 可传 yf（NVDA）或富途（US.NVDA）。"""
    sym = futu_to_yf(symbol_or_code) if "." in symbol_or_code else symbol_or_code
    with _conn(db_path) as c:
        df = pd.read_sql_query(
            "SELECT date, open, high, low, close, adj_close, volume, dividends, splits "
            "FROM ml_quotes_1d WHERE symbol=? ORDER BY date",
            c, params=(sym,),
        )
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def load_hourly(symbol_or_code: str, db_path: Optional[str] = None) -> pd.DataFrame:
    """1h DataFrame，按 ts_et 升序，附 day（美东交易日）列。"""
    sym = futu_to_yf(symbol_or_code) if "." in symbol_or_code else symbol_or_code
    with _conn(db_path) as c:
        df = pd.read_sql_query(
            "SELECT ts_utc, ts_et, open, high, low, close, volume "
            "FROM ml_quotes_1h WHERE symbol=? ORDER BY ts_utc",
            c, params=(sym,),
        )
    df["day"] = df["ts_et"].str.slice(0, 10)
    return df


def intraday_bars_by_day(symbol_or_code: str, db_path: Optional[str] = None) -> dict[str, list[dict]]:
    """{day -> [ {ts_et, open, high, low, close, volume}... ] }，bar 按盘中时间升序。"""
    df = load_hourly(symbol_or_code, db_path)
    out: dict[str, list[dict]] = {}
    for day, g in df.groupby("day", sort=True):
        out[day] = g[["ts_et", "open", "high", "low", "close", "volume"]].to_dict("records")
    return out


def load_deals(code: str, db_path: Optional[str] = None) -> pd.DataFrame:
    """真实成交（ml_deals 快照），按 create_time 升序。code 为富途代码（US.NVDA）。"""
    with _conn(db_path) as c:
        df = pd.read_sql_query(
            "SELECT deal_id, order_id, code, trd_side, price, qty, create_time "
            "FROM ml_deals WHERE code=? ORDER BY create_time",
            c, params=(code,),
        )
    return df


def load_orders(code: str, db_path: Optional[str] = None) -> pd.DataFrame:
    """真实委托（ml_orders 快照），按 create_time 升序。"""
    with _conn(db_path) as c:
        df = pd.read_sql_query(
            "SELECT order_id, code, trd_side, order_status, price, qty, dealt_qty, "
            "dealt_avg_price, create_time, updated_time "
            "FROM ml_orders WHERE code=? ORDER BY create_time",
            c, params=(code,),
        )
    return df
