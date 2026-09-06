# ML 隔夜信息 D1–D3 执行回执_Claude_20260906

> 作者：Claude（Fable 5.1）· 日期：2026-09-06 · 基线 `34653ef`（方案）/ `main`。状态：D1 数据表、D2 决策窗口、D3 特征版本已实现并验证，未接入生产预测器；D4 正式实验待用户决定。方案见 [新一轮方案](../plans/ml-overnight-plan_claude_20260906.md)。
> 边界：按用户授权，独立脚本向运行库 `data/ml/mystock_ml.db` **新增**表 `ml_external_1d` 并写入 ADR 日线与 `ml_sync_log`；未改任何既有表、未改 `predictor.py`／`ml.sh`、未重启 8888、未发布、未新增调度、未交易。写入前运行库已备份（`data/model-matrix-20260906/backup-before-external.db`，哈希与写入前一致）。

## 0. 结论

- **D1**：一个来源（yfinance）、每股一条序列（TCEHY／BABA／XIACY），新表每行带 `available_at`＝该美股交易日的最终确认时刻；已入运行库与冻结副本各 3026 行。
- **D2**：`sessions.preopen_window / check_preopen_decision` 定义港股开盘前决策窗口：最早为港股 as_of 收盘确认与同日历日美股 `final_at` 的较晚者，最晚为现行 09:00 HKT 截止；非港股、日历外一律拒绝。
- **D3**：`features.FEATURE_COLS_V1` 冻结 16 个特征；`FEATURE_COLS_V2 = V1 + adr_ret`；`attach_overnight` 做 as-of join，只取 `date ≥ as_of` 且 `available_at` **严格早于**目标日截止的外部行并复利，美股休市记 0，外部历史前记 NaN。生产 `predictor` 仍只用 `FEATURE_COLS`（= V1）。
- **端到端验证**：用新表＋新拼接函数重跑可行性探针，结果与 CSV 版一致；把外部数据整体后移一个美股交易日后改善塌缩到容量效应水平，证明增益来自时点正确的隔夜信息。
- 7 个新单测，全量 293 通过；Web 可导入。

## 1. 数据（D1）

| for_code | symbol | 行数 | 起止 | 最后 available_at |
| --- | --- | ---: | --- | --- |
| HK.00700 | TCEHY | 1255 | 2021-09-07 – 2026-09-04 | 2026-09-04T20:05Z |
| HK.09988 | BABA | 1255 | 2021-09-07 – 2026-09-04 | 2026-09-04T20:05Z |
| HK.01810 | XIACY | 516 | 2024-08-15 – 2026-09-04 | 2026-09-04T20:05Z |

三只 ADR 的所有日期都落在美股日历上，无零成交量行。采集脚本 `scripts/ml_experiments/fetch_external.py` 要求显式 `--db`，只写 `ml_external_1d` 与 `ml_sync_log`（source `yf_external_1d`），按 (symbol, date) 幂等；未接入 `ml.sh`。`rows_from_history` 拒绝非美股交易日的行和 `available_at` 晚于当前时刻的行。

## 2. 决策窗口（D2）与拼接规则（D3）

| 情形 | 规则 | 单测 |
| --- | --- | --- |
| 正常日（港股 as_of＝周五 09-04，目标 09-07） | 最早＝美股 09-04 `final_at` 20:05Z；截止＝港股 09-07 01:00Z | `test_preopen_window_normal_us_holiday_and_non_hk` |
| 美股休市（09-07 劳动节） | 最早＝港股 09-07 收盘确认；特征记 0（无新信息） | 同上、`test_attach_overnight_normal_holiday_and_multi_session` |
| 港股休市而美股开市（04-03／04-06／04-07） | as_of 04-02 累计 04-02、04-06、04-07 三根美股日线 | 同上 |
| `available_at` 等于截止 | 不进入特征（决策窗口把等于截止判为过期） | `test_attach_overnight_respects_available_at` |
| 外部数据晚一个交易日 | 拼接取到的是前一根，不会前看 | 同上 |
| 非港股 / 决策过早 / 过晚 | `Unavailable`：unavailable / awaiting_overnight / missed_deadline | `test_check_preopen_decision_statuses` |

## 3. 端到端验证（冻结副本，120-session 协议，只改特征集）

开发窗口内 adr_ret 非零天数：腾讯 117／120，阿里 118／120，小米 115／120（其余为美股休市记 0）。

正常对齐：

| 候选 | 侧 | 相对 B0 % | naive skill % | 改善股数 | 最差单股 % | 配对块 95% 区间 % | 腾讯 / 阿里 / 小米 |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| O2_overnight_small | low | 12.75 | 14.90 | 2/3 | -0.70 | [8.48, 16.90] | 12.3 / 26.6 / -0.7 |
| O2_overnight_small | high | 13.81 | 13.18 | 3/3 | 7.00 | [9.32, 18.56] | 14.8 / 19.7 / 7.0 |
| O3_small_only | low | -0.44 | 2.24 | 1/3 | -1.34 | [-2.72, 1.99] | -0.7 / -1.3 / 0.7 |
| O3_small_only | high | 3.38 | 2.61 | 3/3 | 1.99 | [0.67, 6.92] | 2.1 / 2.0 / 6.1 |

外部数据整体后移一个美股交易日（泄漏检验）：

| 候选 | 侧 | 相对 B0 % | naive skill % | 改善股数 | 最差单股 % | 配对块 95% 区间 % | 腾讯 / 阿里 / 小米 |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| O2_overnight_small | low | -0.94 | 2.45 | 0/3 | -1.46 | [-2.54, 0.75] | -0.4 / -0.9 / -1.5 |
| O2_overnight_small | high | 3.39 | 2.95 | 2/3 | -0.01 | [0.75, 7.00] | -0.0 / 3.0 / 7.2 |
| O3_small_only | low | -0.78 | 2.60 | 1/3 | -1.28 | [-2.49, 1.01] | 0.0 / -1.3 / -1.1 |
| O3_small_only | high | 2.44 | 1.99 | 2/3 | -0.61 | [0.07, 5.65] | -0.6 / 2.1 / 5.8 |

读法：O2 的 12.8%／13.8% 与 CSV 版可行性结果一致；后移后 O2 退到 O3（仅小容量）的水平，即隔夜特征在错误时点不再提供信息。小米（XIACY，训练行 282）低侧仍无改善，正式实验前需换代理或接受其为弱股。本表仍是对已查看开发窗口的再一次查看，单种子。

## 4. 代码变更

| 文件 | 变更 |
| --- | --- |
| `mystock/ml/schema.sql` | 新表 `ml_external_1d` 与索引（IF NOT EXISTS，`init_ml_db` 幂等） |
| `mystock/ml/external.py` | 新：映射、`available_at_for`、`rows_from_history`、`fetch`、`load_external` |
| `mystock/ml/sessions.py` | 新增 `preopen_window`、`check_preopen_decision`；既有函数未改 |
| `mystock/ml/features.py` | 新增 `FEATURE_COLS_V1/V2`、`OVERNIGHT_COLS`、`attach_overnight`；`build_features` 与 `FEATURE_COLS` 未改 |
| `scripts/ml_experiments/fetch_external.py` | 新：独立采集入口 |
| `scripts/ml_experiments/overnight_feasibility.py` | 改为读新表、用 `attach_overnight`，新增 `--shift` 泄漏检验 |
| `tests/test_ml_overnight.py` | 新：7 个单测 |

## 5. 未做与下一步

- 未做：D4 正式实验（五种子、更早 120-session 窗口、小米换代理）、D5 前向 shadow、D6 接入 ml.sh 与页面。
- 未做：live 版本留档尚未记录"开盘前决策时刻"，`predict_next_day` 未提供 V2 入口；这些属 D4/D5 范围。
- 建议 D4 预注册：候选只有 O2（V2＋7 叶／min_child 50）对 B0；种子 0–4；第二窗口取当前窗口之前的 120 个 session；小米改用 KWEB 作为对照候选单独报告；宽度／覆盖分开报。
