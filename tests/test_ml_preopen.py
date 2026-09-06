"""US pre-open quotes (revised after Codex review P03/P04): strict 08:00 bar, event-time validation,
per-source validity before priority, decision-time bound, append-only quote versions."""
import pandas as pd
import pytest

from mystock.ml import features, preopen, sessions as s


def test_feature_versions_us():
    assert features.FEATURE_COLS_V2_US == features.FEATURE_COLS_V1 + ['pre_ret']


def test_snapshot_time_and_us_window():
    assert preopen.snapshot_time('US.NVDA', '2026-09-04') == s.utc('2026-09-04T13:00:00Z')
    assert preopen.snapshot_time('US.NVDA', '2026-03-06') == s.utc('2026-03-06T14:00:00Z')
    w = s.preopen_window('US.NVDA', '2026-09-04')
    assert w['target_session'] == '2026-09-08' and w['earliest'] == s.utc('2026-09-08T13:00:00Z') and w['deadline'] == s.utc('2026-09-08T13:30:00Z')
    with pytest.raises(s.Unavailable, match='missed_deadline'):
        s.check_preopen_decision('US.NVDA', '2026-09-04', s.utc('2026-09-08T13:30:00Z'))


def _hourly(stamps_prices):
    idx = pd.DatetimeIndex([pd.Timestamp(t, tz='America/New_York') for t, _ in stamps_prices]).tz_convert('UTC')
    return pd.DataFrame({'Open': [p for _, p in stamps_prices], 'Close': [p for _, p in stamps_prices]}, index=idx)


def test_rows_from_yf_hourly_strict_0800_bar():
    df = _hourly([('2026-09-04 07:00', 100.0), ('2026-09-04 08:00', 101.0), ('2026-09-04 08:30', 103.0), ('2026-09-04 09:00', 105.0),
                  ('2026-09-05 08:00', 99.0), ('2026-09-08 08:00', 102.0), ('2026-09-08 08:00', 102.5)])
    rows = preopen.rows_from_yf_hourly(df, 'US.NVDA', '2026-09-08T12:00:00Z')
    assert [(r['date'], r['price']) for r in rows] == [('2026-09-04', 101.0)]          # C3: 08:30 bar rejected; 09-08 not yet available
    rows = preopen.rows_from_yf_hourly(df, 'US.NVDA', '2026-09-08T13:00:00Z')
    assert [(r['date'], r['price']) for r in rows] == [('2026-09-04', 101.0), ('2026-09-08', 102.0)]   # duplicate 09-08 bar ignored
    assert rows[1]['available_at'] == '2026-09-08T13:00:00+00:00'


def test_futu_snapshot_event_time_validation():
    now = '2026-09-08T13:05:00Z'
    ok = pd.DataFrame([dict(code='US.NVDA', pre_market_price=231.5, prev_close_price=230.0, update_time='2026-09-08 09:04:30')])
    rows, rejected = preopen.rows_from_futu_snapshot(ok, now)
    assert rows[0]['date'] == '2026-09-08' and rows[0]['available_at'] == '2026-09-08T13:05:00+00:00' and rows[0]['source_ref'] == '2026-09-08T13:04:30+00:00' and not rejected
    stale = pd.DataFrame([dict(code='US.NVDA', pre_market_price=231.5, update_time='2026-09-04 09:04:30')])     # C4: old event pasted onto today
    rows, rejected = preopen.rows_from_futu_snapshot(stale, now)
    assert rows == [] and 'outside pre-market window' in rejected['US.NVDA']
    future = pd.DataFrame([dict(code='US.NVDA', pre_market_price=231.5, update_time='2026-09-08 09:20:00')])
    assert preopen.rows_from_futu_snapshot(future, now)[1]['US.NVDA'] == 'event time after capture time'
    inf = pd.DataFrame([dict(code='US.NVDA', pre_market_price=float('inf'), update_time='2026-09-08 09:04:30')])
    assert 'price' in preopen.rows_from_futu_snapshot(inf, now)[1]['US.NVDA']
    nofield = pd.DataFrame([dict(code='US.NVDA', last_price=231.5, update_time='2026-09-08 09:04:30')])
    assert 'price' in preopen.rows_from_futu_snapshot(nofield, now)[1]['US.NVDA']
    assert preopen.rows_from_futu_snapshot(ok, '2026-09-07T13:05:00Z')[1]['US.NVDA'].endswith('not a US session')   # Labor Day


def test_select_quote_validity_before_priority_and_attach_bound():
    deadline = s.session('US.NVDA', '2026-09-08')['deadline']
    quotes = pd.DataFrame([dict(date='2026-09-08', price=110.0, available_at=deadline.isoformat(), source='futu_snapshot'),        # C7: primary at the deadline
                           dict(date='2026-09-08', price=108.0, available_at='2026-09-08T13:00:00+00:00', source='yfinance_live'),
                           dict(date='2026-09-08', price=107.0, available_at='2026-09-08T13:00:00+00:00', source='yfinance_1h')])
    chosen = preopen.select_quote(quotes, 'US.NVDA', '2026-09-08', s.utc('2026-09-08T13:05:00Z'))
    assert chosen.iloc[0]['source'] == 'yfinance_live' and chosen.iloc[0]['price'] == 108.0
    assert preopen.select_quote(quotes, 'US.NVDA', '2026-09-08', s.utc('2026-09-08T12:59:00Z')) is None     # nothing available yet
    df = pd.DataFrame(dict(date=['2026-09-04'], close=[100.0]))
    out = preopen.attach_preopen(df, quotes, 'US.NVDA', decision_at=s.utc('2026-09-08T13:05:00Z'))
    assert abs(out.pre_ret[0] - 0.08) < 1e-12                                                   # backup used, stale primary ignored
    assert pd.isna(preopen.attach_preopen(df, quotes, 'US.NVDA', decision_at=s.utc('2026-09-08T12:59:00Z')).pre_ret[0])   # C5: bound respected
    later = quotes.copy(); later.loc[0, 'available_at'] = '2026-09-08T13:20:00+00:00'
    assert abs(preopen.attach_preopen(df, later, 'US.NVDA', decision_at=s.utc('2026-09-08T13:05:00Z')).pre_ret[0] - 0.08) < 1e-12   # 09:20 quote after a 09:05 decision is not used
    assert abs(preopen.attach_preopen(df, later, 'US.NVDA', decision_at=s.utc('2026-09-08T13:25:00Z')).pre_ret[0] - 0.10) < 1e-12


def test_preopen_table_versions_and_migration(tmp_path):
    from mystock.ml import db
    path = tmp_path / 'ml.db'
    import sqlite3
    with sqlite3.connect(path) as c:   # old shape (PK without available_at) from the previous commit
        c.execute('CREATE TABLE ml_preopen_quotes (code TEXT NOT NULL, date TEXT NOT NULL, price REAL NOT NULL, prev_close REAL, available_at TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT, synced_at TEXT NOT NULL, PRIMARY KEY (code, date, source))')
        c.execute("INSERT INTO ml_preopen_quotes VALUES ('US.NVDA','2026-09-08',110,NULL,'2026-09-08T13:00:00+00:00','futu_snapshot','e','s')")
    db.init_ml_db(path)
    with db.get_ml_connection(path) as conn:
        assert 'available_at' in [r[1] for r in conn.execute('PRAGMA table_info(ml_preopen_quotes)') if r[5]]
        db.upsert(conn, 'ml_preopen_quotes', [dict(code='US.NVDA', date='2026-09-08', price=111.0, prev_close=None, available_at='2026-09-08T13:10:00+00:00', source='futu_snapshot', source_ref='e2', synced_at='s')])
    got = preopen.load_preopen('US.NVDA', path, source='futu_snapshot')
    assert list(got.price) == [110.0, 111.0]        # recapture appended, not overwritten
