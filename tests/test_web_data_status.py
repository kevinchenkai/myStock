"""Synthetic provenance/collection contract tests; never read the runtime DB."""
from contextlib import nullcontext
import sqlite3
import json
import pandas as pd
import pytest
from mystock import db
from mystock.collectors import futu_client as fc, yf_client as yc
from mystock.collectors.snapshot import fields, source_time
from mystock.pipelines import init_load
from mystock.web import data_status as ds

NOW = '2026-09-05T15:00:00+00:00'


def quote(code='US.AAPL', date='2026-09-04', collected='2026-09-05T01:00:00+00:00'):
    return dict(futu_code=code,yf_symbol=code.split('.')[1],date=date,open=100,high=110,low=95,close=105,synced_at=collected)


def snapshot(code='US.AAPL'):
    return dict(code=code,update_time='2026-09-04 16:00:00',last_price=105,prev_close_price=100,
                open_price=100,high_price=110,low_price=95,volume_ratio=0,suspension=False,
                sec_status='NORMAL',lot_size=100 if code.startswith('HK.') else 1,price_spread=.01)


@pytest.fixture
def conn(tmp_path):
    path=tmp_path/'synthetic.db';db.init_db(str(path))
    c=db.get_connection(str(path))
    yield c
    c.close()


@pytest.mark.parametrize('code,now,expected',[
 ('US.AAPL','2026-09-07T18:00:00Z','2026-09-04'), # Labor Day
 ('HK.00700','2026-09-06T01:00:00Z','2026-09-04'),
 ('US.AAPL','2026-09-08T15:00:00Z','2026-09-04'), # in session
 ('US.AAPL','2026-03-09T19:59:00Z','2026-03-06'), # DST
 ('US.AAPL','2026-03-09T22:00:00Z','2026-03-09'),
 ('HK.00700','2026-09-07T08:00:00Z','2026-09-04'), # CAS not final
 ('HK.00700','2026-09-07T10:00:00Z','2026-09-07'),
 ('US.AAPL','2026-09-05T01:00:00Z','2026-09-04'),
])
def test_expected_final_session(code,now,expected):
    r=ds.daily_state(code,None,now)
    assert r['expected_session']==expected and r['status']=='empty'


@pytest.mark.parametrize('row,status',[(quote(),'current'),(quote(date='2026-09-03'),'stale'),
 (quote(collected='2026-09-05 01:00:00'),'unknown'),(quote(collected='2099-09-05T01:00:00Z'),'unknown'),(quote(collected='2026-09-04T18:00:00Z'),'awaiting_final_data'),
 (quote(date='2026-09-08'),'unknown'),(None,'empty')])
def test_daily_states(row,status):
    assert ds.daily_state('US.AAPL',row,NOW)['status']==status


@pytest.mark.parametrize('code,now',[('JP.123','2026-09-05T15:00:00Z'),('US.AAPL','2028-01-01T15:00:00Z'),('US.AAPL','2026-09-05 15:00:00')])
def test_unknown_never_fresh(code,now):
    assert ds.daily_state(code,quote(),now)['status']=='unknown'


@pytest.mark.parametrize('code,time,expected',[
 ('US.AAPL','2026-07-01 09:30:00','2026-07-01T13:30:00+00:00'),
 ('US.AAPL','2026-01-02 09:30:00','2026-01-02T14:30:00+00:00'),
 ('HK.00700','2026-07-01 09:30:00','2026-07-01T01:30:00+00:00'),
 ('US.AAPL','2026-11-01 01:30:00',None),('US.AAPL','2026-03-08 02:30:00',None),
 ('JP.123','2026-07-01 09:30:00',None),('US.AAPL','bad',None)])
def test_source_time(code,time,expected):
    assert source_time(code,time)['snapshot_time_utc']==expected


@pytest.mark.parametrize('value,expected',[(None,None),(float('nan'),None),('False',0),(False,0),('True',1),(True,1),('unknown',None)])
def test_missing_and_suspension(value,expected):
    r=fields('US.AAPL',dict(suspension=value,last_price=float('inf'),volume_ratio=0))
    assert r['suspension']==expected and r['last_price'] is None and r['volume_ratio']==0


def test_batch_failure_preserves_cache_and_records_all_codes(conn,monkeypatch):
    codes=['US.AAPL']+[f'US.TEST{i}' for i in range(400)]
    monkeypatch.setattr(db,'all_traded_codes',lambda c:codes)
    monkeypatch.setattr(init_load,'_now',lambda:NOW)
    monkeypatch.setattr(init_load.time,'sleep',lambda n:None)
    calls=[]
    def fetch(batch):
        calls.append(batch)
        if len(calls)==2:raise RuntimeError('SECRET ACCOUNT /private/path')
        return pd.DataFrame([snapshot()])
    monkeypatch.setattr(fc,'fetch_snapshots',fetch)
    init_load.collect_market_snapshot(conn)
    assert list(map(len,calls))==[400,1]
    assert conn.execute('select count(*) from collection_status').fetchone()[0]==401
    assert conn.execute("select status from collection_status where code='US.AAPL'").fetchone()[0]=='ok'
    before=dict(conn.execute("select * from stock_profiles where futu_code='US.AAPL'").fetchone())
    monkeypatch.setattr(db,'all_traded_codes',lambda c:['US.AAPL'])
    monkeypatch.setattr(fc,'fetch_snapshots',lambda batch:pd.DataFrame([dict(code='US.AAPL',last_price=99)]))
    init_load.collect_market_snapshot(conn)
    assert dict(conn.execute("select * from stock_profiles where futu_code='US.AAPL'").fetchone())==before
    r=ds.stock_status(conn,'US.AAPL',NOW)['snapshot']
    assert r['status']=='unsupported' and r['has_cache'] and r['last_success_at']==NOW
    assert 'SECRET' not in json.dumps(ds.overview(conn,NOW))


def test_latest_summary_ok_does_not_prove_stock_success(conn):
    db.write_sync_log(conn,'yfinance',None,None,1,'ok')
    assert ds.stock_status(conn,'US.AAPL',NOW)['daily']['status']=='empty'
    db.upsert_quotes(conn,[quote()])
    db.write_collection_status(conn,'yfinance','US.AAPL','error',NOW,'request_failed')
    r=ds.stock_status(conn,'US.AAPL',NOW)['daily']
    assert r['status']=='cached' and r['attempt_status']=='error' and r['scope']=='stock'


def test_additive_migration_restore_and_sources_independent(tmp_path):
    path=tmp_path/'old.db'
    c=sqlite3.connect(path);c.execute('create table stock_profiles (futu_code TEXT PRIMARY KEY, long_name TEXT, synced_at TEXT)')
    c.execute("insert into stock_profiles values ('US.AAPL','Synthetic company','old')");c.commit()
    backup=sqlite3.connect(tmp_path/'backup.db');c.backup(backup);c.close()
    db.init_db(str(path));db.init_db(str(path))
    with db.get_connection(str(path)) as c:
        # Original deployed schema already had these numeric legacy columns.
        # The minimalist old fixture is only for migration/compatibility checks.
        assert c.execute('select long_name from stock_profiles').fetchone()[0]=='Synthetic company'
        assert 'snapshot_time_utc' in {r[1] for r in c.execute('pragma table_info(stock_profiles)')}
    restored=sqlite3.connect(tmp_path/'restored.db');backup.backup(restored)
    assert restored.execute('pragma integrity_check').fetchone()[0]=='ok'
    assert restored.execute('select * from stock_profiles').fetchall()==[('US.AAPL','Synthetic company','old')]
    restored.close();backup.close()


def test_snapshot_profile_updates_independent(conn):
    db.upsert_profiles(conn,[dict(futu_code='US.AAPL',long_name='Synthetic',synced_at='profile-time')])
    db.upsert_profiles(conn,fc.snapshot_fields(pd.DataFrame([snapshot()]),NOW))
    db.upsert_profiles(conn,[dict(futu_code='US.AAPL',long_name='New name',synced_at='new-profile-time')])
    r=dict(conn.execute('select * from stock_profiles').fetchone())
    assert r['snapshot_time_utc']=='2026-09-04T20:00:00+00:00'
    assert r['snap_synced_at']==NOW and r['synced_at']=='new-profile-time'


def test_readonly_old_database_and_redacted_errors(tmp_path,monkeypatch):
    from mystock.web.app import app
    path=tmp_path/'old.db';c=sqlite3.connect(path)
    c.execute('create table stock_profiles (futu_code TEXT PRIMARY KEY)');c.commit();c.close()
    old=app.config.copy();app.config.update(TESTING=True,DB_PATH=str(path),DATA_STATUS_NOW=NOW)
    try:
        monkeypatch.setattr(db,'init_db',lambda *a:pytest.fail('GET attempted migration'))
        monkeypatch.setattr(fc,'fetch_snapshots',lambda *a:pytest.fail('GET called API'))
        before=path.read_bytes();client=app.test_client()
        r=client.get('/api/stock/US.AAPL/snapshot')
        assert r.status_code==200 and r.json['snapshot']['migration_required']
        assert client.get('/api/data-status').status_code==200
        assert path.read_bytes()==before
        assert client.get('/api/stock/BAD/snapshot').status_code==400
        app.config['DB_PATH']=str(tmp_path/'PRIVATE-secret-missing.db')
        r=client.get('/api/data-status');assert r.status_code==503
        assert b'PRIVATE' not in r.data
    finally:
        app.config.clear();app.config.update(old)


def test_yfinance_exception_is_not_empty(monkeypatch):
    class Failed:
        @property
        def info(self):raise RuntimeError('synthetic network failure')
    monkeypatch.setattr(yc.yf,'Ticker',lambda *a:Failed())
    with pytest.raises(yc.YFError):yc.fetch_profile('US.AAPL')


def test_in_session_bar_is_pending():
    assert ds.daily_state('US.AAPL',quote(date='2026-09-08'),'2026-09-08T15:00:00Z')['status']=='pending'


def test_snapshot_unknown_prev_close_and_stale_failure(conn):
    row=snapshot();row['prev_close_price']=0
    db.upsert_profiles(conn,fc.snapshot_fields(pd.DataFrame([row]),NOW))
    db.write_collection_status(conn,'futu_snapshot','US.AAPL','error',NOW,'request_failed')
    r=ds.stock_status(conn,'US.AAPL','2026-09-10T15:00:00Z')['snapshot']
    assert r['change'] is None and r['change_pct'] is None
    assert r['freshness']=='stale' and r['status']=='error'


def test_source_summary_dates_and_empty_stock_attempt(conn):
    db.upsert_quotes(conn,[quote()])
    db.write_sync_log(conn,'yfinance',None,None,1,'ok')
    db.write_collection_status(conn,'yfinance','US.AAPL','empty',NOW,'not_returned')
    assert ds.aggregate(conn,'yfinance')['data_as_of']=='2026-09-04'
    r=ds.stock_status(conn,'US.AAPL',NOW)['daily']
    assert r['status']=='cached' and r['attempt_status']=='empty'
