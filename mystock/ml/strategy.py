"""按预测区间挂单的回溯计算（纯函数，可单测）。

策略：基准日 T 收盘后拿到预测 [L_hat, H_hat] → 次一交易日 T+1 同时挂
限价买 L_hat、限价卖 H_hat，各一手。假设现金与持仓充足（不受资金/持仓约束）。

撮合复用 simulator.match_limit_order（1h K 线），能判断盘中先触低还是先触高——
拿日线 low/high 直接比会把"先冲高后砸低"与反过来混为一谈，成交价也不对。

手数按市场分档（LOT_BY_MARKET）：美股 10 股、港股 100 股（港股按板块最小单位）。

盈亏口径：
  cash_flow = 卖出额 − 买入额（已实现现金流）
  mark      = 期末净持仓 × 最后收盘（净持仓可为负=裸空，折算为负）
  total     = cash_flow + mark
未扣佣金/印花税/平台费/融券成本/滑点——如实标注，勿当净收益。
"""
from __future__ import annotations

from typing import Optional

from . import data as mldata
from . import db as mldb
from .simulator import BUY, SELL, match_limit_order

# 每次操作的股数（按市场）。港股 100 股 = 常见板块最小交易单位。
LOT_BY_MARKET = {"US": 10, "HK": 100}
DEFAULT_LOT = 10


def lot_for(code: str) -> int:
    """该标的每次操作股数。code 为富途代码（US.NVDA / HK.00700）。"""
    return LOT_BY_MARKET.get(code.split(".")[0], DEFAULT_LOT)


def _next_day_map(dates: list[str]) -> dict[str, str]:
    return {d: dates[i + 1] for i, d in enumerate(dates[:-1])}


def run_strategy(code: str, days: int = 30, conn=None, db_path=None) -> dict:
    """回溯该标的最近 days 个交易日的挂单结果。

    返回 dict：逐日明细 rows + 汇总。数据不足时 rows 为空、summary 计 0。
    conn 可复用（Web 场景避免反复开库）；不传则自建并在结束时关闭。
    """
    own_conn = conn is None
    conn = conn or mldb.get_ml_connection(db_path)
    try:
        daily = mldata.load_daily(code, db_path)
        if daily.empty:
            return _empty(code, days)
        bars_by_day = mldata.intraday_bars_by_day(code, db_path)
        dates = [str(d) for d in daily["date"]]
        nxt = _next_day_map(dates)
        dmap = {str(r["date"]): r for _, r in daily.iterrows()}
        preds = {p["as_of"]: p for p in mldb.load_predictions(conn, code)}

        # 只取「次日已走出 + 有 1h bars」的基准日；取最近 days 个
        usable = [a for a in sorted(preds)
                  if a in nxt and bars_by_day.get(nxt[a])]
        usable = usable[-days:]
        if not usable:
            return _empty(code, days)

        lot = lot_for(code)
        pos = 0
        cash = 0.0
        buy_amt = sell_amt = 0.0
        n_buy = n_sell = n_both = n_none = 0
        rows = []
        for as_of in usable:
            p = preds[as_of]
            L, H = p["l_hat"], p["h_hat"]
            day = nxt[as_of]
            bars = bars_by_day[day]
            fb = match_limit_order(BUY, L, bars) if L is not None else None
            fs = match_limit_order(SELL, H, bars) if H is not None else None
            b_filled = bool(fb and fb.filled)
            s_filled = bool(fs and fs.filled)
            if b_filled:
                pos += lot
                cash -= fb.fill_price * lot
                buy_amt += fb.fill_price * lot
                n_buy += 1
            if s_filled:
                pos -= lot
                cash += fs.fill_price * lot
                sell_amt += fs.fill_price * lot
                n_sell += 1
            n_both += b_filled and s_filled
            n_none += not b_filled and not s_filled
            bar = dmap[day]
            close = _f(bar["close"])
            rows.append({
                "as_of": as_of, "date": day,
                "l_hat": _r(L), "h_hat": _r(H),
                "low": _r(_f(bar["low"])), "high": _r(_f(bar["high"])),
                "close": _r(close),
                "buy_price": _r(fb.fill_price) if b_filled else None,
                "sell_price": _r(fs.fill_price) if s_filled else None,
                "pos": pos,
                # 逐日权益（现金流 + 当日持仓折算），用于画曲线
                "equity": _r(cash + pos * close) if close is not None else None,
            })

        last_close = _f(dmap[rows[-1]["date"]]["close"]) or 0.0
        mark = pos * last_close
        return {
            "code": code,
            "currency": "USD" if code.startswith("US.") else "HKD",
            "lot": lot,
            "n_days": len(rows),
            "start": rows[0]["date"], "end": rows[-1]["date"],
            "rows": rows,
            "summary": {
                "n_buy": n_buy, "n_sell": n_sell,
                "n_both": n_both, "n_none": n_none,
                "buy_amount": _r(buy_amt), "sell_amount": _r(sell_amt),
                "cash_flow": _r(cash),
                "end_pos": pos, "last_close": _r(last_close),
                "mark_value": _r(mark),
                "total_pnl": _r(cash + mark),
                "min_pos": min(r["pos"] for r in rows),
                "max_pos": max(r["pos"] for r in rows),
            },
        }
    finally:
        if own_conn:
            conn.close()


def _empty(code: str, days: int) -> dict:
    return {
        "code": code, "currency": "USD" if code.startswith("US.") else "HKD",
        "lot": lot_for(code), "n_days": 0, "start": None, "end": None,
        "rows": [], "summary": {
            "n_buy": 0, "n_sell": 0, "n_both": 0, "n_none": 0,
            "buy_amount": 0.0, "sell_amount": 0.0, "cash_flow": 0.0,
            "end_pos": 0, "last_close": None, "mark_value": 0.0,
            "total_pnl": 0.0, "min_pos": 0, "max_pos": 0,
        },
    }


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _r(v, nd: int = 2):
    return None if v is None else round(float(v), nd)


def run_many(codes: list[str], days: int = 30, db_path=None) -> list[dict]:
    """批量回溯（复用同一连接）。"""
    conn = mldb.get_ml_connection(db_path)
    try:
        return [run_strategy(c, days, conn=conn, db_path=db_path) for c in codes]
    finally:
        conn.close()
