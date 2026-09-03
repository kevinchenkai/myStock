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


# ---- 收益率口径（compute_returns，纯算术）----
def test_turnover_return_uses_average_one_way_amount():
    """分母是 (买+卖)/2 —— 买卖各算一次会把同一笔来回重复计数。"""
    r = st.compute_returns(
        {"total_pnl": 10.0, "buy_amount": 100.0, "sell_amount": 100.0}, 0.0, 10)
    assert r["turnover"] == 100.0            # 不是 200
    assert r["turnover_return"] == pytest.approx(0.1)


def test_capital_return_divides_by_peak_exposure():
    r = st.compute_returns(
        {"total_pnl": 50.0, "buy_amount": 400.0, "sell_amount": 400.0}, 500.0, 10)
    assert r["capital_return"] == pytest.approx(0.1)
    assert r["peak_exposure"] == 500.0


def test_annualized_is_linear_not_compounded():
    """线性年化：样本太短，复利会把噪声放大成荒谬的数字。"""
    r = st.compute_returns(
        {"total_pnl": 50.0, "buy_amount": 400.0, "sell_amount": 400.0}, 500.0, 25)
    # 0.1 * 252 / 25 = 1.008，而非 (1.1)**(252/25)-1 ≈ 1.6
    assert r["annualized_return"] == pytest.approx(1.008)


def test_zero_denominator_returns_none_not_zero():
    """「没有本金可言」≠「本金收益为零」——前端要能区分显示 — 而非 0.00%。"""
    r = st.compute_returns({"total_pnl": 0.0, "buy_amount": 0.0,
                            "sell_amount": 0.0}, 0.0, 30)
    assert r["turnover_return"] is None
    assert r["capital_return"] is None
    assert r["annualized_return"] is None


def test_capital_return_none_when_never_held_position():
    """成交了但净持仓始终为 0（买卖同日抵消）→ 从未占款。"""
    r = st.compute_returns(
        {"total_pnl": 5.0, "buy_amount": 100.0, "sell_amount": 105.0}, 0.0, 10)
    assert r["turnover_return"] is not None   # 有成交额
    assert r["capital_return"] is None        # 但无占款
    assert r["annualized_return"] is None


def test_negative_pnl_gives_negative_returns():
    r = st.compute_returns(
        {"total_pnl": -30.0, "buy_amount": 300.0, "sell_amount": 300.0}, 200.0, 10)
    assert r["turnover_return"] == pytest.approx(-0.1)
    assert r["capital_return"] == pytest.approx(-0.15)


def test_missing_summary_keys_default_to_zero():
    """_empty() 传空 dict 进来也不能炸。"""
    r = st.compute_returns({}, 0.0, 0)
    assert r["turnover"] == 0.0
    assert r["turnover_return"] is None


def test_empty_result_carries_returns_block():
    """无数据时也要有 returns，前端不做特判。"""
    q = st._empty("US.NVDA", 30)["summary"]["returns"]
    assert q["capital_return"] is None
    assert q["trading_days_per_year"] == 252


# ---- 组合汇总（aggregate_returns）----
def _mk(code, cur, pnl, turnover, peak, n_days=30):
    return {"code": code, "currency": cur, "n_days": n_days,
            "summary": {"total_pnl": pnl,
                        "returns": {"turnover": turnover, "peak_exposure": peak}}}


def test_aggregate_groups_by_currency_never_sums_across():
    """USD 与 HKD 是两种钱，混加无意义——必须分成两组。"""
    out = st.aggregate_returns([
        _mk("US.NVDA", "USD", 100.0, 1000.0, 500.0),
        _mk("HK.00700", "HKD", 200.0, 2000.0, 1000.0),
    ])
    assert [g["currency"] for g in out] == ["HKD", "USD"]   # 按币种排序
    assert [g["total_pnl"] for g in out] == [200.0, 100.0]


def test_aggregate_sums_within_same_currency():
    out = st.aggregate_returns([
        _mk("US.NVDA", "USD", 100.0, 1000.0, 400.0),
        _mk("US.TSLA", "USD", 50.0, 500.0, 100.0),
    ])
    assert len(out) == 1
    g = out[0]
    assert g["total_pnl"] == 150.0
    assert g["returns"]["turnover"] == 1500.0
    assert g["returns"]["peak_exposure"] == 500.0
    # 组合成交额收益率 = 150 / 1500，而非各票收益率的平均
    assert g["returns"]["turnover_return"] == pytest.approx(0.1)
    assert g["returns"]["capital_return"] == pytest.approx(0.3)


def test_aggregate_skips_empty_results():
    out = st.aggregate_returns([
        _mk("US.NVDA", "USD", 100.0, 1000.0, 500.0),
        st._empty("US.TSLA", 30),
    ])
    assert len(out) == 1
    assert out[0]["codes"] == ["US.NVDA"]


def test_aggregate_of_nothing_is_empty_list():
    assert st.aggregate_returns([]) == []
    assert st.aggregate_returns([st._empty("US.NVDA", 30)]) == []


def test_aggregate_uses_longest_window_for_annualization():
    """各票窗口长度可能不一，年化取最长的那个（更保守）。"""
    out = st.aggregate_returns([
        _mk("US.NVDA", "USD", 100.0, 1000.0, 500.0, n_days=20),
        _mk("US.TSLA", "USD", 0.0, 500.0, 500.0, n_days=30),
    ])
    assert out[0]["n_days"] == 30
