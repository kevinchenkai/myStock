"""Offline, versioned US/HK sessions. Unknown calendar coverage fails closed.

UTC everywhere; HK uses conservative 09:00 decision cutoff and CAS latest close.
The checked-in schedule is generated, never inferred from available quote rows.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path
import csv
import math
from zoneinfo import ZoneInfo

CALENDAR_VERSION = 'pmc-5.1.3-xhkg-4.11.1-2020-2026-cas-weather-v2'
START, END = '2020-01-01', '2026-12-31'

class Unavailable(ValueError):
    def __init__(self, status, detail=''):
        self.status = status
        super().__init__(detail or status)

def utc_now():
    return datetime.now(timezone.utc)

def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise Unavailable('unknown_timestamp', 'Timezone required')
    return value.astimezone(timezone.utc)

def market(code):
    if code.startswith('HK.') or code.endswith('.HK'): return 'HK'
    if code.startswith('US.') or '.' not in code: return 'US'
    raise Unavailable('unavailable', 'Unknown market')

@lru_cache(maxsize=2)
def calendar(mkt):
    with (Path(__file__).parent / 'calendars' / f'{mkt}.csv').open() as f:
        return {r['date']: r for r in csv.DictReader(f)}

@lru_cache(maxsize=12000)
def session(code, day):
    day = str(day)[:10]
    if not START <= day <= END: raise Unavailable('unavailable', 'Calendar outside verified range')
    r = calendar(market(code)).get(day)
    if not r: raise Unavailable('not_session', day)
    return {k: utc(v) if k != 'date' and v else v for k,v in r.items()}

def session_days(code, start, end):
    if not START <= start <= end <= END: raise Unavailable('unavailable', 'Calendar outside verified range')
    return [d for d in calendar(market(code)) if start <= d <= end]

def next_session(code, day):
    dates = session_days(code, str(day)[:10], END)
    dates = [d for d in dates if d > str(day)[:10]]
    if not dates: raise Unavailable('unavailable', 'Next session unavailable')
    return dates[0]

def window(code, end, n):
    if not 1 <= n <= 400: raise ValueError('days must be 1..400')
    ds = session_days(code, START, end)
    if len(ds) < n: raise Unavailable('unavailable', 'Insufficient calendar history')
    return ds[-n:]

def state(code, now=None):
    now = utc(now or utc_now())
    zone = ZoneInfo('Asia/Hong_Kong' if market(code)=='HK' else 'America/New_York')
    day = now.astimezone(zone).date().isoformat()
    dates = session_days(code, START, day)
    if day in calendar(market(code)):
        s = session(code,day)
        if s['deadline'] <= now < s['final_at']:
            return {'status':'skipped_in_session', 'market':market(code)}
    closed = [d for d in dates if session(code,d)['final_at'] <= now]
    if not closed: raise Unavailable('unavailable')
    as_of = closed[-1]; target = next_session(code,as_of)
    return {'status':'ready','as_of':as_of,'target_session':target,
            'deadline':session(code,target)['deadline'].isoformat()}

def ohlc_ok(row):
    try:
        o,h,l,c = [float(row[k]) for k in ('open','high','low','close')]
        return all(math.isfinite(v) and v>0 for v in (o,h,l,c)) and l <= min(o,c) <= max(o,c) <= h
    except (KeyError,TypeError,ValueError): return False

def daily_final(code, row, now=None, historical=False):
    if not ohlc_ok(row): return False
    try:
        s=session(code,row['date']); current=utc(now or utc_now())
        if current < s['final_at']: return False
        stamp=row.get('synced_at')
        # Old timestamps have unknown local zone. Only >1 full day later is
        # accepted for historical reconstruction; never confirm same-day cache.
        if stamp:
            try: fetched=utc(str(stamp))
            except (Unavailable, ValueError):
                fetched=datetime.fromisoformat(str(stamp)).replace(tzinfo=timezone.utc)-timedelta(hours=14)
            return fetched >= s['final_at']
        return historical
    except (Unavailable, ValueError): return False

def hourly_final(code, row, now=None):
    try:
        start=utc(str(row['ts_utc']).replace(' ', 'T') + ('+00:00' if len(str(row['ts_utc']))==19 else ''))
        zone=ZoneInfo('Asia/Hong_Kong' if market(code)=='HK' else 'America/New_York')
        s=session(code,start.astimezone(zone).date().isoformat())
        if not s['open'] <= start < s['close']: return False
        if s.get('break_start') and s['break_start'] <= start and start+timedelta(hours=1) <= s['break_end']: return False
        stop=min(start+timedelta(hours=1),s['close'])
        if s.get('break_start') and start < s['break_start']: stop=min(stop,s['break_start'])
        stamp=row.get('synced_at')
        if stamp:
            try: fetched=utc(str(stamp))
            except (Unavailable,ValueError): fetched=datetime.fromisoformat(str(stamp)).replace(tzinfo=timezone.utc)-timedelta(hours=14)
            if fetched < stop: return False
        return ohlc_ok(row) and utc(now or utc_now()) >= stop
    except (Unavailable,ValueError): return False

def prepare_daily(daily, code, now=None, live=True):
    now=utc(now or utc_now()); st=state(code,now) if live else None
    if live and st['status']!='ready': raise Unavailable(st['status'])
    df=daily.copy().sort_values('date').drop_duplicates('date',keep='last')
    good=[daily_final(code,r,now,historical=not live) for r in df.to_dict('records')]
    # Keep internal missing sessions as NaN so shift(-1) cannot leap a gap.
    df=df.loc[good]
    if df.empty: raise Unavailable('awaiting_final_data')
    if live and str(df.iloc[-1]['date']) != st['as_of']: raise Unavailable('awaiting_final_data')
    ds=session_days(code,str(df.iloc[0]['date']),str(df.iloc[-1]['date']))
    return df.set_index('date').reindex(ds).rename_axis('date').reset_index()

def check_deadline(code,target,now=None):
    if utc(now or utc_now()) >= session(code,target)['deadline']:
        raise Unavailable('missed_deadline')
