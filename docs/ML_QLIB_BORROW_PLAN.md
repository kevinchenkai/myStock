# 借鉴 Qlib 的三项优化 —— 详细实施 Plan（讨论稿 v0.2，已 double-check）

> 作者：Claude · 日期：2026-07-18 · v0.2（同日 double-check 修订）
> 依据：逐文件读 `mystock/ml/`（15 文件 2561 行）+ 三份 ML 文档 + qlib 源码。
>
> **v0.2 修订记录（double-check 发现并修正 6 处）**：
> 1. **修正泄漏定性（最重要）**——本项目扩张窗 + 1 天标签下**不存在真正的未来函数泄漏**；② 的动机改为"边界重叠卫生 + 相似性乐观 + 调参窥视"（第三项才是最大乐观源，见 §2.1）。
> 2. 修 `cv.py` 草图 **fold-0 必被丢弃**的 off-by-purge bug（`te_start` 需平移 `purge_w`，见 §2.2）。
> 3. 特征回看按**价格空间复合**修正：`vol_20d = rolling(20)∘pct_change(1)` → 实际 **21** 天，`purge_w = 22`（见 §1.1）。
> 4. CQR 校准隔离从 `purge_w`(22) 改为 **1 行**（label_horizon）——烧 22 行校准样本且违背 conformal 可交换性诉求（见 §2.3）。
> 5. ③ 新增**宽度 IC**（预测区间宽 vs 真实振幅）为主信号指标——分位模型没学过方向，方向 IC≈0 ≠ 信号无效（见 §3.1）。
> 6. 新增**锁箱 holdout** 验收门槛——对付 `ALPHA_BY_CODE`/`COVERAGE_BY_CODE` 在同批数据上调档的窥视；purge / 多种子都修不了它（见 §2.4）。
> 配套阅读：[`docs/myStock-ML借鉴Qlib深度评估-Claude.html`](myStock-ML借鉴Qlib深度评估-Claude.html)（评估）、[`docs/Qlib深入解读-Claude.html`](Qlib深入解读-Claude.html)（qlib 机制）。

---

## 0. TL;DR（结论先行）

本 Plan 把评估报告里"该借鉴 qlib 的三样东西"落成可动手的工程方案，**全部零 qlib 依赖、零口径改动、纯新增或小改**，严守项目铁律（独立 ML 库 / web 只读 / 永留 S0+三基线 / 打不过就停 / 红涨绿跌 / `data/` gitignore）：

| # | 借鉴项 | 本质 | 落地物 | 工作量 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| ① | 表达式化特征库 | `features.py` → 可组合/可缓存**因子注册表** | 新增 `factors.py`，重构 `features.py` | ~1 天 | P2（第 2 步）|
| ② | Purged + Embargo + 滚动 | 隔离带 + 多种子 + **锁箱 holdout**，把评估提到可信强度 | 新增 `cv.py`，改 `predictor` / `backtest` 切分 | ~2 天 | **P0（最高）** |
| ③ | 两级评估（IC vs 净值）| 抄 qlib `SigAnaRecord`：先看**信号**，再看**策略** | 新增 `signal_eval.py`，接进 `report.py` | ~1 天 | **P0（最高）**|

**核心排序理由**（与评估报告一致）：② 和 ③ 修的是"评估可信度"这个**当前最痛、影响致命/高**的缺口——没有可信的尺子，① 以及团队既有的建议 1/2/3 的"提升"都读不准。**所以 ② ③ 必须先做，① 随后，模型/算法一律靠后。**

**一句话**：先把尺子修准（②③），再谈加因子（①），最后才谈换模型。

---

## 1. 现状锚点（Plan 的出发点，全部核对过源码）

在动手前，先把三项要改的**真实接口**钉死，避免 Plan 悬空：

### 1.1 特征与标签（`features.py`）
- `build_features(daily) -> DataFrame`：加 16 个特征列 + 2 个标签列。
- `FEATURE_COLS`（16 个）、`LABEL_COLS = ["y_high_ret", "y_low_ret"]`。
- **关键窗口面**：特征含 `vol_20d / ma20_dev / dist_hi_20 / vol_ratio_20` 等 **20 日滚动窗口**；标签 `y_high_ret = high(T+1)/close(T)-1` 是 **T+1** 的。
  → **注意复合回看（v0.2 修正）**：`vol_20d = ret1.rolling(20).std()`，而 `ret1 = pct_change(1)` 自身再看 1 天 → **价格空间实际回看 21 天**（同理 `atr_14` 实际 15 天）。
  → **特征回看（价格空间）= 21 天，标签前看 = 1 天**。这两个数字是 ② 里 purge 宽度的依据（`purge_w = 22`）。

### 1.2 walk-forward 切分（`predictor.walk_forward_eval`）
```python
# 当前：纯滚动，train=[0, tr_end), test=[tr_end, te_end)——两段紧贴，无隔离带
tr, te = df.iloc[:tr_end], df.iloc[tr_end:te_end]
```
- **事实核查（v0.2 修正定性）**：扩张窗、test 恒在 train 之后、标签只前看 1 天 → **不存在传统"未来函数"泄漏**——train 末行标签用到的 `high/low(tr_end)`，在首个测试决策时点（`tr_end` 收盘）已实现，线上每日重训同样可得。真正的问题是三个更"软"但同样伤结论可信度的东西（详见 §2.1）：边界标签重叠（1 天）、序列相关带来的相似性乐观、以及**调参窥视**（α/coverage 在同批数据上定档）。
- CQR 校准集从**训练末尾**切（`predictor.IntervalModel.fit` 的 `cal_df = df.iloc[-n_cal:]`）：`fit_df` 末行标签与 `cal_df` 首行共享同一天数据 → 需 **1 行** gap 即可（v0.1 曾写 `purge_w`，过度，理由见 §2.3）。

### 1.3 回测切分（`backtest.run_backtest`）
```python
split_at = valid[int(len(valid) * cfg.train_frac)]   # 单点切分：train < split_at ≤ test
```
- 同样两段紧贴，无 embargo。预测器在 `< split_at` fit、`≥ split_at` 推理。

### 1.4 现有评估口径（只有"策略层"，缺"信号层"）
- `predictor`：`interval_hit_rate`、`pinball_high/low`、`mae_high/low`、`width_pct`。
- `backtest`：`final_equity`、`net_value`、`interval_hit_rate`（策略净值 + 区间命中）。
- **缺失**：信号方向/幅度的 **IC / RankIC / ICIR**——无法回答"bandit 输是信号没预测力，还是执行没把信号用好"。

### 1.5 报告集成点（`report.py`）
- `build_report()` 循环 `mlcfg.TARGETS`，每股 `run_backtest` + `predict_next_day` → `_stock_section`。
- 新增指标只需在 `_stock_section` / 总览表插列，**不改 web、不写生产库**。

### 1.6 测试风格（`tests/test_ml_*.py`）
- 纯函数、确定性、`np.random.default_rng(0)` 造数据、断言精确到分位 index。新代码沿用此风格（可单测优先）。

---

## 2. 借鉴 ②：Purged + Embargo + 滚动 CV【最高优先，先做】

> **为什么先做**：这是"尺子"本身。评估报告第 3 节的悬案（bandit 到底有没有真输给 S0）在没有防泄漏切分前**无法判定**。修好它，等于第一次让所有结论变得可信。对应团队 `ML_ALGORITHM_PROPOSAL 建议 9`，本 Plan 将其**提级为头号优先**。

### 2.1 原理（López de Prado，qlib 生态同源）——先把"为什么"说准

**Double-check 修正（v0.2，重要）**：本项目是**扩张窗 + test 恒在 train 之后 + 标签只前看 1 天**。train 末行标签用到的 `high/low(tr_end)` 在首个测试决策时点已实现，线上每日重训同样可得 → **这里不存在传统"未来函数"泄漏**。② 要修的是三个更"软"、但同样伤结论可信度的问题：

1. **边界标签重叠（1 天）**：train 末行与 test 首行共享同一天数据，折间指标非独立 → purge 掉。
2. **序列相关的相似性乐观**：test 开头样本与 train 末尾样本处于同一波动 regime、高度相似，树模型"背出近期水平"也能得分 → 泛化能力被高估。留 `feat_lookback` 宽度的隔离带把这层乐观也挤掉（保守卫生，代价仅 22 行样本）。
3. **调参窥视（最大项，purge 修不了）**：`ALPHA_BY_CODE` / `COVERAGE_BY_CODE` 按"各股实测甜区"在**同一批 walk-forward 数据**上定档（`config.py` 注释自述）——评估与调参共用数据，这才是最大的系统性乐观源。唯一有效的闸是**锁箱 holdout**（§2.4 新增门槛）。

对本项目的具体数字：
- **特征最长回看（价格空间）** `feat_lookback = 21`（`vol_20d` 复合回看，见 §1.1）。
- **标签前看** `label_horizon = 1`（次日）。
- **purge 宽度** = `label_horizon + feat_lookback = 22`。
- **embargo**：扩张窗单向滚动下为 **no-op**（不存在"test 之后的 train"）；字段保留仅为将来切 K-Fold 模式时复用。

### 2.2 落地物：新增 `mystock/ml/cv.py`（纯函数、可单测）
```python
"""借鉴 ② — Purged/Embargo 滚动切分（防泄漏评估地基）。

纯 index 运算，不依赖 LightGBM/sklearn。返回 (train_idx, test_idx) 列表，
供 predictor.walk_forward_eval / backtest 复用同一套切分口径。
"""
from dataclasses import dataclass

@dataclass
class PurgedConfig:
    n_folds: int = 5
    min_train: int = 250         # purge 之后仍需保有的最小训练行数
    feat_lookback: int = 21      # = features.py 最长回看窗（价格空间复合回看，勿填 20）
    label_horizon: int = 1       # = 标签前看天数（次日）
    embargo: int = 0             # 扩张窗下为 no-op；保留字段仅为将来 K-Fold 模式

def purged_walk_forward(n: int, cfg: PurgedConfig) -> list[tuple[list[int], list[int]]]:
    """扩张窗滚动 + purge。返回每折 (train_idx, test_idx)。

    第 k 折：test = [te_start, te_end)；train = [0, te_start - purge_w)。
    purge_w = label_horizon + feat_lookback：剔除与 test 边界"标签重叠 +
    高度相似"的训练尾部（定性见 §2.1）。embargo 在扩张窗下为 no-op。
    """
    purge_w = cfg.label_horizon + cfg.feat_lookback
    splits = []
    # v0.2 修 bug：te_start 需平移 purge_w，否则第 0 折 train 恒 < min_train 被丢弃
    first_te = cfg.min_train + purge_w
    fold = max(20, (n - first_te) // max(1, cfg.n_folds))
    for k in range(cfg.n_folds):
        te_start = first_te + k * fold
        te_end = min(te_start + fold, n)
        if te_start >= n or te_end <= te_start:
            break
        # purge：训练段尾部 purge_w 行（与 test 边界重叠/高度相似），剔除
        tr_end = max(0, te_start - purge_w)
        train = list(range(0, tr_end))
        test = list(range(te_start, te_end))
        # embargo：扩张窗单向滚动下不存在"test 之后的 train"→ no-op（见 §2.1）
        if len(train) >= cfg.min_train:
            splits.append((train, test))
    return splits
```
**要点**：
- 扩张窗（expanding）下，purge 只需砍训练段**尾部** `purge_w` 行（test 在训练段之后，训练段没有"未来"部分）。
- embargo 在**扩张窗单向滚动**里是 no-op；显式保留字段是为将来若改成 sklearn 式 `PurgedKFold`（test 在中间、两侧都有 train）时能复用同一配置。
- 纯 index，**零数据依赖**，单测只验 index 集合。
- `feat_lookback` 短期手填 21；① 落地后改从 `factors.max_lookback()` 自动推导（§4.3），杜绝"加了长窗因子忘改 purge"。

### 2.3 接入点
1. **`predictor.walk_forward_eval`**：把内部的 `for k in range(n_folds)` 手工切分换成 `cv.purged_walk_forward(n, ...)` 产出的 `(train, test)`。指标计算逻辑（pinball/hit/mae）完全不动。
   - 新增开关 `purged: bool = True`（默认开）；`purged=False` 保留旧行为做 A/B 对照。
2. **`predictor.IntervalModel.fit` 的 CQR 校准集**：`fit_df` 末行标签与 `cal_df` 首行共享同一天数据 → 在两者之间留 **`label_horizon`（=1 行）** 即可。改 3 行：
   ```python
   n_cal = max(5, int(len(df) * self.cal_frac))
   gap = self.label_horizon      # v0.2：只需 1 行。勿用 purge_w(22)——
   fit_df = df.iloc[:-(n_cal + gap)]     # 那会烧掉 22 行样本，且校准集应
   cal_df = df.iloc[-n_cal:]             # 尽量贴近 test（conformal 可交换性）
   ```
3. **`backtest.run_backtest`**：单点 `split_at` 改为"训练段末尾砍 `purge_w` 行"：
   ```python
   split_at = valid[int(len(valid) * cfg.train_frac)]
   purge_w = cfg.feat_lookback + cfg.label_horizon
   train_df = feat.loc[[i for i in valid if i < split_at - purge_w]]   # 只改这一行
   test_idx = [i for i in valid if i >= split_at]                       # test 不变
   ```
   → 回测的预测器 fit 段与 test 段之间有隔离带，净值结论更诚实。
   - **备注（v0.2）**：purge 后 CQR 校准集（取自 train 尾部）随之整体前移 22 行，对 test 的"新鲜度"略降。若实测覆盖率因此系统性偏离目标，可改为"校准集仍取紧邻 test 的最后 `n_cal` 行（校准非记忆式训练，取近样本符合线上实际），仅预测器 fit 段后撤"。二选一，以**实测覆盖率达标**为准。

### 2.4 验收门槛（沿用团队建议 9，v0.2 增锁箱）
- **多种子稳定性（主力）**：≥5 种子跑 `walk_forward_eval`，`std/mean(interval_hit_rate) ≤ 0.3`；否则判"结论方差过大、不可上线"，如实记录。
- **purge 前后对照（预期几乎不变）**：报告并列 `purged=True/False` 两组命中率。v0.2 事实核查后，**预期两组差异微小**（只少 22 行训练样本、无真泄漏可挤）——对照的意义是**证伪"边界泄漏主导结论"并留档**，不是期待大变化。若差异显著，反而应先怀疑实现 bug。
- **锁箱 holdout（v0.2 新增，对付调参窥视）**：保留最近 **6 个月**数据为锁箱：α 档、coverage、reward 超参、bandit 超参的一切调整**不得触碰**；全部定档后**只评一次**并如实写进报告。这是 §2.1 问题 3 唯一有效的闸——purge 和多种子都修不了"评估与调参共用数据"。
- **回测重估**：用 purged 切分 + 多种子重跑 P3 六标的，看 bandit vs S0 的相对关系是否**稳定**（第 1 步的核心动作）。

### 2.5 测试（新增 `tests/test_ml_cv.py`）
- `test_no_overlap`：任一折 `max(train) < min(test) - purge_w`（隔离带存在）。
- `test_min_train_respected`：所有折 `len(train) ≥ min_train`。
- `test_expanding`：折 k+1 的 train 是折 k 的超集（扩张窗单调）。
- `test_purge_width_matches_feature_window`：`purge_w == feat_lookback + label_horizon == 22`。
- `test_first_fold_survives`：n 充足时第 0 折存在且 `len(train) == min_train`（回归 v0.1 的 off-by-purge bug）。
- `test_degenerate_short_series`：n 很小时优雅降折数、不抛异常。

---

## 3. 借鉴 ③：两级评估（信号 IC vs 策略净值）【最高优先，与 ② 并行】

> **为什么先做**：它回答评估报告第 3 节那个真正的悬案——**bandit 输，是信号没预测力，还是执行没用好信号？** 现在系统只有"策略净值"一级，看不到"信号质量"这一级。抄 qlib `SigAnaRecord` 的口径补上第一级。

### 3.1 单标的下的 IC 口径（关键调整，不能照抄 qlib）
qlib 的 IC 是**截面**的（同一天跨数千标的的预测与真实收益的相关）。myStock 是**单标的**——截面维度=1，截面 IC 无意义。**改用时间轴滚动 IC**：
- **预测信号**：对每个交易日 T，取可与真实值比较的标量。三个候选，**按对本系统的忠实度排序（v0.2 重排）**：
  1. **宽度信号（主指标）**：`width_pred = hi_ret - lo_ret` vs 真实振幅 `y_range = y_high_ret - y_low_ret` 的相关（"宽度 IC / 波动 IC"）。理由：predictor 是分位模型，**从未被训练去猜方向**；决策层赚的是"低买高卖吃区间"，其可用性首先取决于预测区间对真实振幅的跟踪能力。
  2. `mid_ret = (lo_ret + hi_ret) / 2` —— 区间中点隐含的方向/幅度（次级诊断）。**要预期它可能 ≈0**——那只等于"无方向信息"，不等于"信号无效"，二者结论完全不同。
  3. `dir_signal = sign(mid_ret)` 方向命中率（再次级）。
- **真实目标**：`y_next_ret = close(T+1)/close(T) - 1`（新增一个标签列，或用现成 `y_high_ret/y_low_ret` 的中点近似）。
- **IC**：`corr(mid_ret_t, y_next_ret_t)`（Pearson）在整个测试段 + **滚动窗（如 60 日）** 两种口径。
- **RankIC**：`spearman(mid_ret, y_next_ret)`（对异常值稳健）。
- **ICIR**：滚动 IC 的 `mean / std`（信号稳定性——比单点 IC 更能说明问题）。

> **诚实标注**：单标的时间轴 IC 的样本量 = 测试天数（几百），比截面 IC 弱。它更多是**"信号随时间是否稳定有预测力"**的诊断，而非选股 IC。报告里要写清这个局限。

### 3.2 落地物：新增 `mystock/ml/signal_eval.py`（纯函数、可单测）
```python
"""借鉴 ③ — 信号层评估（IC/RankIC/ICIR），抄 qlib SigAnaRecord 口径。

单标的 → 时间轴 IC（非截面）。纯 numpy/scipy，不依赖建模库，可单测。
"""
import numpy as np
from scipy.stats import pearsonr, spearmanr

def ic(pred: np.ndarray, target: np.ndarray) -> float:
    """Pearson IC（去 NaN、样本<3 或零方差 → nan）。"""
    ...

def rank_ic(pred, target) -> float:
    """Spearman RankIC。"""
    ...

def rolling_ic(pred, target, window: int = 60) -> np.ndarray:
    """滚动窗 IC 序列（每点用过去 window 天）。"""
    ...

def icir(pred, target, window: int = 60) -> float:
    """ICIR = mean(rolling_ic) / std(rolling_ic)。信号稳定性。"""
    ...

def width_ic(width_pred, y_range) -> float:
    """宽度 IC：预测区间宽 vs 真实振幅（Spearman）。本系统的主信号指标。"""
    ...

def signal_report(lo_ret, hi_ret, y_low, y_high, window: int = 60) -> dict:
    """一站式：返回 {width_ic, mid_ic, mid_rank_ic, icir, dir_hit, n}。"""
    ...
```

### 3.3 接入点
1. **`backtest.run_backtest`**：循环里已经有每日的 `lo_r, hi_r` 与真实 `y_high_ret/y_low_ret`。收集 `mid_ret = (lo_r+hi_r)/2` 与 `y_next_ret`（新增一行计算），测试段末调用 `signal_eval.signal_report(...)`，塞进 `result["signal"]`。**不改 reward、不改动作、不改净值**——纯旁路观测。
2. **`predictor.walk_forward_eval`**：同样在每折计算 IC，汇总进 `metrics["ic"] / metrics["rank_ic"] / metrics["icir"]`。
3. **`report.py`**：
   - `_stock_section` 加一行"信号诊断：IC=… RankIC=… ICIR=…"。
   - 新增一个"**两级评估**"小结块，把信号层（宽度 IC / 方向 IC）与策略层（净值超越）并排，规则化生成一句诊断（v0.2 按宽度优先重写）：
     - **宽度 IC 高 + 净值输** → 区间跟得住振幅但没换成钱 → **执行/挂价档是瓶颈**（攻决策层）。
     - **宽度 IC 低** → 区间连振幅都跟不住 → **攻预测层/因子**（即借鉴 ①）。
     - **方向 IC ≈ 0** → 只说明无方向信息（分位模型本就没学方向），**单凭它不下"信号无效"结论**。
   - 这句诊断**直接告诉团队下一步该往哪使劲**——这是两级评估最大的价值。

### 3.4 验收门槛
- **能分诊**：报告能对每支标的输出"信号层 / 策略层"两个独立结论，且当两者矛盾（IC>0 但净值输）时给出明确指向。
- **IC 稳健性**：随 ② 的多种子一起报 IC 的种子间方差。
- **不设"IC 必须 > X"的硬门槛**（单标的 IC 天然弱），定位是**诊断工具**而非 go/no-go 闸——go/no-go 仍以净值 vs 三基线为准（沿用 `ML_PLAN §7`）。

### 3.5 测试（新增 `tests/test_ml_signal_eval.py`）
- `test_ic_perfect_correlation`：`pred == target` → IC=1；`pred == -target` → IC=-1。
- `test_ic_zero_for_noise`：独立随机 → IC≈0（容差）。
- `test_rank_ic_robust_to_outliers`：注入异常值后 RankIC 比 Pearson IC 稳。
- `test_icir_stability`：稳定正 IC 序列的 ICIR > 抖动序列。
- `test_width_ic_tracks_volatility`：构造"预测宽度随真实振幅同步缩放"的序列 → width_ic 高；打乱后 → ≈0。
- `test_handles_nan_and_short`：NaN 剔除、样本<3 返回 nan 不抛异常。

---

## 4. 借鉴 ①：表达式化因子库【② ③ 之后做】

> **为什么排后**：加因子是"让信号更强"，但在尺子（②③）修准前，无法判断新因子到底有没有用。修好尺子后，① 让"试因子"成本从"改代码"降到"改配置"，为后续 A/B 因子、乃至远期 RD-Agent 式自动挖因子铺路。

### 4.1 目标（借 qlib 思想，不引入 qlib）
qlib 的洞见：**因子 = 声明式表达式**，可组合、可版本化、可缓存。myStock 不需要 qlib 的 bin 数据服务与表达式解析器，只需一个**本地因子注册表**：把散落在 `build_features` 里的 16 段计算，改成一张 `名字 → 计算函数` 的表。

### 4.2 落地物：新增 `mystock/ml/factors.py`
```python
"""借鉴 ① — 可组合因子注册表（本地轻量版，不依赖 qlib）。

每个因子 = 一个 (name, fn) —— fn 接收已 adj 化的中间量，返回一列 Series。
加新因子 = 往 REGISTRY 加一行，不改 build_features / predictor / backtest。
"""
from dataclasses import dataclass
from typing import Callable
import numpy as np, pandas as pd

@dataclass(frozen=True)
class Factor:
    name: str
    fn: Callable          # fn(ctx: FactorCtx) -> pd.Series
    lookback: int         # 最长回看窗（**价格空间**，复合窗口要相加：rolling(20)∘pct_change(1)=21；供 cv.py 自动推导 purge_w）

@dataclass
class FactorCtx:
    """已 adj 化的中间量（adj_close/adj_high/adj_low/adj_open/volume/ret1），
    集中在一处算好，各因子复用，避免重复计算。"""
    adj: pd.Series; adj_high: pd.Series; adj_low: pd.Series
    adj_open: pd.Series; volume: pd.Series; ret1: pd.Series

REGISTRY: list[Factor] = [
    Factor("ret_5d",   lambda c: c.adj.pct_change(5),           5),
    Factor("vol_20d",  lambda c: c.ret1.rolling(20).std(),      21),  # 复合回看：rolling(20)∘pct_change(1)
    Factor("ma20_dev", lambda c: c.adj/c.adj.rolling(20).mean()-1, 20),
    # ... 迁移现有 16 个 ...
    # 新因子零成本加入（示例，qlib Alpha158 风格）：
    Factor("corr_pv20", lambda c: c.ret1.rolling(20).corr(np.log(c.volume+1)), 20),
]

def build_factor_matrix(daily) -> pd.DataFrame:
    """算 FactorCtx → 遍历 REGISTRY → 拼成特征矩阵（替代 build_features 的特征部分）。"""
    ...

def max_lookback() -> int:
    """REGISTRY 里最长回看窗 → 供 cv.PurgedConfig.feat_lookback 自动取值。"""
    return max(f.lookback for f in REGISTRY)
```

### 4.3 与 ② 的联动（这是 ① 的额外红利）
- 现在 `cv.PurgedConfig.feat_lookback` 是**手填 20**。接入 `factors.max_lookback()` 后**自动推导**——加了一个回看 60 天的因子，purge 宽度自动跟着变，**再也不会因忘记改 purge 宽度而引入泄漏**。这是把 ① 和 ② 绑在一起的关键收益。

### 4.4 迁移策略（保证零回归）
1. `factors.py` 先把现有 16 个特征**逐一迁进 REGISTRY**，`build_factor_matrix` 产出的列与 `FEATURE_COLS` **完全一致**（顺序、数值）。
2. `features.build_features` 内部改为调用 `build_factor_matrix` 拼特征 + 原样保留标签计算；`FEATURE_COLS` 改为 `[f.name for f in REGISTRY]`。
3. **回归测试**：新旧 `build_features` 对同一 daily 输出**逐列 `np.allclose`**（新增 `test_ml_factors.py::test_migration_identical`）。全绿才算迁移成功。
4. 迁移完成后，加新因子只需动 `REGISTRY`，`predictor/backtest/report` 一行不改。

### 4.5 缓存（可选，量小时先不做）
- qlib 的因子缓存价值在"大数据重复实验"。本项目每股 ~1255 行，全量算一遍毫秒级，**缓存暂不必要**。
- 若将来因子数暴涨（RD-Agent 式批量生成），再加 `@lru_cache` 或按 `(symbol, factor_name, data_hash)` 存 parquet。**Plan 记录但不落地**。

### 4.6 验收门槛
- **零回归**：迁移后新旧特征矩阵逐列 `allclose`；全套 `pytest` 与迁移前 passed 数一致。
- **加因子成本**：新增一个因子 = REGISTRY 加一行 + 重跑 walk-forward，**不改任何建模文件**。
- **purge 自动化**：`cv` 的 `feat_lookback` 从 `factors.max_lookback()` 取值，验证加长回看因子后 purge 宽度自动增大（`test_factors_drive_purge`）。
- **复合回看不漏记（v0.2 新增）**：`test_lookback_composition` 用"篡改 lookback 之外的数据、因子输出不得变"的方式**实测**每个因子声明的 lookback 足够大（与 `regime.py` 自检同思路，比人工审注册表可靠）。

---

## 5. 执行顺序与里程碑

```
第 0 步 · 修尺子（最高优先，②③ 并行，~2.5 天）
  ├─ ② cv.py + 接 predictor/backtest 切分 + CQR 校准隔离   → 门槛：5 种子 std/mean ≤ 0.3
  └─ ③ signal_eval.py + 接 backtest/report 两级评估        → 门槛：能分诊"信号 vs 执行"
        ↓
第 1 步 · 用新尺子重估现有结论（~0.5 天）
  └─ purged 切分重跑 P3 六标的；两级评估看 bandit 到底信号弱还是执行弱
        ↓ （重点不是结论变不变，而是第一次可信；多种子+锁箱是主力，purge 预期只有微小影响）
第 2 步 · ① 因子注册表（~1 天）
  └─ factors.py 迁移 16 特征（零回归）+ cv.feat_lookback 自动化 + 试 1-2 个新因子
        ↓
第 3 步 · 回到团队既定路线（qlib 不再介入）
  └─ 建议 1/2/3（reward/CQR/regime）的效果，现在可以用可信的尺子重新评估

储备（本 Plan 记录、暂不落地）：因子缓存、DDG-DA 漂移建模、RD-Agent 自动因子、15m 撮合
```

**关键判断（v0.2 修正表述）**：第 1 步是整个 Plan 的**价值兑现点**。团队现有的"bandit 未稳定超越 S0"结论是在**单种子、无隔离带、调参与评估共用数据**的低评估强度下得出的；重估的主力是**多种子方差 + 两级分诊 + 锁箱 holdout**（purge 本身预期只带来微小变化，见 §2.4）。重估后结论可能维持、也可能反转——重点不是它变不变，而是它第一次**可信**，无论用于"继续"还是"停"。

---

## 6. 与项目铁律的一致性核对

| 铁律 | 本 Plan 如何遵守 |
| --- | --- |
| 独立 ML 库、不碰 web/生产库 | 全部新增在 `mystock/ml/`；只读 `data/ml/mystock_ml.db`；报告仍进 `data/ml/reports/`（gitignore）|
| 永远保留 S0 + 三基线对照 | ②③ 只加评估维度，不动策略集；三基线原样保留 |
| 打不过就停、如实记录 | ② 的多种子门槛、③ 的诊断都是"如实暴露"工具；purged 后命中率若降，如实报告 |
| 红涨绿跌 | 报告新增块沿用 `report.py` 现有 `C_UP/C_DOWN` |
| 各股本币闭环、不换汇 | IC/CV 全在单标的内算，不跨标的、不归一 USD |
| 可单测的纯函数优先 | `cv.py / signal_eval.py / factors.py` 全为纯函数，配套 3 个新测试文件 |
| web 写入（P6）最后做 | 本 Plan 全程离线，不触发 P6 |

---

## 7. 明确不做的（避免 scope 蔓延）

| 不做 | 原因 |
| --- | --- |
| 引入 qlib 依赖 / bin 数据格式 | 三项借鉴全可自实现（各几十行）；引 qlib 违反独立库边界，成本远超收益 |
| 截面 IC / 跨标的因子归一 | 单标的截面退化；违反本币闭环锁定 |
| 因子缓存基础设施 | 当前每股千余行，算一遍毫秒级，YAGNI；量暴涨再做 |
| 把 IC 设成 go/no-go 硬门槛 | 单标的 IC 天然弱，定位诊断工具；go/no-go 仍看净值 vs 三基线 |
| 顺手换模型 / 上深度网络 | 评估报告已论证：换模型不是当前瓶颈；先修尺子 |
| sklearn 式 PurgedKFold（test 在中间）| 本项目金融时序用扩张窗滚动更合适；`cv.py` 预留字段便于将来扩展，但现在不实现 |

---

## 8. 交付物清单

**新增文件**
- `mystock/ml/cv.py` —— Purged/Embargo 滚动切分（纯函数）
- `mystock/ml/signal_eval.py` —— IC/RankIC/ICIR 信号评估（纯函数）
- `mystock/ml/factors.py` —— 可组合因子注册表
- `tests/test_ml_cv.py` · `tests/test_ml_signal_eval.py` · `tests/test_ml_factors.py`

**改动文件（小改、向后兼容）**
- `mystock/ml/predictor.py` —— `walk_forward_eval` 用 `cv` 切分 + 每折算 IC；`IntervalModel.fit` 校准集加隔离
- `mystock/ml/backtest.py` —— `run_backtest` 训练段加 purge + 收集信号 IC 进 result
- `mystock/ml/features.py` —— 内部改调 `factors.build_factor_matrix`（标签逻辑不动，输出零回归）
- `mystock/ml/report.py` —— `_stock_section` + 总览加"信号诊断 / 两级评估"块
- `mystock/ml/config.py` —— 新增 `feat_lookback / label_horizon / embargo / purged` 默认（或收进各自 dataclass config）

**验收全绿的标志**：`conda activate mk && python -m pytest tests/ -q` 通过数 ≥ 迁移前 + 新增测试数；`python -m mystock.ml.report` 生成的报告含"信号诊断"与"purged 前后命中率对照"两块，且六标的多种子 `std/mean ≤ 0.3`。

---

## 9. 决策记录与待确认

- **待确认 1**：IC 的"预测信号"用**区间中点收益 `mid_ret`** 还是新增**次日收盘方向**标签？建议先用 `mid_ret`（零新标签、复用现成预测），方向信号作为第二步增强。
- **待确认 2**：embargo 宽度取固定 `label_horizon=1`，还是 `max(1, ceil(0.01*n))`（学界惯例）？建议取两者较大，但本项目 `label_horizon` 小，实际差异可忽略——**先用 1，报告里标注**。
- **待确认 3**：① 的因子迁移是否一次性全迁？建议**一次性迁完 16 个并做逐列 allclose 回归**，避免新旧两套并存的维护负担。
- **待确认 4**：第 1 步重估若发现 bandit 在可信尺子下**更明显输给 S0**，是否直接按 `ML_PLAN §6` 门槛**停在 S0+预测层交付**？建议是——这正是修尺子的意义：让"停"这个决策有可信依据。
- **待确认 5（v0.2 新增）**：锁箱 holdout 的窗口长度与解锁纪律。建议最近 **6 个月**（对 ~494 测试日约占 1/4，牺牲可接受；更短则锁箱自身噪声太大）；纪律=全部定档后只评一次、评完即写报告、不回头改参再评。

> 一句话收尾：这三项借鉴的共同点是**都在加"诚实"，不在加"力量"**。它们让 myStock ML 现有的扎实工程第一次拥有可信的评估底座——在此之前的任何"提升"都读不准，在此之后团队的每一步（包括决定"停"）都站得住。
