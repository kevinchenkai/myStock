"""P3 决策层策略：S0 规则基线 + S2 contextual bandit（LinUCB）。

动作（docs/ML_PLAN.md §3.2，离散+可解释）：
  - 在 [L_hat, H_hat] 区间内挑挂价档（买偏低、卖偏高）；
  - 在持仓/现金允许下挑数量档。
S0：固定规则（买挂 L_hat、卖挂 H_hat、固定量），永远保留做对照。
S2：LinUCB contextual bandit，按 state 特征在线选动作以最大化回合净值（单步 reward）。
    选 LinUCB 而非深度 RL：样本小、可解释、无 torch、方差可控（§6 务实定位）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

BUY = "BUY"
SELL = "SELL"

# 挂价档：相对区间宽度 w=H_hat-L_hat 的位置
#   买档：L_hat + frac*w（frac 越小越保守、越难成交）
#   卖档：H_hat - frac*w
PRICE_FRACS = (0.0, 0.25, 0.5)
# 数量档：相对"单位手数"的倍数（单位手数由 backtest 配置）
QTY_TIERS = (0, 1, 2)


@dataclass
class Action:
    side: Optional[str]      # "BUY" / "SELL" / None(不动)
    limit_price: Optional[float]
    qty_units: int           # 数量档（× unit_shares）
    action_id: int = 0       # 固定 id（bandit 臂索引，全局稳定）


# 固定动作表：id 0 = 不动；1..6 = 买(3 frac × {1,2} qty)；7..12 = 卖。共 13 臂。
_BUY_QTYS = (1, 2)
_SELL_QTYS = (1, 2)
N_ACTIONS = 1 + len(PRICE_FRACS) * len(_BUY_QTYS) + len(PRICE_FRACS) * len(_SELL_QTYS)


def _action_table():
    table = [("NONE", 0.0, 0)]  # (side, frac, qty)
    for f in PRICE_FRACS:
        for q in _BUY_QTYS:
            table.append((BUY, f, q))
    for f in PRICE_FRACS:
        for q in _SELL_QTYS:
            table.append((SELL, f, q))
    return table


_ACTION_TABLE = _action_table()


def enumerate_actions(L_hat: float, H_hat: float, *, can_buy: bool, can_sell: bool):
    """枚举可行动作（含"不动"），每个带稳定 action_id。"""
    w = max(H_hat - L_hat, 1e-9)
    acts = []
    for aid, (side, f, q) in enumerate(_ACTION_TABLE):
        if side == "NONE":
            acts.append(Action(None, None, 0, aid))
        elif side == BUY and can_buy:
            acts.append(Action(BUY, L_hat + f * w, q, aid))
        elif side == SELL and can_sell:
            acts.append(Action(SELL, H_hat - f * w, q, aid))
    return acts


# ---------------------------------------------------------------------------
# S0 规则基线
# ---------------------------------------------------------------------------
class RulePolicy:
    """买挂 L_hat、卖挂 H_hat、固定量；有持仓优先按方向轮动。

    简单确定性策略：无持仓→买；有持仓且浮盈→卖；否则继续按 L_hat 加仓一档。
    """
    def __init__(self, qty_units: int = 1):
        self.qty_units = qty_units

    def act(self, ctx: dict, L_hat: float, H_hat: float) -> Action:
        has_pos = ctx["qty"] > 0
        if not has_pos:
            return Action("BUY", L_hat, self.qty_units)
        # 有持仓：若现价高于均成本则挂高卖，否则挂低买（低吸）
        if ctx["mark"] >= ctx["avg_cost"] > 0:
            return Action("SELL", H_hat, self.qty_units)
        return Action("BUY", L_hat, self.qty_units)


# ---------------------------------------------------------------------------
# S2 LinUCB contextual bandit
# ---------------------------------------------------------------------------
class LinUCB:
    """每个动作一组线性模型，按 UCB 选动作。reward 为该步回合净值（标准化）。"""

    def __init__(self, n_actions: int, dim: int, alpha: float = 0.5,
                 seed: int = 0, epsilon: float = 0.0):
        self.n_actions = n_actions
        self.dim = dim
        self.alpha = alpha
        self.epsilon = epsilon          # ε-探索（P3.1）
        self.A = [np.eye(dim) for _ in range(n_actions)]
        self.b = [np.zeros(dim) for _ in range(n_actions)]
        self.rng = np.random.default_rng(seed)

    def select(self, x: np.ndarray, valid: list[int]) -> int:
        # ε-greedy：小概率随机探索，逃离早期被坏 reward 锁死的臂
        if self.epsilon > 0 and self.rng.random() < self.epsilon:
            return int(self.rng.choice(valid))
        best, best_p = valid[0], -np.inf
        for a in valid:
            Ainv = np.linalg.inv(self.A[a])
            theta = Ainv @ self.b[a]
            mean = float(theta @ x)
            bonus = self.alpha * float(np.sqrt(x @ Ainv @ x))
            p = mean + bonus
            if p > best_p:
                best_p, best = p, a
        return best

    def update(self, a: int, x: np.ndarray, reward: float) -> None:
        self.A[a] += np.outer(x, x)
        self.b[a] += reward * x
