"""Summarise D5 shadow rows against realised, confirmed next-session high/low. Read-only.

python -m scripts.ml_experiments.shadow_report [--db ...] [--json out.json]

Rules (pre-registered): one pair per (code, target_session) = the earliest complete V1/V2 pair
(same pair_id); a target counts as matured only when the session's final_at has passed and the
stored daily bar is confirmed (sessions.daily_final); raw pinball uses the alphas frozen in the
row; B1 is the naive_vol interval rebuilt from daily data up to as_of; planned sessions per
market run from the first recorded target to the last target whose deadline has passed.
"""
import argparse
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

from mystock.ml import config, data, db as mldb, evaluation as ev, sessions, versions
from mystock.ml.features import build_features


def pinball(y, p, a):
    d = y - p
    return np.maximum(a * d, (a - 1) * d)


def naive_interval(daily, as_of, lo_a, hi_a):
    f = build_features(daily[daily.date <= as_of].reset_index(drop=True))
    tr = f.dropna(subset=['vol_20d', 'y_low_ret', 'y_high_ret'])
    last = f.iloc[-1]
    if len(tr) < 50 or not np.isfinite(last.vol_20d):
        return None
    v = ev.scale(tr.vol_20d); vt = ev.scale([last.vol_20d])[0]
    return float(np.quantile(tr.y_low_ret / v, lo_a) * vt), float(np.quantile(tr.y_high_ret / v, hi_a) * vt)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--db', default=None); ap.add_argument('--json', default=None); args = ap.parse_args()
    now = sessions.utc_now()
    with mldb.get_ml_connection_readonly(args.db) as c:
        rows = [r for r in versions.load(c, include_audit=True) if r['status'] == 'shadow']
    attempts = []
    for p in glob.glob(str(config.ML_DIR / 'receipts' / 'shadow-*.json')):
        try:
            r = json.loads(Path(p).read_text())
            for e in r.get('results', []):
                attempts.append(dict(run_id=r['run_id'], market=r['market'], code=e['code'], status=e['status'], target=e.get('target_session')))
        except Exception:  # noqa: BLE001
            continue
    att = pd.DataFrame(attempts) if attempts else pd.DataFrame(columns=['run_id', 'market', 'code', 'status', 'target'])
    report = dict(as_of=now.isoformat(), shadow_rows=len(rows), attempts=len(att), codes=[])
    if not rows:
        print(json.dumps(report)); return
    df = pd.DataFrame(rows)
    for code, g in df.groupby('code'):
        daily = data.load_daily(code, args.db)
        drows = {r['date']: r for r in daily.to_dict('records')}
        pairs = []
        for target, gt in g.groupby('target_session'):
            complete = [pid for pid, gp in gt.groupby('pair_id') if set(gp.feature_version) == {'v1', 'v2'}]
            if not complete: continue
            pid = min(complete, key=lambda x: gt[gt.pair_id == x].decision_at.min())   # earliest complete pair
            gp = gt[gt.pair_id == pid]
            v1 = gp[gp.feature_version == 'v1'].iloc[0]; v2 = gp[gp.feature_version == 'v2'].iloc[0]
            pairs.append(dict(target=target, v1=v1, v2=v2))
        planned = sessions.session_days(code, min(g.target_session), now.astimezone(sessions.ZoneInfo('Asia/Hong_Kong' if sessions.market(code) == 'HK' else 'America/New_York')).date().isoformat())
        planned = [d for d in planned if sessions.session(code, d)['deadline'] <= now]
        mat = []
        for pr in pairs:
            d = drows.get(pr['target'])
            if d and sessions.daily_final(code, d, now):
                mat.append(pr)
        entry = dict(code=code, planned_sessions=len(planned), attempts=int((att.code == code).sum()) if len(att) else 0,
                     attempt_statuses=att[att.code == code].status.value_counts().to_dict() if len(att) else {},
                     recorded_pairs=len(pairs), matured_pairs=len(mat))
        if mat:
            close = np.array([float(p['v1'].close) for p in mat])
            yh = np.array([drows[p['target']]['high'] for p in mat]) / close - 1
            yl = np.array([drows[p['target']]['low'] for p in mat]) / close - 1
            lo_a = np.array([float(p['v1'].low_alpha) for p in mat]); hi_a = np.array([float(p['v1'].high_alpha) for p in mat])
            res = {}
            for v in ('v1', 'v2'):
                lo = np.array([float(p[v].lo_ret_raw) for p in mat]); hi = np.array([float(p[v].hi_ret_raw) for p in mat])
                L = np.array([float(p[v].l_hat) for p in mat]) / close - 1; H = np.array([float(p[v].h_hat) for p in mat]) / close - 1
                res[v] = dict(pinball_low=pinball(yl, lo, lo_a), pinball_high=pinball(yh, hi, hi_a),
                              coverage=float(np.mean((yl >= L) & (yh <= H))), width_pct=float(np.mean(H - L) * 100),
                              lower_miss=float(np.mean(yl < L)), upper_miss=float(np.mean(yh > H)))
            b1 = [naive_interval(daily, p['v1'].as_of, float(p['v1'].low_alpha), float(p['v1'].high_alpha)) for p in mat]
            if all(b is not None for b in b1):
                blo = np.array([b[0] for b in b1]); bhi = np.array([b[1] for b in b1])
                res['b1'] = dict(pinball_low=pinball(yl, blo, lo_a), pinball_high=pinball(yh, bhi, hi_a))
            for side in ('low', 'high'):
                k = f'pinball_{side}'
                d1, d2 = res['v1'][k], res['v2'][k]
                entry[f'{side}_v1'] = round(float(d1.mean()), 6); entry[f'{side}_v2'] = round(float(d2.mean()), 6)
                entry[f'{side}_improvement_pct'] = round(float((1 - d2.mean() / d1.mean()) * 100), 2)
                entry[f'{side}_paired_block_ci_pct'] = [round(float(x), 2) for x in np.array(ev.block_interval((d1 - d2) / d1.mean())) * 100] if len(mat) >= 10 else None
                if 'b1' in res:
                    entry[f'{side}_naive_skill_pct'] = round(float((1 - d2.mean() / res['b1'][k].mean()) * 100), 2)
            for v in ('v1', 'v2'):
                entry[f'{v}_coverage'] = round(res[v]['coverage'], 3); entry[f'{v}_width_pct'] = round(res[v]['width_pct'], 2)
                entry[f'{v}_miss'] = f"{res[v]['lower_miss']:.3f}/{res[v]['upper_miss']:.3f}"
            entry['by_source'] = {str((p['v2'].preopen_source or {}).get('source')): 0 for p in mat}
            for p in mat: entry['by_source'][str((p['v2'].preopen_source or {}).get('source'))] += 1
        report['codes'].append(entry)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
