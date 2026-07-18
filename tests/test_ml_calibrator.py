"""建议 2 — Conformalized Quantile Regression 纯函数单测。

校准核心（calibrator.py）：non-conformity score（Romano 约定）、
有限样本分位、apply 区间扩展、命中率/宽度统计。
"""
import numpy as np
import pytest

from mystock.ml.calibrator import (
    apply_conformal, calibrate, conformal_quantile, interval_hit,
    mean_width_pct, nonconformity_scores,
)


def test_score_sign_romano_convention():
    # 区间已包住真值 → s <= 0；戳出下沿或上沿 → s > 0
    s_contain = nonconformity_scores(
        y_low=np.array([98.0]), L_hat=np.array([97.0]),
        y_high=np.array([102.0]), H_hat=np.array([103.0]),
    )
    assert s_contain[0] <= 0          # L_hat<=y_low 且 H_hat>=y_high → max(-1,-1)=-1
    s_miss_low = nonconformity_scores(
        y_low=np.array([98.0]), L_hat=np.array([99.0]),   # 下沿 99 > 真值下沿 98 → 戳出
        y_high=np.array([102.0]), H_hat=np.array([103.0]),
    )
    assert s_miss_low[0] > 0
    s_miss_high = nonconformity_scores(
        y_low=np.array([98.0]), L_hat=np.array([97.0]),
        y_high=np.array([102.0]), H_hat=np.array([101.0]),  # 上沿 101 < 真值上沿 102 → 戳出
    )
    assert s_miss_high[0] > 0


def test_score_drops_nan():
    s = nonconformity_scores(
        y_low=np.array([98.0, np.nan, 98.0]),
        L_hat=np.array([97.0, 97.0, 97.0]),
        y_high=np.array([102.0, 102.0, np.inf]),
        H_hat=np.array([103.0, 103.0, 103.0]),
    )
    assert len(s) == 1 and np.isfinite(s).all()


def test_conformal_quantile_finite_sample_coverage():
    # 100 个 score 升序 1..100；目标 0.80 → ⌈101*0.8⌉=81 → idx 80 → score 81
    scores = np.arange(1, 101, dtype=float)
    q = conformal_quantile(scores, target_coverage=0.80)
    assert q == 81.0


def test_conformal_quantile_edge_cases():
    assert conformal_quantile(np.array([]), 0.8) == 0.0
    assert conformal_quantile(np.array([1.0, 2.0]), 0.0) == 0.0
    assert conformal_quantile(np.array([1.0, 2.0]), 1.0) == 0.0
    # target 越高 → q 越大（区间越宽）
    s = np.arange(1, 51, dtype=float)
    assert conformal_quantile(s, 0.9) >= conformal_quantile(s, 0.5)


def test_apply_conformal_expands_and_shrinks():
    L = np.array([100.0, 200.0]); H = np.array([110.0, 220.0])
    Lc, Hc = apply_conformal(L, H, q=2.0)   # 扩展
    assert np.allclose(Lc, [98.0, 198.0]) and np.allclose(Hc, [112.0, 222.0])
    Ls, Hs = apply_conformal(L, H, q=-1.0)  # 收紧
    assert np.allclose(Ls, [101.0, 201.0]) and np.allclose(Hs, [109.0, 219.0])
    L0, H0 = apply_conformal(L, H, q=0.0)   # 不变
    assert np.allclose(L0, L) and np.allclose(H0, H)


def test_calibrate_round_trip_raises_coverage():
    # 构造窄区间 0% 命中，CQR 校准后应达到 ≈ 目标覆盖率
    rng = np.random.default_rng(0)
    y_lo = rng.normal(0, 0.01, 300)
    y_hi = y_lo + rng.uniform(0.01, 0.03, 300)
    mid = (y_lo + y_hi) / 2
    # 极窄区间（只取 mid 一个点）
    Ln, Hn = mid, mid
    q = calibrate(y_lo, Ln, y_hi, Hn, target_coverage=0.80)
    Lc, Hc = apply_conformal(Ln, Hn, q)
    cov = interval_hit(y_lo, y_hi, Lc, Hc)
    # 有限样本修正后覆盖率应 >= 0.80（允许略超）
    assert cov >= 0.80 - 1e-6


def test_calibrate_already_containing_returns_nonpositive_q():
    # 区间已全包 → q <= 0（CQR 自适应收紧，不盲目扩展）
    y_lo = np.array([98.0, 98.5, 99.0])
    y_hi = np.array([102.0, 101.5, 101.0])
    L = y_lo - 1.0; H = y_hi + 1.0
    q = calibrate(y_lo, L, y_hi, H, target_coverage=0.80)
    assert q <= 0


def test_interval_hit_and_width():
    y_lo = np.array([98.0, 99.0]); y_hi = np.array([102.0, 101.0])
    L = np.array([97.0, 100.0]); H = np.array([103.0, 100.5])
    # 第 0 个命中（97<=98 且 103>=102），第 1 个不命中（100>99 下沿戳出）
    assert interval_hit(y_lo, y_hi, L, H) == 0.5
    close = np.array([100.0, 100.0])
    w = mean_width_pct(L, H, close)
    # 宽度 ((103-97) + (100.5-100)) / 2 / 100 * 100 = (6 + 0.5)/2/100*100 = 3.25
    assert abs(w - 3.25) < 1e-6


def test_interval_hit_handles_nan():
    y_lo = np.array([98.0, np.nan]); y_hi = np.array([102.0, 101.0])
    L = np.array([97.0, 100.0]); H = np.array([103.0, 100.5])
    # NaN 行不计入分母 → 只看第 0 行（命中）= 1.0
    assert interval_hit(y_lo, y_hi, L, H) == 1.0


def test_coverage_for_defaults_and_override():
    from mystock.ml import config as mlcfg
    # 默认 0.70（收窄档）
    assert mlcfg.coverage_for("US.NVDA") == mlcfg.DEFAULT_COVERAGE == 0.70
    # 未列入标的回退默认
    assert mlcfg.coverage_for("US.UNKNOWN") == mlcfg.DEFAULT_COVERAGE
    # 临时按股覆盖
    saved = dict(mlcfg.COVERAGE_BY_CODE)
    try:
        mlcfg.COVERAGE_BY_CODE["US.NVDA"] = 0.65
        assert mlcfg.coverage_for("US.NVDA") == 0.65
        assert mlcfg.coverage_for("US.TSLA") == 0.70  # 其他股不受影响
    finally:
        mlcfg.COVERAGE_BY_CODE.clear()
        mlcfg.COVERAGE_BY_CODE.update(saved)
