# myStock 需求文档 v1

> 面向 Claude Code 的开发任务说明书。本文档描述「个人港美股持仓 / 交易 / 行情」本地化数据系统的第一版需求。请严格按照本文档实现，遇到歧义时优先参考「附录 A：接口参考」与「附录 B：数据字典」，仍无法确定的请在代码注释中标注 `TODO(confirm)` 并给出默认实现。

---

## 1. 项目概述

myStock 是一个**本地运行**的个人投资数据工具，用于把分散在富途（持仓 / 交易）和 yfinance（行情）的数据，统一抓取并存入本地数据库，并通过一个本地 Web 页面进行查询。

核心价值：
- 一处汇总：持仓、历史订单、历史成交、每日行情。
- 数据本地化：抓取一次后离线可查，支持增量更新。
- 可视化查询：通过浏览器查看持仓 / 交易，并下钻到单支股票。

目标用户：开发者本人（单用户、单机、无需鉴权、无需多账户隔离）。

Github 仓库：
https://github.com/kevinchenkai/myStock

---

## 2. 技术栈与运行环境

| 类别 | 选型 | 说明 |
| --- | --- | --- |
| 语言 | Python 3.10+ | 全部逻辑用 Python 实现 |
| 环境管理 | conda | 通过 `conda` 创建并激活独立环境（环境名建议 `mystock`） |
| 券商数据 | `futu-api`（futu OpenD 网关） | 持仓、历史订单、历史成交 |
| 行情数据 | `yfinance` | 每日 OHLCV 等行情 |
| 数据库 | `sqlite3`（Python 标准库） | 本地单文件数据库 `data/mystock.db` |
| Web 后端 | Flask（推荐，轻量）或 FastAPI | 提供页面与查询 API，监听 `8888` |
| 前端 | 原生 HTML + JS（可用 Jinja2 模板 / 少量 fetch） | 无需引入重型前端框架 |

> 说明：Web 框架默认选 **Flask**；如有更合理理由可改用 FastAPI，但需保持目录结构与接口语义一致。

---

## 3. 系统架构

```
                  ┌─────────────────────────┐
                  │   富途 OpenD（本地网关）   │  ← 需用户自行启动并登录
                  │     默认 127.0.0.1:11111  │
                  └────────────┬────────────┘
                               │ futu-api (Python SDK)
                               ▼
  ┌────────────┐      ┌──────────────────┐      ┌──────────────────┐
  │  yfinance  │────► │   数据采集层        │────► │  SQLite 本地数据库  │
  │ (行情数据)  │      │  collectors/      │      │  data/mystock.db  │
  └────────────┘      └──────────────────┘      └────────┬─────────┘
                                                          │
                                                          ▼
                                              ┌──────────────────────┐
                                              │   Web 服务层 (Flask)   │
                                              │   localhost:8888      │
                                              └──────────────────────┘
```

数据流：
1. 采集层从富途 OpenD 与 yfinance 拉取数据。
2. 统一清洗后写入 SQLite。
3. Web 层只读 SQLite，渲染页面与提供查询接口（**Web 层不直接调用富途 / yfinance**，保证页面响应快、可离线）。

---

## 4. 前置条件与约束（务必在 README 中向用户说明）

1. **富途 OpenD 必须在本机启动并完成登录**。`futu-api` 是通过本地 OpenD 网关（默认 `127.0.0.1:11111`）通信的，程序本身不直接连富途服务器。
2. 富途交易接口需要**解锁交易密码**（部分查询接口可能需要 `unlock_trade`）。交易密码 / 端口等敏感配置通过 `config.yaml` 或环境变量注入，**不得硬编码、不得提交到仓库**。
3. **历史成交接口（`history_deal_list_query`）仅支持实盘 `TrdEnv.REAL`**，模拟环境不支持。
4. 富途历史接口的 `start`/`end` 若不传，单次默认仅返回 90 天范围；抓取 `2025-01-01` 至今需要**显式传入完整时间范围**（必要时按时间窗口分段查询后合并）。
5. 市场范围：**仅 HK 与 US**。
6. 这是单用户本地工具，**不需要登录鉴权**，但 Web 服务应只监听本地（`127.0.0.1` 或 `0.0.0.0` 视需要，默认 `127.0.0.1`）。

---

## 5. 功能需求

### 5.1 获取当前持仓（富途）

- 来源：`OpenSecTradeContext.position_list_query()`。
- 范围：HK + US（按市场分别查询或一次查询后按市场过滤）。
- 入库：写入 `positions` 表（见 §6）。
- 更新策略：持仓为**快照型数据**，每次更新时按抓取时间记录一份快照（`snapshot_date`），便于回看；同一天重复抓取则**覆盖当天快照**。

### 5.2 获取历史订单（富途）

- 来源：`history_order_list_query()`。
- 时间范围：`2025-01-01 00:00:00` 至当前时间。
- 状态：**包含全部状态**（未成交 / 部分成交 / 全部成交 / 已撤单 / 失败等），即 `status_filter_list` 不做过滤（传空列表）。
- 范围：HK + US。
- 入库：写入 `orders` 表。
- 去重：以 `order_id` 为唯一键，`UPSERT`（存在则更新状态等可变字段）。

### 5.3 获取历史成交（富途）

- 来源：`history_deal_list_query()`（仅实盘）。
- 时间范围：`2025-01-01` 至今。
- 范围：HK + US。
- 入库：写入 `deals` 表。
- 去重：以 `deal_id` 为唯一键 `UPSERT`。
- 关联：成交记录通过 `order_id` 关联到 `orders`。

### 5.4 获取每日行情（yfinance）

- 来源：`yfinance`（`yf.Ticker(code).history(...)` 或 `yf.download(...)`）。
- 标的范围：**当前持仓 + 历史交易中出现过的所有股票代码**（即 `positions`、`orders`、`deals` 中去重后的代码全集）。
- 时间范围：`2025-01-01` 至今的**日线**数据。
- 字段（yfinance 日线热门字段，全部入库）：`Open`、`High`、`Low`、`Close`、`Adj Close`、`Volume`、`Dividends`、`Stock Splits`。
- 入库：写入 `daily_quotes` 表，唯一键 `(yf_symbol, date)`，`UPSERT`。
- **代码映射**：富途代码与 yfinance 代码格式不同，必须做转换（见附录 A.3）。

### 5.5 本地数据库存储

- 使用 SQLite，单文件 `data/mystock.db`。
- 首次运行自动建表（`schema.sql` 或 ORM 迁移均可）。
- 所有写入操作需幂等（重复执行不产生重复数据）。
- 维护一张 `sync_log` 表记录每次抓取的时间、数据源、范围、结果，便于增量更新与排查。

### 5.6 脚本（放在仓库根目录或 `scripts/`）

所有脚本需先 `conda activate mystock`（或在脚本内激活），再执行对应 Python 入口。

| 脚本 | 作用 | 行为要求 |
| --- | --- | --- |
| `init.sh` | **首次初始化** | 1) 创建并/或激活 conda 环境、安装依赖；2) 建库建表；3) 全量抓取：富途持仓 / 历史订单 / 历史成交（2025-01-01 至今）+ yfinance 日线（2025-01-01 至今）；4) 写 `sync_log`。可重复执行（幂等）。 |
| `update.sh` | **增量更新**（不定期手动执行） | 1) 读取 `sync_log` 得到上次同步点；2) 抓取自上次同步至今的新数据；3) **当天数据按覆盖处理**（持仓快照覆盖当天、行情覆盖当天、订单/成交按主键 UPSERT）；4) 写 `sync_log`。 |
| `server.sh` | **启动 Web 服务** | 在本地 `8888` 端口启动 Web 服务，提供查询页面。仅读数据库，不触发抓取。 |

> 脚本应有基本的错误提示：例如 OpenD 未启动、未激活环境、数据库不存在时给出清晰报错与处理建议。

### 5.7 Web 查询页面（`http://localhost:8888`）

页面结构：

1. **顶部 Tab 切换**：
   - **我的持仓**：表格展示当前持仓（代码、名称、市场、持仓数量、成本价、市价、市值、浮动盈亏、盈亏比例等）。
   - **我的交易**：表格展示历史订单 / 成交（代码、方向 买/卖、类型、状态、价格、数量、时间）。建议支持按订单 / 按成交两种视角，或在交易 Tab 内再分子 Tab。
2. **单支股票详情（下钻）**：在任意 Tab 中**点击某一支股票**，进入该股票详情页 / 详情面板，展示：
   - **该股票的历史交易日数据**：从 `daily_quotes` 读取该股票的日线（建议同时给出表格 + 一个简单的价格走势图，K 线或收盘价折线均可）。
   - **该股票的我的交易操作**：该股票相关的订单与成交记录（含成交明细）。
3. 查询接口（后端 → 前端）建议提供 JSON API，例如：
   - `GET /api/positions`
   - `GET /api/orders?code=...`（可选过滤）
   - `GET /api/deals?code=...`
   - `GET /api/quotes?code=...&start=...&end=...`
   - `GET /api/stock/<code>`（聚合：该股票行情 + 交易）

### 5.8 显示风格（涨跌配色）

- **红色 = 涨，绿色 = 跌**（中国大陆 / 港股惯例，与美股相反）。
- 适用范围：涨跌幅、浮动盈亏、当日涨跌、K 线 / 折线涨跌等所有表达「涨 / 跌」的数值与图形。
- 规则：值 > 0 用红色，值 < 0 用绿色，值 = 0 用中性色（灰 / 默认）。建议抽成统一的前端样式工具函数 / CSS class，避免散落。

---

## 6. 数据模型（建议 schema，可按需微调但需保留关键字段与主键语义）

```sql
-- 持仓快照（同一 snapshot_date + code + market 唯一，重复抓取覆盖当天）
CREATE TABLE IF NOT EXISTS positions (
    snapshot_date   TEXT NOT NULL,        -- YYYY-MM-DD，抓取日期
    market          TEXT NOT NULL,        -- HK / US
    code            TEXT NOT NULL,        -- 富途代码，如 HK.00700 / US.AAPL
    name            TEXT,
    qty             REAL,                 -- 持仓数量
    can_sell_qty    REAL,                 -- 可卖数量
    cost_price      REAL,                 -- 成本价
    nominal_price   REAL,                 -- 市价
    market_val      REAL,                 -- 市值
    pl_val          REAL,                 -- 浮动盈亏
    pl_ratio        REAL,                 -- 盈亏比例(%)
    currency        TEXT,
    updated_at      TEXT,                 -- 入库时间
    PRIMARY KEY (snapshot_date, market, code)
);

-- 历史订单（order_id 唯一）
CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    market          TEXT,                 -- HK / US
    code            TEXT,
    name            TEXT,
    trd_side        TEXT,                 -- BUY / SELL
    order_type      TEXT,
    order_status    TEXT,                 -- 含未成交/部分成交/全部成交/撤单/失败等
    price           REAL,
    qty             REAL,                 -- 委托数量
    dealt_qty       REAL,                 -- 成交数量
    dealt_avg_price REAL,                 -- 成交均价
    create_time     TEXT,                 -- 下单时间
    updated_time    TEXT,                 -- 最后更新时间
    currency        TEXT,
    raw_json        TEXT,                 -- 原始记录（便于排查）
    synced_at       TEXT
);

-- 历史成交（deal_id 唯一）
CREATE TABLE IF NOT EXISTS deals (
    deal_id         TEXT PRIMARY KEY,
    order_id        TEXT,                 -- 关联 orders.order_id
    market          TEXT,
    code            TEXT,
    name            TEXT,
    trd_side        TEXT,                 -- BUY / SELL
    price           REAL,                 -- 成交价
    qty             REAL,                 -- 成交数量
    create_time     TEXT,                 -- 成交时间
    counter_broker_id   TEXT,
    raw_json        TEXT,
    synced_at       TEXT
);

-- 每日行情（yfinance），yf_symbol + date 唯一
CREATE TABLE IF NOT EXISTS daily_quotes (
    yf_symbol       TEXT NOT NULL,        -- yfinance 代码，如 0700.HK / AAPL
    futu_code       TEXT,                 -- 对应的富途代码，便于关联
    date            TEXT NOT NULL,        -- YYYY-MM-DD
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    adj_close       REAL,
    volume          REAL,
    dividends       REAL,
    stock_splits    REAL,
    synced_at       TEXT,
    PRIMARY KEY (yf_symbol, date)
);

-- 同步日志
CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT,                 -- futu_position / futu_order / futu_deal / yfinance
    range_start     TEXT,
    range_end       TEXT,
    row_count       INTEGER,
    status          TEXT,                 -- ok / error
    message         TEXT,
    run_at          TEXT
);
```

> 字段命名与富途返回的 DataFrame 字段保持语义一致即可；不确定的字段保留进 `raw_json`，避免丢数据。

---

## 7. 建议目录结构

```
myStock/
├── README.md
├── environment.yml            # conda 环境定义（含 futu-api, yfinance, flask 等）
├── config.example.yaml        # 配置模板（端口、交易密码占位、市场等）
├── config.yaml                # 真实配置（.gitignore，不提交）
├── data/
│   └── mystock.db             # SQLite（运行时生成，.gitignore）
├── scripts/
│   ├── init.sh
│   ├── update.sh
│   └── server.sh
├── mystock/
│   ├── __init__.py
│   ├── config.py              # 读取配置
│   ├── db.py                  # 连接、建表、UPSERT 封装
│   ├── code_map.py            # 富途 <-> yfinance 代码映射
│   ├── collectors/
│   │   ├── futu_client.py     # OpenD 连接、持仓/订单/成交查询
│   │   └── yf_client.py       # yfinance 日线抓取
│   ├── pipelines/
│   │   ├── init_load.py       # 全量初始化入口（init.sh 调用）
│   │   └── update_load.py     # 增量更新入口（update.sh 调用）
│   └── web/
│       ├── app.py             # Flask 应用入口（server.sh 调用）
│       ├── templates/
│       └── static/
├── demo/                      # 参考 demo 代码（见 §9 参考资料）
└── .gitignore
```

---

## 8. 验收标准（Definition of Done）

- [ ] `bash scripts/init.sh` 一键完成：建环境（或提示已存在）、建库建表、全量抓取，结束后 `mystock.db` 中四张数据表均有数据，`sync_log` 有对应记录。
- [ ] `bash scripts/update.sh` 可重复执行，增量补齐到当天，且**当天数据被正确覆盖**而非重复累加。
- [ ] `bash scripts/server.sh` 后，浏览器打开 `http://localhost:8888` 正常显示。
- [ ] 「我的持仓」「我的交易」两个 Tab 可正常切换并展示数据。
- [ ] 点击单支股票可进入详情，能看到**该股票的日线历史**与**该股票的交易/成交记录**。
- [ ] 涨用红、跌用绿，全站一致。
- [ ] HK 与 US 两个市场的数据都覆盖。
- [ ] 历史订单包含未成交与已成交等全部状态。
- [ ] 富途代码与 yfinance 代码映射正确（HK / US 均验证至少一支）。
- [ ] 敏感信息（交易密码等）不出现在代码与提交记录中。
- [ ] OpenD 未启动 / 环境未就绪 / 库不存在等情况有清晰报错提示。

---

## 9. 参考资料

- 查询历史订单（富途）：https://openapi.futunn.com/futu-api-doc/trade/get-history-order-list.html
- 查询历史成交（富途）：https://openapi.futunn.com/futu-api-doc/trade/get-history-order-fill-list.html
- yfinance 指南：https://algotrading101.com/learn/yfinance-guide/
- demo 代码：仓库内 `demo/` 目录（如存在，请优先参考其中的连接与查询写法；当前需求方未随文档提供 demo 文件，实现时若 `demo/` 为空，按本文档与官方文档实现，并在 README 中说明）。

---

## 附录 A：接口参考（关键签名，已核对官方文档）

### A.1 富途历史订单

```python
# OpenSecTradeContext 上下文方法
history_order_list_query(
    status_filter_list=[],          # 传空 = 全部状态（满足「含未成交+成单」）
    code='',                        # 空 = 所有标的
    order_market=TrdMarket.NONE,    # 可分别按 HK / US 查询
    start='2025-01-01 00:00:00',    # 严格 'YYYY-MM-DD HH:MM:SS'
    end='<now>',
    trd_env=TrdEnv.REAL,
    acc_id=0, acc_index=0
)
# 返回 (ret, data)；ret == RET_OK 时 data 为 pd.DataFrame
# 关键返回字段：trd_side, order_type, order_status, order_id, code ...
```

### A.2 富途历史成交

```python
history_deal_list_query(
    code='',
    deal_market=TrdMarket.NONE,
    start='2025-01-01 00:00:00',
    end='<now>',
    trd_env=TrdEnv.REAL,            # 仅支持实盘
    acc_id=0, acc_index=0
)
```

> 注意：
> - 不传 `start/end` 时富途默认仅返回 90 天；抓取长区间需显式传范围，必要时**按时间窗口分段**后合并。
> - 持仓使用 `position_list_query()`。
> - 交易接口可能需要先 `unlock_trade(password=...)`。

### A.3 代码映射（富途 ↔ yfinance）

| 市场 | 富途格式 | yfinance 格式 | 规则 |
| --- | --- | --- | --- |
| 港股 | `HK.00700` | `0700.HK` | 去掉 `HK.` 前缀 → 取数字部分（注意前导 0 / 5 位代码）→ 加 `.HK` 后缀 |
| 美股 | `US.AAPL` | `AAPL` | 去掉 `US.` 前缀，直接用 ticker |

> 港股代码位数需谨慎：富途常见 5 位（如 `00700`），yfinance 习惯 4 位（`0700.HK`）。实现时做规整并对至少一支港股做实测校验，写成 `code_map.py` 的纯函数并加单元测试。

### A.4 yfinance 日线

```python
import yfinance as yf
df = yf.Ticker("0700.HK").history(start="2025-01-01", end=None, auto_adjust=False)
# 列：Open, High, Low, Close, Adj Close, Volume, Dividends, Stock Splits
# index 为日期
```

> 设 `auto_adjust=False` 以同时保留 `Close` 与 `Adj Close`。

---

## 附录 B：实现注意事项

1. **幂等与覆盖**：所有写库用 UPSERT（`INSERT ... ON CONFLICT ... DO UPDATE`）。当天可变数据（持仓快照、行情）以覆盖为准。
2. **时区**：富途时间字段注意时区；统一以字符串原样存储，必要时在展示层处理。
3. **限频**：yfinance 与富途均可能限频，批量抓取时加适当 `sleep` / 重试。
4. **配置注入**：端口、交易密码、市场列表、数据库路径等放 `config.yaml`，提供 `config.example.yaml` 模板。
5. **失败可恢复**：单个标的行情抓取失败不应中断整体流程，记录到 `sync_log` 后继续。
6. **README** 必须包含：conda 环境创建命令、OpenD 启动与登录说明、三个脚本的使用顺序（init → update → server）。
