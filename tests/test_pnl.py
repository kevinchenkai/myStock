"""交易盈亏（已实现，移动平均成本法）单元测试。"""
from mystock.pnl import compute_pnl


def _deal(code, side, price, qty, t, market="US", name="X"):
    return {
        "code": code, "trd_side": side, "price": price, "qty": qty,
        "create_time": t, "market": market, "name": name,
    }


def test_simple_buy_then_sell():
    deals = [
        _deal("US.AAPL", "BUY", 100, 10, "2025-01-01 10:00:00"),
        _deal("US.AAPL", "SELL", 120, 10, "2025-01-02 10:00:00"),
    ]
    r = compute_pnl(deals)[0]
    assert r["realized_pnl"] == 200          # (120-100)*10
    assert r["net_qty"] == 0
    assert r["uncovered_sell_qty"] == 0
    assert r["currency"] == "USD"


def test_moving_average_cost():
    # 两笔买入：10@100、10@200 → 平均 150；卖 10@180 → (180-150)*10=300
    deals = [
        _deal("US.X", "BUY", 100, 10, "2025-01-01 10:00:00"),
        _deal("US.X", "BUY", 200, 10, "2025-01-01 11:00:00"),
        _deal("US.X", "SELL", 180, 10, "2025-01-02 10:00:00"),
    ]
    r = compute_pnl(deals)[0]
    assert r["realized_pnl"] == 300
    assert r["avg_buy_price"] == 150
    assert r["net_qty"] == 10


def test_partial_sell_keeps_remaining_cost():
    # 买 10@100，卖 4@130 → (130-100)*4=120；剩 6 股
    deals = [
        _deal("US.X", "BUY", 100, 10, "2025-01-01 10:00:00"),
        _deal("US.X", "SELL", 130, 4, "2025-01-02 10:00:00"),
    ]
    r = compute_pnl(deals)[0]
    assert r["realized_pnl"] == 120
    assert r["net_qty"] == 6


def test_oversell_uses_cost_fallback():
    # 窗口内只有卖（更早建仓不在库）；用持仓成本 90 兜底
    deals = [_deal("US.X", "SELL", 120, 10, "2025-01-02 10:00:00")]
    r = compute_pnl(deals, cost_fallback={"US.X": 90})[0]
    assert r["realized_pnl"] == 300          # (120-90)*10
    assert r["uncovered_sell_qty"] == 0


def test_oversell_negative_fallback_is_uncovered():
    # 富途超卖标的成本为负 → 视为不可用，卖量记入 uncovered，不计盈亏
    deals = [_deal("US.X", "SELL", 120, 10, "2025-01-02 10:00:00")]
    r = compute_pnl(deals, cost_fallback={"US.X": -50})[0]
    assert r["realized_pnl"] == 0
    assert r["uncovered_sell_qty"] == 10


def test_oversell_no_fallback_is_uncovered():
    deals = [_deal("US.X", "SELL", 120, 10, "2025-01-02 10:00:00")]
    r = compute_pnl(deals)[0]
    assert r["realized_pnl"] == 0
    assert r["uncovered_sell_qty"] == 10


def test_partial_window_buy_then_oversell():
    # 买 5@100，卖 8@120：5 股按均价、3 股按兜底成本 90
    deals = [
        _deal("US.X", "BUY", 100, 5, "2025-01-01 10:00:00"),
        _deal("US.X", "SELL", 120, 8, "2025-01-02 10:00:00"),
    ]
    r = compute_pnl(deals, cost_fallback={"US.X": 90})[0]
    # (120-100)*5 + (120-90)*3 = 100 + 90 = 190
    assert r["realized_pnl"] == 190
    assert r["uncovered_sell_qty"] == 0


def test_unsorted_input_sorted_by_time():
    # 乱序输入应按时间处理（先买后卖）
    deals = [
        _deal("US.X", "SELL", 120, 10, "2025-01-02 10:00:00"),
        _deal("US.X", "BUY", 100, 10, "2025-01-01 10:00:00"),
    ]
    r = compute_pnl(deals)[0]
    assert r["realized_pnl"] == 200
    assert r["uncovered_sell_qty"] == 0


def test_hk_currency_from_market():
    deals = [_deal("HK.00700", "BUY", 300, 100, "2025-01-01 10:00:00", market="HK")]
    r = compute_pnl(deals)[0]
    assert r["currency"] == "HKD"


def test_sorted_desc_by_realized():
    deals = [
        _deal("US.LOW", "BUY", 100, 10, "2025-01-01 10:00:00"),
        _deal("US.LOW", "SELL", 110, 10, "2025-01-02 10:00:00"),   # +100
        _deal("US.HIGH", "BUY", 100, 10, "2025-01-01 10:00:00"),
        _deal("US.HIGH", "SELL", 150, 10, "2025-01-02 10:00:00"),  # +500
    ]
    rows = compute_pnl(deals)
    assert rows[0]["code"] == "US.HIGH"
    assert rows[1]["code"] == "US.LOW"


# ---------- analyze_stock（单股交易复盘，FIFO 配对）----------
from mystock.pnl import analyze_stock


def test_analyze_basic_round_trip():
    deals = [
        _deal("US.X", "BUY", 100, 10, "2025-01-01 10:00:00"),
        _deal("US.X", "SELL", 130, 10, "2025-01-11 10:00:00"),
    ]
    a = analyze_stock(deals)
    assert a["summary"]["realized_pnl"] == 300
    assert a["stats"]["closed_trips"] == 1
    assert a["stats"]["win_count"] == 1
    assert a["stats"]["win_rate"] == 100
    rt = a["round_trips"][0]
    assert rt["pnl"] == 300
    assert abs(rt["hold_days"] - 10) < 1e-6


def test_analyze_fifo_two_lots():
    # 两批买入(FIFO)：10@100、10@120；卖 15@130
    #  → 配 10@100=(130-100)*10=300，再 5@120=(130-120)*5=50；共 350
    deals = [
        _deal("US.X", "BUY", 100, 10, "2025-01-01 10:00:00"),
        _deal("US.X", "BUY", 120, 10, "2025-01-02 10:00:00"),
        _deal("US.X", "SELL", 130, 15, "2025-01-05 10:00:00"),
    ]
    a = analyze_stock(deals)
    assert round(a["summary"]["realized_pnl"]) == 350
    assert a["stats"]["closed_trips"] == 2
    assert a["summary"]["net_qty"] == 5


def test_analyze_win_rate_and_profit_factor():
    deals = [
        _deal("US.X", "BUY", 100, 10, "2025-01-01 10:00:00"),
        _deal("US.X", "SELL", 150, 10, "2025-01-02 10:00:00"),   # +500 win
        _deal("US.X", "BUY", 100, 10, "2025-01-03 10:00:00"),
        _deal("US.X", "SELL", 90, 10, "2025-01-04 10:00:00"),    # -100 loss
    ]
    a = analyze_stock(deals)
    st = a["stats"]
    assert st["win_count"] == 1 and st["loss_count"] == 1
    assert st["win_rate"] == 50
    assert st["profit_factor"] == 5.0   # 500 / 100


def test_analyze_uncovered_sell_no_fallback():
    deals = [_deal("US.X", "SELL", 120, 10, "2025-01-02 10:00:00")]
    a = analyze_stock(deals)
    assert a["summary"]["uncovered_sell_qty"] == 10
    assert a["summary"]["realized_pnl"] == 0
    assert a["stats"]["closed_trips"] == 0


def test_analyze_uncovered_sell_with_fallback():
    deals = [_deal("US.X", "SELL", 120, 10, "2025-01-02 10:00:00")]
    a = analyze_stock(deals, cost_fallback=90)
    assert a["summary"]["realized_pnl"] == 300
    assert a["round_trips"][0].get("fallback") is True


def test_analyze_observations_present():
    deals = [
        _deal("US.X", "BUY", 100, 10, "2025-01-01 10:00:00"),
        _deal("US.X", "SELL", 150, 10, "2025-01-12 10:00:00"),
    ]
    a = analyze_stock(deals)
    assert len(a["observations"]) >= 1
    assert all("level" in o and "text" in o for o in a["observations"])


def test_analyze_empty():
    a = analyze_stock([])
    assert a["stats"]["closed_trips"] == 0
    assert a["observations"][0]["level"] == "info"


# ---------- yearly_finance（年度现金流：收-付，按市场分组）----------
from mystock.pnl import yearly_finance


def test_finance_net_cashflow_by_market():
    deals = [
        _deal("US.X", "BUY", 100, 10, "2026-01-01 10:00:00"),   # 付 1000
        _deal("US.X", "SELL", 130, 10, "2026-02-01 10:00:00"),  # 收 1300
        _deal("HK.700", "BUY", 300, 100, "2026-03-01 10:00:00", market="HK"),  # 付 30000
    ]
    res = yearly_finance(deals, "2026")
    us = next(m for m in res["markets"] if m["market"] == "US")
    hk = next(m for m in res["markets"] if m["market"] == "HK")
    assert us["net_cashflow"] == 300        # 1300 - 1000
    assert us["currency"] == "USD"
    assert us["sell_count"] == 1 and us["buy_count"] == 1
    assert hk["net_cashflow"] == -30000     # 只买未卖 → 负（建仓支出）
    assert hk["currency"] == "HKD"


def test_finance_filters_by_year():
    deals = [
        _deal("US.X", "SELL", 130, 10, "2025-12-31 10:00:00"),  # 不在 2026
        _deal("US.X", "SELL", 120, 5, "2026-01-01 10:00:00"),   # 在 2026
    ]
    res = yearly_finance(deals, "2026")
    us = next(m for m in res["markets"] if m["market"] == "US")
    assert us["sell_amount"] == 600         # 仅 2026 的 120*5
    assert us["sell_count"] == 1


def test_finance_available_years_desc():
    deals = [
        _deal("US.X", "BUY", 100, 1, "2024-05-01 10:00:00"),
        _deal("US.X", "BUY", 100, 1, "2026-05-01 10:00:00"),
        _deal("US.X", "BUY", 100, 1, "2025-05-01 10:00:00"),
    ]
    res = yearly_finance(deals, "2026")
    assert res["available_years"] == ["2026", "2025", "2024"]


def test_finance_empty_year_has_no_markets():
    deals = [_deal("US.X", "BUY", 100, 1, "2025-05-01 10:00:00")]
    res = yearly_finance(deals, "2026")
    assert res["markets"] == []
    assert res["year"] == "2026"
