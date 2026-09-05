# myStock

个人 **港股 / 美股** 持仓、交易、行情的**本地化**数据系统，外加一条独立的 **ML 预测 / 回测管线**。

项目分两部分：

| 部分 | 做什么 | 入口 | 数据库 |
| --- | --- | --- | --- |
| **一、Web（数据系统）** | 把富途（持仓/订单/成交/资金流）与 yfinance（行情/汇率/通用信息）的数据抓进本地 SQLite，用本地 Flask 页面查询、下钻单股、算盈亏与复盘 | `bash scripts/server.sh` → <http://localhost:8888> | `data/mystock.db` |
| **二、ML（预测与回测）** | 预测次日高低区间（分位回归 + CQR 校准）、1h K 线模拟撮合、规则/bandit 历史对照；人工生成 HTML 报告，按需发布 | `bash scripts/ml.sh data` → `train` → 按需 `publish` | `data/ml/mystock_ml.db`（独立库） |

**ML 报告对外页面（公网，人工运行并发布后更新）**：<https://g.ismayday.com/mystock/>
（当前 publish 仅覆盖首页 index.html；新报告按 run 在本地归档，不再上传日期目录）

- 一处汇总：持仓、历史订单、历史成交、每日行情、账户资金、资金流向、美元汇率。
- 数据本地化：抓取一次后离线可查，支持增量更新。
- 可视化查询：浏览器查看持仓 / 交易，点击股票下钻到通用信息、K 线走势、资金流向与个股交易明细。
- ML 侧：次日区间预测 + 撮合可信度校准 + 三基线对照回测 + 每日报告 + 预测复盘，**结论照实记录（含负结果）**。
- 现代化界面：支持「跟随系统 / 浅色 / 深色」三态主题切换（记忆选择、无闪烁）。

> 单用户、单机、无需登录鉴权。市场范围：**仅 HK 与 US**。

## 当前升级状态（2026-09-05）

核心目标：预测指定股票下一交易日的低价／高价，辅助人类低买高卖，并在明确本金、库存和费用后比较周期权益。风险覆盖区间与执行报价分开评估；系统不自动下单。

| 项目 | 当前状态／入口 |
| --- | --- |
| 升级前备份 | 已备份 Web、ML、完整 data、配置与 Git 历史；137 个文件解压哈希和两库 integrity_check 通过。[备份记录与恢复说明](docs/ML_PRE_UPGRADE_BACKUP_2026-09-04.md) |
| 执行负责人 | **Codex Astra**，已在 `codex/ml-upgrade-20260904` 隔离工作树及数据副本完成实施，后续合入 main。[执行工单](docs/ML_UPGRADE_WORK_ORDER_2026-09-04.md) |
| 已观察到的进展 | 四批工程已实现：session 守卫、不可覆盖版本、E0–E5 实验、受约束回放与 `/ml-next`；历史副本已补齐 20/60/120 session。Claude 合并前修复及验证见 [修复回执](docs/ML_UPGRADE_CLAUDE_FIXES_2026-09-05.md) |
| 后续范围 | 工程已获确认、合入并部署；E0–E5 无候选达模型晋级门槛，证券规则历史与快照保留策略等继续跟踪 |
| 运行边界 | **已合入 main 并推送，8888 服务已重启，ML 数据／历史重建／训练／公网报告发布验收通过**；仍人工触发，无自动下单或自动任务。[部署回执](docs/ML_DEPLOYMENT_2026-09-05.md) |
| 方案／讨论 | [Codex v1.4](docs/ML_CODEX_UPGRADE_PLAN_2026-09-04.md)、[Claude 合并稿 v0.2.1](docs/ML_CLAUDE_UPGRADE_MERGED.md)、[Claude 原方案](docs/ML_UPGRADE_PLAN.md) |

接手与维护：[docs 索引](docs/README.md) · [HTML 接手指南](docs/项目接手指南.html) · [当前 ML 概览](docs/ML_OVERVIEW.md) · [工程交接与恢复](docs/ML_UPGRADE_HANDOFF_2026-09-04.md)。

以下说明以已部署 main 代码为准，保留 `885e8f7` 的已核实限制。工程部署不等于模型晋级；详细运行与验收证据见部署回执。

---

## 0. 总体架构

```
富途 OpenD（本地网关 127.0.0.1:11111）─┐ futu-api
                                       ├─► collectors ─► SQLite(data/mystock.db) ─► Web(Flask :8888)
yfinance（行情 / 汇率 / 通用信息）──────┘                        │                          ▲
                                                                │ 只读快照                  │ 只读 /api/ml/*
                                                                ▼                          │
                                 ML 管线(ml.sh) ─► SQLite(data/ml/mystock_ml.db) ───────────┘
                                        │
                                        └─► 每日 HTML 报告 ─► https://g.ismayday.com/mystock/
```

三条边界（**改代码时务必遵守**）：

1. **Web 层只读 SQLite**，绝不直接调富途 / yfinance —— 抓取放 pipeline，展示只读库，保证页面快、可离线。
2. **ML 管线不写生产库** —— 只从 `mystock.db` 拷只读快照到 ML 库，训练可复现、不污染生产数据。
3. **Web 可只读 ML 库** —— `/api/ml/*` 把 ML 结果展示出来，但**绝不写 ML 库、绝不触发训练/抓取**；反向依赖禁止（`mystock/ml/` 不得 import `mystock/web/`）。ML 库缺失时相关接口返回 503，不影响其余页面。

---

# 一、Web —— 本地持仓 / 交易 / 行情系统

## 1.1 数据表

采集层从富途 OpenD 与 yfinance 拉数据，清洗后写入 SQLite；Web 层只读。

八张数据表：`positions`（持仓快照）、`orders`（历史订单）、`deals`（历史成交）、`daily_quotes`（日线行情）、`stock_profiles`（股票通用信息：公司/估值 + 富途盘面字段，随每日更新刷新）、`fx_rates`（外汇日线，当前为美元兑人民币 USDCNY）、`account_funds`（账户资金每日快照）、`capital_flow`（个股日频资金流向），外加 `sync_log`（同步日志）与 `quote_skiplist`（行情跳过名单）。详见 [`mystock/schema.sql`](mystock/schema.sql)。

字段级数据字典（含取值分布与已知坑点）见 [`docs/DATA.md`](docs/DATA.md)。

---

## 1.2 前置条件（重要）

1. **富途 OpenD 必须在本机启动并完成登录**。`futu-api` 通过本地 OpenD 网关（默认 `127.0.0.1:11111`）通信，程序本身不直连富途服务器。
   下载 / 文档：<https://openapi.futunn.com/>
2. **交易解锁密码**：部分查询接口可能需要 `unlock_trade`。交易密码通过环境变量或 `config.yaml` 注入，**不要硬编码、不要提交到仓库**。
   - 推荐用环境变量：`export MYSTOCK_FUTU_TRADE_PWD='你的交易密码'`
   - 若密码为空，则不执行 `unlock_trade`（适用于无需解锁即可查询的场景）。
3. **历史成交仅支持实盘**（`TrdEnv.REAL`），模拟环境不支持。
4. 富途历史接口不传 `start/end` 时单次默认仅 90 天；本项目抓取 `2025-01-01` 至今，已在代码中**按 80 天窗口分段查询后合并**。
5. 市场范围仅 **HK / US**。
6. Web 服务默认只监听本地 `127.0.0.1:8888`。

---

## 1.3 安装与配置

### 1.3.1 创建 conda 环境

```bash
conda env create -f environment.yml   # 创建名为 mk 的环境
conda activate mk
```

依赖：`futu-api`、`yfinance`、`Flask`、`PyYAML`、`pandas`（见 [`environment.yml`](environment.yml)）。

### 1.3.2 配置文件

```bash
cp config.example.yaml config.yaml    # config.yaml 已在 .gitignore，不会被提交
```

按需修改 `config.yaml`：富途端口、`trd_env`、抓取起始日期、市场、数据库路径、Web 端口等。
交易密码建议走环境变量 `MYSTOCK_FUTU_TRADE_PWD`（优先级高于 `config.yaml`）。

> 未提供 `config.yaml` 时程序会回退使用 `config.example.yaml` 的默认值并给出提示。

---

## 1.4 使用（脚本顺序：init → update → server）

所有脚本会自动 `conda activate mk`，并在缺少 conda / 环境 / 数据库时给出清晰报错。

| 脚本 | 作用 |
| --- | --- |
| `bash scripts/init.sh` | **首次初始化**：建/更新环境、装依赖、建库建表、全量抓取（富途持仓/订单/成交 + yfinance 日线，2025-01-01 至今），写 `sync_log`。幂等，可重复执行。 |
| `bash scripts/update.sh` | **增量更新**（手动按需执行）：读取上次同步点，抓取至今的新数据；**当天数据按覆盖处理**（持仓快照覆盖当天、行情覆盖当天、订单/成交按主键 UPSERT）；并刷新 `stock_profiles` 通用信息 / 盘面字段、`fx_rates` 美元汇率、`account_funds` 账户快照与 `capital_flow` 资金流向（首次运行自动回补近 1 年，之后每天只重抓当天）。 |
| `bash scripts/server.sh` | **启动 Web 服务**（`http://localhost:8888`），仅读数据库，不触发抓取。 |

> ML 侧另有独立入口 `bash scripts/ml.sh`（data / train / publish / all），与上面三个脚本互不干扰，见 [§2.3](#23-模块与运行)。

典型流程：

```bash
# 1) 启动并登录富途 OpenD（必做）
# 2) 首次全量
bash scripts/init.sh
# 3) 之后不定期增量
bash scripts/update.sh
# 4) 看页面
bash scripts/server.sh   # 浏览器打开 http://localhost:8888
```

---

## 1.5 Web 页面

打开 `http://localhost:8888`：

- **我的持仓**：当前持仓（代码、名称、市场、数量、可卖、成本价、市价、市值、浮动盈亏、盈亏比例、币种）。顶部有**组合概览卡片**：用最新快照按币种（USD / HKD）分别汇总总市值、总浮盈额 / 率、盈亏股数比、美股 / 港股占比（跨币种不相加，各成一卡）。
- **我的交易**：历史订单 / 成交，子 Tab「按订单 / 按成交」切换。订单含**全部状态**（未成交 / 部分成交 / 全部成交 / 撤单 / 失败等）。顶部**时间筛选**：全部 / 最近三年子 Tab（年份），更早年份走下拉框；默认只显示最近一年。
- **交易盈亏**：按**实际成交数据**计算每只股票的**已实现盈亏**（移动平均成本法）。卖出时结算 `(卖出价 − 当时平均成本) × 卖出量`；早于抓取起点的建仓买入不在库中时，用持仓快照的 `cost_price` 兜底（成本缺失或为负则该笔卖出不计入，并在合计栏提示"⚠ N 股卖出无成本基准"）。合计**按币种分别汇总**（HKD / USD 不混算）。**点击代码弹出「交易复盘」浮窗**。页面下方另有**财务统计**板块：按年筛选（默认当年），统计该年度内**成单的现金流**（卖出额 − 买入额），按美股 / 港股分别汇总。
- **交易复盘**（点击盈亏 Tab 的代码弹出浮窗）：聚焦该股的**成单交易明细**，并给出客观的**交易行为复盘**——
  - 指标卡：已实现盈亏、已平仓回合数、胜率、盈亏比、成交笔数、买/卖均价、平均持有天数、净持仓等；
  - **已平仓回合**：FIFO 配对的「买入 → 卖出」回合，含持有天数与单回合盈亏/盈亏率；
  - **盈亏分析总结**：基于历史成交的事实陈述（胜率、盈亏比、平均盈亏、持有时长、最大盈/亏回合、数据完整性提示等）。**仅复盘交易行为，不构成任何投资建议或买卖推荐**。
- **市场筛选**：「我的持仓」「我的交易」「交易盈亏」面板顶部均有 **全部 / 美股 / 港股** 筛选条，点击即时过滤（纯前端，不重新请求后端）。交易 Tab 的筛选对「按订单 / 按成交」两个子表同时生效。
- **表头排序（持仓 / 交易盈亏）**：点击**数值列表头**排序，**循环切换**：倒序 ▼ → 正序 ▲ → 取消（恢复原始顺序）。可与市场筛选叠加；空值恒排末尾。
- **单支股票下钻**：在任意表格中**点击代码**，弹出个股详情：
  - **数据时间提醒**（置顶一行）：显示该股最新日线数据日期与距今天数，绿点=新鲜、琥珀点=滞后（超过 3 天时提示运行 `update.sh`），便于识别行情是否需要更新；
  - **通用信息**：公司名、板块、行业、交易所、市值、流通股本、市盈率(TTM)/预期市盈率、市净率、每股收益、股息率、Beta、目标均价、分析师评级、货币、官网。读自 `stock_profiles` 表（随每日更新刷新）；**市值 / 目标均价 / 每股收益按标的本币标注单位**（如港股显示「市值(百万HKD)」），不误标美元；
  - **价格走势（K线）**：蜡烛图 + 成交量副图（红涨绿跌、十字光标、滚轮缩放 / 拖动），基于本地内置的 [Lightweight-Charts](https://github.com/tradingview/lightweight-charts)（vendored 于 `static/vendor/`，离线可用，前端仍无构建步骤）+ 日线表格；
  - **主力资金流向（近 60 日）**：日频**主力净流入**柱状图（红=净流入 / 绿=净流出，与 K 线时间轴对齐），上方一行汇总「近 N 日合计 + 流入/流出天数」。数据来自富途 `get_capital_flow`（yfinance 无此数据），金额为**标的本币**；
  - 该股票的订单与成交明细。
- **资产趋势**：单独的 Tab，激活历史持仓快照，按市场（美股 USD / 港股 HKD）分别绘制**总市值趋势**与**浮动盈亏趋势**两张折线图（双 Y 轴，跨币种不相加）。顶部**时间控件**（30 / 90 / 360 天 / 全部，默认 30 天；数据不足以覆盖的窗口自动置灰禁用）。图下另有**区间市值变化（每日环比）**表：逐快照日列出美股 / 港股市值及相对上一快照日的环比百分比（红涨绿跌）。数据完全来自已入库快照，不新增抓取；快照随 `update.sh` 逐日自然积累。
- **美元汇率**：单独的 Tab，展示**美元兑人民币（USDCNY）**基本信息（最新汇率、区间高/低、区间涨跌、数据区间、交易日数）与**汇率趋势折线图**。数据来自 yfinance（`CNY=X`），从 2025-01-01 起按天入库、随 `update.sh` 例行更新。趋势图用**中性配色**（汇率涨跌语义中性，不套红涨绿跌）。
- **ML 挂单回溯**：单独的 Tab，**只读 ML 库**（`data/ml/mystock_ml.db`）做**实时**回溯——按 ML 预测区间 `[L̂, Ĥ]` 在次一交易日同时挂限价买 / 限价卖，看这套挂法在过去 N 个交易日实际能撮合出什么结果。按股子 tab + 逐日明细（倒序，最新在上）+ 净持仓漂移图。参数一改立刻按当前库重算，与每日 HTML 报告（当日快照）互补。口径与已知局限见 [§2.6](#26-web-端ml-挂单回溯tab)。ML 库不存在时该 Tab 提示 503，不影响其余页面。
- **主题切换**：页面右上角按钮，循环「🖥️ 跟随系统 → ☀️ 浅色 → 🌙 深色」，选择记忆于浏览器（`localStorage`），并做了首屏防闪烁处理；「跟随系统」时随操作系统深 / 浅色实时变化。

**涨跌配色（全站统一）**：**红色 = 涨，绿色 = 跌**，0 为中性灰。前端统一走 `plClass()` 工具函数与 `.up/.down/.flat` CSS class（见 [`mystock/web/static/app.js`](mystock/web/static/app.js)、[`style.css`](mystock/web/static/style.css)）。

> 前端在首次加载时缓存数据，筛选 / 排序均在缓存上重渲染；数据变化后**刷新页面**即可（Flask 每次请求实时提供静态文件，无需重启 server）。

### JSON API

| 接口 | 说明 |
| --- | --- |
| `GET /api/positions` | 最新快照的持仓 |
| `GET /api/orders?code=` | 历史订单（可按富途代码过滤） |
| `GET /api/deals?code=` | 历史成交（可按富途代码过滤） |
| `GET /api/pnl` | 交易盈亏（已实现，按成交数据计算，每股一行） |
| `GET /api/finance?year=2026` | 年度财务统计（现金流口径：卖出额 − 买入额，按美股 / 港股分别汇总） |
| `GET /api/asset-trend` | 资产趋势（历史快照聚合，按市场分组的每日市值 / 浮盈 / 持仓数时序） |
| `GET /api/account-funds` | 账户资金（最新快照 + 历史净资产序列，HK+US 综合账户按 HKD 记账） |
| `GET /api/fx?pair=USDCNY` | 外汇日线（默认美元兑人民币 USDCNY） |
| `GET /api/stock/<code>/analysis` | 单股交易复盘：成交明细 + FIFO 回合 + 复盘统计 + 客观观察 |
| `GET /api/quotes?code=&start=&end=` | 某代码日线 |
| `GET /api/stock/<code>` | 聚合：该股票行情 + 订单 + 成交 |
| `GET /api/stock/<code>/profile` | 该股票通用信息（公司 / 估值，读自 `stock_profiles`） |
| `GET /api/stock/<code>/capital-flow?days=60` | 该股票日频资金流向（主力 / 超大 / 大 / 中 / 小单净流入，本币） |
| `GET /api/ml/strategy?codes=&days=30` | ML 预测区间挂单回溯（**只读 ML 库**，实时计算；ML 库缺失返回 503） |

---

## 1.6 代码映射（富途 ↔ yfinance）

| 市场 | 富途 | yfinance | 规则 |
| --- | --- | --- | --- |
| 港股 | `HK.00700` | `0700.HK` | 去 `HK.` → 数字规整为 4 位 → 加 `.HK` |
| 美股 | `US.AAPL` | `AAPL` | 去 `US.`，直接用 ticker |

纯函数实现于 [`mystock/code_map.py`](mystock/code_map.py)，含单元测试（HK/US 均覆盖）。

---

# 二、ML —— 次日区间预测 / 撮合回测 / 每日报告

> 一页速览见 [`docs/ML_OVERVIEW.md`](docs/ML_OVERVIEW.md)；完整方案与决策记录见 [`docs/ML_PLAN.md`](docs/ML_PLAN.md)。
> 代码在 [`mystock/ml/`](mystock/ml/)，独立库 `data/ml/mystock_ml.db`，与 Web 生产库分库。

升级工程已合入 main 并完成本机与报告部署，进度与备份入口见文首状态表。当前已提供收盘／发布守卫及不可覆盖预测留档；具体修复与验收见 [执行工单](docs/ML_UPGRADE_WORK_ORDER_2026-09-04.md)。

**对外页面**：<https://g.ismayday.com/mystock/>（人工运行并发布报告后更新；当前 publish 仅更新首页；新历史归档留在本地 reports/runs/）

## 2.1 要解决的问题与设计

要复刻的人工动作：**看几个月趋势 → 预估次日最高 / 最低点 → 挂限价单（买偏低、卖偏高）+ 决定数量**。
业务目标是提高**周期费用后账户权益**，计入现金、未平仓市值、费用与公司行动，并同时观察回撤／资金占用。卖出额减买入额仅是现金流，不能替代投资收益；旧报告和回溯的不同口径见 §2.4–2.6。

**灵魂决策：拆成两层，不做端到端 RL 黑箱。**

| 层 | 干什么 | 方法 | 为什么这么分 |
| --- | --- | --- | --- |
| **预测层** | 预测次日区间 `[L̂, Ĥ]` | 分位数回归 + CQR 校准 | 每天一个样本、样本相对足、可单独算误差、可解释 |
| **决策层** | 挂什么价、挂多少量（含不挂） | 当前有规则 S0／bandit 历史实验；升级采用固定报价＋受约束库存管理 | 预测是否准确与交易是否有利分别验证 |

历史 S0→S1→bandit→离线 RL 路线保留在决策记录中；本轮不重启 RL／TFT／HMM。当前执行顺序是**收盘与读取正确性 → 版本化和朴素基线 → 小模型实验 → 库存回溯与页面**。模型打不过基线就保留基线，继续完成工程可用性。

评估时重点检查：

- **撮合模拟器**：使用 **1h K 线近似盘中限价撮合**，跨 bar 可提供部分顺序信息，同一 bar 内高低先后仍未知。旧订单回放曾记录 88–93% 匹配率，但缺完整委托生效／取消时窗，不能据此证明新库存策略的成交精度。
- **三基线门槛**：所有结果对照 ① 买入持有 ② 真实历史成交回放 ③ S0 规则，**绝不只报一条精挑曲线**。

**贯穿始终的纪律**：防未来函数（决策只用截至当日收盘可得信息、切分一律时间 walk-forward）、敬畏小样本（强正则、多种子看方差）、单标的独立账户、工程解耦（独立库 / 独立采集 / 不写生产库）。

## 2.2 标的与数据

**6 支标的**，各股独立账户、**各自本币不换汇**：

- 美股（USD）：**NVDA 英伟达 / TSLA 特斯拉 / PDD 拼多多**
- 港股（HKD）：**HK.00700 腾讯 / HK.09988 阿里 / HK.01810 小米**

独立库 `data/ml/mystock_ml.db`（已 gitignore），八张表（见 [`mystock/ml/schema.sql`](mystock/ml/schema.sql)）：

| 表 | 覆盖 | 用途 |
| --- | --- | --- |
| `ml_quotes_1d` 日线 | 2026-09-04 调研：2021 年起，每股约 1,279–1,307 行 | 预测层特征 + 标签 |
| `ml_quotes_1h` 1 小时线 | 同次调研：本地累积自 2023 年起，每股约 5,424–5,446 行；API 单次请求窗口另计 | 撮合模拟器盘中路径 |
| `ml_deals` 成交快照 | 2025-01 起 | 撮合校准 + human 回放基线 |
| `ml_orders` 委托快照 | 2025-01 起 | 撮合吻合率校准 |
| `ml_positions` 持仓快照 | 若干快照日 | 当前状态 |
| `ml_predictions` 旧版投影 | 保留已有多来源历史；后续仅有效 live 写入／更新 | 旧 Tab 逐行显示来源，不能据此证明发布时间 |
| `ml_prediction_versions` 不可覆盖版本 | 同 run 同值幂等、冲突拒绝；旧数据为审计记录 | v2、有效 live 报告复盘与显式离线重建 |
| `ml_sync_log` | — | 采集日志 |

行情来自 yfinance（`auto_adjust=False`，留 close + adj_close，指标用 adj_close）；
交易事实从生产库 `mystock.db` **只读快照**拷贝，保证训练可复现、不在生产库跑分析。

**三个必须记住的数据约束**（影响结论可信度）：

1. **预测留档和成交事件样本较少**：各 source、各时期分开统计，不能用短期命中率认定长期准确；样本少也是复杂策略过拟合的风险。
2. **1h ≠ 逐笔**：同 bar 内高低先后、排队与部分成交仍不可知；历史日线／小时线高低还存在不一致日期，需质量检查。
3. **时点和样本依赖**：美港收盘先后不同，同日股票也非独立样本；历史盘中输入、缺失和版本覆盖需先处理，再评价模型增益。

**现有 NaN 防护**：采集检查、回测非有限价格保护、报告显示「—」已有回归测试（[`test_ml_nan_guard.py`](tests/test_ml_nan_guard.py)）。这些检查不能证明行情已收盘或价格关系完整；盘中输入／旧缓存确认、inf 和 OHLC 关系补查属于本轮升级范围。

## 2.3 模块与运行

**代码模块**（[`mystock/ml/`](mystock/ml/)）：

| 文件 | 职责 |
| --- | --- |
| `fetch.py` / `data.py` | 数据采集（独立于 `update.sh`，增量优先）/ 生产库只读对齐 |
| `features.py` | 特征工程（防未来函数、用 adj_close） |
| `predictor.py` | 次日高低分位回归（LightGBM 优先、sklearn 回退）+ walk-forward 评估 |
| `calibrator.py` | **CQR 校准**（split conformal；覆盖保证有可交换性等条件，时序行情需实测校准效果） |
| `cv.py` | **Purged / Embargo 滚动切分**（防泄漏评估地基，纯 index 运算） |
| `signal_eval.py` | **信号层评估**（时间轴 IC / RankIC / ICIR），先看信号再看净值 |
| `simulator.py` | 1h 撮合 + 单标的账户 |
| `calibrate.py` | 真实 orders 回放校准（吻合率） |
| `policy.py` | S0 规则 + S2 LinUCB bandit（ε 探索） |
| `backtest.py` | 逐日回放 + 三基线对照（超额奖励） |
| `report.py` | 每日 HTML 报告（自包含、零 JS、红涨绿跌） |
| `review.py` | 预测 vs 次日实际的对齐与命中判定（纯函数） |
| `backfill.py` | 预测留档回填 / 缺口重算补齐（幂等） |
| `strategy.py` | 按预测区间挂单的查询与回溯，供 Web `/api/ml/strategy`；数据库连接只读 |
| `offline_rl.py` | 离线 RL（Discrete CQL，需 d3rlpy/GPU；**已验证为负结果**） |

**一个入口 `ml.sh` 搞定三件事**。S0/预测/回测/报告全是 CPU 算法，**本机 `mk` 环境即可，无需 GPU**（仅离线 RL 需 GPU）：

```bash
bash scripts/ml.sh data       # ① 例行更新数据（增量优先）
bash scripts/ml.sh train      # ② 冻结输入、回测与预测、生成报告；打印回执路径
bash scripts/ml.sh publish "data/ml/receipts/<train-run-id>.json"  # ③ 使用 train 打印的真实路径
bash scripts/ml.sh all        # 显式选择三步执行，包含公网发布；无参数仅帮助
```

> **增量缓存**：`data` 采集增量优先——库中最新日期距今 ≤5 天则只抓短窗（日线近 1 月 / 1h 近 5 天），UPSERT 与全量历史在库内合并，不重抓 5 年（例行从 ~30s 降到 ~11s）。日线 / 1h **各按自身缺口挑档**，避免 1h 连续失败时被日线新鲜度掩盖成永久空洞。首次或补全历史用 `python -m mystock.ml.fetch --full`。

**近期触发方式：人工运行，自动调度以后再议。** 生产数据更新用 `bash scripts/update.sh`；ML 可分步运行：

```bash
bash scripts/ml.sh data       # 先更新 ML 行情与生产事实快照
bash scripts/ml.sh train      # 使用更新后的数据训练、评估并生成本地报告
```

**发布目标**可用环境变量覆盖：`PUB_HOST`、`PUB_DIR`、`MYSTOCK_ML_ENV`。
本分支按市场守卫：盘中跳过，训练只取已确认收盘数据；US 截止为当地 09:30，HK 为 09:00。迁移后须先 `data` 再 `train`，无时区旧缓存可能报 `awaiting_final_data`；最新特征不完整报 `feature_gap`，需补齐输入。日历覆盖至 2027-12-31，剩余不足 60 日写日志和回执预警，越界拒绝并提示更新日历。

`train` 打印回执路径，可在另一个终端执行 `ml.sh publish <该路径>`；也可显式设置 `MYSTOCK_ML_RECEIPT`，若设置 `MYSTOCK_ML_RUN_ID` 则必须匹配。不会自动选择最新文件。只有 generated/partial 且哈希匹配、未过截止的产物可发布。`all_skipped` 不上传旧报告。数据回执为 `<run>.data.json`，不能用于发布；目前任一标的采集空值或失败仍会让 `all` 整步停止。没有配置自动调度。

`publish` 会把本地报告推到公网服务器，**须由用户自己执行**（报告含真实交易信息）；自动发布调度暂不配置。

**GPU 相关脚本**（仅离线 RL 需要）：`ml_setup_h20.sh`（持久 prefix conda env）、`ml_sync_h20.sh`（同步代码）、`ml_vllm.sh`（训练前停 vllm、训练后恢复）。

## 2.4 每日报告里有什么

访问 <https://g.ismayday.com/mystock/>：

- **指标说明**（顶部可折叠）：解释四条曲线（买入持有=地板 / 人类回放=现状坐标 / 规则 S0=门槛 / Bandit=被考核选手）怎么对比着读，理想梯子 = Bandit ＞ 规则 ＞ 买入持有。
- **总览表**：每支标的的四策略期末净值、是否超越买入持有、**次日预测区间 `[L̂, Ĥ]`**、区间宽（%）、**命中率**、宽度 IC。
- **每股分析总结**：从四个策略的实际期末净值**规则化生成**（非 LLM、可复现）。照实写——多数股 Bandit 其实未跑赢买入持有，报告不替它粉饰。
- **近期预测复盘**：回答「前几天说的区间，实际走出来对不对」。按股 tab（tab 上带命中率），每股默认最近 7 个交易日、倒序，其余折进 `<details>`。「区间对照」带图：浅色条 = 预测区间、实色条 = 当日实际高低并**按结果着色**（命中灰 / 上破红 / 下破绿），方向偏置一眼可见。

报告**自包含单文件、零 JS**（交互全部纯 CSS 的 radio tab + details），离线可读、可归档。

**复盘的三点口径**（报告中已显式标注，勿混读）：

- **判定**：main 当前取下一条已有日线，真实 high ≤ Ĥ 且 low ≥ L̂ 才算命中，区分上破／下破／双破；这尚不能区分交易所休市、停牌和采集漏日。正式日历与缺口状态在升级分支处理。
- **与总览不是同一评估协议**：复盘使用逐次留档；报告回测与独立 walk-forward 评估的训练／校准协议不同，不能直接把指标差异归因于模型优劣。
- **三种 source**：`live`（报告生成）／`backfill`（历史 HTML 解析）／`recomputed`（事后截断行情重算）。source 不证明发布时间有效；重算也不能恢复历史数据版本或未留存的信息。缺口重算会写库，升级验证只能在副本中运行，不能覆盖原 live 留档。

> 分列上破／下破可以发现偏侧线索，但小样本中的不平衡不能直接确诊偏置或决定调参。修订分位／校准规则后仍需共同窗口和前向验证。

## 2.5 历史实验与当前证据边界

现有代码可以生成预测、模拟和报告；这不等于已经证明长期净收益。升级调研发现预测覆盖与触达混用、盘中输入、留档覆盖及账户口径差异，旧实验结果需要在统一协议下重建。下表为历史记录，不是本轮候选的验收成绩。

| 模块 | 结论 | 证据 | 能否交付 |
| --- | --- | --- | --- |
| **预测层** | CQR 在旧实验中提高覆盖 | 更宽区间可能降低触达；本次已完成统一协议 E0–E5 对照，无候选达到晋级门槛 | 保留生成能力，新增 raw 损失、skill 与前向验证 |
| **撮合校准** | 旧协议记录匹配率 88–93% | 缺委托生命周期和同 bar 顺序，不能外推新策略 | 保留近似撮合，补事件约束与歧义状态 |
| **规则基线 S0** | 保留固定对照 | 收益需在相同本金、库存、费用下重算 | 保留，加入 naive_vol 与库存规则对照 |
| **bandit** | 旧窗口表现分化 | reward 与账户收益归属存在待修问题，不宣称特定行情下已可用 | 历史对照，本轮不扩展 |
| **离线 RL（CQL）** | 既有实验为负结果 | 原因不能单凭结果完全归于样本量；没有晋级证据 | 本轮不重启／不上线 |

可信版本、时间截止、朴素基准和受约束回放已交付；后续事项统一见 [未尽事项](docs/OPEN_ITEMS.md)。新增特征或更复杂模型只有在共同样本的消融中显示增益，才进入前向观察；预测增益与策略收益分别验收。

**被证伪并已移除的东西**（[`docs/ML_TIER1_ROBUSTNESS.md`](docs/ML_TIER1_ROBUSTNESS.md)）：曾以单种子 / 单测试窗记录为「四标的改善」的两项决策层增强——**风险调整 reward**（sharpe / drawdown_penalized）与 **HMM regime 软切换**——在**多时段锚定滚动**（6 个起点）复检下**全部方向翻转，胜率 15/36 = 42%（≈掷硬币）**，同一支股票只把评估起点挪几十天，Δ 就从 −40% 翻到 +69%。据「打不过就诚实记录」的纪律**予以移除**。属预测层的 **CQR 校准保留**（未被证伪，且与净值方差无关）。

> 这条记录本身是项目的一部分：**没有可信的尺子，"改善"就是噪声。** 借鉴 qlib 的 purged CV + 两级 IC 评估地基（`cv.py` / `signal_eval.py`）正是为此而落地。

**main 既有配置**：`reward=excess · CQR=on · purge=on(隔离带)`。这是现有运行配置，不代表其全部评估口径已经通过升级验收。

## 2.6 Web 端「ML 挂单回溯」Tab

与每日 HTML 报告的分工 —— **报告是当日快照**（人工运行产出、可发布、可离线存档）；**Web 端是实时查询**，参数一改立刻按当前库重算，适合调参与临时探查。入口在 `bash scripts/server.sh` 起的页面（:8888）「ML 挂单回溯」Tab。

- **策略**：使用基准日 T 的 `[L̂, Ĥ]`，在下一市场 session 同时模拟限价买 L̂／卖 Ĥ。旧版每笔按 `LOT_BY_MARKET` 固定 **US 10 股／HK 100 股**；这是代码参数，不是经核实的港股统一交易单位。
- **撮合**复用 `simulator.match_limit_order`（1h K 线），可处理跨 bar 触达和开盘跳空；同 bar 内高低先后及真实排队成交仍不确定。
- **盈亏** = 现金流净额（卖 − 买）+ 期末净持仓按最后收盘折算。
- **接口**：`GET /api/ml/strategy?codes=US.NVDA,HK.00700&days=30`，返回逐日 `results` + 按币种分组的组合 `totals`。
- **窗口限制**：按最近 N 个市场 session 展示，缺日线／预测／小时线保留状态行。逐行 source 区分离线重建、历史回填和 live 留档；旧 live 标签不等于有效发布时间证明。

### 收益率：先声明分母，否则是个无意义的数

绝对盈亏会误导——两支标的盈亏金额相近，但一支为此周转的成交额是另一支的数倍，赚钱效率能差出一个量级，光看绝对数完全看不出来。

但这个策略**没有天然的本金**：不预设初始现金，净持仓还会单向漂移（可为负 = 裸空）。所以「收益率」必须先说清分母。页面给四个各自回答不同问题的口径（`strategy.compute_returns`，纯函数）：

| 口径 | 分母 | 回答什么问题 |
| --- | --- | --- |
| **成交额收益率** | 平均单边成交额 `(买入额+卖出额)/2` | 相对成交周转规模的毛损益；不同持仓风险、费用与时段仍不能直接比较 |
| **占款收益率** | 峰值占款 `max(\|净持仓\|×当日收盘)` | 「压上的钱回报几何」。最接近直觉的「本金收益率」，但**对单日极端仓位敏感** |
| **现金收益率** | 逐日累计现金余额最低点的绝对值 | 现有日终净额口径；可能遗漏盘中先买后卖占款，不是实际所需本金的完整计算 |
| **线性年化** | 占款收益率 × 252/天数 | 仅供横向比较量级 |

现有指标的三项处理及限制：

- **分母为 0 → 显示「—」而非 0.00%**：「没有本金可言」和「本金收益为零」是两回事。纯裸空策略现金分母就是 0（现金只进不出、一分钱没垫过），此时给个漂亮数字是骗人。
- **现金口径取日终余额最低点、不是期末余额**：可以描述跨日现金净流出，但当天先买后卖的峰值可能被净额抵消。升级引擎按事件及资源预留计算；裸空现金流入也不能说明交易无需资本或抵押。
- **年化是线性、非复利**：样本仅数十天，复利年化会把噪声放大成荒谬的数字。年化只挂在占款口径上，避免同一个数字因新增指标而漂移。

**组合汇总按币种分组**，USD／HKD 不相加。现有分母按各股指标求和，峰值发生日期可能不同；它不是共享现金池的实际资金需求，不直接解释为真实账户收益率。

> **已知局限**：2026-09-04 调研中，六股各自最近 30 个可用预测日均没有双边同时触达，结果可能含负库存与期末未平仓。当前模拟未落实现金／可售库存约束，也未扣佣金、税费、融券成本及滑点，不能当作人类已实现净收益。不同窗口的结果需共同展示，不能挑收益最好的窗口当验证成绩。

### v2 研究页面 `/ml-next`

复用全站主题，提供 20/60/120 session 回放及本金、库存、lot/tick、费用等参数。默认展示已标注的历史重建；下一目标日预测卡片只读有效 live，所以历史完整仍可能显示缺 live。API 为 `/api/ml/v2/{latest,review,compare}`，仍为只读计算；真实订单事实通过 `review?selected=日期` 返回，没有独立 facts 路由。证券规则尚未自动接入页面，港股 lot 需核验并填写，不能统一假设 100 股。有效预测保存在版本表，离线重建不能当作前向 shadow。

## 2.7 ML 侧测试

```bash
conda activate mk && python -m pytest tests/ -q
```

纯函数测试优先，多数使用合成数据；少数测试会读取本地 ML 库并准备数据或训练，需在隔离副本运行：

| 测试 | 覆盖 |
| --- | --- |
| `test_ml_calibrator.py` | CQR non-conformity score、校准分位、扩展 / 收紧两个方向 |
| `test_ml_cv.py` | Purged / Embargo 切分的边界与隔离带宽度 |
| `test_ml_signal_eval.py` | IC / RankIC / ICIR，含空序列与常数序列退化 |
| `test_ml_simulator.py` | 1h 限价撮合（先触低 / 先触高、未成交、跨 bar） |
| `test_ml_policy.py` | S0 规则与 LinUCB（含 ε 探索） |
| `test_ml_strategy.py` | 挂单回溯的手数分档、现金流与净持仓折算；四个收益率口径（含分母为 0 返回 None、现金最低点非期末余额、纯裸空、按币种组合汇总） |
| `test_ml_review.py` | 命中判定、按已有日线对齐次日、上破／下破／双破分类；正式市场日历见 sessions 测试 |
| `test_ml_fetch.py` | 增量窗按表独立挑档、脏 OHLC 行拦截 |
| `test_ml_nan_guard.py` | NaN 三层防护回归（移除第 ② 层即 FAIL，证明是真守卫） |
| `test_ml_offline_rl.py` | 离线 RL 数据构造（无 GPU 时跳过训练） |

---

# 三、通用

## 3.1 目录结构

```
myStock/
├── README.md
├── environment.yml                 # conda 环境（mk）
├── config.example.yaml             # 配置模板
├── config.yaml                     # 真实配置（.gitignore）
├── data/
│   ├── mystock.db                  # Web 生产库（运行时生成，.gitignore）
│   └── ml/
│       ├── mystock_ml.db           # ML 独立库（.gitignore）
│       ├── runs/<run-id>/          # 私有 input.db + manifest，当前逐次完整冻结
│       ├── receipts/               # train 回执与独立 data 回执
│       └── reports/runs/<run-id>/   # 本地 HTML 报告；latest.html 为副本
├── scripts/
│   ├── {init,update,server}.sh     # Web：初始化 / 增量 / 起服务
│   └── ml.sh                       # ML：data|train|publish|all 统一入口
│       ml_{setup,sync}_h20.sh, ml_vllm.sh   # GPU（仅离线 RL 需要）
├── mystock/
│   ├── config.py  db.py  code_map.py  pnl.py  schema.sql
│   ├── collectors/{futu_client,yf_client}.py
│   ├── pipelines/{init_load,update_load,maintenance}.py
│   ├── web/{app.py, templates/, static/}
│   └── ml/{fetch,data,features,predictor,calibrator,cv,signal_eval,
│           simulator,calibrate,policy,backtest,report,review,
│           backfill,strategy,offline_rl}.py + schema.sql
├── docs/                           # 数据字典 / ML 方案 / 决策记录
└── tests/                          # 单元测试（Web + ML）
```

主要文档：

| 文档 | 内容 |
| --- | --- |
| [docs 文档索引](docs/README.md) | 当前接手／运行说明、升级证据链与历史研究导航 |
| [`docs/COLLABORATION.md`](docs/COLLABORATION.md) | **Codex × Claude 协作约定**：docs/ + git 为唯一信道、一轮的形状、文档命名与边界声明 |
| [`docs/OPEN_ITEMS.md`](docs/OPEN_ITEMS.md) | **未尽事项清单**：跨轮次的唯一待办来源，开新工单前先读 |
| [`docs/DATA.md`](docs/DATA.md) | 数据字典：全部表字段、取值特征、已知坑点 |
| [`docs/ML_UPGRADE_WORK_ORDER_2026-09-04.md`](docs/ML_UPGRADE_WORK_ORDER_2026-09-04.md) | 已完成的 Astra 原始工单：四批任务、文件范围、验证、数据隔离与交付边界 |
| [`docs/ML_PRE_UPGRADE_BACKUP_2026-09-04.md`](docs/ML_PRE_UPGRADE_BACKUP_2026-09-04.md) | 升级前备份位置、覆盖范围、恢复验证和回滚原则 |
| [`docs/ML_CODEX_UPGRADE_PLAN_2026-09-04.md`](docs/ML_CODEX_UPGRADE_PLAN_2026-09-04.md) | Codex v1.4：业务目标、证据、模型／回溯方案及 API 特征调研 |
| [`docs/ML_CLAUDE_UPGRADE_MERGED.md`](docs/ML_CLAUDE_UPGRADE_MERGED.md) | Claude 合并讨论与逐轮回应，近期人工触发等决策记录 |
| [`docs/ML_UPGRADE_PLAN.md`](docs/ML_UPGRADE_PLAN.md) | Claude 原始升级方案与探索性实验记录 |
| [`scripts/ml_experiments/README.md`](scripts/ml_experiments/README.md) | 原始实验 A／B 的运行入口与已知限制 |
| [`docs/ML_OVERVIEW.md`](docs/ML_OVERVIEW.md) | ML 一页速览：思路 + 数据 + 实验结论 |
| [`docs/ML_PLAN.md`](docs/ML_PLAN.md) | ML 完整方案与决策记录（历史文档，保留决策脉络） |
| [`docs/ML_ALGORITHM_PROPOSAL.md`](docs/ML_ALGORITHM_PROPOSAL.md) | 候选算法改进清单（分档提案） |
| [`docs/ML_QLIB_BORROW_PLAN.md`](docs/ML_QLIB_BORROW_PLAN.md) | 借鉴 qlib 的评估地基落地计划 |
| [`docs/ML_TIER1_ROBUSTNESS.md`](docs/ML_TIER1_ROBUSTNESS.md) | Tier1 稳健性检验与**移除决策记录**（诚实负结果） |

---

## 3.2 测试

```bash
conda activate mk
python -m pytest tests/ -q
```

测试需在 `mk` 环境（base python 无 yfinance 会报 YFError）。测试数随分支变化，以实际 pytest 收集为准；`test_ml_offline_rl.py` 和 `test_backtest_survives_nan_close` 会在本地库存在时读取数据并准备／拟合模型。完整验证应在执行工单规定的隔离副本中运行，不把“无需联网”理解为“不依赖本地数据”。

排除上述真实库用例的快速回归：

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ml_offline_rl.py -k 'not backtest_survives_nan_close'
```

**Web 侧**：

- `tests/test_code_map.py`：代码映射（港股 4 位规整、美股、往返一致）。
- `tests/test_pnl.py`：交易盈亏（移动平均成本、成本兜底、超卖未覆盖、乱序处理）、单股复盘（FIFO 配对、胜率 / 盈亏比、持有天数）与年度财务统计。
- `tests/test_db.py`：UPSERT 幂等、当天行情覆盖、持仓快照覆盖、跳过名单重置、代码彻底清除（`purge`）。
- `tests/test_yf_client.py`：yfinance `history` 的 `end` 排他修正与限频退避。
- `tests/test_futu_funds.py`：账户资金规整与盘面快照字段提取。
- `tests/test_capital_flow.py`：资金流向规整与 UPSERT 幂等 / `purge` 清理。

**ML 侧**：见 [§2.7](#27-ml-侧测试)。

前端无构建工具，改动后用 `node --check mystock/web/static/app.js` 做语法检查。

---

## 3.3 安全 / 隐私（提交前务必检查）

仓库为**公开**仓库。以下已在 `.gitignore` 且**绝不可提交**：

- `config.yaml`（含交易密码）、`config.*.local.yaml`
- `data/`、`*.db*`（真实持仓 / 交易数据，含 ML 库与报告归档）

交易密码只走环境变量 `MYSTOCK_FUTU_TRADE_PWD` 或 `config.yaml`，**绝不硬编码、绝不进 git**。提交前确认 diff 与新文件无密钥 / token / 密码。

> ML 每日报告含真实交易信息；`ml.sh publish` 会把它推到公网服务器，请自行确认可见范围。

---

## 3.4 实现注意事项

- **幂等与覆盖**：生产可变事实按主键更新，持仓快照可替换；ML 新版本按 run 追加，内容与历史不可覆盖／删除。
- **失败可恢复**：生产采集的单标的失败记 `sync_log` 后继续；ML 的任一标的 empty/error 仍使 data 非零退出，阻断 all 的训练／发布。
- **富途限频**：历史订单 / 成交接口限频「每 30 秒最多 10 次」。采集时**按时间窗口分段**（默认 80 天/窗口）查询，窗口间主动间隔降速；命中限频自动退避重试（见 [`mystock/collectors/futu_client.py`](mystock/collectors/futu_client.py)）。
- **yfinance 限频与噪音抑制**：抓取带重试与退避；对**连续抓不到数据的标的**（如退市股）计数，达阈值（`SKIP_THRESHOLD`=5）后写入 `quote_skiplist` 表并在后续运行中直接跳过，避免无效请求与库的退市警告噪音。**注意**：进入名单的代码不再请求 → 无法自愈；若一次限频 / 网络抖动批量抓空造成误伤（真实持仓被跳过），手动重置：`python -m mystock.pipelines.maintenance reset-skiplist [代码...]`（不带参数清空全部）。退市清仓且不再关注的代码可用 `python -m mystock.pipelines.maintenance purge <代码>` 从所有表删除（**不可逆**，会改历史盈亏）。
- **yfinance `end` 排他**：`Ticker().history(start, end)` 的 `end` 为**排他**（返回严格早于 `end` 的 bar），若传 `end=今天` 会漏掉当天。日线与汇率抓取统一经 `_end_inclusive`（end+1 天）修正，确保抓到"当天"这根 bar。
- **时区**：富途时间字段按字符串原样存储，必要时在展示层处理。
- **不丢数据**：富途订单/成交原始记录保留进 `raw_json` 字段。

## 3.5 常见问题

- **页面无数据 / `/api/*` 返回 503**：先核对实际库路径与 schema；全新安装才运行 `init.sh`，已有运行库先备份并在副本验证迁移。
- **富途数据抓取失败**：确认 OpenD 已启动并登录、端口与 `config.yaml` 一致；查 `sync_log` 表的 `error` 记录。
- **历史成交为空**：确认 `trd_env: REAL`（成交接口仅支持实盘）。
- **某些股票没有行情**：退市 / yfinance 无数据的标的会进入 `quote_skiplist` 跳过名单，属正常；个股详情页会提示「行情数据不足」，不影响其交易记录展示。
- **`init.sh` / `update.sh` 可重复执行**：写库幂等，重复运行不会产生重复数据；当天数据按覆盖处理。
- **ML 页面返回 503**：检查 ML 库路径、schema 和数据；新库按部署次序初始化并 `data`。缺库可隔离为接口错误，但依赖缺失不同：Web 顶层加载 ML API → service → pandas/numpy，缺这些包可能让整个 Web 无法启动，应先安装环境依赖。
- **ML 报告某列显示「—」**：可能为数据不足、非有限值或指标分母不可用；应结合输入日期和日志排查，不能只凭显示「—」认定整条管线数据有效。
- **升级页面入口**：工程已合入 main 并部署，打开 <http://127.0.0.1:8888/ml-next/>；若仍看到旧页，核对服务目录、提交和浏览器缓存。部署验收见 [回执](docs/ML_DEPLOYMENT_2026-09-05.md)。

---

## 参考资料

- 富途 · 查询历史订单：<https://openapi.futunn.com/futu-api-doc/trade/get-history-order-list.html>
- 富途 · 查询历史成交：<https://openapi.futunn.com/futu-api-doc/trade/get-history-order-fill-list.html>
- yfinance 指南：<https://algotrading101.com/learn/yfinance-guide/>
- Lightweight-Charts（价格走势图，已 vendored）：<https://github.com/tradingview/lightweight-charts>
- CQR（Conformalized Quantile Regression, Romano et al. 2019）：<https://arxiv.org/abs/1905.03222>
