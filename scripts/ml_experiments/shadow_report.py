"""Summarise D5 shadow rows (status='shadow') against realised next-session high/low. Read-only.

python -m scripts.ml_experiments.shadow_report [--db ...]
"""
import argparse
import json

import numpy as np
import pandas as pd

from mystock.ml import config, data, db as mldb, versions


def pinball(y, p, a):
    d = y - p
    return np.maximum(a * d, (a - 1) * d)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--db', default=None); args = ap.parse_args()
    out = []
    with mldb.get_ml_connection_readonly(args.db) as c:
        rows = [r for r in versions.load(c, include_audit=True) if r['status'] == 'shadow']
    if not rows:
        print(json.dumps(dict(shadow_rows=0)))
        return
    df = pd.DataFrame(rows)
    for code, g in df.groupby('code'):
        daily = data.load_daily(code, args.db).set_index('date')
        for version, gv in g.groupby('feature_version'):
            gv = gv.drop_duplicates(['target_session'], keep='last')
            matured = gv[gv.target_session.isin(daily.index)]
            if matured.empty:
                out.append(dict(code=code, version=version, recorded=len(gv), matured=0)); continue
            close = matured.close.astype(float).to_numpy()
            yh = daily.loc[matured.target_session, 'high'].to_numpy() / close - 1
            yl = daily.loc[matured.target_session, 'low'].to_numpy() / close - 1
            lo_a, hi_a = config.alpha_for(code)
            pl = pinball(yl, matured.lo_ret_raw.astype(float).to_numpy(), lo_a).mean()
            ph = pinball(yh, matured.hi_ret_raw.astype(float).to_numpy(), hi_a).mean()
            L = matured.l_hat.astype(float).to_numpy() / close - 1; H = matured.h_hat.astype(float).to_numpy() / close - 1
            out.append(dict(code=code, version=version, recorded=len(gv), matured=len(matured), pinball_low=round(float(pl), 6), pinball_high=round(float(ph), 6),
                            coverage=round(float(np.mean((yl >= L) & (yh <= H))), 3), width_pct=round(float(np.mean(H - L) * 100), 2)))
    print(pd.DataFrame(out).to_string(index=False))


if __name__ == '__main__':
    main()
