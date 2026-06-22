# myStock 数据文档（数据字典）

> 本文档面向**数据分析与模型训练**。它描述 myStock 本地 SQLite 库（`data/mystock.db`）中
> 全部数据表的字段、含义、取值特征、已知坑点，以及做分析 / 建模前必须知道的口径与陷阱。
>
> - 数据来源：**富途 OpenD**（持仓 / 订单 / 成交）+ **yfinance**（日线行情 / 公司通用信息）。
> - 市场范围：**仅 HK（港股）与 US（美股）**。
> - 单用户、单机、个人真实交易数据 —— **属隐私数据，`data/`、`*.db` 已 gitignore，切勿外泄或提交**。
> - 文档中的统计快照取自 **2026-06-22** 的库（用于说明量级与分布，会随更新变化）。
>   定义见 schema：[`mystock/schema.sql`](../mystock/schema.sql)；代码映射见 [`mystock/code_map.py`](../mystock/code_map.py)。

---

## 0. 速览

| 表 | 用途 | 主键 | 行数* | 时间跨度* | 来源 |
| --- | --- | --- | --- | --- | --- |
| `positions` | 持仓**每日快照** | (snapshot_date, market, code) | 72 | 2026-06-21 ~ 06-22 | 富途 |
| `orders` | 历史**委托订单** | order_id | 1358 | 2025-01-02 ~ 2026-06-22 | 富途 |
| `deals` | 历史**成交回报** | deal_id | 873 | 2025-01-03 ~ 2026-06-18 | 富途 |
| `daily_quotes` | **日线行情**（OHLCV+复权+分红拆股） | (yf_symbol, date) | 13491 | 2025-01-02 ~ 2026-06-18 | yfinance |
| `stock_profiles` | 公司 / **估值通用信息** | futu_code | 38 | 当前快照 | yfinance |
| `quote_skiplist` | 行情**跳过名单**（退市/无数据） | futu_code | 39 | — | 系统 |
| `sync_log` | **同步日志**（运维元数据） | id | 21 | — | 系统 |

\* 数字为 2026-06-22 快照，仅供量级参考。涉及 34~38 只标的。

**关键标识符与口径**（建模前必读，详见第 8 节）：

- **代码有两套**：富途代码 `HK.00700` / `US.AAPL`（库内主用，列名 `code` / `futu_code`）；yfinance 代码 `0700.HK` / `AAPL`（仅 `daily_quotes.yf_symbol`、`stock_profiles.yf_symbol`）。互转规则见 `code_map.py`。**跨表 JOIN 一律用富途代码**。
- **货币非统一**：HKD 与 USD 混合，每行带 `currency`。**金额类字段做横向比较 / 入模前必须先按币种归一**（换汇或分市场建模）。
- **时间格式**：成交 / 订单为 `YYYY-MM-DD HH:MM:SS.fff`（毫秒，**当地交易所时区**，HK=Asia/Hong_Kong，US=America/New_York，**库内不带时区标记**）；行情 `date` 为 `YYYY-MM-DD`（交易日，无时分秒）。
- **快照 vs 流水**：`positions` 是**状态快照**（每天一份当前持仓），`orders`/`deals` 是**事件流水**（一次性历史回填 + 增量追加）。两者建模含义完全不同。

---

## 1. positions — 持仓每日快照

每天 `update.sh` 抓一次当前持仓，按 `(snapshot_date, market, code)` UPSERT（当天重复抓取覆盖）。
**是状态快照不是流水**：每个 snapshot_date 是当日全部持仓的一份完整切片。

| 列 | 类型 | 含义 | 取值 / 备注 |
| --- | --- | --- | --- |
| `snapshot_date` | TEXT | 快照日期 | `YYYY-MM-DD`，抓取当日 |
| `market` | TEXT | 市场 | `HK` / `US` |
| `code` | TEXT | 富途代码 | 如 `HK.00700` / `US.AAPL` |
| `name` | TEXT | 名称 | 中文/英文，来自富途 |
| `qty` | REAL | 持仓数量 | 观测范围 1 ~ 18000 |
| `can_sell_qty` | REAL | 可卖数量 | ≤ qty（T+0/冻结差异） |
| `cost_price` | REAL | 持仓成本价 | **可能为负，见坑点** |
| `nominal_price` | REAL | 当前市价 | 富途口径，非实时（取自最近交易） |
| `market_val` | REAL | 市值 | = qty × nominal_price（本币） |
| `pl_val` | REAL | 浮动盈亏 | 本币 |
| `pl_ratio` | REAL | 盈亏比例 | **百分比数值**，如 6364.24 表示 +6364.24%；观测 -98.31 ~ 6364.24 |
| `currency` | TEXT | 货币 | `HKD` / `USD` |
| `updated_at` | TEXT | 入库时间 | 本地系统时间 |

**坑点 / 注意**

- ⚠️ **`cost_price` 可能 ≤ 0**：观测到 7 只美股成本价为负（如 `US.AAPL` = -1233.96、`US.BA` = -713.04）。这是富途对**超卖 / 融券 / 历史记账**产生的会计产物，**不是真实成本**。建模时 `cost_price <= 0` 应视为**缺失**，不可直接当成本用（pnl.py 即如此处理 → 记入 uncovered）。
- `pl_ratio` 是百分比（不是小数）；异常大的值（数千 %）通常对应近 0 成本，需结合 cost_price 一起清洗。
- 当前库只有 2 天快照（06-21、06-22）。**持仓时序分析需要先积累足够天数的快照**；历史快照不可回填（富途只给"当前"）。
- 同一只股票每个快照日一行；做面板数据时按 `(code, snapshot_date)` 对齐。

---

## 2. orders — 历史委托订单

一次性回填（`2025-01-01` 起，按 80 天窗口分段抓取后合并）+ 每日增量。按 `order_id` UPSERT。
**事件流水**：一条 = 一笔委托（不一定成交）。

| 列 | 类型 | 含义 | 取值 / 备注 |
| --- | --- | --- | --- |
| `order_id` | TEXT | 订单号（主键） | 富途字符串，如 `FH1A68BAFB703F2000` |
| `market` | TEXT | 市场 | `HK` / `US` |
| `code` | TEXT | 富途代码 | |
| `name` | TEXT | 名称 | |
| `trd_side` | TEXT | 买卖方向 | `BUY` / `SELL` |
| `order_type` | TEXT | 订单类型 | 当前全部 `NORMAL`（限价单） |
| `order_status` | TEXT | 订单状态 | 见下方枚举 |
| `price` | REAL | 委托价 | 本币；无 NULL |
| `qty` | REAL | 委托数量 | |
| `dealt_qty` | REAL | 已成交数量 | 0 ~ qty |
| `dealt_avg_price` | REAL | 成交均价 | 无 NULL（未成交为 0） |
| `create_time` | TEXT | 下单时间 | `YYYY-MM-DD HH:MM:SS.fff`，交易所当地时区 |
| `updated_time` | TEXT | 最后更新时间 | 同上 |
| `currency` | TEXT | 货币 | `HKD` / `USD` |
| `raw_json` | TEXT | 原始记录 | 富途原始字段全集，见下 |
| `synced_at` | TEXT | 入库时间 | |

**`order_status` 枚举**（观测到）：

| 值 | 含义 |
| --- | --- |
| `FILLED_ALL` | 全部成交 |
| `CANCELLED_ALL` | 全部撤单（未成交即撤） |
| `CANCELLED_PART` | 部分成交后撤单 |
| `FAILED` | 失败 |

> 建模常用派生：`是否成交 = order_status in (FILLED_ALL, CANCELLED_PART) 且 dealt_qty>0`；
> `撤单率`、`成交率`、`下单到撤单时长 = updated_time - create_time` 等行为特征都可从此表算。

**`raw_json` 额外字段**（schema 未单列，分析时可挖）：`stock_name`、`order_market`、完整 `qty/price/dealt_qty/dealt_avg_price`、`create_time`、`updated_time` 等富途原始键。若需要 schema 之外的信息，解析此列。

---

## 3. deals — 历史成交回报

委托被撮合后的**真实成交**记录。一条 = 一笔成交（一个订单可拆成多条成交）。按 `deal_id` UPSERT。
**这是计算盈亏 / 复盘最权威的事实表**（pnl.py 即以此为输入）。

| 列 | 类型 | 含义 | 取值 / 备注 |
| --- | --- | --- | --- |
| `deal_id` | TEXT | 成交号（主键） | **库内为字符串**，raw_json 中原为数字 |
| `order_id` | TEXT | 关联订单号 | → `orders.order_id` |
| `market` | TEXT | 市场 | `HK` / `US` |
| `code` | TEXT | 富途代码 | |
| `name` | TEXT | 名称 | |
| `trd_side` | TEXT | 买卖方向 | `BUY` / `SELL` |
| `price` | REAL | 成交价 | 本币 |
| `qty` | REAL | 成交数量 | |
| `create_time` | TEXT | 成交时间 | `YYYY-MM-DD HH:MM:SS.fff`，交易所当地时区 |
| `counter_broker_id` | TEXT | 对手券商 ID | 多为空串 |
| `raw_json` | TEXT | 原始记录 | 含 `status`、`counter_broker_name`、`jp_acc_type` 等 |
| `synced_at` | TEXT | 入库时间 | |

**坑点 / 注意**

- ⚠️ **此表无 `currency` 列**（与 positions/orders 不同）。需要币种时用 `market` 推断（HK→HKD，US→USD）或 JOIN `orders`/`positions` 取。
- ⚠️ **数据窗口从 2025-01-01 起**：更早的买入不在表内 → 出现 **卖出数量 > 买入数量** 的标的（窗口前已持仓）。算 FIFO / 移动平均成本时窗口前缺口需用 `positions.cost_price` 兜底，且兜底成本 ≤ 0 视为不可用（详见 pnl.py 与第 8 节）。
- **关联完整性**：873 条成交全部有 `order_id`；观测到 **1 条 order_id 不在 orders 表**（孤儿，订单窗口边界产物）。JOIN 时用 LEFT JOIN 并容忍极少数缺失。
- **无手续费 / 税费字段**：raw_json 也没有费用明细 → **盈亏均为税前毛额**，建模时若需净额需自行按费率估算。
- `create_time` 毫秒级、当地时区、无时区标记。跨市场按时间排序前需统一时区（或仅在同市场内排序）。

---

## 4. daily_quotes — 日线行情（yfinance）

每标的每交易日一行。按 `(yf_symbol, date)` UPSERT（当天覆盖）。`auto_adjust=False`，故同时保留原始 `close` 与复权 `adj_close`。

| 列 | 类型 | 含义 | 取值 / 备注 |
| --- | --- | --- | --- |
| `yf_symbol` | TEXT | yfinance 代码（主键之一） | `0700.HK` / `AAPL` |
| `futu_code` | TEXT | 对应富途代码 | **跨表 JOIN 用这个** |
| `date` | TEXT | 交易日 | `YYYY-MM-DD`，仅交易日 |
| `open` `high` `low` `close` | REAL | 开/高/低/收 | 原始价（未复权），本币；无 NULL |
| `adj_close` | REAL | 复权收盘价 | **回测 / 收益率计算应优先用此列**（已含分红拆股调整） |
| `volume` | REAL | 成交量 | 股数 |
| `dividends` | REAL | 当日每股分红 | 多数为 0；观测 70 行 >0 |
| `stock_splits` | REAL | 当日拆股比例 | 多数为 0；观测 3 行 ≠0 |
| `synced_at` | TEXT | 入库时间 | |

**坑点 / 注意**

- ✅ OHLCV 当前**无 NULL**（已过滤）。
- ⚠️ **每只股票交易日数不一**：多数 HK/US 主流股有 ~358 个交易日（2025-01 至 2026-06）；少数新上市/退市股极少（如 `US.SPCX` 仅 5 行，且已进 skiplist）。建模做面板 / 对齐时务必**按 date 取交集或显式处理缺口**，勿假设等长。
- ⚠️ **`close` vs `adj_close`**：算日收益率、做技术指标用 `adj_close`（避免分红/拆股日的假跳空）；展示原始价用 `close`。两者在有分红/拆股的日子会分叉。
- 港股代码位数：富途常见 5 位（`00700`），yfinance 习惯 4 位（`0700.HK`），`code_map` 已规整。直接信 `futu_code` 关联即可。
- 行情**只到最近一个已收盘交易日**；当天未收盘 / 未开盘则无当日行（与持仓快照日期可能错位，对齐时注意）。

---

## 5. stock_profiles — 公司 / 估值通用信息（yfinance）

来自 yfinance `Ticker.info`，每日 `update.sh` 全量刷新覆盖。**横截面快照（无历史）**，反映抓取当时的估值。

| 列 | 类型 | 含义 | 取值 / 备注 |
| --- | --- | --- | --- |
| `futu_code` | TEXT | 富途代码（主键） | |
| `yf_symbol` | TEXT | yfinance 代码 | |
| `long_name` | TEXT | 公司全称 | |
| `sector` | TEXT | 板块 | 见枚举 |
| `industry` | TEXT | 行业 | 细分 |
| `exchange` | TEXT | 交易所 | 如 `HKG` / `NMS` |
| `market_cap_mm` | REAL | 市值（**百万，本币**） | 观测 29.2 ~ 5,103,122（百万）；**单位见 currency** |
| `shares_mm` | REAL | 流通股本（百万） | |
| `trailing_pe` | REAL | 市盈率(TTM) | 6/38 为 NULL（亏损/无数据） |
| `forward_pe` | REAL | 预期市盈率 | |
| `price_to_book` | REAL | 市净率 | |
| `trailing_eps` | REAL | 每股收益(TTM) | 本币 |
| `dividend_yield` | REAL | 股息率 | **百分比数值**：8.32 表示 8.32%（非 0.0832）；21/38 为 NULL（不分红） |
| `beta` | REAL | Beta | 2/38 NULL |
| `target_mean_price` | REAL | 分析师目标均价 | 本币；1/38 NULL |
| `recommendation` | TEXT | 分析师评级 | `strong_buy`/`buy`/`hold`/`none`（观测） |
| `currency` | TEXT | 货币 | `HKD` / `USD`，**market_cap_mm / eps / target_price 的计价币** |
| `website` | TEXT | 官网 | |
| `synced_at` | TEXT | 入库时间 | |

**坑点 / 注意**

- ⚠️ **本币计价**：`market_cap_mm`、`trailing_eps`、`target_mean_price` 是**标的本币**（HK→HKD、US→USD），**不是美元**。跨市场比较前必须换汇。
- ⚠️ **`dividend_yield` 是百分比数值**（与某些 yfinance 版本的小数口径不同，本项目入库前已统一为百分比）。
- ⚠️ **无历史**：每天覆盖，只有最新值。若建模需要估值时序，必须自建快照归档（当前 pipeline 不存历史 profile）。
- 缺失率较高的列：`dividend_yield`（55% NULL）、`trailing_pe`（16% NULL）。入模需缺失值策略。
- `sector` 枚举（观测）：Communication Services / Technology / Consumer Defensive / Consumer Cyclical / Industrials / Financial Services。

---

## 6. quote_skiplist — 行情跳过名单（运维）

连续多次抓取为空（退市 / yfinance 无数据）的代码，后续跳过以减少无效请求。**分析时可用作"数据质量黑名单"**。

| 列 | 类型 | 含义 |
| --- | --- | --- |
| `futu_code` | TEXT | 富途代码（主键） |
| `yf_symbol` | TEXT | yfinance 代码 |
| `empty_count` | INTEGER | 连续抓到空的次数 |
| `reason` | TEXT | 备注，如 `no data` / `delisted` |
| `first_seen` `updated_at` | TEXT | 首次/最近记录时间 |

> 注意：进入 skiplist 不等于退市，也可能是 yfinance 临时无数据。`US.SPCX` 等少数据股就在此表。建模时**这些代码的行情可能稀疏或缺失**。

---

## 7. sync_log — 同步日志（运维）

每次抓取写一条，用于排查与确定增量起点。**建模一般不用，但可用于判断某段数据是否成功落库**。

| 列 | 类型 | 含义 |
| --- | --- | --- |
| `id` | INTEGER | 自增主键 |
| `source` | TEXT | 数据源：`futu_position`/`futu_order`/`futu_deal`/`yfinance`/`yf_profile` |
| `range_start` `range_end` | TEXT | 本次抓取覆盖区间 |
| `row_count` | INTEGER | 影响行数 |
| `status` | TEXT | `ok` / `error` |
| `message` | TEXT | 摘要，如 `38 ok / 0 empty / 0 err / 1 skipped` |
| `run_at` | TEXT | 运行时间 |

---

## 8. 跨表关系与建模口径（必读）

### 8.1 实体关系

```
stock_profiles ─┐ (futu_code)
positions ──────┤ 每只股票一个 futu_code，跨表主键
daily_quotes ───┘ (futu_code，注意它也有 yf_symbol)
                  │
orders 1 ──< deals N        (orders.order_id = deals.order_id)
```

- **股票维度**用 `code` / `futu_code` 串起 positions / quotes / profiles。
- **交易维度**：一个 `order` 可对应多条 `deal`（部分成交拆单）。
- daily_quotes 同时有 `yf_symbol` 和 `futu_code` —— **跨表一律用 `futu_code`**。

### 8.2 盈亏口径（沿用本项目 pnl.py）

- **已实现盈亏**：项目内两套口径并存——**移动平均成本**（券商口径，用于盈亏列表）与 **FIFO 配对**（用于单股回合复盘，得到干净的持有周期）。两者结果会有小差异，建模时**先明确口径**。
- **窗口前缺口**：deals 从 2025-01-01 起，更早买入缺失 → 用 `positions.cost_price` 兜底；**兜底成本 ≤ 0 视为不可用**（富途超卖记账产物），对应数量记为 `uncovered_sell_qty`，不可产出有效盈亏。
- **无费用字段**：盈亏为税前毛额。
- 参考实现：[`mystock/pnl.py`](../mystock/pnl.py)（`compute_pnl` 移动平均、`analyze_stock` FIFO 回合）。

### 8.3 做分析 / 建模前的清洗清单

1. **币种归一**：所有金额按 `currency` 换汇到统一货币，或分 HK / US 两套模型。deals 表无 currency，用 market 推断。
2. **代码统一**：跨表用 `futu_code`；只有 quotes/profiles 暴露 `yf_symbol`。
3. **成本异常**：`positions.cost_price <= 0` → 缺失处理。
4. **收益率用复权价**：`daily_quotes.adj_close`，不要用 `close`。
5. **行情对齐**：各标的交易日不等长，按 date 显式对齐 / 取交集；剔除 skiplist 中稀疏标的或单独处理。
6. **时区**：成交/订单时间为交易所当地时区且无时区标记；跨市场排序前先统一。
7. **数据量级**：deals 873 条 / 34 只股、行情 ~358 天 —— **样本偏小**。复杂模型注意过拟合；优先稳健、可解释的方法，必要时引入外部行情扩样本。
8. **持仓快照稀疏**：当前仅 2 天，时序建模需先积累快照。
9. **隐私**：真实个人交易数据，产出的中间文件 / 模型 / 图表注意脱敏，不要进公开仓库。

### 8.4 可直接派生的特征（举例）

| 维度 | 可派生特征 | 来源表 |
| --- | --- | --- |
| 交易行为 | 撤单率、成交率、下单→撤单时长、单/批量委托倾向 | orders |
| 交易盈亏 | 每股已实现盈亏、胜率、盈亏比、平均持有天数、回合次数 | deals (+positions 兜底) |
| 择时 | 买卖点相对当日 OHLC 的位置、买卖点后 N 日收益 | deals × daily_quotes |
| 标的画像 | 板块/行业、估值分位（PE/PB）、Beta、股息率、市值分层 | stock_profiles |
| 行情因子 | 动量、波动率、均线偏离、量价关系（用 adj_close/volume） | daily_quotes |
| 持仓结构 | 集中度、行业暴露、浮盈浮亏分布 | positions × profiles |

---

## 9. 获取数据的方式

- **直接读库**（推荐分析用）：`sqlite3 data/mystock.db` 或 `pandas.read_sql`。
- **只读 JSON API**（Web 层提供，见 [`mystock/web/app.py`](../mystock/web/app.py)）：`/api/positions`、`/api/orders`、`/api/deals`、`/api/quotes`、`/api/stock/<code>`、`/api/stock/<code>/profile`、`/api/stock/<code>/analysis`、`/api/pnl`。
- 刷新数据：`bash scripts/update.sh`（需富途 OpenD 已登录）。

> 数据会随每日更新变化；本文档中的统计数字是 2026-06-22 的快照，结构稳定、数字会变。
