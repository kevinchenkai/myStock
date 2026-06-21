# myStock v1.0 需求文档 / Claude Code、Codex 实现说明

> 文档版本：v1.0  
> 目标读者：Claude Code / Codex / 本地开发者  
> 项目类型：本地只读股票持仓、交易、行情数据同步与查询 Web 工具  
> 技术栈：Python 3、Futu OpenAPI、yfinance、SQLite、FastAPI + Jinja2、Shell scripts、conda `mk` 环境

---

## 0. 给 Claude Code / Codex 的执行指令

你是本项目的实现 Agent。请基于本需求文档在当前代码仓库中实现 `myStock` 第一版。实现前必须先检查仓库中是否存在 `demo/` 目录：

- 若存在 `demo/`，先阅读其中示例代码、配置方式、Futu 连接方式、已有脚本，再决定复用或改造。
- 若不存在 `demo/`，不要阻塞实现，按本需求从零搭建项目结构。
- 本项目必须是**只读查询工具**，禁止实现或调用任何下单、改单、撤单逻辑。
- 所有同步脚本必须可重复执行，不能因为重复运行产生重复数据。
- 所有敏感信息、账户 ID、OpenD host/port、交易环境等必须通过 `.env` 或配置文件读取，不能硬编码。

---

## 1. 项目背景与目标

`myStock` 第一版用于在本地汇总并查询个人股票数据：

1. 通过 Futu API 获取当前港股、美股持仓。
2. 通过 Futu API 获取从 `2025-01-01` 到运行当天的历史下单记录，包含未成交、已成交、撤单、失败等订单状态。
3. 通过 Futu API 获取从 `2025-01-01` 到运行当天的历史成交记录，用于与订单记录关联。
4. 通过 yfinance SDK 获取相关股票从 `2025-01-01` 到运行当天的日线行情。
5. 将所有历史数据保存到本地 SQLite 数据库。
6. 提供本地 Web 查询页面，访问 `http://localhost:8888` 后可查看持仓、交易、单股行情与单股交易记录。

第一版优先保证数据同步、可查询、可重复执行和本地稳定运行，不做交易决策、不做自动下单、不做云端部署。

---

## 2. 参考资料

实现时参考以下资料，并以官方 Futu 文档为交易数据字段与接口行为依据：

- Futu 查询持仓：`position_list_query(...)`
- Futu 查询历史订单：`history_order_list_query(...)`
- Futu 查询历史成交：`history_deal_list_query(...)`
- yfinance 历史行情：`Ticker.history(...)` 或 `yf.download(...)`
- 仓库内示例代码：`demo/`

---

## 3. 范围定义

### 3.1 范围内

- 当前持仓同步：HK、US 两个市场。
- 历史订单同步：从 `2025-01-01 00:00:00` 到运行当天。
- 历史成交同步：从 `2025-01-01 00:00:00` 到运行当天。
- 日线行情同步：从 `2025-01-01` 到运行当天，覆盖当前持仓股票和历史交易中出现过的股票。
- 本地 SQLite 存储。
- 本地 Web UI：持仓列表、交易列表、单股详情页。
- 本地脚本：`init.sh`、`update.sh`、`server.sh`。
- 涨跌显示遵循中国/港股常见习惯：**红色表示上涨或盈利，绿色表示下跌或亏损**。

### 3.2 范围外

- 自动交易、下单、撤单、改单。
- 资产归因、收益率精算、税费精算。
- 多用户登录、权限系统、公网部署。
- 实时行情推送、WebSocket 交易推送。
- 移动端 App。
- 生产级风控或投资建议。

---

## 4. 关键假设与约束

1. 用户本地已安装并运行 Futu OpenD，且 OpenD 已能访问用户真实交易账户。
2. 程序默认连接 `127.0.0.1:11111`，但必须支持配置覆盖。
3. 交易账户建议通过 `FUTU_ACC_ID` 指定，不建议依赖 `acc_index`，因为账户序号可能随开户/销户变化。
4. 历史成交接口仅支持真实交易环境；如用户配置模拟环境，需要给出清晰错误提示。
5. yfinance 属于 Yahoo Finance 非官方数据来源，可能遇到限流、字段变化或个别股票数据缺失；程序必须缓存、重试并记录失败，而不是静默丢失。
6. 港股 Futu 代码与 yfinance 代码需要转换：
   - `US.AAPL` -> `AAPL`
   - `HK.00700` -> `0700.HK`
   - `HK.01810` -> `1810.HK`
   - 港股代码必须保留 4 或 5 位必要前导零，具体以 Yahoo Finance 可识别 ticker 为准。

---

## 5. 功能需求

### FR-001 当前持仓同步

系统必须通过 Futu API 获取当前 HK、US 持仓。

实现要求：

- 对配置中的市场列表分别调用 Futu 交易上下文。
- 推荐调用：`position_list_query(position_market=TrdMarket.HK/US, trd_env=TrdEnv.REAL, acc_id=...)`。
- 保存每次同步的持仓快照，不覆盖历史快照。
- 每个同步日同一账户、同一市场、同一股票、同一持仓方向只保留一条最新快照，可通过 upsert 覆盖当天数据。
- 至少保存以下字段：
  - `acc_id`
  - `market`
  - `code`
  - `stock_name`
  - `position_side`
  - `qty`
  - `can_sell_qty`
  - `currency`
  - `nominal_price`
  - `cost_price`
  - `average_cost`
  - `diluted_cost`
  - `market_val`
  - `pl_ratio`
  - `pl_val`
  - `today_pl_val`
  - `unrealized_pl`
  - `realized_pl`
  - `fetched_at`
  - `raw_json`

验收标准：

- `bash init.sh` 后，数据库中能看到当天 HK、US 持仓快照。
- `bash update.sh` 重复执行不会产生重复持仓记录。
- Web 页面“我的持仓”Tab 能展示最新持仓。

---

### FR-002 历史订单同步

系统必须通过 Futu API 获取从 `2025-01-01` 到运行当天的历史订单列表。

实现要求：

- 推荐调用：`history_order_list_query(status_filter_list=[], code='', order_market=TrdMarket.HK/US, start=..., end=..., trd_env=TrdEnv.REAL, acc_id=...)`。
- `status_filter_list=[]` 表示不按状态过滤，保存所有订单状态。
- 订单状态必须原样保存，例如已成交、部分成交、未成交、已撤单、失败等，不要自行丢弃。
- 使用 `order_id` 作为主键进行 upsert。
- 每次同步应覆盖已有订单的最新状态，因为订单可能从未成交变为成交、撤单或失败。
- 当同步跨度较长时，应按日期窗口拆分请求，建议每个窗口不超过 90 天，避免 API 默认时间行为导致数据遗漏。
- 至少保存以下字段：
  - `order_id`
  - `acc_id`
  - `market`
  - `code`
  - `stock_name`
  - `trd_side`
  - `order_type`
  - `order_status`
  - `qty`
  - `price`
  - `currency`
  - `create_time`
  - `updated_time`
  - `dealt_qty`
  - `dealt_avg_price`
  - `last_err_msg`
  - `remark`
  - `time_in_force`
  - `fill_outside_rth`
  - `session`
  - `amount`
  - `fetched_at`
  - `raw_json`

验收标准：

- 数据库中包含从 `2025-01-01` 至运行日的 HK、US 历史订单。
- 未成交订单和已成交订单都能在“我的交易”Tab 中看到。
- 订单与成交可以通过 `order_id` 关联。

---

### FR-003 历史成交同步

系统必须通过 Futu API 获取从 `2025-01-01` 到运行当天的历史成交列表。

实现要求：

- 推荐调用：`history_deal_list_query(code='', deal_market=TrdMarket.HK/US, start=..., end=..., trd_env=TrdEnv.REAL, acc_id=...)`。
- 该接口只支持真实交易环境；如果配置为模拟环境，程序应提示“历史成交仅支持真实交易环境”。
- 使用 `deal_id` 作为主键进行 upsert。
- 使用 `order_id` 与订单表关联。
- 当同步跨度较长时，应按日期窗口拆分请求，建议每个窗口不超过 90 天。
- 至少保存以下字段：
  - `deal_id`
  - `order_id`
  - `acc_id`
  - `market`
  - `code`
  - `stock_name`
  - `trd_side`
  - `qty`
  - `price`
  - `create_time`
  - `status`
  - `counter_broker_id`
  - `counter_broker_name`
  - `fetched_at`
  - `raw_json`

验收标准：

- 数据库中包含从 `2025-01-01` 至运行日的成交记录。
- Web 中查看单支股票时能看到该股票所有成交明细。
- 若某订单没有成交记录，订单仍应显示在订单列表中。

---

### FR-004 yfinance 日线行情同步

系统必须通过 yfinance 获取相关股票从 `2025-01-01` 到运行当天的日线行情。

股票范围：

- 当前持仓股票。
- 从 `2025-01-01` 起历史订单或历史成交中出现过的股票。

实现要求：

- 优先使用 `yf.download(...)` 批量下载，也可以使用 `yf.Ticker(symbol).history(...)` 单票下载。
- 日线参数：`interval='1d'`。
- 建议设置：`auto_adjust=False`、`actions=True`，以尽量保留 `Open/High/Low/Close/Adj Close/Volume/Dividends/Stock Splits` 等常用日线字段。
- yfinance 的 `end` 通常按结束日期前一日返回数据；实现时若希望包含运行当天，应传入“运行当天 + 1 天”作为 `end`。
- 保存字段：
  - `yf_ticker`
  - `code`
  - `market`
  - `trade_date`
  - `open`
  - `high`
  - `low`
  - `close`
  - `adj_close`
  - `volume`
  - `dividends`
  - `stock_splits`
  - `fetched_at`
  - `raw_json`
- 今日或最近交易日行情可能在盘中变化，`update.sh` 必须覆盖最近若干日数据，建议覆盖最近 7 个自然日。
- yfinance 某只股票失败时，应记录错误并继续同步其他股票。

验收标准：

- 数据库中每只相关股票都有从 `2025-01-01` 起的日线数据。
- 单股详情页能展示该股票的历史交易日行情表。
- 重复执行 `update.sh` 不会产生重复行情行。

---

### FR-005 本地数据库

系统必须使用 SQLite 作为本地数据库，默认路径：`data/mystock.sqlite3`。

实现要求：

- 程序启动时自动创建 `data/` 目录。
- 提供幂等 schema 初始化逻辑。
- 所有同步操作使用事务。
- 所有表包含 `fetched_at` 或 `updated_at` 字段。
- 关键表保存 `raw_json`，便于后续排查字段差异。

推荐表结构见第 7 节。

---

### FR-006 Shell 脚本

项目根目录必须提供以下脚本。

#### `init.sh`

第一次初始化环境并拉取历史数据。

职责：

1. 初始化或检查 conda `mk` 环境。
2. 安装 Python 依赖。
3. 初始化 SQLite schema。
4. 从 Futu 获取当前持仓。
5. 从 Futu 获取 `2025-01-01` 到运行当天的历史订单。
6. 从 Futu 获取 `2025-01-01` 到运行当天的历史成交。
7. 从 yfinance 获取相关股票 `2025-01-01` 到运行当天的日线行情。
8. 记录同步结果。

#### `update.sh`

不定期执行，用于增量补充最新数据。

职责：

1. 检查环境与 schema。
2. 获取最新持仓并覆盖当天快照。
3. 增量获取历史订单，建议从最近成功同步时间往前回看 7 天，避免漏掉状态变化。
4. 增量获取历史成交，建议从最近成功同步时间往前回看 7 天。
5. 增量获取 yfinance 行情，覆盖最近 7 个自然日，确保当天或最近交易日更新。

#### `server.sh`

启动本地 Web 服务。

职责：

1. 激活 conda `mk` 环境。
2. 启动本地服务：`127.0.0.1:8888`。
3. 打开 `http://localhost:8888` 后进入 myStock 页面。

推荐命令：

```bash
python -m mystock.web --host 127.0.0.1 --port 8888
```

或：

```bash
uvicorn mystock.web.app:app --host 127.0.0.1 --port 8888
```

验收标准：

- 三个脚本均可在 macOS/Linux shell 中执行。
- 脚本失败时返回非 0 退出码并输出明确错误。
- 重复运行 `init.sh` 或 `update.sh` 不会重复插入相同订单、成交、行情。

---

### FR-007 Web 查询页面

访问 `http://localhost:8888` 后进入 myStock Web 页面。

页面要求：

#### 首页

- 标题：`myStock`
- 显示最近同步时间。
- 提供 Tab：
  - `我的持仓`
  - `我的交易`

#### Tab：我的持仓

展示最新持仓表格。

字段建议：

- 市场
- 代码
- 名称
- 持仓数量
- 可卖数量
- 现价
- 成本价
- 市值
- 今日盈亏
- 总盈亏
- 盈亏比例
- 币种

交互：

- 点击股票代码或名称，进入单股详情页。
- 盈利、上涨、正收益显示红色。
- 亏损、下跌、负收益显示绿色。
- 0 或无数据使用默认颜色。

#### Tab：我的交易

展示订单与成交关联后的交易视图。

字段建议：

- 下单时间
- 更新时间
- 市场
- 代码
- 名称
- 买/卖方向
- 订单类型
- 订单状态
- 下单数量
- 下单价格
- 成交数量
- 成交均价
- 成交金额
- 订单号

筛选条件：

- 市场：全部 / HK / US
- 股票代码
- 订单状态
- 日期范围
- 买卖方向

交互：

- 点击股票代码或名称，进入单股详情页。
- 点击订单号可展开该订单对应成交明细。

#### 单股详情页

路径建议：`/stock/{market}/{code}`。

内容：

1. 股票基本信息：代码、名称、市场、yfinance ticker。
2. 当前持仓摘要：持仓数量、成本、现价、市值、盈亏。
3. 历史交易日行情表：从 `2025-01-01` 起的日线数据。
4. 我的订单记录：该股票所有订单。
5. 我的成交记录：该股票所有成交。

可选增强：

- 显示收盘价折线图。
- 显示买卖点标记。

---

## 6. 非功能需求

### NFR-001 安全性

- 只读查询，不实现交易写操作。
- 不在日志中输出敏感账户信息的完整值，可脱敏显示。
- 不提交 `.env`。
- 默认只监听 `127.0.0.1`，不暴露公网。

### NFR-002 幂等性

- 同步脚本可重复执行。
- 订单按 `order_id` upsert。
- 成交按 `deal_id` upsert。
- 行情按 `(yf_ticker, trade_date)` upsert。
- 持仓按 `(acc_id, snapshot_date, market, code, position_side)` upsert。

### NFR-003 可维护性

- 代码模块化，Futu、yfinance、数据库、Web 层分离。
- 所有 API 调用封装在 client 层。
- 同步逻辑封装在 service 层。
- Web 层只读数据库，不直接调用外部 API。

### NFR-004 可观测性

- 日志写入 `logs/mystock.log`。
- 每次同步写入 `sync_runs` 表。
- API 失败必须记录接口名、市场、日期窗口、错误消息。

### NFR-005 性能

- 第一版数据量较小，SQLite 足够。
- Web 查询应为常见字段建立索引。
- yfinance 支持批量下载时优先批量，否则逐票下载并重试。

---

## 7. 推荐数据库 Schema

```sql
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    message TEXT
);

CREATE TABLE IF NOT EXISTS symbols (
    code TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    yf_ticker TEXT NOT NULL,
    stock_name TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    acc_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    market TEXT NOT NULL,
    code TEXT NOT NULL,
    stock_name TEXT,
    position_side TEXT,
    qty REAL,
    can_sell_qty REAL,
    currency TEXT,
    nominal_price REAL,
    cost_price REAL,
    average_cost REAL,
    diluted_cost REAL,
    market_val REAL,
    pl_ratio REAL,
    pl_val REAL,
    today_pl_val REAL,
    unrealized_pl REAL,
    realized_pl REAL,
    raw_json TEXT,
    PRIMARY KEY (acc_id, snapshot_date, market, code, position_side)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    acc_id TEXT,
    market TEXT NOT NULL,
    code TEXT NOT NULL,
    stock_name TEXT,
    trd_side TEXT,
    order_type TEXT,
    order_status TEXT,
    qty REAL,
    price REAL,
    currency TEXT,
    create_time TEXT,
    updated_time TEXT,
    dealt_qty REAL,
    dealt_avg_price REAL,
    last_err_msg TEXT,
    remark TEXT,
    time_in_force TEXT,
    fill_outside_rth TEXT,
    session TEXT,
    amount REAL,
    raw_json TEXT,
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_code_time ON orders (market, code, create_time);
CREATE INDEX IF NOT EXISTS idx_orders_status_time ON orders (order_status, create_time);

CREATE TABLE IF NOT EXISTS deals (
    deal_id TEXT PRIMARY KEY,
    order_id TEXT,
    acc_id TEXT,
    market TEXT NOT NULL,
    code TEXT NOT NULL,
    stock_name TEXT,
    trd_side TEXT,
    qty REAL,
    price REAL,
    create_time TEXT,
    status TEXT,
    counter_broker_id TEXT,
    counter_broker_name TEXT,
    raw_json TEXT,
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

CREATE INDEX IF NOT EXISTS idx_deals_code_time ON deals (market, code, create_time);
CREATE INDEX IF NOT EXISTS idx_deals_order_id ON deals (order_id);

CREATE TABLE IF NOT EXISTS daily_prices (
    yf_ticker TEXT NOT NULL,
    code TEXT NOT NULL,
    market TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    adj_close REAL,
    volume INTEGER,
    dividends REAL,
    stock_splits REAL,
    fetched_at TEXT NOT NULL,
    raw_json TEXT,
    PRIMARY KEY (yf_ticker, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_prices_code_date ON daily_prices (market, code, trade_date);
```

---

## 8. 推荐项目结构

```text
myStock/
  README.md
  environment.yml
  pyproject.toml
  .env.example
  init.sh
  update.sh
  server.sh
  demo/
  data/
  logs/
  src/
    mystock/
      __init__.py
      config.py
      logging_config.py
      db.py
      schema.sql
      futu_client.py
      yfinance_client.py
      symbol_mapper.py
      sync_service.py
      cli.py
      web/
        __init__.py
        app.py
        routes.py
        templates/
          base.html
          index.html
          stock_detail.html
        static/
          app.css
          app.js
  tests/
    test_symbol_mapper.py
    test_date_windows.py
    test_db_upsert.py
    test_sync_service.py
```

---

## 9. 配置要求

提供 `.env.example`：

```bash
# Futu OpenD
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_ACC_ID=
FUTU_TRD_ENV=REAL
FUTU_MARKETS=HK,US
FUTU_SECURITY_FIRM=FUTUSECURITIES

# Sync
MYSTOCK_START_DATE=2025-01-01
MYSTOCK_DB_PATH=data/mystock.sqlite3
MYSTOCK_LOG_PATH=logs/mystock.log
MYSTOCK_UPDATE_LOOKBACK_DAYS=7

# Web
MYSTOCK_WEB_HOST=127.0.0.1
MYSTOCK_WEB_PORT=8888
```

说明：

- `FUTU_ACC_ID` 推荐必填。
- 如果用户不填 `FUTU_ACC_ID`，程序可以临时 fallback 到 `acc_index=0`，但必须在日志中提示风险。
- `FUTU_SECURITY_FIRM` 不同账户可能不同，需允许用户自行修改。

---

## 10. 推荐 CLI

实现 Python CLI，供 shell 脚本调用：

```bash
python -m mystock.cli init-db
python -m mystock.cli sync-full
python -m mystock.cli sync-update
python -m mystock.cli serve --host 127.0.0.1 --port 8888
```

CLI 行为：

- `init-db`：创建数据库与表。
- `sync-full`：从 `MYSTOCK_START_DATE` 全量同步持仓、订单、成交、行情。
- `sync-update`：增量同步。
- `serve`：启动 Web。

---

## 11. 数据同步流程

### 11.1 全量初始化流程

```text
init.sh
  -> activate conda env mk
  -> install package/dependencies
  -> init sqlite schema
  -> fetch Futu positions HK/US
  -> fetch Futu orders HK/US from 2025-01-01 to today by date windows
  -> fetch Futu deals HK/US from 2025-01-01 to today by date windows
  -> derive symbol universe from positions + orders + deals
  -> map Futu code to yfinance ticker
  -> fetch yfinance daily prices from 2025-01-01 to today
  -> write sync run result
```

### 11.2 增量更新流程

```text
update.sh
  -> activate conda env mk
  -> init sqlite schema if needed
  -> fetch latest positions HK/US and overwrite today's snapshot
  -> calculate since = max(last_successful_sync_time - lookback_days, 2025-01-01)
  -> fetch orders/deals from since to today by date windows
  -> derive updated symbol universe
  -> fetch yfinance prices from max(last_price_date - lookback_days, 2025-01-01) to today
  -> upsert all data
  -> write sync run result
```

---

## 12. 颜色与展示规则

- 数值 > 0：红色。
- 数值 < 0：绿色。
- 数值 = 0 或 NULL：默认颜色。
- 适用字段：
  - 今日盈亏
  - 总盈亏
  - 盈亏比例
  - 日涨跌额
  - 日涨跌幅
- 买入/卖出方向可以使用文字标签，不强制颜色。

CSS class 建议：

```css
.num-up { color: #d32f2f; }
.num-down { color: #2e7d32; }
.num-flat { color: inherit; }
```

---

## 13. 错误处理要求

### Futu 相关

- OpenD 未启动：提示检查 `FUTU_HOST`、`FUTU_PORT` 和 OpenD 状态。
- 账户 ID 错误：提示检查 `FUTU_ACC_ID`。
- 接口返回 `ret != RET_OK`：抛出带接口名、市场、日期窗口的异常。
- 历史成交在模拟环境不可用：给出明确提示。

### yfinance 相关

- 单票失败：记录错误，继续同步其他股票。
- 字段缺失：缺失字段写 NULL，并在日志中记录。
- 空数据：记录 warning，不视为整个任务失败。

### 数据库相关

- SQLite 文件不可写：提示检查目录权限。
- schema 迁移失败：停止同步并输出错误。

---

## 14. 测试要求

必须提供 pytest 单元测试。

最低测试覆盖：

1. Futu -> yfinance ticker 映射：
   - `US.AAPL` -> `AAPL`
   - `HK.00700` -> `0700.HK`
   - `HK.01810` -> `1810.HK`
2. 日期窗口拆分：
   - `2025-01-01` 到运行日可拆为多个不重叠窗口。
3. SQLite upsert：
   - 同一个 `order_id` 重复写入只更新不重复。
   - 同一个 `deal_id` 重复写入只更新不重复。
   - 同一个 `(yf_ticker, trade_date)` 重复写入只更新不重复。
4. Web 查询：
   - 首页返回 200。
   - 单股详情页返回 200。
5. 颜色逻辑：
   - 正数返回 `num-up`。
   - 负数返回 `num-down`。
   - 0/None 返回 `num-flat`。

---

## 15. 交付物

实现完成后，项目应至少包含：

- `README.md`：安装、配置、运行说明。
- `.env.example`：配置模板。
- `environment.yml` 或 `pyproject.toml`：依赖定义。
- `init.sh`：首次初始化与全量同步。
- `update.sh`：增量同步。
- `server.sh`：启动 Web。
- `src/mystock/...`：核心代码。
- `tests/...`：单元测试。
- `data/`：运行时创建，不提交数据库文件。
- `logs/`：运行时创建，不提交日志文件。

---

## 16. 验收清单

### 初始化验收

- [ ] 执行 `bash init.sh` 成功。
- [ ] 自动创建或检查 conda `mk` 环境。
- [ ] 自动安装依赖。
- [ ] 自动创建 SQLite 数据库。
- [ ] 成功同步当前持仓。
- [ ] 成功同步从 `2025-01-01` 起的历史订单。
- [ ] 成功同步从 `2025-01-01` 起的历史成交。
- [ ] 成功同步相关股票日线行情。

### 更新验收

- [ ] 执行 `bash update.sh` 成功。
- [ ] 重复执行不会插入重复订单、成交、行情。
- [ ] 当天持仓快照会刷新。
- [ ] 最近行情数据会覆盖更新。

### Web 验收

- [ ] 执行 `bash server.sh` 成功。
- [ ] 打开 `http://localhost:8888` 可以进入 myStock 页面。
- [ ] 可以切换“我的持仓”“我的交易”Tab。
- [ ] 点击单支股票可以进入详情页。
- [ ] 详情页可以查看该股票历史交易日行情。
- [ ] 详情页可以查看该股票订单与成交记录。
- [ ] 红色表示上涨/盈利，绿色表示下跌/亏损。

### 安全验收

- [ ] 项目中没有下单、改单、撤单功能。
- [ ] `.env` 不被提交。
- [ ] Web 默认只监听 `127.0.0.1`。
- [ ] 日志不完整输出敏感账户信息。

---

## 17. 推荐实现顺序

1. 创建项目结构、依赖文件、`.env.example`。
2. 实现配置读取与日志。
3. 实现 SQLite schema 与 upsert 工具函数。
4. 实现 Futu client：持仓、历史订单、历史成交。
5. 实现日期窗口拆分工具。
6. 实现 symbol mapper。
7. 实现 yfinance client。
8. 实现 full sync 和 update sync。
9. 实现 CLI。
10. 实现 `init.sh`、`update.sh`、`server.sh`。
11. 实现 Web 页面。
12. 补充测试与 README。

---

## 18. README 运行说明模板

```bash
# 1. 复制配置
cp .env.example .env
# 编辑 .env，填写 FUTU_ACC_ID 等配置

# 2. 确保 Futu OpenD 已启动并可连接

# 3. 首次初始化
bash init.sh

# 4. 后续更新
bash update.sh

# 5. 启动 Web
bash server.sh

# 6. 打开浏览器
open http://localhost:8888
```

---

## 19. 关键实现注意事项

- Futu API 返回 pandas DataFrame 时，统一转换为 dict/list 后写库。
- 日期时间统一保存为 ISO-like 字符串，保留 Futu 原始时间字段。
- yfinance 日期索引需要 reset index 后保存为 `trade_date`。
- `raw_json` 可保存每行原始 dict 的 JSON 字符串。
- 日期窗口必须包含边界，避免遗漏 `2025-01-01` 和运行当天数据。
- 更新最近几天数据时，允许覆盖，避免当天盘中行情或订单状态变化导致数据陈旧。
- Web 层只读数据库，不直接请求 Futu 或 yfinance，避免打开页面时卡住。

