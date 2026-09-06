"""D5 forward shadow: pre-open V2 predictions recorded next to a same-moment V1, never selected by the Web.

python -m mystock.ml.shadow --market HK|US [--db ...] [--no-fetch]

Control flow (real, increasing clock throughout; every attempt leaves a receipt):
  1. guard  — for each code, the market state must be 'ready' and the clock inside the pre-open
              window; nothing is fetched or fitted for a market with no eligible code.
  2. data   — HK: incremental ADR bars; US: Futu snapshot and yfinance backup captured at the same
              moment, both validated against the target's pre-market window and both stored.
  3. decide — decision_at = clock() after data; V2 and V1 predicted from one frozen daily frame
              (content hash recorded), V2 joins only rows with available_at <= decision_at.
  4. record — generated_at = clock(); a code is recorded only if V2 and V1 both succeeded and
              generated_at is still before the target deadline; both rows share a pair_id and are
              appended with status='shadow' (excluded from versions.select_by_target).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from . import config, data, db as mldb, external, predictor, preopen, sessions, versions

HK_CODES = ['HK.00700', 'HK.09988', 'HK.01810']
US_CODES = ['US.NVDA', 'US.TSLA', 'US.PDD']
STATUS = 'shadow'
PROTOCOL = 'd5-shadow-v2'


def _commit() -> str:
    try:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=config.ROOT_DIR, text=True).strip()
    except Exception:  # noqa: BLE001
        return 'unknown'


def fetch_data(market: str, db_path: str, now: str) -> dict:
    """Capture pre-open data for the market at `now`; returns per-source outcome (never raises for a bad source)."""
    info = dict(captured_at=now, sources={})
    with mldb.get_ml_connection(db_path) as conn:
        if market == 'HK':
            for code in HK_CODES:
                try:
                    rows = external.fetch(code, now, period='1mo')
                    n = mldb.upsert(conn, 'ml_external_1d', rows)
                    mldb.log_sync(conn, external.SOURCE, symbol=external.EXTERNAL_BY_CODE[code], row_count=n, status='ok' if rows else 'empty', message=f'shadow incremental for {code}')
                    info['sources'][code] = dict(source=external.SOURCE, rows=n, last=rows[-1]['date'] if rows else None)
                except Exception as e:  # noqa: BLE001
                    mldb.log_sync(conn, external.SOURCE, symbol=external.EXTERNAL_BY_CODE[code], status='error', message=f'shadow: {type(e).__name__}: {e}')
                    info['sources'][code] = dict(source=external.SOURCE, error=f'{type(e).__name__}: {e}')
        else:
            for src, getter in ((preopen.SOURCE_LIVE, lambda: _futu_rows(now)), (preopen.SOURCE_LIVE_BACKUP, lambda: preopen.fetch_live_yf(US_CODES, now))):
                try:
                    rows, rejected = getter()
                    n = mldb.upsert(conn, 'ml_preopen_quotes', rows)
                    mldb.log_sync(conn, 'preopen_live', symbol=','.join(US_CODES), row_count=n, status='ok' if rows else 'empty', message=f'{src} rejected={json.dumps(rejected)}')
                    info['sources'][src] = dict(rows={r['code']: dict(price=r['price'], event=r['source_ref']) for r in rows}, rejected=rejected)
                except Exception as e:  # noqa: BLE001
                    mldb.log_sync(conn, 'preopen_live', symbol=','.join(US_CODES), status='error', message=f'{src}: {type(e).__name__}: {e}')
                    info['sources'][src] = dict(error=f'{type(e).__name__}: {e}')
    return info


def _futu_rows(now: str):
    from ..collectors.futu_client import fetch_snapshots
    return preopen.rows_from_futu_snapshot(fetch_snapshots(US_CODES), now)


def _hk_feature_source(code, as_of, ext, deadline):
    """Which ADR row closes the overnight window for this as_of (for the audit trail)."""
    ds = sessions.calendar_dates('US')
    last = None
    for d in ds:
        if d < as_of: continue
        f = sessions.session(sessions.US_CALENDAR_CODE, d)['final_at']
        if f >= deadline: break
        last = d
    if last is None:
        return dict(source=external.SOURCE, note='no US session in window (holiday) -> 0')
    hit = ext[ext['date'].astype(str) == last]
    if hit.empty:
        return dict(source=external.SOURCE, date=last, note='row missing')
    r = hit.iloc[-1]
    return dict(source=external.SOURCE, date=last, available_at=str(r['available_at']), close=float(r['close']))


def run(market: str, db_path: str | None = None, clock=None, run_id: str | None = None, fetch: bool = True) -> dict:
    db_path = str(db_path or config.ML_DB_PATH)
    clock = clock or sessions.utc_now
    started = sessions.utc(clock())
    run_id = run_id or f"shadow-{market}-{started.strftime('%Y%m%dT%H%M%S')}"
    codes = HK_CODES if market == 'HK' else US_CODES
    receipt = dict(run_id=run_id, market=market, protocol=PROTOCOL, code_commit=_commit(), calendar=sessions.CALENDAR_VERSION,
                   started_at=started.isoformat(), db_path=str(Path(db_path).resolve()), data={}, results=[], appended=0, status='incomplete')
    out = Path(os.environ.get('MYSTOCK_ML_SHADOW_RECEIPT', str(config.ML_DIR / 'receipts' / f'{run_id}.json')))
    try:
        # 1) guard before any data access
        eligible = {}
        for code in codes:
            try:
                st = sessions.state(code, clock())
                if st['status'] != 'ready':
                    receipt['results'].append(dict(code=code, status=st['status'])); continue
                w = sessions.check_preopen_decision(code, st['as_of'], clock())
                eligible[code] = dict(as_of=st['as_of'], target=w['target_session'], deadline=w['deadline'])
            except sessions.Unavailable as e:
                receipt['results'].append(dict(code=code, status=e.status, detail=str(e)))
        if not eligible:
            receipt['status'] = 'no_eligible_code'
            return receipt
        # 2) data
        if fetch:
            receipt['data'] = fetch_data(market, db_path, sessions.utc(clock()).isoformat())
        receipt['data_cutoff'] = sessions.utc(clock()).isoformat()
        # 3) decide per code
        rows = []
        for code, info in eligible.items():
            entry = dict(code=code, as_of=info['as_of'], target_session=info['target'])
            try:
                decision_at = sessions.utc(clock())
                sessions.check_preopen_decision(code, info['as_of'], decision_at)
                daily = data.load_daily(code, db_path)
                if market == 'HK':
                    ext = external.load_external(code, db_path)
                    feature_src = _hk_feature_source(code, info['as_of'], ext, info['deadline'])
                else:
                    ext, chosen = preopen.quotes_for_prediction(code, db_path, info['target'], decision_at)
                    feature_src = dict(source=chosen['source'], available_at=str(chosen['available_at']), event=str(chosen.get('source_ref')), price=float(chosen['price'])) if chosen else dict(note='no valid quote for target')
                input_sha = versions.digest([daily.to_dict('records'), ext.to_dict('records')])
                lo_a, hi_a = config.alpha_for(code); cov = config.coverage_for(code)
                preds = {}
                for version in ('v2', 'v1'):
                    preds[version] = predictor.predict_next_day(daily, code=code, clock=clock, high_alpha=hi_a, low_alpha=lo_a,
                                                                conformal=True, target_coverage=cov, feature_version=version,
                                                                external=ext if version == 'v2' else None, decision_at=decision_at if version == 'v2' else None)
                generated_at = sessions.utc(clock())
                if generated_at >= info['deadline']:
                    raise sessions.Unavailable('missed_deadline', f'generated {generated_at.isoformat()} at/after deadline')
                for version, p in preds.items():
                    rows.append(dict(code=code, as_of=p['as_of'], target_session=p['target_session'], close=p['close'],
                                     l_hat=p['L_hat'], h_hat=p['H_hat'], width_pct=p['width_pct'], low_alpha=lo_a, high_alpha=hi_a,
                                     conformal=1, q_ret=p['q_ret'], target_coverage=cov, backend=p['backend'],
                                     source=f'shadow_{version}', status=STATUS, generated_at=generated_at.isoformat(),
                                     decision_at=decision_at.isoformat(), data_cutoff=receipt['data_cutoff'],
                                     lo_ret_raw=p['lo_ret_raw'], hi_ret_raw=p['hi_ret_raw'], feature_version=version,
                                     preopen_feature=p.get('preopen_feature'), preopen_value=p.get('preopen_value'),
                                     preopen_source=feature_src if version == 'v2' else None, v2_params=p.get('v2_params'),
                                     v2_train_rows=p.get('v2_train_rows'), input_sha256=input_sha, pair_id=run_id,
                                     protocol=PROTOCOL, code_commit=receipt['code_commit'], calendar=sessions.CALENDAR_VERSION))
                entry.update(status='recorded', decision_at=decision_at.isoformat(), generated_at=generated_at.isoformat(),
                             input_sha256=input_sha, feature_source=feature_src,
                             v2=dict(L=preds['v2']['L_hat'], H=preds['v2']['H_hat'], width_pct=preds['v2']['width_pct'], preopen_value=preds['v2'].get('preopen_value')),
                             v1=dict(L=preds['v1']['L_hat'], H=preds['v1']['H_hat'], width_pct=preds['v1']['width_pct']))
            except sessions.Unavailable as e:
                entry.update(status=e.status, detail=str(e))
            except Exception as e:  # noqa: BLE001
                entry.update(status='error', detail=f'{type(e).__name__}: {e}')
            receipt['results'].append(entry)
        # 4) record complete pairs only
        with mldb.get_ml_connection(db_path) as conn:
            receipt['appended'] = sum(versions.append(conn, [r for r in rows if r['feature_version'] == v], run_id=f'{run_id}-{v}', status=STATUS)
                                      for v in ('v2', 'v1'))
        receipt['status'] = 'completed'
        return receipt
    except Exception as e:  # noqa: BLE001
        receipt['status'] = 'error'; receipt['error'] = f'{type(e).__name__}: {e}'
        raise
    finally:
        receipt['finished_at'] = sessions.utc(clock()).isoformat()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, default=str))
        receipt['receipt_path'] = str(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--market', required=True, choices=['HK', 'US'])
    ap.add_argument('--db', default=None)
    ap.add_argument('--no-fetch', action='store_true')
    args = ap.parse_args()
    r = run(args.market, args.db, fetch=not args.no_fetch)
    for e in r['results']:
        print(e['code'], e['status'], e.get('v2', {}).get('L'), e.get('v2', {}).get('H'), e.get('v2', {}).get('preopen_value'), e.get('detail', ''))
    print('status', r['status'], 'appended', r['appended'], 'receipt', r['receipt_path'])


if __name__ == '__main__':
    main()
