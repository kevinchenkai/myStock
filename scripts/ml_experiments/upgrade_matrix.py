"""Bounded E0-E5 development matrix. Offline input required; no network/import Web.

python -m scripts.ml_experiments.upgrade_matrix --db ... --out ... [--seeds 0]
120 most recent mature sessions are development data, not independent holdout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
import lightgbm as lgb
from mystock.ml import config, data, sessions, evaluation as ev, calibrator
from mystock.ml.features import build_features, FEATURE_COLS
from mystock.ml.predictor import _predict_silent

CANDIDATES = {
 'E0_old':{}, 'E0_bagging':{'bagging':1}, 'naive_vol':{'naive':True},
 'E1_train504':{'train':504},'E1_train756':{'train':756},
 'E1_cal60':{'cal':60},'E1_cal120':{'cal':120},
 'E2_vol':{'scale':'vol_20d'},'E2_atr':{'scale':'atr_14'},
 'E3_small':{'small':True},'E5_daily':{'extra':'daily'},
 'E5_hourly':{'extra':'hourly'},'E4_rolling60':{'rolling':60},
}
EXTRA=['ret20','vol60','vol_ratio','range5']
HOUR=['hour_rv','hour_range','hour_tail']

def features(code,db):
    daily=data.load_daily(code,db)
    daily=sessions.prepare_daily(daily,code,sessions.utc('2026-09-05T06:00:00Z'),live=False)
    f=build_features(daily)
    f['target_session']=[sessions.next_session(code,d) for d in f.date]
    # Remove corporate-action target labels from model comparison: raw next-session
    # tradable prices are discontinuous. Execution handles actions independently.
    action=(f.splits.shift(-1).fillna(0)!=0)|(f.dividends.shift(-1).fillna(0)!=0)
    f.loc[action,['y_low_ret','y_high_ret']]=np.nan
    f['ret20']=f.adj_close.pct_change(20,fill_method=None)
    f['vol60']=f.adj_close.pct_change(fill_method=None).rolling(60).std()
    f['vol_ratio']=f.vol_5d/ev.scale(f.vol_20d);f['range5']=f.day_range_rel.rolling(5).mean()
    bars=data.load_hourly(code,db); rows=[]
    for day,g in bars.groupby('day'):
        try:
            s=sessions.session(code,day)
            if not data.complete_bars(code,g.to_dict('records'),sessions.utc('2026-09-05T06:00:00Z')):continue
            ret=np.log(g.close.to_numpy()/g.open.to_numpy())
            rows.append(dict(date=day,hour_rv=float(np.sqrt(np.sum(ret**2))),
                             hour_range=float(g.high.max()/g.low.min()-1),hour_tail=float(g.close.iloc[-1]/g.open.iloc[-1]-1)))
        except sessions.Unavailable:continue
    h=pd.DataFrame(rows,columns=['date']+HOUR)
    f=f.merge(h,on='date',how='left').replace([np.inf,-np.inf],np.nan)
    return f

def fit_predict(a,b,alpha,cols,kw,seed):
    m=lgb.LGBMRegressor(objective='quantile',alpha=alpha,n_estimators=300,learning_rate=.03,
                        num_leaves=7 if kw.get('small') else 15,min_child_samples=50 if kw.get('small') else 30,
                        subsample=.8,subsample_freq=kw.get('bagging',0),colsample_bytree=.8,
                        random_state=seed,verbose=-1,n_jobs=1)
    sc=ev.scale(a[kw['scale']]) if kw.get('scale') else np.ones(len(a))
    y=a['y_low_ret' if alpha<.5 else 'y_high_ret'].to_numpy()/sc
    m.fit(a[cols].to_numpy(),y)
    sb=ev.scale(b[kw['scale']]) if kw.get('scale') else np.ones(len(b))
    return _predict_silent(m,b[cols].to_numpy())*sb

def evaluate(code,f,name,kw,seed,test_dates):
    cols=FEATURE_COLS+(EXTRA if kw.get('extra')=='daily' else HOUR if kw.get('extra')=='hourly' else [])
    usable=f.dropna(subset=cols+['y_low_ret','y_high_ret']).copy()
    alphas=config.alpha_for(code); allrows=[]; folds=[]; residuals=[]
    for block in range(0,len(test_dates),20):
        dates=test_dates[block:block+20];first=dates[0]
        # Each label is known at the first decision close, never after it.
        tr=usable[(usable.date<first)&(usable.target_session<=first)]
        if kw.get('train'):tr=tr.tail(kw['train'])
        ncal=kw.get('cal',max(5,int(len(tr)*.25)))
        a,c=tr.iloc[:-(ncal+1)],tr.iloc[-ncal:]
        if len(a)<100:raise ValueError(f'{code} {name}: insufficient training')
        te=usable[usable.date.isin(dates)]
        if len(te)!=len(dates):raise ValueError('non-common sample mask')
        both=pd.concat([c,te],ignore_index=True)
        if kw.get('naive'):lo,hi=ev.naive_vol(a,both,*alphas)
        else:lo,hi=[fit_predict(a,both,alpha,cols,kw,seed) for alpha in alphas]
        q=calibrator.calibrate(c.y_low_ret.to_numpy(),lo[:len(c)],c.y_high_ret.to_numpy(),hi[:len(c)],.7)
        if not np.isfinite(q):raise ValueError('nonfinite calibration')
        lo,hi=lo[len(c):],hi[len(c):]
        folds.append(dict(train=len(a),cal=len(c),test=len(te),train_label_cutoff=a.target_session.max(),
                          calibration_label_cutoff=c.target_session.max(),test_start=first,test_end=dates[-1]))
        for j,(_,r) in enumerate(te.iterrows()):
            # Rolling q uses only previous OOF labels matured at current T.
            matured=[x[1] for x in residuals if x[0]<=r.date][-kw.get('rolling',60):]
            qi=float(np.quantile(matured,min(1,np.ceil((len(matured)+1)*.7)/len(matured)),method='higher')) if kw.get('rolling') and len(matured)>=20 else float(q)
            yl,yh=float(r.y_low_ret),float(r.y_high_ret)
            residuals.append((r.target_session,max(lo[j]-yl,yh-hi[j])))
            allrows.append(dict(code=code,as_of=r.date,target_session=r.target_session,close=float(r.close),
                                yl=yl,yh=yh,lo=float(lo[j]),hi=float(hi[j]),clo=float(lo[j]-qi),chi=float(hi[j]+qi),q=qi))
    result=ev.metrics(*[[r[k] for r in allrows] for k in ['yl','yh','lo','hi','clo','chi']],alphas)
    result.update(code=code,candidate=name,seed=seed,alphas=alphas,start=test_dates[0],end=test_dates[-1],
                  mask_sha256=hashlib.sha256(json.dumps(test_dates).encode()).hexdigest(),folds=folds)
    return result,allrows

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--out',required=True);ap.add_argument('--seeds',default='0');args=ap.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    summary=[];predictions={};t0=time.perf_counter()
    for code in config.TARGETS:
        f=features(code,args.db)
        # One common mask per stock for all daily candidates, hour group reported separately.
        common=f.dropna(subset=FEATURE_COLS+EXTRA+['y_low_ret','y_high_ret']).date.tail(120).tolist()
        for seed in map(int,args.seeds.split(',')):
            for name,kw in CANDIDATES.items():
                dates=common
                if name=='E5_hourly':
                    dates=f[f.date.isin(common)].dropna(subset=HOUR).date.tolist()
                    if len(dates)<40:
                        summary.append(dict(code=code,candidate=name,seed=seed,status='insufficient_complete_hourly',n=len(dates)));continue
                    base,br=evaluate(code,f,'E5_hourly_control',{},seed,dates);summary.append(base);predictions[f'{code}|E5_hourly_control|{seed}']=br
                result,rows=evaluate(code,f,name,kw,seed,dates);summary.append(result);predictions[f'{code}|{name}|{seed}']=rows
                print(code,name,seed,result['n'],round(result['pinball_low'],6),round(result['pinball_high'],6),flush=True)
        (out/'metrics.json').write_text(json.dumps(summary,indent=2,allow_nan=False));(out/'predictions.json').write_text(json.dumps(predictions,allow_nan=False))
    meta=dict(protocol='development-120-session-v1',seeds=args.seeds,view_count=1,input=str(Path(args.db).resolve()),input_sha256=hashlib.sha256(Path(args.db).read_bytes()).hexdigest(),seconds=time.perf_counter()-t0)
    (out/'protocol.json').write_text(json.dumps(meta,indent=2))
if __name__=='__main__':main()
