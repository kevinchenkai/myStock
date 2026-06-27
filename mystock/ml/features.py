"""P2 特征工程（纯函数，可单测）。

严防未来函数：所有特征只用到截至 T 日收盘可得的信息，预测 T+1 的 high/low。
收益率/技术指标用 adj_close（避免分红/拆股假跳空，DATA.md §4）。

标签（next-day，需对齐 T+1 实际行情）：
  - y_high_ret = high(T+1)/close(T) - 1
  - y_low_ret  = low(T+1)/close(T)  - 1
预测时把比例还原成价位：H_hat = close(T) * (1 + yhat_high)，L_hat 同理。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_features(daily: pd.DataFrame) -> pd.DataFrame:
    """输入 load_daily 的日线（date 升序），输出带特征 + 标签的 DataFrame。

    最后一行（最新交易日）标签为 NaN（次日未知），用于推理；训练时 dropna。
    """
    df = daily.copy().reset_index(drop=True)
    adj = df["adj_close"]
    # 用 adj_close 的比例把原始 OHLC 调成可比口径
    ratio = df["adj_close"] / df["close"]
    adj_high = df["high"] * ratio
    adj_low = df["low"] * ratio
    adj_open = df["open"] * ratio

    # fill_method=None：不前向填充缺口（日线连续无内部 NA，行为不变）；
    # 显式指定以消除 pandas 弃用告警（默认 'pad' 将被移除）。
    ret1 = adj.pct_change(fill_method=None)
    df["ret_1d"] = ret1
    df["ret_5d"] = adj.pct_change(5, fill_method=None)
    df["ret_10d"] = adj.pct_change(10, fill_method=None)
    df["vol_5d"] = ret1.rolling(5).std()
    df["vol_20d"] = ret1.rolling(20).std()

    # 真实波幅 / ATR（用 adj 化的 high/low/前收）
    prev_close = adj.shift(1)
    tr = pd.concat([
        adj_high - adj_low,
        (adj_high - prev_close).abs(),
        (adj_low - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean() / adj  # 归一化为相对收盘的比例

    # 均线偏离
    for w in (5, 10, 20):
        df[f"ma{w}_dev"] = adj / adj.rolling(w).mean() - 1

    # 当日 OHLC 内部结构（无未来）
    rng = (adj_high - adj_low).replace(0, np.nan)
    df["close_pos_in_range"] = (adj - adj_low) / rng     # 收盘在当日区间的位置
    df["day_range_rel"] = rng / adj                       # 当日振幅 / 收盘
    df["gap"] = adj_open / prev_close - 1                  # 跳空

    # 近 N 日相对高低（动量/突破上下文）
    df["dist_hi_20"] = adj / adj_high.rolling(20).max() - 1
    df["dist_lo_20"] = adj / adj_low.rolling(20).min() - 1

    # 量比
    df["vol_ratio_5"] = df["volume"] / df["volume"].rolling(5).mean()
    df["vol_ratio_20"] = df["volume"] / df["volume"].rolling(20).mean()

    # ---- 标签（next-day，相对今日原始 close 的比例）----
    next_high = df["high"].shift(-1)
    next_low = df["low"].shift(-1)
    df["y_high_ret"] = next_high / df["close"] - 1
    df["y_low_ret"] = next_low / df["close"] - 1

    return df


FEATURE_COLS = [
    "ret_1d", "ret_5d", "ret_10d", "vol_5d", "vol_20d", "atr_14",
    "ma5_dev", "ma10_dev", "ma20_dev",
    "close_pos_in_range", "day_range_rel", "gap",
    "dist_hi_20", "dist_lo_20", "vol_ratio_5", "vol_ratio_20",
]
LABEL_COLS = ["y_high_ret", "y_low_ret"]
