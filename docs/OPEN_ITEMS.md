# 未尽事项清单

> **跨轮次的唯一待办来源。** 开新一轮工单前先读这里；一轮结束时把没做完的登记进来。
> 约定见 [`COLLABORATION.md`](COLLABORATION.md) §4。
>
> 关闭一项时**标记完成并写明关闭它的提交／文档，不要删行**——删掉就看不出它曾被判断过。
> 最后更新：2026-09-05（导入升级遗留项；文档同步基线 a460d38/main，补正早期概览的模型结论与来源，未实施模型改动）。

## 状态说明

| 状态 | 含义 |
| --- | --- |
| 待办 | 已确认是问题，尚未动手 |
| 进行中 | 有人在做，注明是谁、在哪个分支 |
| ✅ 已关闭 | 写明关闭它的提交或文档 |
| 已判定不修 | 写明理由（如成本高于收益、场景不成立） |

---

## 来自 ML-UPGRADE-20260904

来源：[Claude 审查报告](records/ml-upgrade-review_claude_20260904.md)（编号定义处）与 [修复回执](records/ml-upgrade-claude-review-fixes_codex_20260905.md)（延期说明）。
该轮已合入 main（`99848c9`）并部署，以下为**当时明确延期**的项，编号沿用原审查报告。

### P2 —— 建议在后续第一批处理

| 编号 | 问题 | 现状 | 状态 |
| --- | --- | --- | --- |
| **P2-3** | 证券规则链路只做了采集，没接到页面；`rules_effective_from` 每天被快照覆盖，无版本历史 | 需独立实现版本历史／变化检测与页面预填，并逐证券核验 lot/tick。文档已标明 HK lot=100 仅为旧模拟参数，非真实交易单位 | 待办 |
| **P2-4** | `runs.start` 每次 train/recompute 全量拷贝 ML 库，无保留策略；硬依赖 git | 存储持续增长。需先定保留与恢复协议才能清理——**不能直接删掉不可覆盖版本所引用的证据**。非 git 部署的回退路径亦未实现 | 待办 |
| **P2-5** | `fetch.run` 任一标的 empty/error 即整步非零，`ml.sh all` 因 `set -e` 不再训练 | 一只票抓不到就阻断全部训练。需要 data/train 对「按市场部分成功」的统一协议 | 待办 |
| **P2-6** | 日历外／非 session 日期的日线与小时线被静默丢弃，无计数 | 已加的是日历整体告警，**不等于**单行丢弃计数。仍未区分 `dropped_not_session` / `dropped_not_final` | 待办 |
| **P2-9** | 迁移后首个 `train` 必然全跳过，需先跑 `data` | 部署顺序已在文档明确（先 data 再 train）；但 `awaiting_final_data` 的回执尚未单独给出「请先运行 data」的提示 | 待办（文档已缓解） |

### P3 —— 低优先级／体验与一致性

| 编号 | 问题 | 状态 |
| --- | --- | --- |
| **P3-2** | `service.review` 的 `end` 默认用 UTC 日期而非交易所本地交易日 | 待办 |
| **P3-3** | `missing_sessions` 混合了「缺模型预测」与「策略可交易性」两种语义；建议拆成 `missing_prediction_sessions` 与 `no_trade_sessions` | 待办 |
| **P3-4** | `published_at` 仅由 `ml.sh publish` 写入，本地 Web 消费场景恒为空 → `facts()` 的「预测在委托前已发布」永远无证据。需要定义「本地可见」时刻 | 待办 |
| **P3-6** | `report.py` 用正则从 HTML 里删除过期标的的行／段落，脆弱；应在渲染前按 code 过滤数据，而非事后改 HTML | 待办 |
| **P3-7** | `service.read_inputs` 自行拼 yf 代码，应复用 `code_map.futu_to_yf` | 待办 |
| **P3-8** | 模块级 `OrderedDict` 缓存无锁（Flask 默认多线程）。风险低 | 待办 |
| **P3-9** | 2026-09-05 已修复状态列表直接输出：`report._status_panel` 按市场、状态和目标日分组，展示中文结果并转义字段；技术设置折叠。`_stock_section` 的旧「回测 + bandit」口径仍待改造 | 部分完成（状态展示已修复，见本行所在修复提交） |

### 模型侧

| 项 | 事实 | 状态 |
| --- | --- | --- |
| E0–E5 无候选晋级 | 等股票权重 raw pinball ≥5%、skill ≥3%、4/6 股票方向改善的门槛均未达成 | 已如实记录为负结果，见[实验汇总](records/ml-upgrade-experiment-results_codex_20260904.md)。**不调低门槛**；后续若做 E6–E8 属新一轮范围 |
| 前向 shadow 未开始 | 历史重建**不是** live、也不是前向 shadow | 待办（需真实等待未来交易日，无法压缩） |

---

## 更早的遗留

| 项 | 出处 | 状态 |
| --- | --- | --- |
| 早期“regime 感知优于堆 RL”主张 | [早期概览快照](https://github.com/kevinchenkai/myStock/blob/e23bb4e184bdbcfff930d57589a6a368f893bc40/docs/ML_OVERVIEW.md)、[Tier1 复检](records/ml-tier1-robustness_claude_20260718.md) | 已判定不按该结论推进：现有 HMM 增强已被多时段复检推翻；新 regime 研究只能作为待独立验证的候选，不能沿用未经证明的优先级 |
| 离线 RL（P4/CQL） | [早期概览快照](https://github.com/kevinchenkai/myStock/blob/e23bb4e184bdbcfff930d57589a6a368f893bc40/docs/ML_OVERVIEW.md)、[当前概览](guides/ml-overview_claude_20260623.md) | 已判定不修 —— 既有实验为负结果，**不上线**；原因不能单凭结果归于样本量。重启须新工单、数据与独立验证协议 |
| Tier1 决策层增强（风险调整 reward、HMM regime 软切换） | [`ml-tier1-robustness_claude_20260718.md`](records/ml-tier1-robustness_claude_20260718.md) | 已判定不修 —— 多时段检验胜率 42%，判为噪声并移除。**不因单次好结果复活** |


## 文档治理来源待确认（2026-09-05）

| 编号 | 事项 | 当前处理 | 状态 |
| --- | --- | --- | --- |
| DOC-01 | 原始需求 v1 的生成者无法从正文或批量导入记录确认 | [原始需求](plans/requirements-v1_unknown_20260621.md) 暂标 unknown；若后续获得作者证据，更新 [清单](catalog.json) 并按 [治理规范](GOVERNANCE.md) 迁移，保留所有旧名 | 待确认来源，不阻塞使用 |
