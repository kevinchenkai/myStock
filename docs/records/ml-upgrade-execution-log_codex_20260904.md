# ML 升级执行日志

> 文档身份：2026-09-04 · 原始作者 codex · 历史记录；2026-09-05 由 Codex 治理文件名与引用。作者／日期依据及旧名见文档清单。 [索引](../README.md) · [清单](../catalog.json)

工单 ML-UPGRADE-20260904；执行分支 `codex/ml-upgrade-20260904`，工作树 `/Users/kk/.codex/worktrees/dc66/myStock`。起点 `446e657`，初始工作区干净（detached HEAD，新建分支后实施）。已读 CLAUDE.md、用户 AGENTS.md 和工单。

- B00–B02：工单及备份可读；SHA256SUMS 全通过，snapshot 137 文件逐一哈希通过，repository.bundle verify 通过，两库 immutable 只读 integrity_check 均 ok。没有复制配置、没有访问原服务。
- B03：独立复制 `data/` 和冻结 `data/upgrade-input/`；46 个输入文件哈希在 `data/upgrade-output/input-hashes.json`，依赖在 `dependencies.json`。无软链接。输出独立到 `data/upgrade-output/`。快速基线：164 passed，1 deselected（2.05s），原 RL 排除按工单命令；原 NaN 回测用例排除。
- 测试与运行 Python：`/opt/anaconda3/envs/mk/bin/python`。实验日志/数据库/报告不提交。所有 Git 提交保留用户 author/committer，追加指定 Codex trailer。

## 第一批（实施中）

离线日历使用固定版本 pandas_market_calendars 5.1.3 生成 US/HK 2020–2026 session 表，不改共享 Python 环境；生成依赖仅装工作树 ignored 目录。HK deadline 保守取 09:00，final_at 包含 CAS 最晚结束及 5 分钟确认缓冲，US 普通/半日市含 5 分钟缓冲。日历界外明确 unavailable。旧无时区采集时间用 UTC−14 小时的保守下界，不臆造精确可用时刻。

来源：[日历库](https://pandas-market-calendars.readthedocs.io/en/stable/pandas_market_calendars.html)、[HKEX 时段](https://www.hkex.com.hk/Services/Trading-hours-and-Severe-Weather-Arrangements/Trading-Hours/Securities-Market?sc_lang=en)。静态表需在 2027 年前更新；交易所临时停市仍需维护表，不视为永久日历承诺。

第一批回归修正了日历依赖的实际缺陷：PMC HKEX 将 2026-12-24 标成全日市，改用 exchange-calendars 4.11.1 XHKG 生成香港表，合成半日市用例覆盖该问题。历史临时天气中断不保证完整，相关缺行情仍保留 missing 状态，不能据此声称精准交易所事件回放。

P01–P08 首轮代码和合成验收通过：176 passed、1 deselected；Shell 语法和 diff 检查通过。P06 固定 session 缺口用于 legacy 审计窗口；v2 的 pending/版本选择在后续批次接入。规则来自 Futu snapshot 的 lot_size/price_spread，按观测日生效并标 approximate；历史未知不反填。未连接 OpenD。

## 第二批

V01–V05：增加唯一新表 ml_prediction_versions（内容禁止 UPDATE/DELETE）；同 run 同内容重试幂等，不同内容冲突。generated/decision/published 分列，输入 SQLite online backup 与哈希记录于私有 runs manifest。兼容投影只允许有效 live 更新，backfill/recomputed 不覆盖 live；原无时区报告原文与日期证据保留，不编造午夜生成时刻。旧 HTML 同日不同报告均留档。

迁移在工作树副本演练并重试，294 个 legacy 版本保留，默认可用信号为 0（缺乏可靠 decision 时间）；源输入副本未迁移。第二批回归 179 passed，1 deselected。CV 最后一折包含余数；raw pinball 从 CQR 指标中分离；bagging 明确为 0 保持冻结旧行为，bagging=1 另作候选。

V06：冻结原 CV 于 `frozen_cv_446e657.py`，实验 A 只增加显式隔离输入路径和 CPU 单线程；按原按股 alpha（PDD 0.25/0.75，其余 0.2/0.8）、共同扩展特征掩码、四折等权复现，保留旧尾部丢弃协议，输出 `exp-a-frozen.txt`。修正矩阵另列协议，不将两者直接差值作为模型增益。B 改为条件事件诊断，5 session 不成熟为 pending、不截成最后现存日；不报告重叠独立回合作为组合收益。

## 第三批

E0/E1/E2/E3/E5/E4 有限矩阵已完成：6 股、13 候选（含 naive）、每股 120 个开发决策 session、seed=0；E5 小时组另有相同 mask 的 control（US 120 / HK 119）。首轮 12:30 Yahoo HK 小时 bucket 跨午休协议修正后，仅重跑小时组。未发现整体晋级候选，不降低 5% / 3% 筛选门槛，不扩大搜索。五种子未运行（没有接近或拟晋级候选）。指标、日期、块区间和负结果见实验报告；逐预测、折训练/校准截止及 mask 哈希在私有 `matrix/`。

E4 在最近成熟 OOF 残差上滚动校准，raw 模型保持 E0；并未实现 ACI。下一个阶段继续固定策略与 Web，生产模型/策略默认不切换。

## 第四批与最终验收

- S01–S04：纯函数事件引擎按提交时现金/库存预留；DAY 委托到期，不卖出未预留库存、不超现金/库存上限。跨日 FIFO 批次、预定最长持有后开盘退出、分红应收/拆股、缺行情持仓保留。固定 boundary/naive_vol/2% offset 对照，legacy 保持原独立口径。费用未填为 gross_fees_missing；测试预算和规则明确 synthetic_fixture。
- W01–W04：`/api/ml/strategy?mode=inventory` schema v2 与 `/api/ml/v2/latest|review|compare`；只读连接、400/503 契约、按内容哈希的有界缓存、小时线仅读取窗口。`/ml-next` 原生 JS + 本地 Lightweight-Charts，风险/报价分层、参数表单、20/60/120重新模拟、连续回放日切片、日期K线/预测/模拟价叠加、仅展示委托事实。快照无完整生命周期，不输出假设收益。
- 隔离 v2 库追加 720 条 E0 离线重建，run=`offline-e0-development-v1`；均明确 recomputed，没有生成伪造 live。初始294条（含旧recomputed）全部 audit_unknown_timing，保留原 source 与原生成时间，排除默认信号和校准。此前“默认有效信号0”指 verified live，旧导入的90条recomputed也已从普通候选选择中排除。
- 浏览器（Codex in-app browser）实际验收：8896 工作树数据正常/缺口；包含重建 + 合成账户计算；选中日期K线叠加和事实不足提示；快速 US.NVDA→US.TSLA→HK.00700 / 60→20 切换最后只显示 HK.00700 20 session；390×844 移动端 document.scrollWidth=innerWidth=390，图表7个canvas，键盘 Tab 可逐日切换。8897空schema库返回60行缺日线；非法 lot=3/数量10显示400错误；8898缺库页面显示503错误。临时空库/缺库服务验收后停止，仅保留8896用户预览。没有提交私人截图。
- 完整 pytest 首次196 passed（含RL与NaN原用例），增加边界用例后201 passed / 3.90s。脚本测试使用 mock Python 子进程，全程不会调用真实 fetch/train/scp/ssh；验证全跳过不发布和失败非零。补充训练前拒旧缓存、训练标签最后一天为T−1、训练跨截止missed_deadline；报告全跳过保留原latest；真实委托晚于预测的时间反向用例；过期bar和多次买入退出不重复配对。
- 108个固定策略合成场景（6股×3窗口×3policy×2费用）完成；原始本地结果 `data/upgrade-output/strategy-validation.json`，汇总见实验报告。最终服务层六股120session cold=1.080s、warm p95=0.122s（10次）；OS磁盘缓存未清空，明确不声称机器重启冷启动。

## 限制和后续

所有改动均待用户审查，不自动合main，不发布、不下单、不设置定时任务，不修改原服务。未进行真实60 session shadow或生产切换；没有候选晋级。日历固定覆盖2020–2026，界外fail closed；历史临时天气中断需要维护，不保证逐次事件已完整收录。证券lot/tick/费用为带来源的观察或显式近似；真实账户预算、历史规则、生效日及分红付款日需正式运行前确认。小时OHLC无法证明同bar内先后及排队成交，回放明确保守顺序假设，不是券商逐笔撮合。前向收益、独立holdout、种子稳定性、E6–E8/外部特征均未完成或未运行，未伪装成交付成绩。

最终复核：203 passed；JS两文件、Shell语法、git diff --check通过。补充partial报告只产生可用市场版本/可恢复manifest、发布实际晚于截止不改生成时间且默认排除；报告原文改为按run独立路径，不覆盖原日期HTML。默认兼容latest仅在有效本次结果生成时更新。最终再校验冻结输入46文件、私有备份snapshot137文件及SHA256SUMS均未改变，工作树ML库integrity_check=ok。完整恢复/部署步骤见 `ML_UPGRADE_HANDOFF_2026-09-04.md`。
