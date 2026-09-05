# ML 升级工程交接

四批工程在 `codex/ml-upgrade-20260904` 分支实施，工作树 `/Users/kk/.codex/worktrees/dc66/myStock`。没有合并 main、推送分支、改生产配置、修改原运行库、重启原 Web、发布公网报告或新增调度。

提交阶段：`c84ac9e` 时间守卫/隔离读/手动管线；`54148b6` 不可覆盖版本/冻结评估；`f701ea7` E0–E5 实验；第四批为本交接文档所在提交。详细证据见[执行日志](ML_UPGRADE_EXECUTION_LOG_2026-09-04.md)和[实验汇总](ML_UPGRADE_EXPERIMENT_RESULTS_2026-09-04.md)。

当前预览：[ML v2](http://127.0.0.1:8896/ml-next)。下一目标日卡片只读有时间证据的 live；当前旧库没有这类有效预测，卡片显示 unavailable 是正确结果。回溯区域默认选择“历史回溯（包含离线重建）”，再点击“填入合成测试场景”可审查已有模型的固定策略闭环。后续已完成 720 条逐日重建及默认模式修复，详见 [历史补齐记录](ML_HISTORY_REFRESH_2026-09-05.md)。该按钮写入浏览器表单，不写任何账户/数据库。20/60/120 为独立初始状态的重模拟；每日明细是该次连续回放的切片。

## 本地复现

以下命令在上述工作树执行，Python 均指向 `/opt/anaconda3/envs/mk/bin/python`。详细数据/模型/订单/截图留在 `data/`，不要提交。

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/mk/bin/python -m pytest tests/ -q -p no:cacheprovider
node --check mystock/web/static/app.js
node --check mystock/web/static/ml_next.js
bash -n scripts/ml.sh
git diff --check

MYSTOCK_EXPERIMENT_DB="$PWD/data/upgrade-input/ml/mystock_ml.db" PYTHONPATH=. /opt/anaconda3/envs/mk/bin/python scripts/ml_experiments/exp_a_baseline.py
/opt/anaconda3/envs/mk/bin/python -m scripts.ml_experiments.upgrade_matrix --db data/upgrade-input/ml/mystock_ml.db --out data/upgrade-output/matrix-rerun
/opt/anaconda3/envs/mk/bin/python -m scripts.ml_experiments.exp_b_touch_economics --db data/upgrade-input/ml/mystock_ml.db --out data/upgrade-output/exp-b-rerun.json
/opt/anaconda3/envs/mk/bin/python -m scripts.ml_experiments.strategy_validation --db data/ml/mystock_ml.db --out data/upgrade-output/strategy-rerun.json

# 若需把新矩阵归档到另一个临时副本，只追加 recomputed，不会升级为 live：
# python -m scripts.ml_experiments.archive_development --db <可写临时副本> --input data/upgrade-input/ml/mystock_ml.db --matrix data/upgrade-output/matrix-rerun

# 当前8896已运行；仅进程退出后手动启动（或另选空闲端口）：
/opt/anaconda3/envs/mk/bin/python -m scripts.ml_preview --db data/mystock.db --ml-db data/ml/mystock_ml.db --port 8896
```

冻结输入哈希在 `data/upgrade-output/input-hashes.json`，原依赖版本 `dependencies.json`；报告、矩阵和逐版本 manifest 在各自私有 runs/输出目录。日历生成工具只在隔离 `data/upgrade-runtime` 中使用 PMC5.1.3 / exchange-calendars4.11.1，不要求修改原共享环境；日历CSV已提交，运行时不需要这些生成依赖。

## API 与规则

- `GET /api/ml/strategy` 继续 schema1 legacy；显式 `mode=inventory` 才采用受约束策略。不能直接比较两个模式的收益分母。
- v2：`latest` 看时间状态；`review` 固定市场session窗口与事实；`compare` 对比三种固定policy。参数缺失/非法为400，缺库/缺schema为503；无数据的session仍返回。
- 所有库存账户参数均显式输入，费用空白为 gross/费用缺失；合成正费用不是券商费用模型。HK lot=100 是测试值，不能推广到全部港股。
- 新预测有 run/manifest/input/代码/依赖/特征/seed/训练校准截止；generated、decision、published 独立，发布实际晚于截止的版本排除默认信号。报告按 run 保存原文，latest 只是兼容副本；全跳过不会覆盖旧 latest。
- ML 不 import Web、不触发在线 API；Web 连接 mode=ro + query_only。旧未知时区时间保守处理，收盘后旧盘中缓存仍 awaiting_final_data。

## 后续生产切换（本次未执行）

1. 审查分支、实验负结果和手动流程；本轮没有模型晋级，也不建议据开发样本直接改生产默认。
2. 在新的部署副本先保存当前运行源码、数据库在线备份、哈希和恢复演练；不得以本次2026-09-04备份覆盖后来新增交易事实。
3. 明确每股真实历史lot/tick/费用、币种账户初始状态、订单量、库存上限、最大持有以及分红付款信息；更新日历临时停市事件与2027覆盖。
4. 仅在部署副本演练 `init_ml_db` + `versions.migrate_legacy`、采集最终确认数据、生成版本和独立端口Web验证。确认报告时区/输入/目标日/截止；人工逐日积累60个真实session shadow。
5. 用户批准切换时再按正常Git审查流程合并/部署，停旧Web并启动新服务；此操作本次没有执行。不要将历史recomputed视为shadow。

回滚：切回切换前代码与配置，把新服务端口撤下、恢复旧服务；如新schema/版本库有兼容问题，使用**切换当时**的ML库备份恢复到新的路径后验证，不直接覆盖持续写入的原交易库。保留新增版本、发布回执与失败日志供审计；版本内容不可覆盖或删除。原运行目录与2026-09-04私有备份目前均保留。

限制：静态日历至2026年底，临时天气中断仍需维护；小时OHLC有路径/排队歧义，使用显式保守次序；真实费用/账户参数未确认；分红付款日期不明只计应收；缺行情的期末估值可能使用标记stale的最后确认价；无独立holdout、五种子晋级复核或真实60-session shadow。E6–E8、RL/TFT/HMM与外部特征未开展。
