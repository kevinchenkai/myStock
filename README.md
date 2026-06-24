# myStock

个人 **港股 / 美股** 持仓、交易、行情的**本地化**数据系统。

把分散在 **富途**（持仓 / 历史订单 / 历史成交）和 **yfinance**（每日行情）的数据，统一抓取并存入本地 SQLite，再通过本地 Web 页面查询、下钻到单支股票。

- 一处汇总：持仓、历史订单、历史成交、每日行情。
- 数据本地化：抓取一次后离线可查，支持增量更新。
- 可视化查询：浏览器查看持仓 / 交易，点击股票下钻到通用信息、日线走势与个股交易明细。
- 现代化界面：支持「跟随系统 / 浅色 / 深色」三态主题切换（记忆选择、无闪烁）。

> 单用户、单机、无需登录鉴权。市场范围：**仅 HK 与 US**。

---

## 1. 架构与数据流

```
富途 OpenD（本地网关 127.0.0.1:11111）──┐
                                       │ futu-api
yfinance（行情）──► 采集层 collectors ──┴──► SQLite (data/mystock.db) ──► Web 服务 (Flask, :8888)
```

- 采集层从富途 OpenD 与 yfinance 拉数据，清洗后写入 SQLite。
- **Web 层只读 SQLite**，不直接调用富途 / yfinance，保证页面快、可离线。

六张数据表：`positions`（持仓快照）、`orders`（历史订单）、`deals`（历史成交）、`daily_quotes`（日线行情）、`stock_profiles`（股票通用信息：公司/估值，随每日更新刷新）、`fx_rates`（外汇日线，当前为美元兑人民币 USDCNY），外加 `sync_log`（同步日志）与 `quote_skiplist`（行情跳过名单）。详见 [`mystock/schema.sql`](mystock/schema.sql)。

---

## 2. 前置条件（重要）

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

## 3. 安装与配置

### 3.1 创建 conda 环境

```bash
conda env create -f environment.yml   # 创建名为 mk 的环境
conda activate mk
```

依赖：`futu-api`、`yfinance`、`Flask`、`PyYAML`、`pandas`（见 [`environment.yml`](environment.yml)）。

### 3.2 配置文件

```bash
cp config.example.yaml config.yaml    # config.yaml 已在 .gitignore，不会被提交
```

按需修改 `config.yaml`：富途端口、`trd_env`、抓取起始日期、市场、数据库路径、Web 端口等。
交易密码建议走环境变量 `MYSTOCK_FUTU_TRADE_PWD`（优先级高于 `config.yaml`）。

> 未提供 `config.yaml` 时程序会回退使用 `config.example.yaml` 的默认值并给出提示。

---

## 4. 使用（脚本顺序：init → update → server）

所有脚本会自动 `conda activate mk`，并在缺少 conda / 环境 / 数据库时给出清晰报错。

| 脚本 | 作用 |
| --- | --- |
| `bash scripts/init.sh` | **首次初始化**：建/更新环境、装依赖、建库建表、全量抓取（富途持仓/订单/成交 + yfinance 日线，2025-01-01 至今），写 `sync_log`。幂等，可重复执行。 |
| `bash scripts/update.sh` | **增量更新**（手动按需执行）：读取上次同步点，抓取至今的新数据；**当天数据按覆盖处理**（持仓快照覆盖当天、行情覆盖当天、订单/成交按主键 UPSERT）；并刷新 `stock_profiles` 通用信息与 `fx_rates` 美元汇率。 |
| `bash scripts/server.sh` | **启动 Web 服务**（`http://localhost:8888`），仅读数据库，不触发抓取。 |

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

## 5. Web 页面

打开 `http://localhost:8888`：

- **我的持仓**：当前持仓（代码、名称、市场、数量、可卖、成本价、市价、市值、浮动盈亏、盈亏比例、币种）。
- **我的交易**：历史订单 / 成交，子 Tab「按订单 / 按成交」切换。订单含**全部状态**（未成交 / 部分成交 / 全部成交 / 撤单 / 失败等）。
- **交易盈亏**：按**实际成交数据**计算每只股票的**已实现盈亏**（移动平均成本法）。卖出时结算 `(卖出价 − 当时平均成本) × 卖出量`；早于抓取起点的建仓买入不在库中时，用持仓快照的 `cost_price` 兜底（成本缺失或为负则该笔卖出不计入，并在合计栏提示"⚠ N 股卖出无成本基准"）。合计**按币种分别汇总**（HKD / USD 不混算）。**点击代码弹出「交易复盘」浮窗**。
- **交易复盘**（点击盈亏 Tab 的代码弹出浮窗）：聚焦该股的**成单交易明细**，并给出客观的**交易行为复盘**——
  - 指标卡：已实现盈亏、已平仓回合数、胜率、盈亏比、成交笔数、买/卖均价、平均持有天数、净持仓等；
  - **已平仓回合**：FIFO 配对的「买入 → 卖出」回合，含持有天数与单回合盈亏/盈亏率；
  - **盈亏分析总结**：基于历史成交的事实陈述（胜率、盈亏比、平均盈亏、持有时长、最大盈/亏回合、数据完整性提示等）。**仅复盘交易行为，不构成任何投资建议或买卖推荐**。
- **市场筛选**：「我的持仓」「我的交易」「交易盈亏」面板顶部均有 **全部 / 美股 / 港股** 筛选条，点击即时过滤（纯前端，不重新请求后端）。交易 Tab 的筛选对「按订单 / 按成交」两个子表同时生效。
- **表头排序（持仓 / 交易盈亏）**：点击**数值列表头**排序，**循环切换**：倒序 ▼ → 正序 ▲ → 取消（恢复原始顺序）。可与市场筛选叠加；空值恒排末尾。
- **单支股票下钻**：在任意表格中**点击代码**，弹出个股详情：
  - **通用信息**（置顶）：公司名、板块、行业、交易所、市值、流通股本、市盈率(TTM)/预期市盈率、市净率、每股收益、股息率、Beta、目标均价、分析师评级、货币、官网。读自 `stock_profiles` 表（随每日更新刷新）；**市值 / 目标均价 / 每股收益按标的本币标注单位**（如港股显示「市值(百万HKD)」），不误标美元；
  - **价格走势（K线）**：蜡烛图 + 成交量副图（红涨绿跌、十字光标、滚轮缩放 / 拖动），基于本地内置的 [Lightweight-Charts](https://github.com/tradingview/lightweight-charts)（vendored 于 `static/vendor/`，离线可用，前端仍无构建步骤）+ 日线表格；
  - 该股票的订单与成交明细。
- **美元汇率**：单独的 Tab，展示**美元兑人民币（USDCNY）**基本信息（最新汇率、区间高/低、区间涨跌、数据区间、交易日数）与**汇率趋势折线图**。数据来自 yfinance（`CNY=X`），从 2025-01-01 起按天入库、随 `update.sh` 例行更新。趋势图用**中性配色**（汇率涨跌语义中性，不套红涨绿跌）。
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
| `GET /api/fx?pair=USDCNY` | 外汇日线（默认美元兑人民币 USDCNY） |
| `GET /api/stock/<code>/analysis` | 单股交易复盘：成交明细 + FIFO 回合 + 复盘统计 + 客观观察 |
| `GET /api/quotes?code=&start=&end=` | 某代码日线 |
| `GET /api/stock/<code>` | 聚合：该股票行情 + 订单 + 成交 |
| `GET /api/stock/<code>/profile` | 该股票通用信息（公司 / 估值，读自 `stock_profiles`） |

---

## 6. 代码映射（富途 ↔ yfinance）

| 市场 | 富途 | yfinance | 规则 |
| --- | --- | --- | --- |
| 港股 | `HK.00700` | `0700.HK` | 去 `HK.` → 数字规整为 4 位 → 加 `.HK` |
| 美股 | `US.AAPL` | `AAPL` | 去 `US.`，直接用 ticker |

纯函数实现于 [`mystock/code_map.py`](mystock/code_map.py)，含单元测试（HK/US 均覆盖）。

---

## 7. 测试

```bash
conda activate mk
python -m pytest -q
```

- `tests/test_code_map.py`：代码映射（港股 4 位规整、美股、往返一致）。
- `tests/test_pnl.py`：交易盈亏（移动平均成本、成本兜底、超卖未覆盖、乱序处理）与单股复盘（FIFO 配对、胜率/盈亏比、持有天数、客观观察等）。
- `tests/test_db.py`：UPSERT 幂等、当天行情覆盖、持仓快照覆盖、代码全集去重。

---

## 8. 目录结构

```
myStock/
├── README.md
├── environment.yml            # conda 环境
├── config.example.yaml        # 配置模板
├── config.yaml                # 真实配置（.gitignore）
├── data/mystock.db            # SQLite（运行时生成，.gitignore）
├── scripts/{init,update,server}.sh
├── mystock/
│   ├── config.py  db.py  code_map.py  pnl.py  schema.sql
│   ├── collectors/{futu_client,yf_client}.py
│   ├── pipelines/{init_load,update_load}.py
│   └── web/{app.py, templates/, static/}
└── tests/                     # code_map / pnl 单元测试
```

---

## 9. 实现注意事项

- **幂等与覆盖**：所有写库用 UPSERT；当天可变数据（持仓快照、行情）以覆盖为准。
- **失败可恢复**：单个标的行情抓取失败不中断整体流程，记录到 `sync_log` 后继续。
- **富途限频**：历史订单 / 成交接口限频「每 30 秒最多 10 次」。采集时**按时间窗口分段**（默认 80 天/窗口）查询，窗口间主动间隔降速；命中限频自动退避重试（见 [`mystock/collectors/futu_client.py`](mystock/collectors/futu_client.py)）。
- **yfinance 限频与噪音抑制**：抓取带重试与退避；对**连续抓不到数据的标的**（如退市股）计数，达阈值后写入 `quote_skiplist` 表并在后续运行中直接跳过，避免无效请求与库的退市警告噪音；若该标的日后恢复有数据会自动移出名单。
- **时区**：富途时间字段按字符串原样存储，必要时在展示层处理。
- **不丢数据**：富途订单/成交原始记录保留进 `raw_json` 字段。

## 10. 常见问题

- **页面无数据 / `/api/*` 返回 503**：数据库不存在，请先 `bash scripts/init.sh`。
- **富途数据抓取失败**：确认 OpenD 已启动并登录、端口与 `config.yaml` 一致；查 `sync_log` 表的 `error` 记录。
- **历史成交为空**：确认 `trd_env: REAL`（成交接口仅支持实盘）。
- **某些股票没有行情**：退市 / yfinance 无数据的标的会进入 `quote_skiplist` 跳过名单，属正常；个股详情页会提示「行情数据不足」，不影响其交易记录展示。
- **`init.sh` / `update.sh` 可重复执行**：写库幂等，重复运行不会产生重复数据；当天数据按覆盖处理。

---

## 参考资料

- 富途 · 查询历史订单：<https://openapi.futunn.com/futu-api-doc/trade/get-history-order-list.html>
- 富途 · 查询历史成交：<https://openapi.futunn.com/futu-api-doc/trade/get-history-order-fill-list.html>
- yfinance 指南：<https://algotrading101.com/learn/yfinance-guide/>
