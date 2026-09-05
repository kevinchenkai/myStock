import json
import pytest
from mystock.ml import pipeline as p, sessions as s

def test_receipt_rejects_skips_stale_and_deadline(tmp_path,monkeypatch):
    r=tmp_path/'receipt.json';monkeypatch.setenv('MYSTOCK_ML_RECEIPT',str(r));monkeypatch.setenv('MYSTOCK_ML_RUN_ID','new')
    artifact=tmp_path/'index.html';artifact.write_text('current')
    p.write_status([{'code':'US.NVDA','status':'skipped_in_session'}],None)
    with pytest.raises(ValueError):p.validate_publish(now=s.utc('2026-09-04T22:00Z'))
    p.write_status([{'code':'US.NVDA','status':'generated','target_session':'2026-09-08'}],artifact)
    assert p.validate_publish(now=s.utc('2026-09-04T22:00Z'))==artifact
    with pytest.raises(ValueError):p.validate_publish(run_id='old')
    with pytest.raises(s.Unavailable):p.validate_publish(now=s.utc('2026-09-08T14:00Z'))
    artifact.write_text('stale')
    with pytest.raises(ValueError):p.validate_publish(now=s.utc('2026-09-04T22:00Z'))

def test_readonly_and_rule_snapshot(tmp_path):
    import sqlite3
    import pandas as pd
    from mystock import db
    from mystock.collectors.futu_client import snapshot_fields
    from mystock.ml.rules import read_rule
    path=tmp_path/'prod.db';db.init_db(str(path))
    rows=snapshot_fields(pd.DataFrame([dict(code='HK.00700',lot_size=100,price_spread=.2)]),'2026-09-04T22:00Z')
    with db.get_connection(str(path)) as c: db.upsert_profiles(c,rows)
    assert read_rule('HK.00700',path,'2026-09-04').lot_size==100
    assert read_rule('HK.00700',path,'2026-09-03').lot_size is None
    with db.get_connection_readonly(path) as c:
        with pytest.raises(sqlite3.OperationalError):c.execute('delete from stock_profiles')


def test_report_all_skipped_keeps_latest(tmp_path,monkeypatch):
    from mystock.ml import report,db,config,runs
    from pathlib import Path
    path=tmp_path/'ml.db';db.init_ml_db(path)
    reports=tmp_path/'reports';reports.mkdir();latest=reports/'latest.html';latest.write_text('previous valid report')
    monkeypatch.setattr(config,'REPORTS_DIR',reports);monkeypatch.setattr(config,'ML_DIR',tmp_path)
    monkeypatch.setenv('MYSTOCK_ML_RECEIPT',str(tmp_path/'receipt.json'))
    monkeypatch.setattr(s,'prepare_daily',lambda *a,**k: (_ for _ in ()).throw(s.Unavailable('skipped_in_session')))
    assert report.build_report(db_path=path,clock=lambda:s.utc('2026-09-04T15:00Z')) is None
    assert latest.read_text()=='previous valid report'
    assert json.loads((tmp_path/'receipt.json').read_text())['status']=='all_skipped'


def test_script_all_skip_and_failure_never_calls_publish(tmp_path):
    """Every child process is mocked. No train/fetch/scp/ssh reaches the network."""
    import os,subprocess
    from pathlib import Path
    fake=tmp_path/'python';calls=tmp_path/'calls'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\ncase "$*" in\n *mystock.ml.fetch*) exit "${FETCH_RC:-0}";;\n *mystock.ml.report*) exit 0;;\n *mystock.ml.pipeline*) exit 88;;\n *) exit 1;;\nesac\n')
    fake.chmod(0o755)
    env={**os.environ,'MYSTOCK_ML_PYTHON':str(fake),'CALLS':str(calls),'MYSTOCK_ML_RECEIPT':str(tmp_path/'receipt')}
    root=Path(__file__).resolve().parents[1]
    r=subprocess.run(['bash','scripts/ml.sh','all'],cwd=root,env=env,capture_output=True)
    assert r.returncode==0 and 'mystock.ml.pipeline' not in calls.read_text()
    env['FETCH_RC']='7';calls.write_text('')
    r=subprocess.run(['bash','scripts/ml.sh','all'],cwd=root,env=env,capture_output=True)
    assert r.returncode==7 and 'mystock.ml.report' not in calls.read_text()


def test_publication_time_is_separate_and_late_excluded(tmp_path,monkeypatch):
    from mystock.ml import db,versions
    path=tmp_path/'ml.db';db.init_ml_db(path);receipt=tmp_path/'receipt.json';artifact=tmp_path/'report.html';artifact.write_text('fixture')
    monkeypatch.setenv('MYSTOCK_ML_RUN_ID','fixture-run');monkeypatch.setenv('MYSTOCK_ML_RECEIPT',str(receipt))
    row=dict(code='US.NVDA',as_of='2026-09-04',target_session='2026-09-08',source='live',close=100,l_hat=90,h_hat=110,generated_at='2026-09-04T22:00:00Z',decision_at='2026-09-04T22:00:00Z')
    with db.get_ml_connection(path) as c:versions.append(c,[row],run_id='fixture-run')
    p.write_status([dict(code='US.NVDA',target_session='2026-09-08',status='generated')],artifact,path)
    assert p.record_publication(now=s.utc('2026-09-08T13:31:00Z'))=='published_late'
    with db.get_ml_connection_readonly(path) as c:
        assert versions.load(c)==[]
        assert versions.select_by_target(versions.load(c,include_audit=True))=={}
        r=versions.load(c,include_audit=True)[0];assert r['generated_at']==row['generated_at'] and r['published_at']=='2026-09-08T13:31:00+00:00'


def test_partial_report_keeps_other_market_and_versions(tmp_path,monkeypatch):
    import pandas as pd
    from mystock.ml import report,db,config,versions
    path=tmp_path/'ml.db';db.init_ml_db(path)
    monkeypatch.setattr(config,'ML_DIR',tmp_path);monkeypatch.setattr(config,'REPORTS_DIR',tmp_path/'reports');monkeypatch.setattr(config,'TARGETS',['US.NVDA','HK.00700'])
    monkeypatch.setenv('MYSTOCK_ML_RECEIPT',str(tmp_path/'receipt.json'))
    def prepare(daily,code,*args,**kw):
        if code.startswith('HK'):raise s.Unavailable('skipped_in_session')
        return pd.DataFrame([dict(date='2026-09-03',close=100)])
    monkeypatch.setattr(s,'prepare_daily',prepare)
    monkeypatch.setattr(report,'run_backtest',lambda *a,**k:dict(backend='fixture',final_equity={'bandit':20000,'buy_hold':20000}))
    monkeypatch.setattr(report,'predict_next_day',lambda *a,**k:dict(as_of='2026-09-03',target_session='2026-09-04',close=100,L_hat=90,H_hat=110,width_pct=20,conformal=True,q_ret=.01,target_coverage=.7))
    monkeypatch.setattr(report,'_stock_section',lambda *a:'<p>synthetic fixture</p>')
    out=report.build_report(db_path=path,clock=lambda:s.utc('2026-09-04T02:00:00Z'))
    assert out and out.exists()
    r=json.loads((tmp_path/'receipt.json').read_text());assert r['status']=='partial'
    with db.get_ml_connection_readonly(path) as c:
        rows=versions.load(c);assert len(rows)==1 and rows[0]['code']=='US.NVDA'
        manifest=json.loads(__import__('pathlib').Path(rows[0]['manifest_path']).read_text())
        assert manifest['input_sha256'] and manifest['predictions'][0]['target_session']=='2026-09-04'
