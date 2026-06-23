"""P1 — 1h 盘中限价撮合模拟器（纯函数，可单测）。

口径见 docs/ML_PLAN.md §4：
  - 对某交易日的逐根 1h K 线按盘中顺序遍历；
  - 限价买 P_b：第一根满足 low(bar) <= P_b 的 bar 成交，成交价 = min(P_b, open(bar))；
  - 限价卖 P_s：第一根满足 high(bar) >= P_s 的 bar 成交，成交价 = max(P_s, open(bar))；
  - 当日未触达 → 不成交（当日有效单）。
相比纯日线，能正确处理盘中"先到 low 还是先到 high"的顺序。
残余近似：1h bar 内部顺序、部分成交、滑点未建模（如实标注）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

BUY = "BUY"
SELL = "SELL"


@dataclass
class Fill:
    filled: bool
    fill_price: Optional[float] = None
    bar_index: Optional[int] = None  # 第几根 bar 成交（0-based）
    ts_et: Optional[str] = None


def match_limit_order(
    side: str,
    limit_price: float,
    bars: Sequence[dict],
) -> Fill:
    """对单日 1h bars 撮合一个限价单。

    bars: [{open, high, low, close, [ts_et]}...]，按盘中时间升序。
    """
    if not bars or limit_price is None:
        return Fill(filled=False)

    for i, bar in enumerate(bars):
        o, hi, lo = bar["open"], bar["high"], bar["low"]
        if side == BUY:
            if lo <= limit_price:
                # 成交价：开盘已低于挂价则按开盘（更优），否则按挂价
                price = min(limit_price, o)
                return Fill(True, price, i, bar.get("ts_et"))
        elif side == SELL:
            if hi >= limit_price:
                price = max(limit_price, o)
                return Fill(True, price, i, bar.get("ts_et"))
        else:
            raise ValueError(f"未知方向 {side!r}，应为 BUY/SELL")
    return Fill(filled=False)


@dataclass
class Account:
    """单标的独立账户（docs/ML_PLAN.md §3.4：不共享现金池）。"""
    cash: float
    qty: float = 0.0
    # 移动平均成本（券商口径）；用于结算回合净值
    avg_cost: float = 0.0
    realized: float = 0.0  # 累计已实现净值（卖出额 - 对应买入成本）

    def buy(self, price: float, qty: float) -> None:
        cost = price * qty
        if qty <= 0 or cost > self.cash + 1e-9:
            return
        new_qty = self.qty + qty
        self.avg_cost = (self.avg_cost * self.qty + cost) / new_qty if new_qty > 0 else 0.0
        self.qty = new_qty
        self.cash -= cost

    def sell(self, price: float, qty: float) -> None:
        qty = min(qty, self.qty)
        if qty <= 0:
            return
        proceeds = price * qty
        self.realized += proceeds - self.avg_cost * qty  # 回合净值（毛额）
        self.qty -= qty
        self.cash += proceeds
        if self.qty <= 1e-9:
            self.qty = 0.0
            self.avg_cost = 0.0

    def equity(self, mark_price: float) -> float:
        """账户净值 = 现金 + 持仓市值。"""
        return self.cash + self.qty * mark_price
