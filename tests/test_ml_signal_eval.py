"""借鉴 ③ — 信号层评估（IC/RankIC/ICIR）纯函数单测。

单标的时间轴 IC。验证：完美/反相关的极值、噪声≈0、RankIC 抗异常值、
ICIR 稳定性、宽度 IC 跟踪振幅、NaN/短序列不抛异常。
"""
import numpy as np

from mystock.ml.signal_eval import (
    ic, icir, rank_ic, signal_report, width_ic,
)


def test_ic_perfect_correlation():
    x = np.linspace(-1, 1, 50)
    assert abs(ic(x, x) - 1.0) < 1e-9        # 完全正相关
    assert abs(ic(x, -x) + 1.0) < 1e-9       # 完全负相关


def test_ic_zero_for_noise():
    rng = np.random.default_rng(0)
    a = rng.normal(size=500)
    b = rng.normal(size=500)   # 独立
    assert abs(ic(a, b)) < 0.15


def test_rank_ic_robust_to_outliers():
    rng = np.random.default_rng(1)
    x = rng.normal(size=200)
    y = x + rng.normal(0, 0.1, size=200)     # 强单调关系
    # 注入几个极端异常值（破坏 Pearson，不太破坏秩相关）
    x2, y2 = x.copy(), y.copy()
    x2[:5] = 50.0
    y2[:5] = -50.0
    assert rank_ic(x2, y2) > ic(x2, y2)      # RankIC 比 Pearson 更稳


def test_icir_stability():
    # 稳定正相关序列 ICIR 高；抖动序列 ICIR 低（甚至 nan）
    rng = np.random.default_rng(2)
    n = 400
    x = rng.normal(size=n)
    y_stable = x + rng.normal(0, 0.3, size=n)      # 常年正相关
    ir_stable = icir(x, y_stable, window=60)
    y_noise = rng.normal(size=n)                    # 无相关
    ir_noise = icir(x, y_noise, window=60)
    assert np.isfinite(ir_stable) and ir_stable > 1.0
    # 噪声序列的 ICIR 明显更低（接近 0 或 nan）
    assert not np.isfinite(ir_noise) or abs(ir_noise) < ir_stable


def test_width_ic_tracks_volatility():
    rng = np.random.default_rng(3)
    y_range = rng.uniform(0.02, 0.10, size=200)     # 真实振幅
    width_pred = y_range * 1.2 + rng.normal(0, 0.002, size=200)  # 预测宽度随振幅同步缩放
    assert width_ic(width_pred, y_range) > 0.8       # 跟得住 → 高
    shuffled = rng.permutation(width_pred)
    assert abs(width_ic(shuffled, y_range)) < 0.2    # 打乱 → ≈0


def test_signal_report_structure_and_width_primary():
    rng = np.random.default_rng(4)
    n = 150
    # 构造：预测宽度跟真实振幅，中点无方向信息（分位模型典型形态）
    y_low = -rng.uniform(0.01, 0.05, size=n)
    y_high = rng.uniform(0.01, 0.05, size=n)
    y_range = y_high - y_low
    lo_ret = y_low * 0.9 + rng.normal(0, 0.001, size=n)   # 下沿跟真实低点
    hi_ret = y_high * 0.9 + rng.normal(0, 0.001, size=n)  # 上沿跟真实高点
    rep = signal_report(lo_ret, hi_ret, y_low, y_high, window=60)
    assert set(rep) == {"width_ic", "mid_ic", "mid_rank_ic", "icir", "dir_hit", "n"}
    assert rep["n"] == n
    assert rep["width_ic"] > 0.7                          # 宽度信号强（主指标）


def test_handles_nan_and_short():
    # 含 NaN 不抛异常，短序列返回 nan
    a = np.array([1.0, np.nan, 3.0, 4.0, 5.0])
    b = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
    assert np.isfinite(ic(a, b)) or ic(a, b) != ic(a, b)  # 不抛异常即可
    assert ic([1.0], [2.0]) != ic([1.0], [2.0])           # 样本<3 → nan
    rep = signal_report([0.1], [0.2], [0.05], [0.15])
    assert rep["n"] == 1 and rep["width_ic"] is None       # 短序列 → None
