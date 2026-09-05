# ML 升级：Claude 审查与 main 上线流程

## 当前建议

本分支是待独立审查的工程候选，尚未合并 main 或部署。209 项测试通过不能替代独立审查与部署演练。审查阻塞项修复、复测及迁移演练通过后，可以合入工程改动，保留现有模型与 legacy 默认入口，另以 `/ml-next` 提供 v2 预览。

本轮 E0–E5 开发样本没有支持模型晋级。工程合并与模型晋级是两个决定；新模型/策略改为生产默认仍需要独立 holdout、晋级复核及真实前向 60-session shadow，不能把历史 recomputed 当成前向成绩。

后续历史补齐已在隔离工作树完成：六股近 120 session 行情完整，并新增 720 条逐日重建预测，详见 [2026-09-05 执行记录](ML_HISTORY_REFRESH_2026-09-05.md)。这些私有数据尚未迁移到原运行库；审查需包含逐日拟合、港股来源转换、日历修正和来源字段迁移。

## 给 Claude Code 的任务

在本工作树启动 Claude Code，把下面这段作为审查任务即可；不需要先合并 main、部署或上传私有数据库。

> 请对当前 `codex/ml-upgrade-20260904` 分支做独立 code review，先记录当前 HEAD SHA，完整审查 `446e657..HEAD` 的代码、测试和文档；不要只看最后一笔样式提交。先阅读 CLAUDE.md、docs/ML_UPGRADE_WORK_ORDER_2026-09-04.md、docs/ML_UPGRADE_EXECUTION_LOG_2026-09-04.md、docs/ML_UPGRADE_EXPERIMENT_RESULTS_2026-09-04.md、docs/ML_UPGRADE_HANDOFF_2026-09-04.md、docs/ML_HISTORY_REFRESH_2026-09-05.md 和本文件。报告结论只是待核实证据，不要默认接受。
>
> 请重点检查：时间泄漏与市场日历/DST/半日市/HK 午休及 CAS；盘中缓存收盘后是否被误当最终数据；生成/决策/发布截止与不可覆盖版本；legacy 审计迁移的幂等性与兼容读取；实验 OOF、校准、共同样本、负结果和选择偏差；库存现金预留、到期退出、lot/tick、费用、公司行动与同 bar 歧义；Web 只读库、缺 ML 库/依赖时主站可用性、缓存隔离和失效、并发请求与错误状态；共享主题与原首页回归。确认离线重建不能混成 live 或 shadow，ML 不反向 import Web。
>
> 这是审查任务，先不要改代码。禁止修改原运行目录、原数据库和配置；禁止执行 update.sh、ml.sh data/train/publish/all、init.sh、外部采集、scp/ssh、服务切换、Git 合并/推送及交易操作。测试仅使用临时合成数据库；如确需本工作树私有副本，只读且不要将持仓、订单、配置、完整数据或截图内容带入提交/报告。不要读取配置密钥。
>
> 输出按 P0/P1/P2/P3 排序的具体发现，每项提供文件与行号、触发条件/复现方法、实际影响、最小修复建议和验证办法。分别给出“是否可以合入 main”“是否可以部署工程”“是否可以晋级模型”的判断。未发现阻塞问题时，也要列出审查覆盖范围、实际运行的验证和剩余风险。main 当前另有 README 文档更新 `885e8f7`，合并时应保留；不要把它误认为本分支删除文档。

可供审查者执行的命令（在此工作树，不切分支）：

```bash
git rev-parse HEAD
git diff --stat 446e657..HEAD
git diff 446e657..HEAD -- mystock scripts tests docs
git diff 446e657..main -- README.md
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/mk/bin/python -m pytest tests/ -q -p no:cacheprovider
node --check mystock/web/static/app.js
node --check mystock/web/static/ml_next.js
node --check mystock/web/static/theme.js
bash -n scripts/ml.sh
git diff --check 446e657..HEAD
```

本地路径：`/Users/kk/.codex/worktrees/dc66/myStock`。若 Claude 只能通过远端仓库审查，另行推送此功能分支并创建 PR，提交同一审查说明即可；Git 不包含被忽略的 `data/` 和配置，因此远端只能复现合成测试，不能声称已重跑私有数据实验。本次未推送、未创建 PR、未向 Claude 发送消息。

## 审查通过后的部署次序（本次未执行）

1. 修复审查阻塞项并复测。合并前检查 main 最新进展，保留其 README 更新；不要用旧分支的文件整体覆盖 main。
2. 在切换当时做源码/配置和 SQLite 在线备份，并在隔离部署副本验证恢复。2026-09-04 的历史备份不能覆盖之后的新交易事实。
3. 用隔离部署副本演练主库 schema 初始化与 ML 版本迁移，旧记录只能作为 `audit_unknown_timing`；验证旧入口及 v2 的缺数据处理。连接必须显式关闭。以下在**已确认的部署副本根目录**、已激活 `mk` 环境执行：

```bash
python - <<'PY'
from mystock import db as primary_db
from mystock.ml import db as ml_db, versions
primary_db.init_db()  # 仅初始化/迁移 schema，不采集数据
ml_db.init_ml_db()
conn = ml_db.get_ml_connection()
try:
    with conn:
        versions.migrate_legacy(conn)
finally:
    conn.close()
PY
```

部署副本必须确认配置解析出的主库/ML 库路径均指向副本，再执行以上代码。不能只凭当前目录推定配置安全。正式切换时重复已验证的迁移步骤，并重启 Web 以加载新后端和模板。

4. 若需要最新账户事实和预测，在获准的运行目录按以下顺序手动执行：

```bash
bash scripts/update.sh
bash scripts/ml.sh data
bash scripts/ml.sh train
```

`update.sh` 需要 OpenD 已登录，更新主库持仓/委托/成交/证券资料等；它不负责 ML 训练。`ml.sh data` 更新 ML 行情并快照主库事实；`ml.sh train` 生成满足时间守卫的新预测版本及报告，包含原报告回溯。若只是发布页面样式且数据已新鲜，不必因此重跑采集/训练。

生成应在所需市场收盘数据最终确认后、下一目标 session 决策截止前；盘中/缺最终数据/过截止时跳过是正确行为。HK 截止为当地 09:00，不能用所有市场一个固定本地时刻推定可运行。`ml.sh` 无参数只显示帮助。`publish`/`all` 会触发公网发布，不是本地 Web 更新的必要步骤。

5. v2 的 20/60/120 库存回放在 Web 请求时只读计算，不需要每次更新后再跑一个持久化回溯任务。`train` 也不会自动补齐 120 日有效历史版本。首次迁移后 live 为空是可能的：旧审计记录不能伪装成有效预测。
6. 若要填充历史研究视图，另行用冻结输入跑 `upgrade_matrix`，再用 `archive_development` 追加 recomputed（命令见交接文档），页面需显式选择“包含离线历史重建”。这不是上线必做项，不改变生产模型，也不计入真实 shadow。实验副本数据不会随 Git 合并自动上线；不要整库覆盖运行数据。run 的输入路径与 manifest 在目标机器上也需要重新验证。
7. 独立端口核对原 Web、`/ml-next`、版本时间和数据来源后再切换。回滚按交接文档恢复切换前代码/服务与当时备份，保留后来新增的交易事实和审计记录。

## 本次样式补充验证

`ml-next` 复用 `style.css`、`theme.js` 及与首页共用的页头/首屏主题模板。卡片、表格、按钮、字体和红涨绿跌使用同一套变量；K 线主题切换原地更新，保留选中日期与价格叠加线。主题按钮禁止窄屏拆字。当前回归：209 项通过；另检查 JavaScript/脚本语法、首页模板、深浅主题、390px 手机布局及合成场景回放。具体提交见分支最新日志。
