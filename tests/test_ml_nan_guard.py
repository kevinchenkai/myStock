"""脏数据（NaN OHLC）防护回归测试。

复现并锁定 2026-06-28 周末事故：yfinance 偶发返回美股某日 NaN 行 → 写进库 →
回测 mark 为 NaN → 净值曲线被污染 → 总览表显示 nan。三层防护各自单测：
  1. 采集层 fetch._ohlc_ok：丢弃 NaN/不完整/非正 行
  2. 回测层 run_backtest：NaN mark 跳过该日，期末净值仍有限
  3. 报告层 report._fmt_eq：None/NaN 显示「—」而非裸 nan
"""
import math

import pytest

pytest.importorskip("sklearn")

from mystock.ml.fetch import _ohlc_ok
from mystock.ml.report import _fmt_eq


# ---- 层 1：采集层丢脏行 ----
def test_ohlc_ok_accepts_clean_row():
    assert _ohlc_ok({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5})


@pytest.mark.parametrize("bad", [
    {"open": 100.0, "high": 101.0, "low": 99.0, "close": None},          # None
    {"open": 100.0, "high": 101.0, "low": 99.0, "close": float("nan")},  # NaN
    {"open": 0.0, "high": 101.0, "low": 99.0, "close": 100.5},           # 非正
    {"open": 100.0, "high": 101.0, "low": 99.0},                          # 缺键
])
def test_ohlc_ok_rejects_bad_row(bad):
    assert not _ohlc_ok(bad)


# ---- 层 3：报告层 NaN → 「—」 ----
def test_fmt_eq_handles_nan_and_none():
    assert _fmt_eq(float("nan")) == "—"
    assert _fmt_eq(None) == "—"
    assert _fmt_eq(21815.85) == "21,816"


# ---- 层 2：回测层 NaN mark 不污染净值曲线 ----
def _has_db():
    from mystock.ml import config as mlcfg
    return mlcfg.ML_DB_PATH.exists()


@pytest.mark.skipif(not _has_db(), reason="需要 data/ml/mystock_ml.db")
def test_backtest_survives_nan_close(monkeypatch):
    """在加载后的日线里注入一根 NaN close，回测期末净值仍须有限（不为 nan）。"""
    from mystock.ml import backtest as bt
    from mystock.ml import data as mldata

    real_load = mldata.load_daily

    def poisoned_load(code, db_path=None):
        df = real_load(code, db_path).copy()
        # 把测试区间内某一行的 close 置为 NaN（模拟 yfinance 脏行漏进库）
        if len(df) > 50:
            df.loc[df.index[-5], "close"] = float("nan")
        return df

    monkeypatch.setattr(bt.mldata, "load_daily", poisoned_load)
    r = bt.run_backtest("US.NVDA")
    fe = r["final_equity"]
    for k in ("bandit", "rule", "human", "buy_hold"):
        v = fe[k]
        assert v is not None and math.isfinite(v), f"{k} 期末净值被 NaN 污染: {v}"
