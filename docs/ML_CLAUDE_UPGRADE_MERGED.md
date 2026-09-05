# myStock ML 升级 —— 合并方案（Claude v0.1 × Codex v1.2 → 讨论稿 v0.1）

> 作者：Claude · 日期：2026-09-04 · 状态：**合并建议，待与 Codex 共同研究后再定执行方案**
>
> 输入：[`ML_UPGRADE_PLAN.md`](ML_UPGRADE_PLAN.md)（Claude v0.1）与 [`ML_CODEX_UPGRADE_PLAN_2026-09-04.md`](ML_CODEX_UPGRADE_PLAN_2026-09-04.md)（Codex v1.2）。
> 本文不重复两份原文的论证，只做三件事：① 列出双方一致且已核实的事实；② 给出分批合并顺序与各批范围；③ 列出需要三方（用户 / Codex / Claude）拍板的分歧点。
> 本文只改文档，未改代码；所有「已核实」均为 2026-09-04 对本地库与源码的只读检查。

---

## 0. 一句话

两份方案方向一致：**覆盖率与成交是相反目标、先立朴素波动率基线、策略改为多日库存式、Web 串起「明日建议 → 复盘 → 真实挂单对照」、不做 RL/TFT/HMM、Web 只读不下单。**
差异在侧重：Claude 提供了量化证据（模型 ≈ 朴素基线、触价经济性），Codex 提供了工程可信度（版本化、只读保证、交易日历、事件账本）。合并原则：**先修可用性硬伤 → 再立评估地基 → 再做预测层实验 → 最后策略 v2 与页面**；Codex §8 的大规模建表与 §14 外部特征暂缓，等预测层拿出增量证据再立项。

---

## 1. 双方一致、且已核实的事实（作为后续讨论的共同底座）

### 1.1 数据与预测层

| 事实 | 数字 | 来源 |
| --- | --- | --- |
| LightGBM 分位模型 ≈ 波动率缩放的朴素经验分位 | pinball 差异 ±3% 内，6 支中 4 支朴素基线更小 | Claude 实验 A（Codex 采纳为必须战胜的基准，未独立复现） |
| CQR 70% 覆盖把区间撑到真实振幅的 1.9–2.3 倍 | 区间宽 5.8–8.8% vs 真实日振幅 2.5–4.6% | 双方各自统计一致 |
| 留档预测下买卖双边同日成交为零 | 六支 × 47–49 个已结算日，全部为 0 | 双方各自统计一致 |
| 线上覆盖率离散、walk-forward 超覆盖 | 线上 51–79%，walk-forward 73–81%（目标 70%） | Claude；Codex 提醒来源协议不同，不能全归因于静态 q |
| 多日轮回可完成且毛利为正 | 39/43 完成，均 4–7 天，毛利 +0.3%～+5.1% | Claude 实验 B（Codex 采纳为候选，不采纳「已成立」定性） |
| 单边成交存在逆向选择 | TSLA 买成后当日收盘 −3.0%，00700 −1.1% | Claude 实验 B |

### 1.2 代码与数据硬伤（Codex 提出，Claude 逐条核实为真）

| 编号 | 事实 | 核实结果 |
| --- | --- | --- |
| F05 | Web 回溯路径拿到可写 ML 连接 | `strategy.run_many` / `run_strategy` 走 `db.get_ml_connection`（可写）；仅 `data._conn` 为 `mode=ro` |
| F03 | `days=30` 实际是「30 个可用样本」，缺口被静默吞掉 | 留档缺口：US 08-18/19/20/21/26，HK 08-19/20/21/24（2026 年）；日线均在 |
| F15 | `backfill.recompute_gaps` 收到 `db_path` 但 `load_daily(code)` 未透传 | 属实 |
| F10 | `fetch._ohlc_ok` 不拒绝 inf、不检查 high ≥ low | 属实 |
| 数据 | 日线与 1h 聚合高低差异 >1% 的历史天数 | 0700.HK 92 / 1810.HK 51 / 9988.HK 119（共 781 天）；美股 7–10 天；2026-06-22 之后全部为 0 |
| F08 | `subsample=0.8` 但 `subsample_freq` 默认 0，bagging 未生效 | 属实（LightGBM 语义） |
| F09 | pinball 在 CQR 扩展后的边界上用原 α 计算 | 属实，raw 与校准指标混算 |
| F14 | bandit 的 step_pnl 只算当日交易边际，却减去完整 buy&hold 日变化 | 属实 |
| lot | 港股 board lot 非统一 100 股 | **HK.01810 真实成交全部为 200 股整数倍，现持仓 3,800 股**；现网 `LOT_BY_MARKET["HK"]=100` 对小米错误，Claude v0.1 亦沿用此错误假设 |

---

## 2. 合并后的分批顺序

> 每批可独立审查、独立交付；后一批不依赖前一批的实验结论，只依赖其代码。工期为单人粗估，不含前向 shadow 等待。

### 第一批：可用性硬伤修复（约 3 天，以 Codex 阶段 A 为主）

| 项 | 范围 | 验收 |
| --- | --- | --- |
| 只读保证 | `strategy` / `backfill` / Web 全链路使用 `mode=ro` 连接；`db.py` 增 `get_ml_connection_readonly()` | 在 Web 路径上执行写 SQL 必失败（加测试） |
| 交易日历窗口 | `days` 按标的自身交易日取最近 N 个 session；缺预测 / 缺 1h / 缺日线的日子保留为状态行（`missing_prediction / missing_bars / missing_daily`） | 缺一天页面显示一天缺口，不再静默截窗 |
| 参数校验 | 无效代码返回 400 并列出原因；重复代码去重保序；不再静默回退默认股票 | 加 API 契约测试 |
| 交易单位 | `LOT_BY_MARKET` 改为按标的 `LOT_BY_CODE`（01810=200，其余 100 / US 10），来源先用成交记录推断并注明 | 01810 手数 200 |
| 采集质量 | `_ohlc_ok` 拒绝 inf 与 high<low；`recompute_gaps` 透传 `db_path` | 加单测 |
| 文档 | `ml.sh` 注释改为实际时区（PDT 02:40，对应美股前一交易日收盘后 / 港股当日收盘后） | — |

### 第二批：评估地基（约 3 天，= Codex 阶段 C 核心 + Claude A0）

| 项 | 范围 | 验收 |
| --- | --- | --- |
| 朴素基线固化 | 新增 `ml/baselines.py`：`naive_vol_interval`（训练集 `quantile(y/vol_20d, α) × 测试日 vol_20d`，含尺度下限） | 复现实验 A 数字（±0.0001） |
| skill 指标 | `walk_forward_eval` 输出 `pinball_skill_low/high = 1 − model/naive`、raw 与校准后分开评分、滚动覆盖偏差 | 报告总览增 skill / 覆盖偏差列 |
| 切分修正 | CV 最后一折覆盖尾部余数；`subsample_freq` 显式设置；保留旧协议结果作 A/B | `test_ml_cv` 扩 |
| 多种子 | 5 种子（0–4）mean/std；小样本或接近零 skill 时报告绝对差与分块区间，不用 std/mean 作阈值 | — |
| 留档最小版本化 | `ml_predictions` 增 4 列：`model_version / l_raw / h_raw / published_at`（`ALTER TABLE` 幂等）；**不建 Codex §8 的七张表** | 重跑不丢 raw 值；旧行为 NULL 并标 unknown |
| holdout 口径 | 最近 120 个 session 作为工程 holdout（`ALPHA_BY_CODE` 于 2026-07-01 前调档，之后数据未参与调参）；正式上线另需 ≥60 session 前向 shadow | 两个口径并列写进报告 |

### 第三批：预测层实验（约 1.5 周，Codex E1→E5 顺序 + Claude A1/A2 候选）

每轮只改一个机制；共同样本掩码、相同测试日期、相同基线。

| 序 | 实验 | 候选内容 |
| --- | --- | --- |
| E1 | 训练窗与校准窗 | 全历史 vs 最近 504/756 session；校准集 25% 尾部 vs 固定 60/120 session |
| E2 | 目标标准化 | 原生 y vs `y / vol_20d`（或 ATR）标准化后还原 |
| E3 | 正则化 | `num_leaves 15→7`、`min_child_samples 30→50`、显式 bagging、时间序早停 |
| E5 | 波动率特征 | 日线组：Parkinson / Garman-Klass / Rogers-Satchell、vol_5/vol_20、vol-of-vol；1h 组：已完成 bar 收益平方和的 1/5/22 session 聚合、尾盘收益 |
| E4 | 自适应校准 | 固定 q vs 最近 60 session OOF 残差滚动 q；其后才试 ACI；分侧 q 须先定误覆盖预算（`α_low + α_high = 0.30`） |
| E6 | 多分位与池化 | 多 α 网格 + 单调重排；6 支池化（标签标准化 + `market` / `stock_id`）vs 独立模型 |
| E7/E8（可选） | 跳空分解、触价概率 | E7 先做误差归因，联合残差再校准；E8 触价概率用 Brier / 可靠性图验收，触价后收益先诊断后建模 |

验收门槛（沿用 Codex §6.4 与 Claude §4.2 的交集）：holdout 上 raw pinball 相对冻结 champion 改善 ≥5%、相对 naive_vol skill ≥ +3%、≥4/6 标的方向改善、无单股恶化 >10%；覆盖偏差 ±5 pt 内。**若无候选通过，保留 E0 并记录负结果，不阻塞第四批。**

### 第四批：策略 v2 与 Web 联动（约 1.5 周，Codex §7.3–7.5 引擎规则 + Claude B1–B6 页面）

| 项 | 范围 |
| --- | --- |
| 账户事件引擎 | 新增 `ml/execution.py`：订单含生效/失效时刻、资源预留、禁裸空、按事件记账、费用 profile 按市场、同 bar 双触达标注保守假设、超时退出为预先冻结参数、缺行情为数据状态而非「未成交」 |
| 策略 v2 | `mode="inventory"`（多日库存状态机：空仓挂买 → 有仓按次日 Ĥ 挂卖 → 上限禁加仓 → 超时退出 → 窗口末盯市）；`mode="same_day"` 保留为 legacy；报价规则 `boundary / naive_vol / fixed_offset` 三选一，`touch` 待 E8 通过 |
| 基线 | 同窗口内朴素带、买入持有、空仓、真实挂单回放（仅事实对照，生命周期不足时不做假设回放） |
| 多窗口 | 固定 20/60/120 session 并列，各显示收益、回撤、暴露、回合数、缺口；区分「从窗口初始状态重模拟」与「连续模拟的绩效切片」 |
| 明日建议卡 | 基准日 / 目标 session / 风险区间 / 固定 policy 的模拟报价 / 来源 / 更新时间；过期、未发布、数据不完整分别显示 expired / pending / unavailable |
| 复盘面板 | 复用 `review.py`，Web 与报告同一函数；增「可买成 / 可卖成」两列 |
| K 线联动 | 浮窗叠加选中日 L̂/Ĥ 与成交点；先核对生产库 `daily_quotes` 与 ML 库价格版本一致，不一致则由 ML 只读接口返回行情再叠加 |
| 接口 | 保留 `GET /api/ml/strategy` 兼容；新增 `latest / review / compare` 三个只读接口（不一次建齐 Codex §8.2 的六个） |
| 性能 | 进程内缓存，key 含全部场景参数 + 预测集合版本（不依赖文件 mtime）；1h 表按日期范围裁剪读取；先看 EXPLAIN 再加索引 |

---

## 3. 暂缓项（等第三批出结果再立项）

| 项 | 来源 | 暂缓理由 |
| --- | --- | --- |
| `ml_model_runs / ml_prediction_versions / ml_data_snapshots / ml_session_quality / ml_instrument_rules / ml_replay_runs / ml_touch_predictions` 七张表 | Codex §8.1 | 单用户本地系统，先用 4 列最小版本化；证据出来前不建 MLOps 骨架 |
| v2 接口全套（status / predictions / replay / evaluation） | Codex §8.2 | 第四批只做 latest / review / compare |
| Futu IV、卖空、回购、期权统计、财报日历 | Codex §14.2 | 多数本地 SDK 无方法或无历史快照；先证明预测层有增量 |
| 指数 / VIX 上下文、财报日标记 | Claude A1.3–A1.4、Codex §14.3 | 需新增抓取与 point-in-time 管理；E5 通过后作为 F1/F3 单独消融 |
| 富途 `capital_flow` 作特征 | 双方 | 仅 1 年、~280 行/股；先作 Web 解释性展示，积累到 2 年再评估 |
| 15m 撮合 | Claude §7 | 1h 吻合率 88–93% 够用，先把策略定义改对 |

---

## 4. 需要三方拍板的分歧点

| # | 分歧 | Claude 立场 | Codex 立场 | 建议 |
| --- | --- | --- | --- | --- |
| 1 | 版本化的粒度 | `ml_predictions` 加 4 列即可 | 七张表 + 不可覆盖的 `prediction_id` | 第二批用 4 列；若第三批有候选胜出且要进入 shadow，再补 `ml_prediction_versions` 一张表 |
| 2 | 最近 120 日能否作 holdout | 可作工程 holdout（调档在 07-01 前） | 「历史团队已看过」，不算锁箱 | 两个口径并列：holdout 用于开发期验收，≥60 session 前向 shadow 用于上线验收 |
| 3 | 实验 A 数字的地位 | 已跑通、15 秒可复现 | 未独立复现，仅作参考 | 第二批第一项就是固化并复现，争议自然消失 |
| 4 | 跳空分解（E7）的定位 | 分别建模 + 联合残差校准，一步做 | 先只做误差归因 | 先归因；归因显示跳空占误差 >40% 才进入建模 |
| 5 | 触价后收益模型 | 进入策略期望收益计算 | 先诊断、样本够再立项，策略不能只算 `argmax p×E[ret]` | 第四批策略 v2 用固定规则报价；E8 通过后再加 `touch` 模式 |
| 6 | 工期 | 约 4 周 | 16–24 人日 + shadow | 按本文四批约 4.5 周；第三批无胜者时第四批照常交付 |
| 7 | 预测生成时点 | 沿用现网 T 收盘后（PDT 02:40 批） | 「T 收盘后」为待确认默认值，盘前更新须另立版本 | 维持 T 收盘后；盘前版本不在本轮范围 |

---

## 5. 明确不做（双方一致）

- 自动下单 / 对接富途交易接口；Web 只读、不训练、不写 ML 库。
- RL（含 IQL / Cal-QL）、TFT / Transformer、HMM regime、风险调整 reward（已被 [`ML_TIER1_ROBUSTNESS`](ML_TIER1_ROBUSTNESS.md) 证伪）。
- 跨币种归一、共享现金池、ML 结果写进生产库。
- 扩大选股池。

---

## 6. 下一步

用户与 Codex 研究本文后，三方共同把第一批、第二批展开成可执行的任务清单（文件级改动 + 测试用例 + 验收命令），再开工。第三批、第四批的细节等第二批的 skill 数字出来后再定。
