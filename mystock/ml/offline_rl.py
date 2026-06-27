"""P4 — 离线 RL（研究性增量，docs/ML_PLAN.md §6 S3）。

务实定位：小样本上 model-free 在线 RL 方差极大，故走**离线 RL**——
  1. 用行为策略（S0 规则 + ε-随机，保证动作覆盖）在 1h 模拟器上 rollout，记录
     (state, action, reward, next_state, terminal) 形成离线数据集；
  2. 训练 Discrete CQL（保守 Q 学习，小离线集首选，抑制高估）；
  3. 用与 P3 相同的回测口径评估学到的策略 vs 三基线（可比）。

与 P1–P3 一致：相同 13 个离散动作、相同 1h 撮合、相同"超额奖励"与净值口径。
需 d3rlpy + GPU（在 H20 prefix env 跑）。CPU 也能跑（慢）。
运行：python -m mystock.ml.offline_rl
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config as mlcfg
from . import data as mldata
from .backtest import BTConfig, _mark_price, _state_vec, _apply, _ctx
from .features import FEATURE_COLS, build_features
from .policy import N_ACTIONS, RulePolicy, enumerate_actions
from .simulator import Account, BUY, SELL, match_limit_order


@dataclass
class RLConfig:
    init_cash: float = 20000.0
    unit_shares: int = 5
    train_frac: float = 0.6
    seed: int = 0
    high_alpha: float = 0.9
    low_alpha: float = 0.1
    n_episodes: int = 40          # rollout 多少条轨迹（不同 ε/种子）增样
    behavior_eps: float = 0.5     # 行为策略探索率（高→覆盖广，利于离线学习）
    n_steps: int = 20000          # CQL 训练步数
    reward_scale: float = 50.0


# ---------------------------------------------------------------------------
# 1) 用行为策略 rollout 生成离线数据集
# ---------------------------------------------------------------------------
def collect_dataset(code: str, cfg: RLConfig, db_path=None):
    """返回 (observations, actions, rewards, terminals)，只用训练区间（防泄漏）。"""
    daily = mldata.load_daily(code, db_path)
    feat = build_features(daily).reset_index(drop=True)
    bars_by_day = mldata.intraday_bars_by_day(code, db_path)
    valid = feat.dropna(subset=FEATURE_COLS + ["y_high_ret", "y_low_ret"]).index
    valid = [i for i in valid if i + 1 < len(feat)]
    split_at = valid[int(len(valid) * cfg.train_frac)]
    train_idx = [i for i in valid if i < split_at]   # 仅训练区间 rollout

    # 训练区间内用全历史前段 fit 预测器（防泄漏：只用 < split 的数据）
    from .predictor import IntervalModel, _predict_silent
    model = IntervalModel(seed=cfg.seed, high_alpha=cfg.high_alpha,
                          low_alpha=cfg.low_alpha).fit(feat.loc[train_idx])

    rule = RulePolicy(qty_units=1)
    obs, acts, rews, terms = [], [], [], []
    rng = np.random.default_rng(cfg.seed)

    for ep in range(cfg.n_episodes):
        acc = Account(cash=cfg.init_cash)
        bh_shares, bh_prev = None, cfg.init_cash
        for j, i in enumerate(train_idx):
            row = feat.iloc[i]
            close_t = float(row["close"])
            next_day = feat.iloc[i + 1]["date"]
            bars = bars_by_day.get(next_day)
            if not bars:
                continue
            L = close_t * (1 + float(_predict_silent(model.m_low, row[FEATURE_COLS].values.reshape(1, -1))[0]))
            H = close_t * (1 + float(_predict_silent(model.m_high, row[FEATURE_COLS].values.reshape(1, -1))[0]))
            mark_next = _mark_price(feat, i + 1)
            if bh_shares is None:
                bh_shares = cfg.init_cash / close_t
            bh_now = bh_shares * mark_next
            bh_step = bh_now - bh_prev
            bh_prev = bh_now

            x = _state_vec(row).astype(np.float32)
            avail = enumerate_actions(L, H, can_buy=acc.cash > close_t * cfg.unit_shares,
                                      can_sell=acc.qty > 0)
            by_id = {a.action_id: a for a in avail}
            # 行为策略：ε 随机 / 否则 S0 规则
            if rng.random() < cfg.behavior_eps:
                aid = int(rng.choice(sorted(by_id.keys())))
            else:
                ra = rule.act(_ctx(acc, close_t), L, H)
                aid = ra.action_id if ra.action_id in by_id else 0
            chosen = by_id[aid]
            eq_before = acc.equity(mark_next)
            _apply(acc, chosen, bars, cfg)
            eq_after = acc.equity(mark_next)
            reward = (eq_after - eq_before - bh_step) / cfg.init_cash * cfg.reward_scale

            obs.append(x)
            acts.append(aid)
            rews.append(np.float32(reward))
            terms.append(1.0 if j == len(train_idx) - 1 else 0.0)

    return (np.array(obs, dtype=np.float32),
            np.array(acts, dtype=np.int64),
            np.array(rews, dtype=np.float32),
            np.array(terms, dtype=np.float32))


# ---------------------------------------------------------------------------
# 2) 训练 Discrete CQL
# ---------------------------------------------------------------------------
def train_cql(obs, acts, rews, terms, cfg: RLConfig):
    import d3rlpy
    from d3rlpy.dataset import MDPDataset
    from d3rlpy.algos import DiscreteCQLConfig

    dataset = MDPDataset(observations=obs, actions=acts.reshape(-1, 1),
                         rewards=rews.reshape(-1, 1), terminals=terms)
    device = "cuda:0" if _cuda() else "cpu"
    cql = DiscreteCQLConfig().create(device=device)
    cql.fit(dataset, n_steps=cfg.n_steps, n_steps_per_epoch=max(1000, cfg.n_steps // 10),
            show_progress=False)
    return cql


def _cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 3) 用学到的策略在测试区间回测（与 P3 同口径）
# ---------------------------------------------------------------------------
def eval_policy(code: str, cql, cfg: RLConfig, db_path=None) -> dict:
    daily = mldata.load_daily(code, db_path)
    feat = build_features(daily).reset_index(drop=True)
    bars_by_day = mldata.intraday_bars_by_day(code, db_path)
    valid = feat.dropna(subset=FEATURE_COLS + ["y_high_ret", "y_low_ret"]).index
    valid = [i for i in valid if i + 1 < len(feat)]
    split_at = valid[int(len(valid) * cfg.train_frac)]
    test_idx = [i for i in valid if i >= split_at]

    from .predictor import IntervalModel, _predict_silent
    model = IntervalModel(seed=cfg.seed, high_alpha=cfg.high_alpha,
                          low_alpha=cfg.low_alpha).fit(feat.loc[[i for i in valid if i < split_at]])

    acc = Account(cash=cfg.init_cash)
    bh_shares = None
    nav = []
    for i in test_idx:
        row = feat.iloc[i]
        close_t = float(row["close"])
        next_day = feat.iloc[i + 1]["date"]
        bars = bars_by_day.get(next_day)
        if not bars:
            continue
        L = close_t * (1 + float(_predict_silent(model.m_low, row[FEATURE_COLS].values.reshape(1, -1))[0]))
        H = close_t * (1 + float(_predict_silent(model.m_high, row[FEATURE_COLS].values.reshape(1, -1))[0]))
        mark_next = _mark_price(feat, i + 1)
        if bh_shares is None:
            bh_shares = cfg.init_cash / close_t

        x = _state_vec(row).astype(np.float32).reshape(1, -1)
        aid = int(cql.predict(x)[0])
        avail = {a.action_id: a for a in enumerate_actions(
            L, H, can_buy=acc.cash > close_t * cfg.unit_shares, can_sell=acc.qty > 0)}
        chosen = avail.get(aid)
        if chosen is not None:
            _apply(acc, chosen, bars, cfg)
        nav.append(acc.equity(mark_next))

    return {
        "code": code,
        "n_test_days": len(nav),
        "final_equity": round(nav[-1], 2) if nav else None,
        "net_value": round(acc.realized, 2),
        "buy_hold": round(bh_shares * _mark_price(feat, test_idx[-1] + 1), 2) if bh_shares else None,
    }


def run(code: str, cfg: RLConfig | None = None, db_path=None) -> dict:
    cfg = cfg or RLConfig()
    obs, acts, rews, terms = collect_dataset(code, cfg, db_path)
    cql = train_cql(obs, acts, rews, terms, cfg)
    res = eval_policy(code, cql, cfg, db_path)
    res["dataset_size"] = len(obs)
    res["device"] = "cuda" if _cuda() else "cpu"
    return res


if __name__ == "__main__":
    for code in mlcfg.TARGETS:
        r = run(code)
        print(f"{code}: CQL 期末={r['final_equity']} buy&hold={r['buy_hold']} "
              f"达成净值={r['net_value']} (数据 {r['dataset_size']} transitions, {r['device']})")
