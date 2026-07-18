"""Conformalized Quantile Regression (CQR) —— 区间预测的有限样本覆盖率校准。

docs/ML_ALGORITHM_PROPOSAL.md 建议 2。在现有分位数回归（predictor.IntervalModel）
外面套一层 split conformal 校准，把按股手调的 ALPHA_BY_CODE 分位档替换为
"目标覆盖率 → 自适应半宽"，并给出有限样本覆盖率保证（标准 QR 只有渐近保证）。

口径（CQR，Romano et al. 2019）：
  - non-conformity score（"需扩展多少才能包住"，越大表示区间越不够）：
        s_i = max(L_hat_i - y_low_i, y_high_i - H_hat_i)
    （预测下沿 L_hat 高于真值下沿 → L_hat - y_low > 0，下侧戳出；
     预测上沿 H_hat 低于真值上沿 → y_high - H_hat > 0，上侧戳出；
     取 max 同时惩罚两侧。区间已包住时 s_i <= 0，表示可适当收紧。）
  - 校准分位 q = ⌈(n+1)(1-α)⌉-1 分位的 score（有限样本覆盖率 ≥ 1-α）。
  - 校准后区间 [L_hat - q, H_hat + q]：q > 0 扩展（base 偏窄）、q < 0 收紧
    （base 偏宽，CQR 自适应到目标覆盖率，这正是它替代手调 α 的价值）。

本模块全部纯函数，可单测。不依赖 LightGBM / sklearn —— 只对 (y, hat) 数组算分位。
"""
from __future__ import annotations

from typing import Optional

import numpy as np


def nonconformity_scores(
    y_low: np.ndarray, L_hat: np.ndarray,
    y_high: np.ndarray, H_hat: np.ndarray,
) -> np.ndarray:
    """CQR non-conformity score（半宽损失）。

    所有数组同长。返回 s_i = max(L_hat_i - y_low_i, y_high_i - H_hat_i)
    （Romano 约定：s>0 = 区间需扩展 s 才能包住、s<=0 = 已包含可收紧）。
    NaN 自动剔除（不出现在结果里）。
    """
    y_low = np.asarray(y_low, dtype=float)
    y_high = np.asarray(y_high, dtype=float)
    L_hat = np.asarray(L_hat, dtype=float)
    H_hat = np.asarray(H_hat, dtype=float)
    s = np.maximum(L_hat - y_low, y_high - H_hat)
    return s[np.isfinite(s)]


def conformal_quantile(scores: np.ndarray, target_coverage: float) -> float:
    """从校准集 score 取校准半宽 q，保证有限样本覆盖率 ≥ target_coverage。

    用 (n+1) 分位修正（Vovk 等），保证 ≥ 1-α 的覆盖率：
        q = ⌈(n+1)(1-α)⌉-1 分位（即第 ceil((n+1)*(1-α)) 个升序 score）。
    target_coverage = 1 - α。如 0.80 → α=0.20。
    n=0 或 score 全空 → 0.0（不校准，退回原区间）。
    """
    if not 0.0 < target_coverage < 1.0:
        return 0.0
    scores = np.asarray(scores, dtype=float)
    scores = scores[np.isfinite(scores)]
    n = len(scores)
    if n == 0:
        return 0.0
    alpha = 1.0 - target_coverage
    # ⌈(n+1)(1-α)⌉，再转 0-based index（-1）。clamp 到 [0, n-1]。
    rank = int(np.ceil((n + 1) * (1.0 - alpha)))
    idx = max(0, min(n - 1, rank - 1))
    return float(np.sort(scores)[idx])


def apply_conformal(
    L_hat: np.ndarray, H_hat: np.ndarray, q: float
) -> tuple[np.ndarray, np.ndarray]:
    """用校准半宽 q 扩展区间：[L_hat - q, H_hat + q]。q<=0 时区间不变。"""
    L_hat = np.asarray(L_hat, dtype=float)
    H_hat = np.asarray(H_hat, dtype=float)
    return L_hat - q, H_hat + q


def interval_hit(
    y_low: np.ndarray, y_high: np.ndarray,
    L_hat: np.ndarray, H_hat: np.ndarray,
) -> float:
    """区间命中率：真实次日 high<=H_hat 且 low>=L_hat 的比例（与 predictor 口径一致）。

    任意一侧 NaN 的样本不计入分母（与 walk_forward_eval 的 dropna 等价）。
    """
    y_low = np.asarray(y_low, dtype=float)
    y_high = np.asarray(y_high, dtype=float)
    L_hat = np.asarray(L_hat, dtype=float)
    H_hat = np.asarray(H_hat, dtype=float)
    mask = np.isfinite(y_low) & np.isfinite(y_high) & np.isfinite(L_hat) & np.isfinite(H_hat)
    if not mask.any():
        return 0.0
    hit = (y_high[mask] <= H_hat[mask]) & (y_low[mask] >= L_hat[mask])
    return float(hit.mean())


def mean_width_pct(
    L_hat: np.ndarray, H_hat: np.ndarray, close: np.ndarray
) -> float:
    """平均区间宽度 / 收盘（%），用于验收门槛"宽度 ≤ +15%"对照。"""
    L_hat = np.asarray(L_hat, dtype=float)
    H_hat = np.asarray(H_hat, dtype=float)
    close = np.asarray(close, dtype=float)
    mask = np.isfinite(L_hat) & np.isfinite(H_hat) & np.isfinite(close) & (close > 0)
    if not mask.any():
        return 0.0
    return float(np.mean((H_hat[mask] - L_hat[mask]) / close[mask]) * 100.0)


def calibrate(
    y_low_cal: np.ndarray, L_hat_cal: np.ndarray,
    y_high_cal: np.ndarray, H_hat_cal: np.ndarray,
    target_coverage: float,
) -> float:
    """一步完成校准：算 non-conformity score → 取 conformal 分位 → 返回 q。"""
    s = nonconformity_scores(y_low_cal, L_hat_cal, y_high_cal, H_hat_cal)
    return conformal_quantile(s, target_coverage)


def split_calibrate(
    feat_train: "object",          # 带 y_high_ret/y_low_ret 的 DataFrame（已 fit 用）
    lo_ret_train: np.ndarray,      # 在 train 上 fit 后对 train 自身的下分位预测
    hi_ret_train: np.ndarray,
    close_train: np.ndarray,
    target_coverage: float,
    cal_frac: float = 0.25,
) -> Optional[float]:
    """从训练集切出最近 cal_frac 段做校准（时序末尾，无泄漏）。

    返回 q（价位空间的半宽）。返回 None 表示样本不足，调用方应退回原区间。
    feat_train 须按时间升序；校准集取末尾 cal_frac 行。
    """
    n = len(feat_train)
    if n < 20 or cal_frac <= 0:
        return None
    n_cal = max(5, int(n * cal_frac))
    cal_lo = lo_ret_train[-n_cal:]
    cal_hi = hi_ret_train[-n_cal:]
    y_lo = np.asarray(feat_train["y_low_ret"].to_numpy()[-n_cal:], dtype=float)
    y_hi = np.asarray(feat_train["y_high_ret"].to_numpy()[-n_cal:], dtype=float)
    # ret 空间的 score → 用最近 close 还原成价位空间半宽（与 apply 同口径）
    q_ret = calibrate(y_lo, cal_lo, y_hi, cal_hi, target_coverage)
    if not np.isfinite(q_ret):
        return None
    # 价位半宽 = 最近 close × |q_ret|（ret 是相对 close 的比例）
    last_close = float(close_train[-1]) if len(close_train) else 0.0
    return q_ret * last_close if last_close > 0 else q_ret


if __name__ == "__main__":
    # 自检：构造一组被包住的样本，q 应为 <=0（区间已足够），apply 后区间不变或更宽。
    rng = np.random.default_rng(0)
    y_lo = rng.normal(0, 0.01, 200)
    y_hi = y_lo + rng.uniform(0.01, 0.03, 200)
    L = y_lo - 0.005   # 区间下沿略低于真值
    H = y_hi + 0.005   # 区间上沿略高于真值
    q = calibrate(y_lo, L, y_hi, H, target_coverage=0.80)
    print(f"q={q:.5f}（区间已包住，q 应 <=0）  命中={interval_hit(y_lo, y_hi, L, H):.2%}")
    # 故意把区间收窄一半 → q 应为正，apply 后命中率回升
    Ln, Hn = (L + H) / 2 + (L - H) * 0.25, (L + H) / 2 + (H - L) * 0.25
    q2 = calibrate(y_lo, Ln, y_hi, Hn, target_coverage=0.80)
    Lc, Hc = apply_conformal(Ln, Hn, q2)
    print(f"收窄后 q={q2:.5f}  原命中={interval_hit(y_lo, y_hi, Ln, Hn):.2%}  "
          f"校准后命中={interval_hit(y_lo, y_hi, Lc, Hc):.2%}")
