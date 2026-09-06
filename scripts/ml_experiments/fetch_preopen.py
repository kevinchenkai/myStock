"""US pre-open quotes into ml_preopen_quotes. Standalone; not wired into ml.sh.

Historical (yfinance prepost hourly, 730 days):
    python -m scripts.ml_experiments.fetch_preopen --db <ml.db> --history
Live (Futu snapshot at run time; requires OpenD):
    python -m scripts.ml_experiments.fetch_preopen --db <ml.db> --futu
"""
import argparse

from mystock.ml import db as mldb, preopen, sessions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--codes', default=','.join(preopen.US_TARGETS))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--history', action='store_true')
    g.add_argument('--futu', action='store_true')
    args = ap.parse_args()
    mldb.init_ml_db(args.db)
    now = sessions.utc_now().isoformat()
    codes = args.codes.split(',')
    with mldb.get_ml_connection(args.db) as conn:
        if args.history:
            for code in codes:
                try:
                    rows = preopen.fetch_history(code, now)
                    n = mldb.upsert(conn, 'ml_preopen_quotes', rows)
                    rng = (rows[0]['date'], rows[-1]['date']) if rows else ('', '')
                    mldb.log_sync(conn, 'preopen_history', symbol=code, range_start=rng[0], range_end=rng[1], row_count=n, status='ok' if rows else 'empty')
                    print(code, n, *rng, flush=True)
                except Exception as e:  # noqa: BLE001
                    mldb.log_sync(conn, 'preopen_history', symbol=code, status='error', message=f'{type(e).__name__}: {e}')
                    raise SystemExit(1)
        else:
            from mystock.collectors.futu_client import fetch_snapshots
            rows, rejected = preopen.rows_from_futu_snapshot(fetch_snapshots(codes), now)
            n = mldb.upsert(conn, 'ml_preopen_quotes', rows)
            mldb.log_sync(conn, 'preopen_live', symbol=','.join(codes), row_count=n, status='ok' if rows else 'empty', message=f'futu rejected={rejected}')
            print('futu snapshot rows', n, 'rejected', rejected, now)


if __name__ == '__main__':
    main()
