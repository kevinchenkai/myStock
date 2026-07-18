"""借鉴 ③ — 信号层评估（IC/RankIC/ICIR），抄 qlib SigAnaRecord 口径。

两级评估的第一级：先看**信号质量**（预测有没有预测力），再看**策略净值**
（有没有把信号换成钱）。回答评估报告悬案——bandit 输是信号弱还是执行弱。

单标的 → **时间轴 IC**（非截面）。qlib 的 IC 是同一天跨数千标的的截面相关，
本项目单标的截面维度=1，截面 IC 无意义，改用时间轴滚动 IC。纯 numpy/scipy，
不依赖建模库，可单测。

诚实局限（报告须标注）：单标的时间轴 IC 样本量 = 测试天数（几百），比截面 IC 弱；
它是"信号随时间是否稳定有预测力"的诊断，不是选股 IC，不作 go/no-go 硬门槛。
"""
from __future__ import annotations

import numpy as np


def _clean(pred, target):
    """对齐两序列、去 NaN/Inf。返回 (p, t)（可能为空）。"""
    p = np.asarray(pred, dtype=float)
    t = np.asarray(target, dtype=float)
    n = min(len(p), len(t))
    p, t = p[:n], t[:n]
    ok = np.isfinite(p) & np.isfinite(t)
    return p[ok], t[ok]


def _pearson(p, t) -> float:
    if len(p) < 3 or np.std(p) < 1e-12 or np.std(t) < 1e-12:
        return float("nan")
    return float(np.corrcoef(p, t)[0, 1])


def _spearman(p, t) -> float:
    if len(p) < 3:
        return float("nan")
    # 用秩变换后的 Pearson（避免 scipy 依赖；与 spearmanr 等价）
    pr = _rankdata(p)
    tr = _rankdata(t)
    return _pearson(pr, tr)


def _rankdata(a: np.ndarray) -> np.ndarray:
    """平均秩（并列取均值），等价 scipy.stats.rankdata。"""
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1, dtype=float)
    # 处理并列：同值取平均秩
    _, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inv, ranks)
    return (sums / counts)[inv]


def ic(pred, target) -> float:
    """Pearson IC（去 NaN、样本<3 或零方差 → nan）。"""
    p, t = _clean(pred, target)
    return _pearson(p, t)


def rank_ic(pred, target) -> float:
    """Spearman RankIC（对异常值稳健）。"""
    p, t = _clean(pred, target)
    return _spearman(p, t)


def rolling_ic(pred, target, window: int = 60) -> np.ndarray:
    """滚动窗 IC 序列（每点用过去 window 天的 Pearson IC）。

    返回长度 = len(pred)，前 window-1 个为 nan。
    """
    p = np.asarray(pred, dtype=float)
    t = np.asarray(target, dtype=float)
    n = min(len(p), len(t))
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        pw, tw = _clean(p[i - window + 1:i + 1], t[i - window + 1:i + 1])
        out[i] = _pearson(pw, tw)
    return out


def icir(pred, target, window: int = 60) -> float:
    """ICIR = mean(rolling_ic) / std(rolling_ic)。信号稳定性。

    比单点 IC 更能说明问题：高 ICIR = 信号常年稳定有预测力。
    """
    roll = rolling_ic(pred, target, window)
    roll = roll[np.isfinite(roll)]
    if len(roll) < 2 or np.std(roll) < 1e-12:
        return float("nan")
    return float(np.mean(roll) / np.std(roll))


def width_ic(width_pred, y_range) -> float:
    """宽度 IC：预测区间宽 vs 真实振幅（Spearman）。本系统的**主信号指标**。

    理由：predictor 是分位模型，从未被训练去猜方向；决策层赚的是"低买高卖吃区间"，
    可用性首先取决于预测区间对真实振幅的跟踪能力。
    """
    p, t = _clean(width_pred, y_range)
    return _spearman(p, t)


def signal_report(lo_ret, hi_ret, y_low, y_high, window: int = 60) -> dict:
    """一站式信号诊断。

    输入四条 ret 空间序列（预测下沿/上沿 + 真实次日低/高相对今收的比例）：
      - width_pred = hi_ret - lo_ret（预测区间宽）
      - y_range    = y_high - y_low（真实次日振幅）
      - mid_ret    = (lo_ret + hi_ret) / 2（区间中点隐含方向/幅度，次级诊断）
      - y_mid      = (y_low + y_high) / 2（真实次日高低中点相对今收，方向代理）
        —— 待确认1：先用中点近似，零新标签、复用现成预测；方向标签作后续增强。

    返回 {width_ic（主）, mid_ic, mid_rank_ic, icir, dir_hit, n}。
    mid_ic ≈ 0 **不等于**信号无效——分位模型本就没学方向，须结合 width_ic 判读。
    """
    lo = np.asarray(lo_ret, dtype=float)
    hi = np.asarray(hi_ret, dtype=float)
    yl = np.asarray(y_low, dtype=float)
    yh = np.asarray(y_high, dtype=float)
    width_pred = hi - lo
    y_range = yh - yl
    mid_ret = (lo + hi) / 2
    y_mid = (yl + yh) / 2

    _, mclean = _clean(mid_ret, y_mid)  # 仅用于 n 统计
    dir_hit = _dir_hit(mid_ret, y_mid)
    return {
        "width_ic": _round(width_ic(width_pred, y_range)),    # 主指标（Spearman）
        "mid_ic": _round(ic(mid_ret, y_mid)),                 # 次级：方向/幅度 Pearson
        "mid_rank_ic": _round(rank_ic(mid_ret, y_mid)),       # 次级：方向 Spearman
        "icir": _round(icir(mid_ret, y_mid, window)),         # 中点信号稳定性
        "dir_hit": _round(dir_hit),                           # 方向命中率（再次级）
        "n": int(len(mclean)),
    }


def _dir_hit(mid_ret, y_mid) -> float:
    """方向命中率：sign(mid_ret) == sign(y_mid) 的比例。样本<3 → nan。"""
    p, t = _clean(mid_ret, y_mid)
    if len(p) < 3:
        return float("nan")
    return float(np.mean(np.sign(p) == np.sign(t)))


def _round(v, nd: int = 4):
    return None if v is None or (isinstance(v, float) and v != v) else round(float(v), nd)
