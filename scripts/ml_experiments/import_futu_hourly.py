"""Import an audited HK full-day AuType.NONE K_60M CSV from OpenD.

Explicit local files only. Futu end labels are mapped to exchange segment
starts; daily OHLC and overlapping Yahoo morning bars must agree first.
"""
import argparse
from contextlib import closing
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from mystock.ml import data, db, sessions
from mystock.code_map import futu_to_yf


def normalize(code, day, frame, daily, existing, now, source_hash):
    if not code.startswith('HK.') or set(frame.code) != {code}:
        raise ValueError('Only a single HK security is supported')
    schedule = sessions.session(code, day)
    if not schedule.get('break_start'):
        raise ValueError('Only a full HK split session is supported')
    periods = [(schedule['open'], schedule['break_start']), (schedule['break_end'], schedule['close'])]
    intervals = []
    for start, end in periods:
        while start < end:
            stop = min(start + timedelta(hours=1), end)
            intervals.append((start, stop)); start = stop
    frame = frame.sort_values('time_key')
    zone = ZoneInfo('Asia/Hong_Kong')
    expected = [stop.astimezone(zone).strftime('%Y-%m-%d %H:%M:%S') for _, stop in intervals]
    if frame.time_key.tolist() != expected:
        raise ValueError('Incomplete or unexpected end-labelled bucket grid')
    aggregate = [frame.open.iloc[0], frame.high.max(), frame.low.min(), frame.close.iloc[-1]]
    if not np.allclose(aggregate, [daily[k] for k in ('open','high','low','close')], rtol=1e-6, atol=1e-5):
        raise ValueError('Unadjusted daily OHLC does not reconcile')
    result = []
    for (start, _), (_, bar) in zip(intervals, frame.iterrows()):
        row = dict(symbol=futu_to_yf(code), futu_code=code,
            ts_utc=start.strftime('%Y-%m-%d %H:%M:%S'),
            ts_et=start.astimezone(zone).strftime('%Y-%m-%d %H:%M:%S'),
            **{k:float(bar[k]) for k in ('open','high','low','close','volume')},
            synced_at=now.isoformat(), data_source='futu_none', source_ref=source_hash)
        if start < schedule['break_start']:
            old = next((b for b in existing if b['ts_utc'] == row['ts_utc']), None)
            # Provider opening prints can differ. The first open is checked
            # against daily OHLC above; every other overlapping price must agree.
            keys = ('high','low','close') if start == schedule['open'] else ('open','high','low','close')
            if old is None or not np.allclose([row[k] for k in keys],
                [old[k] for k in keys], rtol=1e-6, atol=1e-5):
                raise ValueError('Morning overlap does not reconcile')
        result.append(row)
    if not data.complete_bars(code, result, now):
        raise ValueError('Normalized day is not complete')
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for arg in ('db','csv','code','date','receipt'):ap.add_argument('--'+arg, required=True)
    args=ap.parse_args(); path=Path(args.db).resolve(strict=True); source=Path(args.csv).resolve(strict=True)
    now=sessions.utc_now(); digest=hashlib.sha256(source.read_bytes()).hexdigest()
    daily=data.load_daily(args.code,path); daily=daily[daily.date==args.date].iloc[0].to_dict()
    hourly=data.load_hourly(args.code,path); existing=hourly[hourly.day==args.date].to_dict('records')
    rows=normalize(args.code,args.date,pd.read_csv(source),daily,existing,now,digest)
    db.init_ml_db(path)
    with closing(db.get_ml_connection(path)) as conn:
        with conn:
            conn.execute('DELETE FROM ml_quotes_1h WHERE symbol=? AND substr(ts_et,1,10)=?',(futu_to_yf(args.code),args.date))
            db.upsert(conn,'ml_quotes_1h',rows)
    receipt=dict(code=args.code,date=args.date,rows=len(rows),provider='Futu OpenD',request='K_60M / AuType.NONE',
        source=str(source),source_sha256=digest,imported_at=now.isoformat(),
        transform='end labels to calendar segment starts; no OHLC interpolation',
        validation='daily OHLC and overlapping morning prices agree; first open uses verified daily open',
        first_open_difference=float(rows[0]['open'] - existing[0]['open']))
    Path(args.receipt).write_text(json.dumps(receipt,indent=2))
    print(args.code,args.date,len(rows),'complete reconciled Futu bars imported')


if __name__=='__main__':main()
