"""ML 子包配置（独立于 mystock/config.py，避免耦合）。

只放训练/采集相关的常量与路径。生产库路径从 mystock.config 复用（只读）。
"""
from __future__ import annotations

from pathlib import Path

# 仓库根目录（mystock/ml/ 的上两级）
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# ---- 标的（第一版：3 支最活跃美股，单标的独立资金）----
TARGETS: list[str] = ["US.NVDA", "US.TSLA", "US.PDD"]

# ---- 抓取窗口（yfinance 实测硬限制，见 docs/ML_PLAN.md §2.1）----
DAILY_PERIOD = "5y"      # 日线可取 5 年
HOURLY_PERIOD = "730d"   # 1h 可取约 2 年；分段合并以绕过窗口限制

# ---- 路径（全部在 data/ml/ 下，已被 .gitignore 的 data/ 规则覆盖）----
ML_DIR = ROOT_DIR / "data" / "ml"
ML_DB_PATH = ML_DIR / "mystock_ml.db"
REPORTS_DIR = ML_DIR / "reports"

# 生产库（只读快照来源）
PROD_DB_PATH = ROOT_DIR / "data" / "mystock.db"

# ML 库 schema
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
