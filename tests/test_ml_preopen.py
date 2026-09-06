"""US pre-open quotes: snapshot moment, historical bar selection, as-of join, US decision window."""
import pandas as pd
import pytest

from mystock.ml import features, preopen, sessions as s


def test_feature_versions_us():
    assert features.FEATURE_COLS_V2_US == features.FEATURE_COLS_V1 + ['pre_ret']
    assert features.FEATURE_COLS == features.FEATURE_COLS_V1


def test_snapshot_time_and_us_window():
    assert preopen.snapshot_time('US.NVDA', '2026-09-04') == s.utc('2026-09-04T13:00:00Z')   # EDT: open 13:30Z
    assert preopen.snapshot_time('US.NVDA', '2026-03-06') == s.utc('2026-03-06T14:00:00Z')   # EST
    w = s.preopen_window('US.NVDA', '2026-09-04')
    assert w['target_session'] == '2026-09-08' and w['earliest'] == s.utc('2026-09-08T13:00:00Z') and w['deadline'] == s.utc('2026-09-08T13:30:00Z')
    with pytest.raises(s.Unavailable, match='awaiting_overnight'):
        s.check_preopen_decision('US.NVDA', '2026-09-04', s.utc('2026-09-08T12:59:00Z'))
    with pytest.raises(s.Unavailable, match='missed_deadline'):
        s.check_preopen_decision('US.NVDA', '2026-09-04', s.utc('2026-09-08T13:30:00Z'))
    assert s.check_preopen_decision('US.NVDA', '2026-09-04', s.utc('2026-09-08T13:05:00Z'))['target_session'] == '2026-09-08'
    # HK behaviour unchanged
    assert s.preopen_window('HK.00700', '2026-09-04')['earliest'] == s.session('US.NVDA', '2026-09-04')['final_at']


def _hourly(stamps_prices):
    idx = pd.DatetimeIndex([pd.Timestamp(t, tz='America/New_York') for t, _ in stamps_prices]).tz_convert('UTC')
    return pd.DataFrame({'Open': [p for _, p in stamps_prices], 'Close': [p for _, p in stamps_prices]}, index=idx)


def test_rows_from_yf_hourly_picks_0800_bar_only():
    df = _hourly([('2026-09-04 07:00', 100.0), ('2026-09-04 08:00', 101.0), ('2026-09-04 09:00', 105.0),
                  ('2026-09-05 08:00', 99.0),      # Saturday: not a session
                  ('2026-09-08 08:00', 102.0)])
    rows = preopen.rows_from_yf_hourly(df, 'US.NVDA', '2026-09-08T12:00:00Z')
    assert [(r['date'], r['price']) for r in rows] == [('2026-09-04', 101.0)]     # 09-08 snapshot (13:00Z) is after now
    assert rows[0]['available_at'] == '2026-09-04T13:00:00+00:00' and rows[0]['source'] == 'yfinance_1h'
    rows = preopen.rows_from_yf_hourly(df, 'US.NVDA', '2026-09-08T13:00:00Z')
    assert [r['date'] for r in rows] == ['2026-09-04', '2026-09-08']
    with pytest.raises(ValueError):
        preopen.rows_from_yf_hourly(df.tz_localize(None), 'US.NVDA', '2026-09-08T13:00:00Z')


def test_attach_preopen_targets_next_session_and_respects_deadline():
    quotes = pd.DataFrame([dict(date='2026-09-08', price=110.0, available_at='2026-09-08T13:00:00+00:00'),
                           dict(date='2026-09-09', price=120.0, available_at='2026-09-09T13:30:00+00:00')])   # at the deadline: too late
    df = pd.DataFrame(dict(date=['2026-09-03', '2026-09-04', '2026-09-08'], close=[100.0, 100.0, 100.0]))
    out = preopen.attach_preopen(df, quotes, 'US.NVDA')
    assert pd.isna(out.pre_ret[0])                     # target 09-04 has no quote
    assert abs(out.pre_ret[1] - 0.10) < 1e-12          # 09-04 -> target 09-08 (Labor Day skipped)
    assert pd.isna(out.pre_ret[2])                     # 09-09 quote not before the deadline
    assert pd.isna(preopen.attach_preopen(df, pd.DataFrame(), 'US.NVDA').pre_ret).all()


def test_rows_from_futu_snapshot_requires_pre_market_price():
    now = '2026-09-08T13:00:00Z'
    df = pd.DataFrame([dict(code='US.NVDA', pre_market_price=231.5, prev_close_price=230.0, update_time='2026-09-08 09:00:00')])
    rows = preopen.rows_from_futu_snapshot(df, now)
    assert rows[0]['date'] == '2026-09-08' and rows[0]['price'] == 231.5 and rows[0]['source'] == 'futu_snapshot' and rows[0]['available_at'] == '2026-09-08T13:00:00+00:00'
    with pytest.raises(ValueError):
        preopen.rows_from_futu_snapshot(pd.DataFrame([dict(code='US.NVDA', last_price=231.5)]), now)
    with pytest.raises(ValueError):
        preopen.rows_from_futu_snapshot(df, '2026-09-07T13:00:00Z')   # Labor Day


def test_preopen_table_roundtrip(tmp_path):
    from mystock.ml import db
    path = tmp_path / 'ml.db'; db.init_ml_db(path)
    with db.get_ml_connection(path) as conn:
        db.upsert(conn, 'ml_preopen_quotes', [dict(code='US.NVDA', date='2026-09-08', price=110.0, prev_close=None, available_at='2026-09-08T13:00:00+00:00',
                                                   source='yfinance_1h', source_ref='x', synced_at='2026-09-08T13:00:00+00:00')])
    assert list(preopen.load_preopen('US.NVDA', path).price) == [110.0]
    assert preopen.load_preopen('US.NVDA', path, source='futu_snapshot').empty
