# myStock 文档导航

> 更新：2026-09-05；治理基线 `25fca51`，分支 `main`。本轮完成命名、分类、来源与引用治理。ML 工程已合入 main 并部署；E0–E5 没有候选达到模型晋级门槛。当前人工采集／训练／按回执发布，无自动下单或自动调度。运行状态以部署回执和现行说明为准，历史提案不再作为执行入口。

## 如何找文档

使用指南在 `guides/`，需求／方案在 `plans/`，专题研究在 `research/`，工单及交付证据在 `records/`。文件名为 **主题_原始作者_YYYYMMDD**；后续换模型修订不会改写原始作者。日期不是最后修改日，当前状态要看正文和清单。

[命名规范与旧名迁移表](GOVERNANCE.md) · [作者／日期依据清单](catalog.json)。固定入口保留在 docs 根目录；原始需求作者无法确认，已明确标为 unknown。

## 当前工单：Web 两项 P0 与依赖升级

用户已批准升级 yfinance／futu-api，并只选择数据更新时间与异常提示、个股缓存快照两个 P0，执行方为原 Astra 任务。实际升级／实施结果以执行回执为准；其余候选和 ML 模型升级继续暂缓。

| 文档 | 用途 |
| --- | --- |
| [Web API 优化调研](research/web-api-opportunities_codex_20260905.md) | Futu／yfinance 能力、代码缺口、最新版本与候选功能 |
| [Web 两项 P0 与依赖升级工单](records/web-data-upgrade-work-order_codex_20260905.md) | 已批准 DEP-01／WEB-01／WEB-02，包含验证、回退和交付范围 |

## 接手与日常使用

| 文档 | 用途 |
| --- | --- |
| [项目 README](../README.md) | 安装、总体架构、Web 与 ML 的运行入口 |
| [项目接手指南 HTML](guides/project-onboarding_cursor_20260704.html) | 从模块、数据到运维的完整导读；已按原始作者 Cursor 和身份日期命名，章节锚点保留 |
| [ML 当前概览](guides/ml-overview_claude_20260623.md) | 业务目标、页面使用、版本／来源、人工发布、当前限制 |
| [ML 工程交接](records/ml-upgrade-handoff_codex_20260904.md) | 已部署状态、排错、恢复及隔离复现；文件日期为工单起始日，正文标记最后更新 |
| [数据字典](guides/data-dictionary_claude_20260623.md) | 当前生产／ML 表与数据口径；6 月统计仅为历史快照 |
| [共用项目约定](../AGENTS.md) / [Claude 入口](../CLAUDE.md) | 极简开发边界、验证与隐私规则；通用约定统一维护 |
| [Codex × Claude 协作](COLLABORATION.md) | 通过 docs/ + git 交接、按阶段留档 |
| [未尽事项](OPEN_ITEMS.md) | 跨轮次唯一待办入口，保留问题编号与出处 |
| [交易日历说明](../mystock/ml/calendars/README.md) | 2020–2027 日历、来源、生成与时区／截止维护 |
| [实验工具说明](../scripts/ml_experiments/README.md) | 离线实验、历史重建、审计和补采工具 |

## 本次升级的证据链

按“方案 → 备份 → 实施 → 实验／审查 → 修复 → 部署”阅读。阶段记录中的工作树、端口、测试数量和“未部署”仅说明当时情况。

| 文档 | 状态与用途 |
| --- | --- |
| [升级前备份](records/ml-pre-upgrade-backup_codex_20260904.md) | Web／ML／完整 data／配置／Git 的备份和恢复验证；不是后来新增数据的覆盖源 |
| [执行工单](records/ml-upgrade-work-order_codex_20260904.md) | 四批工程原始范围，已完成；后续部署授权与结果另记 |
| [执行日志](records/ml-upgrade-execution-log_codex_20260904.md) | 各批实现、验证和阶段决策 |
| [实验结果](records/ml-upgrade-experiment-results_codex_20260904.md) | E0–E5 负结果、协议及晋级门槛；不能据工程上线宣称模型胜出 |
| [历史补齐记录](records/ml-history-refresh_codex_20260905.md) | 隔离历史修复／逐日重建及 Futu 来源审计；部署时另行在目标 ML 库重建 |
| [Claude 独立审查](records/ml-upgrade-review_claude_20260904.md) | 原始审查，结合修复回执判断哪些问题仍存在 |
| [Claude 修复回执](records/ml-upgrade-claude-review-fixes_codex_20260905.md) | 合并前修复、227 passed 的验证记录及逐项延期清单 |
| [审查与上线流程](records/ml-upgrade-review-release_codex_20260904.md) | 历史审查／部署演练流程；下一次部署需用新的备份和目标提交 |
| [部署回执](records/ml-deployment_codex_20260905.md) | 已合入 main、重启 8888、迁移、720 条历史重建、六股 live 生成与公网发布的验收依据 |

当前后续任务：证券 lot／tick 历史与页面接入、冻结快照保留协议、采集部分成功和统计、时间／报告展示改造，以及独立模型验证。执行状态统一维护在 [未尽事项](OPEN_ITEMS.md)，原始证据保留在 Claude 修复回执。

## 方案讨论与历史研究

以下保留原论证、版本和历史实验结果；其中的计划、调度、接口草案或收益结论不一定等于当前实现。

| 文档 | 定位 |
| --- | --- |
| [Codex 升级方案 v1.4](plans/ml-upgrade-plan_codex_20260904.md) | 核心目标、优化背景、建模选择、特征与时点协议 |
| [Claude 合并讨论稿 v0.2.1](plans/ml-upgrade-merged_claude_20260904.md) | 多方共识与取舍过程 |
| [Claude 原升级方案](plans/ml-upgrade-plan_claude_20260904.md) | 初始诊断与候选思路 |
| [旧 ML 概览快照](https://github.com/kevinchenkai/myStock/blob/e23bb4e184bdbcfff930d57589a6a368f893bc40/docs/ML_OVERVIEW.md) | 更新前完整概览与实验叙述，供历史追溯；结论需结合后续复检 |
| [早期 ML 计划](plans/ml-plan_claude_20260623.md) | 原 S0–S3／GPU／调度方案及决策历史 |
| [算法建议](plans/ml-algorithm-proposal_cursor_20260704.md) | 早期候选列表，不代表均已实现或晋级 |
| [第一档稳健性复检](records/ml-tier1-robustness_claude_20260718.md) | 早期单窗结论被多时段复检推翻的证据 |
| [Qlib 借鉴计划](plans/ml-qlib-borrow-plan_claude_20260718.md) | 防泄漏切分与信号评估的设计背景 |
| [需求 v1](plans/requirements-v1_unknown_20260621.md) · [需求 v1 GPT](plans/requirements-v1_gpt_20260621.md) | 最初产品范围与需求讨论 |
| [Futu 数据扩展调研](research/futu-api-research_claude_20260718.html) | 当时接口实测及分期扩展建议；后续特征路线同时参考 Codex v1.4 |
| [Qlib 中文介绍](research/qlib-introduction_grok_20260718.html) · [Qlib 深入解读](research/qlib-deep-dive_claude_20260718.html) | 框架背景资料 |
| [ML 与 Qlib 评估](research/ml-qlib-evaluation_grok_20260718.html) · [Claude 深度评估](research/ml-qlib-evaluation_claude_20260718.html) | 项目借鉴研究与比较 |

## 文档维护规则

命名、分类和来源按 [治理规范](GOVERNANCE.md) 维护，提交前运行 `python3 scripts/check_docs.py`。当前说明（本索引、接手指南、ML 概览、工程交接、数据字典）随实现更新，标记更新时间。日期方案和实验／审查／部署回执保留当时事实，后续补充状态导航，不把旧负结果改写为成功。测试数量、数据完整性和发布结果必须指向具体回执，不能当作永久保证。

此前文档治理轮次直接在 main 治理文档与配套文档路径并按用户要求提交、推送，未执行分支合并；未修改原运行数据库、未重启 8888、未发布公网 ML 报告、未新增调度、未进行交易。该治理轮次没有重跑 API 调研或模型实验；后续 API 核查另见上方 Web 调研。数据库、配置、冻结输入、真实订单、备份和详细回执留在 Git 忽略目录，不提交到公开仓库。
