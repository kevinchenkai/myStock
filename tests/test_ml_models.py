"""models.py: one interface, strict dependency failure, backend metadata."""
import numpy as np
import pytest

from mystock.ml import models


def _data(n=300, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 5))
    y = 0.01 * X[:, 0] + 0.02 * rng.normal(size=n)
    return X, y


@pytest.mark.parametrize('backend', ['lightgbm', 'catboost', 'xgboost', 'linear'])
def test_fit_predict_shape_and_meta(backend):
    if not models.available(backend):
        pytest.skip(f'{backend} not installed')
    X, y = _data()
    m = models.QuantileModel(backend, 0.2, seed=0).fit(X, y)
    p = m.predict(X[:7])
    assert p.shape == (7,) and np.isfinite(p).all()
    meta = m.meta()
    assert meta['backend'] == backend and meta['alpha'] == 0.2 and 'version' in meta
    if backend == 'catboost':
        assert meta['boosting_type'] == 'Ordered'


def test_low_quantile_below_high_quantile_on_average():
    X, y = _data(600)
    lo = models.QuantileModel('linear', 0.2).fit(X, y).predict(X)
    hi = models.QuantileModel('linear', 0.8).fit(X, y).predict(X)
    assert lo.mean() < hi.mean()
    assert 0.1 < np.mean(y <= lo) < 0.3 and 0.7 < np.mean(y <= hi) < 0.9


def test_missing_backend_fails_loudly(monkeypatch):
    import importlib
    real = importlib.import_module

    def fake(name):
        if name == 'catboost':
            raise ImportError('simulated missing')
        return real(name)
    monkeypatch.setattr(models.importlib, 'import_module', fake)
    X, y = _data(50)
    with pytest.raises(models.BackendUnavailable):
        models.QuantileModel('catboost', 0.2).fit(X, y)
    assert models.available('catboost') is False


def test_rejects_bad_inputs():
    X, y = _data(50)
    with pytest.raises(ValueError):
        models.QuantileModel('nope', 0.2)
    with pytest.raises(ValueError):
        models.QuantileModel('linear', 1.2)
    with pytest.raises(ValueError):
        models.QuantileModel('linear', 0.2).fit(np.r_[X[:-1], [[np.nan] * 5]], y)
    with pytest.raises(RuntimeError):
        models.QuantileModel('linear', 0.2).predict(X)


def test_lightgbm_default_matches_frozen_predictor_params():
    if not models.available('lightgbm'):
        pytest.skip('lightgbm not installed')
    X, y = _data(100)
    eff = models.QuantileModel('lightgbm', 0.2).fit(X, y).meta()['effective']
    assert (eff['n_estimators'], eff['learning_rate'], eff['num_leaves'], eff['min_child_samples'],
            eff['subsample'], eff['subsample_freq'], eff['colsample_bytree']) == (300, 0.03, 15, 30, 0.8, 0, 0.8)
