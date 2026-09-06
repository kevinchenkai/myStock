"""Feasibility probe (2026-09-06): does overnight cross-market information, available before the
HK open, reduce next-day high/low quantile error? Read-only; frozen DB + external CSV.

python -m scripts.ml_experiments.overnight_feasibility --db ... --external ... --out ...

Alignment: for an HK as-of date t, the US session dated t (ends 04:00 HKT t+1) is complete before
the HK open on t+1. Features: ADR return, KWEB return, Nasdaq return on calendar date t. US
holidays give no new information and are filled with 0. This is another look at the same
120-session development window; candidates share one common mask per stock, and B0 is refit on
that mask so the only change is the feature set.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from mystock.ml import calibrator, config, evaluation as ev, models, scales
from mystock.ml.features import FEATURE_COLS
from scripts.ml_experiments.upgrade_matrix import EXTRA, features
from scripts.ml_experiments.model_matrix import pinball_rows, summarize, TARGET_COVERAGE

ADR = {'HK.00700': 'TCEHY', 'HK.09988': 'BABA', 'HK.01810': 'XIACY'}
OVN = ['adr_ret', 'kweb_ret', 'ixic_ret']


def external_returns(path):
    x = pd.read_csv(path)
    out = {}
    for sym, g in x.groupby('symbol'):
        g = g.sort_values('Date')
        out[sym] = pd.Series(g.Close.pct_change().to_numpy(), index=g.Date.to_numpy())
    return out


def attach(f, code, ext):
    f = f.copy()
    adr = ext[ADR[code]]
    start = adr.index.min()
    f['adr_ret'] = f.date.map(adr)
    f['kweb_ret'] = f.date.map(ext['KWEB'])
    f['ixic_ret'] = f.date.map(ext['^IXIC'])
    inrange = f.date >= start
    for c in OVN:
        f.loc[inrange & f[c].isna(), c] = 0.0   # US holiday: no new information
    f['next_open_gap'] = f.open.shift(-1) / f.close - 1
    return f, start


def blocks_for(f, start):
    common = f.dropna(subset=FEATURE_COLS + EXTRA + OVN + ['y_low_ret', 'y_high_ret']).date.tail(120).tolist()
    usable = f[f.date >= start].dropna(subset=FEATURE_COLS + OVN + ['y_low_ret', 'y_high_ret']).copy()
    blocks = []
    for i in range(0, len(common), 20):
        dates = common[i:i + 20]
        first = dates[0]
        tr = usable[(usable.date < first) & (usable.target_session <= first)]
        ncal = max(5, int(len(tr) * .25))
        a, c = tr.iloc[:-(ncal + 1)], tr.iloc[-ncal:]
        if len(a) < 100:
            raise ValueError('insufficient training rows')
        te = usable[usable.date.isin(dates)]
        assert len(te) == len(dates)
        blocks.append(dict(dates=dates, a=a, c=c, te=te))
    return blocks


def run(code, blocks, name, cols, params, seed=0):
    alphas = config.alpha_for(code)
    rows = []
    for k, b in enumerate(blocks):
        a, c, te = b['a'], b['c'], b['te']
        both = pd.concat([c, te], ignore_index=True)
        if name == 'B1_naive_vol':
            sa, sb = a.s_vol_20d.to_numpy(), both.s_vol_20d.to_numpy()
            lo = scales.scaled_quantile(a.y_low_ret, sa, alphas[0], sb)
            hi = scales.scaled_quantile(a.y_high_ret, sa, alphas[1], sb)
        else:
            lo = models.QuantileModel('lightgbm', alphas[0], params, seed).fit(a[cols].to_numpy(), a.y_low_ret.to_numpy()).predict(both[cols].to_numpy())
            hi = models.QuantileModel('lightgbm', alphas[1], params, seed).fit(a[cols].to_numpy(), a.y_high_ret.to_numpy()).predict(both[cols].to_numpy())
        q = calibrator.calibrate(c.y_low_ret.to_numpy(), lo[:len(c)], c.y_high_ret.to_numpy(), hi[:len(c)], TARGET_COVERAGE)
        lo, hi = lo[len(c):], hi[len(c):]
        pl, ph = pinball_rows(te.y_low_ret, lo, alphas[0]), pinball_rows(te.y_high_ret, hi, alphas[1])
        for j, (_, r) in enumerate(te.iterrows()):
            rows.append(dict(code=code, as_of=r.date, yl=float(r.y_low_ret), yh=float(r.y_high_ret), lo=float(lo[j]), hi=float(hi[j]),
                             clo=float(lo[j] - q), chi=float(hi[j] + q), pin_lo=float(pl[j]), pin_hi=float(ph[j]), gap=float(r.next_open_gap)))
    res = ev.metrics(*[[r[k] for r in rows] for k in ['yl', 'yh', 'lo', 'hi', 'clo', 'chi']], alphas)
    res.update(code=code, candidate=name, alphas=alphas, start=rows[0]['as_of'], end=rows[-1]['as_of'], train_rows=int(len(blocks[0]['a'])))
    return res, rows


def gap_stats(f, code):
    d = f.dropna(subset=OVN + ['next_open_gap', 'ret_1d']).tail(500)
    X = np.c_[np.ones(len(d)), d.adr_ret, d.kweb_ret, d.ixic_ret, d.ret_1d]
    beta, *_ = np.linalg.lstsq(X, d.next_open_gap, rcond=None)
    resid = d.next_open_gap - X @ beta
    r2 = 1 - resid.var() / d.next_open_gap.var()
    return dict(code=code, n=len(d), corr_gap_adr=round(float(np.corrcoef(d.adr_ret, d.next_open_gap)[0, 1]), 2),
                corr_gap_kweb=round(float(np.corrcoef(d.kweb_ret, d.next_open_gap)[0, 1]), 2),
                corr_gap_ixic=round(float(np.corrcoef(d.ixic_ret, d.next_open_gap)[0, 1]), 2),
                r2_gap_on_overnight=round(float(r2), 2), resid_std_pct=round(float(resid.std() * 100), 2), gap_std_pct=round(float(d.next_open_gap.std() * 100), 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True); ap.add_argument('--external', required=True); ap.add_argument('--out', required=True)
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    ext = external_returns(args.external)
    stats, metrics, preds = [], [], {}
    small = dict(num_leaves=7, min_child_samples=50)
    for code in ADR:
        f = features(code, args.db)
        f['s_vol_20d'] = f.vol_20d
        f, start = attach(f, code, ext)
        stats.append(gap_stats(f, code))
        blocks = blocks_for(f, start)
        for name, cols, params in (('B0_frozen', FEATURE_COLS, {}), ('B1_naive_vol', None, None),
                                   ('O1_overnight', FEATURE_COLS + OVN, {}), ('O2_overnight_small', FEATURE_COLS + OVN, small),
                                   ('O3_small_only', FEATURE_COLS, small)):
            res, rows = run(code, blocks, name, cols, params)
            metrics.append(res); preds[f'{code}|{name}'] = rows
            print(code, name, res['n'], 'train', res['train_rows'], round(res['pinball_low'], 6), round(res['pinball_high'], 6), flush=True)
    summary = summarize(metrics, preds, list(ADR))
    (out / 'metrics.json').write_text(json.dumps(metrics, indent=2))
    (out / 'summary.json').write_text(json.dumps(dict(gap_stats=stats, summary=summary), indent=2))
    print(pd.DataFrame(stats).to_string(index=False))
    for r in summary:
        print(f"{r['candidate']:20s} {r['side']:4s} imp {r['improvement_pct']:6.2f} skill {r['naive_skill_pct']:6.2f} {r['improved']}/3 worst {r['worst_pct']:6.2f} ci {r['block_ci_pct']} {r['per_code_improvement_pct']}")


if __name__ == '__main__':
    main()
