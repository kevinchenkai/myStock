"""P2 — 次日 high/low 区间预测器（分位数回归）。

docs/ML_PLAN.md S1：预测 next-day high/low，输出 [L_hat, H_hat] 区间 + 不确定性。
口径：
  - 目标用比例（y_high_ret / y_low_ret，相对今日 close），推理时还原成价位。
  - high 取上分位、low 取下分位张成区间 [L_hat, H_hat]。分位越靠 0.5 区间越窄、
    命中率越低（宽度与覆盖直接对赌）；生产口径按股自适应（config.alpha_for）。
  - 评估：pinball loss + 区间命中率（真实 high/low 同时落在 [L_hat,H_hat] 的比例）+ MAE。
  - 切分：按时间 walk-forward，绝不随机打散（防泄漏）。

后端优先 LightGBM（objective=quantile），无则回退 sklearn GradientBoostingRegressor(loss=quantile)。
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .features import FEATURE_COLS, build_features


def _predict_silent(model, X):
    """对 fit 过的模型做 numpy 预测，并精确静默 sklearn/lightgbm 的
    "X does not have valid feature names" 无害告警。

    根因：lightgbm 即便用 numpy fit 也会自动合成列名（Column_0..），predict 传
    裸 numpy 时校验层判定"无列名 vs 有列名"不一致而告警。我们保证列顺序一致
    （FEATURE_COLS），故安全静默。用 catch_warnings 局部作用，不污染全局、
    不被外部 -W 覆盖（比模块级 filterwarnings 更稳健）。
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="X does not have valid feature names",
            category=UserWarning,
        )
        return model.predict(X)

try:
    import lightgbm as lgb
    _HAS_LGB = True
except Exception:  # noqa: BLE001
    _HAS_LGB = False

from sklearn.ensemble import GradientBoostingRegressor


def _fit_quantile(X, y, alpha: float, seed: int = 0):
    if _HAS_LGB:
        m = lgb.LGBMRegressor(
            objective="quantile", alpha=alpha, n_estimators=300,
            learning_rate=0.03, num_leaves=15, min_child_samples=30,
            subsample=0.8, colsample_bytree=0.8, random_state=seed, verbose=-1,
        )
    else:
        m = GradientBoostingRegressor(
            loss="quantile", alpha=alpha, n_estimators=300,
            learning_rate=0.03, max_depth=3, subsample=0.8, random_state=seed,
        )
    m.fit(X, y)
    return m


def pinball_loss(y_true, y_pred, alpha: float) -> float:
    d = y_true - y_pred
    return float(np.mean(np.maximum(alpha * d, (alpha - 1) * d)))


@dataclass
class IntervalModel:
    """一支标的的区间模型：high 取上分位、low 取下分位。"""
    seed: int = 0
    high_alpha: float = 0.9
    low_alpha: float = 0.1
    m_high: object = None
    m_low: object = None

    def fit(self, df: pd.DataFrame):
        # 用纯 numpy（无列名）fit，预测端也用 numpy → 一致，消除 sklearn/lightgbm
        # "X does not have valid feature names" 警告。
        X = df[FEATURE_COLS].to_numpy()
        self.m_high = _fit_quantile(X, df["y_high_ret"].to_numpy(), self.high_alpha, self.seed)
        self.m_low = _fit_quantile(X, df["y_low_ret"].to_numpy(), self.low_alpha, self.seed)
        return self

    def predict_ret(self, df: pd.DataFrame):
        X = df[FEATURE_COLS].to_numpy()
        return _predict_silent(self.m_low, X), _predict_silent(self.m_high, X)

    def predict_prices(self, df: pd.DataFrame):
        """返回 (L_hat, H_hat) 价位，相对各行 close 还原。"""
        lo_ret, hi_ret = self.predict_ret(df)
        return df["close"].values * (1 + lo_ret), df["close"].values * (1 + hi_ret)


@dataclass
class WalkForwardResult:
    code: str
    n_folds: int
    metrics: dict = field(default_factory=dict)
    per_fold: list = field(default_factory=list)


def walk_forward_eval(
    daily: pd.DataFrame, code: str, *,
    n_folds: int = 4, min_train: int = 250, seed: int = 0,
    high_alpha: float = 0.9, low_alpha: float = 0.1,
) -> WalkForwardResult:
    """按时间滚动评估。返回区间命中率 / pinball / MAE 的汇总。"""
    df = build_features(daily).dropna(subset=FEATURE_COLS + ["y_high_ret", "y_low_ret"])
    df = df.reset_index(drop=True)
    n = len(df)
    if n < min_train + n_folds * 20:
        # 数据太短，单折评估
        n_folds = max(1, (n - min_train) // 30)

    fold_size = max(20, (n - min_train) // max(1, n_folds))
    hits, pin_h, pin_l, mae_h, mae_l = [], [], [], [], []
    per_fold = []

    for k in range(n_folds):
        tr_end = min_train + k * fold_size
        te_end = min(tr_end + fold_size, n)
        if tr_end >= n or te_end <= tr_end:
            break
        tr, te = df.iloc[:tr_end], df.iloc[tr_end:te_end]
        model = IntervalModel(seed=seed, high_alpha=high_alpha, low_alpha=low_alpha).fit(tr)
        lo_ret, hi_ret = model.predict_ret(te)

        # 区间命中：真实次日 high<=H_hat 且 low>=L_hat（区间完全包住次日波动）
        hit = np.mean((te["y_high_ret"].values <= hi_ret) & (te["y_low_ret"].values >= lo_ret))
        hits.append(hit)
        pin_h.append(pinball_loss(te["y_high_ret"].values, hi_ret, high_alpha))
        pin_l.append(pinball_loss(te["y_low_ret"].values, lo_ret, low_alpha))
        mae_h.append(float(np.mean(np.abs(te["y_high_ret"].values - hi_ret))))
        mae_l.append(float(np.mean(np.abs(te["y_low_ret"].values - lo_ret))))
        per_fold.append({"fold": k, "train": tr_end, "test": len(te),
                         "interval_hit": round(float(hit), 3)})

    metrics = {
        "interval_hit_rate": round(float(np.mean(hits)), 4) if hits else None,
        "pinball_high": round(float(np.mean(pin_h)), 5) if pin_h else None,
        "pinball_low": round(float(np.mean(pin_l)), 5) if pin_l else None,
        "mae_high_ret": round(float(np.mean(mae_h)), 5) if mae_h else None,
        "mae_low_ret": round(float(np.mean(mae_l)), 5) if mae_l else None,
        "backend": "lightgbm" if _HAS_LGB else "sklearn",
    }
    return WalkForwardResult(code=code, n_folds=len(per_fold), metrics=metrics, per_fold=per_fold)


def predict_next_day(daily: pd.DataFrame, *, seed: int = 0,
                     high_alpha: float = 0.9, low_alpha: float = 0.1) -> dict:
    """用全历史 fit，对最新交易日预测次日 [L_hat, H_hat]。供推理/报告用。"""
    df = build_features(daily)
    train = df.dropna(subset=FEATURE_COLS + ["y_high_ret", "y_low_ret"])
    last = df.dropna(subset=FEATURE_COLS).iloc[[-1]]  # 最新一行（标签 NaN，用于推理）
    model = IntervalModel(seed=seed, high_alpha=high_alpha, low_alpha=low_alpha).fit(train)
    L, H = model.predict_prices(last)
    close = float(last["close"].iloc[0])
    return {
        "as_of": str(last["date"].iloc[0]),
        "close": round(close, 4),
        "L_hat": round(float(L[0]), 4),
        "H_hat": round(float(H[0]), 4),
        "width_pct": round((float(H[0]) - float(L[0])) / close * 100, 2),
    }


if __name__ == "__main__":
    from . import config as mlcfg
    from . import data as mldata
    for code in mlcfg.TARGETS:
        daily = mldata.load_daily(code)
        lo_a, hi_a = mlcfg.alpha_for(code)  # 与回测/报告同口径（按股自适应分位）
        res = walk_forward_eval(daily, code, high_alpha=hi_a, low_alpha=lo_a)
        m = res.metrics
        print(f"{code}: folds={res.n_folds} α=({lo_a},{hi_a}) 区间命中={m['interval_hit_rate']}  "
              f"pinball(H/L)={m['pinball_high']}/{m['pinball_low']}  "
              f"MAE(H/L)={m['mae_high_ret']}/{m['mae_low_ret']}  [{m['backend']}]")
