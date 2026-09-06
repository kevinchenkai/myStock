"""D5 forward shadow: pre-open V2 predictions recorded next to a same-moment V1, never selected by the Web.

python -m mystock.ml.shadow --market HK|US [--db ...] [--no-fetch]

Per run (manual, inside the pre-open window of that market):
  1. data: HK -> incremental ADR bars; US -> Futu pre-market snapshot, yfinance backup.
  2. predict: for each code, V2 (pre-open features) and V1 (frozen) with the same clock.
  3. record: append both to ml_prediction_versions with status='shadow' (excluded from
     versions.select_by_target, so reports and the Web keep using production rows) and
     write a receipt under data/ml/receipts/.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import config, data, db as mldb, external, predictor, preopen, sessions, versions

HK_CODES = ['HK.00700', 'HK.09988', 'HK.01810']
US_CODES = ['US.NVDA', 'US.TSLA', 'US.PDD']
STATUS = 'shadow'


def fetch_data(market: str, db_path: str, now: str) -> dict:
    info = {}
    with mldb.get_ml_connection(db_path) as conn:
        if market == 'HK':
            for code in HK_CODES:
                rows = external.fetch(code, now, period='1mo')
                n = mldb.upsert(conn, 'ml_external_1d', rows)
                mldb.log_sync(conn, external.SOURCE, symbol=external.EXTERNAL_BY_CODE[code], row_count=n, status='ok' if rows else 'empty', message=f'shadow incremental for {code}')
                info[code] = dict(source=external.SOURCE, rows=n, last=rows[-1]['date'] if rows else None)
        else:
            try:
                from ..collectors.futu_client import fetch_snapshots
                rows = preopen.rows_from_futu_snapshot(fetch_snapshots(US_CODES), now)
                src = preopen.SOURCE_LIVE
            except Exception as e:  # noqa: BLE001
                rows = preopen.fetch_live_yf(US_CODES, now)
                src = preopen.SOURCE_LIVE_BACKUP
                info['futu_error'] = f'{type(e).__name__}: {e}'
            n = mldb.upsert(conn, 'ml_preopen_quotes', rows)
            mldb.log_sync(conn, 'preopen_live', symbol=','.join(US_CODES), row_count=n, status='ok', message=src)
            for r in rows:
                info[r['code']] = dict(source=src, price=r['price'], available_at=r['available_at'])
    return info


def run(market: str, db_path: str | None = None, now=None, run_id: str | None = None, fetch: bool = True) -> dict:
    db_path = str(db_path or config.ML_DB_PATH)
    now_dt = sessions.utc(now or sessions.utc_now())
    now_iso = now_dt.isoformat()
    run_id = run_id or f"shadow-{market}-{now_dt.strftime('%Y%m%dT%H%M%S')}"
    codes = HK_CODES if market == 'HK' else US_CODES
    receipt = dict(run_id=run_id, market=market, decision_at=now_iso, db_path=str(Path(db_path).resolve()), data={}, results=[])
    if fetch:
        receipt['data'] = fetch_data(market, db_path, now_iso)
    rows = []
    for code in codes:
        daily = data.load_daily(code, db_path)
        ext = external.load_external(code, db_path) if market == 'HK' else preopen.load_preopen_any(code, db_path)
        lo_a, hi_a = config.alpha_for(code)
        cov = config.coverage_for(code)
        entry = dict(code=code)
        for version in ('v2', 'v1'):
            try:
                p = predictor.predict_next_day(daily, code=code, clock=lambda: now_dt, high_alpha=hi_a, low_alpha=lo_a,
                                               conformal=True, target_coverage=cov, feature_version=version,
                                               external=ext if version == 'v2' else None)
                row = dict(code=code, as_of=p['as_of'], target_session=p['target_session'], close=p['close'],
                           l_hat=p['L_hat'], h_hat=p['H_hat'], width_pct=p['width_pct'], low_alpha=lo_a, high_alpha=hi_a,
                           conformal=1, q_ret=p['q_ret'], target_coverage=cov, backend='lightgbm',
                           source=f'shadow_{version}', status=STATUS, generated_at=now_iso, decision_at=now_iso,
                           lo_ret_raw=p['lo_ret_raw'], hi_ret_raw=p['hi_ret_raw'], feature_version=version,
                           preopen_feature=p.get('preopen_feature'), preopen_value=p.get('preopen_value'),
                           preopen_source=(receipt['data'].get(code) or {}).get('source'))
                rows.append(row)
                entry[version] = dict(status='recorded', target_session=p['target_session'], L=p['L_hat'], H=p['H_hat'], width_pct=p['width_pct'],
                                      preopen_value=p.get('preopen_value'))
            except sessions.Unavailable as e:
                entry[version] = dict(status=e.status, detail=str(e))
        receipt['results'].append(entry)
    with mldb.get_ml_connection(db_path) as conn:
        # Distinct run identity per feature version: the same (code, as_of, target) legitimately
        # has two shadow rows, one V2 and one V1, and identities must not collide.
        receipt['appended'] = sum(versions.append(conn, [r for r in rows if r['feature_version'] == v], run_id=f'{run_id}-{v}', status=STATUS)
                                  for v in ('v2', 'v1'))
    out = Path(os.environ.get('MYSTOCK_ML_SHADOW_RECEIPT', str(config.ML_DIR / 'receipts' / f'{run_id}.json')))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False))
    receipt['receipt_path'] = str(out)
    return receipt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--market', required=True, choices=['HK', 'US'])
    ap.add_argument('--db', default=None)
    ap.add_argument('--no-fetch', action='store_true')
    args = ap.parse_args()
    r = run(args.market, args.db, fetch=not args.no_fetch)
    for e in r['results']:
        print(e['code'], 'v2', e['v2']['status'], 'v1', e['v1']['status'], e['v2'].get('L'), e['v2'].get('H'), e['v2'].get('preopen_value'))
    print('appended', r['appended'], 'receipt', r['receipt_path'])


if __name__ == '__main__':
    main()
