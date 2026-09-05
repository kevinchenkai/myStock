# ML 升级工程交接

> 工单起始日：2026-09-04；本交接更新：2026-09-05，核对基线 `a460d38` / `main`，本轮仅同步文档。工程已合入 main、推送并部署，详见 [部署回执](ML_DEPLOYMENT_2026-09-05.md)。当前入口为 [8888 ML 页面](http://127.0.0.1:8888/ml-next/)；原隔离工作树已移除，8896 是历史预览地址。

## 已交付与未完成的目标

Codex Astra 在隔离分支完成四批工程，经 Claude 独立审查和合并前修复后，以 `99848c9` 合入 main（含修复 `7d95fc7`）。Web 已从 main 重启；生产 ML 库已迁移、更新行情、追加 720 条历史重建版本、生成并公开发布六股 live 报告。过程保存部署前备份、输入与发布证据，生产账户事实未被该部署流程改写。

工程包含 session 守卫、不可覆盖版本、E0–E5 实验、受约束库存回放及只读 `/ml-next`。**没有候选达到模型晋级门槛**，没有独立 holdout／五种子晋级复核／真实 60-session 前向 shadow；不把历史重建算作 live。

原四批实施证据见 [执行日志](ML_UPGRADE_EXECUTION_LOG_2026-09-04.md)，模型结果见 [实验汇总](ML_UPGRADE_EXPERIMENT_RESULTS_2026-09-04.md)，生产验收见 [部署回执](ML_DEPLOYMENT_2026-09-05.md)。这些记录中的“未部署”描述属于各自阶段。

## 接手阅读与页面检查

先读 [协作约定](COLLABORATION.md) 与 [未尽事项](OPEN_ITEMS.md)，再读 [当前运行概览](ML_OVERVIEW.md)、[数据字典](DATA.md)、[项目接手指南](项目接手指南.html)，再读 [Claude 修复与剩余待办](ML_UPGRADE_CLAUDE_FIXES_2026-09-05.md)。

1. 确认 checkout、提交、配置和数据库路径，打开现有 8888 服务。已有运行库不需要重新执行 `init.sh`。
2. 下一目标日卡片只取有效 live，核对目标日、状态及发布时间。卡片 expired／unavailable 不等于历史版本丢失。
3. 历史回溯默认包含离线重建。检查 20／60／120 session 的缺口及来源；选中日期查看真实订单快照。
4. 填入明确场景后比较三种固定策略。每个窗口独立初始化；合成账户只用于验证，不代表真实资金／费用／证券规则。

## 人工运行

命令在当前项目根目录执行，Python 使用 conda `mk`。既有数据查看无需刷新；首次安装流程见根 README。

```bash
# 仅在需要更新生产账户事实时：
bash scripts/update.sh
# ML 单独采集并确认收盘缓存：
bash scripts/ml.sh data
# 生成预测，保留终端打印的 Train receipt 路径：
bash scripts/ml.sh train
# 确定需要公开发布后，使用该路径，不自动寻找旧报告：
bash scripts/ml.sh publish <Train-receipt路径>
```

`all` 会公开发布；当前全部人工触发，无自动调度。`data` 写 ML 库，读取生产快照；Web 只读两库。`train` 冻结输入并追加版本，`publish` 校验哈希／目标截止，只覆盖公网静态首页。任一标的采集失败仍会阻断 all，不能把旧 latest 当作本轮成功。

## 排错与恢复

| 现象 | 处理 |
| --- | --- |
| 盘中跳过／awaiting_final_data | 核对市场时区、收盘确认和 synced_at；收盘后先 data 再 train，不能改时间戳绕过守卫 |
| feature_gap | 检查所需 as_of、连续 session 与特征缺口；先审计数据，修复有来源证据的行情后再生成新 run |
| 日历告警／越界 | 当前覆盖至 2027 年末；维护临时停市和后续年份，按日历说明验证后更新，不能用工作日近似替代 |
| 历史缺预测 | 核对 source 模式；recomputed 模式可用且 live 模式缺失是合法区别。补齐流程见历史记录 |
| API 400／503 | 400 检查场景／代码／日期；503 检查实际库路径和 schema，迁移先在备份副本演练，Web 不负责建库 |
| 发布拒绝 | 核对本次回执和产物哈希；过期或全跳过不应重复发布旧产物 |

升级前备份覆盖 Web／ML／完整 data／配置／Git，见 [备份记录](ML_PRE_UPGRADE_BACKUP_2026-09-04.md)。最近一次部署前备份位于忽略目录 `data/deployments/20260905-ml-upgrade/`，保存源码、配置和两库在线备份；恢复应使用与目标切换时间一致的备份，不用 9 月 4 日整库覆盖后来新增交易事实。

回滚时保留新版本、冻结输入、回执与失败日志；在新路径恢复对应备份并检查 SQLite 完整性与旧代码兼容，再切服务配置。代码回滚不会自动回滚数据库或公网 index.html；公网旧首页备份和发布证据见部署目录。不要直接删除不可覆盖版本、整库覆盖生产账户事实，或将旧报告重新包装为当前有效预测。

## 验证与离线复现

合并前修复记录为 **227 passed**。少数既有测试读取本地库并拟合模型，完整测试和实验应在新建的隔离代码／数据副本执行；原 dc66 工作树不再是可用复现目录。

```bash
# 在已准备的隔离副本根目录：
conda activate mk
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/ -q -p no:cacheprovider
node --check mystock/web/static/app.js
node --check mystock/web/static/ml_next.js
bash -n scripts/ml.sh
git diff --check

# 输入路径需指向已备份的隔离 ML 数据库：
python -m scripts.ml_experiments.upgrade_matrix --db data/upgrade-input/ml/mystock_ml.db --out data/upgrade-output/matrix-rerun
python -m scripts.ml_experiments.strategy_validation --db data/upgrade-input/ml/mystock_ml.db --out data/upgrade-output/strategy-rerun.json
# 可另选空闲端口查看副本，不占用生产 8888：
python -m scripts.ml_preview --db data/mystock.db --ml-db data/upgrade-input/ml/mystock_ml.db --port 8896
```

历史审计／修复／逐日重建的命令与来源要求见 [历史补齐记录](ML_HISTORY_REFRESH_2026-09-05.md)，实验参数见 [实验工具说明](../scripts/ml_experiments/README.md)。audit 仅读；repair 联网写指定 ML 库；rebuild 读取冻结输入、追加 recomputed。不要为验证文档而执行采集、拟合、迁移或发布。

## API 与后续维护边界

`/api/ml/v2/latest` 看时间状态；`review` 看固定 session 和缺口，`selected=日期` 附加订单事实；`compare` 比较三种固定 policy。没有独立 facts 路由。默认 API source 为 live，页面历史区显式允许重建。legacy `/api/ml/strategy` 保留 schema 1；显式 `mode=inventory` 才切受约束回放，收益分母不可混用。

所有账户参数显式输入，费用空白为 gross；HK lot=100 只是测试值。日历已延长到 2027，证券规则历史／页面接入、快照保留策略、采集部分失败协议及报告改造尚未完成，详见 [剩余待办](ML_UPGRADE_CLAUDE_FIXES_2026-09-05.md)。小时路径歧义、未知分红付款日、stale 估值仍须在结果中保留。E6–E8、外部特征和 RL／TFT／HMM 未开展。
