"""D1-D3 of the overnight plan: external rows carry available_at, HK pre-open window,
as-of join never sees information after the cutoff, V1 stays frozen."""
import pandas as pd
import pytest

from mystock.ml import external, features, sessions as s


def test_feature_versions_frozen():
    assert features.FEATURE_COLS_V1 == features.FEATURE_COLS
    assert len(features.FEATURE_COLS_V1) == 16
    assert features.FEATURE_COLS_V2 == features.FEATURE_COLS_V1 + ['adr_ret']


def test_preopen_window_normal_us_holiday_and_non_hk():
    w = s.preopen_window('HK.00700', '2026-09-04')          # Friday; US trades 09-04, HK trades 09-07
    assert w['target_session'] == '2026-09-07'
    assert w['earliest'] == s.session('US.NVDA', '2026-09-04')['final_at']
    assert w['deadline'] == s.session('HK.00700', '2026-09-07')['deadline']
    assert w['earliest'] < w['deadline']
    w2 = s.preopen_window('HK.00700', '2026-09-07')         # US Labor Day: no US session dated as_of
    assert w2['earliest'] == s.session('HK.00700', '2026-09-07')['final_at']
    with pytest.raises(s.Unavailable):
        s.preopen_window('XX.0001', '2026-09-04')       # unknown market fails closed


def test_check_preopen_decision_statuses():
    with pytest.raises(s.Unavailable, match='awaiting_overnight'):
        s.check_preopen_decision('HK.00700', '2026-09-04', s.utc('2026-09-04T19:00:00Z'))
    with pytest.raises(s.Unavailable, match='missed_deadline'):
        s.check_preopen_decision('HK.00700', '2026-09-04', s.utc('2026-09-07T01:00:00Z'))
    w = s.check_preopen_decision('HK.00700', '2026-09-04', s.utc('2026-09-07T00:30:00Z'))
    assert w['target_session'] == '2026-09-07'


def _ext(rows):
    return pd.DataFrame([dict(date=d, close=c, available_at=external.available_at_for(d)) for d, c in rows])


def test_attach_overnight_normal_holiday_and_multi_session():
    ext = _ext([('2026-04-01', 100.0), ('2026-04-02', 110.0), ('2026-04-06', 121.0), ('2026-04-07', 133.1),
                ('2026-09-03', 200.0), ('2026-09-04', 210.0), ('2026-09-08', 220.5)])
    df = pd.DataFrame(dict(date=['2026-03-31', '2026-04-02', '2026-09-04', '2026-09-07']))
    out = features.attach_overnight(df, ext, 'HK.00700')
    # Before external history: NaN
    assert pd.isna(out.adr_ret[0])
    # HK closed 04-03/04-06/04-07 while US traded 04-06/04-07: compound 04-02, 04-06, 04-07 = 1.1^3 - 1
    assert abs(out.adr_ret[1] - (1.1 ** 3 - 1)) < 1e-9
    # Normal day: just the US session dated as_of
    assert abs(out.adr_ret[2] - 0.05) < 1e-9
    # US Labor Day 09-07: no new information before HK 09-08 open -> 0
    assert out.adr_ret[3] == 0.0


def test_attach_overnight_respects_available_at():
    late = s.session('HK.00700', '2026-09-07')['deadline'].isoformat()   # not available before the cutoff
    ext = pd.DataFrame([dict(date='2026-09-03', close=200.0, available_at=external.available_at_for('2026-09-03')),
                        dict(date='2026-09-04', close=210.0, available_at=late)])
    out = features.attach_overnight(pd.DataFrame(dict(date=['2026-09-04'])), ext, 'HK.00700')
    assert out.adr_ret[0] == 0.0
    # Shifted external data (information dated one session later than it truly is) must not leak.
    shifted = _ext([('2026-09-03', 200.0), ('2026-09-04', 210.0), ('2026-09-08', 220.5)])
    out2 = features.attach_overnight(pd.DataFrame(dict(date=['2026-09-04', '2026-09-08'])), shifted, 'HK.00700')
    assert abs(out2.adr_ret[0] - 0.05) < 1e-9 and abs(out2.adr_ret[1] - 0.05) < 1e-9
    empty = features.attach_overnight(pd.DataFrame(dict(date=['2026-09-04'])), pd.DataFrame(), 'HK.00700')
    assert pd.isna(empty.adr_ret[0])


def test_rows_from_history_filters_non_session_and_not_final():
    hist = pd.DataFrame({'Date': pd.to_datetime(['2026-09-03', '2026-09-05', '2026-09-04']),
                         'Open': [100, 100, 100], 'High': [110, 110, 110], 'Low': [90, 90, 90], 'Close': [105, 105, 105],
                         'Adj Close': [105, 105, 105], 'Volume': [1, 1, 1]}).set_index('Date')
    rows = external.rows_from_history(hist, 'TCEHY', 'HK.00700', '2026-09-04T20:04:00Z')
    assert [r['date'] for r in rows] == ['2026-09-03']          # 09-05 not a session; 09-04 not final at 20:04Z
    rows = external.rows_from_history(hist, 'TCEHY', 'HK.00700', '2026-09-04T20:05:00Z')
    assert [r['date'] for r in rows] == ['2026-09-03', '2026-09-04']
    assert rows[1]['available_at'] == s.session('US.NVDA', '2026-09-04')['final_at'].isoformat()
    assert rows[1]['for_code'] == 'HK.00700' and rows[1]['data_source'] == 'yfinance'


def test_external_table_roundtrip(tmp_path):
    from mystock.ml import db
    path = tmp_path / 'ml.db'
    db.init_ml_db(path)
    with db.get_ml_connection(path) as conn:
        db.upsert(conn, 'ml_external_1d', [dict(symbol='TCEHY', for_code='HK.00700', date='2026-09-04', open=1, high=2, low=0.5, close=1.5,
                                                adj_close=1.5, volume=10, available_at=external.available_at_for('2026-09-04'),
                                                synced_at='2026-09-06T00:00:00+00:00', data_source='yfinance')])
    got = external.load_external('HK.00700', path)
    assert list(got.date) == ['2026-09-04'] and got.close[0] == 1.5
