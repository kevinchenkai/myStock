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
    from .db import get_ml_connection_readonly
    return get_ml_connection_readonly(db_path)


def load_daily(symbol_or_code: str, db_path: Optional[str] = None) -> pd.DataFrame:
    """日线 DataFrame，按 date 升序。symbol 可传 yf（NVDA）或富途（US.NVDA）。"""
    sym = futu_to_yf(symbol_or_code) if "." in symbol_or_code else symbol_or_code
    with _conn(db_path) as c:
        df = pd.read_sql_query(
            "SELECT date, open, high, low, close, adj_close, volume, dividends, splits, synced_at "
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


def complete_bars(code, bars, now=None):
    """Accept full regular-session coverage on exchange or Yahoo's anchored grid.

    HK Yahoo hourly buckets can straddle lunch (12:30 contains 13:00–13:30).
    Entirely inactive buckets are excluded; partial/missing sets stay unavailable.
    """
    from . import sessions
    from datetime import timedelta
    if not bars: return False
    day=bars[0].get('ts_et','')[:10]
    try:
        s=sessions.session(code,day)
        if not all(sessions.ohlc_ok(b) for b in bars):return False
        stamps=[sessions.utc(b['ts_utc'].replace(' ','T')+'+00:00') for b in bars]
        expected=[];t=s['open']
        while t<s['close']:
            end=min(t+timedelta(hours=1),s['close'])
            if not s.get('break_start') or not (t>=s['break_start'] and end<=s['break_end']):expected.append(t)
            t+=timedelta(hours=1)
        segmented=[]
        periods=[(s['open'],s['break_start']),(s['break_end'],s['close'])] if s.get('break_start') else [(s['open'],s['close'])]
        for t,end in periods:
            while t<end:segmented.append(t);t+=timedelta(hours=1)
        return len(stamps)==len(set(stamps)) and (set(stamps)==set(expected) or set(stamps)==set(segmented)) and sessions.utc(now or sessions.utc_now())>=s['final_at']
    except (sessions.Unavailable, ValueError, KeyError):return False
