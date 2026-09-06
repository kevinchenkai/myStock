"""D1: fetch overnight external daily bars (ADR per HK stock) into ml_external_1d.

python -m scripts.ml_experiments.fetch_external --db data/ml/mystock_ml.db [--period 5y] [--codes HK.00700,...]

Standalone by design: not wired into ml.sh until the overnight round is validated.
Writes only ml_external_1d and ml_sync_log; idempotent upsert on (symbol, date).
"""
import argparse
import sqlite3

from mystock.ml import db as mldb, external, sessions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, help='ML database path (explicit; never defaults)')
    ap.add_argument('--period', default=external.PERIOD)
    ap.add_argument('--codes', default=','.join(external.EXTERNAL_BY_CODE))
    ap.add_argument('--alt', action='store_true', help='fetch the pre-registered control proxies (EXTERNAL_ALT) instead of ADRs')
    args = ap.parse_args()
    mapping = external.EXTERNAL_ALT if args.alt else external.EXTERNAL_BY_CODE
    mldb.init_ml_db(args.db)
    now = sessions.utc_now().isoformat()
    with mldb.get_ml_connection(args.db) as conn:
        for code in args.codes.split(','):
            if code not in mapping:
                continue
            symbol = mapping[code]
            try:
                rows = external.fetch(code, now, period=args.period, symbol=symbol)
                n = mldb.upsert(conn, 'ml_external_1d', rows)
                rng = (rows[0]['date'], rows[-1]['date']) if rows else ('', '')
                mldb.log_sync(conn, external.SOURCE, symbol=symbol, range_start=rng[0], range_end=rng[1],
                              row_count=n, status='ok' if rows else 'empty', message=f'for {code}')
                print(code, symbol, n, *rng, flush=True)
            except Exception as e:  # noqa: BLE001
                mldb.log_sync(conn, external.SOURCE, symbol=symbol, status='error', message=f'{code}: {type(e).__name__}: {e}')
                print(code, symbol, 'ERROR', e, flush=True)
                raise SystemExit(1)


if __name__ == '__main__':
    main()
