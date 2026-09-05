# ML 升级执行日志

工单 ML-UPGRADE-20260904；执行分支 `codex/ml-upgrade-20260904`，工作树 `/Users/kk/.codex/worktrees/dc66/myStock`。起点 `446e657`，初始工作区干净（detached HEAD，新建分支后实施）。已读 CLAUDE.md、用户 AGENTS.md 和工单。

- B00–B02：工单及备份可读；SHA256SUMS 全通过，snapshot 137 文件逐一哈希通过，repository.bundle verify 通过，两库 immutable 只读 integrity_check 均 ok。没有复制配置、没有访问原服务。
- B03：独立复制 `data/` 和冻结 `data/upgrade-input/`；46 个输入文件哈希在 `data/upgrade-output/input-hashes.json`，依赖在 `dependencies.json`。无软链接。输出独立到 `data/upgrade-output/`。快速基线：164 passed，1 deselected（2.05s），原 RL 排除按工单命令；原 NaN 回测用例排除。
- 测试与运行 Python：`/opt/anaconda3/envs/mk/bin/python`。实验日志/数据库/报告不提交。所有 Git 提交保留用户 author/committer，追加指定 Codex trailer。

## 第一批（实施中）

离线日历使用固定版本 pandas_market_calendars 5.1.3 生成 US/HK 2020–2026 session 表，不改共享 Python 环境；生成依赖仅装工作树 ignored 目录。HK deadline 保守取 09:00，final_at 包含 CAS 最晚结束及 5 分钟确认缓冲，US 普通/半日市含 5 分钟缓冲。日历界外明确 unavailable。旧无时区采集时间用 UTC−14 小时的保守下界，不臆造精确可用时刻。

来源：[日历库](https://pandas-market-calendars.readthedocs.io/en/stable/pandas_market_calendars.html)、[HKEX 时段](https://www.hkex.com.hk/Services/Trading-hours-and-Severe-Weather-Arrangements/Trading-Hours/Securities-Market?sc_lang=en)。静态表需在 2027 年前更新；交易所临时停市仍需维护表，不视为永久日历承诺。

第一批回归修正了日历依赖的实际缺陷：PMC HKEX 将 2026-12-24 标成全日市，改用 exchange-calendars 4.11.1 XHKG 生成香港表，合成半日市用例覆盖该问题。历史临时天气中断不保证完整，相关缺行情仍保留 missing 状态，不能据此声称精准交易所事件回放。

P01–P08 首轮代码和合成验收通过：176 passed、1 deselected；Shell 语法和 diff 检查通过。P06 固定 session 缺口用于 legacy 审计窗口；v2 的 pending/版本选择在后续批次接入。规则来自 Futu snapshot 的 lot_size/price_spread，按观测日生效并标 approximate；历史未知不反填。未连接 OpenD。
