# ML 实验与历史修复工具

这些工具不属于每日生产管线。研究输入应使用已冻结的 ML 数据库副本；产物放在忽略目录 `data/upgrade-output/`，不提交真实账户数据。模型实验与历史重建都不能追认当时的 live 预测，也不能作为前向 shadow。

## 只读研究入口

在 `mk` 环境、仓库根目录执行，把占位路径替换为实际冻结副本：

```bash
conda activate mk
export MYSTOCK_EXPERIMENT_DB=/absolute/path/to/frozen-input.db
mkdir -p data/upgrade-output/research
python -m scripts.ml_experiments.exp_a_baseline
python -m scripts.ml_experiments.exp_b_touch_economics --db "$MYSTOCK_EXPERIMENT_DB" --out data/upgrade-output/research/touch.json
python -m scripts.ml_experiments.upgrade_matrix --db "$MYSTOCK_EXPERIMENT_DB" --out data/upgrade-output/research/matrix --seeds 0,1,2
python -m scripts.ml_experiments.strategy_validation --db "$MYSTOCK_EXPERIMENT_DB" --out data/upgrade-output/research/strategy.json
```

- A 必须设置 `MYSTOCK_EXPERIMENT_DB`，只读输入，结果打印到终端。purged walk-forward、CQR 关；`naive_vol` 是训练集的收益/波动率分位乘测试日波动率。原 `lgb_extra_x` 同时变更容量和特征，是探索性组合；独立消融用 `upgrade_matrix`。
- B 现在要求 `--db`、`--out`；只读连接。统计旧版留档触价后的 1/5 session 条件事件收益，重叠事件不是账户收益；成熟性截止固定为 2026-09-05T06:00Z，未成熟项保留 pending。已不执行旧版最多 20 日轮回逻辑。
- `upgrade_matrix` 写实验文件但不改输入库；E0–E5 结果为负，不支持模型晋级。
- `model_matrix`（2026-09-06）：预注册的学习器／尺度矩阵（冻结 LightGBM、naive_vol、EWMA／GK／GARCH 尺度、线性分位、LightGBM／CatBoost／XGBoost 小网格），`--db --out [--only --codes --seed]`；指定后端缺失时直接失败，不回退。首轮结果为负，见 [模型矩阵回执](../../docs/records/ml-model-matrix_claude_20260906.md)。
- `fetch_external`（2026-09-06）：D1 独立采集入口，`--db` 必填，只写 `ml_external_1d` 与 `ml_sync_log`；未接入 ml.sh。
- `fetch_preopen`（2026-09-06）：美股盘前报价入 `ml_preopen_quotes`，`--db --history`（yfinance 盘前小时线 730 天）或 `--futu`（实盘快照，需 OpenD）；未接入 ml.sh。
- `overnight_d4`（2026-09-06）：预注册正式实验，`--db --out [--seeds] [--market HK|US]`，O2 对 B0、五种子、两窗口，小米另跑 KWEB 代理；美股结果见 [美股 D4 回执](../../docs/records/ml-preopen-us-d4_claude_20260906.md)；结果见 [D4 回执](../../docs/records/ml-overnight-d4_claude_20260906.md)。`fetch_external --alt` 抓预注册的对照代理。
- `overnight_feasibility`（2026-09-06）：港股隔夜特征探针，`--db --out [--shift]`，读 `ml_external_1d` 并用 `features.attach_overnight` 拼接；`--shift` 为泄漏检验；结果见 [D1–D3 回执](../../docs/records/ml-overnight-d1d3_claude_20260906.md)。
- `strategy_validation` 使用固定历史窗口和合成账户；HK lot=100 是 fixture 参数，不能当作所有港股的真实交易单位。

## 显式写入／补采工具

| 工具 | 行为与参数 |
| --- | --- |
| `archive_development` | `--db` 指定可写副本、`--input` 冻结输入、`--matrix` 实验矩阵；追加 recomputed 版本，不能写运行库或覆盖 live |
| `rebuild_history audit` | `--db --out --end`；只读审计 20/60/120 session 的日线、小时线和预测覆盖 |
| `rebuild_history repair` | 同上；联网补采到显式副本，保存修复证据 |
| `rebuild_history rebuild` | 同上；逐日截断／拟合后追加历史预测和 manifest，不生成新 live |
| `import_futu_hourly` | `--db --csv --code --date --receipt`；验证富途小时桶、日线及重叠数据后导入显式副本，并保留来源证据 |
| `freeze_calendar` | `--end 2027-12-31`；改写仓库内两份 CSV，需 PMC 5.1.3 / exchange-calendars 4.11.1 隔离工具环境；更新年份先核验交易所公告并同步常量和测试 |

回溯重建只进不可覆盖版本表，旧版投影仅新增经时间验证的 live。已有旧版重建数据继续保留并标注来源。新的 gap 检查同时查询有效版本表，避免因重建不再写旧表而重复计算。

完整执行证据见 [历史修复记录](../../docs/records/ml-history-refresh_codex_20260905.md)、[升级实验结果](../../docs/records/ml-upgrade-experiment-results_codex_20260904.md)及 [Claude 修复回执](../../docs/records/ml-upgrade-claude-review-fixes_codex_20260905.md)。


### 文档治理后的汇总输出

`summarize_upgrade.py` 默认写 `data/upgrade-output/experiment-summary.md` 私有草稿；`--out` 可明确指定输出路径。已有实验回执是已审查记录，脚本不再默认覆盖它。新结果审阅后按 [文档治理规范](../../docs/GOVERNANCE.md) 命名、登记到清单和索引；文件迁移不改变原实验协议。
