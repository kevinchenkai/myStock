from contextlib import closing
import pandas as pd
import pytest
from mystock.ml import db, sessions
from scripts.ml_experiments import rebuild_history as history


def test_confirmed_weather_closures_do_not_insert_fake_training_days():
    assert sessions.next_session('HK.00700', '2023-08-31') == '2023-09-04'
    assert sessions.next_session('HK.00700', '2023-09-07') == '2023-09-11'
    with pytest.raises(sessions.Unavailable, match='2023-09-08'):
        sessions.session('HK.00700', '2023-09-08')


def daily_fixture():
    rows = []
    for i, day in enumerate(sessions.session_days('US.NVDA', '2024-01-02', '2026-09-04')):
        c = 100 + i * .1
        rows.append(dict(date=day, open=c, high=c+2, low=c-2, close=c+1,
            adj_close=c+1, volume=10000+i, dividends=0, splits=0,
            synced_at='2026-09-05T01:00:00Z'))
    return pd.DataFrame(rows)


def test_refits_never_receive_target_or_future_ohlc():
    daily = daily_fixture()
    daily.loc[daily.date == '2026-09-04', ['open', 'high', 'low', 'close', 'adj_close']] = 1e9
    seen = []
    def predict(past, **kwargs):
        as_of = past.date.iloc[-1]
        seen.append(as_of)
        assert past.close.max() < 1e9
        assert kwargs['historical'] and kwargs['conformal']
        return dict(as_of=as_of, target_session=sessions.next_session('US.NVDA', as_of),
            L_hat=90., H_hat=110., close=100.)
    rows = history.predict_history('US.NVDA', daily, ['2026-09-03', '2026-09-04'],
        sessions.utc('2026-09-05T06:00:00Z'), predict=predict)
    assert seen == ['2026-09-02', '2026-09-03']
    assert all(r['training_label_cutoff'] < r['calibration_cutoff'] <= r['as_of'] < r['target_session'] for r in rows)
    assert all(r['source'] == 'recomputed' and r['decision_at'] is None and r['published_at'] is None for r in rows)


def test_stale_last_feature_fails_instead_of_silently_reusing_old_prediction():
    def stale(past, **kwargs):
        return dict(as_of='2026-09-01', target_session='2026-09-02', L_hat=90., H_hat=110.)
    with pytest.raises(ValueError, match='stale'):
        history.predict_history('US.NVDA', daily_fixture(), ['2026-09-04'],
            sessions.utc('2026-09-05T06:00:00Z'), predict=stale)


def test_incomplete_provider_response_cannot_erase_existing_bars(tmp_path, monkeypatch):
    from mystock.ml import fetch
    path = tmp_path / 'ml.db'; db.init_ml_db(path)
    row = dict(symbol='NVDA', futu_code='US.NVDA', ts_utc='2026-09-03 13:30:00',
        ts_et='2026-09-03 09:30:00', open=100., high=110., low=90., close=105., volume=100.,
        synced_at='2026-09-05T01:00:00Z')
    with closing(db.get_ml_connection(path)) as conn:
        db.upsert(conn, 'ml_quotes_1h', [row])
    monkeypatch.setattr(fetch, 'fetch_hourly', lambda *args: [dict(row, close=106.)])
    before = dict(stocks=[dict(code='US.NVDA', training_daily_gaps=[], hourly_gaps=['2026-09-03'])])
    history.repair(path, before, tmp_path, sessions.utc('2026-09-05T06:00:00Z'))
    with closing(db.get_ml_connection_readonly(path)) as conn:
        assert conn.execute('select close from ml_quotes_1h').fetchone()[0] == 105.


def test_futu_end_labels_preserve_lunch_and_reject_mixed_prices():
    from scripts.ml_experiments.import_futu_hourly import normalize
    code='HK.00700'; day='2026-04-20'
    frame=pd.DataFrame([dict(code=code,time_key=f'{day} {t}:00',open=100.,high=110.,low=90.,close=105.,volume=100.)
        for t in ['10:30','11:30','12:00','14:00','15:00','16:00']])
    daily=dict(open=100.,high=110.,low=90.,close=105.)
    existing=[dict(ts_utc=f'{day} {t}:00',open=100.02 if i==0 else 100.,high=110.,low=90.,close=105.)
        for i,t in enumerate(['01:30','02:30','03:30'])]
    now=sessions.utc('2026-09-05T06:00:00Z')
    rows=normalize(code,day,frame,daily,existing,now,'fixture-sha')
    assert [r['ts_et'][11:16] for r in rows] == ['09:30','10:30','11:30','13:00','14:00','15:00']
    assert rows[0]['open'] == daily['open']
    assert all(r['data_source']=='futu_none' and r['source_ref']=='fixture-sha' for r in rows)
    bad=frame.copy(); bad.loc[1,'open']=101.
    with pytest.raises(ValueError,match='overlap'):
        normalize(code,day,bad,daily,existing,now,'fixture-sha')
    with pytest.raises(ValueError,match='bucket grid'):
        normalize(code,day,frame.iloc[:-1],daily,existing,now,'fixture-sha')


def test_hourly_source_migration_keeps_legacy_rows(tmp_path):
    import sqlite3
    path=tmp_path/'old.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE ml_quotes_1h(symbol TEXT, futu_code TEXT, ts_utc TEXT, ts_et TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, synced_at TEXT, PRIMARY KEY(symbol,ts_utc))')
        conn.execute("INSERT INTO ml_quotes_1h(symbol,ts_utc,close) VALUES('NVDA','2026-09-03 13:30:00',100)")
    db.init_ml_db(path); db.init_ml_db(path)
    with closing(db.get_ml_connection_readonly(path)) as conn:
        row=conn.execute('SELECT close,data_source,source_ref FROM ml_quotes_1h').fetchone()
    assert tuple(row)==(100,'yfinance',None)
