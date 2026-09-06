"""scales.py: no look-ahead, positive scales, standardized quantile matches naive_vol."""
import numpy as np
import pandas as pd
import pytest

from mystock.ml import evaluation as ev
from mystock.ml import scales


def _returns(n=400, seed=0):
    rng = np.random.default_rng(seed)
    vol = 0.01 + 0.01 * (np.sin(np.arange(n) / 40) + 1)
    return rng.normal(size=n) * vol


def test_ewma_no_lookahead_and_positive():
    r = _returns()
    a = scales.ewma_scale(r)
    r2 = r.copy(); r2[300:] *= 5
    b = scales.ewma_scale(r2)
    assert np.allclose(a[:300], b[:300], equal_nan=True)
    assert np.isnan(a[:19]).all() and np.isfinite(a[19:]).all() and (a[19:] > 0).all()
    assert b[-1] > a[-1]


def test_ewma_tracks_volatility_level():
    r = _returns(2000)
    s = scales.ewma_scale(r)
    assert abs(np.nanmean(s[500:]) / np.std(r[500:]) - 1) < 0.25


def test_garman_klass_no_lookahead_and_positive():
    rng = np.random.default_rng(1)
    n = 200
    c = 100 * np.exp(np.cumsum(rng.normal(size=n) * 0.02))
    o = c * np.exp(rng.normal(size=n) * 0.005)
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(size=n)) * 0.01)
    l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(size=n)) * 0.01)
    a = scales.garman_klass_scale(o, h, l, c)
    h2 = h.copy(); h2[150:] *= 1.5
    b = scales.garman_klass_scale(o, h2, l, c)
    assert np.allclose(a[:150], b[:150], equal_nan=True)
    assert np.isnan(a[:19]).all() and (a[19:] > 0).all()


def test_scaled_quantile_equals_naive_vol_when_scale_is_vol20():
    rng = np.random.default_rng(2)
    n = 300
    df = pd.DataFrame(dict(vol_20d=np.abs(rng.normal(size=n)) * 0.01 + 1e-3,
                           y_low_ret=-np.abs(rng.normal(size=n)) * 0.02,
                           y_high_ret=np.abs(rng.normal(size=n)) * 0.02))
    train, test = df.iloc[:200], df.iloc[200:]
    lo_ref, hi_ref = ev.naive_vol(train, test, 0.2, 0.8)
    lo = scales.scaled_quantile(train.y_low_ret, train.vol_20d, 0.2, test.vol_20d)
    hi = scales.scaled_quantile(train.y_high_ret, train.vol_20d, 0.8, test.vol_20d)
    assert np.allclose(lo, lo_ref) and np.allclose(hi, hi_ref)


def test_garch_filter_no_lookahead_and_prefix_check():
    pytest.importorskip('arch')
    r = _returns(700, seed=3)
    fit = r[:500]
    s_a, info = scales.garch_scale(fit, r)
    r2 = r.copy(); r2[600:] *= 4
    s_b, _ = scales.garch_scale(fit, r2)
    assert np.allclose(s_a[:600], s_b[:600])
    assert (s_a > 0).all() and info['stationary'] and info['n_fit'] == 500
    assert s_b[-1] > s_a[-1]
    with pytest.raises(ValueError):
        scales.garch_scale(r[10:200], r)
