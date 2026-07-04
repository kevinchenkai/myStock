# ML 新算法建议清单（讨论稿 v0.1）

> 状态：本文件为**新算法建议清单**，承接 [`ML_OVERVIEW.md`](ML_OVERVIEW.md) 的"下一步优先级 = regime 感知 > 扩样本 > RL"与 [`ML_PLAN.md`](ML_PLAN.md) 的"能停则停、打不过基线如实记录"纪律。**未拍板、未开工**，供讨论与排期。
>
> 关联：数据口径见 [`DATA.md`](DATA.md)，盈亏口径见 [`mystock/pnl.py`](../mystock/pnl.py)，现有 ML 代码见 [`mystock/ml/`](../mystock/ml/)。
>
> 调研基准：2026-07-04，已对照 2025–2026 当前最佳实践（Conformal Quantile Regression、HMM regime、IQL/Cal-QL、Bootstrap Thompson Sampling、Purged K-Fold）。
>
> 一句话裁决：**第一档三件套（风险调整 reward + CQR 校准 + HMM regime 软切换）最该先做；RL 死刑略早，给 IQL/Cal-QL 一次机会；TFT / 端到端在线 RL 在当前样本量下明确不推荐。**

---

## 0. TL;DR

- **第一档（高价值低风险，建议立即做）**：
  1. 风险调整 reward（Sharpe 化 / 回撤惩罚）—— 延续 P3.1"奖励对齐 > 模型复杂度"的成功经验，对症 PDD 震荡市过度交易。**1-2 天**。
  2. Conformalized Quantile Regression (CQR) —— 替换 `config.ALPHA_BY_CODE` 的手调分位，给区间覆盖率**有限样本保证**。**1-2 天**。
  3. HMM regime 软切换 —— 项目自己点名的 #1 优先级，直接攻 P3.1"regime 依赖"根因。**3-5 天**。
- **第二档（中价值，第一档验证后做）**：Bootstrap Ensemble + Thompson Sampling（攻 LinUCB 线性假设）、IQL/Cal-QL（直接修 P4 CQL"过保守→不动"根因）、离线策略评估 OPE（评估闸门）。
- **第三档（研究性）**：多步多目标预测、跨标的多任务。TFT 在当前样本量下**不推荐**。
- **基础设施（非算法但放大算法价值）**：多种子方差报告 + Purged/Embargo CV、撮合保真度 1h → 15m。
- **明确不推荐**：端到端在线 RL（PPO/SAC）、纯神经 bandit、跨币种归一、硬切换 regime。

---

## 1. 现状摘要（建议的出发点）

| 层 | 现用算法 | 文件 | 已验证结论 |
| --- | --- | --- | --- |
| 预测 P2 | 分位数回归（LightGBM 优先 / sklearn 回退），按股自适应 α | [`predictor.py`](../mystock/ml/predictor.py) | 区间命中 ~50%（收窄后），宽度↓25-30%，walk-forward 无泄漏 |
| 决策 P3 | LinUCB contextual bandit（13 臂，ε-探索，超额奖励） | [`policy.py`](../mystock/ml/policy.py) | regime 依赖：NVDA 超基线，TSLA 改善仍输，PDD 退化 |
| RL P4 | Discrete CQL（离线） | [`offline_rl.py`](../mystock/ml/offline_rl.py) | **负结果**：三支全退化为"不动"，`conservative_loss(1.93) > td_loss(1.17)` |
| 撮合 P1 | 1h bar 限价撮合模拟器 | [`simulator.py`](../mystock/ml/simulator.py) | 真实 orders 回放吻合 88-90% |

**项目自己给出的下一步优先级**（[`ML_OVERVIEW.md`](ML_OVERVIEW.md) 实验总结）：**regime 感知 > 扩样本 > RL**。

**关键洞见**：P3→P3.1 仅把奖励改成"相对 buy&hold 超额"，NVDA 就从输变赢 —— **奖励/目标对齐比换更复杂模型更管用**。这条经验贯穿本文档所有建议。

---

## 2. 硬约束（决定哪些算法值得加、哪些不值得）

1. **样本极小**：真实成交每标的仅 40-110 笔；可决策日线样本 ~1234/标的、测试 ~494 日。任何"数据 hungry"的深度模型默认不推荐。
2. **单一 regime**：2024-2026 一段，NVDA/TSLA 高相关、PDD 较独立。跨 regime 不可验证。
3. **工程边界不可破**：独立 ML 库 `data/ml/mystock_ml.db`、独立采集 `scripts/ml.sh`、不碰 web/生产库、H20 独立 conda env `mystock-ml`。
4. **撮合有残余失真**：1h bar 内顺序未知，吻合率 88-90% 是天花板。
5. **可解释性偏好**：项目原则"只做客观行为建模，不输出投资建议"，可解释方法优先。
6. **算力富余**：H20 双卡 96GB，模型小 → 算力非瓶颈，可用于并行铺种子/超参/标的。
7. **永远保留 S0 + 三基线对照**：新增策略**并存**而非替换，打不过就停在上一阶段交付。

---

## 3. 第一档：高价值低风险，建议立即做

### 3.1 建议 1 — 风险调整 reward（延续 P3.1 成功经验）

**是什么**：当前 reward = `相对 buy&hold 超额 × reward_scale`（[`backtest.py::BTConfig`](../mystock/ml/backtest.py)）。建议加两个可选目标 A/B 对照：
- (a) **Sharpe 化回合 reward**：用滚动 20 日 reward 的 mean/std 归一，惩罚高方差路径；
- (b) **回撤惩罚 reward**：reward 减去 `max(0, drawdown_t - dd_threshold)` 项，直接对齐"不要大幅回撤"。

**为什么贴合本项目**：
- P3.1 最大教训 = **奖励对齐 > 模型复杂度**。当前 reward 只看超额收益、不控路径风险 → PDD 在震荡市过度交易磨损。直接对症。
- 不引入新模型，只改 reward 函数，**改动面最小、可解释性最强**。

**集成点**：`backtest.py::BTConfig` 加 `reward_mode ∈ {excess, sharpe, drawdown_penalized}`；reward 计算那段 if/else；报告里并列三栏对比。

**工作量**：1-2 天。**风险**：低。**预期收益**：高概率改善 PDD（震荡市过度交易是它的主要病）。**性价比最高的单点改动。**

**验收门槛**：walk-forward 测试窗内，`sharpe` 或 `drawdown_penalized` 模式须在至少 2/6 标的上提升 `final_equity` 且不恶化其余，否则退回 `excess` 并如实记录。

---

### 3.2 建议 2 — 预测层：Conformalized Quantile Regression (CQR)

**是什么**：在现有 LightGBM 分位数回归外面套一层 split conformal 校准——用一段 held-out 校准集算 non-conformity score `max(y_low - L_hat, H_hat - y_high)`，取其 (1-α) 分位数 `q` 作为半宽，最终区间 `[L_hat - q, H_hat + q]`。可用 [MAPIE](https://mapie.readthedocs.io/) 或 [quantile-guard](https://pypi.org/project/quantile-guard/)，或纯函数自实现（<50 行）。

**为什么贴合本项目**：
- 当前 [`config.ALPHA_BY_CODE`](../mystock/ml/config.py) 是**手调**的逐股分位（NVDA 0.20/0.80、PDD 0.25/0.75…），靠 walk-forward 实测"宽度 vs 命中率"甜区定档。CQR 把这个手调过程**自动化 + 给出有限样本覆盖率保证**（标准 QR 只有渐近保证）。
- 项目已严格按时间 walk-forward 切分，天然适合时序版 conformal（Adaptive Conformal Inference，随时间更新 `q`）。
- **不改预测层结构**，只加一层后处理，`IntervalModel` 接口不变，回测/报告全链路零改动。

**集成点**：[`predictor.py::IntervalModel.predict_ret`](../mystock/ml/predictor.py) 返回前加一层 conformal 校准；新增 `calibrator.py`（纯函数可单测，符合项目"新逻辑优先写成纯函数"约定）；`config.py` 的 `ALPHA_BY_CODE` 退化为"目标覆盖率"而非"分位档"。

**工作量**：1-2 天。**风险**：极低（失败只是退回原 QR）。**预期收益**：消除手调 α、覆盖率有保证、报告"命中率"列从启发式变成有保证的量。

**验收门槛**：测试窗实测覆盖率 ≥ 目标覆盖率（如目标 80% 则实测 ≥ 80%），且区间宽度不显著大于现 `excess` 模式宽度（≤ +15%）。不达标则保留原 QR 并记录校准诊断。

**证据**：[MAPIE CQR tutorial](https://mapie.readthedocs.io/en/v1.0.1/examples_regression/2-advanced-analysis/plot_cqr_tutorial.html)、[quantile-guard](https://pypi.org/project/quantile-guard/)、[Conformal prediction for prob. ML (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11246475/)。

---

### 3.3 建议 3 — 决策层：HMM regime 软切换（项目点名的 #1 优先级）

**是什么**：用 2-3 状态 Gaussian HMM（`hmmlearn`）在每股的 (log return, 20d vol, MA50-MA200 spread) 上**离线**学 regime（涨势 / 震荡 / 下行），然后用 regime 后验概率**软切换**策略池，而非硬 if/else。

**为什么贴合本项目**：
- 这是 [`ML_OVERVIEW.md`](ML_OVERVIEW.md) 实验总结**明确点名**的下一步："没有单一策略通吃……下一步真正值得做的是 regime 感知，而非堆 RL"。
- 直接解释 P3.1 的矛盾结果：NVDA/TSLA 单边涨 → bandit 该让位 buy&hold；PDD 震荡下行 → bandit 该择时。**同一个 bandit 在不同 regime 下最优动作不同**，但当前 LinUCB 把它们混在一个模型里学。
- HMM 是**小样本友好的生成式模型**（比 change-point 检测、Transformer regime 检测都轻），2-3 状态 + 3 特征 = 参数极少，1234 样本足够。
- 软切换（按 regime 后验概率加权多个 LinUCB 的 θ）比硬切换稳，符合项目"敬畏小样本、看方差"的纪律。

**集成点**：新增 `regime.py`（HMM fit + `regime_prob(row)` 纯函数）；[`policy.py::LinUCB`](../mystock/ml/policy.py) 改为"每 regime 一组 A/b"，`select(x, valid, regime_probs)` 按 `regime_probs` 加权合成 θ；[`backtest.py`](../mystock/ml/backtest.py) 在 `_state_vec` 旁加 regime 后验。**S0 规则也 regime 化**：涨势偏 buy&hold（少动）、震荡偏 bandit、下行偏保守。

**防泄漏要点**：HMM 只能用 ≤ t 的数据 fit（滚动重训或 walk-forward），绝不能用全量 fit 后再回溯标注 —— 否则 regime 标签泄漏。

**工作量**：3-5 天（含 walk-forward 验证）。**风险**：中（regime 数选择、状态解释需人工核对）。**预期收益**：直接攻 P3.1"regime 依赖"根因，最有可能让 bandit 在 TSLA/PDD 上稳定超基线。

**验收门槛**：6 标的上 regime 软切换 bandit 须在 ≥ 4 标的 `final_equity` ≥ 单 regime bandit，且整体方差（多种子 std）不上升。否则退回单 regime 并记录 regime 解释图。

**证据**：[LSEG market regime detection](https://developers.lseg.com/en/article-catalog/article/market-regime-detection)、[RegimeSense soft allocation](https://github.com/moh1tt/RegimeSense)、[HMM + macro features](https://pyquantlab.medium.com/regime-aware-trading-with-hidden-markov-models-hmms-and-macro-features-c75f6d357880)。

---

## 4. 第二档：中等价值，第一档验证后做

### 4.1 建议 4 — 决策层：Bootstrap Ensemble + Thompson Sampling（攻 LinUCB 线性假设）

**是什么**：用 10 个 bootstrap 子样本训练 10 个轻量 reward 模型（LightGBM 或小 MLP），用它们的**分歧**当不确定性，Thompson Sampling 选臂（随机抽一个模型当 posterior sample）。

**为什么贴合本项目**：
- 当前 LinUCB 假设 reward 是特征的**线性**函数，但 16 维特征里有 ATR、量比、均线偏离等强非线性量。检索证据明确："对非线性特征，线性 LinUCB/TS 都会 underperform"。
- **纯神经 bandit 数据不够**（需 10k+ 决策/天）。Bootstrap ensemble 是小样本下的 Bayesian 近似，正好夹在中间。
- LinUCB 在金融场景"34% 更低 reward 方差、可解释"优势项目已享受过；切换需谨慎，**保留 LinUCB 作为对照基线**（项目惯例）。

**集成点**：[`policy.py`](../mystock/ml/policy.py) 新增 `BootstrapTS` 类与 `LinUCB` 并列；[`backtest.py`](../mystock/ml/backtest.py) 加一个 `"bandit_ts"` 账户。**不替换 LinUCB，并存对比。**

**工作量**：2-3 天。**风险**：中（10 模型训练成本×10，模型小，H20 可忽略；本机 CPU 也可接受）。**预期收益**：中等——只在非线性结构真重要时见效，需 walk-forward 实测。

**验收门槛**：测试窗 `bandit_ts.final_equity` 须在 ≥ 4/6 标的上 ≥ `bandit`（LinUCB），否则保留 LinUCB 不上线。

**证据**：[Neural Contextual Bandits guide](https://www.vitorsousa.com/blog/contextual-bandits-neural/)、[UCB vs Thompson Sampling in prod](https://mcpanalytics.ai/whitepapers/contextual-bandits-whitepaper.html)（后者明确：金融场景 LinUCB 风险调整后更稳，TS 高流量场景才赢——本项目决策样本仅 ~494 测试日，**不是高流量场景**，故用 Bootstrap 近似 TS 而非纯 TS）。

---

### 4.2 建议 5 — RL 替代：CQL → IQL 或 Cal-QL（直接修 P4 失败根因）

**是什么**：P4 的 CQL 退化为"不动"，根因是 `conservative_loss(1.93) > td_loss(1.17)`——保守惩罚对**弱奖励信号**过强。两个直接对症的替代：
- **IQL（Implicit Q-Learning）**：不查询 OOD 动作的 Q，用 expectile regression 在数据支持内学 V，再抽 policy。**d3rlpy 已内置**（`DiscreteIQLConfig`），改一行导入即可。
- **Cal-QL**：在 CQL 基础上加"校准到参考策略价值下界"，防止 Q 值塌缩到零。正是 P4"Q 全为 0、策略不动"的对症药。

**为什么贴合本项目**：
- P4 负结果**不是"RL 不行"，是"CQL 在此奖励规模下过保守"**。[`ML_PLAN.md §6`](ML_PLAN.md) 自己也说"优先离线 RL"，但选了 CQL。IQL 在小数据 + 弱奖励场景实证优于 CQL（D4RL benchmark IQL 47 vs CQL 44 vs 朴素 35）。
- **零新增依赖**（d3rlpy 既有）、零口径改动（同样 13 臂、同模拟器、同超额奖励）。
- 项目原则"能停则停、跑不赢如实记录"——但 P4 还没试 IQL 就判 RL 死刑略早。给 IQL 一次机会是诚实的。

**集成点**：[`offline_rl.py::train_cql`](../mystock/ml/offline_rl.py) 加 `algo ∈ {cql, iql, cal_cql}` 分支；报告把 P4 三种算法并列。

**工作量**：1 天（主要跑实验+写报告，代码改动极小）。**风险**：低。**预期收益**：中等——最坏又一个诚实负结果（仍写进报告），最好情况翻盘一两个标的。

**验收门槛**：IQL/Cal-QL 须在 ≥ 1 标的上 `final_equity > buy_hold` 且不退化为"全程不动"（即 `net_value ≠ 0` 或交易笔数 > 0）。否则确认 RL 在此数据量下无效，保留为最终负结果。

**证据**：[Strategic CQL paper (arXiv 2406.04534)](https://arxiv.org/pdf/2406.04534)、[CQL/IQL intuition](https://embodiedbook.apartsin.com/part-5-learning-from-demonstration-and-robot-data/module-25-offline-rl-and-dataset-based-robot-learning/section-25.3.html)、[Offline RL implementation notes](https://apxml.com/courses/advanced-reinforcement-learning/chapter-7-offline-reinforcement-learning/offline-rl-implementation-notes)。

---

### 4.3 建议 6 — 离线策略评估 (OPE)：上线前独立一道闸

**是什么**：新增 `ope.py`，用 **Importance Sampling (IPS) / Fitted Q Evaluation (FQE)** 在离线数据上估策略价值，**不进模拟器**就能粗估新策略上下界。d3rlpy 自带 FQE。

**为什么贴合本项目**：
- 当前评新策略只能跑 494 天回测，样本小、方差大、可能过拟合测试集。OPE 给"上线前"独立一道闸。
- 项目"打不过基线就停"纪律需要**可靠评估**支撑，否则容易选中过拟合测试集的运气曲线。

**工作量**：2 天。**风险**：低。**预期收益**：提升所有决策层实验可信度，间接提升 go/no-go 判断质量。

**验收门槛**：OPE 估值与模拟器回测净值的相关性 ≥ 0.6（多个策略跨标的），否则 OPE 自身不可信、不上线当闸。

---

## 5. 第三档：研究性，第二档之后再考虑

### 5.1 建议 7 — 预测层：多步多目标预测（TFT 暂不推荐）

**是什么**：
- **直接多目标 LightGBM**（推荐）：把 `y_high_ret / y_low_ret` 扩成 `{high, low, close, range} × {T+1, T+2, T+3}`，一次多输出训练，给决策层"未来 3 天区间路径"而非只 T+1。
- **TFT（Temporal Fusion Transformer）**（暂不推荐）：多视野注意力时序模型。

**为什么 TFT 暂不推荐**：检索证据一致——TFT 在小数据/噪声大数据上**输给 ARIMA 和 LightGBM**，只在"高维、长期依赖、充足数据"时赢。本项目 1234 样本、单标的、强噪声 → TFT 几乎确定过拟合。**直接多目标 LightGBM 是低成本升级**，TFT 留作"扩样本到 10+ 标的"之后再试。

**集成点**：[`features.py::LABEL_COLS`](../mystock/ml/features.py) 扩列；[`predictor.py`](../mystock/ml/predictor.py) 训练一个 multi-output LGBM；[`backtest.py`](../mystock/ml/backtest.py) 在 T+1 决策时用 T+1..T+3 区间做"挂单有效期/数量档"更优决策。

**工作量**：3-4 天。**风险**：中。**预期收益**：中等（多步区间让限价单挂得更聪明：今天挂的买单若 T+2 才触达，可结合 T+2 区间判断是否值得等）。

**证据**：[TFT vs LightGBM day-ahead (diva-portal)](https://www.diva-portal.org/smash/get/diva2:2057868/FULLTEXT01.pdf)、[ARIMA vs TFT small-data study (SMU)](https://scholar.smu.edu/cgi/viewcontent.cgi?article=1307&context=datasciencereview)、[Practical Forecasting Guide](https://ai.ozdemir.be/guides/forecasting-practical)。

---

### 5.2 建议 8 — 跨标的多任务学习

**是什么**：6 标的当前各自独立训练。可用 LightGBM 多任务（同模型 + 标的 one-hot/embedding）或 federated quantile regression，让腾讯/阿里的港股规律帮 NVDA/TSLA。

**为什么放第三档**：项目明确"单标的独立账户、本币闭环"（[`config.py`](../mystock/ml/config.py) 注释、[`ML_PLAN.md §3.4`](ML_PLAN.md)），且 NVDA/TSLA 高相关、PDD/港股独立——跨标的有信号但风险是**相关性污染**（高相关标的共享模型会放大 regime 同步错误）。需先有 regime 检测（建议 3）再考虑。

**工作量**：1 周。**风险**：中高。**预期收益**：不确定，需实验。

---

## 6. 第四档：基础设施与评估纪律（非算法但放大算法价值）

### 6.1 建议 9 — 多种子方差报告 + Purged/Embargo CV

**是什么**：
- 把 `seed` 从单值改成 `seeds=[0,1,2,3,4]`，报告加"均值 ± 标准差"栏（项目文档已提"多种子看方差"，但代码里 `seed=0` 是单值）。
- 把 walk-forward 升级为 **Purged K-Fold with Embargo**（de Prado），训练/测试间留 embargo 窗口（如 5 天），切断 1h bar 撮合带来的标签泄漏（T+1 区间和 T 日特征在 1h 粒度上可能有重叠信息）。

**为什么贴合本项目**：小样本高方差是头号风险，方差报告是诚实交付的必备件；embargo CV 直接对齐项目"防泄漏"铁律。

**工作量**：1-2 天。**风险**：极低。**预期收益**：提升所有上述算法结论的可信度。

**集成点**：[`predictor.py::walk_forward_eval`](../mystock/ml/predictor.py) 与 [`backtest.py::run_backtest`](../mystock/ml/backtest.py) 的切分逻辑；`report.py` 加方差栏。

---

### 6.2 建议 10 — 撮合保真度升级：1h → 15m

**是什么**：[`fetch.py`](../mystock/ml/fetch.py) 加抓 15m 线（yfinance 60 天可取 ~1500 行/股），仅回测/校准时用，不进生产库。校准吻合率从 88-90% 往上推。

**为什么有价值**：撮合是整个决策层可信度的命门（[`ML_PLAN.md §4.3`](ML_PLAN.md)）。15m 把"1h bar 内 high/low 先后未知"的残余失真再压一个量级。

**集成点**：[`schema.sql`](../mystock/ml/schema.sql) 加 `ml_quotes_15m`；`fetch.py` 加一档（`H_TIERS` 旁加 `M15_TIERS`）；[`simulator.py::match_limit_order`](../mystock/ml/simulator.py) 加 `bar_interval` 参数；[`calibrate.py`](../mystock/ml/calibrate.py) 选 15m 或 1h。

**工作量**：1 天。**风险**：低（仅 60 天窗口，不影响 5y/2y 主数据）。**预期收益**：校准吻合率每提升 5pct，所有决策层结论可信度同步上升。

---

## 7. 推荐执行顺序（路线图）

```
第 1 周（性价比最高）：
  建议 1 (风险调整 reward)   →  1-2 天，最可能修 PDD
  建议 2 (CQR 校准)          →  1-2 天，自动化手调 α
  建议 9 (多种子+embargo)    →  1-2 天，评估可信度地基

第 2-3 周（攻 regime 根因）：
  建议 3 (HMM regime 软切换) →  3-5 天，项目点名的 #1 优先级

第 4 周（给 RL 一次机会）：
  建议 5 (IQL/Cal-QL)        →  1 天，零口径改动
  建议 6 (OPE)               →  2 天，评估闸门

第 5 周+（研究性，按需）：
  建议 10 (15m 撮合)         →  提升所有结论可信度
  建议 4 (Bootstrap TS)      →  若 LinUCB 线性假设成瓶颈再做
  建议 7 (多步多目标)        →  若单步区间成瓶颈再做
  建议 8 (跨标的多任务)      →  扩样本后再考虑
```

**每一步都遵守项目铁律**：独立 ML 库、不碰 web/生产库、永远保留 S0 + 三基线对照、打不过就停在上一阶段交付并如实记录、产物进 `data/ml/reports/<date>/`（已 gitignore）、红涨绿跌配色。

---

## 8. 明确不推荐的做法（避免踩坑）

| 不推荐 | 原因 |
| --- | --- |
| **端到端在线 RL（PPO/SAC）** | 小样本必过拟合，[`ML_PLAN.md §6`](ML_PLAN.md) 已预判、§9 风险 1 已警示，CQL 负结果再次印证 |
| **TFT / 时序 Transformer（现阶段）** | 检索证据：小数据噪声大时输给 LightGBM/ARIMA；需先扩样本（建议 7 的证据） |
| **纯神经 bandit（无 Bootstrap）** | 需 10k+ 决策/天，本项目决策样本仅 ~494 测试日（建议 4 证据） |
| **把 RL 推理写进 web 生产库** | 违反 P6 顺序与"抓取/计算与展示分离"边界，达标稳定前禁做 |
| **跨币种归一成 USD 再训练** | 违反项目"各股本币闭环、不换汇"锁定（[`config.py`](../mystock/ml/config.py) 注释、[`ML_PLAN.md §3.4`](ML_PLAN.md)） |
| **硬切换 regime（if bull → 策略A）** | 软切换（按后验概率加权）对小样本更稳，硬切换在 regime 边界抖动会反复刷单 |
| **跨标的共享现金池 / 组合级再平衡** | 第一版明确锁定单标的独立账户（[`ML_PLAN.md §3.4`](ML_PLAN.md)），非本阶段范围 |

---

## 9. 每条建议的验收门槛汇总

| 建议 | 门槛（不达即停在上一阶段交付并如实记录） |
| --- | --- |
| 1 风险调整 reward | `sharpe` 或 `drawdown_penalized` 在 ≥ 2/6 标的提升 `final_equity` 且不恶化其余 |
| 2 CQR | 测试窗实测覆盖率 ≥ 目标覆盖率，区间宽度 ≤ +15% |
| 3 HMM regime | regime 软切换 bandit 在 ≥ 4/6 标的 `final_equity` ≥ 单 regime bandit，且多种子方差不升 |
| 4 Bootstrap TS | 测试窗 `bandit_ts` 在 ≥ 4/6 标的 ≥ `bandit`（LinUCB） |
| 5 IQL/Cal-QL | ≥ 1 标的 `final_equity > buy_hold` 且不退化为"全程不动" |
| 6 OPE | OPE 估值与模拟器回测净值相关性 ≥ 0.6（否则 OPE 自身不可信） |
| 7 多步多目标 | T+1 决策用多步区间后 `final_equity` 在 ≥ 3/6 标的 ≥ 单步 |
| 8 跨标的多任务 | 须在建议 3 落地后；不恶化任一标的独立模型表现 |
| 9 多种子+embargo | 5 种子 std / mean ≤ 0.3（否则结论方差过大不可上线） |
| 10 15m 撮合 | 校准吻合率 ≥ 90%（否则保留 1h） |

---

## 10. 决策记录与剩余待确认

**本文件提出（未拍板）**：
1. 第一档三件套（风险调整 reward + CQR + HMM regime）应作为下一阶段主攻方向，对应项目"regime 感知 > 扩样本 > RL"优先级。
2. RL 死刑略早，建议给 IQL/Cal-QL 一次机会（零口径改动、零新增依赖）。
3. TFT / 端到端在线 RL 在当前样本量下明确不推荐。

**剩余开工时定（不阻塞）**：
- 风险调整 reward 的 Sharpe 滚动窗口（20 日？30 日？）、回撤阈值（10%？15%？）。
- HMM regime 数（2 还是 3）、特征组合（是否加跨标的宏观如 VIX/恒指）。
- CQR 的校准集占比与是否用 Adaptive Conformal Inference（时序版）。
- IQL 的 temperature τ（检索证据：窄动作空间默认 τ 偏高，需降低）。
- 15m 撮合的 60 天窗口是否够校准（当前 1h 校准用 ~70 单，15m 覆盖更短可能样本更少）。

> 建议先开建议 1（改动最小、见效最快）验证"奖励对齐 > 模型复杂度"假设在本项目仍成立，再依次推进。
