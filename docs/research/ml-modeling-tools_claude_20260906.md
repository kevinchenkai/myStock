# ML 建模工具调研：是否有比 LightGBM 更好的工具_Claude_20260906

> 作者：Claude（Fable 5.1）· 日期：2026-09-06 · 基线 `b988ea24cbc1119c4b63e90a94f18518f05ab939` / `main`。状态：只读调研与实验设计，待用户决定是否执行；参考并逐条回应同日的 [Codex 审查](ml-review_codex_20260906.md)。
> 边界：本轮只读取代码、文档、mk 环境版本和 ML 库的表行数（`mode=ro`）；未训练任何模型、未安装新依赖、未修改运行库、未重启 8888、未发布公网报告、未新增调度、未交易。文中所有候选都是待验证假设，不承诺提升幅度。

## 0. 结论

**没有一个工具可以在现有证据下被指定为“已经比 LightGBM 更好”。** 但问题本身应改写：在“六只股票、每股约一千行、信号基本等于条件波动率”的数据上，**什么归纳偏置最合适**。

证据链指向瓶颈不在学习器：零参数的 `naive_vol` 与现网 LightGBM 打平；E0–E5 无候选过门槛；`width_IC` 有值而 `mid_IC` 约为零。因此换一个同范式的树模型，预期仍落在正负三个百分点的噪声带里。

建议把候选按“能补 LightGBM 什么”分成三组，按成本从低到高做，任一组过门槛才进下一层验证：

1. **零依赖组（最优先）**：专门的条件尺度模型（GARCH／HAR-RV／CARR／区间估计量）替换 `vol_20d`，配标准化经验分位；线性 L1 分位回归作低方差对照；OOF 误差相关性检查后的分位组合。
2. **一个新依赖组**：CatBoost Quantile／MultiQuantile，先只换学习器，后承接池化（E6）。XGBoost 只跑固定默认配置，不给搜索预算。
3. **探针组**：TabPFN 指定分位输出，在隔离环境跑一次有界实验，先核许可证、版本与推理成本。

不重开 TFT／LSTM／Transformer 从头训练、RL、HMM regime。门槛沿用现行：上下侧 raw pinball 相对冻结 E0 各改善至少 5%、相对 naive_vol 的 skill 至少 3%、至少 4/6 股方向改善、无单股恶化超过 10%；之后再过费用后收益与前向 shadow。

## 1. 本次实际看了什么

| 范围 | 文件／来源 | 结论要点 |
| --- | --- | --- |
| 预测器 | [predictor.py](../../mystock/ml/predictor.py) | 每股两个分位模型；300 树、15 叶、单种子、无早停、`subsample_freq=0`；缺 LightGBM 时静默回退 sklearn |
| 特征与标签 | [features.py](../../mystock/ml/features.py)、[config.py](../../mystock/ml/config.py) | 16 个日线特征，全为价格／波动／量比变换；特征用复权比例，标签用原始 high/low 相对原始 close |
| 评估与校准 | [cv.py](../../mystock/ml/cv.py)、[evaluation.py](../../mystock/ml/evaluation.py)、[calibrator.py](../../mystock/ml/calibrator.py)、[signal_eval.py](../../mystock/ml/signal_eval.py) | purged walk-forward、raw pinball、naive skill、split CQR、时间轴 IC |
| 实验 runner | [upgrade_matrix.py](../../scripts/ml_experiments/upgrade_matrix.py)、[exp_a_baseline.py](../../scripts/ml_experiments/exp_a_baseline.py) | 120 个成熟开发 session、20 session 重拟合；`lightgbm` 硬编码 import |
| 历史证据 | [实验汇总](../records/ml-upgrade-experiment-results_codex_20260904.md)、[Claude 方案实验 A](../plans/ml-upgrade-plan_claude_20260904.md)、[Tier1 复检](../records/ml-tier1-robustness_claude_20260718.md)、[未尽事项](../OPEN_ITEMS.md) | E0–E5 负结果；naive_vol 在 4/6 股更优；HMM／风险 reward 已证伪 |
| 数据规模 | `data/ml/mystock_ml.db`（只读） | 见 §2 表 |
| 环境 | `/opt/anaconda3/envs/mk` | Python 3.10、lightgbm 4.6.0、scikit-learn 1.7.2、numpy 2.2.6、pandas 2.3.3、scipy 1.15.3；无 torch／xgboost／catboost／statsmodels |

## 2. 已确认的事实

| 项 | 数值或状态 | 含义 |
| --- | --- | --- |
| 日线行数／股 | HK 三股各 1279，US 三股各 1308（2021-06 至 2026-09-04） | 去掉特征回看与校准段后，树模型实际拟合约 900 行 |
| 小时线根数／股 | 约 5400（2023-07 起） | 只够做实现波动率摘要，不够训练序列模型 |
| 预测留档 | 297 行 `ml_predictions`，其中 live 72、recomputed 90、backfill 135 | 前向 shadow 尚未开始 |
| 现网相对 naive_vol 的 skill | low +1.63%，high −0.18%（开发窗口） | 学习器对朴素尺度的增量极弱 |
| E3_small（7 叶／min_child 50） | high +1.03%，5/6 股改善 | 唯一有方向一致微弱迹象的容量调整，未过门槛 |
| 现网 width_IC／mid_IC | 0.14–0.31／约 0 | 模型学到的是“波动大则区间宽”，没有方向信息 |

数值出处为仓库实验记录，本轮未重跑。

## 3. 与 Codex 审查的对照

Codex 审查的主体判断我同意，尤其是：不指定生产替代者、统一样本外协议、公网旧 Bandit 报告不能当验收、bagging 修复不等于基线、CQR 可交换性边界、公司行动掩码要预先规定、加新信息前先做误差归因。以下只列有差异或需补充的点。

| 议题 | Codex 建议 | 本文立场 | 理由 |
| --- | --- | --- | --- |
| 首轮学习器 | CatBoost + XGBoost 并列首轮，各给 20–30 次内层 trial | CatBoost 保留首轮；XGBoost 降为固定默认配置一组，不给搜索预算 | XGBoost 与 LightGBM 同属直方图梯度提升，差异集中在正则细节；证据已说明学习器不是瓶颈，多一组搜索只增加对同一开发窗口的查看次数 |
| 条件尺度模型 | 未列入（naive_vol 仅作基线） | 新增 S 组作为最优先候选 | skill 数字说明信号就是尺度；GARCH／HAR-RV／CARR 是为条件尺度设计的强先验模型，零依赖，最可能同时打败 LightGBM 和 naive_vol |
| 线性分位回归 | 未列入 | 新增 L 组作对照 | sklearn 自带 `QuantileRegressor`；用它区分“树在过拟合”还是“根本没有可学的方向信息” |
| 超参搜索粒度 | 每模型有界 trial，未限定粒度 | 超参按市场共享，不按“每股每侧”单独搜索；回执记录总 trial 数与查看次数 | 六股每侧独立搜索会把选择偏差放大十二倍 |
| TabPFN | 首选探索组，引用 TabPFN-3 报告与许可 | 保留为探针；执行前先核实论文编号、权重许可证、版本哈希与 CPU 推理耗时 | 本轮无法在仓库内核实外部引用；权重为非商业许可，个人项目可用性需确认；torch 不得进入 mk |
| 分位组合 | 先检查 OOF 误差相关性再冻结权重；组合后重新校准 | 采纳，写入 A 组规则 | 一致 |
| 池化 | 单独实验，不与换模型同轮 | 采纳，排在 C1 之后作 P 组 | 一致 |
| 审查发现 5.1–5.6 | 作为先决条件处理 | 同意但不阻塞第一批零依赖实验；登记到未尽事项单独跟踪 | 这些是报告与 reward 口径问题，与 raw pinball 比较相互独立 |
| Optuna／AutoGluon／Qlib | 有界搜索可用；AutoGluon 外围；Qlib 借鉴 | 第一、二批不引入 Optuna，用固定小网格；AutoGluon、Qlib 本轮不碰 | 候选总数已够多，先控制查看次数 |

## 4. 候选工具评估

| 候选 | 补 LightGBM 什么 | 依赖与成本 | 主要风险 | 优先级 |
| --- | --- | --- | --- | --- |
| S 组：GARCH(1,1)／GJR、EWMA、HAR-RV（小时实现波动）、CARR（直接建模日内幅度）、Parkinson／Garman-Klass／Rogers-Satchell 区间估计量 | 把“尺度”这条已被证实的主信号做专；参数少、适合小样本 | scipy 自写 MLE 或 pip `arch`；HAR-RV 需小时线完整性检查（已有 `complete_bars`） | 尺度下限与极端事件保护；HAR 只能覆盖 2023-07 之后 | 第一 |
| L 组：`QuantileRegressor`（L1）在标准化目标上 | 低方差、可解释；多分位单调易控 | 零依赖 | 线性假设可能欠拟合 | 第一 |
| A 组：LightGBM + naive_vol + S 组的 raw 分位平均 | 各模型误差部分独立 | 零依赖 | 分位平均不是分布混合；须重新校准 | 第一（顺带） |
| C 组：CatBoost Quantile／MultiQuantile | 有序 boosting 与对称树抗小样本过拟合；MultiQuantile 单模型输出多分位并训练时抑制交叉；原生类别特征承接池化 | 一个新库，Apple Silicon 有 wheel，CPU 足够 | 与 LightGBM 同范式，增量可能仍在噪声带 | 第二 |
| X 组：XGBoost `reg:quantileerror` | 互补误差的可能性 | 一个新库 | 与 LightGBM 高度同质 | 第二（固定配置一组） |
| T 组：TabPFN 回归指定分位 | 预训练先验，零调参，专为万行以下表格设计 | torch；隔离环境；许可证与版本核实 | 许可证；不可复现；金融分位任务无外部证据 | 第三（探针） |
| N 组：NGBoost | 输出参数化分布，直接给触价概率 | 一个新库 | 分布假设本身成误差源；属概率产品扩展而非准确性主线 | 后置，随 E8 |
| 深度时序从头训练（TFT／LSTM／Transformer）、Chronos／TimesFM | 无 | 高 | 样本不支持；预测 close 区间不等于全天 high/low 包络 | 不做 |
| RL、HMM regime、风险 reward | 无 | 高 | 已有负结果 | 不重开 |

## 5. 合并后的实验矩阵

与 Codex §8.1 合并，编号沿用其 B／C／X／T／A，新增 S／L／P。每个候选只改一个机制。

| ID | 内容 | 只改变什么 | 批次 |
| --- | --- | --- | --- |
| B0 | 冻结当前 LightGBM（含 `subsample_freq=0`） | 无，审计锚点 | 第一 |
| B1 | naive_vol（`vol_20d`） | 无，简单基准 | 第一 |
| S1 | 标准化经验分位 × EWMA／GARCH(1,1)／GJR 预测尺度 | 尺度估计器 | 第一 |
| S2 | 标准化经验分位 × 区间估计量（Parkinson／GK／RS，5／10／20 日） | 尺度估计器 | 第一 |
| S3 | 标准化经验分位 × HAR-RV（小时 RV 1／5／22 日） | 尺度估计器；仅共同小时掩码 | 第一 |
| S4 | CARR 直接建模次日幅度，再按 `close_pos_in_range` 类特征分配上下侧 | 目标定义 | 第一（可选） |
| L1 | `QuantileRegressor` L1，目标按 `vol_20d` 标准化 | 学习器 | 第一 |
| A1 | B0、B1、S 组最优的 raw 分位等权平均；先查 OOF 误差相关性 | 组合 | 第一 |
| B2 | LightGBM 固定小网格（叶 7／15／31、min_child 20／40／80），超参按市场共享 | 容量 | 第二 |
| C1 | CatBoost Quantile，沿用 alpha／特征／CQR | 学习器 | 第二 |
| C2 | CatBoost MultiQuantile（0.1／0.2／0.25／0.5／0.75／0.8／0.9） | 多分位输出方式 | 第二 |
| X1 | XGBoost `reg:quantileerror` 固定默认配置 | 学习器 | 第二 |
| P1 | 六股池化（标签按尺度标准化，加 `stock_id`／`market`），LightGBM 与 CatBoost 各一组，与 per-stock 对照 | 数据组织 | 第二（C1 之后） |
| T1 | TabPFN 指定分位，16 特征，单权重版本 | 预训练学习器 | 第三 |

停止规则：第一批全部未达门槛，则记录负结果后仍可做第二批，因为第二批回答的是不同问题（换学习器是否有用）。第二批也全部未达门槛，则第三批只作探针，不再扩展；生产保持 B0，不为使用新工具更换模型。

## 6. 统一协议

沿用 Codex §8.2 的九条，补充以下约束：

- **后端严格**：实验中指定后端缺失时直接失败，不允许静默回退 sklearn；`upgrade_matrix` 的 `lightgbm` 硬编码改为候选注册表。
- **查看计数**：120-session 开发窗口已被 E0–E5 和 Claude 实验 A 查看过；每批新增的查看次数写进回执。任何候选接近门槛后，先跑 0–4 五种子，再在未查看的更早历史窗口做稳健性检验，最后才是前向 shadow。
- **超参共享**：树模型超参按市场（US／HK）共享，内层时间验证选参；不做每股每侧独立搜索。
- **尺度候选的公平性**：S 组与 B1 使用相同的训练集经验分位与相同尺度下限；尺度估计器只能用截至 T 收盘的数据拟合，GARCH 参数在每次重拟合时重新估计。
- **组合规则**：A1 只平均 raw 分位，组合后在独立校准段重新做 CQR；不平均已校准边界。
- **公司行动**：普通交易日作主比较，拆股／分红目标日单列，口径与 `upgrade_matrix.features` 一致。
- **最后一道闸**：预测门槛通过后，用 `execution.replay` 在固定资金、数量、库存上限、费用与退出规则下比较费用后权益、回撤与占用；预测改善不自动等于收益改善（AGENTS.md）。

## 7. 依赖与工程落点

- Web 顶层 import ML，因此 catboost／xgboost／arch 只能像 lightgbm 一样可选导入，或只留在 `scripts/ml_experiments`；torch 一律不进 mk，TabPFN 在独立 conda 环境跑。
- 建议新增 `mystock/ml/models/` 适配层：`fit(X, y, alpha)`／`predict_quantile`／元数据（后端、版本、参数哈希、特征顺序）。`IntervalModel` 继续管理 low/high 与 CQR，不让各后端各自改校准。
- `runs.py`／`versions.py` 记录后端与权重哈希，使 recomputed 与 live 可归因。
- 第一批不改任何生产代码，只加实验脚本；候选未过门槛就不进入 `predictor.py`。

## 8. 待用户决定

1. 是否授权在冻结副本上执行第一批（S／L／A，零依赖）。
2. 是否允许在 mk 或独立环境安装 catboost／xgboost 以执行第二批。
3. TabPFN 探针是否值得投入，取决于许可证核实结果。
4. Codex 审查提出的报告协议、reward 口径、标签与特征复权口径不一致三项，是否作为独立工单处理；已登记到 [未尽事项](../OPEN_ITEMS.md)。
