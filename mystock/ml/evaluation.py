"""Shared finite metrics: raw quantiles, separate CQR diagnostics, naive_vol."""
import numpy as np
from .calibrator import calibrate

def scale(values, floor=1e-4):
    a=np.asarray(values,dtype=float)
    return np.maximum(np.where(np.isfinite(a),np.abs(a),floor),floor)

def pinball(y,p,a):
    y,p=np.asarray(y,float),np.asarray(p,float)
    mask=np.isfinite(y)&np.isfinite(p)
    if not mask.any():return None
    d=y[mask]-p[mask];return float(np.maximum(a*d,(a-1)*d).mean())

def skill(loss, reference):
    if loss is None or reference is None or not np.isfinite([loss,reference]).all() or reference<=0:return None
    return float(1-loss/reference)

def naive_vol(train,test,low_alpha,high_alpha):
    v=scale(train.vol_20d); vt=scale(test.vol_20d)
    yl,yh=train.y_low_ret.to_numpy()/v,train.y_high_ret.to_numpy()/v
    mask=np.isfinite(yl)&np.isfinite(yh)
    if not mask.any():raise ValueError('no finite training labels')
    return np.quantile(yl[mask],low_alpha)*vt,np.quantile(yh[mask],high_alpha)*vt

def metrics(yl,yh,lo,hi,clo,chi,alphas):
    values=[np.asarray(v,float) for v in [yl,yh,lo,hi,clo,chi]]
    mask=np.logical_and.reduce([np.isfinite(v) for v in values])
    if not mask.any():return {'n':0}
    yl,yh,lo,hi,clo,chi=[v[mask] for v in values]
    return dict(n=int(mask.sum()),pinball_low=pinball(yl,lo,alphas[0]),pinball_high=pinball(yh,hi,alphas[1]),
                raw_coverage=float(np.mean((yl>=lo)&(yh<=hi))),coverage=float(np.mean((yl>=clo)&(yh<=chi))),
                raw_width=float(np.mean(hi-lo)),width=float(np.mean(chi-clo)),
                lower_miss=float(np.mean(yl<clo)),upper_miss=float(np.mean(yh>chi)))

def block_interval(differences,block=10,seed=991,n_boot=1000):
    d=np.asarray(differences,float);d=d[np.isfinite(d)]
    if len(d)<block:return [None,None]
    rng=np.random.default_rng(seed)
    means=[]
    for _ in range(n_boot):
        starts=rng.integers(0,len(d),size=int(np.ceil(len(d)/block)))
        ix=np.concatenate([(np.arange(block)+i)%len(d) for i in starts])[:len(d)]
        means.append(d[ix].mean())
    return np.quantile(means,[.025,.975]).tolist()
