# ML 升级调研用实验脚本（只读，非生产管线）

与 [`docs/ML_UPGRADE_PLAN.md`](../../docs/ML_UPGRADE_PLAN.md) §3.1 / §3.3 及
[`docs/ML_CLAUDE_UPGRADE_MERGED.md`](../../docs/ML_CLAUDE_UPGRADE_MERGED.md) 对应的原始脚本，
原样入库供复现（Codex v1.3 §2.4 的复现前提）。两者都只读 `data/ml/mystock_ml.db`，
不写库、不抓取、不改模型；数字随库内增量数据微动，2026-09-04 快照的结果已固化在上述文档。

```bash
conda activate mk
PYTHONPATH=. python scripts/ml_experiments/exp_a_baseline.py          # 实验 A：模型 vs 朴素波动率基线 vs 加特征（约 45 s）
PYTHONPATH=. python scripts/ml_experiments/exp_b_touch_economics.py   # 实验 B：留档预测的触价经济性与多日轮回（约 5 s）
```

- 实验 A 切分：`cv.purged_walk_forward(n_folds=4, min_train=250)`；分位按 `config.alpha_for`；CQR 关。
  `naive_vol` = 训练集 `quantile(y / vol_20d, α) × 测试日 vol_20d`。`lgb_extra_x` 同时改了特征与容量，
  是探索性组合而非单机制消融（Codex 已指出，第三批按 E1→E5 拆开）。
- 实验 B 的「多日轮回」= 买成后逐日用当日留档 Ĥ 挂卖，最多 20 个交易日；不含费用、不设库存上限。
- 注意：实验 B 里 `mldb.get_ml_connection` 是可写连接（沿用现网 `strategy.py` 的口径）；脚本本身不执行任何写操作。
  第一批交付后应改为只读连接。
