# myStock ML 升级方案 —— 预测准确性 + 「ML 挂单回溯」可用性（Claude 讨论稿 v0.1）

> **状态导航（2026-09-05）**：以下保留原阶段的方案／记录，文中的“当前”、隔离目录、端口及未部署状态均按当时理解。四批工程现已合入 main 并部署，模型未晋级；当前使用与恢复见 [工程交接](ML_UPGRADE_HANDOFF_2026-09-04.md)，验收见 [部署回执](ML_DEPLOYMENT_2026-09-05.md)，全部资料见 [文档索引](README.md)。

> 作者：Claude · 日期：2026-09-04 · 状态：**只读调研 + 方案，未改任何代码**
>
> 依据：逐文件读 `mystock/ml/`（19 文件）、`mystock/web/app.py` 的 `/api/ml/*`、前端 `app.js` ML 段、
> 六份 ML 文档（[`ML_PLAN`](ML_PLAN.md) / [`ML_OVERVIEW`](ML_OVERVIEW.md) / [`ML_ALGORITHM_PROPOSAL`](ML_ALGORITHM_PROPOSAL.md) /
> [`ML_QLIB_BORROW_PLAN`](ML_QLIB_BORROW_PLAN.md) / [`ML_TIER1_ROBUSTNESS`](ML_TIER1_ROBUSTNESS.md) / [`DATA`](DATA.md)），
> 并对真实 ML 库（`data/ml/mystock_ml.db`，截至 2026-09-04）做了只读统计与两个 scratchpad 实验（脚本未入库，数字已固化在附录）。
> 测试基线：`pytest tests/ -q` → **167 passed**（mk 环境：lightgbm 4.6.0 / sklearn 1.7.2 / pandas 2.3.3 / numpy 2.2.6）。

---

## 0. TL;DR（结论先行）

**两个用户目标，四条硬发现，一条主线。**

1. **预测层没有超过「波动率缩放的朴素基线」。** 用同一套 purged walk-forward 切分，把训练集里 `y/vol_20d` 的经验分位乘以当日 `vol_20d` 当区间（零参数、零模型），pinball loss 与现网 LightGBM 分位模型**打平**，六标的里四支朴素基线反而更小（§3.1 表）。现网 16 个特征、单种子、无早停的 LightGBM 学到的，基本只是「波动率越大区间越宽」——这正是 `width_IC 0.14–0.31、mid_IC≈0` 在说的话。**「提准确性」的头号抓手不是换更大的模型，而是把波动率/幅度这条主信号做深，并换一把能量出增量的尺子。**
2. **「区间覆盖率」和「挂单成交」是两个相反方向的目标，现网用同一个区间同时干两件事。** CQR 目标 70% 覆盖把区间撑到 **5.8–8.8%** 宽，而六支标的真实日振幅只有 **2.5–4.6%**。结果：线上留档 49 个交易日里，**买卖双边同日成交的天数 = 0**（六支全部为 0），单边买成 8–28%、卖成 11–31%。「挂单回溯」页展示的盈亏，本质是单边成交累积出来的**方向性净持仓再按期末价折算**，不是「低买高卖吃区间」；同一支标的窗口从 20 天换到 40 天，盈亏从 −2,589 翻到 +16,509（HK.00700）。**页面不可用的根因在策略定义，不在展示。**
3. **静态 split-CQR 的线上覆盖率离散得厉害**（51% / 79% / 77% / 55% / 73% / 53%，目标 70%），walk-forward 里则系统性**超覆盖**（73–81%）。校准半宽 q 来自训练尾部一次性切出的校准集，purge 之后离测试窗更远，不随近期行情自适应——覆盖率要么虚高（区间白白变宽）要么失守（HK.00700 上破 15 vs 下破 7，方向偏置）。
4. **但「多日轮回」是成立的。** 用留档预测做「买成后逐日按当日 Ĥ 挂卖直到成交」：六支合计 **39/43 次买入在 4–7 个交易日内完成卖出**，毛利 +0.3%～+5.1%（§3.3）。人类真实挂单也是这个形态（买单挂前收 −2.3%～−5.3%、卖单 +2.5%～+4.5%，跨日有效，成交率 44–94%）。**「挂单回溯」应从「同日双边、无限库存」重定义为「多日库存式区间交易、有仓位上限、扣费」**——这才是能跟真实操作对上的策略。

**主线**：`评估地基（换尺子）→ 预测层做深波动率/幅度信号 + 自适应校准 + 直接建模「触价概率」→ 策略 v2（多日库存式）→ Web 端把「明日挂单建议 / 逐日复盘 / 与你真实挂单对照」串成一条闭环`。分三期，每期有可量化的 go/no-go；沿用项目纪律——**打不过基线就停、多时段验证、不做 RL、Web 只读、绝不自动下单**。

---

## 1. 调研范围与方法

| 对象 | 怎么看的 |
| --- | --- |
| 代码 | `mystock/ml/` 全部模块（config/data/features/predictor/cv/calibrator/calibrate/simulator/policy/backtest/strategy/review/backfill/report/fetch/db/signal_eval/offline_rl），`web/app.py::api_ml_strategy`，`app.js` 1547–1700 行，`index.html` ML 面板 |
| 数据 | `ml_quotes_1d`（每股 1,279–1,307 行，2021-06 起）、`ml_quotes_1h`（每股 5,424–5,446 行，2023-06/07 起，常规日 7 根）、`ml_predictions`（每股 48–50 条，2026-06-22 起，source = backfill 135 / recomputed 90 / live 69）、`ml_orders`（每股 61–238 单）、`ml_deals`、`ml_sync_log` |
| 产物 | `data/ml/reports/2026-09-04/index.html`（总览 + 近期预测复盘） |
| 实验 | A：模型 vs 朴素基线 vs 加特征（同 purged 4 折，CQR 关）；B：留档预测的挂单经济性（逆向选择、多日轮回） |
| 运行 | `python -m mystock.ml.predictor`（14.5 s）、`pytest`（4.2 s）、`/api/ml/strategy` 延迟（4 支 / 30 天 ≈ 1.05 s） |

> 本文所有「现网数字」以 2026-09-04 库为准；日后重跑会因增量数据微动，结论不变。

---

## 2. 现状盘点（一页看懂现在是什么）

```
fetch.py  ──5y 日线 + 2y 1h + 生产库快照──►  mystock_ml.db
predictor.py  build_features(16 特征) → LightGBM quantile(α_lo/α_hi 按股) × 2
              └─ IntervalModel.fit: 训练尾 25% 做 split-CQR 校准 → q（ret 空间半宽）
backtest.py   walk-forward（train_frac 0.6 + purge 22）→ 规则 S0 / LinUCB / 人类回放 / 买入持有
report.py     每日 HTML：总览 + 近期预测复盘（review.py）+ 写 ml_predictions(live)
strategy.py   Web 实时：按 ml_predictions 的 [L̂,Ĥ] 次日同时挂买/卖各一手 → 1h 撮合 → 盈亏 + 4 种收益率
app.py        GET /api/ml/strategy?codes=&days=   （只读 ML 库；唯一的 ML 接口）
```

**预测层要点**
- 标签：`y_high_ret = high(T+1)/close(T) − 1`、`y_low_ret` 同理；**隔夜跳空与日内振幅混在一个标签里**。
- 特征（`features.FEATURE_COLS`）：ret 1/5/10d、vol 5/20d、ATR14、MA 5/10/20 偏离、收盘在日内位置、日振幅、跳空、20 日高低距离、量比 5/20。**全部来自日线；1h 数据只用于撮合，不做特征；无日历、无跨标的/指数、无财报日、无资金流向。**
- 模型：`LGBMRegressor(objective=quantile, n_estimators=300, lr=0.03, num_leaves=15, min_child_samples=30)`，high/low 各一个，**单种子、无早停、无分位交叉约束、每股独立训练（约 1,000 行）**。
- 分位档：`ALPHA_BY_CODE` 手调（0.20/0.80，PDD 0.25/0.75）；`COVERAGE_BY_CODE` 默认 0.70。`ML_QLIB_BORROW_PLAN §2.4` 已指出这里存在「同批数据调档」的窥视，锁箱 holdout 尚未落地。
- 校准：split-CQR（Romano 2019）静态半宽；**没有随时间自适应**。

**决策层要点**：S0 规则 + LinUCB（13 臂）+ 超额奖励。Tier1 的风险调整 reward 与 HMM regime 已被多时段检验证伪并移除；CQL 离线 RL 为负结果。**决策层不是本轮重点**（见 §7「不做的事」）。

**Web「ML 挂单回溯」要点**
- 只有一个接口、一种策略、一个可调参数（`days`）。策略假设「现金与持仓充足」，净持仓可无限漂移（实测 HK.00700 累到 −800 股）。
- **Web 端看不到「明天该挂什么」**——线上次日预测只在 HTML 报告里；「近期预测复盘」也只在报告里。Web 与报告是两条平行轨道，用户要在两个地方来回切。
- 未扣费用；无基线对照；无日期范围；每次请求全量重算（读整张 1h 表）；表格无法跳转个股 K 线看当日 L̂/Ĥ 与真实高低的相对位置。
- 时序：机器时区为 PDT，cron 实际 02:40 PDT 跑完（美股前一交易日收盘后、港股当日收盘后），能赶在两个市场开盘前给出预测——**满足「收盘后预测、开盘前挂单」的时序**；但 `ml.sh` 注释写的「北京时间早 8 点」与实际不符，文档需修。

---

## 3. 关键发现（带数字）

### 3.1 预测层：模型 ≈ 朴素波动率基线（实验 A）

同一套 `purged_walk_forward(n_folds=4, min_train=250)`，α 按股（0.20/0.80，PDD 0.25/0.75），CQR 关。
`naive_vol` = 训练集 `quantile(y/vol_20d, α) × 测试日 vol_20d`（零参数）；`lgb_base` = 现网；`lgb_extra` = 现网 + 8 个廉价特征（ret_20d、vol_60d、vol_5/20 比、5 日均振幅、前日振幅/ATR、星期几、|ret_1d|、5 日高低位置）；`lgb_extra_x` = 加特征 + 强正则（600 树、lr 0.02、num_leaves 7、min_child 50）。

| 标的 | 模型 | pinball（L/H 均值，越小越好） | 命中 | 宽度% |
| --- | --- | --- | --- | --- |
| US.NVDA | naive_vol | 0.00705 | 0.601 | 7.08 |
| | lgb_base | 0.00700 | 0.505 | 6.24 |
| | lgb_extra_x | **0.00690** | 0.534 | 6.49 |
| US.TSLA | naive_vol | 0.00836 | 0.601 | 8.53 |
| | lgb_base | **0.00827** | 0.508 | 7.34 |
| | lgb_extra_x | 0.00828 | 0.496 | 7.38 |
| US.PDD | naive_vol | **0.00907** | 0.514 | 6.52 |
| | lgb_base | 0.00914 | 0.541 | 6.85 |
| | lgb_extra_x | 0.00913 | 0.560 | 6.92 |
| HK.00700 | naive_vol | 0.00503 | 0.605 | 5.35 |
| | lgb_base | 0.00511 | 0.519 | 4.77 |
| | lgb_extra_x | **0.00499** | 0.548 | 4.92 |
| HK.09988 | naive_vol | **0.00717** | 0.566 | 6.49 |
| | lgb_base | 0.00734 | 0.538 | 6.20 |
| | lgb_extra_x | 0.00720 | 0.555 | 6.31 |
| HK.01810 | naive_vol | 0.00642 | 0.611 | 6.96 |
| | lgb_base | 0.00658 | 0.480 | 5.90 |
| | lgb_extra_x | **0.00641** | 0.512 | 6.11 |

读法：
- pinball 差异全部在 **±3% 以内**；朴素基线在 PDD / 09988 / 00700 / 01810 上**不输甚至更好**。现网模型的「信号」≈ 波动率缩放。
- 「命中率」列不能拿来比模型：朴素基线命中更高只是因为它更宽。**命中率必须与宽度一起看，或者干脆换成 pinball / CRPS 相对基线的 skill score。**
- 加特征 + 强正则有**一致的微小改善**（5/6 标的 pinball 下降），说明现网 `num_leaves=15、300 树、无早停` 在 ~1,000 行样本上略过拟合；但幅度小，单靠加特征不够。

### 3.2 覆盖率 vs 成交：一个区间干不了两件事

**留档预测（source 全部）与真实次日对照（每股 47–49 个已结算日）：**

| 标的 | 命中(双侧全包) | 次日 low ≤ L̂ (可买成) | 次日 high ≥ Ĥ (可卖成) | **同日双边** | 区间宽% | 真实振幅% |
| --- | --- | --- | --- | --- | --- | --- |
| HK.00700 | 55.1% | 14.3% | 30.6% | **0** | 5.90 | 3.14 |
| HK.01810 | 53.1% | 18.4% | 28.6% | **0** | 8.07 | 4.55 |
| HK.09988 | 73.5% | 8.2% | 18.4% | **0** | 8.88 | 3.78 |
| US.NVDA | 51.1% | 27.7% | 21.3% | **0** | 6.32 | 3.10 |
| US.PDD | 76.6% | 12.8% | 10.6% | **0** | 5.78 | 2.52 |
| US.TSLA | 78.7% | 8.5% | 12.8% | **0** | 8.53 | 3.84 |

区间宽度是真实振幅的 **1.9–2.3 倍**——这是「70% 覆盖」的必然代价，也是双边同日不可能成交的直接原因。

**同日双边成交在物理上就很稀：** 用 `close×(1±x%)` 对称带看近 250 个交易日——±1% 时双边同日触达仅 10–29%，±2% 时 0–4%。**任何以「同日双边」为回合的策略，在这六支上都注定是单边累积库存。**

### 3.3 挂单经济性：单边成交有逆向选择，多日轮回却是正的（实验 B）

用留档预测 + 1h 撮合回放：

| 标的 | 买成次数 | 买成后当日收盘 vs 成交价 | 5 日后 | 卖成次数 | 卖成价 vs 当日收盘 | 多日轮回完成 | 均耗时 | 轮回毛利 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| US.NVDA | 13 | +0.08% | +2.58% | 10 | −0.12% | 13/13 | 4.6 天 | +2.29% |
| US.TSLA | 4 | **−3.02%** | −2.74% | 6 | −1.01% | 3/4 | 6.7 天 | +0.34% |
| US.PDD | 6 | −0.40% | +2.74% | 5 | −0.88% | 4/6 | 4.5 天 | +2.38% |
| HK.00700 | 7 | −1.11% | −2.31% | 15 | +0.57% | 6/7 | 6.8 天 | +0.58% |
| HK.09988 | 4 | +0.02% | +2.47% | 9 | −0.33% | 4/4 | 4.2 天 | +5.11% |
| HK.01810 | 9 | +0.26% | +0.29% | 14 | −0.45% | 9/9 | 6.0 天 | +0.68% |

- **逆向选择真实存在**：能跌到 L̂ 的日子往往是趋势日，当日收盘多在成交价之下（TSLA −3%、00700 −1.1%）；卖成同理。**「触价 = 好价」不成立，触价概率与触价后的收益要分开建模**（§5 A4）。
- **多日轮回 39/43 完成、毛利全部为正**。这不是收益承诺（样本 49 天、单一 regime），但足以说明**策略形态应改为多日库存式**，与人类真实挂单形态一致。

### 3.4 校准不自适应

- walk-forward：CQR 目标 70%，实测 **73–81%**（超覆盖），宽度被撑大 **+27%～+46%**（6.21→8.81、7.32→10.32、7.39→10.78、5.03→6.38、6.37→8.64、5.86→8.15）。
- 线上：**51%～79%**（同一目标）。两端都偏说明 q 与近期行情脱节——校准集固定取训练尾部（purge 后离测试更远），且一次算完不更新。
- 方向偏置：HK.00700 上破 15 / 下破 7；PDD/TSLA 下破略多。**单一对称 q 修不了单侧偏置。**

### 3.5 Web「挂单回溯」的可用性缺口

| 缺口 | 现状 | 影响 |
| --- | --- | --- |
| 看不到「明日建议」 | 次日 [L̂,Ĥ] 只在 HTML 报告 | 用户真正要用的信息不在 Web |
| 策略不可信 | 同日双边、无限库存、不扣费 | 盈亏是方向性净持仓的折算，随窗口翻转（00700：20 天 −2,589 / 40 天 +16,509 / 49 天 +15,032；01810：+248 / −895 / −1,239） |
| 无基线 | 没有「朴素带」「买入持有」「你的真实挂单」对照 | 无法判断模型有没有增量 |
| 复盘割裂 | 命中/上破/下破在报告，不在 Web | 无法一屏看「预测→挂单→成交→复盘」 |
| 无下钻 | 表格行不能跳到个股 K 线看 L̂/Ĥ 在图上的位置 | 直觉校验缺失 |
| 性能 | 每次请求全量读 1h 表并重算（≈1 s/4 支/30 天） | 多窗口、多策略对比会线性变慢 |
| 时序文档 | `ml.sh` 注释「北京 8 点」，实际 PDT 02:40 | 接手者误判预测时效 |

### 3.6 工程债（顺手记录，不阻塞）

- `fetch.py`：2026-07-06 一次 yfinance 限频造成 17/16 条错误日志，靠次日增量自愈；标的间无节流（生产侧 `yf_client` 已有节流，可复用思路）。
- HK 有 2–6 根 bar 的交易日（半日市 / 缺根），撮合器按已有 bar 顺序遍历，语义正确但**回溯页没标注**。
- `predict_next_day` 每次全历史重训（6 支 × 2 模型 + CQR），报告与 `backfill.recompute_gaps` 都靠它；无模型持久化、无版本号——线上预测不可复现到「哪一版模型」。
- `ml_predictions` 只存区间，不存**分位原始值**（去 q 前）、不存模型版本 → 事后无法分离「模型」与「校准」的责任。

---

## 4. 目标与验收（先定尺子，再动手）

> 教训来自 [`ML_TIER1_ROBUSTNESS`](ML_TIER1_ROBUSTNESS.md)：单种子 + 单窗口的「改善」42% 概率是噪声。本轮所有门槛都要求**锁箱 holdout + 多时段 + 多种子**三者同时满足。

### 4.1 预测准确性的新主指标

| 指标 | 定义 | 为什么 |
| --- | --- | --- |
| **Pinball skill** | `1 − pinball(model) / pinball(naive_vol)`，L/H 分别算 | 直接量「比波动率缩放多学到了什么」，§3.1 已证明这是唯一诚实的尺子 |
| **覆盖偏差** | 滚动 60 日实测覆盖率 − 目标覆盖率 | 校准是否自适应；目标 ±5 pt |
| **触价概率校准** | 对「买挂在 close×(1−d)」的预测触价概率 vs 实际触价频率的 ECE | 挂单策略真正消费的量 |
| **宽度 IC** | 沿用 `signal_eval.width_ic` | 保留，作诊断 |

命中率、mid_IC 降级为展示指标，不再作为门槛。

### 4.2 go/no-go 门槛

| 阶段 | 门槛（不达即停在上一阶段，如实记录） |
| --- | --- |
| P-A 评估地基 | 报告与 `predictor.__main__` 输出 skill / 覆盖偏差 / 触价 ECE；锁箱 holdout（最近 120 个交易日）独立于调参数据 |
| P-B 预测层 | 锁箱上 **pinball skill ≥ +3% 且 ≥ 4/6 标的为正**；5 种子 std/mean ≤ 0.3；滚动覆盖偏差在 ±5 pt 内的标的 ≥ 5/6 |
| P-C 触价模型 | 触价 ECE ≤ 0.05（10 桶）；按触价概率分桶的实际触价单调 |
| P-D 策略 v2 | 三个不同起点的 120 日窗口里，**扣费后成交额收益率符号一致 ≥ 4/6**；与「朴素带 v2」对照不劣于 4/6 |
| P-E Web 联动 | 全部纯展示；`pytest` 全绿；`node --check app.js` 通过；ML 库缺失仍 503 且其余页面正常 |

---

## 5. 升级方案 A：提升预测准确性

按「改动小、证据强」排序。每条给：做什么 / 为什么 / 改哪里 / 门槛。

### A0 评估地基：换尺子、上锁箱、多种子（P-A，1–2 天）

- **新增 `mystock/ml/baselines.py`**（纯函数）：`naive_vol_interval(train_df, test_df, lo_a, hi_a)`，以及后续策略层要用的「朴素带 v2」（§6 B2）。
- **`predictor.walk_forward_eval`** 增加 `pinball_skill_low/high`、`coverage_bias`（按折）、多种子循环（`seeds=(0,1,2,3,4)`，输出 mean/std）。
- **锁箱**：`config.LOCKBOX_DAYS = 120`；`walk_forward_eval` 与 `backtest` 默认**不碰**最近 120 个交易日；新增 `--lockbox` 开关只在验收时跑。`ALPHA_BY_CODE / COVERAGE_BY_CODE` 的任何改档都要在锁箱上复核。
- **报告**：总览表加「skill」「覆盖偏差」两列，替换掉容易误读的「命中率」（命中率挪到复盘面板）。
- 测试：`tests/test_ml_baselines.py`（朴素基线的分位单调、NaN 安全）、`test_ml_cv.py` 追加锁箱切分用例。

### A1 把「幅度/波动率」这条主信号做深（P-B，核心）

现网学到的就是波动率缩放，那就把波动率估计做到最好——这是小样本下**最确定**的增量来源。

1. **标签分解**：`y_high_ret = gap + intraday_up`，其中 `gap = open(T+1)/close(T) − 1`，`intraday_up = high(T+1)/open(T+1) − 1`；low 同理。分别建模再合成区间（或直接作为两组分位）。理由：跳空由隔夜信息驱动、日内幅度由波动率驱动，两者的可预测性与特征完全不同，混在一起会互相稀释。
2. **区间估计量特征**（全部从现有日线/1h 可得，零新抓取）：
   - Parkinson / Garman-Klass / Rogers-Satchell 日内波动率（5/10/20 日）——比 close-to-close 的 `vol_20d` 有效样本多 5 倍以上；
   - **1h 实现波动率 RV**（7 根 bar 的 log 收益平方和）的 HAR 结构：RV_1d / RV_5d / RV_22d；
   - vol-of-vol、`vol_5d/vol_20d` 比（regime 转换的最便宜代理）、前日振幅/ATR；
   - 1h 形态：尾盘一小时收益、下午振幅占比、收盘距日内高低距离（`close_pos_in_range` 已有，补「距高」「距低」两个方向）。
3. **日历/事件**：星期几（美股周一/周五振幅系统性不同）、**财报日 ±1 标记**（yfinance `Ticker.earnings_dates`，每季一次；ML 采集加一张 `ml_events` 表）、港股半日市标记（从 1h bar 数直接判）。
4. **跨标的上下文**（可选，需新增指数抓取）：`ml_quotes_1d` 追加 `SPY / QQQ / ^HSI / ^VIX`（yfinance 同口径），特征用指数的 ret_1d / vol_20d / VIX 水平与变化。理由：六支标的的振幅同步性很强，指数波动率是共同因子；NVDA/TSLA 相关 0.7 级别。**跨币种不归一、不共享现金池的铁律不受影响**——只是特征，不是账户。
5. **富途资金流向**（`capital_flow`，生产库）：只有 1 年（2025-07 起）、覆盖 ~250 行/股，**不够训练**；先不进模型，可作 Web 展示用的解释性特征，等积累到 2 年再评估。

改哪里：`features.py`（新增列，`FEATURE_COLS` 拆为 `FEATURE_COLS_V1`（冻结，保证旧口径可复现）与 `FEATURE_COLS_V2`），`data.py` 新增 `load_hourly_rv()`；`cv.PurgedConfig.feat_lookback` 随最长回看（22→60 若用 60 日）更新；`fetch.py` 若加指数与财报表则各自增量。

### A2 模型：正则化 + 早停 + 多种子 + 跨标的池化（P-B）

- **超参**：`num_leaves 15→7、min_child_samples 30→50、n_estimators 300→按早停`，早停用**时间序最后 15%** 做验证（不是随机）；`extra_trees=True` 或 `bagging` 5 种子平均——实验 A 已显示这个方向一致有效。
- **分位交叉与多分位联合**：一次训练多个 α（0.10/0.20/0.25/0.50/0.75/0.80/0.90），预测后按 α 排序（rearrangement）消除交叉；**多分位同时输出是 A4 触价概率曲线的原料**。
- **跨标的池化模型**（最大的单项样本增益）：六支合并训练一个模型，标签用 `y/vol_20d` 标准化，特征加 `market`（HK/US）与 `stock_id`（类别特征）。样本从 ~1,000 变 ~6,000。按股独立模型保留为对照；**验收看锁箱 skill 是否在 ≥4/6 标的上优于独立模型**。风险：NVDA/TSLA 强相关会主导——用 per-stock 权重均衡。
- **模型持久化与版本**：`data/ml/models/<date>/<code>.txt`（LightGBM 原生格式）+ `model_version` 字符串写进 `ml_predictions`（schema 加列，`ALTER TABLE ... ADD COLUMN` 幂等）。线上预测从此可复现、可归因。

### A3 校准：从静态 split-CQR 到自适应（P-B）

- **ACI / 滚动 conformal**：q 用**最近 N=60 个已结算交易日**的 non-conformity score 分位数计算，每天更新（`predict_next_day` 已经拿得到全部历史留档与真实高低——`review.py` 就是现成的 score 来源）；再叠加 Gibbs–Candès 的 ACI 步长修正 `α_t+1 = α_t + γ(α − err_t)`，让覆盖率在漂移时自动回到目标。
- **分侧校准**：上沿 q_hi、下沿 q_lo 分别算，修方向偏置（HK.00700 上破多 → q_hi 自动变大、q_lo 变小，总宽不必增加）。
- **两套目标覆盖率**：报告展示用 70%（回答「明天大概率在哪」）；**策略层不用 CQR 扩展后的区间**，直接消费原始分位或 A4 的触价曲线（回答「挂哪里成交概率多少」）。这一步就把 §3.2 的目标冲突拆开了。
- `calibrator.py` 增 `rolling_conformal_quantile(scores, window, target)` 与 `aci_step()` 纯函数；`ml_predictions` 新增 `l_raw / h_raw / q_lo / q_hi` 列，区分「模型」与「校准」。

### A4 直接建模「触价概率」与「触价后收益」（P-C，与策略 v2 联动的关键）

预测区间是给人看的；挂单要的是一条曲线：**买单挂在 `close×(1−d)`，明天被触到的概率 P_buy(d)，以及被触到之后的期望收益 E[ret | 触到]**。

- **P_buy(d) / P_sell(u)**：由 A2 的多分位输出直接反推（分位函数的逆），或用 LightGBM 二分类对离散档 `d ∈ {0.5%,1%,1.5%,2%,3%,4%}` 逐档建模（六个小模型，标签 = 次日 low ≤ close×(1−d)）。ECE 作门槛。
- **触价后收益**（逆向选择修正）：标签 = `close(T+1)/fill − 1`（同日）与 `close(T+5)/fill − 1`（5 日），只在触价样本上训练，输出按档的 E[ret|fill]。§3.3 显示这项在 TSLA/00700 显著为负，**必须进入策略的期望收益计算**，否则挂得越低成交越多、亏得越多。
- 输出物：`ml_touch_curve(code, as_of, side, offset_pct, p_touch, e_ret_1d, e_ret_5d)` 表，由 `ml.sh train` 写入，Web 只读。

### A5 明确不做 / 后置

- **TFT / Transformer / 深度时序**：样本量不支持（[`ML_ALGORITHM_PROPOSAL §8`](ML_ALGORITHM_PROPOSAL.md) 已否），本轮不碰。
- **HMM regime 软切换**：已被多时段检验证伪，不复活；A1 的 `vol_5d/vol_20d` 比与 VIX 是更便宜的 regime 代理。
- **RL（含 IQL/Cal-QL）**：不在预测层议题内，且 P4 负结果未被新数据推翻。

---

## 6. 升级方案 B：「ML 挂单回溯」联动与可用性

原则：**Web 只读 ML 库、不训练、不下单**；重计算尽量前移到 `ml.sh train` 落表，Web 端只做参数化聚合与展示。

### B1 策略 v2：多日库存式区间交易（P-D，核心）

替换 `strategy.run_strategy` 的「同日双边、无限库存」：

| 维度 | v1（现网） | v2 |
| --- | --- | --- |
| 回合 | 同日买 L̂ 卖 Ĥ | **买入后持有，逐日按当日 Ĥ（或触价曲线选的 u）挂卖直到成交**；空头对称（可关） |
| 库存 | 无上限 | `max_lots`（默认 3 手）；到顶不再加仓 |
| 挂价 | 固定 L̂/Ĥ | 模式 `interval`（沿用 L̂/Ĥ）/ `touch`（A4 曲线选 `argmax p·E[ret|fill]`）/ `naive_vol`（基线） |
| 费用 | 无 | 按市场费率表（`FEES_BY_MARKET`：HK 印花税 0.1% + 佣金/平台费；US 按股/按单），配置可改，默认给保守值 |
| 止损/超时 | 无 | 可选 `max_hold_days`（默认 20）到期按收盘平仓，避免库存无限期挂着 |
| 基线 | 无 | 同窗口内 `naive_vol` 带、买入持有、**你的真实挂单回放**（`ml_orders` 同一撮合器） |
| 输出 | 逐日 + 汇总 | 逐日 + **逐回合**（开仓日/平仓日/持有天数/毛利/费用）+ 汇总 + 多窗口稳健性 |

`compute_returns` 的四个口径保留（成交额/占款/现金/年化），v2 下「实际动用现金」终于有真实上界（`max_lots × 价格`），收益率不再随窗口翻转。

实现：`strategy.py` 保留 v1 为 `mode="same_day"`（旧口径可复现），新增 `mode="inventory"`；纯函数拆分 `simulate_inventory(preds, bars, daily, cfg)` 便于单测；`tests/test_ml_strategy.py` 加库存上限、费用、超时平仓、回合配对用例。

### B2 「明日挂单建议」卡（P-E，最直接的可用性提升）

Web ML Tab 顶部新增卡片（每支标的一张，按币种分组）：
- 基准日 / 次日、收盘价、**建议买价 / 卖价**（v2 模式下由触价曲线给出，默认仍显示 L̂/Ĥ）、触价概率、触价后期望收益、当前模拟库存、建议手数。
- 数据来源：`ml_predictions` 最新一行 + `ml_touch_curve`（新接口 `GET /api/ml/latest`）。**零计算、毫秒级**。
- 显式声明：「离线数据分析，非投资建议；系统不下单」。
- 若最新预测的 `as_of` 早于最近交易日 → 卡片显示「预测未更新（上次 YYYY-MM-DD）」，把 cron 失败暴露出来（现在只能去看 `cron.log`）。

### B3 逐日复盘搬进 Web（P-E）

- 新接口 `GET /api/ml/review?code=&days=`：复用 `review.review_predictions` + `summarize`（纯函数，已有测试）。
- 面板：命中率、上破/下破、平均戳出、**触价（可买成/可卖成）比例**——这两列在报告里没有，却是策略最关心的。
- 报告与 Web 共用同一套函数 → 两边数字永远一致。

### B4 K 线联动（P-E）

- 复盘 / 回溯表每行可点 → 打开个股浮窗（已有 `openStock`），在 Lightweight-Charts 上叠加当日 L̂/Ĥ 两条 price line 与成交标记（`createPriceLine` / `setMarkers`）。
- 只需前端改动：浮窗多接一个 `mlOverlay` 参数，从已加载的回溯数据取值；不新增后端接口。

### B5 与你的真实挂单对照（P-E）

- 同一交易日：你的限价（`orders`）vs 模型建议价 vs 真实高低；你成交了模型没成交 / 反之，各计数。
- 数据：生产库 `orders` + ML 库 `ml_predictions`。Web 已能读两个库，纯只读聚合；接口 `GET /api/ml/compare?code=&days=`。
- 这是把 ML 从「另一个页面」变成「对我操作的镜子」的一步，也是 `ML_PLAN §2.5`「人类行为可学」当年立项证据的闭环。

### B6 稳健展示（P-E）

- 窗口改为固定档 **20 / 60 / 120 / 全部**，一屏并列显示四档的扣费后成交额收益率与回合数，**符号不一致时红字提示「窗口敏感」**——把 Tier1 的教训固化进 UI。
- 汇总条加基线列：模型 v2 / 朴素带 / 买入持有 / 你的回放。

### B7 性能与工程（P-E）

- `ml.sh train` 新增步骤 `python -m mystock.ml.strategy --materialize`：把 v2 逐日/逐回合结果写 `ml_strategy_daily`（PK code+date+mode），Web 默认读表；「重新计算」按钮才走实时路径（保留调参能力）。
- 实时路径加进程内缓存：key = (codes, days, mode, ML 库 mtime)；1h 表按日期范围裁剪读取（现在整表读）。
- `ml.sh` 注释改成实际时区口径；报告页脚打印「预测生成时间（本机 PDT）/ 对应市场开盘前」。

---

## 7. 明确不做的事（避免 scope 蔓延）

| 不做 | 原因 |
| --- | --- |
| 自动下单 / 对接富途交易接口 | Web 只读边界；系统定位是分析工具，本文所有「建议」均为离线展示 |
| RL、TFT、HMM regime | 已有负结果或样本不支持，见 §5 A5 |
| 跨币种归一、共享现金池 | 项目铁律（各股本币闭环） |
| 分钟级撮合（15m） | yfinance 15m 只有 60 天，校准样本不足；1h 吻合率 88–93% 够用，先把策略定义改对 |
| 把 ML 结果写进生产库 | 反向依赖禁止；Web 读 ML 库已够 |

---

## 8. 实施顺序与里程碑

```
第一期（评估地基 + 预测层，约 2 周）
  W1  A0 尺子/锁箱/多种子 → 报告出 skill 列            [P-A 门槛]
  W1  A1 特征 v2 + 标签分解（先离线跑实验 A 的扩展版）
  W2  A2 正则/早停/多分位/池化 A/B，A3 滚动 conformal    [P-B 门槛]
      产物：ml_predictions 加 raw/q_lo/q_hi/model_version；模型落盘
第二期（触价模型 + 策略 v2，约 1.5 周）
  W3  A4 触价曲线 + 触价后收益 → ml_touch_curve            [P-C 门槛]
  W3-4 B1 strategy v2（inventory 模式、费用、上限、回合）+ 基线   [P-D 门槛]
第三期（Web 联动，约 1 周）
  W5  B2 明日建议卡、B3 复盘、B6 固定窗口 + 基线列
  W5  B4 K 线叠加、B5 真实挂单对照、B7 落表/缓存           [P-E 门槛]
```

每期结束更新 [`ML_OVERVIEW`](ML_OVERVIEW.md) 的「当前状态」与实验结论表；负结果照记。

---

## 9. 文件改动清单（预估）

| 文件 | 改动 | 期 |
| --- | --- | --- |
| `mystock/ml/baselines.py` | 新增：朴素波动率区间 / 朴素带 v2 | 一 |
| `mystock/ml/features.py` | V2 特征（Parkinson/GK、HAR-RV、1h 形态、日历/事件、指数）、标签分解；V1 冻结 | 一 |
| `mystock/ml/data.py` | `load_hourly_rv`、`load_index_daily`、`load_events` | 一 |
| `mystock/ml/predictor.py` | 多分位联合 + rearrangement、早停、多种子、池化模式、skill/覆盖偏差指标、模型持久化 | 一 |
| `mystock/ml/calibrator.py` | 滚动 conformal、ACI 步进、分侧 q | 一 |
| `mystock/ml/cv.py` | 锁箱切分、`feat_lookback` 参数化 | 一 |
| `mystock/ml/config.py` | `LOCKBOX_DAYS`、`FEATURE_SET`、`FEES_BY_MARKET`、`INDEX_TICKERS`、`ROLLING_CAL_WINDOW` | 一/二 |
| `mystock/ml/schema.sql` | `ml_predictions` 加列；新表 `ml_events`、`ml_touch_curve`、`ml_strategy_daily` | 一/二 |
| `mystock/ml/fetch.py` | 指数与财报日增量抓取；标的间节流 | 一 |
| `mystock/ml/touch.py` | 新增：触价概率 / 触价后收益模型 | 二 |
| `mystock/ml/strategy.py` | `mode` 参数、inventory 模拟、费用、回合、基线、`--materialize` | 二 |
| `mystock/ml/report.py` | 总览列改 skill/覆盖偏差；页脚时区口径 | 一 |
| `mystock/web/app.py` | `/api/ml/latest`、`/api/ml/review`、`/api/ml/compare`、`/api/ml/strategy` 新参数与缓存 | 三 |
| `mystock/web/static/app.js`、`index.html`、`style.css` | 建议卡、复盘面板、固定窗口、K 线叠加、对照表 | 三 |
| `scripts/ml.sh` | 注释时区；train 加 materialize 步骤 | 二/三 |
| `tests/test_ml_baselines.py`、`test_ml_touch.py`、`test_ml_strategy.py`（扩）、`test_ml_calibrator.py`（扩）、`test_ml_cv.py`（扩） | 纯函数单测 | 各期 |
| `docs/ML_OVERVIEW.md`、`README.md`、`CLAUDE.md` | 同步口径（skill 指标、策略 v2、接口表） | 各期 |

---

## 10. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 加特征后仍打不过朴素基线 | 这本身就是有价值的结论：预测层退化为「最好的波动率估计 + 自适应校准」，把精力全部转到 A4/B1；不硬凑 |
| 池化模型被 NVDA/TSLA 主导 | per-stock 样本权重均衡；独立模型保留为对照，按锁箱择优 |
| 触价后收益模型样本少（每股几十次触价） | 先按市场池化（HK/US 各一个），档位粗（4 档）；ECE 不达标则策略 v2 只用 P_touch，不用 E[ret] |
| 费用参数不准 | 费率表可配置且在页面显式展示；默认取保守上限 |
| 窗口敏感依旧 | B6 四窗口并列 + 符号一致性提示，让敏感性可见而不是藏起来 |
| 指数/财报抓取触发 yfinance 限频 | 标的间节流 + 指数只抓日线；失败不阻塞主流程（沿用 `run_step` 不中断语义） |

---

## 附录 A：本次实验的复现要点

- 实验 A（模型 vs 朴素基线）：对每支标的 `build_features(load_daily)` → 追加 8 个特征 → `purged_walk_forward(n_folds=4, min_train=250)` → 每折分别拟合 `naive_vol`（`np.quantile(y/vol_20d, α) × vol_20d`）、`lgb_base`（现网参数）、`lgb_extra`、`lgb_extra_x`（600 树 / lr 0.02 / leaves 7 / min_child 50），记录 pinball（L/H 均值）、命中率、宽度。全程 CQR 关，α 按 `config.alpha_for`。
- 实验 B（挂单经济性）：遍历 `ml_predictions`（全部 source），次日 1h bars 走 `simulator.match_limit_order`；买成后记录当日收盘 / 5 日后收盘相对成交价；多日轮回 = 买成后逐日用**当日留档 Ĥ** 挂卖，最多 20 个交易日。
- 触价统计：SQL 直接对 `ml_predictions` × `ml_quotes_1d`（次日 low/high）做 `low ≤ L̂`、`high ≥ Ĥ`、二者同真。
- 人类挂单形态：`ml_orders` 限价相对前一交易日收盘的偏移、按 `order_status ∈ {FILLED_ALL, CANCELLED_PART} ∧ dealt_qty > 0` 判成交。

## 附录 B：现网 walk-forward 快照（2026-09-04，`python -m mystock.ml.predictor`）

| 标的 | 命中 raw→CQR | 宽度 raw→CQR (%) | width_IC | mid_IC | pinball H/L |
| --- | --- | --- | --- | --- | --- |
| US.NVDA | 0.499→0.763 | 6.21→8.81 | 0.312 | 0.055 | 0.00705/0.00757 |
| US.TSLA | 0.505→0.748 | 7.32→10.32 | 0.273 | 0.017 | 0.00904/0.00877 |
| US.PDD | 0.603→0.813 | 7.39→10.78 | 0.143 | 0.049 | 0.01115/0.01184 |
| HK.00700 | 0.548→0.730 | 5.03→6.38 | 0.247 | 0.025 | 0.00558/0.00539 |
| HK.09988 | 0.543→0.734 | 6.37→8.64 | 0.308 | 0.037 | 0.00816/0.00788 |
| HK.01810 | 0.476→0.731 | 5.86→8.15 | 0.226 | −0.022 | 0.00697/0.00676 |
