"""D4 formal experiment (pre-registered 2026-09-06): O2 (V2 features + 7 leaves / min_child 50) vs B0
(frozen V1 LightGBM), seeds 0-4, two 120-session windows (W1 = current development window,
W2 = the 120 sessions before it), on a frozen ML DB copy holding ml_external_1d.

python -m scripts.ml_experiments.overnight_d4 --db <frozen.db> --out <dir> [--seeds 0,1,2,3,4]

Primary decision (pre-registered): both main HK stocks (HK.00700, HK.09988) improve on both
sides by >= 5% raw pinball vs B0 and >= 3% skill vs naive_vol, on the seed mean, in both windows.
HK.01810 is observational; it is also run with the KWEB control proxy (O2_kweb). CQR coverage
and width are reported separately and are not part of the gate.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from mystock.ml import evaluation as ev, external, features as feat
from mystock.ml.features import FEATURE_COLS_V1, FEATURE_COLS_V2, OVERNIGHT_COLS
from scripts.ml_experiments.upgrade_matrix import EXTRA, features
from scripts.ml_experiments.overnight_feasibility import run, SMALL

MAIN = ['HK.00700', 'HK.09988']
OBS = ['HK.01810']


def blocks_for(f, cols, window):
    common_all = f.dropna(subset=FEATURE_COLS_V1 + EXTRA + OVERNIGHT_COLS + ['y_low_ret', 'y_high_ret']).date.tolist()
    common = common_all[-120:] if window == 1 else common_all[-240:-120]
    if len(common) != 120:
        raise ValueError('window not available')
    start = f.dropna(subset=OVERNIGHT_COLS).date.min()
    usable = f[f.date >= start].dropna(subset=cols + ['y_low_ret', 'y_high_ret']).copy()
    blocks = []
    for i in range(0, 120, 20):
        dates = common[i:i + 20]
        first = dates[0]
        tr = usable[(usable.date < first) & (usable.target_session <= first)]
        ncal = max(5, int(len(tr) * .25))
        a, c = tr.iloc[:-(ncal + 1)], tr.iloc[-ncal:]
        if len(a) < 100:
            raise ValueError(f'insufficient training rows ({len(a)})')
        te = usable[usable.date.isin(dates)]
        assert len(te) == len(dates), 'non-common sample mask'
        blocks.append(dict(dates=dates, a=a, c=c, te=te))
    return blocks


def gate_table(metrics, preds, codes, seeds, window, cand):
    """Seed-mean improvement vs B0 and skill vs B1 per code/side; paired block CI on seed-mean per-session differences."""
    rows = []
    for side, key, pk in (('low', 'pinball_low', 'pin_lo'), ('high', 'pinball_high', 'pin_hi')):
        imp, sk, series = {}, {}, []
        for c in codes:
            b0 = np.mean([metrics[(window, s, c, 'B0_frozen')][key] for s in seeds])
            b1 = metrics[(window, 0, c, 'B1_naive_vol')][key]
            o2 = np.mean([metrics[(window, s, c, cand)][key] for s in seeds])
            imp[c] = 1 - o2 / b0
            sk[c] = 1 - o2 / b1
            base = np.mean([[r[pk] for r in preds[(window, s, c, 'B0_frozen')]] for s in seeds], axis=0)
            new = np.mean([[r[pk] for r in preds[(window, s, c, cand)]] for s in seeds], axis=0)
            series.append((base - new) / b0)
        ci = np.array(ev.block_interval(np.mean(series, axis=0))) * 100
        per_seed = {c: [round((1 - metrics[(window, s, c, cand)][key] / metrics[(window, s, c, 'B0_frozen')][key]) * 100, 2) for s in seeds] for c in codes}
        rows.append(dict(window=window, candidate=cand, side=side,
                         improvement_pct={c: round(v * 100, 2) for c, v in imp.items()},
                         skill_pct={c: round(v * 100, 2) for c, v in sk.items()},
                         per_seed_improvement_pct=per_seed,
                         block_ci_pct=[round(float(x), 2) for x in ci],
                         main_gate=(bool(all(imp[c] >= .05 and sk[c] >= .03 for c in codes if c in MAIN)) if any(c in MAIN for c in codes) else None)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--seeds', default='0,1,2,3,4')
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(',')]
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    metrics, preds, windows_info = {}, {}, {}
    frames = {}
    for code in MAIN + OBS:
        f = features(code, args.db)
        frames[(code, 'adr')] = feat.attach_overnight(f, external.load_external(code, args.db), code)
        if code in external.EXTERNAL_ALT:
            frames[(code, 'alt')] = feat.attach_overnight(f, external.load_external(code, args.db, symbol=external.EXTERNAL_ALT[code]), code)
    for window in (1, 2):
        for code in MAIN + OBS:
            f = frames[(code, 'adr')]
            blocks = blocks_for(f, FEATURE_COLS_V2, window)
            windows_info[(window, code)] = dict(start=blocks[0]['dates'][0], end=blocks[-1]['dates'][-1], train_rows_first_block=int(len(blocks[0]['a'])),
                                                adr_zero_days=int(sum((b['te'].adr_ret == 0).sum() for b in blocks)))
            res, rows = run(code, blocks, 'B1_naive_vol', None, None)
            metrics[(window, 0, code, 'B1_naive_vol')] = res; preds[(window, 0, code, 'B1_naive_vol')] = rows
            for seed in seeds:
                for name, cols, params in (('B0_frozen', FEATURE_COLS_V1, {}), ('O2_overnight_small', FEATURE_COLS_V2, SMALL)):
                    res, rows = run(code, blocks, name, cols, params, seed)
                    metrics[(window, seed, code, name)] = res; preds[(window, seed, code, name)] = rows
                if (code, 'alt') in frames:
                    fa = frames[(code, 'alt')]
                    ba = blocks_for(fa, FEATURE_COLS_V2, window)
                    res, rows = run(code, ba, 'O2_kweb', FEATURE_COLS_V2, SMALL, seed)
                    metrics[(window, seed, code, 'O2_kweb')] = res; preds[(window, seed, code, 'O2_kweb')] = rows
                print(window, code, seed, {n: round(metrics[(window, seed, code, n)]['pinball_low'], 6) for n in ('B0_frozen', 'O2_overnight_small')}, flush=True)
    gates = []
    for window in (1, 2):
        gates += gate_table(metrics, preds, MAIN + OBS, seeds, window, 'O2_overnight_small')
        gates += gate_table(metrics, preds, OBS, seeds, window, 'O2_kweb')
    # CQR coverage / width (seed mean) per window/code/candidate
    cqr = []
    for (window, seed, code, name), r in metrics.items():
        cqr.append(dict(window=window, seed=seed, code=code, candidate=name, coverage=r['coverage'], width_pct=r['width'] * 100,
                        lower_miss=r['lower_miss'], upper_miss=r['upper_miss'], pinball_low=r['pinball_low'], pinball_high=r['pinball_high']))
    cq = pd.DataFrame(cqr).groupby(['window', 'code', 'candidate'])[['coverage', 'width_pct', 'lower_miss', 'upper_miss', 'pinball_low', 'pinball_high']].mean().round(4).reset_index()
    meta = dict(protocol='D4 pre-registered: O2 vs B0, seeds 0-4, W1 current 120 sessions, W2 previous 120; main gate on HK.00700+HK.09988 both sides >=5% and skill >=3% in both windows',
                seeds=seeds, input=str(Path(args.db).resolve()), input_sha256=hashlib.sha256(Path(args.db).read_bytes()).hexdigest(),
                windows={f'{w}|{c}': v for (w, c), v in windows_info.items()}, seconds=round(time.perf_counter() - t0, 1),
                gates=gates, cqr=cq.to_dict('records'))
    (out / 'protocol.json').write_text(json.dumps(meta, indent=2))
    (out / 'metrics.json').write_text(json.dumps({f'{w}|{s}|{c}|{n}': r for (w, s, c, n), r in metrics.items()}, indent=2))
    for g in gates:
        print(g['window'], g['candidate'], g['side'], 'imp', g['improvement_pct'], 'skill', g['skill_pct'], 'ci', g['block_ci_pct'], 'MAIN_GATE' if g['main_gate'] else '-')
    print(cq.to_string(index=False))


if __name__ == '__main__':
    main()
