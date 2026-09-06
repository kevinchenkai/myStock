"""V2 predictor entry (frozen V1 regression), shadow control flow (guard before fetch, real clock,
pair-or-none, cross-deadline), and isolation of shadow rows from production selection."""
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mystock.ml import db, external, predictor, sessions as s, shadow, versions, service

EXPECTED_V1 = json.loads(Path(__file__).with_name('fixtures_v1_expected.json').read_text())


def _daily(code, start, end, seed=0):
    rng = np.random.default_rng(seed)
    days = s.session_days(code, start, end)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
    return pd.DataFrame([dict(date=d, open=px * 0.995, high=px * 1.02, low=px * 0.98, close=px, adj_close=px, volume=1000,
                              dividends=0.0, splits=0.0, synced_at='2026-09-06T12:00:00+00:00') for d, px in zip(days, c)])


def _external(code, start, end, seed=1):
    rng = np.random.default_rng(seed)
    days = s.session_days('US.NVDA', start, end)
    px = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
    return pd.DataFrame([dict(symbol='TCEHY', for_code=code, date=d, open=p, high=p * 1.01, low=p * 0.99, close=p, adj_close=p, volume=1,
                              available_at=external.available_at_for(d), synced_at='2026-09-06T12:00:00+00:00', data_source='yfinance')
                         for d, p in zip(days, px)])


@pytest.mark.parametrize('code', ['HK.00700', 'US.NVDA'])
def test_v1_matches_frozen_b988ea2_output(code):
    """Expected values were produced by the pre-change predictor (commit b988ea2) on this fixture."""
    daily = _daily(code, '2024-01-02', '2026-09-04')
    r = predictor.predict_next_day(daily, code=code, clock=lambda: s.utc('2026-09-06T12:00:00Z'), conformal=True, target_coverage=0.7, low_alpha=0.2, high_alpha=0.8)
    for k, v in EXPECTED_V1[code].items():
        assert r[k] == v, (k, r[k], v)
    assert 'feature_version' not in r and r['backend'] == 'lightgbm'


def test_v2_fields_and_fail_closed():
    daily = _daily('HK.00700', '2024-01-02', '2026-09-04')
    ext = _external('HK.00700', '2024-01-02', '2026-09-04')
    clock = lambda: s.utc('2026-09-06T12:00:00Z')
    v2 = predictor.predict_next_day(daily, code='HK.00700', clock=clock, conformal=True, target_coverage=0.7, low_alpha=0.2, high_alpha=0.8, feature_version='v2', external=ext)
    assert v2['feature_version'] == 'v2' and v2['preopen_feature'] == 'adr_ret' and v2['v2_params'] == {'num_leaves': 7, 'min_child_samples': 50}
    assert v2['v2_train_rows'] >= predictor.V2_MIN_ROWS and v2['L_hat'] < v2['close'] < v2['H_hat']
    for ext_variant, status in ((ext[ext.date < '2026-09-04'], 'awaiting_preopen_data'), (ext[ext.date >= '2026-06-01'], 'insufficient_v2_rows')):
        with pytest.raises(s.Unavailable) as e:
            predictor.predict_next_day(daily, code='HK.00700', clock=clock, feature_version='v2', external=ext_variant)
        assert e.value.status == status
    with pytest.raises(s.Unavailable) as e:
        predictor.predict_next_day(daily, code='HK.00700', clock=lambda: s.utc('2026-09-04T19:00:00Z'), feature_version='v2', external=ext)
    assert e.value.status == 'awaiting_overnight'
    # a decision_at earlier than the ADR bar makes the feature unavailable even if the clock is inside the window
    with pytest.raises(s.Unavailable) as e:
        predictor.predict_next_day(daily, code='HK.00700', clock=clock, feature_version='v2', external=ext, decision_at=s.utc('2026-09-04T19:00:00Z'))
    assert e.value.status == 'awaiting_preopen_data'


def _seed_db(tmp_path, with_external=True):
    path = tmp_path / 'ml.db'; db.init_ml_db(path)
    with db.get_ml_connection(path) as conn:
        daily = _daily('HK.00700', '2024-01-02', '2026-09-04'); daily['symbol'] = '0700.HK'; daily['futu_code'] = 'HK.00700'
        db.upsert(conn, 'ml_quotes_1d', daily.to_dict('records'))
        if with_external:
            db.upsert(conn, 'ml_external_1d', _external('HK.00700', '2024-01-02', '2026-09-04').to_dict('records'))
    return path


@pytest.fixture
def one_code(monkeypatch, tmp_path):
    monkeypatch.setattr(shadow, 'HK_CODES', ['HK.00700'])
    monkeypatch.setenv('MYSTOCK_ML_SHADOW_RECEIPT', str(tmp_path / 'receipt.json'))
    return tmp_path


def _clock(*stamps):
    it = iter(stamps); last = [stamps[-1]]
    def clock():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return s.utc(last[0])
    return clock


def test_shadow_records_pairs_and_is_invisible_to_production(one_code, monkeypatch):
    path = _seed_db(one_code)
    calls = []
    monkeypatch.setattr(shadow, 'fetch_data', lambda m, d, n: calls.append(n) or dict(captured_at=n, sources={}))
    r = shadow.run('HK', path, clock=_clock('2026-09-06T12:00:00Z', '2026-09-06T12:00:01Z', '2026-09-06T12:00:02Z', '2026-09-06T12:00:03Z', '2026-09-06T12:00:04Z', '2026-09-06T12:00:05Z', '2026-09-06T12:00:30Z'))
    assert r['status'] == 'completed' and r['appended'] == 2 and len(calls) == 1
    e = r['results'][0]
    assert e['status'] == 'recorded' and e['decision_at'] < e['generated_at'] and e['feature_source']['date'] == '2026-09-04'
    with db.get_ml_connection_readonly(path) as c:
        rows = versions.load(c, 'HK.00700', include_audit=True)
        assert {x['status'] for x in rows} == {'shadow'} and {x['source'] for x in rows} == {'shadow_v1', 'shadow_v2'}
        assert len({x['pair_id'] for x in rows}) == 1 and all(x['input_sha256'] == rows[0]['input_sha256'] for x in rows)
        assert all(x['decision_at'] and x['data_cutoff'] and x['backend'] == 'lightgbm' for x in rows)
        assert versions.load(c, 'HK.00700') == [] and versions.select_by_target(rows) == {}
        assert c.execute('select count(*) from ml_predictions').fetchone()[0] == 0
    assert service.latest(str(path), ['HK.00700'], now=s.utc('2026-09-06T12:00:00Z'))['results'][0]['prediction'] is None
    assert json.loads(Path(os.environ['MYSTOCK_ML_SHADOW_RECEIPT']).read_text())['appended'] == 2


def test_shadow_guard_before_fetch_and_no_partial_pairs(one_code, monkeypatch):
    path = _seed_db(one_code, with_external=False)
    calls = []
    monkeypatch.setattr(shadow, 'fetch_data', lambda m, d, n: calls.append(n) or {})
    # too early: nothing fetched, nothing fitted, receipt still written
    r = shadow.run('HK', path, clock=_clock('2026-09-04T19:00:00Z'))
    assert r['status'] == 'no_eligible_code' and calls == [] and r['results'][0]['status'] == 'awaiting_overnight'
    # inside the window but V2 has no ADR data: V1 alone must not be recorded
    r = shadow.run('HK', path, clock=_clock('2026-09-06T12:00:00Z'))
    assert r['appended'] == 0 and r['results'][0]['status'] == 'awaiting_preopen_data' and len(calls) == 1


def test_shadow_rejects_generation_after_deadline(one_code, monkeypatch):
    path = _seed_db(one_code)
    monkeypatch.setattr(shadow, 'fetch_data', lambda m, d, n: {})
    # Stub predictor: guards pass at 00:59Z, but the clock reads 01:00Z (the 09:00 HKT cutoff) when generation completes.
    stub = dict(as_of='2026-09-04', target_session='2026-09-07', close=100.0, L_hat=95.0, H_hat=105.0, width_pct=10.0, q_ret=0.0,
                lo_ret_raw=-0.04, hi_ret_raw=0.04, backend='lightgbm')
    monkeypatch.setattr(shadow.predictor, 'predict_next_day', lambda *a, **k: dict(stub))
    stamps = ['2026-09-07T00:59:00Z'] * 6 + ['2026-09-07T01:00:00Z']
    r = shadow.run('HK', path, clock=_clock(*stamps))
    assert r['appended'] == 0 and r['results'][0]['status'] == 'missed_deadline'
