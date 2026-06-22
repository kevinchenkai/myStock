# CLAUDE.md

本文件供 Claude Code 在本仓库工作时参考。面向开发者的完整说明见 [`README.md`](README.md)。

## 项目简介

myStock 是个人 **港股 / 美股** 持仓、交易、行情的**本地化**数据系统：把分散在富途（持仓 / 订单 / 成交）和 yfinance（每日行情、通用信息）的数据抓进本地 SQLite，再用本地 Flask 页面查询、下钻单股、计算交易盈亏与复盘。单用户、单机、无登录鉴权。市场范围**仅 HK 与 US**。

## 架构与数据流

```
富途 OpenD（本地网关 127.0.0.1:11111）──┐ futu-api
yfinance（行情/通用信息）────────────────┴──► collectors ──► SQLite(data/mystock.db) ──► Web(Flask :8888)
```

- **采集层**（`mystock/collectors/`）从富途 OpenD 与 yfinance 拉数据，清洗后写库。
- **Web 层只读 SQLite**，绝不直接调富途 / yfinance —— 保证页面快、可离线。新增页面功能时遵守此边界：抓取放 pipeline，展示只读库。
- 数据表（见 [`mystock/schema.sql`](mystock/schema.sql)）：`positions`、`orders`、`deals`、`daily_quotes`、`stock_profiles`，外加 `sync_log`、`quote_skiplist`。

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

测试需在 `mk` 环境（base anaconda python 无 yfinance 会报 YFError）。当前 `tests/`：`test_code_map.py`、`test_db.py`、`test_pnl.py`。`pnl.py` 与 `code_map.py` 是纯函数，新逻辑优先写成可单测的纯函数。

前端无构建工具，改动后用 `node --check mystock/web/static/app.js` 做语法检查。

## 约定与注意事项

- **涨跌配色（中国习惯）**：红=涨 / 绿=跌。前端切勿用国际惯例反过来。
- **货币**：yfinance 的 marketCap / EPS / 目标价为**标的本币**（HK→HKD、US→USD），非 USD。展示时按 `currency` 字段标注，勿硬编码美元。
- **路由**：股票代码不含 `/`，路由用 `<code>`（string 转换器，遇 `/` 停）而非 `<path:code>`（会贪婪吞掉 `/profile`、`/analysis` 子路径）。
- **P&L 口径**：盈亏 Tab 用移动平均成本（券商口径）；单股复盘用 FIFO 配对（干净的持有周期）。窗口前缺失的买入用 `positions.cost_price` 兜底；兜底成本 ≤ 0（富途超卖记账产物）视为不可用 → 记入 `uncovered_sell_qty`。
- **复盘范围**：`analyze_stock` 仅做客观交易行为复盘，**不输出个性化投资建议**（你不是持牌投顾）。
- **价格走势图**：用 vendored Lightweight-Charts（`static/vendor/`，离线、无构建步骤）。该库需真实 DOM 容器 + 创建后注入数据，故 `renderChart` 只产出占位容器，`openStock` 在 `innerHTML` 写入后调 `mountChart()` 挂载；浮窗关闭 / 切复盘时须 `destroyChart()` 释放。颜色从 CSS 变量读取以适配红涨绿跌 + 深浅主题。

## 安全 / 隐私（提交前务必检查）

以下已在 `.gitignore` 且**绝不可提交**（仓库为公开）：

- `config.yaml`（含交易密码）、`config.*.local.yaml`
- `data/`、`*.db*`（真实持仓/交易数据）

交易密码只走环境变量或 `config.yaml`，**绝不硬编码、绝不进 git**。提交前确认 diff 与新文件无密钥/token/密码。

## 前置条件

1. 富途 **OpenD** 须在本机启动并登录（`127.0.0.1:11111`）才能抓富途数据。
2. 历史成交仅支持实盘（`TrdEnv.REAL`）。
3. 富途历史接口单次默认仅 90 天 → 代码按 80 天窗口分段查询后合并（抓取范围 `2025-01-01` 至今）。
