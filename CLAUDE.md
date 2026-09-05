# CLAUDE.md

本文件供 Claude Code 在本仓库工作时参考。面向开发者的完整说明见 [`README.md`](README.md)。

## 项目简介

myStock 是个人 **港股 / 美股** 持仓、交易、行情的**本地化**数据系统：把分散在富途（持仓 / 订单 / 成交）和 yfinance（每日行情、通用信息）的数据抓进本地 SQLite，再用本地 Flask 页面查询、下钻单股、计算交易盈亏与复盘。单用户、单机、无登录鉴权。市场范围**仅 HK 与 US**。

## 架构与数据流

```
富途 OpenD（本地网关 127.0.0.1:11111）──┐ futu-api
yfinance（行情/通用信息）────────────────┴──► collectors ──► SQLite(data/mystock.db) ──┐
                                                                                      ├─► Web(Flask :8888)
ML 管线（ml.sh data/train）──────────────────► SQLite(data/ml/mystock_ml.db) ─────────┘（只读）
```

- **采集层**（`mystock/collectors/`）从富途 OpenD 与 yfinance 拉数据，清洗后写库。
- **Web 层只读 SQLite**，绝不直接调富途 / yfinance —— 保证页面快、可离线。新增页面功能时遵守此边界：抓取放 pipeline，展示只读库。
- **Web 可只读 ML 库**（`data/ml/mystock_ml.db`）：ML 的预测留档与回溯结果通过 `/api/ml/*` 暴露给页面。
  仍是「只读」边界——**Web 绝不写 ML 库、绝不触发训练/抓取**，计算走 `mystock/ml/` 里的纯函数（如 `strategy.run_many`）。
  反向依赖依然禁止：`mystock/ml/` 不得 import `mystock/web/`。ML 库缺失时相关接口返回 503，不影响其余页面。
- 数据表（见 [`mystock/schema.sql`](mystock/schema.sql)）：`positions`、`orders`、`deals`、`daily_quotes`、`stock_profiles`、`fx_rates`、`account_funds`、`capital_flow`，外加 `sync_log`、`quote_skiplist`。
- ML 库表（见 [`mystock/ml/schema.sql`](mystock/ml/schema.sql)）：`ml_quotes_1d`、`ml_quotes_1h`、`ml_predictions`、`ml_prediction_versions`、`ml_deals`、`ml_orders`、`ml_positions`、`ml_sync_log`。

## 关键模块

| 路径 | 职责 |
| --- | --- |
| `mystock/config.py` | 读 `config.yaml`；交易密码用环境变量 `MYSTOCK_FUTU_TRADE_PWD` 覆盖 |
| `mystock/code_map.py` | 富途 ↔ yfinance 代码互转（纯函数，HK.00700↔0700.HK、US.AAPL↔AAPL） |
| `mystock/collectors/futu_client.py` | 富途持仓/订单/成交抓取 |
| `mystock/collectors/yf_client.py` | yfinance 日线 + 通用信息（`_profile_from_info`、`fetch_profile`） |
| `mystock/db.py` | SQLite 读写，全部 UPSERT（幂等） |
| `mystock/pipelines/init_load.py` | 全量初始化（建库 + 全量抓取 + profiles） |
| `mystock/pipelines/update_load.py` | 增量更新（当天覆盖，profiles 全量刷新） |
| `mystock/pnl.py` | 交易盈亏：`compute_pnl`（移动平均成本+成本兜底）、`analyze_stock`（FIFO 回合复盘） |
| `mystock/web/app.py` | Flask 路由（只读 API + 页面） |
| `mystock/web/static/{app.js,theme.js,style.css}` | 前端（无构建步骤，原生 JS） |
| `mystock/web/static/vendor/` | 第三方库（本地 vendored）：Lightweight-Charts（价格走势 K 线图） |

## 常用命令

环境为 conda env **`mk`**（yfinance/futu-api 只装在这里）。脚本会自动 `conda activate mk`。

```bash
bash scripts/init.sh      # 首次：建环境 + 建库 + 全量抓取（幂等）
bash scripts/update.sh    # 增量更新（需 OpenD 已登录）
bash scripts/server.sh    # 启动 Web（127.0.0.1:8888），仅读库
```

**改了后端路由 / pnl 逻辑后，需重启 server.sh 才生效**（运行中的进程不会热加载）。

### 测试

```bash
conda activate mk && python -m pytest tests/ -q
```

测试需在 `mk` 环境（base Python 缺 yfinance 等依赖可能报错）。文件数／用例数随分支变化，以 `python -m pytest tests --collect-only -q` 为准；本次验证见 [Claude 修复回执](docs/records/ml-upgrade-claude-review-fixes_codex_20260905.md)。多数测试为合成数据；少数既有 ML 测试读取本地库并拟合模型，完整验证在隔离工作树副本运行。

前端无构建工具，改动后用 `node --check mystock/web/static/app.js` 做语法检查。

## 约定与注意事项

- **涨跌配色（中国习惯）**：红=涨 / 绿=跌。前端切勿用国际惯例反过来。
- **货币**：yfinance 的 marketCap / EPS / 目标价为**标的本币**（HK→HKD、US→USD），非 USD。展示时按 `currency` 字段标注，勿硬编码美元。
- **路由**：股票代码不含 `/`，路由用 `<code>`（string 转换器，遇 `/` 停）而非 `<path:code>`（会贪婪吞掉 `/profile`、`/analysis` 子路径）。
- **P&L 口径**：盈亏 Tab 用移动平均成本（券商口径）；单股复盘用 FIFO 配对（干净的持有周期）。窗口前缺失的买入用 `positions.cost_price` 兜底；兜底成本 ≤ 0（富途超卖记账产物）视为不可用 → 记入 `uncovered_sell_qty`。
- **复盘范围**：`analyze_stock` 仅做客观交易行为复盘，**不输出个性化投资建议**（你不是持牌投顾）。
- **行情跳过名单（skiplist）不自愈**：连续抓空 `SKIP_THRESHOLD`（=5）次的代码会被跳过且不再请求 → 无从触发 `clear`。一次限频/网络抖动会批量抓空，阈值太低会误伤真实持仓。误伤后手动止血：`python -m mystock.pipelines.maintenance reset-skiplist [代码...]`（清空/指定重置）。退市清仓且不再关注的代码用 `... maintenance purge <代码>`（删所有表，**不可逆**，会改历史盈亏）。
- **yfinance `end` 排他**：`Ticker().history(start, end)` 的 `end` **排他**（只返回 `< end` 的 bar），传 `end=今天` 会漏当天。日线（`fetch_daily`）与汇率（`fetch_fx`）统一经 `_end_inclusive`（end+1 天）修正。
- **资产趋势 / 组合概览口径**：均从已入库 `positions` 快照聚合，**零新抓取**（Web 只读边界）。跨币种（USD/HKD）**不相加**，按市场各画一条线 / 各一张卡；快照不可从富途回补（富途只给当前持仓），历史空缺只能随 `update.sh` 自然积累。
- **价格走势图**：用 vendored Lightweight-Charts（`static/vendor/`，离线、无构建步骤）。该库需真实 DOM 容器 + 创建后注入数据，故 `renderChart` 只产出占位容器，`openStock` 在 `innerHTML` 写入后调 `mountChart()` 挂载；浮窗关闭 / 切复盘时须 `destroyChart()` 释放。颜色从 CSS 变量读取以适配红涨绿跌 + 深浅主题。资金流向柱图同一套两段式（`renderCapitalFlow` 占位 → `loadCapitalFlow` 取数 → `mountFlowChart` 挂载），其销毁挂在 `destroyChart()` 里一并触发，不必在每个关闭点各调一次。
- **资金流向（`capital_flow`）**：富途独有（yfinance 无），`get_capital_flow(PeriodType.DAY)` **只给近 1 年**日频 —— 回补起点经 `CAPITAL_FLOW_MAX_DAYS`（370 天）抬高，传更早的 start 只是白跑。`start`/`end` 均为**闭区间**（与 yfinance 的 `end` 排他相反，勿套用 `_end_inclusive`）。金额为标的**本币**（HK→HKD、US→USD）且 `main_in_flow`（主力）≈ 超大单 + 大单，**不是**各档之和，六个字段勿相加。首次 `update.sh` 无同步点 → 自动回补近 1 年（38 只约 1 分钟），之后每天只重抓当天。

## 多 agent 协作（Codex × Claude）

**`docs/` + git 是唯一协作信道**——不读对方 session 状态、不看进程、不进对方工作树窥探进度。要知道对方做到哪了就读 `git log` 与该轮文档；要交东西给对方就提交进 git，没提交 = 没交付。对方没写进文档的就是没做，不替它脑补进度。

文档命名与旧名映射见 [docs/GOVERNANCE.md](docs/GOVERNANCE.md)，新增／改名需同步清单和索引，并运行 `python3 scripts/check_docs.py`。

完整约定见 [`docs/COLLABORATION.md`](docs/COLLABORATION.md)：一轮的形状（工单 → 执行 → 审查 → 修复 → 交接 → 部署）、文档命名、每份文档头三行必须交代的基线 SHA／分支／状态／边界。

两条最容易出事的：

- **边界要写「没做什么」**，逐条声明是否合并推送、是否碰过原运行库、是否重启 8888、是否发布公网、是否新增调度、是否有真实下单——这些不可逆或对外可见，审查方要能一眼确认，而不是从 diff 里推断。
- **未尽事项登记到 [`docs/OPEN_ITEMS.md`](docs/OPEN_ITEMS.md)**，别留在某轮的收尾文档里沉底。开新一轮前先读这份清单。

## ML 升级运行约定

- `/ml-next` 使用共享主题；`/api/ml/v2/{latest,review,compare}` 只读 ML 库。真实订单事实通过 `review?selected=日期` 附加返回，没有独立 facts 路由。历史回溯默认显示明确标注的重建，下一目标日卡片只取 live；不得把 recomputed 当成当天生成或前向 shadow。
- `ml_prediction_versions` 按 run 追加不可覆盖版本；`ml_predictions` 为旧版投影，仅后续有效 live 写入，已有混合来源逐行标注。审计迁移不追认历史时间。
- `data/ml/runs/<id>/` 保存私有冻结 input.db 与 manifest；`receipts/` 保存训练回执和独立 `.data.json` 数据回执；`reports/runs/<id>/` 是本地报告归档。
- `mystock/ml/calendars/` 为 2020–2027 冻结日历；剩余不足 60 天日志／回执预警，越界拒绝。US 决策截止 09:30 ET，HK 09:00；生成器依赖只在隔离工具环境使用。
- 全部人工触发：迁移副本 → `update.sh` → `ml.sh data` → `ml.sh train`。首次 train 前先 data 确认收盘缓存；feature_gap 先修行情缺口。`ml.sh` 无参数仅帮助，没有 cron。
- `train` 打印回执；换终端用 `bash scripts/ml.sh publish <回执路径>`，校验产物哈希、run（如显式指定）和目标截止。不自动选择旧文件，仅覆盖公网 index.html，不上传日期归档。`all` 包含公网发布；任一采集失败仍会中止 all。详细演练见 [交接流程](docs/records/ml-upgrade-review-release_codex_20260904.md)。
- Web 顶层 import ML API → service → pandas/numpy；缺 ML 数据库可返回接口错误，但缺这些 Python 依赖可能阻止 Web 启动，不能声称全部延迟导入。
- legacy 的 US 10/HK 100 为模拟参数，不是证券实际交易单位；v2 lot/tick 仍需人工核验，规则快照尚未完整接入。

## 安全 / 隐私（提交前务必检查）

以下已在 `.gitignore` 且**绝不可提交**（仓库为公开）：

- `config.yaml`（含交易密码）、`config.*.local.yaml`
- `data/`、`*.db*`（真实持仓/交易数据）

交易密码只走环境变量或 `config.yaml`，**绝不硬编码、绝不进 git**。提交前确认 diff 与新文件无密钥/token/密码。

## 前置条件

1. 富途 **OpenD** 须在本机启动并登录（`127.0.0.1:11111`）才能抓富途数据。
2. 历史成交仅支持实盘（`TrdEnv.REAL`）。
3. 富途历史接口单次默认仅 90 天 → 代码按 80 天窗口分段查询后合并（抓取范围 `2025-01-01` 至今）。
