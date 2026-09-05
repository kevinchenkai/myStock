# myStock 数据文档（数据字典）

> 文档身份：2026-06-23 · 原始作者 claude · 现行维护；2026-09-05 由 Codex 治理文件名与引用。作者／日期依据及旧名见文档清单。 [索引](../README.md) · [清单](../catalog.json)

> 更新：2026-09-05；核对基线 `a460d38` / `main`，本轮仅同步文档。本文面向**数据分析与模型训练**，覆盖生产库 `data/mystock.db` 和独立 ML 库 `data/ml/mystock_ml.db`。第 1–9 节保留早期字段与统计背景，第 10–12 节补齐当前表、迁移列、预测版本及建模约束；原文的行数、时间跨度与“当前仅几天”均指 2026-06-22 快照，不是当前库存量。
>
> - 数据来源：**富途 OpenD**（持仓 / 订单 / 成交）+ **yfinance**（日线行情 / 公司通用信息）。
> - 市场范围：**仅 HK（港股）与 US（美股）**。
> - 单用户、单机、个人真实交易数据 —— **属隐私数据，`data/`、`*.db` 已 gitignore，切勿外泄或提交**。
> - 文档中的统计快照取自 **2026-06-22** 的库（用于说明量级与分布，会随更新变化）。
>   当前结构须同时看 [生产 schema](../../mystock/schema.sql)、[生产列迁移](../../mystock/db.py)、[ML schema](../../mystock/ml/schema.sql) 与 [ML 迁移](../../mystock/ml/db.py)。导航见 [文档索引](../README.md)。

---

## 0. 早期生产数据快照（2026-06-22）

| 表 | 用途 | 主键 | 行数* | 时间跨度* | 来源 |
| --- | --- | --- | --- | --- | --- |
| `positions` | 持仓**每日快照** | (snapshot_date, market, code) | 72 | 2026-06-21 ~ 06-22 | 富途 |
| `orders` | 历史**委托订单** | order_id | 1358 | 2025-01-02 ~ 2026-06-22 | 富途 |
| `deals` | 历史**成交回报** | deal_id | 873 | 2025-01-03 ~ 2026-06-18 | 富途 |
| `daily_quotes` | **日线行情**（OHLCV+复权+分红拆股） | (yf_symbol, date) | 13491 | 2025-01-02 ~ 2026-06-18 | yfinance |
| `stock_profiles` | 公司 / **估值通用信息** | futu_code | 38 | 当前快照 | yfinance |
| `fx_rates` | **外汇日线**（美元兑人民币 USDCNY） | (pair, date) | 381 | 2025-01-02 ~ 至今 | yfinance |
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

- ⚠️ **`cost_price` 可能 ≤ 0**：早期快照曾出现负成本（合成示例：`cost_price=-10`）。这是富途对**超卖 / 融券 / 历史记账**产生的会计产物，**不是真实成本**。建模时 `cost_price <= 0` 应视为**缺失**，不可直接当成本用（pnl.py 即如此处理 → 记入 uncovered）。
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
| `adj_close` | REAL | 复权收盘价 | 可用于一致复权口径的收益率特征；限价撮合用未复权价格与显式分红／拆股，见第 12 节 |
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

## 5b. fx_rates — 外汇日线（yfinance）

美元兑人民币（及未来可扩展的其它货币对）日线。来自 yfinance `CNY=X`，从 2025-01-01 起，
按 `(pair, date)` UPSERT（当天覆盖）。外汇对**仅有 OHLC，无成交量/分红/复权**。

| 列 | 类型 | 含义 | 取值 / 备注 |
| --- | --- | --- | --- |
| `pair` | TEXT | 货币对（主键之一） | 当前仅 `USDCNY`；预留以便扩展 |
| `date` | TEXT | 交易日（主键之一） | `YYYY-MM-DD` |
| `open` `high` `low` `close` | REAL | 开/高/低/收盘汇率 | `close` = **1 美元对应的人民币**（如 6.7745） |
| `synced_at` | TEXT | 入库时间 | |

**坑点 / 注意**

- ⚠️ **当天行可能 `close` 为空**：外汇当天未收盘时只有部分价。前端 / 分析须**过滤 `close` 为 NULL/空** 的行（注意 `Number(null)` / `Number("")` 在 JS 中等于 0，不能只用 `isFinite` 判断）。
- 观测区间 close ∈ [6.757, 7.350]（2025-01 ~ 2026-06，人民币整体升值约 7%）。
- 与股票行情相互独立，做"金额换汇到统一货币"时即可用此表按日期取汇率。
- yfinance 的 `CNY=X` 即 USDCNY；切勿与 `CNH=X`（离岸）混淆。

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
| `source` | TEXT | 数据源：`futu_position`/`futu_order`/`futu_deal`/`yfinance`/`yf_profile`/`fx_usdcny` |
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
- 参考实现：[`mystock/pnl.py`](../../mystock/pnl.py)（`compute_pnl` 移动平均、`analyze_stock` FIFO 回合）。

### 8.3 做分析 / 建模前的清洗清单

1. **币种归一**：所有金额按 `currency` 换汇到统一货币，或分 HK / US 两套模型。deals 表无 currency，用 market 推断。
2. **代码统一**：跨表用 `futu_code`；只有 quotes/profiles 暴露 `yf_symbol`。
3. **成本异常**：`positions.cost_price <= 0` → 缺失处理。
4. **区分收益率特征与撮合价格**：一致复权收益可用 `adj_close`；实际限价和库存回放使用未复权 OHLC，显式处理公司行动，勿混用或重复计分红（见第 12 节）。
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

- **直接只读库**（推荐分析用）：`sqlite3 -readonly data/mystock.db`；Python 使用 SQLite URI `mode=ro` 并设 `PRAGMA query_only=ON` 后再传给 `pandas.read_sql`。
- **只读 JSON API**（Web 层提供，见 [`mystock/web/app.py`](../../mystock/web/app.py)）：`/api/positions`、`/api/orders`、`/api/deals`、`/api/quotes`、`/api/stock/<code>`、`/api/stock/<code>/profile`、`/api/stock/<code>/analysis`、`/api/pnl`。
- 刷新数据：`bash scripts/update.sh`（需富途 OpenD 已登录）。

> 数据会随每日更新变化；本文档中的统计数字是 2026-06-22 的快照，结构稳定、数字会变。


## 10. 当前生产库补充（2026-09-05）

生产库共 10 张业务／运维表（不计 SQLite 内部表），另有独立 ML 库 8 张表。上面的 8 表速览是 6 月快照，缺少以下两张生产表；字段定义以 schema 与代码迁移合并为准。

### 10.1 account_funds — 每日账户资金快照

主键 `snapshot_date`（TEXT），来源富途账户资金查询，重复抓取覆盖当天。历史资金快照不能从当前账户状态回填。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `snapshot_date` | TEXT | 快照日期 |
| `report_currency` | TEXT | 综合账户记账币种，当前 HKD |
| `total_assets` / `market_val` | REAL | 账户净资产／证券市值 |
| `cash` / `frozen_cash` / `avl_withdrawal_cash` | REAL | 现金／冻结现金／可提取现金 |
| `power` | REAL | 购买力，不等于无杠杆可用现金 |
| `hkd_assets` / `hk_cash` | REAL | 港币侧资产／现金 |
| `usd_assets` / `us_cash` | REAL | 美元侧资产／现金，不与港币字段直接求和 |
| `risk_status` / `updated_at` | TEXT | 风险状态／入库时间 |

该表不是每股库存模拟的隐式本金来源；v2 的每股初始现金仍由场景显式输入。

### 10.2 capital_flow — 日频资金流向

主键 `(code, date)`，均为 TEXT，来源富途；每股每天一行。`in_flow`、`main_in_flow`、`super_in_flow`、`big_in_flow`、`mid_in_flow`、`sml_in_flow` 为 REAL，分别为整体／主力／超大／大／中／小单净流入；`synced_at` 为 TEXT。

金额为标的本币；主力约为超大 + 大单，不能把所有字段相加。现有采集按近一年窗口回补；它不自动构成多年可用的 point-in-time 特征，新特征实验需单独核验历史覆盖和信息可用时间。

### 10.3 stock_profiles 的盘面与规则列

该表是随更新覆盖的横截面快照，不能把今天的值填回历史训练日。

| 字段 | 类型 | 来源／限制 |
| --- | --- | --- |
| `turnover_rate` / `amplitude` | REAL | 富途快照的换手率／振幅百分数 |
| `week52_high` / `week52_low` | REAL | 52 周高／低，本币价格 |
| `snap_synced_at` | TEXT | 富途盘面字段的入库时间，独立于 yfinance 的 synced_at |
| `lot_size` | INTEGER | 富途证券交易单位，通过 `db.py` 列迁移加入 |
| `price_spread` | REAL | 富途快照价格档差，通过迁移加入；不是完整历史价格阶梯规则 |
| `rules_effective_from` | TEXT | 当前采集代码写入快照日期；会随刷新覆盖，不证明规则真正的历史生效起点 |

规则历史版本／变化检测及 v2 自动预填仍待实现。回放场景里的 `tick_size` 需显式核验，不能把最新 `price_spread` 当成全历史常量，HK lot=100 也不是普遍规则。

## 11. 当前 ML 库与预测版本

ML 只读生产事实再写入自己的数据库；Web 连接只读 ML 库。数据表定义见 [ML schema](../../mystock/ml/schema.sql)。

| 表 | 主键 | 内容与边界 |
| --- | --- | --- |
| `ml_quotes_1d` | `(symbol, date)` | yfinance 日线：OHLC、adj_close、volume、dividends、splits、futu_code、synced_at |
| `ml_quotes_1h` | `(symbol, ts_utc)` | 小时 OHLCV、futu_code、ts_et、synced_at、data_source、source_ref |
| `ml_deals` | `deal_id` | 生产成交副本：订单、代码、市场、方向、价量、成交时间、snapshot_taken_at |
| `ml_orders` | `order_id` | 生产订单快照：订单状态、价量、成交价量、创建／更新时间与 snapshot_taken_at |
| `ml_positions` | `(snapshot_date, market, code)` | 生产持仓快照副本：数量／可卖量、成本、名义价、盈亏比例与 snapshot_taken_at |
| `ml_sync_log` | `id` | ML 专属采集来源、范围、行数、状态及运行时间 |
| `ml_predictions` | `(code, as_of)` | 旧兼容投影，包含 l_hat／h_hat、分位与 CQR 参数、来源及生成时间；已有混合来源需逐行识别 |
| `ml_prediction_versions` | `prediction_id` | 新版本证据，另有 `(run_id, code, as_of, target_session)` 唯一约束；内容不可覆盖或删除 |

小时 `ts_utc` 是统一主键时间。`ts_et` 名称不能用于推断市场：读取时结合代码及 session 规则；当前采集按相应市场本地时间组织日期。`data_source` 默认 yfinance；已审查的 Futu 不复权补采为 `futu_none`，`source_ref` 保存原始证据 SHA256。不要插值伪造缺失小时。日线日期也是各自市场交易日，不应把 HK 行按美东日期解释。

### 11.1 ml_prediction_versions 字段

| 字段（均 TEXT） | 含义 |
| --- | --- |
| `prediction_id` / `run_id` | 单条预测标识／生成运行标识 |
| `code` / `as_of` / `target_session` | 富途代码／信息基准日／目标交易日 |
| `source` / `status` | live、backfill、recomputed 等来源与有效／审计状态；有效性还需时间证据校验 |
| `generated_at` / `decision_at` / `published_at` | 生成时间／决策截止／真实公网发布时间，各自独立，不互相追认 |
| `manifest_path` | 私有运行 manifest 路径 |
| `payload_json` | 预测价格、校准参数及其他版本内容 |
| `content_hash` | 不可覆盖内容的摘要 |

数据库触发器禁止内容字段更新和历史删除，生命周期 status／published_at 可按协议附加。新 backfill／recomputed 只进版本表；后续只有经时间核验的 generated live 才投影到旧表。旧 live 标签不证明真正发布时间，未知时间只作审计。

`data/ml/runs/<id>/input.db` 冻结本次输入，manifest 保存 SHA256、Git SHA、依赖、特征、seed、日历及训练／校准截止。`reports/runs/<id>/` 是报告归档，`receipts/` 是训练／发布回执，`.data.json` 只描述采集。数据库内版本和文件证据互有关联，不得独立删除快照或整库覆盖。

## 12. 当前建模与回溯口径补充

- **价格与公司行动**：复权收益率特征与未复权限价分开。库存回放使用未复权 OHLC，显式处理拆股和分红；付款日未知只计应收，不把应收当可下单现金，不同时重复计算复权收益和分红。
- **时间与信息可用性**：训练／校准标签截止不得晚于 as_of；目标日必须晚于 as_of。行情日期完整不等于已经收盘确认，synced_at／generated_at／published_at 各需独立核验。当前日历覆盖 2020–2027，详见日历说明。
- **特征可用性**：最新 profiles 不能当作历史值。成交后 N 日收益是事后标签／诊断，不能作为下单时输入特征；订单状态和撤单耗时同样不能穿越其可用时点。
- **历史与前向证据**：2026-09-05 的 720 条重建是事后历史研究；不等于 720 次当时发布。live、HTML 回填与 recomputed 分开，缺口显式保留。
- **窗口与权益**：20／60／120 是各市场 session 数，每个窗口独立初始化。各股独立本币账户；费用未填标 gross，不把 legacy 成交额收益率与库存初始权益收益率直接比较。
- **真实订单边界**：只有订单快照，缺完整生命周期；事实通过 `/api/ml/v2/review?selected=YYYY-MM-DD` 返回，没有独立 facts 路由。不把事后替换订单当作真实可执行收益。
- **只读与写入**：分析和 Web 只读；采集、修复、迁移、重建均有写入边界，应先备份并明确目标库。历史 repair／rebuild 命令见 [历史补齐记录](../records/ml-history-refresh_codex_20260905.md)，部署后的现状以 [部署回执](../records/ml-deployment_codex_20260905.md) 为准。
