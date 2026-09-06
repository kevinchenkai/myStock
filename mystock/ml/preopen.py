"""US pre-open quotes (pre-market price before the open) for the pre-open decision.

Historical rows: yfinance hourly bars with prepost=True; only the bar that starts exactly at
08:00 America/New_York is accepted and its close is the price at 09:00 ET (= the pre-registered
snapshot moment, open − 30 min). Live rows: Futu market snapshot (primary) and yfinance
`preMarketPrice` (backup), both captured at the same planned moment and both stored; each row
keeps the provider event time in `source_ref` and is validated against the target session's
pre-market window before it can be used. Selection among valid rows follows LIVE_PRIORITY.
Written only by `scripts/ml_experiments/fetch_preopen.py` / `mystock.ml.shadow`; the production
predictor does not read it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
import zoneinfo

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
NY = zoneinfo.ZoneInfo('America/New_York')
PREMARKET_START_HOUR = 4          # US pre-market opens 04:00 ET


def snapshot_time(code: str, date: str):
    """Pre-registered decision snapshot moment: 30 minutes before that session's open (UTC)."""
    return sessions.session(code, date)['open'] - timedelta(minutes=PREOPEN_MINUTES)


def _num(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _valid_price(p) -> bool:
    return p is not None and np.isfinite(p) and p > 0


def premarket_window(code: str, date: str):
    """(start, end) UTC of the pre-market period for that session: 04:00 ET .. open."""
    s = sessions.session(code, date)
    start = datetime.combine(datetime.fromisoformat(date).date(), datetime.min.time(), tzinfo=NY).replace(hour=PREMARKET_START_HOUR)
    return start.astimezone(timezone.utc), s['open']


def rows_from_yf_hourly(df: pd.DataFrame, code: str, now: str) -> list[dict]:
    """Historical pre-open rows from a yfinance prepost hourly frame (tz-aware index).

    Accepts only bars starting exactly 08:00:00 New York time on a US session; duplicates are
    rejected; rows whose snapshot moment is after `now` are dropped.
    """
    current = sessions.utc(now)
    if df is None or df.empty:
        return []
    idx = df.index
    if getattr(idx, 'tz', None) is None:
        raise ValueError('hourly frame must be timezone-aware')
    local = idx.tz_convert('America/New_York')
    rows, seen = [], set()
    for ts_local, (_, r) in zip(local, df.iterrows()):
        if (ts_local.hour, ts_local.minute, ts_local.second) != (8, 0, 0):
            continue
        day = ts_local.strftime('%Y-%m-%d')
        try:
            avail = snapshot_time(code, day)
        except sessions.Unavailable:
            continue
        if avail > current or day in seen:
            continue
        price = _num(r['Close'])
        if not _valid_price(price):
            continue
        seen.add(day)
        rows.append(dict(code=code, date=day, price=price, prev_close=None, available_at=avail.isoformat(),
                         source=SOURCE_HISTORY, source_ref=ts_local.isoformat(), synced_at=now))
    return rows


def fetch_history(code: str, now: str, *, period: str = '730d') -> list[dict]:
    """Download prepost hourly bars for `code` (network) and convert to pre-open rows."""
    from ..code_map import futu_to_yf
    import yfinance as yf
    df = yf.Ticker(futu_to_yf(code)).history(period=period, interval='1h', prepost=True, auto_adjust=False)
    return rows_from_yf_hourly(df, code, now)


def _parse_event(value, *, zone=NY):
    """Parse a provider timestamp (ISO / 'YYYY-MM-DD HH:MM:SS' local / epoch seconds) to UTC."""
    if value is None or (isinstance(value, float) and not np.isfinite(value)) or str(value).strip() == '':
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    return dt.astimezone(timezone.utc)


def _live_row(code, price, prev_close, event, now_dt, now_iso, source):
    """Validate one live quote against the session dated by `now` (New York) and return (row, reason)."""
    day = now_dt.astimezone(NY).date().isoformat()
    try:
        start, end = premarket_window(code, day)
    except sessions.Unavailable:
        return None, f'{day} is not a US session'
    if not _valid_price(price):
        return None, 'price missing or invalid'
    if event is None:
        return None, 'event time missing'
    if not (start <= event < end):
        return None, f'event time {event.isoformat()} outside pre-market window of {day}'
    if event > now_dt:
        return None, 'event time after capture time'
    return dict(code=code, date=day, price=float(price), prev_close=prev_close, available_at=now_iso,
                source=source, source_ref=event.isoformat(), synced_at=now_iso), None


def rows_from_futu_snapshot(df: pd.DataFrame, now: str) -> tuple[list[dict], dict]:
    """Live rows from a Futu get_market_snapshot frame captured at `now`.

    Uses `pre_market_price` and `update_time` (Futu: last update, US quotes in New York time).
    Returns (valid rows, {code: rejection reason}). No fallback to last_price.
    """
    now_dt = sessions.utc(now); now_iso = now_dt.isoformat()
    rows, rejected = [], {}
    for _, r in df.iterrows():
        code = str(r['code'])
        price = _num(r['pre_market_price']) if 'pre_market_price' in df.columns else None
        row, why = _live_row(code, price, _num(r.get('prev_close_price')), _parse_event(r.get('update_time')), now_dt, now_iso, SOURCE_LIVE)
        if row: rows.append(row)
        else: rejected[code] = why
    return rows, rejected


def fetch_live_yf(codes: list[str], now: str) -> tuple[list[dict], dict]:
    """Backup live source: yfinance `info['preMarketPrice']` with `preMarketTime` (network)."""
    from ..code_map import futu_to_yf
    import yfinance as yf
    now_dt = sessions.utc(now); now_iso = now_dt.isoformat()
    rows, rejected = [], {}
    for code in codes:
        try:
            info = yf.Ticker(futu_to_yf(code)).info or {}
        except Exception as e:  # noqa: BLE001
            rejected[code] = f'yfinance error: {type(e).__name__}'
            continue
        row, why = _live_row(code, _num(info.get('preMarketPrice')), _num(info.get('previousClose')), _parse_event(info.get('preMarketTime')), now_dt, now_iso, SOURCE_LIVE_BACKUP)
        if row: rows.append(row)
        else: rejected[code] = why
    return rows, rejected


def load_preopen(code: str, db_path: Optional[str] = None, *, source: str | None = None) -> pd.DataFrame:
    with mldb.get_ml_connection_readonly(db_path) as c:
        if source:
            return pd.read_sql_query('SELECT code, date, price, prev_close, available_at, source, source_ref, synced_at '
                                     'FROM ml_preopen_quotes WHERE code=? AND source=? ORDER BY date, available_at', c, params=(code, source))
        return pd.read_sql_query('SELECT code, date, price, prev_close, available_at, source, source_ref, synced_at '
                                 'FROM ml_preopen_quotes WHERE code=? ORDER BY date, available_at', c, params=(code,))


def select_quote(quotes: pd.DataFrame, code: str, target: str, decision_at, priority=LIVE_PRIORITY):
    """Among rows dated `target`, keep those valid for a decision at `decision_at` (available_at strictly
    before the session deadline and not after decision_at, finite positive price) and pick by priority,
    latest available_at within a source. Returns a one-row DataFrame or None."""
    deadline = sessions.session(code, target)['deadline']
    bound = sessions.utc(decision_at)
    cands = []
    for _, r in quotes[quotes['date'].astype(str) == target].iterrows():
        try:
            a = sessions.utc(str(r['available_at']))
        except (ValueError, sessions.Unavailable):
            continue
        if _valid_price(_num(r['price'])) and a < deadline and a <= bound:
            cands.append((priority.index(r['source']) if r['source'] in priority else len(priority), -a.timestamp(), r))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1]))
    return pd.DataFrame([cands[0][2]])


def quotes_for_prediction(code: str, db_path, target: str, decision_at) -> tuple[pd.DataFrame, Optional[dict]]:
    """History rows before `target` plus the selected live/history row for `target` (if any)."""
    allq = load_preopen(code, db_path)
    hist = allq[(allq['source'] == SOURCE_HISTORY) & (allq['date'].astype(str) < target)]
    chosen = select_quote(allq, code, target, decision_at)
    frame = pd.concat([hist, chosen], ignore_index=True) if chosen is not None else hist.reset_index(drop=True)
    return frame, (chosen.iloc[0].to_dict() if chosen is not None else None)


def attach_preopen(df: pd.DataFrame, quotes: pd.DataFrame, code: str, decision_at=None) -> pd.DataFrame:
    """pre_ret(as_of) = pre-open price on the target session / close(as_of) − 1 (pure as-of join).

    Only a quote dated exactly the target session, with a finite positive price, available_at
    strictly before that session's deadline and (if given) not after decision_at is used; when
    several qualify the LIVE_PRIORITY source wins. Anything else gives NaN (fail closed).
    """
    out = df.copy()
    if quotes is None or len(quotes) == 0:
        out['pre_ret'] = np.nan
        return out
    bound = sessions.utc(decision_at) if decision_at is not None else None
    by_date: dict[str, list] = {}
    for _, r in quotes.iterrows():
        try:
            by_date.setdefault(str(r['date']), []).append((r['source'], _num(r['price']), sessions.utc(str(r['available_at']))))
        except (ValueError, sessions.Unavailable):
            continue
    values = []
    for as_of, close in zip(out['date'].astype(str), out['close']):
        try:
            target = sessions.next_session(code, as_of)
            deadline = sessions.session(code, target)['deadline']
        except sessions.Unavailable:
            values.append(np.nan)
            continue
        ok = [(LIVE_PRIORITY.index(src) if src in LIVE_PRIORITY else len(LIVE_PRIORITY), -a.timestamp(), p)
              for src, p, a in by_date.get(target, []) if _valid_price(p) and a < deadline and (bound is None or a <= bound)]
        if not ok or not (isinstance(close, (int, float)) and np.isfinite(close) and close > 0):
            values.append(np.nan)
        else:
            ok.sort()
            values.append(ok[0][2] / float(close) - 1.0)
    out['pre_ret'] = values
    return out
