# Web API 优化调研_Codex_20260905

> 作者：Codex · 日期：2026-09-05 · 基线 `b2a2e155095079e0e2aa76c5d4e61aef8248bd0c` / `main`。状态：完成代码与官方接口文档只读调研，调研已完成；最新选择为升级两个依赖并执行两个 P0，其余候选暂缓。
> 边界：本轮仅调研与整理讨论文档；未访问账户接口、修改运行数据库、重启 8888、训练 ML、发布公网报告、新增调度或执行交易。未实测各股票的远端数据完整性／行情权限，不把文档支持视为账户实测可用。

## 结论与范围

优先把已有数据变得可理解，再增加一个低频事件来源。原拟三批（现为待讨论候选）：**数据更新时间与采集状态 → 单股快照卡 → 公司事件卡**。保持 Flask 只读本地数据库、原生 JS 与现有页面结构；不新增实时终端、模型实验或自动交易。

用户已经暂缓多分位概率预测与波动率归一化升级。本轮新增事件、快照只用于 Web 展示，不进入 ML 特征、训练、历史重建或挂单参数自动调整。

正式执行范围：[Web 数据可用性升级工单](../records/web-data-upgrade-work-order_codex_20260905.md)。此前 [Futu 调研](futu-api-research_claude_20260718.html) 的账户资金、盘面增量字段和资金流已在代码中落地，本轮不重复列作新功能。

## 1. 已核实的代码缺口

| 代码证据 | 现状 | 优化价值 |
| --- | --- | --- |
| `mystock/web/static/app.js` / `dataFreshnessBanner` | 用浏览器本地日期算自然日差，超过 3 天即提示滞后；仅覆盖个股日线 | 按数据源区分行情日期、采集时间与失败，避免休市与采集异常混淆 |
| `mystock/schema.sql` / `sync_log`；`mystock/pipelines/init_load.py` | 有采集记录；部分流程用汇总 `ok` 加文字描述 partial/empty；`fetch_profile` 吞异常后返回 None | 不能从整体 ok 推断每股成功，也不能把接口失败当“暂无资料” |
| `mystock/collectors/futu_client.py` / `snapshot_fields` | 已取换手率、振幅、52 周高低、lot_size、price_spread；丢弃原始行情更新时间、价格、停牌状态等 | 同一次快照响应即可补足核心展示，通常不增加行情请求次数 |
| `mystock/web/static/app.js` / `PROFILE_FIELDS` 与 `mystock/web/app.py` / `_PROFILE_LABELS` | 展示估值与部分盘面字段；lot_size / price_spread 尚无对应展示 | 给用户当前每手股数和价格上下文，避免猜交易单位 |
| `mystock/collectors/yf_client.py` / `fetch_daily`；`daily_quotes` | 已存 dividends、stock_splits；图表缺少公司事件标注 | 历史分红与拆合股标记可先复用本地数据，无须新抓取链路 |
| `mystock/collectors/yf_client.py` / `fetch_profile` | 当前只抓 info，没有事件日历采集 | 补低频财报／除息日期提示，辅助解释短期异动与看盘安排 |

以上为代码核查结论；本轮没有读取真实持仓和数据库来证明数据覆盖，也没有进行浏览器版式验收。

## 2. API 能力与取舍

| 能力 | 官方依据与语义 | 本轮决定 |
| --- | --- | --- |
| Futu 市场快照 | [get_market_snapshot](https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html)：提供行情更新时间、价格、量、停牌、证券状态、每手股数等；每批最多 400 个标的 | 复用已有批量请求，增加少量字段与来源时间；显示“缓存快照”，不叫实时行情 |
| Futu 报价档位 | 同上：`price_spread` 是当前向上的摆盘报价档位差；`ask_price`、`bid_price` 才分别为卖价、买价 | 不把 price_spread 命名为买卖价差，不据其实现任意价格的 tick 舍入或历史交易规则 |
| Futu 除权除息 | [get_rehab](https://openapi.futunn.com/futu-api-doc/quote/get-rehab.html)：除权除息日、每股派现、拆合股及复权因子 | 有价值的第二来源，但首轮暂不接，避免同时维护两套事件与比例转换。不是到账流水，也不据此推断实际收到股息 |
| yfinance 历史公司行为 | [get_actions](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_actions.html)；本项目 history 已入库对应字段 | 优先复用 daily_quotes；不重复全量请求，也不改变现有 P&L |
| yfinance 财报日期 | [get_earnings_dates](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_earnings_dates.html)：返回 DataFrame 或 None，默认 limit=12 | 候选低频采集与未来财报提示；历史日期可辅助展示，但不反推当时已经知道该日期 |
| yfinance 事件日历 | [Ticker.calendar](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.calendar.html)：字典形式的事件、财报、分红信息 | 补充日期／日期范围；来源未确认的未来财报一律标预计，保留时间精度，缺值不推断“无事件” |
| Futu 实时报价／盘口 | [行情接口总览](https://openapi.futunn.com/futu-api-doc/quote/overview.html)：订阅报价等接口与快照不同 | 本轮不做订阅、WebSocket 或自动刷新行情管线，避免权限、在线进程与运维成本 |
| 分析师、更多估值与资金流 | 现有 info 已取目标价／评级，已有资本流和账户资金功能 | 先改善现有数据解释；不堆重复指标，也不把评级包装成下单指令 |

## 3. 版本、时间与缺失值

2026-09-05 用 conda `mk` 的发行元数据核验：**yfinance 1.4.1、futu-api 10.7.6708**。Futu 在线文档页标题为 v10.10，不能假设所有列都被当前 SDK／当前权限返回。只读本地签名核验：`get_earnings_dates(self, limit=12, offset=0)`、`calendar` 为 property、`get_actions(self, period='max')`；未调用远端行情／账户接口。

- Futu `update_time` 与本机采集时间不是同一字段；HK 按 Asia/Hong_Kong、US 按 America/New_York 解释无时区源时间，转换后保留来源时区与原始值。旧数据的未知时区不补造。
- 证券未返回、字段不支持、OpenD 不可达、限频、空结果要能区分；不得用 0 代替未知，旧快照保留但注明过期／失败。
- 财报日期可变、可为一个区间；不能把范围中的首日写成已确认日期。港股覆盖、盘前盘后时点与日期完整性未在本轮实测。
- 同一事件的采集时间不能冒充公告首次发布时间；这些事件不满足 point-in-time 特征证明，不能偷偷用于历史模型或交易回测。
- 账户金额与合成示例分开；所有新展示继续注明币种。股息每股金额不是用户实际到账，拆合股方向必须由来源定义验证。

## 4. 成本控制与验收判断

三批都以现有单股详情和一个轻量数据状态入口承接，不重做全站布局。快照扩列与历史事件利用既有请求／数据；只有未来事件需要新接口，限定当前持仓与现有 ML 关注股的去重 HK/US 集合，按日缓存、串行节流、有限重试。

首批不做新模型、盘中推送、大规模历史补采、财务报表中心、盘口深度、期权或券商下单。API 暂不可用时交付明确状态和可用的其他批次；不得为了凑齐卡片编造数据，也不整体阻断既有更新流程。具体变更文件、返回协议、测试与交接要求以工单为准。


## 5. 依赖更新与新增 Futu 能力补查

本轮先按用户要求只查版本并讨论功能；随后用户明确批准升级 yfinance／futu-api，并只选择两个 P0。下列能力调研保留作为取舍依据，不自动成为执行范围；依赖与功能实际执行结果另见回执。

2026-09-05 14:50 UTC 直接查询 PyPI JSON，并与发布页核对：

| 包 | 本机 mk | 最新正式版 | 发布日期 | 建议 |
| --- | --- | --- | --- | --- |
| yfinance | 1.4.1 | 1.7.0 | 2026-08-26 | 值得安排兼容验证后升级；基础快照／历史事件展示不以升级为前提 |
| futu-api | 10.7.6708 | 10.10.7008 | 2026-08-13 | 若选新财报／派息日历或资讯接口，升级优先级提高；仅展示现有快照则不急 |

来源：[yfinance PyPI](https://pypi.org/project/yfinance/)、[Futu PyPI](https://pypi.org/project/futu-api/)。本机 Python 3.10.20、curl_cffi 0.15.0、pandas 2.3.3、protobuf 7.35.1；这些是元数据检查，不是新版安装或兼容性验收。当前 environment.yml 只设宽泛下限，未来验收通过后应记录实际验证版本组合，避免重建环境静默漂移。

[yfinance 1.7.0 完整变更记录](https://raw.githubusercontent.com/ranaroussi/yfinance/1.7.0/CHANGELOG.rst) 显示：从 1.4.1 往后修复 info 空结果处理、部分基本面请求超时降级、curl_cffi>=0.16 兼容问题，以及代理／价格修复相关问题。当前 curl_cffi 为 0.15.0，因此不能宣称本机已经发生 0.16 兼容故障。项目没有默认启用 repair；修复该功能不等于现有日线一定改善，升级时不得顺便开启 repair 或改复权口径。

[Futu 更新日志](https://openapi.futunn.com/futu-api-doc/changelog/changelog.html) 的 10.8 已列出财报／派息日历、搜索与更多基本面能力；10.10 还改变了命令行 OpenD 的账户密码配置方式。**Python SDK 与 OpenD 是不同组件**：本轮未核实当前运行 OpenD 的版本或登录方式，不能据 pip 版本判定网关已匹配，更不能自动改登录配置。

本机 quote context 源码静态核对：存在 `get_market_snapshot`、`get_corporate_actions_dividends`、`get_corporate_actions_stock_splits`；未见 `get_earnings_calendar`、`get_dividend_calendar`、`get_search_news`、`get_search_quote`。未建立网络上下文验证这些能力。

| 新候选 | 已查官方接口 | 约束与取舍 |
| --- | --- | --- |
| Futu 财报日历 | [get_earnings_calendar](https://openapi.futunn.com/futu-api-doc/quote/get-earnings-calendar.html) | 按市场查询，起止间隔不超过 7 天；不是单股无限历史接口，30 天看板需要分段并过滤关注股 |
| Futu 派息日历 | [get_dividend_calendar](https://openapi.futunn.com/futu-api-doc/quote/get-dividend-calendar.html) | 按市场单日查询，包含除净／登记／派息等日期；需考虑分页与多日请求成本，不直接默认采集全市场 30 天 |
| 新闻／公告 | [get_search_news](https://openapi.futunn.com/futu-api-doc/quote/get-search-news.html) | 关键词匹配非严格单股归属；最多 100 条，限频每 30 秒 10 次；先验证匹配质量，只考虑标题／时间／原文入口，不复制全文 |
| 财报前后表现 | [行情总览](https://openapi.futunn.com/futu-api-doc/quote/overview.html) 中 `get_financials_earnings_price_move`／`get_financials_earnings_price_history` | 可作为解释性页面候选；尚未验证本机可用性与字段，不据历史反应推断下次涨跌 |

上述为官方文档与本地 SDK 的静态证据，行情权限、股票覆盖及 SDK/OpenD 组合仍需后续独立验证。依赖升级建议先在隔离环境做导入、mock 回归及有限公开行情探测，确认后再安排运行环境升级；用户现已授权两个依赖升级；依照正式工单先验证后升级，结果以执行回执为准。


## 6. 用户最终选定范围

2026-09-05 用户确认：① 升级 yfinance、futu-api；② 只优化两个 P0。对应 DEP-01／WEB-01／WEB-02，详见正式工单。公司事件历史标记、未来财报／派息日历、新闻与财报表现均不实施，已暂停的 ML 模型升级继续暂停。

调研任务仅生成 ignored 环境清单与 pip dry-run 预检供 Astra 使用；这不等于已安装、已回归或已完成实际 mk 升级。
