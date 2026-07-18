"""借鉴 ② — Purged/Embargo 滚动切分（防泄漏评估地基）。

纯 index 运算，不依赖 LightGBM/sklearn。返回 (train_idx, test_idx) 列表，
供 predictor.walk_forward_eval / backtest 复用同一套切分口径。

为什么需要它（定性见 docs/ML_QLIB_BORROW_PLAN.md §2.1）：本项目是**扩张窗 +
test 恒在 train 之后 + 标签只前看 1 天**，train 末行标签在首个测试决策时点已实现
→ **不存在传统"未来函数"泄漏**。purge 修的是三个更"软"但同样伤可信度的问题：
  1. 边界标签重叠（1 天）——train 末行与 test 首行共享同一天数据；
  2. 序列相关的相似性乐观——test 开头与 train 末尾同波动 regime、高度相似；
  3. （purge 修不了的）调参窥视——需锁箱 holdout，见 Plan §2.4。
purge 掉训练段尾部 purge_w 行即可挤掉 1、2 层乐观，代价仅 purge_w 行样本。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PurgedConfig:
    n_folds: int = 5
    min_train: int = 250         # purge 之后仍需保有的最小训练行数
    feat_lookback: int = 21      # = features.py 最长回看窗（价格空间复合回看，勿填 20）
    label_horizon: int = 1       # = 标签前看天数（次日）
    embargo: int = 0             # 扩张窗下为 no-op；保留字段仅为将来 K-Fold 模式

    @property
    def purge_w(self) -> int:
        """隔离带宽度 = 标签前看 + 特征回看（价格空间复合窗）。"""
        return self.label_horizon + self.feat_lookback


def purged_walk_forward(n: int, cfg: PurgedConfig | None = None) -> list[tuple[list[int], list[int]]]:
    """扩张窗滚动 + purge。返回每折 (train_idx, test_idx)。

    第 k 折：test = [te_start, te_end)；train = [0, te_start - purge_w)。
    purge_w = label_horizon + feat_lookback：剔除与 test 边界"标签重叠 +
    高度相似"的训练尾部（定性见 §2.1）。embargo 在扩张窗下为 no-op。

    n 太小时优雅降折（fold 宽度按可用长度均分），不抛异常。
    """
    cfg = cfg or PurgedConfig()
    purge_w = cfg.purge_w
    splits: list[tuple[list[int], list[int]]] = []
    # v0.2 修 bug：te_start 需平移 purge_w，否则第 0 折 train 恒 < min_train 被丢弃
    # （第 0 折 test 从 min_train+purge_w 起 → purge 后 train 恰为 [0, min_train)）。
    first_te = cfg.min_train + purge_w
    if first_te >= n:
        return splits  # 数据不足以留出任何隔离带 + 最小训练集
    fold = max(20, (n - first_te) // max(1, cfg.n_folds))
    for k in range(cfg.n_folds):
        te_start = first_te + k * fold
        te_end = min(te_start + fold, n)
        if te_start >= n or te_end <= te_start:
            break
        # purge：训练段尾部 purge_w 行（与 test 边界重叠/高度相似），剔除
        tr_end = max(0, te_start - purge_w)
        train = list(range(0, tr_end))
        test = list(range(te_start, te_end))
        # embargo：扩张窗单向滚动下不存在"test 之后的 train"→ no-op（见 §2.1）
        if len(train) >= cfg.min_train:
            splits.append((train, test))
    return splits
