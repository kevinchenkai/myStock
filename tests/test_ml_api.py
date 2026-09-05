import pytest
from mystock.web.app import app,get_db
from mystock.ml import db,versions

@pytest.fixture
def api(tmp_path):
    ml=tmp_path/'ml.db';db.init_ml_db(ml)
    app.config.update(TESTING=True,ML_DB_PATH=str(ml),DB_PATH=str(ml))
    yield app.test_client(),ml

def test_contracts_readonly_missing_invalid(api,tmp_path):
    c,path=api
    with app.app_context():
        con=get_db()
        with pytest.raises(Exception):con.execute('create table forbidden(a)')
        con.close()
    assert c.get('/api/ml/v2/latest?codes=BAD').status_code==400
    assert c.get('/api/ml/strategy?codes=BAD').status_code==400
    assert c.get('/api/ml/v2/compare?codes=US.NVDA').status_code==400
    assert c.get('/api/ml/strategy?mode=inventory&codes=US.NVDA').status_code==400
    assert c.get('/api/ml/v2/review?days=0').status_code==400
    assert c.get('/api/ml/v2/review?days=nan').status_code==400
    assert c.get('/api/ml/v2/review?end=bad').status_code==400
    app.config['ML_DB_PATH']=str(tmp_path/'missing.db')
    assert c.get('/api/ml/v2/latest').status_code==503
    assert c.get('/api/ml/strategy').status_code==503

def test_sessions_keep_middle_gap_and_order_codes(api):
    c,path=api
    r=c.get('/api/ml/v2/review?codes=US.NVDA,US.NVDA,HK.00700&days=3&end=2026-09-03').get_json()
    assert [v['code'] for v in r['results']]==['US.NVDA','HK.00700']
    assert [v['date'] for v in r['results'][0]['rows']]==['2026-09-01','2026-09-02','2026-09-03']
    assert all(v['status']=='missing_daily' for v in r['results'][0]['rows'])

def test_inventory_explicit_schema_and_params(api):
    c,path=api
    params='codes=US.NVDA&end=2026-09-03&days=3&initial_cash=20000&initial_inventory=0&order_qty=10&max_inventory=100&max_holding=20&lot_size=1'
    r=c.get('/api/ml/strategy?mode=inventory&'+params)
    assert r.status_code==200
    obj=r.get_json();assert obj['schema_version']==2 and obj['mode']=='inventory'
    assert len(obj['results'][0]['policies'][0]['rows'])==3
    assert obj['results'][0]['policies'][0]['summary']['fee_status']=='gross_fees_missing'
    assert c.get('/api/ml/v2/compare?'+params+'&fee_bps=NaN').status_code==400


def test_prediction_later_than_order_remains_fact_only(api):
    from mystock.ml.service import facts
    _,p=api
    with db.get_ml_connection(p) as c:
        c.execute("insert into ml_orders(order_id,code,trd_side,price,qty,create_time) values('fixture-order','US.NVDA','BUY',100,1,'2026-09-04 10:00:00')");c.commit()
    rows=facts(p,'US.NVDA','2026-09-04',{'published_at':'2026-09-04T15:00:00Z'})
    assert rows[0]['prediction_available_at_order'] is False
    assert rows[0]['hypothetical_pnl'] is None


def test_recomputed_history_is_discoverable_but_never_becomes_live(api, monkeypatch):
    from mystock.ml import sessions
    c, path = api
    now = sessions.utc('2026-09-05T06:00:00Z')
    monkeypatch.setattr(sessions, 'utc_now', lambda: now)
    with db.get_ml_connection(path) as conn:
        db.upsert(conn, 'ml_quotes_1d', [dict(symbol='NVDA', futu_code='US.NVDA',date=d,
            open=100,high=105,low=95,close=101,adj_close=101,volume=1000,
            dividends=0,splits=0,synced_at=now.isoformat()) for d in ['2026-09-02','2026-09-03']])
        db.upsert(conn, 'ml_quotes_1h', [dict(symbol='NVDA',futu_code='US.NVDA',
            ts_utc=f'2026-09-03 {hour}:30:00',ts_et=f'2026-09-03 {hour-4:02}:30:00',
            open=100,high=105,low=95,close=101,volume=100,synced_at=now.isoformat()) for hour in range(13,20)])
        versions.append(conn, [dict(code='US.NVDA',as_of='2026-09-02',target_session='2026-09-03',
            close=101,l_hat=90,h_hat=110,source='recomputed',generated_at=now.isoformat())],run_id='history-fixture')
    query='codes=US.NVDA&days=1&end=2026-09-03'
    live=c.get('/api/ml/v2/review?'+query).get_json()['results'][0]['rows'][0]
    assert live['status']=='missing_prediction' and live['has_recomputed'] is True
    assert live['prediction']['prediction_id'] is None
    rebuilt=c.get('/api/ml/v2/review?'+query+'&source=recomputed').get_json()['results'][0]['rows'][0]
    assert rebuilt['status']=='ok' and rebuilt['prediction_status']=='recomputed'
    assert rebuilt['prediction']['run_id']=='history-fixture'
    assert rebuilt['prediction']['decision_at'] is None and rebuilt['prediction']['published_at'] is None
    assert c.get('/api/ml/v2/latest?codes=US.NVDA').get_json()['results'][0]['prediction'] is None
    absent=c.get('/api/ml/v2/review?codes=US.NVDA&days=1&end=2026-09-04').get_json()['results'][0]['rows'][0]
    assert absent['has_recomputed'] is False


def test_ml_next_accepts_both_url_spellings(api):
    c,_=api
    assert c.get('/ml-next').status_code == 200
    assert c.get('/ml-next/').status_code == 200
