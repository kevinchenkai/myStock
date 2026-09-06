"""V2 predictor entry, shadow recording, and isolation of shadow rows from production selection."""
import numpy as np
import pandas as pd
import pytest

from mystock.ml import db, external, predictor, sessions as s, shadow, versions, service


def _daily(code, start, end, seed=0):
    rng = np.random.default_rng(seed)
    days = s.session_days(code, start, end)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
    rows = []
    for d, px in zip(days, c):
        rows.append(dict(date=d, open=px * 0.995, high=px * 1.02, low=px * 0.98, close=px, adj_close=px, volume=1000,
                         dividends=0.0, splits=0.0, synced_at='2026-09-06T12:00:00+00:00'))
    return pd.DataFrame(rows)


def _external(code, start, end, seed=1):
    rng = np.random.default_rng(seed)
    days = s.session_days('US.NVDA', start, end)
    px = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
    return pd.DataFrame([dict(symbol='TCEHY', for_code=code, date=d, open=p, high=p * 1.01, low=p * 0.99, close=p, adj_close=p, volume=1,
                              available_at=external.available_at_for(d), synced_at='2026-09-06T12:00:00+00:00', data_source='yfinance')
                         for d, p in zip(days, px)])


def test_v1_output_unchanged_and_v2_adds_fields():
    daily = _daily('HK.00700', '2024-01-02', '2026-09-04')
    ext = _external('HK.00700', '2024-01-02', '2026-09-04')
    clock = lambda: s.utc('2026-09-06T12:00:00Z')      # inside the HK pre-open window for target 09-07
    v1 = predictor.predict_next_day(daily, code='HK.00700', clock=clock, conformal=True, target_coverage=0.7, low_alpha=0.2, high_alpha=0.8)
    assert v1['target_session'] == '2026-09-07' and 'feature_version' not in v1 and np.isfinite(v1['lo_ret_raw'])
    v2 = predictor.predict_next_day(daily, code='HK.00700', clock=clock, conformal=True, target_coverage=0.7, low_alpha=0.2, high_alpha=0.8,
                                    feature_version='v2', external=ext)
    assert v2['feature_version'] == 'v2' and v2['preopen_feature'] == 'adr_ret' and v2['v2_params'] == {'num_leaves': 7, 'min_child_samples': 50}
    assert v2['v2_train_rows'] >= predictor.V2_MIN_ROWS and v2['L_hat'] < v2['close'] < v2['H_hat']
    # v2 fails closed: missing latest ADR row -> awaiting_preopen_data; too little history -> insufficient_v2_rows
    with pytest.raises(s.Unavailable) as e:
        predictor.predict_next_day(daily, code='HK.00700', clock=clock, feature_version='v2', external=ext[ext.date < '2026-09-04'])
    assert e.value.status == 'awaiting_preopen_data'
    with pytest.raises(s.Unavailable) as e:
        predictor.predict_next_day(daily, code='HK.00700', clock=clock, feature_version='v2', external=ext[ext.date >= '2026-06-01'])
    assert e.value.status == 'insufficient_v2_rows'
    # v2 respects the pre-open window (too early: US 09-04 session not final yet)
    with pytest.raises(s.Unavailable) as e:
        predictor.predict_next_day(daily, code='HK.00700', clock=lambda: s.utc('2026-09-04T19:00:00Z'), feature_version='v2', external=ext)
    assert e.value.status == 'awaiting_overnight'


def test_shadow_rows_are_recorded_but_never_selected(tmp_path):
    path = tmp_path / 'ml.db'; db.init_ml_db(path)
    with db.get_ml_connection(path) as conn:
        daily = _daily('HK.00700', '2024-01-02', '2026-09-04'); daily['symbol'] = '0700.HK'; daily['futu_code'] = 'HK.00700'
        db.upsert(conn, 'ml_quotes_1d', daily.to_dict('records'))
        db.upsert(conn, 'ml_external_1d', _external('HK.00700', '2024-01-02', '2026-09-04').to_dict('records'))
    shadow.HK_CODES_BACKUP = shadow.HK_CODES
    try:
        shadow.HK_CODES = ['HK.00700']
        import os; os.environ['MYSTOCK_ML_SHADOW_RECEIPT'] = str(tmp_path / 'r.json')
        r = shadow.run('HK', path, now=s.utc('2026-09-06T12:00:00Z'), fetch=False)
    finally:
        shadow.HK_CODES = shadow.HK_CODES_BACKUP; os.environ.pop('MYSTOCK_ML_SHADOW_RECEIPT', None)
    assert r['appended'] == 2 and r['results'][0]['v2']['status'] == 'recorded' and r['results'][0]['v1']['status'] == 'recorded'
    with db.get_ml_connection_readonly(path) as c:
        rows = versions.load(c, 'HK.00700', include_audit=True)
        assert {x['status'] for x in rows} == {'shadow'} and {x['source'] for x in rows} == {'shadow_v1', 'shadow_v2'}
        assert all(x['decision_at'] == '2026-09-06T12:00:00+00:00' for x in rows)
        assert versions.load(c, 'HK.00700') == []                       # default loader hides shadow
        assert versions.select_by_target(rows) == {}                  # never selectable
        assert c.execute('select count(*) from ml_predictions').fetchone()[0] == 0   # legacy projection untouched
    latest = service.latest(str(path), ['HK.00700'], now=s.utc('2026-09-06T12:00:00Z'))
    assert latest['results'][0]['prediction'] is None
