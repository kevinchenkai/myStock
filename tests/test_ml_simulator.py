"""P1 撮合模拟器纯函数单测。"""
from mystock.ml.simulator import match_limit_order, Account, BUY, SELL


def _bar(o, h, l, c, ts="d"):
    return {"open": o, "high": h, "low": l, "close": c, "ts_et": ts}


def test_buy_fills_when_low_touches_limit():
    bars = [_bar(100, 101, 99.5, 100.5)]  # low 99.5 <= 99? no; <=100 yes
    f = match_limit_order(BUY, 100, bars)
    assert f.filled and f.fill_price == 100  # open 100 == limit → min=100


def test_buy_better_price_on_gap_down_open():
    bars = [_bar(98, 99, 97, 98.5)]  # 开盘 98 已低于挂价 100
    f = match_limit_order(BUY, 100, bars)
    assert f.filled and f.fill_price == 98  # min(100, 98)


def test_buy_not_filled_when_low_above_limit():
    bars = [_bar(105, 106, 104, 105.5)]
    f = match_limit_order(BUY, 100, bars)
    assert not f.filled


def test_sell_fills_when_high_touches_limit():
    bars = [_bar(100, 101, 99, 100.5)]
    f = match_limit_order(SELL, 101, bars)
    assert f.filled and f.fill_price == 101  # max(101, open 100)=101


def test_sell_better_price_on_gap_up_open():
    bars = [_bar(103, 104, 102, 103.5)]  # 开盘 103 已高于挂价 101
    f = match_limit_order(SELL, 101, bars)
    assert f.filled and f.fill_price == 103  # max(101, 103)


def test_intraday_order_first_touch_wins():
    bars = [_bar(100, 100.5, 99.8, 100.2),   # bar0: low 99.8 > 99 → no
            _bar(100.2, 100.3, 98.5, 99.0),  # bar1: low 98.5 <= 99 → fill here
            _bar(99, 99.5, 97, 98)]
    f = match_limit_order(BUY, 99, bars)
    assert f.filled and f.bar_index == 1


def test_no_bars_no_fill():
    assert not match_limit_order(BUY, 100, []).filled


def test_account_round_trip_net_value():
    acc = Account(cash=10000)
    acc.buy(price=100, qty=10)   # 花 1000，cash 9000，avg 100
    assert acc.qty == 10 and abs(acc.cash - 9000) < 1e-6
    acc.sell(price=120, qty=10)  # 卖 1200，回合净值 = (120-100)*10 = 200
    assert abs(acc.realized - 200) < 1e-6
    assert acc.qty == 0 and abs(acc.cash - 10200) < 1e-6


def test_account_avg_cost_moving():
    acc = Account(cash=10000)
    acc.buy(100, 10)
    acc.buy(120, 10)             # avg = (1000+1200)/20 = 110
    assert abs(acc.avg_cost - 110) < 1e-6
    acc.sell(130, 10)            # realized = (130-110)*10 = 200
    assert abs(acc.realized - 200) < 1e-6


def test_account_cannot_overspend():
    acc = Account(cash=500)
    acc.buy(100, 10)             # 需 1000 > 500 → 不成交
    assert acc.qty == 0 and acc.cash == 500
