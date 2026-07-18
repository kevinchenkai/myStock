"""ML 子包配置（独立于 mystock/config.py，避免耦合）。

只放训练/采集相关的常量与路径。生产库路径从 mystock.config 复用（只读）。
"""
from __future__ import annotations

from pathlib import Path

# 仓库根目录（mystock/ml/ 的上两级）
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# ---- 标的（3 美 + 3 港，单标的独立资金，各股本币不换汇）----
#   美股（USD）：NVDA / TSLA / PDD
#   港股（HKD）：腾讯 00700 / 阿里 09988 / 小米 01810
# 各股独立账户、各自本币闭环，回测/预测无需跨币种归一（docs/ML_PLAN.md §3.4）。
TARGETS: list[str] = [
    "US.NVDA", "US.TSLA", "US.PDD",
    "HK.00700", "HK.09988", "HK.01810",
]

# ---- 次日区间预测的分位（按股自适应，docs/ML_PLAN.md S1）----
# 区间 [L_hat,H_hat] 由 low/high 两个分位张成：分位越靠 0.5，区间越窄、但命中率越低
# （宽度与覆盖率直接对赌）。下表按各股实测「宽度 vs 命中率」甜区定档：
# 多数股 0.20/0.80（命中率守 ~50%、宽度较 0.10/0.90 降 ~25-30%）；
# 低波动的 PDD 可激进到 0.25/0.75（命中率仍 ~56%、宽度更小）。
# 未列入的标的回退 DEFAULT_ALPHA。改档前请用 walk-forward 实测验证命中率不崩。
DEFAULT_ALPHA = (0.20, 0.80)  # (low_alpha, high_alpha)
ALPHA_BY_CODE: dict[str, tuple[float, float]] = {
    "US.NVDA":  (0.20, 0.80),
    "US.TSLA":  (0.20, 0.80),
    "US.PDD":   (0.25, 0.75),
    "HK.00700": (0.20, 0.80),
    "HK.09988": (0.20, 0.80),
    "HK.01810": (0.20, 0.80),
}


def alpha_for(code: str) -> tuple[float, float]:
    """返回该标的的 (low_alpha, high_alpha)，未配置则回退默认。"""
    return ALPHA_BY_CODE.get(code, DEFAULT_ALPHA)


# ---- CQR 目标覆盖率（建议 2，按股自适应）----
# 区间宽度与覆盖率直接对赌：目标越高→CQR 扩展越多→区间越宽。
#   0.80：宽区间、命中 ~82%（宽度 7-13%）
#   0.70：中档、命中 ~72%（宽度比 0.80 降 ~20%）← 默认
#   0.60：窄区间、命中 ~62%（激进，易脱靶）
# 注：base 分位（ALPHA_BY_CODE）收窄不会让最终区间变窄——CQR 会自适应补偿到目标
# 覆盖率。要收窄区间只能调这里。低波动股可更激进（0.65），高波动股可放宽（0.75）。
# 改前用 walk-forward 实测验证命中率不崩。
DEFAULT_COVERAGE = 0.70
COVERAGE_BY_CODE: dict[str, float] = {}
def coverage_for(code: str) -> float:
    """返回该标的的 CQR 目标覆盖率，未配置则回退默认。"""
    return COVERAGE_BY_CODE.get(code, DEFAULT_COVERAGE)


# ---- 抓取窗口（yfinance 实测硬限制，见 docs/ML_PLAN.md §2.1）----
DAILY_PERIOD = "5y"      # 日线可取 5 年
HOURLY_PERIOD = "730d"   # 1h 上限约 2 年（730d 单次可取，无需分段）

# ---- 路径（全部在 data/ml/ 下，已被 .gitignore 的 data/ 规则覆盖）----
ML_DIR = ROOT_DIR / "data" / "ml"
ML_DB_PATH = ML_DIR / "mystock_ml.db"
REPORTS_DIR = ML_DIR / "reports"

# 生产库（只读快照来源）
PROD_DB_PATH = ROOT_DIR / "data" / "mystock.db"

# ML 库 schema
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
