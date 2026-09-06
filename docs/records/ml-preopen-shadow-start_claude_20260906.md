# ML 开盘前预测工程接入与 D5 shadow 启动回执_Claude_20260906

> 作者：Claude（Fable 5.1）· 日期：2026-09-06 · 基线 `d38bdbd`（美股 D4）/ `main`。状态：V2 预测入口、决策时刻留档、`ml.sh shadow HK|US`、Futu 盘前快照采集（含 yfinance 备用）已实现；**港股第一条 shadow 已于 2026-09-06 14:03 UTC 留档**（目标日 09-07）；美股 shadow 从 09-08 09:00 ET 起人工触发。生产默认仍为 V1，页面与公网报告不读 shadow 行。
> 边界：写入运行库的只有 `ml_prediction_versions` 中 status=`shadow` 的追加行、`ml_external_1d` 增量 ADR 行与 `ml_sync_log`；未改既有生产行、未改 `predictor` 的 V1 路径行为、未重启 8888、未发布、未新增调度、未交易、未启动 Futu OpenD。

## 0. 数据回溯长度（用户问题的结论）

| 数据 | 回溯 | 依据 |
| --- | --- | --- |
| 日线 | 5 年（不变） | 训练主体 |
| 港股 ADR | 5 年，已抓全 | 与日线对齐 |
| 美股盘前价 | 2 年，已抓全 | yfinance 盘前小时线上限 730 天；D4 W2 首块约 300 行仍过门槛；此后由 Futu 快照逐日累积 |
| 训练下限 | 250 行（`predictor.V2_MIN_ROWS`） | 带新特征的可用行不足时该股 V2 失败关闭，不硬训 |

## 1. 实现

| 组件 | 变更 | 生产影响 |
| --- | --- | --- |
| `predictor.predict_next_day` | 新参数 `feature_version`（默认 `v1`）与 `external`；`v2` 拼接港股 `adr_ret`／美股 `pre_ret`，容量用预注册 `V2_PARAMS`，live 守卫改用开盘前窗口，最新行特征缺失或训练行不足失败关闭；输出新增 `lo_ret_raw`／`hi_ret_raw` 与 V2 元数据 | `v1` 路径参数与行为不变 |
| `IntervalModel` | 新增 `feature_cols`、`params` 字段，默认值即 V1 | 默认行为不变 |
| `features.attach_overnight` | 窗口内应有的美股交易日缺行时记 NaN（数据未到），只有真正的美股假日才记 0 | 仅 V2 使用 |
| `mystock/ml/shadow.py` | data（港股增量 ADR；美股 Futu 快照，失败转 yfinance `preMarketPrice`）→ 同一时钟下 V2 与 V1 预测 → 以 `status=shadow`、`source=shadow_v2/shadow_v1`、独立 run_id 追加版本表 → 回执 `data/ml/receipts/shadow-<市场>-<时刻>.json` | `versions.select_by_target` 与默认 `load` 都排除 `shadow`，Web／报告不可见（有单测） |
| `scripts/ml.sh shadow HK|US` | 新子命令；`data`／`train`／`publish`／`all` 不变 | 无 |
| `scripts/ml_experiments/shadow_report.py` | 已成熟 shadow 行的 raw pinball、覆盖、宽度，V2 对 V1 | 只读 |
| 测试 | 新增 2 个（V2 入口与失败关闭；shadow 留档与隔离），全量 301 通过；Web 可导入 | |

## 2. 运行方式（人工触发，不调度）

```bash
bash scripts/ml.sh shadow HK    # 港股：美股收盘确认后至 09:00 HKT，建议 08:30 HKT
bash scripts/ml.sh shadow US    # 美股：09:00–09:30 ET，建议 09:00 ET（Futu OpenD 在线；否则自动转 yfinance 备用并在回执标明）
python -m scripts.ml_experiments.shadow_report   # 随时查看已成熟 session 的 V2 对 V1
```

窗口外运行会按状态拒绝（`awaiting_overnight`／`missed_deadline`），不会留下错误时点的记录。现有 `ml.sh train` 仍在收盘后运行，生产预测不变。

## 3. 第一条港股 shadow（2026-09-06）

run_id `shadow-HK-20260906T140340`，决策时刻 2026-09-06T14:03:40Z，目标日 2026-09-07（美股 09-04 收盘已确认，港股 09-07 开市而美股休市）。

| 股票 | V2 区间 | V2 宽度 % | ADR 隔夜收益 | V1 区间 | V1 宽度 % |
| --- | --- | ---: | ---: | --- | ---: |
| HK.00700 | 434.9945 – 453.8907 | 4.27 | 1.34% | 432.2218 – 452.2508 | 4.52 |
| HK.09988 | 107.7334 – 115.1091 | 6.7 | 1.28% | 105.8961 – 113.5733 | 6.97 |
| HK.01810 | 27.554 – 29.2818 | 6.08 | 2.10% | 27.5681 – 29.0945 | 5.37 |

六条记录（三股 × V2／V1）追加到版本表；生产可选行数与旧投影表行数在运行前后不变。

## 4. 未做与注意

- 美股 Futu 快照路径只有单测覆盖，首次实盘在 09-08 09:00 ET；若 OpenD 未运行会转 yfinance 备用，回执 `data.futu_error` 会记录原因。两来源的口径差在 shadow 期间对照。
- 页面与公网报告尚未标注"开盘前预测"；切换前不展示 shadow。
- `shadow_report` 只在目标日日线入库后才有成熟样本；60 个 session 约需三个月日历时间。
- 本回执与前四份一并交 Codex Astra 审核。
