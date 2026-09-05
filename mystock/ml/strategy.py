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

收益率口径（compute_returns）：
  这个策略**没有天然的本金**——不预设初始现金，净持仓还会单向漂移（可为负=裸空），
  所以「收益率」必须先声明分母，否则是个无意义的数。这里给三个各自回答不同问题的分母：

    turnover_return = total / ((buy_amount + sell_amount) / 2)
        —— 单位成交额的赚钱效率。回答「每做 1 块钱生意赚几分」，与仓位规模无关，
           跨标的可比性最好，也最接近"手续费能不能覆盖"这个实际问题。
    capital_return  = total / peak_exposure
        —— peak_exposure = max(|净持仓| × 当日收盘)，即整个窗口里被占用的最大资金
           （多头=买入占款，空头=保证金/融券市值）。回答「压上的钱回报几何」，
           是最接近直觉「本金收益率」的一个，但对单日极端仓位敏感。
    cash_return     = total / max_cash_used
        —— max_cash_used = 逐日累计现金余额跌到最深的那一点（取绝对值）。
           前两个分母都是「估算占款」，这个是**真金白银**：按实际成交价累计
           买卖现金流，余额最低点就是你账上必须准备好的现金下限。
           与 peak_exposure 的差别有二：① 用成交价而非当日收盘（现金是按成交
           价划走的，不逐日盯市）；② 只算现金方向，裸空产生的是现金流入
           （余额上升）而非占款 —— 所以纯裸空策略这个分母可能为 0，
           表示「一分钱现金都没垫付」，此时收益率返回 None 而非无穷大。
    annualized      = capital_return 按 252 交易日线性年化
        —— 年化仍挂在 capital_return 上（口径不变，避免同一个数字因新增指标而漂移）；
           想按现金口径年化，用 cash_return × 252/天数 自行折算即可。
        —— 仅供横向比较量级；样本仅 days 天，不可当预期收益。

  三者都不含融资成本与费用；capital_return 的分母是**峰值**占款而非平均占款，
  故偏保守（真实占款更少 → 真实收益率更高）。分母为 0（无任何成交）时返回 None。
"""
from __future__ import annotations

from typing import Optional

from . import data as mldata
from . import db as mldb
from . import sessions
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
    conn = conn or mldb.get_ml_connection_readonly(db_path)
    try:
        daily = mldata.load_daily(code, db_path)
        if daily.empty:
            return _empty(code, days)
        bars_by_day = mldata.intraday_bars_by_day(code, db_path)
        dates = [str(d) for d in daily["date"]]
        nxt = {d: sessions.next_session(code, d) for d in dates}
        dmap = {str(r["date"]): r for _, r in daily.iterrows()}
        preds = {p["as_of"]: p for p in mldb.load_predictions(conn, code)}

        targets = sessions.window(code, dates[-1], days)
        previous = {sessions.next_session(code,d):d for d in sessions.session_days(code,sessions.START,dates[-1])}
        usable = [previous[d] for d in targets]

        lot = lot_for(code)
        pos = 0
        cash = 0.0
        peak_exposure = 0.0
        min_cash = 0.0          # 累计现金余额的最低点（<=0），绝对值即实际垫付现金
        buy_amt = sell_amt = 0.0
        n_buy = n_sell = n_both = n_none = 0
        rows = []
        for as_of in usable:
            day = sessions.next_session(code, as_of)
            status = ('missing_daily' if day not in dmap else
                      'missing_prediction' if as_of not in preds else
                      'missing_bars' if not bars_by_day.get(day) else 'ok')
            if status != 'ok':
                close = _f(dmap.get(day, {}).get('close'))
                rows.append(dict(as_of=as_of, date=day, status=status, pos=pos, cash=cash,
                                 equity=cash+pos*close if close else None, close=close,
                                 l_hat=None,h_hat=None,low=None,high=None,buy_price=None,sell_price=None))
                continue
            p = preds[as_of]
            L, H = p["l_hat"], p["h_hat"]
            day = sessions.next_session(code, as_of)
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
            # 现金余额最低点：当天买卖都记完再看——同日双边成交时，先扣买再加卖
            # 与先加卖再扣买的中间态不同，但收盘时的余额是唯一确定的，取它。
            min_cash = min(min_cash, cash)
            # 峰值占款：|净持仓| 按当日收盘折算——多头是买入占款，空头是融券市值/保证金。
            # 用当日收盘（而非成交价）是因为占款是逐日盯市的，不是建仓那一刻定死的。
            if close is not None:
                peak_exposure = max(peak_exposure, abs(pos) * close)
            rows.append({
                "as_of": as_of, "date": day, "status": "ok",
                "l_hat": _r(L), "h_hat": _r(H),
                "low": _r(_f(bar["low"])), "high": _r(_f(bar["high"])),
                "close": _r(close),
                "buy_price": _r(fb.fill_price) if b_filled else None,
                "sell_price": _r(fs.fill_price) if s_filled else None,
                "pos": pos,
                "cash": _r(cash),
                # 逐日权益（现金流 + 当日持仓折算），用于画曲线
                "equity": _r(cash + pos * close) if close is not None else None,
            })

        last_close = next((_f(r["close"]) for r in reversed(rows) if _f(r["close"]) is not None), 0.0)
        mark = pos * last_close
        summary = {
            "n_buy": n_buy, "n_sell": n_sell,
            "n_both": n_both, "n_none": n_none,
            "buy_amount": _r(buy_amt), "sell_amount": _r(sell_amt),
            "cash_flow": _r(cash),
            "end_pos": pos, "last_close": _r(last_close),
            "mark_value": _r(mark),
            "total_pnl": _r(cash + mark),
            "min_pos": min(r["pos"] for r in rows),
            "max_pos": max(r["pos"] for r in rows),
        }
        # min_cash <= 0，取负得垫付额；max(0.0, ...) 抹掉 -0.0（否则渲染成 "-0.00"）
        cash_used = max(0.0, -min_cash)
        summary["max_cash_used"] = _r(cash_used)
        summary["returns"] = compute_returns(
            summary, peak_exposure, len(rows), cash_used)
        return {
            "code": code,
            "currency": "USD" if code.startswith("US.") else "HKD",
            "lot": lot,
            "n_days": len(rows),
            "start": rows[0]["date"], "end": rows[-1]["date"],
            "rows": rows,
            "summary": summary,
        }
    finally:
        if own_conn:
            conn.close()


TRADING_DAYS_PER_YEAR = 252


def compute_returns(summary: dict, peak_exposure: float, n_days: int,
                    max_cash_used: float = 0.0) -> dict:
    """把绝对盈亏折成收益率。纯算术，不碰库，便于单测。

    分母的选择见模块 docstring。三个分母都可能为 0（窗口内一次没成交、
    成交后净持仓始终为 0 → 从未占款、或纯裸空 → 现金只进不出从未垫付），
    此时对应收益率返回 None，而不是 0.0 —— 「没有本金可言」和「本金收益
    为零」是两回事，前端要能区分着显示（显示 — 而非 0.00%）。

    max_cash_used 由 run_strategy 逐日累计后传入（需要按顺序的成交价，
    这里拿不到），语义是"累计现金余额最低点的绝对值"，已保证 >= 0。
    """
    total = summary.get("total_pnl") or 0.0
    buy_amt = summary.get("buy_amount") or 0.0
    sell_amt = summary.get("sell_amount") or 0.0

    # 平均单边成交额：买卖各算一次会把同一笔来回重复计数，故取均值
    turnover = (buy_amt + sell_amt) / 2.0
    turnover_return = (total / turnover) if turnover > 0 else None
    capital_return = (total / peak_exposure) if peak_exposure > 0 else None
    # 实际动用现金：真金白银的口径。为 0 表示全程没垫过钱（纯裸空/无成交），
    # 此时"收益率"无从谈起 → None，绝不能当成 0% 或除出无穷大。
    cash_return = (total / max_cash_used) if max_cash_used > 0 else None

    # 线性年化（非复利）：样本太短，复利年化会把噪声放大成荒谬的数字
    annualized = None
    if capital_return is not None and n_days > 0:
        annualized = capital_return * TRADING_DAYS_PER_YEAR / n_days

    return {
        "turnover": _r(turnover),
        "peak_exposure": _r(peak_exposure),
        "max_cash_used": _r(max_cash_used),
        "turnover_return": _r(turnover_return, 6),
        "capital_return": _r(capital_return, 6),
        "cash_return": _r(cash_return, 6),
        "annualized_return": _r(annualized, 6),
        "trading_days_per_year": TRADING_DAYS_PER_YEAR,
    }


def _empty(code: str, days: int) -> dict:
    return {
        "code": code, "currency": "USD" if code.startswith("US.") else "HKD",
        "lot": lot_for(code), "n_days": 0, "start": None, "end": None,
        "rows": [], "summary": {
            "n_buy": 0, "n_sell": 0, "n_both": 0, "n_none": 0,
            "buy_amount": 0.0, "sell_amount": 0.0, "cash_flow": 0.0,
            "end_pos": 0, "last_close": None, "mark_value": 0.0,
            "total_pnl": 0.0, "min_pos": 0, "max_pos": 0,
            "max_cash_used": 0.0,
            "returns": compute_returns({}, 0.0, 0, 0.0),
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


def aggregate_returns(results: list[dict]) -> list[dict]:
    """把多支标的汇总成「组合收益率」，**按币种分组**。

    跨币种不相加（项目通用口径：USD 与 HKD 是两种钱，混加无意义）——
    所以返回的是一个 list，每个币种一条，而不是一个总数。

    组合分母同样取「各标的之和」：
      turnover      = Σ 各标的平均单边成交额
      peak_exposure = Σ 各标的峰值占款
      max_cash_used = Σ 各标的实际垫付现金
    注意 Σ 峰值 ≥ 组合真实峰值（各标的的峰值未必同一天出现），
    故组合口径比单票更保守——这是有意为之，宁可低估收益率。
    """
    by_cur: dict[str, dict] = {}
    for r in results:
        if not r.get("n_days"):
            continue
        s, q = r["summary"], r["summary"]["returns"]
        g = by_cur.setdefault(r["currency"], {
            "currency": r["currency"], "codes": [], "n_days": 0,
            "total_pnl": 0.0, "turnover": 0.0, "peak_exposure": 0.0,
            "max_cash_used": 0.0,
        })
        g["codes"].append(r["code"])
        g["n_days"] = max(g["n_days"], r["n_days"])   # 各票窗口长度可能不一
        g["total_pnl"] += s["total_pnl"] or 0.0
        g["turnover"] += q["turnover"] or 0.0
        g["peak_exposure"] += q["peak_exposure"] or 0.0
        # 同 peak_exposure：Σ 各票最低点 >= 组合真实最低点（未必同日出现），偏保守
        g["max_cash_used"] += q.get("max_cash_used") or 0.0

    out = []
    for g in by_cur.values():
        rt = compute_returns(
            {"total_pnl": g["total_pnl"],
             # compute_returns 用 (buy+sell)/2 求平均单边额，这里已是平均额，
             # 故两边各传一份，除以 2 后还原回 g["turnover"]
             "buy_amount": g["turnover"], "sell_amount": g["turnover"]},
            g["peak_exposure"], g["n_days"], g["max_cash_used"])
        g["total_pnl"] = _r(g["total_pnl"])
        g["returns"] = rt
        out.append(g)
    return sorted(out, key=lambda x: x["currency"])


def run_many(codes: list[str], days: int = 30, db_path=None) -> list[dict]:
    """批量回溯（复用同一连接）。"""
    conn = mldb.get_ml_connection_readonly(db_path)
    try:
        return [run_strategy(c, days, conn=conn, db_path=db_path) for c in codes]
    finally:
        conn.close()
