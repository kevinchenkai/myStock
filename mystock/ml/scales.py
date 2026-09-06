"""Conditional-scale estimators for standardized empirical quantiles (experiments only).

Every estimator returns, for each row t, a positive scale usable for the t+1 label and
computed only from data up to and including t. Pair it with `scaled_quantile`, which
re-estimates the standardized quantile for each scale candidate (never reuses another
candidate's quantile). Units: un-annualized simple-return scale, like `vol_20d`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .evaluation import scale as floor_scale


def ewma_scale(ret1, lam: float = 0.94, init: int = 20) -> np.ndarray:
    """RiskMetrics EWMA volatility: s_t^2 = lam*s_{t-1}^2 + (1-lam)*r_t^2.

    Initialised with the sample variance of the first `init` finite returns; rows before
    initialisation are NaN. NaN returns carry the previous state forward.
    """
    r = np.asarray(ret1, dtype=float)
    out = np.full(len(r), np.nan)
    finite = np.where(np.isfinite(r))[0]
    if len(finite) < init:
        return out
    start = finite[init - 1]
    var = float(np.var(r[finite[:init]]))
    out[start] = np.sqrt(var)
    for t in range(start + 1, len(r)):
        if np.isfinite(r[t]):
            var = lam * var + (1.0 - lam) * r[t] * r[t]
        out[t] = np.sqrt(var)
    return out


def garman_klass_scale(adj_open, adj_high, adj_low, adj_close, window: int = 20) -> np.ndarray:
    """Rolling mean of daily Garman-Klass variance, square-rooted.

    Pre-registered t->t+1 rule: the rolling mean at t is used as the t+1 scale.
    Uses only same-day OHLC, so overnight gap risk is not represented (report gap days separately).
    """
    o, h, l, c = (pd.Series(np.asarray(v, dtype=float)) for v in (adj_open, adj_high, adj_low, adj_close))
    hl = np.log(h / l) ** 2
    co = np.log(c / o) ** 2
    gk = 0.5 * hl - (2.0 * np.log(2.0) - 1.0) * co
    return np.sqrt(gk.rolling(window).mean().clip(lower=0)).to_numpy()


def garch_scale(ret_fit, ret_all, *, dist: str = 'normal'):
    """GARCH(1,1), zero mean. Parameters estimated on `ret_fit` (a prefix of `ret_all`);
    conditional variance then filtered forward over `ret_all` with those fixed parameters.

    Returns (scale_all, info). scale_all[t] is the h=1 forecast made at t (uses returns
    through t). Requires the `arch` package; raises if unavailable or estimation fails.
    """
    try:
        from arch import arch_model
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f'arch unavailable: {e}') from e
    fit = np.asarray(ret_fit, dtype=float)
    full = np.asarray(ret_all, dtype=float)
    if len(fit) > len(full) or not np.allclose(fit, full[:len(fit)], equal_nan=True):
        raise ValueError('ret_fit must be a prefix of ret_all')
    if not np.isfinite(fit).all() or not np.isfinite(full).all():
        raise ValueError('returns must be finite')
    res = arch_model(fit * 100.0, mean='Zero', vol='GARCH', p=1, q=1, dist=dist, rescale=False).fit(disp='off')
    params = res.params
    omega, a1, b1 = float(params['omega']), float(params['alpha[1]']), float(params['beta[1]'])
    ok = np.isfinite([omega, a1, b1]).all() and omega > 0 and a1 >= 0 and b1 >= 0 and a1 + b1 < 1
    info = dict(omega=omega, alpha=a1, beta=b1, convergence_flag=int(res.convergence_flag),
                stationary=bool(ok), n_fit=int(len(fit)))
    if not ok:
        raise RuntimeError(f'garch estimate unusable: {info}')
    fixed = arch_model(full * 100.0, mean='Zero', vol='GARCH', p=1, q=1, dist=dist, rescale=False).fix(params.values)
    var = fixed.forecast(horizon=1, start=0, reindex=False).variance['h.1'].to_numpy()
    return np.sqrt(var) / 100.0, info


def scaled_quantile(y_train, s_train, alpha: float, s_apply) -> np.ndarray:
    """Standardized empirical quantile: q = quantile(y/s over train), prediction = q * s_apply.

    Scales go through the shared positive floor so every candidate uses the same guard.
    """
    s_tr = floor_scale(s_train)
    z = np.asarray(y_train, dtype=float) / s_tr
    mask = np.isfinite(z)
    if not mask.any():
        raise ValueError('no finite standardized labels')
    q = float(np.quantile(z[mask], alpha))
    return q * floor_scale(s_apply)
