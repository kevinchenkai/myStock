"""实验 A（docs/plans/ml-upgrade-plan_claude_20260904.md §3.1 原脚本，只读）：当前 LightGBM 分位模型 vs 波动率缩放朴素基线 vs 加特征版（同 purged 切分，CQR off，α=0.2/0.8）。"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from mystock.ml import config as mlcfg, data as mldata
from mystock.ml.features import build_features, FEATURE_COLS
from scripts.ml_experiments.frozen_cv_446e657 import purged_walk_forward, PurgedConfig
import os
from pathlib import Path
DB_PATH = Path(os.environ["MYSTOCK_EXPERIMENT_DB"]).resolve()
from mystock.ml.predictor import _fit_quantile, pinball_loss, _predict_silent

def extra_feats(df):
    df = df.copy()
    adj = df["adj_close"]; r = adj.pct_change(fill_method=None)
    df["ret_20d"] = adj.pct_change(20, fill_method=None)
    df["vol_60d"] = r.rolling(60).std()
    df["vol_ratio_5_20"] = df["vol_5d"] / df["vol_20d"]
    df["range_5d_mean"] = df["day_range_rel"].rolling(5).mean()
    df["range_prev_vs_atr"] = df["day_range_rel"] / df["atr_14"]
    df["dow"] = pd.to_datetime(df["date"]).dt.dayofweek
    df["abs_ret_1d"] = r.abs()
    df["hi_lo_pos_5"] = (adj - adj.rolling(5).min()) / (adj.rolling(5).max() - adj.rolling(5).min()).replace(0, np.nan)
    return df
EXTRA = ["ret_20d","vol_60d","vol_ratio_5_20","range_5d_mean","range_prev_vs_atr","dow","abs_ret_1d","hi_lo_pos_5"]

def evaluate(code, lo_a, hi_a):
    daily = mldata.load_daily(code, DB_PATH)
    df = extra_feats(build_features(daily))
    cols_all = FEATURE_COLS + EXTRA
    df = df.dropna(subset=cols_all + ["y_high_ret","y_low_ret"]).reset_index(drop=True)
    splits = purged_walk_forward(len(df), PurgedConfig(n_folds=4, min_train=250))
    out = {k: dict(pin=[], hit=[], w=[]) for k in ("naive_vol","lgb_base","lgb_extra","lgb_extra_x")}
    for tr, te in splits:
        a, b = df.iloc[tr], df.iloc[te]
        yl, yh = b["y_low_ret"].values, b["y_high_ret"].values
        # 朴素：y/vol_20d 的训练集经验分位 × 测试集 vol_20d
        ql = np.quantile(a["y_low_ret"]/a["vol_20d"], lo_a); qh = np.quantile(a["y_high_ret"]/a["vol_20d"], hi_a)
        lo, hi = ql*b["vol_20d"].values, qh*b["vol_20d"].values
        _rec(out["naive_vol"], yl, yh, lo, hi, lo_a, hi_a)
        for name, cols, kw in (("lgb_base", FEATURE_COLS, {}), ("lgb_extra", cols_all, {}),
                               ("lgb_extra_x", cols_all, dict(n_estimators=600, learning_rate=0.02, num_leaves=7, min_child_samples=50))):
            ml = _fitq(a[cols].to_numpy(), a["y_low_ret"].to_numpy(), lo_a, kw)
            mh = _fitq(a[cols].to_numpy(), a["y_high_ret"].to_numpy(), hi_a, kw)
            lo, hi = _predict_silent(ml, b[cols].to_numpy()), _predict_silent(mh, b[cols].to_numpy())
            _rec(out[name], yl, yh, lo, hi, lo_a, hi_a)
    return {k: (round(np.mean(v["pin"]),5), round(np.mean(v["hit"]),3), round(np.mean(v["w"]),2)) for k, v in out.items()}

def _fitq(X, y, alpha, kw):
    import lightgbm as lgb
    p = dict(objective="quantile", alpha=alpha, n_estimators=300, learning_rate=0.03, num_leaves=15,
             min_child_samples=30, subsample=0.8, colsample_bytree=0.8, random_state=0, verbose=-1, n_jobs=1)
    p.update(kw); m = lgb.LGBMRegressor(**p); m.fit(X, y); return m

def _rec(d, yl, yh, lo, hi, lo_a, hi_a):
    d["pin"].append((pinball_loss(yl, lo, lo_a) + pinball_loss(yh, hi, hi_a)) / 2)
    d["hit"].append(float(np.mean((yh <= hi) & (yl >= lo))))
    d["w"].append(float(np.mean(hi - lo)) * 100)

print("code | model | pinball(avg L/H) | hit | width%")
for code in mlcfg.TARGETS:
    lo_a, hi_a = mlcfg.alpha_for(code)
    res = evaluate(code, lo_a, hi_a)
    for k, v in res.items():
        print(f"{code} | {k:12s} | {v[0]} | {v[1]} | {v[2]}")
