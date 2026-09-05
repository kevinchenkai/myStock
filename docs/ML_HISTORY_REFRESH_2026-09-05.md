# 近 20/60/120 个交易日历史补齐与重算

已在隔离工作树完成，截止目标交易日为 **2026-09-04**。六只股票的 18 个窗口全部逐日通过：日线已最终确认、小时线完整、当日目标预测存在；108 组固定策略/费用组合回放没有缺行情 session。工作树代码提交 `6cfa245`，未合并 main、未部署原服务、未发布静态报告。

## 覆盖结果

各市场三只股票均达到下表覆盖：

| 市场 | 交易日窗口 | 目标日起止 | 完整天数 |
| --- | --- | --- | --- |
| 美股 | 20 | 2026-08-10 → 2026-09-04 | 20/20 |
| 美股 | 60 | 2026-06-11 → 2026-09-04 | 60/60 |
| 美股 | 120 | 2026-03-17 → 2026-09-04 | 120/120 |
| 港股 | 20 | 2026-08-10 → 2026-09-04 | 20/20 |
| 港股 | 60 | 2026-06-11 → 2026-09-04 | 60/60 |
| 港股 | 120 | 2026-03-12 → 2026-09-04 | 120/120 |

共追加 **720 条** `recomputed` 预测。run_id：`97d92926b6a04ed6916ed12478765676`。旧预测版本逐行核对未改变，主库内容及 ML 中订单/成交/持仓事实也与操作前备份一致。输入冻结 SHA256：`be7d19a9403e306b91b01d8afa627bb9cfe488336ffc99d09292e3818ab404c4`。

## 数据修复与口径

- Yahoo 补采写入 9 条日线记录、63 条小时线记录（含重新确认的旧记录），消除最新日的数据缺口。所有写入均限定在检查发现的缺失/未确认日期。
- 2026-04-20 三只港股的 Yahoo 小时线在广窗和单日重查后仍各只有 4 根。通过本机 OpenD 只读 `request_history_kline`，显式 `K_60M / AuType.NONE` 获取各 6 根完整时段。没有访问交易接口或修改账户。
- Futu 的结束时间标签转换为交易所上午/下午分段的开始时间；没有插值或合成 OHLC。完整日 OHLC 与 Yahoo 日线一致，重叠上午时段也核对通过。小米第一根开盘价 Yahoo 为 32.04、Futu 为 32.02；Futu 与当日日线开盘 32.02 一致，因此整日统一采用 Futu，并把 -0.02 差异记入导入回执。其余重叠价格在浮点精度内一致。
- 该 3 个港股日共以 18 根完整 Futu 小时线替换 12 根不完整 Yahoo 小时线。数据库保存 `data_source=futu_none` 和原始文件 SHA256；页面逐日明细及选中日期均显示 Futu / 不复权。日线 K 线图仍使用 ML 库中的 Yahoo 日线。数据源边界已留痕，不能假定不同供应商的所有逐笔口径相同。
- 训练历史检查发现 2023-09-01、2023-09-08 属港交所确认的全天停市，已修正冻结日历及其生成器，未伪造行情。公告与版本说明见 [日历说明](../mystock/ml/calendars/README.md)。该修正不是对所有历史天气事件的完整认证。

## 预测重算方法

协议 `historical-daily-refit-120-v1`，沿用现有 IntervalModel 的容量、逐股分位、CQR 覆盖目标和 seed=0。每个目标日先切掉当日及以后日线，再构建特征、标签并重新拟合；训练/校准标签截止逐条记录，断言不超过 as_of，且 as_of 严格早于目标日。

此次按**连续市场 session**生成，包括先前实验共同样本筛选漏掉的除息日。不是复用旧 E0–E5 的过滤日期或每 20 日拟合结果。因此它是新的历史研究版本，不能与旧开发实验指标混成一个协议。没有晋级新模型、没有回写 live 时间、没有把重建计为真实前向 shadow。

## 验证与查看

209 项回归测试通过；新增测试覆盖目标/未来数据不能进入拟合、陈旧特征拒绝、全天停市、Futu 时段与价格核对、残缺响应不能删除原行情、来源字段迁移幂等性。另验证 18 个窗口和 108 个合成账户策略案例，冻结输入哈希不变，旧版本与交易事实不变。

六股 120 日三策略 API：冷缓存 1.31s，5 次暖缓存采样 p95 0.152s；未清 OS 文件缓存。合成验证使用 200000 本币初始现金；HK lot=100 是测试规则，非真实证券历史规则认证。费用与收益只用于工程验证。

打开 [本地预览](http://127.0.0.1:8896/ml-next)，预测来源选“包含离线历史重建”，选择 20/60/120。复盘明细会直接读取数据库，无需预生成页面；填写场景后点击“重新模拟当前窗口”才计算策略。默认 live 模式继续排除重建版本。

原运行库没有收到这些数据；Git 合并只带代码。将来部署需在目标副本重现补采/重算，或另行审查数据迁移与 run 输入路径，不能整库覆盖之后新增的交易事实。

## 私有证据与复现

工作树：`/Users/kk/.codex/worktrees/dc66/myStock`。

- 操作前主库/ML 库 SQLite 在线备份、Yahoo 原始补采、Futu CSV 和导入回执：`data/upgrade-output/history-refresh-20260905/`。
- 审计：`audit-before-repair.json`、`audit-after-rebuild.json`。
- 重算结果：`rebuild.json`、`rebuild.log`、逐股 predictions JSON。
- 验证：`verification.json`、`verify.py`、`verify.log`。
- 冻结输入与运行 manifest：`data/ml/runs/97d92926b6a04ed6916ed12478765676/`，包括输入哈希、Git SHA、依赖、日历、逐日截止及补采来源回执。

上述私有文件被 Git 忽略，不提交数据库、配置或账户事实。以下命令只对**已备份且确认路径的隔离副本**执行；audit 仅读，repair 会联网写指定 ML 库，rebuild 只读冻结输入并追加历史版本：

```bash
conda activate mk
python -m scripts.ml_experiments.rebuild_history audit --db data/ml/mystock_ml.db --out data/upgrade-output/history-new-run --end 2026-09-04
# 有行情缺口时才执行；不会访问券商交易接口：
python -m scripts.ml_experiments.rebuild_history repair --db data/ml/mystock_ml.db --out data/upgrade-output/history-new-run --end 2026-09-04
# Yahoo 仍缺小时线时，先检查供应商原始响应；不要伪造或放宽完整性条件。
# 经审查的 Futu 不复权 CSV 可用 import_futu_hourly --help 查看导入参数。
python -m scripts.ml_experiments.rebuild_history rebuild --db data/ml/mystock_ml.db --out data/upgrade-output/history-new-run --end 2026-09-04
```

Claude review 应包含本批代码（`446e657..HEAD`），重点核查日期切片/标签截止、来源转换、旧库迁移和三只港股的补采证据。历史重算完成不等于生产上线获批。
