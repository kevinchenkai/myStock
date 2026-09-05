# Claude 独立审查：合并前修复回执

> 文档身份：2026-09-05 · 原始作者 codex · 历史记录；2026-09-05 由 Codex 治理文件名与引用。作者／日期依据及旧名见文档清单。 [索引](../README.md) · [清单](../catalog.json)

审查报告：[ml-upgrade-review_claude_20260904.md](ml-upgrade-review_claude_20260904.md)，已完整阅读，原文未修改。

实施分支：`codex/ml-upgrade-20260904`。原审查基线为 `190b582`；本次从 `e623d41` 开始，保留 `6cfa245 / 83027fb / e623d41` 的历史补齐、来源证据及默认历史页面修复。范围按原报告 §5 的合并前清单执行，其余逐条列为待办。本次没有合并 main、推送、生产部署、外部数据采集或真实发布；仅重启工作树的 8896 预览。

## 已完成

| 发现 | 修复与核验 |
| --- | --- |
| P1-1 日历到期 | US/HK CSV 延长到 2027-12-31，2020–2026 原有行逐字节不变；2027 全部工作日休市／半日市与 NYSE、HKEX 官方公告核对，来源见 [日历说明](../../mystock/ml/calendars/README.md)。新增 calendar_days_left 与不足 60 天告警；fetch/report 写日志及回执，manifest 保留训练告警。fetch 捕获越界，输出更新日历命令并非零退出。数据回执单独为 `.data.json`，不会替换训练回执或授权发布。 |
| P1-2 错误回退日期 | 预测器在创建／拟合模型前核验最新完整特征日等于所需 as_of；内部缺 session、末日特征缺失、无可用特征均返回 feature_gap，非有限数按缺失处理。live 与 historical 均覆盖。现有 recompute_gaps 原本已有 as_of 一致性守卫，予以保留并增加“返回旧日期不写任何表”回归，未将其误报为新增修复。 |
| P2-1 legacy 性能 | 日历日期缓存为有序 tuple，bisect 查询；删除全历史 next_session 映射，只取窗口及一个前置 session。保留 400 日接口上限。六股 30 日三次中位耗时 3.859 → 1.411 秒，降低约 63%；除新增 source 字段外，完整返回值与修复前基线逐项相同。 |
| P2-2 来源混入 legacy | 新 backfill/recomputed/未知时间 live 仅进版本表，只有经验证的 generated live 投影到旧表；保留已有混合来源记录，在 legacy API 与逐行 UI 标明来源。旧 live 标签也不证明发布时间。missing_dates 同时检查有效版本表，避免重建不再写旧表后重复计算；显式 HTML 空库导入同时看两表。幂等、冲突和禁止覆盖测试继续通过。 |
| P2-7 文档 | README 以 main `885e8f7` 为底稿更新，保留其中关于负结果、现金口径、历史证据、私有数据及隔离测试的限制说明。同步 CLAUDE.md、实验工具 README、部署／复核交接；覆盖版本表、v2 路由、runs/receipts/calendar、真实依赖链和 HK lot 假设。移除 report 未使用的 mlbackfill import，并修正相关存储 docstring。 |
| P2-8 独立 publish | 使用显式 `ml.sh publish <train 打印的回执路径>`，无需跨终端保留自动生成的 run ID；也接受显式 MYSTOCK_ML_RECEIPT，若设置 run ID 则继续验证匹配。训练输出操作命令；缺回执、错误文件、哈希变化、过期均拒绝。此处采用“明确指定产物”的修复，与报告建议的“自动选择最新 eligible 回执”不同，避免误选旧产物，文档已说明。真实 shell／validator 配合模拟时钟和本地 scp 替身验证，无网络发布。 |
| P3-1 截止口径说明 | README、CLAUDE、日历与交接文档明确 US 09:30 ET / HK 09:00。 |
| P3-5 all_skipped 测试 | fake Python 的 report 分支实际写出 all_skipped 回执，`-c` 分支执行真实 JSON 读取和判断；不再靠固定返回 1 绕过判断。采集失败仍验证阻断训练。 |

## 验证结果

- `python -m pytest tests/ -q -p no:cacheprovider`：**23 个测试文件，227 passed，4.39 秒**，在工作树隔离副本运行。新增合成用例覆盖上述日历、回执、错误 as_of 与投影；少数既有测试只读本地副本并拟合模型。
- `node --check`：app.js / ml_next.js / theme.js 全通过；`bash -n scripts/ml.sh`、`git diff --check` 通过。
- 只读 HTTP：`/`、`/ml-next`、`/ml-next/` 返回 200；legacy 30/400 session 窗口返回长度正确，全部行带 source。
- Node 直接渲染 mlPanel：离线重建／历史回填／live 留档／来源未知四种标签均正确。此次未重新做完整移动端与主题视觉验收，前次结果保留在交接文档。
- 现有隔离 ML 库只读复核：六股近 20/60/120 session 分别 120/360/720 条 `ok`，无缺行情／预测／小时线；保留此前历史修复成果。
- 日历变更只追加新年份，不改变此前 720 条重建输入；现有冻结 input.db 与不可覆盖版本没有重写。
- 性能基线与结果保存在忽略目录 `data/upgrade-output/claude-fixes-20260905/`，不提交真实行明细。

## 按原报告延期的项目

| 发现 | 当前事实与后续处理 |
| --- | --- |
| P2-3 | 证券规则有效日起点仍可能随每日快照覆盖，rules 尚未自动接入页面；需独立实现版本历史／变化检测与页面预填，逐证券核验 lot/tick。文档已明确 HK 100 仅为旧模拟参数。 |
| P2-4 | runs.start 仍逐次完整冻结数据库并依赖 Git。需要制定保留和恢复协议后处理存储增长，不能直接删掉不可覆盖版本所引用的证据；非 Git 部署回退亦未实现。 |
| P2-5 | 任一标的采集 empty/error 仍让 all 中止；新数据回执提供结果与告警，但未改为按市场部分成功。后续需要 data/train 对市场可用性的统一协议。 |
| P2-6 | fetch 仍未区分统计 dropped_not_session / dropped_not_final。已增加的是日历整体告警，不等于单行丢弃计数。 |
| P2-9 | 部署顺序已明确先 data 再 train；无时区旧缓存仍保守拒绝，尚未给 awaiting_final_data 单独增加 receipt 的“先运行 data”提示。 |
| P3-2 | review 默认 end 仍取 UTC 日期；未来应统一到市场本地交易日。 |
| P3-3 | missing_sessions 仍混合模型缺失与策略可交易性；需另拆 no_trade 等指标。 |
| P3-4 | 本地 Web 可见时点仍未定义；published_at 只对应真正的公网发布，不追认本地读取时间。 |
| P3-6 | 报告过期行仍用 HTML 正则／子串过滤，待改为结构化数据过滤后渲染。 |
| P3-7 | service 的代码转换仍有重复实现，待统一使用 code_map。 |
| P3-8 | 模块级 OrderedDict 缓存仍无显式锁，待并发策略统一处理。 |
| P3-9 | 原报告仍有旧 bandit 口径和状态列表直接转字符串的展示，待报告模板改造。 |

## 合并与交接结论

原报告 §5 的合并前代码与文档清单已完成，可以交 Claude 做修复复核。工程合并仍需检查 main 当时状态并完成部署副本演练；本次没有替用户执行这两步。新模型仍不满足晋级要求，历史重建不是 live 或前向 shadow。

复核可从 `git diff e623d41..HEAD` 开始，再补看初次审查之后的历史数据修复。完整复核提示词及部署命令见 [审查与上线流程](ml-upgrade-review-release_codex_20260904.md)。Git 合并只带代码与公共日历，不会自动迁移私有 720 条历史版本；本次无需为追加 2027 日历而重新采集或拟合此前 20/60/120 日历史数据。
