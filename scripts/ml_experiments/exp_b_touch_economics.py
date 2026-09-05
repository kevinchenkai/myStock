"""Conditional event diagnostics only: overlapping events are not portfolio returns.
Mature 1/5 target sessions; unknown horizons stay pending, never clipped to last row.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from mystock.ml import config, data, db, sessions
from mystock.ml.simulator import match_limit_order, BUY, SELL

def run(path):
    out=[]
    with db.get_ml_connection_readonly(path) as conn:
        for code in config.TARGETS:
            daily=data.load_daily(code,path); dmap=daily.set_index('date').to_dict('index');bars=data.intraday_bars_by_day(code,path)
            rows=[]
            for p in db.load_predictions(conn,code):
                target=sessions.next_session(code,p['as_of'])
                if not bars.get(target):continue
                horizon=sessions.session_days(code,target,sessions.END)[:5]
                for side,key in [(BUY,'l_hat'),(SELL,'h_hat')]:
                    f=match_limit_order(side,p[key],bars[target])
                    if not f.filled:continue
                    r=dict(side=side,target=target,source=p.get('source'),timing='unverified_legacy',r1=None,r5=None,status='pending')
                    for h,day in [(1,target),(5,horizon[-1])]:
                        actual=dmap.get(day)
                        if actual and sessions.daily_final(code,dict(date=day,**actual),sessions.utc('2026-09-05T06:00:00Z')):
                            close=actual['close'];r['r'+str(h)]=close/f.fill_price-1 if side==BUY else f.fill_price/close-1
                    if r['r5'] is not None:r['status']='mature'
                    rows.append(r)
            out.append(dict(code=code,events=len(rows),pending=sum(r['status']=='pending' for r in rows),
                            diagnostics={side:{'n':sum(r['side']==side for r in rows),
                                              'mean_r1':mean([r['r1'] for r in rows if r['side']==side]),
                                              'mean_r5':mean([r['r5'] for r in rows if r['side']==side])} for side in [BUY,SELL]}))
    return out

def mean(values):
    values=[v for v in values if v is not None and np.isfinite(v)]
    return float(np.mean(values)) if values else None
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    Path(a.out).write_text(json.dumps(run(a.db),indent=2,allow_nan=False))
