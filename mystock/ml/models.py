"""Quantile-regression backends behind one interface (experiments only).

Production `predictor.py` keeps its own frozen LightGBM call. This module exists so
the model matrix can swap learners without touching features, labels or CQR, and
so a missing dependency fails loudly instead of silently running another model.

Backends: lightgbm, catboost, xgboost, linear (sklearn QuantileRegressor with a
StandardScaler fitted on the training rows only). All fit on numpy arrays.
"""
from __future__ import annotations

import importlib
import warnings

import numpy as np


class BackendUnavailable(RuntimeError):
    """Raised when a requested backend's library is not importable."""


def _require(module: str):
    try:
        return importlib.import_module(module)
    except Exception as e:  # noqa: BLE001
        raise BackendUnavailable(f'backend library {module!r} unavailable: {e}') from e


class QuantileModel:
    """One fitted quantile model. `meta()` records what actually ran."""

    def __init__(self, backend: str, alpha: float, params: dict | None = None, seed: int = 0):
        if backend not in BACKENDS:
            raise ValueError(f'unknown backend {backend!r}')
        if not 0.0 < alpha < 1.0:
            raise ValueError('alpha must be in (0, 1)')
        self.backend = backend
        self.alpha = float(alpha)
        self.params = dict(params or {})
        self.seed = int(seed)
        self._model = None
        self._scaler = None
        self._meta: dict = {}

    def fit(self, X, y):
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        if X.ndim != 2 or len(X) != len(y):
            raise ValueError('X must be 2-D and aligned with y')
        if not (np.isfinite(X).all() and np.isfinite(y).all()):
            raise ValueError('non-finite training data')
        BACKENDS[self.backend](self, X, y)
        return self

    def predict(self, X):
        if self._model is None:
            raise RuntimeError('model not fitted')
        X = np.asarray(X, dtype=float)
        if self._scaler is not None:
            X = self._scaler.transform(X)
        with warnings.catch_warnings():
            # LightGBM synthesises column names on numpy fit; predicting on numpy then warns harmlessly.
            warnings.filterwarnings('ignore', message='X does not have valid feature names', category=UserWarning)
            return np.asarray(self._model.predict(X), dtype=float)

    def meta(self) -> dict:
        return dict(backend=self.backend, alpha=self.alpha, seed=self.seed, params=dict(self.params), **self._meta)


def _fit_lightgbm(m: QuantileModel, X, y):
    lgb = _require('lightgbm')
    p = dict(objective='quantile', alpha=m.alpha, n_estimators=300, learning_rate=0.03,
             num_leaves=15, min_child_samples=30, subsample=0.8, subsample_freq=0,
             colsample_bytree=0.8, random_state=m.seed, verbose=-1, n_jobs=1)
    p.update(m.params)
    m._model = lgb.LGBMRegressor(**p).fit(X, y)
    m._meta = dict(version=lgb.__version__, effective=p)


def _fit_catboost(m: QuantileModel, X, y):
    cb = _require('catboost')
    p = dict(iterations=300, learning_rate=0.03, depth=4, l2_leaf_reg=3.0, boosting_type='Ordered',
             random_seed=m.seed, verbose=0, thread_count=1, allow_writing_files=False)
    p.update(m.params)
    model = cb.CatBoostRegressor(loss_function=f'Quantile:alpha={m.alpha}', **p).fit(X, y)
    m._model = model
    m._meta = dict(version=cb.__version__, effective=p,
                   boosting_type=model.get_all_params().get('boosting_type'))


def _fit_xgboost(m: QuantileModel, X, y):
    xgb = _require('xgboost')
    p = dict(objective='reg:quantileerror', quantile_alpha=m.alpha, n_estimators=300, learning_rate=0.03,
             max_depth=3, min_child_weight=5, tree_method='hist', colsample_bytree=0.8,
             random_state=m.seed, n_jobs=1)
    p.update(m.params)
    m._model = xgb.XGBRegressor(**p).fit(X, y)
    m._meta = dict(version=xgb.__version__, effective=p)


def _fit_linear(m: QuantileModel, X, y):
    skl = _require('sklearn')
    from sklearn.linear_model import QuantileRegressor
    from sklearn.preprocessing import StandardScaler
    # `quantile` is the level; `alpha` is L1 strength (sklearn naming). We expose `l1`.
    l1 = float(m.params.get('l1', 1e-3))
    m._scaler = StandardScaler().fit(X)
    m._model = QuantileRegressor(quantile=m.alpha, alpha=l1, solver='highs').fit(m._scaler.transform(X), y)
    m._meta = dict(version=skl.__version__, effective=dict(quantile=m.alpha, l1=l1, solver='highs', scaler='standard'))


BACKENDS = {
    'lightgbm': _fit_lightgbm,
    'catboost': _fit_catboost,
    'xgboost': _fit_xgboost,
    'linear': _fit_linear,
}


def available(backend: str) -> bool:
    try:
        _require({'lightgbm': 'lightgbm', 'catboost': 'catboost', 'xgboost': 'xgboost', 'linear': 'sklearn'}[backend])
        return True
    except (BackendUnavailable, KeyError):
        return False
