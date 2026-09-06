"""Pre-registered model matrix (2026-09-06). Offline frozen input only; never writes the runtime DB.

python -m scripts.ml_experiments.model_matrix --db data/model-matrix-20260906/input.db --out data/model-matrix-20260906/run1

Protocol: same development window as upgrade_matrix (per stock the 120 most recent mature
sessions, 20-session refit blocks, common daily mask, corporate-action target days excluded,
25% tail calibration with a 1-row gap, CQR target 0.70). Raw pinball is scored on the
uncalibrated quantiles. This is one more look at an already-viewed development window,
not a holdout. Grid candidates choose one configuration per side, shared across all six
stocks, by inner time-ordered validation on the last 20% of each training segment; the
calibration segment never takes part in selection.
"""
import argparse
import hashlib
import json
import platform
import time
import traceback
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from mystock.ml import calibrator, config, evaluation as ev, models, scales
from mystock.ml.features import FEATURE_COLS
from scripts.ml_experiments.upgrade_matrix import EXTRA, features

warnings.filterwarnings('ignore', message='X does not have valid feature names')

GRID_LGB = [dict(num_leaves=l, min_child_samples=m) for l in (7, 15, 31) for m in (20, 50)]
GRID_CAT = [dict(depth=d, l2_leaf_reg=r) for d in (3, 4, 6) for r in (3.0, 10.0)]
GRID_XGB = [dict(max_depth=d, min_child_weight=w) for d in (2, 3, 4) for w in (5, 20)]
CANDIDATES = {
    'B0_frozen': dict(kind='model', backend='lightgbm', grid=[dict(num_leaves=15, min_child_samples=30)]),
    'B1_naive_vol': dict(kind='scale', scale='vol_20d'),
    'S1_ewma094': dict(kind='scale', scale='ewma'),
    'S2_gk20': dict(kind='scale', scale='gk20'),
    'S3_garch11': dict(kind='scale', scale='garch'),
    'L_raw': dict(kind='model', backend='linear', grid=[dict(l1=1e-3)]),
    'B2_lgb_grid': dict(kind='model', backend='lightgbm', grid=GRID_LGB),
    'C1_cat_grid': dict(kind='model', backend='catboost', grid=GRID_CAT),
    'X1_xgb_grid': dict(kind='model', backend='xgboost', grid=GRID_XGB),
}
BASE, NAIVE = 'B0_frozen', 'B1_naive_vol'
TARGET_COVERAGE = 0.7
COUNTERS = dict(fits=0)


def pinball_rows(y, p, alpha):
    d = np.asarray(y, float) - np.asarray(p, float)
    return np.maximum(alpha * d, (alpha - 1) * d)


def prepare(code, db):
    f = features(code, db)
    ratio = f.adj_close / f.close
    f['s_vol_20d'] = f.vol_20d
    f['s_ewma'] = scales.ewma_scale(f.ret_1d.to_numpy(), lam=0.94)
    f['s_gk20'] = scales.garman_klass_scale(f.open * ratio, f.high * ratio, f.low * ratio, f.adj_close, window=20)
    common = f.dropna(subset=FEATURE_COLS + EXTRA + ['y_low_ret', 'y_high_ret']).date.tail(120).tolist()
    usable = f.dropna(subset=FEATURE_COLS + ['y_low_ret', 'y_high_ret']).copy()
    blocks = []
    for i in range(0, len(common), 20):
        dates = common[i:i + 20]
        first = dates[0]
        tr = usable[(usable.date < first) & (usable.target_session <= first)]
        ncal = max(5, int(len(tr) * .25))
        a, c = tr.iloc[:-(ncal + 1)], tr.iloc[-ncal:]
        if len(a) < 100:
            raise ValueError(f'{code}: insufficient training rows')
        te = usable[usable.date.isin(dates)]
        if len(te) != len(dates):
            raise ValueError('non-common sample mask')
        blocks.append(dict(dates=dates, a=a, c=c, te=te))
    return f, blocks


def fit_predict(backend, cfg, alpha, a, both, seed):
    COUNTERS['fits'] += 1
    m = models.QuantileModel(backend, alpha, cfg, seed).fit(a[FEATURE_COLS].to_numpy(), a[LABEL[alpha < .5]].to_numpy())
    return m.predict(both[FEATURE_COLS].to_numpy()), m.meta()


LABEL = {True: 'y_low_ret', False: 'y_high_ret'}


def inner_select(name, spec, data, seed):
    """Pick one config per side by equal-weight relative inner-validation pinball."""
    grid = spec['grid']
    if len(grid) == 1:
        return {'low': 0, 'high': 0}, {}
    scores = {}
    for side, idx in (('low', 0), ('high', 1)):
        per_code = {}
        for code, (f, blocks) in data.items():
            alpha = config.alpha_for(code)[idx]
            pins = np.zeros(len(grid))
            for b in blocks:
                a = b['a']
                n_val = max(20, int(len(a) * .2))
                a_fit, a_val = a.iloc[:-(n_val + 1)], a.iloc[-n_val:]
                for g, cfg in enumerate(grid):
                    p, _ = fit_predict(spec['backend'], cfg, alpha, a_fit, a_val, seed)
                    pins[g] += pinball_rows(a_val[LABEL[idx == 0]], p, alpha).mean() / len(blocks)
            per_code[code] = pins
        rel = np.mean([v / v[0] for v in per_code.values()], axis=0)
        scores[side] = dict(relative=rel.tolist(), per_code={k: v.tolist() for k, v in per_code.items()})
    chosen = {side: int(np.argmin(scores[side]['relative'])) for side in scores}
    return chosen, scores


def scale_for(kind, f, b):
    """Return (scale_a, scale_both, info) for a block; raises on failure."""
    a, c, te = b['a'], b['c'], b['te']
    both = pd.concat([c, te], ignore_index=True)
    if kind == 'garch':
        series = f[['date', 'ret_1d']].dropna()
        fit = series[series.date <= a.date.max()].ret_1d.to_numpy()
        allr = series[series.date <= te.date.max()]
        s, info = scales.garch_scale(fit, allr.ret_1d.to_numpy())
        m = dict(zip(allr.date, s))
        sa, sb = a.date.map(m).to_numpy(), both.date.map(m).to_numpy()
    else:
        col = 's_' + kind
        sa, sb, info = a[col].to_numpy(), both[col].to_numpy(), {}
    if not (np.isfinite(sa).all() and np.isfinite(sb).all()):
        raise ValueError(f'{kind}: non-finite scale rows')
    return sa, sb, info


def evaluate(name, spec, code, f, blocks, chosen, seed):
    alphas = config.alpha_for(code)
    rows, folds, failures, metas = [], [], [], []
    for k, b in enumerate(blocks):
        a, c, te = b['a'], b['c'], b['te']
        both = pd.concat([c, te], ignore_index=True)
        fold = dict(block=k, train=len(a), cal=len(c), test=len(te), train_label_cutoff=str(a.target_session.max()),
                    test_start=b['dates'][0], test_end=b['dates'][-1], fallback=False)
        try:
            if spec['kind'] == 'scale':
                sa, sb, info = scale_for(spec['scale'], f, b)
                fold.update(info)
                lo = scales.scaled_quantile(a.y_low_ret, sa, alphas[0], sb)
                hi = scales.scaled_quantile(a.y_high_ret, sa, alphas[1], sb)
            else:
                cfg_lo, cfg_hi = spec['grid'][chosen['low']], spec['grid'][chosen['high']]
                lo, m1 = fit_predict(spec['backend'], cfg_lo, alphas[0], a, both, seed)
                hi, m2 = fit_predict(spec['backend'], cfg_hi, alphas[1], a, both, seed)
                metas.append(dict(low=m1, high=m2))
        except Exception as e:  # noqa: BLE001
            # Pre-registered fallback: this block uses B1 (vol_20d) and is flagged; reported separately.
            failures.append(dict(block=k, error=f'{type(e).__name__}: {e}'))
            fold['fallback'] = True
            sa, sb = a.s_vol_20d.to_numpy(), both.s_vol_20d.to_numpy()
            lo = scales.scaled_quantile(a.y_low_ret, sa, alphas[0], sb)
            hi = scales.scaled_quantile(a.y_high_ret, sa, alphas[1], sb)
        if not (np.isfinite(lo).all() and np.isfinite(hi).all()):
            raise ValueError(f'{code} {name} block {k}: non-finite predictions')
        q = calibrator.calibrate(c.y_low_ret.to_numpy(), lo[:len(c)], c.y_high_ret.to_numpy(), hi[:len(c)], TARGET_COVERAGE)
        if not np.isfinite(q):
            raise ValueError('nonfinite calibration')
        lo, hi = lo[len(c):], hi[len(c):]
        fold['q'] = float(q)
        folds.append(fold)
        pl, ph = pinball_rows(te.y_low_ret, lo, alphas[0]), pinball_rows(te.y_high_ret, hi, alphas[1])
        for j, (_, r) in enumerate(te.iterrows()):
            rows.append(dict(code=code, as_of=r.date, target_session=r.target_session, close=float(r.close), block=k,
                             fallback=fold['fallback'], yl=float(r.y_low_ret), yh=float(r.y_high_ret),
                             lo=float(lo[j]), hi=float(hi[j]), clo=float(lo[j] - q), chi=float(hi[j] + q),
                             pin_lo=float(pl[j]), pin_hi=float(ph[j])))
    keys = ['yl', 'yh', 'lo', 'hi', 'clo', 'chi']
    result = ev.metrics(*[[r[k] for r in rows] for k in keys], alphas)
    clean = [r for r in rows if not r['fallback']]
    if clean and len(clean) != len(rows):
        result['model_only'] = ev.metrics(*[[r[k] for r in clean] for k in keys], alphas)
    result.update(code=code, candidate=name, seed=seed, alphas=alphas, start=rows[0]['as_of'], end=rows[-1]['as_of'],
                  n_fallback=sum(r['fallback'] for r in rows), failures=failures, folds=folds,
                  chosen=chosen if spec['kind'] == 'model' else None,
                  meta=metas[0] if metas else None)
    return result, rows


def summarize(metrics, preds, codes):
    d = {(r['code'], r['candidate']): r for r in metrics}
    names = list(dict.fromkeys(r['candidate'] for r in metrics))
    table = []
    for name in names:
        if name == BASE:
            continue
        for side, key, pk in (('low', 'pinball_low', 'pin_lo'), ('high', 'pinball_high', 'pin_hi')):
            imp = np.array([1 - d[c, name][key] / d[c, BASE][key] for c in codes])
            sk = np.array([1 - d[c, name][key] / d[c, NAIVE][key] for c in codes])
            series = []
            for c in codes:
                old = {r['as_of']: r[pk] for r in preds[f'{c}|{BASE}']}
                series.append([(old[r['as_of']] - r[pk]) / d[c, BASE][key] for r in preds[f'{c}|{name}']])
            length = min(map(len, series))
            ci = np.array(ev.block_interval(np.mean([v[-length:] for v in series], axis=0))) * 100
            gate = bool(imp.mean() >= .05 and sk.mean() >= .03 and (imp > 0).sum() >= 4 and imp.min() >= -.1)
            table.append(dict(candidate=name, side=side, improvement_pct=round(imp.mean() * 100, 2),
                              naive_skill_pct=round(sk.mean() * 100, 2), improved=int((imp > 0).sum()),
                              worst_pct=round(imp.min() * 100, 2), block_ci_pct=[round(float(x), 2) for x in ci],
                              per_code_improvement_pct={c: round(v * 100, 2) for c, v in zip(codes, imp)}, gate=gate))
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--only', default='', help='comma-separated candidate names (default all)')
    ap.add_argument('--codes', default='', help='comma-separated codes (default config.TARGETS)')
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    codes = args.codes.split(',') if args.codes else list(config.TARGETS)
    names = args.only.split(',') if args.only else list(CANDIDATES)
    for n in names:
        if n not in CANDIDATES:
            raise SystemExit(f'unknown candidate {n}')
    for spec in (CANDIDATES[n] for n in names):
        if spec['kind'] == 'model' and not models.available(spec['backend']):
            raise SystemExit(f"backend {spec['backend']} unavailable; refusing to substitute")
    t0 = time.perf_counter()
    data = {code: prepare(code, args.db) for code in codes}
    metrics, preds, timings, selection = [], {}, {}, {}
    for name in names:
        spec = CANDIDATES[name]
        t1 = time.perf_counter()
        chosen, scores = inner_select(name, spec, data, args.seed) if spec['kind'] == 'model' else (None, {})
        if scores:
            selection[name] = dict(chosen={s: spec['grid'][i] for s, i in chosen.items()}, inner=scores)
        for code in codes:
            f, blocks = data[code]
            try:
                result, rows = evaluate(name, spec, code, f, blocks, chosen, args.seed)
            except Exception:  # noqa: BLE001
                metrics.append(dict(code=code, candidate=name, status='failed', error=traceback.format_exc()))
                print(code, name, 'FAILED', flush=True)
                continue
            metrics.append(result)
            preds[f'{code}|{name}'] = rows
            print(code, name, result['n'], round(result['pinball_low'], 6), round(result['pinball_high'], 6),
                  'fallback', result['n_fallback'], flush=True)
        timings[name] = round(time.perf_counter() - t1, 1)
        (out / 'metrics.json').write_text(json.dumps(metrics, indent=2, allow_nan=False))
        (out / 'predictions.json').write_text(json.dumps(preds, allow_nan=False))
    ok = [r for r in metrics if 'pinball_low' in r]
    summary = summarize(ok, preds, codes) if all((c, BASE) in {(r['code'], r['candidate']) for r in ok} for c in codes) and any(r['candidate'] == NAIVE for r in ok) else []
    versions = {}
    for mod in ('numpy', 'pandas', 'scipy', 'sklearn', 'lightgbm', 'catboost', 'xgboost', 'arch'):
        try:
            versions[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001
            versions[mod] = None
    meta = dict(protocol='development-120-session-v1 (one additional look)', seed=args.seed, codes=codes,
                candidates={n: CANDIDATES[n] for n in names}, target_coverage=TARGET_COVERAGE,
                inner_validation='last 20% of each training segment, 1-row gap, config shared across stocks per side',
                selection=selection, fit_count=COUNTERS['fits'], timings_seconds=timings,
                input=str(Path(args.db).resolve()), input_sha256=hashlib.sha256(Path(args.db).read_bytes()).hexdigest(),
                versions=versions, platform=platform.platform(), machine=platform.machine(),
                seconds=round(time.perf_counter() - t0, 1), summary=summary)
    (out / 'protocol.json').write_text(json.dumps(meta, indent=2))
    for row in summary:
        print(row['candidate'], row['side'], row['improvement_pct'], row['naive_skill_pct'], row['improved'], row['worst_pct'], row['block_ci_pct'], 'GATE' if row['gate'] else '-')


if __name__ == '__main__':
    main()
