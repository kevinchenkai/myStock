"""Regression cases from the independent review; only synthetic writable DBs."""
import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest
from mystock.ml import sessions as s, db, config, predictor, backfill, versions


@pytest.mark.parametrize('code,closed,half', [
    ('US.NVDA', '01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24', '11-26'),
    ('HK.00700', '01-01 02-08 02-09 03-26 03-29 04-05 05-13 06-09 07-01 09-16 10-01 10-08 12-27', '02-05 12-24 12-31'),
])
def test_2027_exchange_notices(code, closed, half):
    # NYSE official hours/calendars; HKEX CT/077/26. Sources in calendars/README.
    dates = s.session_days(code, '2027-01-01', '2027-12-31')
    weekdays = {d.strftime('%Y-%m-%d') for d in pd.bdate_range('2027-01-01', '2027-12-31')}
    assert weekdays - set(dates) == {'2027-' + d for d in closed.split()}
    assert {d for d in dates if (s.session(code, d)['close'] - s.session(code, d)['open']).total_seconds() < 18000} == {'2027-' + d for d in half.split()}
    assert s.next_session(code, '2026-12-31') == '2027-01-04'
    assert s.state(code, s.utc('2027-01-04T22:00Z'))['as_of'] == '2027-01-04'
    assert len(s.window(code, '2027-01-05', 20)) == 20
    with pytest.raises(s.Unavailable, match='freeze_calendar'):
        s.next_session(code, '2027-12-31')


def test_calendar_warning_boundary_and_report_receipt(tmp_path, monkeypatch):
    from mystock.ml import report
    assert s.calendar_days_left(s.utc('2027-11-01T00:00Z')) == 60
    assert s.calendar_warnings(s.utc('2027-11-01T00:00Z')) == []
    assert s.calendar_warnings(s.utc('2027-11-02T00:00Z'))[0]['calendar_days_left'] == 59
    assert s.calendar_warnings(s.utc('2028-01-01T00:00Z'))[0]['status'] == 'calendar_expired'
    path = tmp_path/'ml.db'
    monkeypatch.setattr(config, 'ML_DIR', tmp_path)
    monkeypatch.setattr(config, 'REPORTS_DIR', tmp_path/'reports')
    monkeypatch.setattr(config, 'TARGETS', ['US.NVDA'])
    monkeypatch.setenv('MYSTOCK_ML_RECEIPT', str(tmp_path/'receipt.json'))
    monkeypatch.setattr(s, 'prepare_daily', lambda *a, **k: (_ for _ in ()).throw(s.Unavailable('skipped_in_session')))
    assert report.build_report(db_path=path, clock=lambda: s.utc('2027-11-02T15:00Z')) is None
    receipt = json.loads((tmp_path/'receipt.json').read_text())
    assert receipt['warnings'][0]['calendar_days_left'] == 59
    with db.get_ml_connection_readonly(path) as c:
        assert c.execute("select count(*) from ml_sync_log where status='calendar_expiring'").fetchone()[0] == 1


def test_fetch_expired_calendar_has_actionable_receipt_without_network(tmp_path, monkeypatch):
    from mystock.ml import fetch
    monkeypatch.setattr(config, 'ML_DB_PATH', tmp_path/'ml.db')
    monkeypatch.setattr(config, 'PROD_DB_PATH', tmp_path/'absent.db')
    monkeypatch.setattr(config, 'TARGETS', ['US.NVDA', 'HK.00700'])
    monkeypatch.setenv('MYSTOCK_ML_RECEIPT', str(tmp_path/'receipt.json'))
    monkeypatch.setattr(db, 'now_str', lambda: '2028-01-04T22:00:00Z')
    def forbidden(*a, **kw):
        pytest.fail('calendar failure must precede online fetching')
    monkeypatch.setattr(fetch, 'fetch_daily', forbidden)
    monkeypatch.setattr(fetch, 'fetch_hourly', forbidden)
    with pytest.raises(RuntimeError, match='freeze_calendar'):
        fetch.run()
    receipt = json.loads((tmp_path/'receipt.data.json').read_text())
    assert receipt['status'] == 'data_failed' and len(receipt['markets']) == 2
    assert receipt['warnings'][0]['status'] == 'calendar_expired'
    assert not (tmp_path/'receipt.json').exists()
    with db.get_ml_connection_readonly(tmp_path/'ml.db') as c:
        assert c.execute("select count(*) from ml_sync_log where source='session' and message like '%freeze_calendar%'").fetchone()[0] == 2


def fixture_daily():
    return pd.DataFrame([dict(date=d, open=100+i*.2, high=103+i*.2, low=98+i*.2,
                              close=101+i*.2, adj_close=101+i*.2, volume=100+i,
                              synced_at='2026-09-04T22:00:00Z')
                         for i, d in enumerate(s.session_days('US.NVDA', '2026-05-01', '2026-09-04'))])


@pytest.mark.parametrize('historical', [False, True])
@pytest.mark.parametrize('fault', ['internal_gap', 'last_feature', 'no_valid_features'])
def test_prediction_never_falls_back_or_fits_on_feature_gap(monkeypatch, historical, fault):
    daily = fixture_daily()
    if fault == 'internal_gap':
        daily = daily[daily.date != '2026-08-26']
    elif fault == 'last_feature':
        daily.loc[daily.index[-1], 'adj_close'] = float('nan')
    else:
        daily = daily.tail(10)
    def forbidden(*a, **kw):
        pytest.fail('model constructed before feature validity check')
    monkeypatch.setattr(predictor, 'IntervalModel', forbidden)
    with pytest.raises(s.Unavailable) as error:
        predictor.predict_next_day(daily, code='US.NVDA', historical=historical,
                                   clock=lambda: s.utc('2026-09-04T22:00Z'))
    assert error.value.status == 'feature_gap'


def test_recompute_rejects_wrong_asof_before_write(tmp_path, monkeypatch):
    path = tmp_path/'ml.db'
    monkeypatch.setattr(config, 'ML_DIR', tmp_path)
    monkeypatch.setattr(config, 'TARGETS', ['US.NVDA'])
    monkeypatch.setattr(backfill.mldata, 'load_daily', lambda *a: fixture_daily())
    monkeypatch.setattr(backfill, '_predict', lambda *a: {'as_of': '2026-08-25'})
    assert backfill.recompute_gaps(since='2026-09-04', db_path=path, verbose=False) == 0
    with db.get_ml_connection_readonly(path) as c:
        assert c.execute('select count(*) from ml_prediction_versions').fetchone()[0] == 0
        assert c.execute('select count(*) from ml_predictions').fetchone()[0] == 0


@pytest.mark.parametrize('source', ['recomputed', 'backfill', 'unknown', 'live'])
def test_only_timing_verified_live_enters_legacy(tmp_path, source):
    path = tmp_path/'ml.db'; db.init_ml_db(path)
    row = dict(code='US.NVDA', as_of='2026-09-04', target_session='2026-09-08',
               source=source, close=100, l_hat=90, h_hat=110)
    with db.get_ml_connection(path) as c:
        assert versions.append(c, [row], run_id='fixture') == 1
        assert versions.append(c, [row], run_id='fixture') == 0
        assert c.execute('select count(*) from ml_predictions').fetchone()[0] == 0
        assert len(versions.load(c, include_audit=True)) == 1
        if source == 'recomputed':
            assert backfill.missing_dates(c, 'US.NVDA', pd.DataFrame([{'date': '2026-09-04'}])) == []


def test_standalone_publish_uses_explicit_receipt_across_shells(tmp_path):
    """Real shell + receipt validator; replace scp with a local recorder only."""
    import os
    import sys
    receipt = tmp_path/'trained.json'; artifact = tmp_path/'index.html'
    artifact.write_text('fixture report')
    path = tmp_path/'ml.db'; db.init_ml_db(path)
    # Fixed simulated process clock keeps this regression independent of real time.
    row = dict(code='US.NVDA', as_of='2027-12-29', target_session='2027-12-30',
               source='live', close=100, l_hat=90, h_hat=110,
               generated_at='2027-12-29T22:00:00Z', decision_at='2027-12-29T22:00:00Z')
    with db.get_ml_connection(path) as c:
        versions.append(c, [row], run_id='trained-run')
    import hashlib
    receipt.write_text(json.dumps(dict(run_id='trained-run', status='generated',
        artifact=str(artifact), sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        db_path=str(path), markets=[dict(code='US.NVDA', target_session='2027-12-30', status='generated')])))
    fake_scp = tmp_path/'scp'; calls = tmp_path/'calls'
    fake_scp.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SCP_CALLS"\n')
    fake_scp.chmod(0o755)
    env = {k:v for k,v in os.environ.items() if k not in ('MYSTOCK_ML_RUN_ID', 'MYSTOCK_ML_RECEIPT')}
    fixed_python = tmp_path/'fixed-python'
    fixed_python.write_text(f'#!{sys.executable}\n' + '''import runpy, sys
from mystock.ml import sessions
sessions.utc_now = lambda: sessions.utc('2027-12-29T22:01:00Z')
module = sys.argv[2]
sys.argv = [module, *sys.argv[3:]]
runpy.run_module(module, run_name='__main__')
''')
    fixed_python.chmod(0o755)
    env.update(MYSTOCK_ML_PYTHON=str(fixed_python), PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               PATH=str(tmp_path)+os.pathsep+env['PATH'], SCP_CALLS=str(calls))
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['bash','scripts/ml.sh','publish',str(receipt)], cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert str(artifact) in calls.read_text()
    assert json.loads(receipt.read_text())['run_id'] == 'trained-run'
    calls.unlink()
    for args in ([], [str(tmp_path/'missing.json')]):
        result = subprocess.run(['bash','scripts/ml.sh','publish',*args], cwd=root, env=env, capture_output=True)
        assert result.returncode != 0 and not calls.exists()
    artifact.write_text('tampered')
    result = subprocess.run(['bash','scripts/ml.sh','publish',str(receipt)], cwd=root, env=env, capture_output=True)
    assert result.returncode != 0 and not calls.exists()
