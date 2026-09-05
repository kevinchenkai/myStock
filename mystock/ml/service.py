"""Read-only v2 application service. Content-keyed cache, explicit gaps and scenarios."""
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
import json
import sqlite3
import time
import numpy as np
from . import data, db, config, sessions, versions
from .execution import Scenario, replay
from .features import build_features, FEATURE_COLS
from .evaluation import naive_vol

_CACHE=OrderedDict()
_REVIEW_CACHE=OrderedDict()

def validate(codes,days):
    codes=list(dict.fromkeys(c.strip().upper() for c in codes if c.strip()))
    if not codes:codes=config.TARGETS[:]
    if any(c not in config.TARGETS for c in codes):raise ValueError('invalid code')
    if not isinstance(days,int) or not 1<=days<=400:raise ValueError('days must be 1..400')
    return codes

def read_inputs(path,code,start,end):
    if not Path(path).exists():raise FileNotFoundError('ML database unavailable')
    with db.get_ml_connection_readonly(path) as c:
        tables={r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        if not {'ml_quotes_1d','ml_quotes_1h','ml_prediction_versions'} <= tables:raise FileNotFoundError('ML schema upgrade required')
        sym=code[3:] if code.startswith('US.') else code[3:].lstrip('0').zfill(4)+'.HK'
        daily=[dict(r) for r in c.execute('select * from ml_quotes_1d where symbol=? and date<=? order by date',(sym,end))]
        bars=[dict(r) for r in c.execute('select * from ml_quotes_1h where symbol=? and substr(ts_et,1,10)>=? and substr(ts_et,1,10)<=? order by ts_utc',(sym,start,end))]
        preds=versions.load(c,code,include_audit=True)
    return daily,bars,preds

def latest(path,codes,now=None,allow_recomputed=False):
    now=sessions.utc(now or sessions.utc_now());results=[]
    for code in validate(codes,1):
        with db.get_ml_connection_readonly(path) as c:ps=versions.load(c,code,include_audit=True)
        selected=versions.select_by_target(ps,allow_recomputed=allow_recomputed)
        eligible=list(selected.values());p=eligible[-1] if eligible else None
        try:st=sessions.state(code,now)
        except sessions.Unavailable as e:st={'status':e.status}
        status=st['status']
        if p:
            status='expired' if now>=sessions.session(code,p['target_session'])['deadline'] else 'pending'
        elif status=='ready':status='unavailable'
        results.append(dict(code=code,status=status,market_state=st,prediction=p,
                            audit_versions=len([p for p in ps if p['status'].startswith('audit')]),
                            note='风险覆盖区间；模拟报价另按固定 policy 与账户参数计算。'))
    return dict(schema_version=2,results=results,calendar_version=sessions.CALENDAR_VERSION)

def review(path,code,days=60,end=None,*,allow_recomputed=False,now=None):
    validate([code],days);now=sessions.utc(now or sessions.utc_now())
    end=end or now.date().isoformat();dates=sessions.window(code,end,days)
    ds,bs,ps=read_inputs(path,code,dates[0],dates[-1]);fingerprint=versions.digest([ds,bs,ps])
    review_key=versions.digest([fingerprint,code,dates,allow_recomputed,[now>=sessions.session(code,d)['final_at'] for d in dates]])
    if review_key in _REVIEW_CACHE:
        value=_REVIEW_CACHE.pop(review_key);_REVIEW_CACHE[review_key]=value
        return json.loads(value)
    selected=versions.select_by_target(ps,allow_recomputed=allow_recomputed)
    reconstructed=versions.select_by_target([p for p in ps if p['source']=='recomputed'],allow_recomputed=True)
    dm={r['date']:r for r in ds};bm={}
    for b in bs:bm.setdefault(b['ts_et'][:10],[]).append(b)
    rows=[]
    import pandas as pd
    try:
        clean=sessions.prepare_daily(pd.DataFrame(ds),code,now,live=False) if ds else pd.DataFrame()
        f=build_features(clean) if not clean.empty else pd.DataFrame()
    except sessions.Unavailable: f=pd.DataFrame()
    for date in dates:
        d=dm.get(date);p=selected.get(date);bars=bm.get(date,[])
        mature=now>=sessions.session(code,date)['final_at']
        final=bool(d and sessions.daily_final(code,d,now))
        complete=data.complete_bars(code,bars,now)
        status='pending' if not mature else 'missing_daily' if not d else 'awaiting_final_data' if not final else 'missing_prediction' if not p else 'missing_bars' if not complete else 'ok'
        base=None
        previous=[r for r in ds if r['date']<date]
        if previous:
            pr=previous[-1]
            if sessions.next_session(code,pr['date'])==date and sessions.daily_final(code,pr,now):
                base=dict(as_of=pr['date'],close=pr['close'],target_session=date,prediction_id=None,source='fixed_baseline')
                if not f.empty:
                    tr=f[(f.date<pr['date'])].dropna(subset=['vol_20d','y_low_ret','y_high_ret']).tail(504)
                    te=f[f.date==pr['date']]
                    if len(tr)>=60 and not te.empty:
                        lo,hi=naive_vol(tr,te,*config.alpha_for(code));base.update(naive_low=float(pr['close']*(1+lo[0])),naive_high=float(pr['close']*(1+hi[0])))
        prediction={**(base or {}),**(p or {})} or None
        hit=None
        if p and final:hit=bool(d['low']>=p['l_hat'] and d['high']<=p['h_hat'])
        rows.append(dict(date=date,status=status,has_recomputed=date in reconstructed,prediction=prediction,daily=d if final else None,
                         hourly_sources=sorted({b.get('data_source') or 'yfinance' for b in bars}),
                         bars=bars if complete and final else [],bar_status='complete' if complete else 'missing_bars',
                         hit=hit,price_basis='yfinance_unadjusted_same_as_prediction',
                         prediction_status='recomputed' if p and p['source']=='recomputed' else 'available' if p else 'missing_prediction'))
    result=dict(schema_version=2,code=code,currency='USD' if code.startswith('US.') else 'HKD',days=days,
                start=dates[0],end=dates[-1],input_version=fingerprint,rows=rows,
                source_mode='historical_reconstruction' if allow_recomputed else 'verified_live_only')
    _REVIEW_CACHE[review_key]=json.dumps(result,allow_nan=False)
    while len(_REVIEW_CACHE)>32:_REVIEW_CACHE.popitem(last=False)
    return result

def compare(path,codes,days,scenario,end=None,*,allow_recomputed=False,now=None,policies=None):
    codes=validate(codes,days);scenario.validate();results=[];started=time.perf_counter()
    for code in codes:
        r=review(path,code,days,end,allow_recomputed=allow_recomputed,now=now)
        key=versions.digest([r,asdict(scenario),policies])
        if key in _CACHE:
            out=_CACHE.pop(key);_CACHE[key]=out
        else:
            out=dict(code=code,currency=r['currency'],input_version=r['input_version'],source_mode=r['source_mode'],
                     policies=[replay(r['rows'],scenario,policy=p) for p in (policies or ['boundary_inventory','naive_vol_inventory','fixed_offset_inventory'])])
            _CACHE[key]=out
            while len(_CACHE)>32:_CACHE.popitem(last=False)
        results.append(out)
    return dict(schema_version=2,mode='inventory',window_mode='restart_simulation',days=days,results=results,
                elapsed_ms=(time.perf_counter()-started)*1000,
                note='各股独立同币种账户；20/60/120 为从输入初始状态重新模拟，连续回放切片请保留原始起点。')

def facts(path,code,date,prediction=None):
    """Order facts only. Current snapshot is insufficient to reconstruct lifecycle."""
    with db.get_ml_connection_readonly(path) as c:
        orders=[dict(r) for r in c.execute('select order_id,trd_side,price,qty,order_status,create_time,updated_time from ml_orders where code=? and substr(create_time,1,10)=? order by create_time',(code,date))]
    zone='Asia/Hong_Kong' if code.startswith('HK.') else 'America/New_York'
    from datetime import datetime
    from zoneinfo import ZoneInfo
    for o in orders:
        stamp=prediction.get('published_at') if prediction else None
        available=False
        if stamp:
            try:available=sessions.utc(stamp)<=datetime.fromisoformat(o['create_time']).replace(tzinfo=ZoneInfo(zone)).astimezone(sessions.utc_now().tzinfo)
            except ValueError:pass
        o['prediction_available_at_order']=available;o['lifecycle']='snapshot_only';o['hypothetical_pnl']=None
    return orders
