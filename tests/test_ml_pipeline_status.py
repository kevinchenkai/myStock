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
