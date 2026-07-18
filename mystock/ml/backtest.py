"""P3 回测引擎：决策层策略在 1h 模拟器上的逐日回放 + 三基线对照。

主指标（docs/ML_PLAN.md §7）：测试区间累计**达成交易净值**（卖出额−买入额，含期末持仓
按最后收盘折算），单标的独立账户（§3.4，不共享现金）。

对照三基线：
  1. buy_hold      — 期初一次性买入、期末市值（折算成"净值增量"口径）
  2. human_replay  — 用真实 ml_deals 在同一 1h 撮合规则下回放
  3. rule (S0)     — RulePolicy
被评估：bandit (S2, LinUCB)

严防泄漏：每个交易日 T 的决策只用截至 T 收盘的特征 + 预测；撮合用 T+1 的 1h bars。
预测器按 walk-forward：在 [0, split) 上 fit，[split, end) 上逐日推理与回测。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config as mlcfg
from . import data as mldata
from . import signal_eval as sig
from .features import FEATURE_COLS, build_features
from .policy import Action, LinUCB, N_ACTIONS, RulePolicy, enumerate_actions
from .predictor import IntervalModel
from .simulator import Account, BUY, SELL, match_limit_order


@dataclass
class BTConfig:
    init_cash: float = 20000.0
    unit_shares: int = 5          # 数量档单位
    train_frac: float = 0.6       # 前 60% 训练预测器+热身，后 40% 回测
    seed: int = 0
    high_alpha: float = 0.9
    low_alpha: float = 0.1
    bandit_alpha: float = 0.5
    # P3.1 改进开关（超额奖励=相对 buy&hold，直接对齐"打赢持有"）
    excess_reward: bool = True    # True→reward 用超额；False→用原始 step_pnl
    epsilon: float = 0.05         # bandit ε-探索（逃离早期坏臂）
    reward_scale: float = 50.0    # 超额奖励放大，提升 LinUCB 学习信号
    # 建议 2：CQR 校准（保留——提升区间命中率，属预测层；默认开）
    conformal: bool = True
    target_coverage: float = 0.8
    cal_frac: float = 0.25
    # 借鉴②（Plan §2.3）：预测器 fit 段与 test 段之间的隔离带。默认开——训练段
    # 末尾砍 purge_w 行，净值结论更诚实。purged=False 保留旧的紧贴切分做 A/B。
    purged: bool = True
    feat_lookback: int = 21       # 特征最长回看（价格空间复合窗，勿填 20）
    label_horizon: int = 1        # 标签前看（次日）


def compute_reward(step_pnl: float, bh_step: float, init_cash: float,
                   scale: float, *, excess: bool = True) -> float:
    """回合单步 reward（纯函数，可单测）。

      - excess=True：相对 buy&hold 的超额（(step_pnl - bh_step)/init_cash·scale），
        直接对齐"打赢持有"，是 P3.1 的默认。
      - excess=False：原始 step_pnl/init_cash·scale（不对齐基准）。

    注：曾试验过的风险调整 reward（sharpe / drawdown_penalized，即"Tier1 建议1"）
    未通过时段稳健性检验（6 段翻转、胜率 42%），已移除，详见
    docs/ML_TIER1_ROBUSTNESS.md。
    """
    if excess:
        return (step_pnl - bh_step) / init_cash * scale
    return step_pnl / init_cash * scale


def _state_vec(row: pd.Series) -> np.ndarray:
    """bandit 上下文特征（用已有日线特征子集 + 常数项）。"""
    feats = [float(row[c]) for c in FEATURE_COLS]
    return np.array([1.0] + feats, dtype=float)


def _mark_price(daily: pd.DataFrame, i: int) -> float:
    return float(daily.iloc[i]["close"])


def run_backtest(code: str, cfg: BTConfig | None = None, db_path=None) -> dict:
    cfg = cfg or BTConfig()
    daily = mldata.load_daily(code, db_path)
    feat = build_features(daily).reset_index(drop=True)
    bars_by_day = mldata.intraday_bars_by_day(code, db_path)

    # 只在特征齐全的行上决策
    valid = feat.dropna(subset=FEATURE_COLS + ["y_high_ret", "y_low_ret"]).index
    valid = [i for i in valid if i + 1 < len(feat)]  # 需要次日 bars
    if len(valid) < 60:
        return {"code": code, "error": "样本不足"}

    split_at = valid[int(len(valid) * cfg.train_frac)]
    # 借鉴②（Plan §2.3）：训练段末尾砍 purge_w 行隔离带（test 不变），使预测器
    # fit 段与 test 段不相邻——挤掉边界标签重叠 + 相似性乐观，净值结论更诚实。
    # 备注：purge 后 CQR 校准集（取自 train 尾部）随之前移，对 test 的"新鲜度"略降；
    # 若实测覆盖率系统性偏离目标，可考虑校准集仍取紧邻 test 的近样本（Plan §2.3 备注）。
    purge_w = (cfg.feat_lookback + cfg.label_horizon) if cfg.purged else 0
    train_df = feat.loc[[i for i in valid if i < split_at - purge_w]]
    lo_a, hi_a = mlcfg.alpha_for(code)  # 按股自适应分位（与报告一致）
    model = IntervalModel(
        seed=cfg.seed, high_alpha=hi_a, low_alpha=lo_a,
        conformal=cfg.conformal, target_coverage=cfg.target_coverage,
        cal_frac=cfg.cal_frac, label_horizon=cfg.label_horizon,
    ).fit(train_df)

    test_idx = [i for i in valid if i >= split_at]
    dim = 1 + len(FEATURE_COLS)

    # 预测向量化：模型 fit 后对全部测试行一次性推理（逐日单行 predict 是纯浪费，
    # 结果与单行完全一致——同模型、同特征、确定性）。推理产出 ret，价位在循环内还原。
    lo_ret, hi_ret = model.predict_ret(feat.loc[test_idx])
    lo_by_i = dict(zip(test_idx, lo_ret))
    hi_by_i = dict(zip(test_idx, hi_ret))

    # 各策略独立账户
    accs = {k: Account(cash=cfg.init_cash) for k in ("rule", "bandit", "human")}
    rule = RulePolicy(qty_units=1)
    bandit = LinUCB(n_actions=N_ACTIONS, dim=dim, alpha=cfg.bandit_alpha,
                    seed=cfg.seed, epsilon=cfg.epsilon)

    # 人类真实成交按天索引（用于 human_replay 在测试区间回放）
    deals = mldata.load_deals(code, db_path)
    deals_by_day: dict[str, list] = {}
    for _, d in deals.iterrows():
        deals_by_day.setdefault(str(d["create_time"])[:10], []).append(d)

    nav_curves = {k: [] for k in ("rule", "bandit", "human", "buy_hold")}
    nav_dates: list[str] = []
    bh_shares = None
    bh_prev = cfg.init_cash  # 上一步 buy&hold 净值（算超额奖励用）
    hit_n = hit_total = 0    # 区间命中统计（与 predictor 口径一致：次日真实高低全落入区间）
    # 借鉴③（Plan §3.3）：旁路收集信号序列（不改 reward/动作/净值），测试段末算 IC
    sig_lo, sig_hi, sig_yl, sig_yh = [], [], [], []

    for i in test_idx:
        row = feat.iloc[i]
        close_t = float(row["close"])
        next_day = feat.iloc[i + 1]["date"]
        bars = bars_by_day.get(next_day)
        if not bars:
            continue
        mark_next = _mark_price(feat, i + 1)
        # 兜底：今日 close / 次日 mark 为 NaN 则跳过该日（否则 equity=qty*nan 会污染
        # 整条净值曲线，总览显示 nan）。采集层已丢脏行，这里再防一道（DATA.md §4）。
        if not (np.isfinite(close_t) and np.isfinite(mark_next)):
            continue
        lo_r, hi_r = float(lo_by_i[i]), float(hi_by_i[i])
        L_hat, H_hat = close_t * (1 + lo_r), close_t * (1 + hi_r)
        # 区间命中：真实次日 high≤H_hat 且 low≥L_hat（ret 空间判定，与 predictor 口径一致）
        hit_total += 1
        if row["y_high_ret"] <= hi_r and row["y_low_ret"] >= lo_r:
            hit_n += 1
        # 借鉴③：旁路记录（预测下/上沿 + 真实次日低/高，ret 空间）
        sig_lo.append(lo_r); sig_hi.append(hi_r)
        sig_yl.append(float(row["y_low_ret"])); sig_yh.append(float(row["y_high_ret"]))

        # --- buy_hold：期初一次性买入并持有 ---
        if bh_shares is None:
            bh_shares = cfg.init_cash / close_t
        bh_now = bh_shares * mark_next
        bh_step = bh_now - bh_prev   # buy&hold 本步净值变化（超额奖励基准）
        bh_prev = bh_now
        nav_curves["buy_hold"].append(bh_now)
        nav_dates.append(next_day)

        # --- S0 规则 ---
        ract = rule.act(_ctx(accs["rule"], close_t), L_hat, H_hat)
        _apply(accs["rule"], ract, bars, cfg)
        nav_curves["rule"].append(accs["rule"].equity(mark_next))

        # --- S2 bandit ---
        x = _state_vec(row)
        acts = enumerate_actions(L_hat, H_hat,
                                 can_buy=accs["bandit"].cash > close_t * cfg.unit_shares,
                                 can_sell=accs["bandit"].qty > 0)
        by_id = {a.action_id: a for a in acts}
        eq_before = accs["bandit"].equity(mark_next)
        chosen_id = bandit.select(x, sorted(by_id.keys()))
        chosen = by_id[chosen_id]
        _apply(accs["bandit"], chosen, bars, cfg)
        eq_after = accs["bandit"].equity(mark_next)
        step_pnl = eq_after - eq_before
        reward = compute_reward(step_pnl, bh_step, cfg.init_cash,
                                cfg.reward_scale, excess=cfg.excess_reward)
        bandit.update(chosen_id, x, reward)
        nav_curves["bandit"].append(eq_after)

        # --- human replay ---
        for d in deals_by_day.get(next_day, []):
            f = match_limit_order(d["trd_side"], float(d["price"]), bars)
            if f.filled:
                if d["trd_side"] == BUY:
                    accs["human"].buy(f.fill_price, float(d["qty"]))
                else:
                    accs["human"].sell(f.fill_price, float(d["qty"]))
        nav_curves["human"].append(accs["human"].equity(mark_next))

    result = {
        "code": code,
        "n_test_days": len(nav_curves["bandit"]),
        # 测试窗区间命中率（与报告展示的自适应分位同口径，供报告直接引用）
        "interval_hit_rate": round(hit_n / hit_total, 4) if hit_total else None,
        "net_value": {  # 达成净值（已实现，卖出−买入）
            "rule": round(accs["rule"].realized, 2),
            "bandit": round(accs["bandit"].realized, 2),
            "human": round(accs["human"].realized, 2),
        },
        "final_equity": {  # 期末账户净值（含持仓折算）
            k: round(nav_curves[k][-1], 2) if nav_curves[k] else None
            for k in ("rule", "bandit", "human", "buy_hold")
        },
        "init_cash": cfg.init_cash,
        "backend": "lightgbm" if _lgb() else "sklearn",
        "nav_curves": nav_curves,
        "nav_dates": nav_dates,
        # 借鉴③：信号层评估（旁路观测，不影响净值）。width_ic 为主指标。
        "signal": sig.signal_report(sig_lo, sig_hi, sig_yl, sig_yh),
        # 口径标注（报告头部用）
        "reward_mode": "excess" if cfg.excess_reward else "raw",
        "conformal": bool(cfg.conformal),
        "purged": bool(cfg.purged),
    }
    return result


# ---- helpers ----
def _ctx(acc: Account, mark: float) -> dict:
    return {"qty": acc.qty, "avg_cost": acc.avg_cost, "mark": mark}


def _apply(acc: Account, a: Action, bars, cfg: BTConfig) -> None:
    if a.side is None or a.qty_units <= 0:
        return
    qty = a.qty_units * cfg.unit_shares
    f = match_limit_order(a.side, a.limit_price, bars)
    if not f.filled:
        return
    if a.side == BUY:
        acc.buy(f.fill_price, qty)
    else:
        acc.sell(f.fill_price, qty)


def _lgb():
    try:
        import lightgbm  # noqa: F401
        return True
    except Exception:
        return False


if __name__ == "__main__":
    from dataclasses import replace
    # 标准口径：超额-bandit + CQR 校准 + purged 隔离带（与 report 同口径）。
    # 风险调整 reward / regime 软切换（原 Tier1 建议1+3）未通过时段稳健性检验，
    # 已移除，详见 docs/ML_TIER1_ROBUSTNESS.md。
    base = BTConfig()  # conformal/purged 默认开
    print(f"{'code':9} {'days':>5} {'rule':>9} {'bandit':>9} {'human':>9} {'buy_hold':>9}  net(bandit)")
    for code in mlcfg.TARGETS:
        cov = mlcfg.coverage_for(code)
        cfg_i = replace(base, target_coverage=cov)
        r = run_backtest(code, cfg_i)
        if "error" in r:
            print(f"{code}: {r['error']}"); continue
        fe = r["final_equity"]
        s = r.get("signal") or {}
        print(f"{code:9} {r['n_test_days']:>5} "
              f"{fe['rule']:>9} {fe['bandit']:>9} {fe['human']:>9} {fe['buy_hold']:>9}  "
              f"净值={r['net_value']['bandit']} 命中={r['interval_hit_rate']} "
              f"宽度IC={s.get('width_ic')}  [{r['backend']}]")
