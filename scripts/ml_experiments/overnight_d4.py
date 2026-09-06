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

from mystock.ml import evaluation as ev, external, features as feat, preopen
from mystock.ml.features import FEATURE_COLS_V1, FEATURE_COLS_V2, FEATURE_COLS_V2_US, OVERNIGHT_COLS, PREOPEN_COLS
from scripts.ml_experiments.upgrade_matrix import EXTRA, features
from scripts.ml_experiments.overnight_feasibility import run, SMALL

MARKETS = {
    'HK': dict(main=['HK.00700', 'HK.09988'], obs=['HK.01810'], cols=FEATURE_COLS_V2, extra_cols=OVERNIGHT_COLS),
    'US': dict(main=['US.NVDA', 'US.TSLA'], obs=['US.PDD'], cols=FEATURE_COLS_V2_US, extra_cols=PREOPEN_COLS),
}
MAIN, OBS = MARKETS['HK']['main'], MARKETS['HK']['obs']


def blocks_for(f, cols, window, extra_cols=OVERNIGHT_COLS):
    common_all = f.dropna(subset=FEATURE_COLS_V1 + EXTRA + extra_cols + ['y_low_ret', 'y_high_ret']).date.tolist()
    common = common_all[-120:] if window == 1 else common_all[-240:-120]
    if len(common) != 120:
        raise ValueError('window not available')
    start = f.dropna(subset=extra_cols).date.min()
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


def _paired(preds, window, seeds, code, cand, base, pk, denom):
    """Per-date seed-mean pinball difference (base - cand) / denom, keyed by as_of; pairs by actual date."""
    b, n = {}, {}
    for sd in seeds:
        for r in preds[(window, sd, code, base)]:
            b.setdefault(r['as_of'], []).append(r[pk])
        for r in preds[(window, sd, code, cand)]:
            n.setdefault(r['as_of'], []).append(r[pk])
    return {d: (np.mean(b[d]) - np.mean(n[d])) / denom for d in sorted(set(b) & set(n))}


def gate_table(metrics, preds, codes, seeds, window, cand, base='B0_frozen', naive='B1_naive_vol'):
    """Seed-mean improvement vs `base` and skill vs `naive` per code/side; block CIs paired by actual date:
    per code, and for the main group (dates present for every main code, equal weight). The main gate
    requires every main code to pass on its own; observational codes never affect it."""
    rows = []
    for side, key, pk in (('low', 'pinball_low', 'pin_lo'), ('high', 'pinball_high', 'pin_hi')):
        imp, sk, ci_code, paired = {}, {}, {}, {}
        for c in codes:
            bseeds = [sd for sd in seeds if (window, sd, c, base) in metrics]
            b0 = np.mean([metrics[(window, sd, c, base)][key] for sd in bseeds])
            b1 = metrics[(window, 0, c, naive)][key]
            o2 = np.mean([metrics[(window, sd, c, cand)][key] for sd in seeds])
            imp[c] = 1 - o2 / b0
            sk[c] = 1 - o2 / b1
            paired[c] = _paired(preds, window, seeds, c, cand, base, pk, b0)
            ci_code[c] = [round(float(x), 2) for x in np.array(ev.block_interval(list(paired[c].values()))) * 100]
        main = [c for c in codes if c in MAIN]
        main_ci = None
        if main:
            common = sorted(set.intersection(*[set(paired[c]) for c in main]))
            if len(common) >= 10:
                main_ci = [round(float(x), 2) for x in np.array(ev.block_interval([np.mean([paired[c][d] for c in main]) for d in common])) * 100]
        per_seed = {c: [round((1 - metrics[(window, sd, c, cand)][key] / metrics[(window, sd, c, base)][key]) * 100, 2) for sd in seeds if (window, sd, c, base) in metrics] for c in codes}
        per_code_gate = {c: bool(imp[c] >= .05 and sk[c] >= .03) for c in codes}
        rows.append(dict(window=window, candidate=cand, side=side, base=base, naive=naive,
                         improvement_pct={c: round(v * 100, 2) for c, v in imp.items()},
                         skill_pct={c: round(v * 100, 2) for c, v in sk.items()},
                         per_seed_improvement_pct=per_seed, per_code_gate=per_code_gate,
                         block_ci_pct_by_code=ci_code, main_block_ci_pct=main_ci,
                         main_gate=(bool(all(per_code_gate[c] for c in main)) if main else None)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--seeds', default='0,1,2,3,4')
    ap.add_argument('--market', default='HK', choices=list(MARKETS))
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(',')]
    global MAIN, OBS
    mk = MARKETS[args.market]; MAIN, OBS = mk['main'], mk['obs']; V2, XCOLS = mk['cols'], mk['extra_cols']
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    metrics, preds, windows_info = {}, {}, {}
    frames = {}
    for code in MAIN + OBS:
        f = features(code, args.db)
        if args.market == 'HK':
            frames[(code, 'adr')] = feat.attach_overnight(f, external.load_external(code, args.db), code)
            if code in external.EXTERNAL_ALT:
                frames[(code, 'alt')] = feat.attach_overnight(f, external.load_external(code, args.db, symbol=external.EXTERNAL_ALT[code]), code)
        else:
            frames[(code, 'adr')] = preopen.attach_preopen(f, preopen.load_preopen(code, args.db), code)
    for window in (1, 2):
        for code in MAIN + OBS:
            f = frames[(code, 'adr')]
            blocks = blocks_for(f, V2, window, XCOLS)
            windows_info[(window, code)] = dict(start=blocks[0]['dates'][0], end=blocks[-1]['dates'][-1], train_rows_first_block=int(len(blocks[0]['a'])),
                                                zero_days=int(sum((b['te'][XCOLS[0]] == 0).sum() for b in blocks)))
            res, rows = run(code, blocks, 'B1_naive_vol', None, None)
            metrics[(window, 0, code, 'B1_naive_vol')] = res; preds[(window, 0, code, 'B1_naive_vol')] = rows
            for seed in seeds:
                # O3 = V1 features with the same small capacity: separates feature gain from capacity change.
                for name, cols, params in (('B0_frozen', FEATURE_COLS_V1, {}), ('O2_overnight_small', V2, SMALL), ('O3_small_only', FEATURE_COLS_V1, SMALL)):
                    res, rows = run(code, blocks, name, cols, params, seed)
                    metrics[(window, seed, code, name)] = res; preds[(window, seed, code, name)] = rows
                if (code, 'alt') in frames:
                    # KWEB proxy on its own history mask, with B0 / B1 rebuilt on that same mask for a matched comparison.
                    fa = frames[(code, 'alt')]
                    ba = blocks_for(fa, V2, window, XCOLS)
                    if seed == seeds[0]:
                        res, rows = run(code, ba, 'B1_kweb_mask', None, None)
                        metrics[(window, 0, code, 'B1_kweb_mask')] = res; preds[(window, 0, code, 'B1_kweb_mask')] = rows
                    for name, cols, params in (('B0_kweb_mask', FEATURE_COLS_V1, {}), ('O2_kweb', V2, SMALL)):
                        res, rows = run(code, ba, name, cols, params, seed)
                        metrics[(window, seed, code, name)] = res; preds[(window, seed, code, name)] = rows
                print(window, code, seed, {n: round(metrics[(window, seed, code, n)]['pinball_low'], 6) for n in ('B0_frozen', 'O2_overnight_small')}, flush=True)
    gates = []
    for window in (1, 2):
        gates += gate_table(metrics, preds, MAIN + OBS, seeds, window, 'O2_overnight_small')
        gates += gate_table(metrics, preds, MAIN + OBS, seeds, window, 'O3_small_only')
        for row in gate_table(metrics, preds, MAIN + OBS, seeds, window, 'O2_overnight_small', base='O3_small_only'):
            row['candidate'] = 'O2_vs_O3'; gates.append(row)
        if any((window, seeds[0], c, 'O2_kweb') in metrics for c in OBS):
            gates += gate_table(metrics, preds, OBS, seeds, window, 'O2_kweb', base='B0_kweb_mask', naive='B1_kweb_mask')
    # CQR coverage / width (seed mean) per window/code/candidate
    cqr = []
    for (window, seed, code, name), r in metrics.items():
        cqr.append(dict(window=window, seed=seed, code=code, candidate=name, coverage=r['coverage'], width_pct=r['width'] * 100,
                        lower_miss=r['lower_miss'], upper_miss=r['upper_miss'], pinball_low=r['pinball_low'], pinball_high=r['pinball_high']))
    cq = pd.DataFrame(cqr).groupby(['window', 'code', 'candidate'])[['coverage', 'width_pct', 'lower_miss', 'upper_miss', 'pinball_low', 'pinball_high']].mean().round(4).reset_index()
    meta = dict(protocol=f'D4 pre-registered ({args.market}): O2 vs B0, seeds 0-4, W1 current 120 sessions, W2 previous 120; main gate on {MAIN} both sides >=5% and skill >=3% in both windows; O3 = V1 + small capacity control; KWEB vs B0/B1 rebuilt on its own mask', market=args.market,
                seeds=seeds, input=str(Path(args.db).resolve()), input_sha256=hashlib.sha256(Path(args.db).read_bytes()).hexdigest(),
                windows={f'{w}|{c}': v for (w, c), v in windows_info.items()}, seconds=round(time.perf_counter() - t0, 1),
                gates=gates, cqr=cq.to_dict('records'))
    (out / 'protocol.json').write_text(json.dumps(meta, indent=2))
    (out / 'metrics.json').write_text(json.dumps({f'{w}|{s}|{c}|{n}': r for (w, s, c, n), r in metrics.items()}, indent=2))
    (out / 'predictions.json').write_text(json.dumps({f'{w}|{s}|{c}|{n}': r for (w, s, c, n), r in preds.items()}))
    for g in gates:
        print(g['window'], g['candidate'], g['side'], 'imp', g['improvement_pct'], 'skill', g['skill_pct'], 'ci', g['block_ci_pct_by_code'], 'main_ci', g['main_block_ci_pct'],
              {True: 'MAIN_GATE', False: 'main_fail', None: 'n/a'}[g['main_gate']])
    print(cq.to_string(index=False))


if __name__ == '__main__':
    main()
