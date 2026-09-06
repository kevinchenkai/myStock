"""Overnight-information probe for HK (D1-D3 chain check). Read-only on a frozen ML DB copy that
already holds ml_external_1d (populated by fetch_external).

python -m scripts.ml_experiments.overnight_feasibility --db <frozen.db> --out <dir> [--shift]

Protocol: the 120-session / 20-session-refit development window of upgrade_matrix; one common
mask per stock; B0 refit on that mask so the only change is the feature set. Features come from
features.attach_overnight (as-of join on available_at, strictly before the HK 09:00 cutoff).
--shift re-dates every external row to the next US session (information appears one session
late); with correct alignment the improvement must collapse. Another look at a viewed window.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from mystock.ml import calibrator, config, evaluation as ev, external, features as feat, models, scales, sessions
from mystock.ml.features import FEATURE_COLS_V1, FEATURE_COLS_V2, OVERNIGHT_COLS
from scripts.ml_experiments.upgrade_matrix import EXTRA, features
from scripts.ml_experiments.model_matrix import pinball_rows, summarize, TARGET_COVERAGE

SMALL = dict(num_leaves=7, min_child_samples=50)


def shift_external(ext):
    """Re-date each row to the following US session (and its available_at)."""
    ext = ext.copy()
    nxt = [sessions.next_session(external.US_REFERENCE, d) for d in ext.date]
    ext['date'] = nxt
    ext['available_at'] = [external.available_at_for(d) for d in nxt]
    return ext


def blocks_for(f, cols):
    common = f.dropna(subset=FEATURE_COLS_V1 + EXTRA + OVERNIGHT_COLS + ['y_low_ret', 'y_high_ret']).date.tail(120).tolist()
    start = f.dropna(subset=OVERNIGHT_COLS).date.min()
    usable = f[f.date >= start].dropna(subset=cols + ['y_low_ret', 'y_high_ret']).copy()
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
        assert len(te) == len(dates), 'non-common sample mask'
        blocks.append(dict(dates=dates, a=a, c=c, te=te))
    return blocks


def run(code, blocks, name, cols, params, seed=0):
    alphas = config.alpha_for(code)
    rows = []
    for b in blocks:
        a, c, te = b['a'], b['c'], b['te']
        both = pd.concat([c, te], ignore_index=True)
        if cols is None:
            sa, sb = a.vol_20d.to_numpy(), both.vol_20d.to_numpy()
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
                             clo=float(lo[j] - q), chi=float(hi[j] + q), pin_lo=float(pl[j]), pin_hi=float(ph[j])))
    res = ev.metrics(*[[r[k] for r in rows] for k in ['yl', 'yh', 'lo', 'hi', 'clo', 'chi']], alphas)
    res.update(code=code, candidate=name, alphas=alphas, start=rows[0]['as_of'], end=rows[-1]['as_of'], train_rows=int(len(blocks[0]['a'])))
    return res, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--shift', action='store_true', help='leakage check: external rows dated one US session late')
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    metrics, preds, coverage = [], {}, {}
    for code in external.EXTERNAL_BY_CODE:
        f = features(code, args.db)
        ext = external.load_external(code, args.db)
        if args.shift:
            ext = shift_external(ext)
        f = feat.attach_overnight(f, ext, code)
        blocks = blocks_for(f, FEATURE_COLS_V2)
        te_all = pd.concat([b['te'] for b in blocks])
        coverage[code] = dict(external_rows=int(len(ext)), external_start=str(ext.date.min()), adr_zero_days=int((te_all.adr_ret == 0).sum()),
                              adr_nonzero_days=int((te_all.adr_ret != 0).sum()))
        for name, cols, params in (('B0_frozen', FEATURE_COLS_V1, {}), ('B1_naive_vol', None, None),
                                   ('O2_overnight_small', FEATURE_COLS_V2, SMALL), ('O3_small_only', FEATURE_COLS_V1, SMALL)):
            res, rows = run(code, blocks, name, cols, params)
            metrics.append(res); preds[f'{code}|{name}'] = rows
            print(code, name, res['n'], 'train', res['train_rows'], round(res['pinball_low'], 6), round(res['pinball_high'], 6), flush=True)
    summary = summarize(metrics, preds, list(external.EXTERNAL_BY_CODE))
    (out / 'metrics.json').write_text(json.dumps(metrics, indent=2))
    (out / 'summary.json').write_text(json.dumps(dict(shift=args.shift, coverage=coverage, summary=summary), indent=2))
    print(json.dumps(coverage))
    for r in summary:
        print(f"{r['candidate']:20s} {r['side']:4s} imp {r['improvement_pct']:6.2f} skill {r['naive_skill_pct']:6.2f} {r['improved']}/3 worst {r['worst_pct']:6.2f} ci {r['block_ci_pct']} {r['per_code_improvement_pct']}")


if __name__ == '__main__':
    main()
