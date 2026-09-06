"""Overnight cross-market external daily bars (D1 of the overnight plan).

One source (yfinance), one series per HK stock: its own US-listed ADR. Every row carries
`available_at`, the UTC moment the information exists (the final confirmation time of that
US session), so downstream joins can be audited against a decision time. Written only by
`scripts/ml_experiments/fetch_external.py`; the production predictor does not read it.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from . import db as mldb
from . import sessions

# Pre-registered mapping (docs/plans/ml-overnight-plan_claude_20260906.md §2, §6).
EXTERNAL_BY_CODE: dict[str, str] = {
    'HK.00700': 'TCEHY',
    'HK.09988': 'BABA',
    'HK.01810': 'XIACY',
}
# Pre-registered control proxy for the thin XIACY series (D4): a liquid China-internet ETF.
EXTERNAL_ALT: dict[str, str] = {
    'HK.01810': 'KWEB',
}
US_REFERENCE = 'US.NVDA'   # any US code: only used to read the US calendar
PERIOD = '5y'
SOURCE = 'yf_external_1d'


def available_at_for(date: str) -> str:
    """UTC ISO time at which a US-session daily bar dated `date` is final."""
    return sessions.session(US_REFERENCE, date)['final_at'].isoformat()


def rows_from_history(df: pd.DataFrame, symbol: str, for_code: str, now: str) -> list[dict]:
    """Convert a yfinance history frame into ml_external_1d rows.

    Keeps only rows on the US calendar whose `available_at` is not after `now`; a bar that is
    not yet final can never enter the table.
    """
    current = sessions.utc(now)
    df = df.reset_index()
    date_col = 'Date' if 'Date' in df.columns else df.columns[0]
    rows = []
    for _, r in df.iterrows():
        day = pd.to_datetime(r[date_col]).strftime('%Y-%m-%d')
        try:
            avail = sessions.session(US_REFERENCE, day)['final_at']
        except sessions.Unavailable:
            continue   # not a US session: reject rather than guess
        if avail > current:
            continue
        row = dict(symbol=symbol, for_code=for_code, date=day,
                   open=_f(r, 'Open'), high=_f(r, 'High'), low=_f(r, 'Low'), close=_f(r, 'Close'),
                   adj_close=_f(r, 'Adj Close'), volume=_f(r, 'Volume'),
                   available_at=avail.isoformat(), synced_at=now, data_source='yfinance')
        if sessions.ohlc_ok(row):
            rows.append(row)
    return rows


def _f(row, col):
    try:
        v = float(row[col])
        return v if v == v else None
    except (KeyError, TypeError, ValueError):
        return None


def fetch(for_code: str, now: str, *, period: str = PERIOD, max_retries: int = 3, symbol: str | None = None) -> list[dict]:
    """Download the external history serving `for_code` (network); default symbol is its ADR."""
    from .fetch import _require_yf, _yf_history
    _require_yf()
    symbol = symbol or EXTERNAL_BY_CODE[for_code]
    df = _yf_history(symbol, period=period, interval='1d', max_retries=max_retries)
    if df is None or df.empty:
        return []
    return rows_from_history(df, symbol, for_code, now)


def load_external(for_code: str, db_path: Optional[str] = None, *, symbol: str | None = None) -> pd.DataFrame:
    """External rows serving `for_code` for one symbol (default its ADR), date ascending."""
    symbol = symbol or EXTERNAL_BY_CODE[for_code]
    with mldb.get_ml_connection_readonly(db_path) as c:
        df = pd.read_sql_query(
            'SELECT symbol, date, open, high, low, close, adj_close, volume, available_at, synced_at '
            'FROM ml_external_1d WHERE for_code=? AND symbol=? ORDER BY date', c, params=(for_code, symbol))
    return df
