"""P3 策略/动作枚举 + LinUCB 纯逻辑单测。"""
import numpy as np

from mystock.ml.policy import (
    Action, BUY, SELL, LinUCB, N_ACTIONS, RulePolicy, enumerate_actions,
)


def test_action_table_size():
    # 不动 + 3frac×2qty 买 + 3frac×2qty 卖 = 13
    assert N_ACTIONS == 13


def test_enumerate_action_ids_stable():
    acts = enumerate_actions(100.0, 110.0, can_buy=True, can_sell=True)
    ids = [a.action_id for a in acts]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)  # 唯一且有序
    assert acts[0].side is None  # id 0 = 不动


def test_buy_prices_within_interval_low_side():
    acts = enumerate_actions(100.0, 110.0, can_buy=True, can_sell=False)
    buys = [a for a in acts if a.side == BUY]
    # 买挂价应落在 [L_hat, L_hat+0.5w] = [100, 105]
    assert all(100.0 <= a.limit_price <= 105.0 for a in buys)


def test_sell_prices_within_interval_high_side():
    acts = enumerate_actions(100.0, 110.0, can_buy=False, can_sell=True)
    sells = [a for a in acts if a.side == SELL]
    assert all(105.0 <= a.limit_price <= 110.0 for a in sells)


def test_cannot_buy_when_disabled():
    acts = enumerate_actions(100.0, 110.0, can_buy=False, can_sell=False)
    assert all(a.side is None for a in acts)  # 只剩"不动"


def test_rule_policy_buys_when_flat():
    rp = RulePolicy(qty_units=1)
    a = rp.act({"qty": 0, "avg_cost": 0, "mark": 100}, 99.0, 105.0)
    assert a.side == BUY and a.limit_price == 99.0


def test_rule_policy_sells_when_in_profit():
    rp = RulePolicy(qty_units=1)
    a = rp.act({"qty": 10, "avg_cost": 90, "mark": 100}, 99.0, 105.0)
    assert a.side == SELL and a.limit_price == 105.0


def test_linucb_learns_better_arm():
    # arm 1 给高 reward，arm 0 给低；同一 context，UCB 应逐渐偏向 arm 1
    np.random.seed(0)
    b = LinUCB(n_actions=2, dim=2, alpha=0.1, seed=0)
    x = np.array([1.0, 0.5])
    for _ in range(50):
        a = b.select(x, [0, 1])
        reward = 1.0 if a == 1 else 0.0
        b.update(a, x, reward)
    # 训练后对该 context，arm1 的预测均值应高于 arm0
    t0 = np.linalg.solve(b.A[0], b.b[0]) @ x
    t1 = np.linalg.solve(b.A[1], b.b[1]) @ x
    assert t1 > t0
