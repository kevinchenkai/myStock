"""Explicit-path historical audit, quote repair, and daily refit (never live).

Use a backed-up isolated database. Each action is separate; no broker access,
static report publication, or implicit collection during reconstruction.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path

import numpy as np
from mystock.ml import config, data, db, sessions, versions, runs, predictor
from mystock.ml.features import build_features, FEATURE_COLS, LABEL_COLS
from mystock.code_map import futu_to_yf

PROTOCOL = 'historical-daily-refit-120-v1'


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def audit(path, end, now):
    result = dict(end=end, checked_at=now.isoformat(), stocks=[])
    for code in config.TARGETS:
        dates = sessions.window(code, end, 120)
        if now < sessions.session(code, dates[-1])['final_at']:
            raise ValueError('Requested window includes an unfinished session')
        daily = data.load_daily(code, path)
        hourly = data.load_hourly(code, path)
        dm = {r['date']: r for r in daily.to_dict('records')}
        bm = {d: g.to_dict('records') for d, g in hourly.groupby('day')}
        with closing(db.get_ml_connection_readonly(path)) as conn:
            ps = versions.select_by_target(versions.load(conn, code), allow_recomputed=True)
        history = sessions.session_days(code, min(dm) if dm else dates[0], dates[-1])
        gaps = [d for d in history if d not in dm or not sessions.daily_final(code, dm[d], now)]
        result['stocks'].append(dict(code=code, start=dates[0], end=dates[-1],
            daily_rows=len(daily), training_daily_gaps=gaps,
            daily_gaps=[d for d in dates if d in gaps],
            hourly_gaps=[d for d in dates if not data.complete_bars(code, bm.get(d, []), now)],
            prediction_gaps=[d for d in dates if d not in ps]))
    return result


def repair(path, before, out, now):
    from mystock.ml import fetch  # Network is reachable only in the repair action.
    log = []
    for stock in before['stocks']:
        code = stock['code']
        for kind, gaps in [('daily', stock['training_daily_gaps']), ('hourly', stock['hourly_gaps'])]:
            if not gaps:
                continue
            # A broad provider window can locate internal holes that MAX(date) misses.
            rows = (fetch.fetch_daily(code, now.isoformat()) if kind == 'daily'
                    else fetch.fetch_hourly(code, now.isoformat()))
            save(out / f"fetched-{code}-{kind}.json", rows)
            chosen = [r for r in rows if (r['date'] if kind == 'daily' else r['ts_et'][:10]) in gaps]
            with closing(db.get_ml_connection(path)) as conn:
                # Avoid mixing two provider bucket grids. Replace only a returned,
                # verified complete day; incomplete responses cannot erase old bars.
                if kind == 'hourly':
                    groups = {}
                    for r in chosen:
                        groups.setdefault(r['ts_et'][:10], []).append(r)
                    chosen = []
                    with conn:
                        for day, bars in groups.items():
                            if data.complete_bars(code, bars, now):
                                conn.execute('DELETE FROM ml_quotes_1h WHERE symbol=? AND substr(ts_et,1,10)=?', (futu_to_yf(code), day))
                                chosen.extend(bars)
                        db.upsert(conn, 'ml_quotes_1h', chosen)
                else:
                    db.upsert(conn, 'ml_quotes_1d', chosen)
            item = dict(code=code, kind=kind, requested_days=gaps, returned_rows=len(rows), written_rows=len(chosen))
            log.append(item)
            save(out / 'repair-log.json', log)
            print(code, kind, 'returned', len(rows), 'written', len(chosen), flush=True)
    return log


def predict_history(code, daily, dates, now, predict=predictor.predict_next_day):
    """Slice BEFORE feature/label creation; target OHLC never enters the fit."""
    output = []
    for i, target in enumerate(dates):
        as_of = sessions.window(code, target, 2)[0]
        past = daily[daily.date <= as_of].copy()
        clean = sessions.prepare_daily(past, code, now, live=False)
        frame = build_features(clean).replace([np.inf, -np.inf], np.nan)
        train = frame.dropna(subset=FEATURE_COLS + LABEL_COLS)
        if len(train) < 250:
            raise ValueError(f'{code} {target}: insufficient training history')
        alpha_low, alpha_high = config.alpha_for(code)
        prediction = predict(past, code=code, historical=True, clock=lambda: now,
            seed=0, low_alpha=alpha_low, high_alpha=alpha_high, conformal=True,
            target_coverage=config.coverage_for(code))
        if prediction['as_of'] != as_of or prediction['target_session'] != target:
            raise ValueError(f'{code} {target}: latest usable feature is stale')
        if not (0 < prediction['L_hat'] <= prediction['H_hat'] and np.isfinite(prediction['H_hat'])):
            raise ValueError(f'{code} {target}: invalid predicted interval')
        ncal = max(5, int(len(train) * .25))
        fit = train.iloc[:-(ncal + 1)]
        row = dict(prediction, code=code, l_hat=prediction['L_hat'], h_hat=prediction['H_hat'],
            low_alpha=alpha_low, high_alpha=alpha_high, source='recomputed',
            generated_at=sessions.utc_now().isoformat(), decision_at=None, published_at=None,
            protocol=PROTOCOL, seed=0, backend='lightgbm' if predictor._HAS_LGB else 'sklearn',
            training_label_cutoff=sessions.next_session(code, fit.date.iloc[-1]),
            calibration_cutoff=sessions.next_session(code, train.date.iloc[-1]),
            training_rows=len(fit), calibration_rows=ncal)
        output.append(row)
        if (i + 1) % 20 == 0:
            print(code, f'{i + 1}/{len(dates)} daily refits', flush=True)
    return output


def rebuild(path, end, out, now):
    check = audit(path, end, now)
    if any(s['training_daily_gaps'] for s in check['stocks']):
        raise ValueError('Daily quote gaps remain; inspect audit before reconstruction')
    manifest, mp = runs.start(path, protocol=PROTOCOL)
    manifest.update(end=end, fit_schedule='every as_of', target_sessions=120,
        historical_only=True, calibration_fraction=.25, fit_calibration_gap=1,
        hourly_gaps={s['code']: s['hourly_gaps'] for s in check['stocks']},
        quote_repair_receipts=[json.loads(p.read_text()) for p in sorted(out.glob('import-*.json'))],
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    save(mp, manifest)
    rows = []
    try:
        for stock in check['stocks']:
            code = stock['code']
            stock_rows = predict_history(code, data.load_daily(code, manifest['input_path']), sessions.window(code, end, 120), now)
            rows.extend(stock_rows)
            save(out / f'predictions-{code}.json', stock_rows)
        # Complete all six stocks before appending any versions.
        if len(rows) != len(config.TARGETS) * 120:
            raise ValueError('Incomplete reconstruction')
        with closing(db.get_ml_connection(path)) as conn:
            versions.append(conn, rows, run_id=manifest['run_id'], manifest_path=mp.resolve())
        runs.finish(manifest, mp, rows, [{'status': 'offline_recomputed', 'rows': len(rows)}])
        # Retain actual fit/calibration label cutoffs, not generic as_of bounds.
        manifest['predictions'] = [{k: r[k] for k in ('code', 'as_of', 'target_session', 'training_label_cutoff', 'calibration_cutoff', 'training_rows', 'calibration_rows')} for r in rows]
        save(mp, manifest)
        save(out / 'rebuild.json', dict(run_id=manifest['run_id'], manifest=str(mp.resolve()), input_sha256=manifest['input_sha256'], rows=len(rows)))
        return rows
    except Exception as exc:
        manifest.update(status='failed', error=str(exc))
        save(mp, manifest)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['audit', 'repair', 'rebuild'])
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--end', required=True)
    args = ap.parse_args()
    path = Path(args.db).resolve(strict=True)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    now = sessions.utc_now()
    check = audit(path, args.end, now)
    save(out / f'audit-before-{args.action}.json', check)
    if args.action == 'repair':
        repair(path, check, out, now)
    elif args.action == 'rebuild':
        rebuild(path, args.end, out, now)
    after = audit(path, args.end, sessions.utc_now())
    save(out / f'audit-after-{args.action}.json', after)
    for stock in after['stocks']:
        print(stock['code'], {k: len(v) for k, v in stock.items() if k.endswith('gaps')}, flush=True)


if __name__ == '__main__':
    main()
