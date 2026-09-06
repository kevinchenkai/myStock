"""US pre-open quotes (pre-market price 30 minutes before the open) for the pre-open decision.

Historical rows come from yfinance hourly bars with prepost=True: the 08:00–09:00 ET bar's close
is the price at 09:00 ET. Live rows come from a Futu market snapshot taken at run time. Both
carry `available_at` = the snapshot moment, so the as-of join can be audited. Written only by
`scripts/ml_experiments/fetch_preopen.py`; the production predictor does not read it.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd

from . import db as mldb
from . import sessions

PREOPEN_MINUTES = 30
SOURCE_HISTORY = 'yfinance_1h'
SOURCE_LIVE = 'futu_snapshot'
SOURCE_LIVE_BACKUP = 'yfinance_live'
LIVE_PRIORITY = (SOURCE_LIVE, SOURCE_LIVE_BACKUP, SOURCE_HISTORY)
US_TARGETS = ['US.NVDA', 'US.TSLA', 'US.PDD']


def snapshot_time(code: str, date: str):
    """Pre-registered decision snapshot moment: 30 minutes before that session's open (UTC)."""
    return sessions.session(code, date)['open'] - timedelta(minutes=PREOPEN_MINUTES)


def rows_from_yf_hourly(df: pd.DataFrame, code: str, now: str) -> list[dict]:
    """Historical pre-open rows from a yfinance prepost hourly frame (tz-aware index).

    Uses only the bar starting 08:00 local (New York); its close is the price at 09:00 ET,
    which equals the snapshot moment. Rows whose snapshot moment is after `now` are dropped.
    """
    current = sessions.utc(now)
    if df is None or df.empty:
        return []
    idx = df.index
    if getattr(idx, 'tz', None) is None:
        raise ValueError('hourly frame must be timezone-aware')
    local = idx.tz_convert('America/New_York')
    rows = []
    for ts_local, (_, r) in zip(local, df.iterrows()):
        if ts_local.hour != 8:
            continue
        day = ts_local.strftime('%Y-%m-%d')
        try:
            avail = snapshot_time(code, day)
        except sessions.Unavailable:
            continue
        if avail > current:
            continue
        price = float(r['Close'])
        if not (np.isfinite(price) and price > 0):
            continue
        rows.append(dict(code=code, date=day, price=price, prev_close=None, available_at=avail.isoformat(),
                         source=SOURCE_HISTORY, source_ref=ts_local.isoformat(), synced_at=now))
    return rows


def fetch_history(code: str, now: str, *, period: str = '730d') -> list[dict]:
    """Download prepost hourly bars for `code` (network) and convert to pre-open rows."""
    from ..code_map import futu_to_yf
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError('yfinance unavailable') from e
    df = yf.Ticker(futu_to_yf(code)).history(period=period, interval='1h', prepost=True, auto_adjust=False)
    return rows_from_yf_hourly(df, code, now)


def rows_from_futu_snapshot(df: pd.DataFrame, now: str) -> list[dict]:
    """Live pre-open rows from a Futu get_market_snapshot frame taken at `now`.

    Requires a pre-market price column; refuses to fall back to last_price silently.
    """
    current = sessions.utc(now)
    rows = []
    for _, r in df.iterrows():
        code = str(r['code'])
        price = None
        for col in ('pre_market_price', 'pre_price'):
            if col in df.columns and pd.notna(r[col]):
                price = float(r[col])
                break
        if price is None or not price > 0:
            raise ValueError(f'{code}: pre-market price missing in snapshot')
        zone = 'America/New_York'
        day = current.astimezone(__import__('zoneinfo').ZoneInfo(zone)).date().isoformat()
        try:
            sessions.session(code, day)
        except sessions.Unavailable as e:
            raise ValueError(f'{code}: {day} is not a US session') from e
        rows.append(dict(code=code, date=day, price=price, prev_close=_num(r.get('prev_close_price')),
                         available_at=current.isoformat(), source=SOURCE_LIVE,
                         source_ref=str(r.get('update_time', '')), synced_at=now))
    return rows


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def load_preopen(code: str, db_path: Optional[str] = None, *, source: str = SOURCE_HISTORY) -> pd.DataFrame:
    with mldb.get_ml_connection_readonly(db_path) as c:
        return pd.read_sql_query('SELECT code, date, price, prev_close, available_at, source, source_ref, synced_at '
                                 'FROM ml_preopen_quotes WHERE code=? AND source=? ORDER BY date', c, params=(code, source))


def attach_preopen(df: pd.DataFrame, quotes: pd.DataFrame, code: str) -> pd.DataFrame:
    """pre_ret(as_of) = pre-open price on the target session / close(as_of) − 1 (pure as-of join).

    Only the quote dated exactly the target session and with available_at strictly before that
    session's decision deadline is used; anything else gives NaN (no information, fail closed).
    """
    out = df.copy()
    if quotes is None or len(quotes) == 0:
        out['pre_ret'] = np.nan
        return out
    q = {str(d): (float(p), sessions.utc(str(a))) for d, p, a in zip(quotes['date'], quotes['price'], quotes['available_at'])}
    values = []
    for as_of, close in zip(out['date'].astype(str), out['close']):
        try:
            target = sessions.next_session(code, as_of)
            deadline = sessions.session(code, target)['deadline']
        except sessions.Unavailable:
            values.append(np.nan)
            continue
        hit = q.get(target)
        if hit is None or hit[1] >= deadline or not (isinstance(close, (int, float)) and np.isfinite(close) and close > 0):
            values.append(np.nan)
        else:
            values.append(hit[0] / float(close) - 1.0)
    out['pre_ret'] = values
    return out


def fetch_live_yf(codes: list[str], now: str) -> list[dict]:
    """Backup live source: yfinance `info['preMarketPrice']` at `now` (network)."""
    from ..code_map import futu_to_yf
    import yfinance as yf
    import zoneinfo
    current = sessions.utc(now)
    day = current.astimezone(zoneinfo.ZoneInfo('America/New_York')).date().isoformat()
    rows = []
    for code in codes:
        sessions.session(code, day)   # fail closed on non-sessions
        info = yf.Ticker(futu_to_yf(code)).info or {}
        price = _num(info.get('preMarketPrice'))
        if price is None or not price > 0:
            raise ValueError(f'{code}: preMarketPrice missing from yfinance')
        rows.append(dict(code=code, date=day, price=price, prev_close=_num(info.get('previousClose')),
                         available_at=current.isoformat(), source=SOURCE_LIVE_BACKUP,
                         source_ref=str(info.get('preMarketTime', '')), synced_at=now))
    return rows


def load_preopen_any(code: str, db_path: Optional[str] = None, *, priority=LIVE_PRIORITY) -> pd.DataFrame:
    """Merge sources by date with the given priority (first wins); adds a `source` column."""
    frames = [load_preopen(code, db_path, source=src) for src in priority]
    merged = {}
    for df in frames:
        for _, r in df.iterrows():
            merged.setdefault(str(r['date']), r)
    if not merged:
        return pd.DataFrame(columns=['code', 'date', 'price', 'prev_close', 'available_at', 'source', 'source_ref', 'synced_at'])
    return pd.DataFrame([merged[k] for k in sorted(merged)]).reset_index(drop=True)
