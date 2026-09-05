# ML 升级分支独立审查（Claude）

> 审查对象：`codex/ml-upgrade-20260904`，HEAD `190b582daae0781c50d306631ab1a43464d6edc8`，范围 `446e657..190b582`（5 个提交，53 文件，+5716/−227）。
> 审查者：Claude · 日期：2026-09-05（PDT 2026-09-04 晚）· 状态：**审查结论，未改任何代码；未合并、未推送、未运行 update.sh / ml.sh、未触碰原运行目录的数据库。**
> 依据：通读全部代码/测试/文档 diff；在工作树用合成数据与只读查询逐条验证疑点；对 Codex 预览服务（127.0.0.1:8896）做只读 HTTP 探测。

---

## 0. 三个判断

| 问题 | 判断 | 条件 |
| --- | --- | --- |
| 是否可以合入 main | **可以，修完 P1 后合入** | P1-1（日历 2027）与 P1-2（回看窗缺口）改动都很小；P2-7 文档同步应随合并一起做。合并保留 main 的 README `885e8f7`（分支未改 README，无冲突） |
| 是否可以部署工程 | **可以，按交接文档在部署副本演练后部署** | 注意 §3 的三个部署事实：首个 `train` 前必须先 `data`；publish 流程语义已变；legacy Tab 变慢 |
| 是否可以晋级模型 | **否** | E0–E5 无候选达门槛（与实验报告一致）；`naive_vol` 与现网 E0 打平再次印证「模型 ≈ 波动率缩放」；无独立 holdout、无前向 shadow |

没有 P0。第一批（时间守卫）与第二批（不可覆盖留档）的核心不变量经我独立验证成立；第四批的账户引擎在手算路径上正确；实验协议未发现泄漏。

---

## 1. 审查覆盖范围与实际运行的验证

**读过的代码**：`sessions / versions / runs / pipeline / execution / rules / service / evaluation` 全文；`fetch / predictor / report / data / db / strategy / backfill / review / cv / backtest` diff；`web/app.py / ml_api.py / ml_next.js / ml_next.html / ml_next.css / 模板 include / style.css`；`scripts/ml.sh / ml_preview.py`；8 个实验脚本；5 个新测试文件与 2 个改动测试；两份日历 CSV 抽查；四份交接文档。

**实际运行**（全部在工作树，只读或临时对象）：

| 验证 | 结果 |
| --- | --- |
| `pytest tests/ -q -p no:cacheprovider` | 203 passed，4.3 s |
| `node --check` app.js / ml_next.js / theme.js；`bash -n scripts/ml.sh`；`git diff --check` | 全部通过 |
| ML 是否反向 import Web | 无 |
| 提交作者/trailer | 5 个提交均为用户 author，含 `Co-authored-by: Codex` |
| 日线日期 vs 日历一致性（2024-01 起，六支） | **完全一致**：DB 无日历外日期，日历无 DB 缺失日期 |
| 日历抽查（US 2025-01-09 卡特哀悼日、2026-06-19、2026-07-03、2026-09-07；HK 2026-04-07、07-01、10-01、10-19、2025-10-29、2026-02-17 休市；HK 2026-09-07 交易；半日市与 DST） | 全部正确 |
| 工作树 ML 库 `journal_mode` | delete（`mode=ro` 无 WAL 副作用） |
| 工作树迁移状态 | `ml_prediction_versions`：backfill 135 / live 69 / 旧 recomputed 90 均为 `audit_unknown_timing`；新 recomputed 720（E0 离线重建）；触发器与 UNIQUE 存在 |
| `service.latest`（live）六支 | 全部 `unavailable`（audit 48–50 条）——与交接文档一致 |
| `service.review`（120 session，含重建） | US.NVDA ok 118 / missing_prediction 2；HK.00700 ok 118 / missing_bars 1 / missing_prediction 1；朴素报价 120/120 |
| `service.compare` 合成账户（US.NVDA 60 session） | 三 policy 正常，0.11 s（review 已缓存） |
| legacy `strategy.run_many`（6 支 / 30 天） | 正常，状态行渲染为「—」；**3.97 s**（见 P2-1） |
| 8896 预览服务只读探测 | `/`、`/ml-next`、`/api/positions`、legacy strategy、v2 latest/review/compare 均 200；非法代码 400 |
| 执行引擎手工路径（合成日） | 现金守恒、跳空低开按开盘成交、拆股后 lot 调整、分红计应收不计现金、到期开盘退出优先于 hi 卖单、同 bar 双触达先卖后买并标记歧义、刚好等于现金可买 |
| 疑点复现 | 见 §2 各条的「复现」 |

**未做**：浏览器视觉/移动端验收（信任 Codex 记录）；未运行 `ml.sh train`（禁止项）；未在原运行目录做任何操作；未读配置密钥。

---

## 2. 发现（按严重度）

### P1-1 静态日历 2026-12-31 硬到期，四个月后整条管线与 v2 页面停摆，且无预警

- **位置**：`mystock/ml/sessions.py:15`（`END='2026-12-31'`）、`:45`、`:51`；`fetch.py::run` 中 `sessions.state()` 调用不在 try 内。
- **复现**（已跑）：`sessions.state('US.NVDA', 2027-01-04T22:00Z)`、`next_session('US.NVDA','2026-12-31')`、`window(...,'2027-01-05',20)` 均抛 `Unavailable('unavailable')`。
- **影响**：2027-01-02 起 `ml.sh data` 直接 traceback；`ml.sh train` 六支全部 `unavailable`（receipt all_skipped，无提示原因是日历）；v2 review/latest 返回 400/unavailable。日历重生成依赖隔离环境里的 pmc 5.1.3 / exchange-calendars 4.11.1，临时补救成本高。
- **最小修复**：① 现在就用 `scripts/ml_experiments/freeze_calendar.py` 生成到 2027-12-31 并提交（2027 年假期各交易所已公布）；② `sessions` 增加 `calendar_days_left()`，`report`/`fetch` 起步时若 <60 天写 receipt 与日志警告；③ `fetch.run` 捕获 `Unavailable` 并输出可操作信息（「日历已过期，运行 freeze_calendar」）。
- **验证**：单测 2027 日期可用；日志含警告；`test_ml_sessions` 增加「日历末 60 天内」用例。

### P1-2 特征回看窗内缺一个 session 时，`predict_next_day` 静默回退到缺口前的行

- **位置**：`mystock/ml/predictor.py::predict_next_day`，`last = df.dropna(subset=FEATURE_COLS).iloc[[-1]]`。
- **机理**：`prepare_daily` 把缺失 session 保留为 NaN 行（正确），但 `vol_20d` 等滚动特征在缺口后 ~21 行全为 NaN，`dropna` 后「最新一行」变成缺口前的行。
- **复现**（已跑，合成数据 2026-05-01…09-04，删除 2026-08-26，clock 09-04 22:00Z）：live 模式报 `missed_deadline`（对照组无缺口 → as_of 09-04 / target 09-08 正常）。
- **影响**：live 模式 fail-closed 但状态误导（操作者会以为跑晚了，而不是数据缺口）；**historical 模式**（`backfill.recompute_gaps`、`archive_development`）不做截止检查，会为**错误的、更早的 as_of** 生成一条 recomputed 预测，目标缺口反而没补。yfinance 偶发漏一天即触发。
- **最小修复**：`predict_next_day` 在取 `last` 后断言 `last.date == prepared as_of`（live 用 `state().as_of`，historical 用输入最后一行），否则 `raise Unavailable('feature_gap')`；`recompute_gaps` 校验返回的 `as_of` 等于目标缺口日。
- **验证**：把我的复现固化为单测（live → `feature_gap`；historical → 不写行）。

### P2-1 legacy `/api/ml/strategy` 变慢约 2.5 倍

- **位置**：`strategy.py::run_strategy`：`nxt = {d: sessions.next_session(code, d) for d in dates}`（每个历史日期全扫日历）与 `previous = {...for d in session_days(START, dates[-1])}`。
- **测量**：6 支 / 30 天 3.97 s（改动前约 1.6 s；`next_session` ×1323 ≈ 0.16 s/支，两处合计 ≈ 0.4 s/支）。旧 Tab 默认加载 4 支 ≈ 2.7 s。
- **修复**：`sessions` 用排序列表 + `bisect` 实现 `next_session`；或在 `run_strategy` 一次性构建 target→as_of 映射（只需最近 `days+1` 个 session）。

### P2-2 任何 source 的版本都会 `insert or ignore` 进 legacy `ml_predictions`，旧 Tab 无法区分 recomputed

- **位置**：`versions.py:46-48`。
- **证据**：工作树副本运行 `archive_development` 后，legacy 表 recomputed 由 90 增至 528；legacy 接口行无 `source` 字段，旧 Tab 把 E0 离线重建当普通预测展示（2026-08-18…08-26 的缺口在旧 Tab 里「消失」）。生产库只有在有人运行 archive/recompute 时才会发生，但这两个入口本来就是交接文档推荐的「填充历史研究视图」步骤。
- **修复**：legacy 投影只写 live（backfill/recomputed 不进 legacy 表），或 legacy 行带 `source` 并在旧 Tab 标注「离线重建」。

### P2-3 证券规则链路只做了采集，没有接到页面；`rules_effective_from` 每天被覆盖

- **位置**：`collectors/futu_client.py:470-472`（`rules_effective_from = now[:10]`）；`rules.read_rule` 无生产调用（仅测试）。
- **影响**：每次 `update.sh` 都把「生效日」改成当天，`read_rule(code, db, 昨天)` 永远 unknown；页面 lot 仍靠用户手填；HK.01810 每手 200 的问题在 legacy `LOT_BY_MARKET["HK"]=100` 与 v2 合成 fixture（HK lot=100）里都未解决。
- **修复**：仅在列为 NULL 或 `lot_size` 变化时更新 `rules_effective_from`；`/api/ml/v2/latest` 返回 `rule` 供页面预填并标注 approximate；legacy `lot_for` 优先读快照。

### P2-4 `runs.start` 每次 train / recompute 全量拷贝 ML 库，无保留策略；硬依赖 git

- **位置**：`runs.py:16-19`。
- **影响**：每次 ~8 MB 到 `data/ml/runs/<id>/input.db`，日跑一年约 3 GB；`subprocess.check_output(['git',...])` 在非 git 目录或无 git 的 shell 直接崩，发生在任何守卫之前。
- **修复**：保留最近 N 个 run 的 input.db（或只存哈希 + 指向最近一次完整快照）；git 不可用时 `git_commit='unknown'`。

### P2-5 `fetch.run` 任一标的 empty/error 即整步非零，`ml.sh all` 因 `set -e` 不再训练

- **位置**：`fetch.py::run` 末尾 `if failures: raise RuntimeError`；`scripts/ml.sh` `set -euo pipefail`。
- **影响**：一次 yfinance 限频（2026-07-06 曾发生过 6 支同时限频）会阻断全部市场的训练与发布；改动前是「记录后继续」。
- **修复**：按市场聚合失败；仅该市场全部标的失败时把该市场标为 `data_failed` 并在 train 中跳过，其余市场照常；receipt 记录。

### P2-6 日历外 / 非 session 日期的日线与小时线被静默丢弃，无计数

- **位置**：`fetch.py::fetch_daily/fetch_hourly` 过滤；`sessions.daily_final` 对 `not_session` 返回 False。
- **影响**：日历若错（交易所临时安排、日历版本问题），数据无声消失，事后只能靠对比发现。
- **修复**：`log_sync` 记录 `dropped_not_session` / `dropped_not_final` 计数。

### P2-7 文档与代码不同步（合并时必须一起做）

- `CLAUDE.md:24` ML 表清单缺 `ml_prediction_versions`；`:60` 测试数 167→203、文件数 16→21；`ml.sh` 段落仍是「三步一条龙 / cron」；未提 `/ml-next`、`/api/ml/v2/*`、`data/ml/runs/`、`receipts/`、`calendars/`；「Web 延迟导入 ML」的说明已不成立（`app.py` 顶层 import `ml_api` → `service` → pandas/numpy）。
- `README.md`（main 侧）仍写「首页 = 最新报告，`.../<date>/` = 历史归档」与「港股每次 100 股」；publish 已不再上传日期归档。
- `scripts/ml_experiments/README.md` 未更新：`exp_a_baseline.py` 现在要求 `MYSTOCK_EXPERIMENT_DB`，README 里的命令会 `KeyError`；`exp_b` 参数亦变。
- `report.py:14` 仍 import 未使用的 `mlbackfill`。

### P2-8 publish 语义变化未落文档，单独 `ml.sh publish` 基本不可用

- **位置**：`scripts/ml.sh`：`MYSTOCK_ML_RUN_ID` 与 `MYSTOCK_ML_RECEIPT` 每次新 shell 重新生成；`publish` 只校验当前 run 的 receipt。
- **影响**：`train` 后另起 shell 跑 `publish` → receipt 不存在 → 失败；只有 `all` 或手动导出同一 run id 才能发布。help 文本有一句说明，但 README/交接文档没写操作步骤。
- **修复**：`publish` 无 run id 时默认取 `data/ml/receipts/` 最新且状态为 generated/partial 的 receipt；文档写明。

### P2-9 迁移后首个 `train` 必然全跳过，需先 `data`

- **机理**：旧库 `synced_at` 为无时区本地时间，`daily_final` 的 −14 h 保守规则把最近一根日线判为未确认（已复现：09-03 行 stamp「09-04 02:39」→ 非最终；09-02 行 → 最终）。
- **影响**：这是设计选择（「直接 train 不能绕过」），但部署时若先 `train` 会得到 all_skipped 且状态 `awaiting_final_data`，容易误判为故障。交接文档的顺序 `update.sh → ml.sh data → ml.sh train` 是对的，建议 receipt 在此情形下附「先运行 data」提示。

### P3（不阻塞，记录）

- P3-1 US 决策截止 = 开盘 09:30 ET，盘前信息可用；HK 为 09:00。可接受，但应写进文档作为口径。
- P3-2 `service.review` 的 `end` 默认用 UTC 日期而非交易所本地日期（`now.date()`）。
- P3-3 `missing_sessions` 统计包含「缺模型预测但 naive/fixed 策略仍下单」的日子，语义混淆；建议分 `missing_prediction_sessions` 与 `no_trade_sessions`。
- P3-4 `published_at` 仅由 `ml.sh publish` 写入；本地 Web 消费场景永远为空，`facts()` 的「预测在委托前已发布」恒为无证据。需要一个「本地可见」时刻的定义。
- P3-5 `test_script_all_skip_and_failure_never_calls_publish` 的 all-skipped 分支靠假 python 对 `-c` 返回 1 达成，并未真正验证 receipt 判断；建议让假 python 真读 receipt。
- P3-6 `report.py` 用正则从 HTML 里删除过期标的的行/段落，`sections` 过滤用代码子串匹配，脆弱；建议在渲染前按 code 过滤数据而非事后改 HTML。
- P3-7 `service.read_inputs` 自行拼 yf 代码（`code[3:].lstrip('0').zfill(4)+'.HK'`），应复用 `code_map.futu_to_yf`。
- P3-8 模块级 `OrderedDict` 缓存无锁（Flask 默认多线程）；风险低。
- P3-9 `report` 中 `_stock_section` 等仍按旧「回测 + bandit」口径渲染，`本次状态：{statuses}` 直接 str(list) 进 HTML，可读性差。

---

## 3. 部署前必须知道的三个事实（非缺陷，行为变更）

1. **顺序**：迁移（`init_ml_db` + `versions.migrate_legacy`）→ `update.sh` → `ml.sh data` → `ml.sh train`。跳过 `data` 直接 `train` 会 all_skipped（P2-9）。
2. **publish**：不再上传 `<date>/` 归档，只覆盖 `index.html`；单独 publish 需同一 run id（P2-8）。公网页面的历史归档功能从此中断，需决定是否保留。
3. **legacy Tab**：仍可用、口径不变（schema_version 1），但变慢（P2-1），且窗口语义改为「最近 N 个 session，缺口以状态行显示」（正确的修复）。

---

## 4. 经独立验证成立的关键不变量（供后续不必重复审）

- 收盘守卫：盘中 bar 不入库（`fetch` 过滤）、训练前再查（`prepare_daily`）、训练后再查截止（`check_deadline`）、全部市场训练完再查一次（`report` 的 expired 重检）。合成用例与我的手工用例均通过。
- 午休/半日市/DST/假期：HK 12:30 桶跨午休的处理、半日市 final_at、US 夏令时切换，均与日历一致。
- 不可覆盖：同 run 同内容幂等、不同内容冲突、跨 run 并存、触发器阻止 UPDATE 内容列与 DELETE；`migrate_legacy` 可重复执行；legacy live 迁入后为 audit 状态、不进默认信号。
- 只读边界：Web 主库与 ML 库连接均 `mode=ro + query_only`；写 SQL 必失败（测试与我在 8896 的探测一致）。
- 账户引擎：现金/库存守恒、禁裸空、预留、到期开盘退出、FIFO 回合唯一配对、分红计应收、拆股调整、同 bar 歧义显式标注；重启窗口与连续切片语义不同。
- 实验协议：`upgrade_matrix` 训练集标签成熟时刻 ≤ 首个测试决策日；滚动 q 只用已成熟残差；raw pinball 与 CQR 指标分离；共同样本掩码；结果诚实为负。
- 隐私：diff 内无持仓/订单/配置；实验结果文档仅含合成账户与汇总指标。

---

## 5. 建议的合并前清单

1. 修 P1-1（生成 2027 日历 + 过期告警）与 P1-2（`feature_gap` 断言）。
2. 修 P2-1（legacy 性能）与 P2-2（legacy 投影只写 live），改动都在十行以内。
3. 同步 P2-7 的三处文档；补 P2-8 的 publish 操作说明。
4. 复跑 `pytest`、`node --check`、`bash -n`、`git diff --check`。
5. 合并时保留 main 的 README `885e8f7`；合并后按 §3 顺序在部署副本演练一次。

P2-3 / P2-4 / P2-5 / P2-6 / P2-9 可作为合并后的第一批小修复；P3 记入待办。
