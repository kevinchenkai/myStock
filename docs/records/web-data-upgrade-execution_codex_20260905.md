# Web 两项 P0 与依赖升级执行回执

> 作者 Codex · 2026-09-05 · 基线 `fc32045`。按用户后续指示直接在本地主目录 `/Users/kk/Work/Workpace/GitHub/Seattle/myStock` 的 `main` 实施；初建工作树已迁回并清理，未保留执行分支。
> 状态：实际 mk 依赖升级完成；WEB-01／WEB-02 实现与隔离验收完成，生产 Web 部署待执行。本轮只实现 [正式工单](web-data-upgrade-work-order_codex_20260905.md) 的三个交付项。

## DEP-01：实际依赖与恢复证据

| 包 | 升级前 | 实际 mk 验收版本 |
| --- | --- | --- |
| yfinance | 1.4.1 | 1.7.0 |
| futu-api | 10.7.6708 | 10.10.7008 |
| lxml | 未安装 | 6.1.3（新增依赖） |

Python 3.10.20、pandas 2.3.3、numpy 2.2.6、curl_cffi 0.15.0、protobuf 7.35.1、lightgbm 4.6.0 等既有包版本保持不变。前后全量发行元数据逐项比较，实际变更只有表中三项。`environment.yml` 精确固定两个目标包版本。

先克隆 mk 建立私有候选环境，保存原版和新版安装包；Futu 源码包还构建了 wheel，避免离线恢复临时依赖构建服务器。候选环境实际降回旧两包、移除新增 lxml 后，完整包版本集合与基线完全相同，`pip check` 通过；随后恢复候选新版，兼容验证通过后才更新实际 mk。基线、候选和升级后的实际 mk 均无 pip 依赖冲突。升级前未检测到本机正在运行的 myStock 采集／训练模块。

旧环境 50 项解析／状态测试通过；候选和实际 mk 各 89 项相关测试通过，覆盖解析、同步、数据库、日历、只读 API 等；不包含 ML 模型拟合。最终实际 mk 扩展回归为 112 项通过，另有 JavaScript 语法、文档与 diff 检查通过。

公开冒烟仅查询 `US.AAPL`、`HK.00700`：新版 yfinance 各返回 4 条日线及可用公司资料，保留 Close／Adj Close／dividends／stock_splits；Futu 快照均通过字段规整。OpenD `server_ver` 返回 `904`，上述既有接口兼容；没有升级或重启网关，没有改登录配置。此结果不证明所有股票、账户行情权限或新 SDK 的其他功能均可用。官方依据：[Futu 快照](https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html)、[yfinance 版本](https://pypi.org/project/yfinance/1.7.0/)、[Futu SDK 版本](https://pypi.org/project/futu-api/10.10.7008/)。

私有证据统一在 `data/web-upgrade/`：环境清单、包差异、安装报告、原版／新版 wheel、回退演练、测试及公开冒烟日志。可在项目根目录使用已演练的离线依赖回退：

```bash
/opt/anaconda3/envs/mk/bin/python -m pip install --no-index --only-binary=:all: --no-deps --find-links data/web-upgrade/wheels yfinance==1.4.1 futu-api==10.7.6708
/opt/anaconda3/envs/mk/bin/python -m pip uninstall -y lxml
/opt/anaconda3/envs/mk/bin/python -m pip check
```

这些命令只针对本轮准确的三包差异；后续若其他功能开始依赖 lxml，须重新核对再回退。

## WEB-01／WEB-02：已完成行为

- 首页可折叠数据状态：区分来源内最新业务日期、最近尝试、最近成功及个股缓存；汇总成功不冒充每股成功。
- 个股日线按交易所时区和既有离线日历判断应覆盖的最近确认收盘日，避免休市／夏令时误报。未知时间、缺数据、盘中、日历越界分别展示；浏览器时区不参与业务判断。
- 个股 Futu 缓存卡展示本币价格、昨收涨跌、日内高低、量比、停牌、每手股数、来源时间及采集时间；0 和未知分开。证券未知枚举不解释为正常；报价档位间隔只在说明内展示，不用于交易或回溯。
- 增加 `collection_status` 的逐股结构化尝试结果；快照按每批最多 400、批间 0.6 秒请求。完整有效响应才更新快照缓存；partial／empty／error／unsupported／unknown 保留原缓存及最近完整成功时间。日线和资料也记录后续逐股尝试；旧汇总不追补虚构明细。
- yfinance 公司资料异常交给管线记录为 error；真实空结果记录 empty。Web 不输出供应商原始异常、账户号或私有数据库路径。
- 新只读接口 `/api/data-status`、`/api/stock/<code>/snapshot`。旧库缺扩展字段／状态表时返回不可用或需升级提示，不在 GET 中迁移。关闭／切换详情会释放 K 线与资金流图的 ResizeObserver，过期异步响应不会覆盖新详情。

结构升级为 additive，快照字段与 yfinance 资料时间独立；隔离库迁移幂等、SQLite 在线备份／复制恢复和只读 GET 不改变数据库字节均已验证。新主库采集时间使用带 UTC 偏移的 ISO 文本；历史无时区文本保留并标未知，不重写旧订单／成交的交易所业务时间。

## 预览、部署和边界

合成数据预览：<http://127.0.0.1:8891/>，使用 `data/web-upgrade/preview.db`，含正常缓存、失败旧缓存、停牌和未知数据。Playwright 已通过 1200 / 390 像素、浅色／深色、正常／失败旧缓存／未知三种场景共 12 组检查：卡片无横向溢出，关闭后两类图表与观察器均释放，页面脚本错误为 0。美西／东京浏览器时区下业务日线状态一致。截图和浏览器检查证据在同一私有目录；预览不读取真实持仓。

生产 Web 尚未迁移或重启。后续获准部署时：先在线备份主库并验证恢复；运行独立 CLI 初始化以添加表／列，再运行例行采集产生新快照和逐股状态；最后重启 8888 并核对两条新 API。旧库可直接被新 Web 只读打开并显示升级提示，不能通过打开页面来完成迁移。代码回退可恢复旧 Web；新增表／列可保留，若需恢复数据库则使用一致性备份，避免覆盖部署后新增的账户事实。

本轮按用户要求在 main 直接提交推送，未执行分支合并。已升级实际 mk 两包；未迁移或写入运行主库／ML 库，未升级或重启 OpenD，未重启 8888，未发布公网报告，未运行 ml.sh train/all/publish，未新增调度，未进行账户查询或真实交易。公开冒烟只使用行情上下文。公司事件、财报日历、新闻和 ML 模型升级均未实现。P2-3 的证券规则历史／回溯参数接入继续待办；生产部署及依赖恢复包保留事项见 [未尽事项](../OPEN_ITEMS.md)。
