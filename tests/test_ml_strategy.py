"""挂单回溯（strategy.py）单测：手数分档、成交判定、盈亏口径、边界。

撮合本身由 test_ml_simulator 覆盖，这里只测策略层的组装逻辑。
"""
import pytest

from mystock.ml import strategy as st


# ---- 手数按市场分档 ----
def test_lot_us_is_10():
    assert st.lot_for("US.NVDA") == 10
    assert st.lot_for("US.TSLA") == 10


def test_lot_hk_is_100():
    assert st.lot_for("HK.00700") == 100
    assert st.lot_for("HK.09988") == 100


def test_lot_unknown_market_falls_back():
    assert st.lot_for("SG.D05") == st.DEFAULT_LOT


# ---- 盈亏口径的纯算术（不碰库）----
def test_next_day_map_skips_to_following_row():
    m = st._next_day_map(["2026-07-23", "2026-07-24", "2026-07-27"])
    assert m["2026-07-24"] == "2026-07-27"   # 周五→周一，按交易日历
    assert "2026-07-27" not in m             # 末日无次日


def test_f_cleans_nan_and_none():
    assert st._f(None) is None
    assert st._f(float("nan")) is None
    assert st._f("abc") is None
    assert st._f("12.5") == pytest.approx(12.5)


def test_r_rounds_and_passes_none():
    assert st._r(1.23456) == 1.23
    assert st._r(None) is None


def test_empty_result_shape():
    """无数据时也要返回完整结构，前端才不用做特判。"""
    r = st._empty("US.NVDA", 30)
    assert r["n_days"] == 0
    assert r["rows"] == []
    assert r["lot"] == 10
    assert r["currency"] == "USD"
    s = r["summary"]
    for k in ("n_buy", "n_sell", "n_both", "n_none", "cash_flow",
              "end_pos", "total_pnl", "min_pos", "max_pos"):
        assert k in s
    assert s["total_pnl"] == 0.0


def test_empty_result_hk_currency_and_lot():
    r = st._empty("HK.00700", 30)
    assert r["currency"] == "HKD"
    assert r["lot"] == 100
