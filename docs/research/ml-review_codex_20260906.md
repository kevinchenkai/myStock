# myStock ML 模块审查与 LightGBM 替代方案研究

日期：2026-09-06  
作者：Codex  
审查代码：main，固定提交 b988ea24cbc1119c4b63e90a94f18518f05ab939  
线上观察：2026-09-06 读取的 2026-09-05 静态报告  
性质：代码与文档审查、官方资料研究、下一轮实验建议；不是新增模型跑分或投资收益认证。

## 1. 结论与建议顺序

**有值得挑战 LightGBM 的工具，但当前证据不足以指定一个“已经更好”的替代品。**

针对 myStock 的“六只港美股、逐股训练、次日 high/low 分位预测”：

1. **首轮替换实验：CatBoost Quantile 与 XGBoost Quantile。** 与现有任务直接对齐、接入范围小，能比较学习器本身的差异。
2. **预训练探索组：TabPFN-3。** 它提供不同于梯度提升树的建模路线，支持分位数输出，值得用同样 16 个特征做小规模样本外比较。
3. **概率产品方向：多分位模型／NGBoost。** 用于报价触达概率和分布表达；这是预测任务的扩展，不能混同于“更换回归器就提高收益”。
4. **实验管理：优先复用现有 runner，必要时加 Optuna。** AutoGluon 可做外围实验，但当前没有必要整体迁移。
5. **Qlib：借鉴模型接口与实验方法。** 它是量化研究框架，也包含 LightGBM，不能把它当作替代 LightGBM 的某一个算法。
6. **暂不优先：从头训练 TFT/LSTM/Transformer、重启既有 RL/HMM。** 预训练模型可以有限试验；已失败的旧策略不能因为换了名词而直接恢复。

以上是基于任务适配性和实施成本的推荐顺序，不是对 myStock 实际精度的排名。

**先决条件：模型选择应使用统一的样本外预测协议；公网旧 Bandit 报告不能直接承担新模型晋级验收。**

## 2. 本次实际看了什么

通过 GitHub 连接读取仓库元数据、文件树与预测代码，随后检出相同提交，检查工作区无改动，并阅读以下主链路：

| 范围 | 文件 | 审查重点 |
| --- | --- | --- |
| 预测器 | mystock/ml/predictor.py | 分位损失、训练参数、CQR 拆分、预测时间 |
| 特征与标签 | mystock/ml/features.py、config.py | 16 个特征、原始价标签、六股配置 |
| 评估 | cv.py、evaluation.py、signal_eval.py、calibrator.py | 时间切分、pinball、naive、覆盖与宽度 |
| 旧回测与报告 | backtest.py、simulator.py、policy.py、report.py | 公网收益来源、账户假设、Bandit reward |
| 新回放 | execution.py、service.py | 现金／库存约束、费用、公司行动、历史来源 |
| 重建与实验 | scripts/ml_experiments/upgrade_matrix.py、rebuild_history.py | 开发样本、重拟合频率、公司行动过滤 |
| 项目状态 | AGENTS.md、docs/OPEN_ITEMS.md | 当前规则、未晋级候选、延期内容 |
| 历史证据 | 9 月升级实验、部署、历史重建回执；7 月稳健性记录 | 已实施内容与研究建议区分 |

公网首页读取成功，HTML 为 2026-09-05 报告，显示六股预测已生成，HK 目标日 09-07、US 目标日 09-08。

**本次没有取得运行数据库、冻结的逐样本实验输出或本机依赖环境，因此没有重新训练 CatBoost、XGBoost 或 TabPFN，也没有独立复现仓库报告的收益。** 下文引用的历史数值归属于仓库实验记录或线上展示；新实验方案均明确为建议。

代码入口：[固定提交](https://github.com/kevinchenkai/myStock/tree/b988ea24cbc1119c4b63e90a94f18518f05ab939)。线上入口：[myStock 静态报告](https://g.ismayday.com/mystock/)。

## 3. 当前 ML 模块的真实定位

### 3.1 这是日频区间预测，而非选股排序

| 项目 | 当前实现 |
| --- | --- |
| 标的 | NVDA、TSLA、PDD、腾讯、阿里、小米 |
| 模型组织 | 每只股票单独训练 high、low 两个模型 |
| 日线采集配置 | 5 年；具体可用行数需运行库确认 |
| 盘中数据配置 | 小时线，用于撮合与回放；小时摘要曾进入离线候选 |
| 输入 | 16 个单股数值特征 |
| low 标签 | low(T+1) / close(T) − 1 |
| high 标签 | high(T+1) / close(T) − 1 |
| 分位设置 | 多数股票 low=0.20、high=0.80；PDD=0.25/0.75 |
| 区间校准 | split CQR，默认目标联合覆盖率 70% |
| 输出 | 次日价格下沿、上沿、宽度、校准信息 |

16 个特征分为收益率、波动率／ATR、均线偏离、日内结构、20 日位置和量比。当前预测器没有接入市场指数、行业、财报日历、新闻等外部变量。

按五年日频估计，每股是约千余条样本的量级；CQR 又保留末尾 25% 作校准，所以底层树实际训练样本更少。这里是配置推算，不是对运行数据库行数的测量。

这使树模型成为合理基线，也意味着“有 H20 算力”不能解决独立金融样本和新信息不足的问题。

来源：[features.py](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/mystock/ml/features.py)、[config.py](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/mystock/ml/config.py)。

### 3.2 LightGBM 实际用得比较克制

当前 _fit_quantile 的主要参数为：300 棵树、学习率 0.03、15 叶、叶节点最少 30 样本、列采样 0.8、单线程；没有内部验证集早停和通用超参搜索接口。

subsample=0.8 但 subsample_freq=0，表示行 bagging 未启用。这不是本次首次发现：仓库 E0_bagging 已专门对照启用 bagging，结果没有通过晋级。因此不能把开启 bagging 当作“确定可修好的精度 Bug”，也不能改变它后继续称为冻结旧基线。

LightGBM 缺失时自动回退 sklearn GradientBoostingRegressor。生产可用性上有回退价值，但比较实验必须让指定后端缺失时明确失败，避免名为 LightGBM 的实验实际跑了另一种模型。

来源：[predictor.py](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/mystock/ml/predictor.py)、[LightGBM 官方参数](https://lightgbm.readthedocs.io/en/latest/Parameters.html)。

## 4. 项目已有证据比通用榜单更重要

9 月实验 runner 采用每股 120 个成熟开发决策日、20 日重拟合，已比较 E0–E5。文档明确：这不是独立 holdout，也没有完成 60-session 前向 shadow。

以下数值来自仓库记录，正值表示损失改善：

| 方案 | low 相对旧基线改善 | high 相对旧基线改善 | 解读 |
| --- | ---: | ---: | --- |
| 开启 bagging | −0.06% | −0.98% | 没有提升证据 |
| 504 日训练窗 | −5.85% | −1.29% | 当前样本中整体变差 |
| 波动率归一化 | −0.84% | −1.32% | 不能重新当作已验证升级 |
| ATR 归一化 | −0.15% | +0.60% | 小幅且不充分 |
| 更小的树 | −0.02% | +1.03% | high 有局部迹象，整体未过关 |
| 小时摘要特征 | −4.64% | −2.16% | 在对应共同小时样本对照下未通过 |
| 滚动校准 | 0.00% | 0.00% | 改的是校准输出，不改善 raw pinball |

最关键的是：**旧 LightGBM 相对 naive_vol 的 skill，low 为 +1.63%，high 为 −0.18%。** 在这段开发窗口里，当前模型对朴素波动率基线的增量很弱。不能据此断言 LightGBM 在所有时期无效，但足以要求新候选同时挑战旧模型和朴素模型。

原晋级要求包括：上下侧相对 E0 均改善至少 5%、对 naive 的 skill 均至少 3%、至少 4/6 股改善、无单股恶化超过 10%。这一轮没有候选通过，不应为了让新工具胜出而放宽门槛。

7 月 HMM／风险 reward 联合增强的历史记录为 15/36 场景胜出、时段结论翻转；这支持保留负结果。它也不是单独隔离每个组件的因果实验，因此不能把所有原因都确定归为样本量或某个机制。

来源：[9 月实验结果](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/docs/records/ml-upgrade-experiment-results_codex_20260904.md)、[7 月稳健性记录](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/docs/records/ml-tier1-robustness_claude_20260718.md)。

## 5. 先处理这些影响判断的事项

### 5.1 公网旧报告与新执行引擎并非同一协议

report.build_report 仍调用 backtest.run_backtest；后者使用 simulator.Account，而不是 execution.replay。

| 协议 | 预测如何产生 | 执行与用途 |
| --- | --- | --- |
| 公网旧 Bandit 曲线 | 60% 历史训练一次，后续测试期间不再拟合预测器 | 旧账户撮合；用于历史探索 |
| E0–E5 开发实验 | 最近 120 个成熟开发日，每 20 日重拟合 | raw／CQR 模型比较 |
| 新历史重建 | 每个目标日切断未来数据、逐日重拟合 | 明确标记 recomputed |
| 当次 live | 截至当时确认数据重新训练并追加版本 | 下一目标日预测，不等于已成熟业绩 |

旧 Account 有现金与库存限制，但没有费用参数、证券 lot/tick 校验和公司行动记账；buy_hold 以初始现金除以 T 日收盘计算份额，旧交易侧采用固定股数单位。新 execution.py 已加入费用、资源预留、库存上限、拆股／分红等能力，但公网曲线仍未全部使用这些能力。

因此：

- 公网展示的高收益不能当作费用后实盘收益。
- 公网命中率与 120-session 重建覆盖率不能混成一个指标。
- 更换模型必须在同一个新 runner／执行协议里比较，不能跨报告拿数字拼接。
- 公网“~50% 属预期”的旧文案，与当前 CQR 目标 70% 并不一致，应按实际协议更新。

来源：[report.py](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/mystock/ml/report.py)、[backtest.py](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/mystock/ml/backtest.py)、[simulator.py](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/mystock/ml/simulator.py)、[execution.py](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/mystock/ml/execution.py)。

### 5.2 Bandit reward 与完整账户日收益不一致

旧 backtest 中，交易前、交易后的权益都用 mark_next，即 T+1 收盘价计算，然后相减得到 step_pnl。这样衡量的是当日交易相对当日收盘的增量，旧持仓从 T 到 T+1 的涨跌在差值里抵消了；随后又减去 buy_hold 的整段当日权益变化。

举例：原来持有 100 股，从 100 跌到 90，当天完全不交易，真实持仓减少 1,000；上述 step_pnl 为 0，再减去另一个基准的涨跌。这不等于完整账户超额日收益。

使用次日价格计算已完成动作的 reward 本身，不等于模型提前偷看次日行情。这里的问题是奖励定义与“组合周期收益”目标不同，且历史持仓后果可能未被当前奖励充分表达。

建议明确选择交易增量目标还是完整账户收益目标，重新固定协议后验证。不能未经独立实验就断言修正 reward 必然提高收益，更不能把奖励改动与换模型同时打包跑分。

来源：[backtest.py 的 Bandit 循环](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/mystock/ml/backtest.py#L173-L189)。

### 5.3 风险包络不等于理想买卖报价

CQR 当前校准事件是：

真实 low ≥ L_hat，且真实 high ≤ H_hat。

把区间往外扩张，通常更容易包住全天价格；如果直接把更低的下沿当买价、更高的上沿当卖价，触达机会通常会减少。

以连续分布且边际分位准确为前提：low 的 20% 分位所对应的买入阈值，触达概率约 20%；high 的 80% 分位所对应的卖出阈值，触达概率也约 20%。CQR 校准后的边界不再保留原始单侧 alpha 的直接解释。

建议产品层将“风险区间”“候选报价”“触达概率”“受约束模拟结果”分别表达。这样未来即使仍使用 LightGBM，也能避免把高覆盖率误当成好交易位置。

### 5.4 正的 width IC 不证明只有执行层有问题

公网 width IC 约为 +0.19 至 +0.42，说明预测宽度与次日真实振幅有相关性。但波动率有持续性，naive_vol 也可能得到正相关。

真正要问的是：复杂模型相对 naive 的 pinball、宽度、覆盖和费用后表现有多少增量。仅凭 width IC 正且没跑赢买入持有，无法确定“瓶颈只在执行”。

线上“学习信号成立”“下一步攻决策层”等自动归因应改为待验证的解释。

### 5.5 CQR 与时间切分的边界

当前代码确实按时间划分训练和校准，不能泛称“随机切分泄漏”。但有三个需要保留的限制：

- CQR 标准有限样本覆盖结论依赖可交换性等条件，非平稳金融时序不能无条件套用。
- 末尾 25% 数据只用于校准，底层树没有直接拟合这些最新样本；按五年日线估算可能隔离约一年。缩短校准窗 E1_cal60/120 已经试过且未达标，因此这是待解释的机制，不是确定收益点。
- 历史 purge=22 来自回看窗加标签跨度。只使用过去数据的特征回看重叠，并不自动等于未来信息泄漏。纯前向评估应按标签实际成熟时刻定义必要隔离；额外 22 日可以作为保守敏感性实验，不能假设越大越正确。

此外，calibrator.conformal_quantile 在极小样本、高目标覆盖时把超出样本范围的阶数截到样本最大值，不能继续声称任意覆盖目标均获理论保证。对当前 70% 且较大校准集通常不是主要触发条件。

来源：[calibrator.py](https://github.com/kevinchenkai/myStock/blob/b988ea24cbc1119c4b63e90a94f18518f05ab939/mystock/ml/calibrator.py)、[CQR 原论文](https://arxiv.org/abs/1905.03222)、[Adaptive Conformal Inference](https://arxiv.org/html/2106.00170v3)。

### 5.6 公司行动与样本掩码需要一致

当前 features.py 对特征使用复权比例，而标签仍是原始 high/low 相对前一日 close。开发 runner 排除了拆股／分红目标日；新逐日重建又包含连续交易日中的事件日。

事件日不是任意删掉就能视为解决。下一轮必须预先规定：普通交易日作主要模型比较，事件日单列；若建模当时已知公司行动，要保存当时可用的事件信息；账户侧独立处理库存和现金／应收变化。不能把样本过滤或标签口径变化宣称为换模型带来的精度提升。

## 6. 候选工具：具体适配与限制

### 6.1 CatBoost：最值得先做的小范围替换

**适配理由。** 它与现有表格分位任务兼容，默认对称树提供不同于 LightGBM 的结构约束；在这个小样本问题上值得比较，但不保证更好。

**第一轮做法。** 分别对 y_low_ret、y_high_ret 使用 Quantile，沿用各股 alpha、特征、样本和外部 CQR。先只改变学习器，不增加新特征，也不同时池化六股。

**第二轮能力。** MultiQuantile 可一次拟合“同一个目标”的多个分位。仍需区分 low、high 两个随机变量；不能把多分位输出误认为自动学到了 high/low 联合路径分布。

**注意。** 当前 16 个特征全是数值，所以原生类别处理不是立即收益来源。只有后续加入 stock_id、market 等类别变量时才更相关。has_time 可保留输入顺序，但不能替代严格时间验证。官方支持表列出 Quantile 支持 GPU、MultiQuantile 暂不支持 GPU；这个数据规模先用 CPU 即可。

来源：[CatBoost Quantile／MultiQuantile](https://catboost.ai/docs/en/concepts/loss-functions-regression)、[树结构与时间参数](https://catboost.ai/docs/en/concepts/parameter-tuning)。

### 6.2 XGBoost：应该与 CatBoost 一起比较

XGBoost 已有原生 reg:quantileerror，quantile_alpha 可指定单个或多个分位，官方示例使用 hist，并提示可能出现 quantile crossing。

现有代码本来已把拟合集中在 _fit_quantile；适合新增一个独立后端实现。不同树生长约束、正则化和叶值估计可能产生互补误差，但不能仅凭“更强正则”断言一定胜过 LightGBM。

先按同一 low/high 标签各训练一个模型；多分位实验另开。检查同一目标各分位单调性，并另查价格下沿不高于上沿。分位输出不等于完整联合分布。

来源：[XGBoost 官方分位示例](https://xgboost.readthedocs.io/en/release_2.1.0/python/examples/quantile_regression.html)。

### 6.3 TabPFN-3：最值得检验的预训练路线

截至本次调研，官方已有 TabPFN-3 技术报告和可用代码。作者报告其在通用表格基准中强于多种调优树模型；这属于官方基准证据，不能推出在 myStock 的金融分位任务中获胜。

它值得试的原因是：通过预训练获得归纳偏置，随后使用当前表格样本进行预测；这与仅用六股数据从头训练深度网络不同。官方回归接口提供预测分布和指定 quantile 输出，能与现有标签对齐。

建议实验：

- 仍使用 16 个特征，low/high 分开处理；
- 与其他模型使用同一个过去训练窗口；
- 必须调用指定分位输出，不能拿默认均值预测去比较 pinball；
- 所有预处理、上下文、缓存均限于该时点允许的历史；
- 冻结 package 版本、权重版本与哈希、推理设置；
- 基础模式先做一次有界实验，暂不开大规模集成／微调。

TabPFN-3 权重有非商业许可证条件；研究评估与生产集成的许可范围须按所选权重确认，不能把可下载权重等同于无条件商用。单纯 CPU 树实验无需使用大显存机器；TabPFN 如使用 GPU，计入完整重训／推理成本后再比较。

来源：[TabPFN-3 技术报告](https://arxiv.org/abs/2605.13986)、[回归与分位接口](https://docs.priorlabs.ai/capabilities/regression)、[官方仓库及许可说明](https://github.com/PriorLabs/TabPFN)。

### 6.4 NGBoost：分布表达的候选

NGBoost 通过预测条件分布参数输出概率分布，官方提供 pred_dist 接口。这适合研究“某个价格阈值有多大概率被穿过”。

例如：买价为 p 时，研究 P(low(T+1) ≤ p)；卖价为 p 时，研究 P(high(T+1) ≥ p)。

限制是分布设定本身可能成为误差来源。默认 Normal 并不能保证适合金融极值、偏态与尾部，须在样本外比较分布评分和尾部校准。low/high 单独预测的边际分布，不能直接相乘估计先买后卖的概率。

若只想检验现有两条边界是否更准，CatBoost/XGBoost 更直接；若想升级概率表达，NGBoost 才更有区分度。LightGBM 多分位和 CatBoost MultiQuantile 也能构建概率近似，并非只有 NGBoost 才能做。

来源：[NGBoost 使用说明](https://stanfordmlgroup.github.io/ngboost/1-useage.html)、[官方项目](https://github.com/stanfordmlgroup/ngboost)。

### 6.5 Optuna、AutoGluon、Qlib：三个不同层次

| 工具 | 本质 | 对当前项目的建议 |
| --- | --- | --- |
| Optuna | 超参数优化工具 | 给现有 runner 加有界搜索，目标函数使用内层时间验证 |
| AutoGluon | 自动化模型训练／选择／集成框架 | 作为外围比较工具；不要默认替换已有 ML 生命周期 |
| Qlib | 量化研究平台 | 借鉴统一模型接口、数据处理和实验记录；暂不全量迁移 |

Optuna 不会自动防止研究者对同一开发窗口反复优化。必须冻结预算、搜索空间和外层评估。

AutoGluon 当前官方文档已有 time_on 等结构化验证能力，因此不能沿用“它完全不支持时间结构”的旧印象。但时间块验证也不自动满足 myStock 的标签成熟、校准隔离及跨市场时点要求；检查实际训练／验证索引，必要时使用外部滚动循环、显式 tuning_data，并关闭未经验证的 bagging／stacking。

Qlib 包含 LightGBM、其他树模型和深度模型。它的基准目标／市场／交易组合与本项目不完全相同，不能把股票排序 IC 榜单直接换算成次日 high/low 预测收益。

来源：[Optuna Study](https://optuna.readthedocs.io/en/stable/reference/generated/optuna.study.Study.html)、[AutoGluon fit 文档](https://auto.gluon.ai/stable/api/autogluon.tabular.TabularPredictor.fit.html)、[Qlib](https://github.com/microsoft/qlib)、[Qlib 模型基准](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)。

### 6.6 Chronos、TimesFM、TFT／LSTM

Chronos 和 TimesFM 属于预训练时序模型，可以作为独立探索候选；不能仅因当前只有六条股票序列就一律排除。TimesFM-3 的官方介绍还包含多目标和分位预测能力。

但它们需要适配目标序列、时点协变量、交易日历与高低价约束。预测 close 的区间不能冒充下一交易日全天 high/low 包络。评估仍要回到相同收益率标签，避免靠拟合价格水平趋势获得看似优异的 RMSE。

对当前输入已经整理成 16 维表格的项目，TabPFN 的替换实验更直接。仅用当前数据从头训练 TFT/LSTM/Transformer，作为第一步的成本收益不如小型树对照；只有引入更大股票池、更长盘中历史或新任务后再重新评估。

来源：[Chronos 官方仓库](https://github.com/amazon-science/chronos-forecasting)、[TimesFM-3 官方说明](https://research.google/blog/timesfm-3-a-zero-shot-foundation-model-for-multivariate-forecasting/)。

## 7. 更有价值的建模扩展，不等于立即实施

仓库最新待办记录：用户暂缓了多分位概率与波动率归一化升级。本次只研究这些方向的价值，不将其视为恢复实施授权。

### 7.1 多分位报价与触达概率

建议将 low/high 各自的多个分位输出用于描述报价机会，同时保留单独校准的风险包络。

令 d 为相对昨收的买价偏移，u 为卖价偏移，则：

P_buy(d) = F_low(d)  
P_sell(u) = 1 − F_high(u−)

如果 d 采用“向下距离的正数”记法，则第一式应为 F_low(−d)，接口必须固定符号约定。

概率评价使用 Brier skill、log loss、带样本数的可靠性分桶，以及尾部样本量。相邻报价档位、同一天股票和重叠持有期不能当作大量独立证据。

触达不是成交，成交也不是获利。盘口排队、价差、现金、库存、退出价格和未来持有风险仍由执行与收益评估承担。

### 7.2 跨股池化

可比较逐股模型与全局模型，但应单独实验：

- 第一阶段保持六股、收益率标签和相同特征，新增 stock_id／market；
- 按决策时间统一截断所有股票数据；
- 港股 T 收盘时，不得使用尚未完成的美股同日期收盘信息；
- 股票数少且同日相关，六股 × 天数不是同量级独立样本；
- 不要把池化、标签归一化、模型更换和新特征一起改。

CatBoost 的类别处理在此时更有用，但 LightGBM 也能做池化对照，不能把数据组织方式的改善都归给某个工具。

### 7.3 增加新信息前先做误差归因

当前特征多为价格／波动率变换，信息来源同质。相比继续堆更多相似技术指标，后续可先把误差分为普通日、跳空日、财报日、除息／拆股日、市场大幅波动日，再决定是否引入市场／行业、事件或盘中信息。

必须使用当时已知的历史快照和发布时间。今日抓到的财报信息、行业归类或事后修订数据不能无差别回填到历史特征。

这只是信息增量假设。已有小时摘要 E5 负结果，不应被包装成“加盘中数据必然有效”。

## 8. 一轮可执行、可停止的模型对照方案

### 8.1 实验矩阵

| ID | 内容 | 只改变什么 |
| --- | --- | --- |
| B0 | 冻结当前 LightGBM | 原始生产机制，作审计锚点 |
| B1 | naive_vol | 简单统计参考 |
| B2 | 受限调优 LightGBM | 同模型的合理调优预算 |
| C1 | CatBoost Quantile | 学习器与其受限超参数 |
| X1 | XGBoost Quantile | 学习器与其受限超参数 |
| T1 | TabPFN-3 指定分位 | 预训练学习器，探索组 |
| A1（可选） | 固定 50/50 原始分位混合 | 检验已证实互补的误差 |

第一轮核心是 B0、B1、B2、C1、X1；T1 可单独计预算。混合不能因为两个模型都存在就默认有用；先检查历史 OOF 误差相关性，再冻结权重。

分位数加权平均不是分布混合的精确分位数。若使用简单平均，称为“预测组合”，对组合后的 raw 输出独立评估，并在独立校准集重新校准；不能直接平均已校准边界后声称保持同样覆盖保证。

### 8.2 统一的数据协议

1. 使用用户本机已有冻结 ML 数据副本；保存代码 SHA、文件哈希、依赖版本、时区和日历。
2. 所有核心候选使用相同 16 个特征、alpha、标签、普通／事件日规则及每股共同测试样本。
3. 先固定重拟合频率。若以现有开发 runner 为起点则统一 20-session；接近晋级后另做与 live 一致的逐日拟合复核，并把它当独立协议。
4. 每个决策时点只训练标签已成熟的数据。不要把固定“隔离一行”视为所有任务通用，未来多日收益标签须相应延长。
5. 每个模型的拟合、调参验证、CQR 校准、外层测试分别记录允许的数据截止。校准段不能同时作为早停／调参数据，却继续声称满足独立 split-conformal 设定。
6. 使用内层时间验证选择超参数，之后只用冻结配置评估外层。最终 CQR 留出段不进入模型／参数选择。
7. 多市场比较时，以统一日历时间块保留同市场／跨股票的共同冲击；不要默认六股 session 序号等价于同一个自然日。
8. 所有候选记录失败／缺失率，不能各自 dropna 后在不同分母上比较。
9. 现有 120-session 开发窗已经查看过，不能重新命名为 untouched holdout。历史其他时期只有能证明未参与选择才可称新留出；否则作为稳健性窗口，并用未来数据承担最终验证。

### 8.3 有界搜索建议

以下只是初始实验设计，不是最优参数：

| 模型 | 建议搜索重点 |
| --- | --- |
| LightGBM | num_leaves 7/15/31，min_child_samples 20/40/80，正则项，树数；bagging 独立记录 |
| CatBoost | depth 3/4/5/6，l2_leaf_reg，学习率／树数；先 CPU |
| XGBoost | max_depth 2/3/4/5，min_child_weight，reg_lambda，学习率／树数，hist |
| TabPFN | 固定一个权重版本、有限推理设置；不同时做复杂调优和微调 |

可给三类树模型各最多 20–30 次内层 trial，同时设置相同时间上限；实际训练时长记录下来。相同 trial 数不代表计算量相同，报告需要同时给成本。先做一个 seed 的筛选，只有接近门槛的候选才进入 0–4 共五个 seed 的复核。

这样允许合理寻找参数，又不会在每股每侧上无边界搜索，放大选择偏差。

### 8.4 评价指标与晋级

| 层 | 主要问题 | 指标 |
| --- | --- | --- |
| 原始预测 | 新模型是否增加预测信息？ | low/high raw pinball、相对 E0 改善、naive skill |
| 校准区间 | 是否在相近覆盖下更窄、更稳？ | 联合覆盖、下漏／上漏、平均及 P90 宽度 |
| 概率扩展 | 输出概率是否可信？ | Brier skill、log loss、可靠性曲线、样本量 |
| 交易执行 | 相同约束下能否产生价值？ | 费用后权益、回撤、资金／库存占用、期末浮盈亏 |
| 运行 | 是否值得部署？ | 拟合／推理时间、内存、失败率、版本可复现性 |

保留现有预测晋级门槛：上下侧分别至少 5% 相对 E0 改善、至少 3% naive skill、至少 4/6 股票改善、无单股损失恶化超过 10%。对边界相同覆盖的改进可另设预注册标准，但不能事后为了胜出更换主指标。

建议用配对时间分块区间表达不确定性，考虑共同市场冲击；不要只比较一个汇总平均数。收益在各股本币账户内评价，跨股先归一化成收益／风险或分别展示，不能直接相加 USD 与 HKD。

对费用后交易验证，复用 execution.replay，固定资金、数量、库存上限、退出规则、费用和证券规则。规则有缺失时明确近似，不能靠增大预算制造成交，也不能把零成交等同于模型优秀。

独立前向 shadow 建议至少 60 个成熟交易日，优先视为第一道观察门槛；若触达／完成交易过少则需更久。它不是收益显著性的保证，也不能靠历史重建压缩完成。

### 8.5 交付内容与停止规则

下一轮执行方应交付：

- 模型后端适配层，保持特征／标签／CQR 与后端解耦；
- 每个候选逐日 raw 预测、校准预测、实际值、来源和时间截止；
- 共同样本损失表、逐股结果、时间分块区间和成本；
- 使用同一事件引擎的受约束策略对照；
- 冻结候选清单、研究查看次数和未触碰的未来验证方案。

候选均未达标时，保留原模型与 naive 对照，记录负结果，停止继续搜索。不要为使用新工具而更改生产模型。

## 9. 建议的实施落点

| 文件／组件 | 建议 |
| --- | --- |
| predictor._fit_quantile | 加可选后端；保留冻结 LightGBM 的精确参数 |
| 新增 ml/models 适配层 | 将 fit／quantile predict／模型元数据统一，依赖显式检查 |
| IntervalModel | 继续管理 low/high 与 CQR；不让各后端各自悄悄改变校准 |
| evaluation.py | 统一共同样本、分侧损失、naive skill、区间校准指标 |
| upgrade_matrix.py 或新独立 runner | 去除对 LightGBM 单一硬编码，采用预注册候选与内层时间验证 |
| runs.py／versions.py | 保存后端、参数、权重哈希、特征顺序、允许的数据截止 |
| report.py | 区分旧历史研究、重建结果和 live；展示样本数、窗口、费用与协议 |
| backtest.py | 单独审查 reward；与模型替换分开，不在一轮中混合归因 |
| execution.py／service.py | 作为统一受约束回放基础；保留同 bar 歧义与规则来源 |

这些是可交给 Codex／Claude 的实施建议，本次未修改仓库、未发布服务、未切换运行模型。

## 10. 最后回答“有没有比 LightGBM 更好的工具”

- **要最低成本验证替代品：CatBoost + XGBoost。**
- **要检验预训练是否带来新的上限：TabPFN-3。**
- **要从区间升级为报价概率：多分位模型，NGBoost 作分布候选。**
- **要改善实验效率：现有 runner + Optuna。**
- **要全面量化研究平台：Qlib 有价值，但当前整体迁移收益不明确。**

当前最大的证据缺口是新模型在统一协议下，能否稳定打赢 naive_vol 和冻结 LightGBM。完成这个比较之前，不应指定生产替代者；完成之后，再判断预测改善是否能在真实资金、费用与库存约束下转化为业务价值。

