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
from . import calibrator as calib
from . import signal_eval as sig
from .cv import PurgedConfig, purged_walk_forward


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
    """一支标的的区间模型：high 取上分位、low 取下分位。

    可选 CQR 校准（建议 2）：fit 时从训练末尾切 cal_frac 做校准集，算出 ret 空间
    的 conformal 半宽 q，predict 时把区间扩展为 [lo_ret - q, hi_ret + q]。q>0 扩展
    （base 偏窄）、q<0 收紧（base 偏宽）——自适应到 target_coverage，替代手调 α。
    conformal=False 时 q=0，行为与原版完全一致（向后兼容）。
    """
    seed: int = 0
    high_alpha: float = 0.9
    low_alpha: float = 0.1
    m_high: object = None
    m_low: object = None
    conformal: bool = False
    target_coverage: float = 0.8
    cal_frac: float = 0.25
    label_horizon: int = 1   # 借鉴②：fit 段与校准段之间的隔离行数（标签前看=1）
    q: float = 0.0   # ret 空间的 conformal 半宽（fit 后置）

    def fit(self, df: pd.DataFrame):
        fit_df = df
        if self.conformal and self.cal_frac > 0 and len(df) >= 20:
            n_cal = max(5, int(len(df) * self.cal_frac))
            # 借鉴②（Plan §2.3）：fit_df 末行标签与 cal_df 首行共享同一天数据
            # → 留 label_horizon(=1) 行隔离即可。勿用 purge_w(22)：那会烧掉 22 行
            # 校准样本，且校准集应尽量贴近 test（conformal 可交换性）。
            gap = max(0, self.label_horizon)
            fit_df = df.iloc[:-(n_cal + gap)] if gap else df.iloc[:-n_cal]
            cal_df = df.iloc[-n_cal:]
        # 用纯 numpy（无列名）fit，预测端也用 numpy → 一致，消除 sklearn/lightgbm
        # "X does not have valid feature names" 警告。
        X = fit_df[FEATURE_COLS].to_numpy()
        self.m_high = _fit_quantile(X, fit_df["y_high_ret"].to_numpy(), self.high_alpha, self.seed)
        self.m_low = _fit_quantile(X, fit_df["y_low_ret"].to_numpy(), self.low_alpha, self.seed)
        if self.conformal and self.cal_frac > 0 and len(df) >= 20:
            lo_cal, hi_cal = self._predict_ret_raw(cal_df)
            self.q = calib.calibrate(
                cal_df["y_low_ret"].to_numpy(), lo_cal,
                cal_df["y_high_ret"].to_numpy(), hi_cal,
                self.target_coverage,
            )
            if not np.isfinite(self.q):
                self.q = 0.0
        else:
            self.q = 0.0
        return self

    def _predict_ret_raw(self, df: pd.DataFrame):
        X = df[FEATURE_COLS].to_numpy()
        return _predict_silent(self.m_low, X), _predict_silent(self.m_high, X)

    def predict_ret(self, df: pd.DataFrame):
        """返回 (lo_ret, hi_ret)，已按 q 做 conformal 扩展。"""
        lo_ret, hi_ret = self._predict_ret_raw(df)
        return lo_ret - self.q, hi_ret + self.q

    def predict_prices(self, df: pd.DataFrame):
        """返回 (L_hat, H_hat) 价位，相对各行 close 还原（含 conformal 扩展）。"""
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
    conformal: bool = False, target_coverage: float = 0.8, cal_frac: float = 0.25,
    purged: bool = True, ic_window: int = 60,
) -> WalkForwardResult:
    """按时间滚动评估。返回区间命中率 / pinball / MAE / 信号 IC 的汇总。

    conformal=True 时启用 CQR 校准：每折从训练末尾切 cal_frac 做校准集，报告
    校准后命中率（interval_hit_rate）与原始命中率（interval_hit_rate_raw）+ 平均
    区间宽（width_pct_raw/cal，ret 空间 %），供"覆盖率≥目标且宽度≤+15%"验收门槛对照。

    借鉴②（Plan §2）：purged=True（默认）用 cv.purged_walk_forward 切分——训练段
    尾部砍 purge_w(=22) 行隔离带，挤掉"边界标签重叠 + 相似性乐观"。purged=False
    保留旧的紧贴切分，供 A/B 对照（预期两组差异微小，见 Plan §2.4）。

    借鉴③（Plan §3）：每折算信号 IC（width_ic 主 / mid_ic 次），汇总进 metrics。
    """
    df = build_features(daily).dropna(subset=FEATURE_COLS + ["y_high_ret", "y_low_ret"])
    df = df.reset_index(drop=True)
    n = len(df)

    if purged:
        # 借鉴②：防泄漏切分（训练段尾部 purge_w 行隔离带）
        splits = purged_walk_forward(
            n, PurgedConfig(n_folds=n_folds, min_train=min_train))
        # splits 已是 (train_idx, test_idx) 列表；空则降级为空评估
        split_pairs = [(df.iloc[tr], df.iloc[te]) for tr, te in splits]
    else:
        # 旧行为：紧贴滚动（无隔离带），仅供 A/B 对照
        if n < min_train + n_folds * 20:
            n_folds = max(1, (n - min_train) // 30)
        fold_size = max(20, (n - min_train) // max(1, n_folds))
        split_pairs = []
        for k in range(n_folds):
            tr_end = min_train + k * fold_size
            te_end = min(tr_end + fold_size, n)
            if tr_end >= n or te_end <= tr_end:
                break
            split_pairs.append((df.iloc[:tr_end], df.iloc[tr_end:te_end]))

    hits, pin_h, pin_l, mae_h, mae_l = [], [], [], [], []
    hits_raw, width_pct_raw, width_pct_cal = [], [], []
    ic_width, ic_mid = [], []
    per_fold = []

    for k, (tr, te) in enumerate(split_pairs):
        model = IntervalModel(
            seed=seed, high_alpha=high_alpha, low_alpha=low_alpha,
            conformal=conformal, target_coverage=target_coverage, cal_frac=cal_frac,
        ).fit(tr)
        lo_ret, hi_ret = model.predict_ret(te)
        lo_raw, hi_raw = lo_ret + model.q, hi_ret - model.q  # 还原 base QR（去掉 q）

        # 区间命中：真实次日 high<=H_hat 且 low>=L_hat（区间完全包住次日波动）
        y_hi = te["y_high_ret"].values
        y_lo = te["y_low_ret"].values
        hit = float(np.mean((y_hi <= hi_ret) & (y_lo >= lo_ret)))
        hit_raw = float(np.mean((y_hi <= hi_raw) & (y_lo >= lo_raw)))
        hits.append(hit); hits_raw.append(hit_raw)
        width_pct_raw.append(float(np.mean(hi_raw - lo_raw)) * 100)
        width_pct_cal.append(float(np.mean(hi_ret - lo_ret)) * 100)
        pin_h.append(pinball_loss(y_hi, hi_ret, high_alpha))
        pin_l.append(pinball_loss(y_lo, lo_ret, low_alpha))
        mae_h.append(float(np.mean(np.abs(y_hi - hi_ret))))
        mae_l.append(float(np.mean(np.abs(y_lo - lo_ret))))
        # 借鉴③：折内信号 IC（宽度主 / 中点次）
        srep = sig.signal_report(lo_ret, hi_ret, y_lo, y_hi, window=ic_window)
        if srep["width_ic"] is not None:
            ic_width.append(srep["width_ic"])
        if srep["mid_ic"] is not None:
            ic_mid.append(srep["mid_ic"])
        per_fold.append({"fold": k, "train": len(tr), "test": len(te),
                         "interval_hit": round(hit, 3),
                         "interval_hit_raw": round(hit_raw, 3),
                         "width_ic": srep["width_ic"], "mid_ic": srep["mid_ic"],
                         "q_ret": round(float(model.q), 5)})

    metrics = {
        "interval_hit_rate": round(float(np.mean(hits)), 4) if hits else None,
        "interval_hit_rate_raw": round(float(np.mean(hits_raw)), 4) if hits_raw else None,
        "width_pct_raw": round(float(np.mean(width_pct_raw)), 3) if width_pct_raw else None,
        "width_pct_cal": round(float(np.mean(width_pct_cal)), 3) if width_pct_cal else None,
        "pinball_high": round(float(np.mean(pin_h)), 5) if pin_h else None,
        "pinball_low": round(float(np.mean(pin_l)), 5) if pin_l else None,
        "mae_high_ret": round(float(np.mean(mae_h)), 5) if mae_h else None,
        "mae_low_ret": round(float(np.mean(mae_l)), 5) if mae_l else None,
        # 借鉴③：信号层（跨折均值）。width_ic 是主指标（分位模型的忠实度量）。
        "width_ic": round(float(np.mean(ic_width)), 4) if ic_width else None,
        "mid_ic": round(float(np.mean(ic_mid)), 4) if ic_mid else None,
        "backend": "lightgbm" if _HAS_LGB else "sklearn",
        "conformal": bool(conformal),
        "purged": bool(purged),
        "target_coverage": float(target_coverage) if conformal else None,
    }
    return WalkForwardResult(code=code, n_folds=len(per_fold), metrics=metrics, per_fold=per_fold)


def predict_next_day(daily: pd.DataFrame, *, seed: int = 0,
                     high_alpha: float = 0.9, low_alpha: float = 0.1,
                     conformal: bool = False, target_coverage: float = 0.8,
                     cal_frac: float = 0.25) -> dict:
    """用全历史 fit，对最新交易日预测次日 [L_hat, H_hat]。供推理/报告用。

    conformal=True 时启用 CQR（从训练末尾切 cal_frac 校准，ret 空间半宽 q 应用到次日）。
    """
    df = build_features(daily)
    train = df.dropna(subset=FEATURE_COLS + ["y_high_ret", "y_low_ret"])
    last = df.dropna(subset=FEATURE_COLS).iloc[[-1]]  # 最新一行（标签 NaN，用于推理）
    model = IntervalModel(
        seed=seed, high_alpha=high_alpha, low_alpha=low_alpha,
        conformal=conformal, target_coverage=target_coverage, cal_frac=cal_frac,
    ).fit(train)
    L, H = model.predict_prices(last)
    close = float(last["close"].iloc[0])
    return {
        "as_of": str(last["date"].iloc[0]),
        "close": round(close, 4),
        "L_hat": round(float(L[0]), 4),
        "H_hat": round(float(H[0]), 4),
        "width_pct": round((float(H[0]) - float(L[0])) / close * 100, 2),
        "conformal": bool(conformal),
        "q_ret": round(float(model.q), 5),
        "target_coverage": float(target_coverage) if conformal else None,
    }


if __name__ == "__main__":
    from . import config as mlcfg
    from . import data as mldata
    # 第一档建议 2 默认开：CQR 校准对照（MYSTOCK_ML_CQR=0 可关）；目标覆盖率按股自适应
    import os
    do_cqr = os.environ.get("MYSTOCK_ML_CQR", "1") != "0"
    for code in mlcfg.TARGETS:
        daily = mldata.load_daily(code)
        lo_a, hi_a = mlcfg.alpha_for(code)  # 与回测/报告同口径（按股自适应分位）
        target = mlcfg.coverage_for(code)
        res = walk_forward_eval(daily, code, high_alpha=hi_a, low_alpha=lo_a,
                                conformal=do_cqr, target_coverage=target)
        m = res.metrics
        purged_tag = "purged" if m.get("purged") else "紧贴"
        line = (f"{code}: folds={res.n_folds}({purged_tag}) α=({lo_a},{hi_a}) 区间命中={m['interval_hit_rate']}  "
                f"信号 width_IC={m.get('width_ic')} mid_IC={m.get('mid_ic')}  "
                f"pinball(H/L)={m['pinball_high']}/{m['pinball_low']}  [{m['backend']}]")
        if do_cqr and m.get("interval_hit_rate_raw") is not None:
            line += (f"\n   CQR(目标{target:.0%}): 命中 {m['interval_hit_rate_raw']}→"
                     f"{m['interval_hit_rate']}  宽 {m['width_pct_raw']}→{m['width_pct_cal']}%")
        print(line)
