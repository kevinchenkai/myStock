# 文档治理规范与迁移记录

> 作者：Codex · 日期：2026-09-05 · 基线 `25fca51` / `main`。状态：本轮完成 27 份文档的分类、重命名、来源登记与引用迁移；后续提交以 Git 记录为准。
> 边界：沿用用户授权提交并推送文档治理；未合并其他分支，未修改原运行数据库、未重启 8888、未运行 ML 训练／公网报告发布、未新增调度、未执行交易。配套脚本仅调整文档路径与草稿输出，计算协议保持原样。

## 1. 目录与固定入口

| 位置 | 用途 | 生命周期 |
| --- | --- | --- |
| `guides/` | 当前接手指南、运行概览、数据字典 | 随实现维护，更新正文日期，保留文档身份 |
| `plans/` | 需求、算法建议、升级讨论与方案 | 当前已有方案均为历史讨论；新方案单独登记 |
| `research/` | API、Qlib 等专题调研 | 保留原调研日期与作者，不冒充最新 API 核查 |
| `records/` | 工单、执行、实验、审查、修复、部署及交接 | 阶段回执保留事实；当前工程交接可持续维护并标记更新 |
| [README.md](README.md) | 全量人工导航 | 固定入口 |
| [COLLABORATION.md](COLLABORATION.md) | 协作约定 | 固定入口 |
| [OPEN_ITEMS.md](OPEN_ITEMS.md) | 跨轮次唯一待办 | 固定入口 |
| [GOVERNANCE.md](GOVERNANCE.md) | 本规范与旧名迁移表 | 固定入口 |
| [catalog.json](catalog.json) | 文档身份、原始作者、日期依据、旧名、状态与首次提交 | 机器可读清单，配合检查脚本维护 |

按用途分类，文件名同时可按主题聚合、按作者和日期检索；不为每个生成工具再复制一套目录。同一文档只保留一个正文文件，历史版本使用 Git 追溯。

## 2. 文件名

```text
docs/<category>/<topic>_<producer>_<YYYYMMDD>.<md|html>

plans/ml-upgrade-plan_codex_20260904.md
plans/ml-upgrade-plan_claude_20260904.md
guides/project-onboarding_cursor_20260704.html
research/qlib-introduction_grok_20260718.html
```

- `category` 使用上表四类；文件名全部 ASCII 小写，主题内部用短横线，三个主字段按主题、作者、日期排列并用下划线。中文完整标题保留在正文和索引中。
- `producer` 表示**原始生成者／执笔者**，当前使用 `codex`、`claude`、`cursor`、`grok`、`gpt`、`unknown`。不把导入人、审查人或最后修订人当成作者。只有明确共同执笔才用 `joint`，并在清单列出双方；融合双方观点本身不足以认定共同执笔。
- 模型版本（Astra、Fable 5.1、Opus 等）仅在有依据时写正文，不写进文件名，不因换模型修订而改名。后续维护人由 Git 留痕；例如 Cursor 接手指南已由 Claude／Codex 维护，仍保留原始作者标记。
- 日期在文件名中使用紧凑 **YYYYMMDD**（例如 `20260905`），正文与清单保留易读的 ISO 日期 `2026-09-05`。日期是**文档身份日期**。本次已有文件名日期原样保留；无日期时优先明确的成文日期，否则使用首次入库提交自身时区的日期，并在清单注明“首次入库”，不伪称真实创建日。数据快照日期、实验窗口和本次改名日期均不能代替成文日期。
- 同一天小修继续更新同一文档，版本号写正文；独立新一轮方案或需要冻结的阶段快照另立文件。主题相同、作者和日期也相同的独立文件，用有意义的主题后缀区分，避免 `final`、`latest2`、`最终版`。
- README、协作约定、待办、本治理规范和清单是有意保留的固定入口，作者／日期登记在清单里。其他文件不随每次维护改名。

## 3. 来源核验与存疑项

依据优先级：正文明确署名／成文日期 → 提交明确说明“导入某工具版本” → 专门文档提交的署名与首次日期。批量导入时的提交署名只证明导入，不能自动给每份原文归属作者。

本轮保留的关键差异：

- Qlib 两份 Grok 报告由 Claude 批量入库，提交 `f7b6081` 明确注明 Grok 版；文件名按 Grok 标注。
- Claude 独立审查由 Codex 代为提交，正文明确审查者为 Claude；修复回执则是 Codex 执行，主题中的 `claude-review` 表示回应对象。
- 原始需求 v1 无明确生成者，记 `unknown`；GPT 需求仅能确认原文件名的 `gpt` 标记，不能推断具体模型／客户端。
- 审查原文件日期为 09-04，正文写 09-05（PDT 09-04 晚）；历史补齐原文件为 09-05，首次提交 PDT 为 09-04。均保留原日期，不通过改名抹平时间差异。首次提交与日期依据详见清单。

## 4. 旧名、新名与历史链接

本次是用户明确要求的一次集中迁移，取代旧命名规则中“原则上不改名”的默认约定。迁移之后继续维持身份稳定。没有删除研究结果、合并不同作者的方案或改写模型结论。

- 仓库内 Markdown／HTML 相对链接按新目录重算；根 README／CLAUDE、脚本文档路径和当前指南目录树同步更新。
- **固定 Git SHA 的历史链接保留旧路径**：旧提交中的文件本来就叫旧名，改成新名会破坏历史证据。用户以前保存的 main 分支旧路径书签可用下表查找新入口；本次不留下 27 份占位文件或重复正文，也不改写 Git 历史。
- 冻结评估脚本 `scripts/ml_experiments/frozen_cv_446e657.py` 字节不变，旧注释中的路径通过本表追溯；其 SHA256 记录在清单。旧阶段正文中的原始文件名也可作为历史叙述保留，不作为现行可点击入口。
- `ml_sync_h20.sh` 更新文档打包路径，本轮没有执行远端同步。实验汇总脚本默认写私有草稿，避免再次运行时覆盖已审查的历史实验回执；审阅后再按本规范纳入 docs。

### 本轮迁移表（27 份）

旧路径均位于 `docs/` 根目录；新链接相对本目录。完整来源依据和首次提交见 [清单](catalog.json)。

| 旧文件名 | 新文件 | 原始作者 |
| --- | --- | --- |
| `DATA.md` | [guides/data-dictionary_claude_20260623.md](guides/data-dictionary_claude_20260623.md) | claude |
| `ML_OVERVIEW.md` | [guides/ml-overview_claude_20260623.md](guides/ml-overview_claude_20260623.md) | claude |
| `项目接手指南.html` | [guides/project-onboarding_cursor_20260704.html](guides/project-onboarding_cursor_20260704.html) | cursor |
| `myStock-需求文档-v1-gpt.md` | [plans/requirements-v1_gpt_20260621.md](plans/requirements-v1_gpt_20260621.md) | gpt |
| `myStock-需求文档-v1.md` | [plans/requirements-v1_unknown_20260621.md](plans/requirements-v1_unknown_20260621.md) | unknown |
| `ML_PLAN.md` | [plans/ml-plan_claude_20260623.md](plans/ml-plan_claude_20260623.md) | claude |
| `ML_ALGORITHM_PROPOSAL.md` | [plans/ml-algorithm-proposal_cursor_20260704.md](plans/ml-algorithm-proposal_cursor_20260704.md) | cursor |
| `ML_QLIB_BORROW_PLAN.md` | [plans/ml-qlib-borrow-plan_claude_20260718.md](plans/ml-qlib-borrow-plan_claude_20260718.md) | claude |
| `ML_CLAUDE_UPGRADE_MERGED.md` | [plans/ml-upgrade-merged_claude_20260904.md](plans/ml-upgrade-merged_claude_20260904.md) | claude |
| `ML_UPGRADE_PLAN.md` | [plans/ml-upgrade-plan_claude_20260904.md](plans/ml-upgrade-plan_claude_20260904.md) | claude |
| `ML_CODEX_UPGRADE_PLAN_2026-09-04.md` | [plans/ml-upgrade-plan_codex_20260904.md](plans/ml-upgrade-plan_codex_20260904.md) | codex |
| `ML_TIER1_ROBUSTNESS.md` | [records/ml-tier1-robustness_claude_20260718.md](records/ml-tier1-robustness_claude_20260718.md) | claude |
| `ML_UPGRADE_CLAUDE_REVIEW_2026-09-04.md` | [records/ml-upgrade-review_claude_20260904.md](records/ml-upgrade-review_claude_20260904.md) | claude |
| `ML_PRE_UPGRADE_BACKUP_2026-09-04.md` | [records/ml-pre-upgrade-backup_codex_20260904.md](records/ml-pre-upgrade-backup_codex_20260904.md) | codex |
| `ML_UPGRADE_EXECUTION_LOG_2026-09-04.md` | [records/ml-upgrade-execution-log_codex_20260904.md](records/ml-upgrade-execution-log_codex_20260904.md) | codex |
| `ML_UPGRADE_EXPERIMENT_RESULTS_2026-09-04.md` | [records/ml-upgrade-experiment-results_codex_20260904.md](records/ml-upgrade-experiment-results_codex_20260904.md) | codex |
| `ML_UPGRADE_HANDOFF_2026-09-04.md` | [records/ml-upgrade-handoff_codex_20260904.md](records/ml-upgrade-handoff_codex_20260904.md) | codex |
| `ML_UPGRADE_REVIEW_AND_RELEASE_2026-09-04.md` | [records/ml-upgrade-review-release_codex_20260904.md](records/ml-upgrade-review-release_codex_20260904.md) | codex |
| `ML_UPGRADE_WORK_ORDER_2026-09-04.md` | [records/ml-upgrade-work-order_codex_20260904.md](records/ml-upgrade-work-order_codex_20260904.md) | codex |
| `ML_DEPLOYMENT_2026-09-05.md` | [records/ml-deployment_codex_20260905.md](records/ml-deployment_codex_20260905.md) | codex |
| `ML_HISTORY_REFRESH_2026-09-05.md` | [records/ml-history-refresh_codex_20260905.md](records/ml-history-refresh_codex_20260905.md) | codex |
| `ML_UPGRADE_CLAUDE_FIXES_2026-09-05.md` | [records/ml-upgrade-claude-review-fixes_codex_20260905.md](records/ml-upgrade-claude-review-fixes_codex_20260905.md) | codex |
| `futu-API数据扩展调研.html` | [research/futu-api-research_claude_20260718.html](research/futu-api-research_claude_20260718.html) | claude |
| `myStock-ML借鉴Qlib深度评估-Claude.html` | [research/ml-qlib-evaluation_claude_20260718.html](research/ml-qlib-evaluation_claude_20260718.html) | claude |
| `Qlib深入解读-Claude.html` | [research/qlib-deep-dive_claude_20260718.html](research/qlib-deep-dive_claude_20260718.html) | claude |
| `myStock-ML与Qlib深度调研评估.html` | [research/ml-qlib-evaluation_grok_20260718.html](research/ml-qlib-evaluation_grok_20260718.html) | grok |
| `Qlib中文详细介绍.html` | [research/qlib-introduction_grok_20260718.html](research/qlib-introduction_grok_20260718.html) | grok |

## 5. 新增／修订流程

1. 先看索引与未尽事项，确认是修订当前指南、补充既有讨论，还是独立新一轮文档；不为不同模型的修改自动复制正文。
2. 新文档采用规范文件名，正文头部记录作者、身份日期、最后修订日期、基线 SHA／分支、阶段状态与实际操作边界。无法追溯的信息明确 unknown，不伪造。
3. 在 `catalog.json` 登记路径、标题、分类、日期／作者依据和状态，并在 `docs/README.md` 对应类别加入口；关键新工单／规范同步根 README，普通记录由完整 docs 索引承接。
4. 需要重命名时同步入站链接、脚本读写／打包路径和目录树，旧路径追加到 `legacy_paths`；不得碰固定 SHA 历史链接和冻结源码。内容改动与路径改动尽量分别审阅。
5. 执行只读检查后提交：

```bash
python3 scripts/check_docs.py
git diff --check
```

检查覆盖清单完整性、名称／日期／作者一致性、大小写冲突、重复旧名、全量索引、本地文件链接、HTML 章节锚点和冻结脚本哈希。外部 URL 不做联网检查，避免把本轮治理冒充 API 重新调研。HTML 有版式改动时另做浏览器检查。

旧记录新增阶段结果时单独立回执并链接，不覆盖已审查实验；待办统一登记 OPEN_ITEMS，不因搬文件把工程状态或模型晋级状态改变。


## 6. 本轮验证（2026-09-05）

- 31 份文档与清单一致，27 个旧名映射有效，465 处本地链接／HTML 锚点通过检查；故意注入的断链在只读探测中被正确拒绝。
- 迁移后接手指南在浏览器可打开，19 个章节保留，1280px 视口无横向溢出；所有迁移 HTML 的内嵌脚本保持原样。
- 固定 SHA 来源 URL 保留，冻结 CV 文件哈希不变。除汇总输出入口外，现有 Python／Shell／SQL 改动仅为文档引用；汇总函数的计算 AST 与原实现一致。
- 临时合成输入验证默认草稿输出、显式输出路径和已审查回执不被覆盖；没有使用生产数据。Python 语法、Shell 语法与 git diff 检查通过。

历史追溯可用 `git log --follow -- docs/guides/project-onboarding_cursor_20260704.html`；更早的完整原始路径与首次提交也保留在 catalog。
