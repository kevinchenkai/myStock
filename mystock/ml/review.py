"""预测复盘：把留档的次日区间预测与**真实次日**高/低对齐、判命中、算偏移。

回答报告读者最直接的问题——「前几天说的区间，实际走出来对不对？」

口径（与 predictor / backtest 的命中判定一致）：
  - 预测在基准日 T 收盘后给出，标的是 T 的**次一交易日** T+1 的 [low, high]。
  - 命中 = 次日真实 high <= H_hat **且** 真实 low >= L_hat（区间完全包住次日波动）。
    单边戳出即算未命中——这是分位区间的诚实口径，不做"部分命中"粉饰。
  - 偏移(miss_pct)：戳出幅度 / close，取上下两侧较大者，正数。命中时为 0。
    上破(above)=真实高超过上沿；下破(below)=真实低跌破下沿；两侧都破=both。

「次日」一律按**该标的自身的交易日历**取下一根日线，不按自然日 +1 —— 周末、
假期、停牌都靠这个避开（美股 07-24 的次日是 07-27，港股假期同理）。

纯函数、不碰 IO：输入预测 dict 列表 + 日线 DataFrame，输出复盘 dict 列表。
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd
from . import sessions


def _f(v) -> Optional[float]:
    """转 float；None/NaN/空串 → None（脏数据不进判定）。"""
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x   # NaN


def next_trading_day_map(daily: pd.DataFrame) -> dict[str, str]:
    """{某交易日 -> 其次一交易日}。最后一日无次日，不入表。"""
    dates = [str(d) for d in daily["date"]]
    return {d: dates[i + 1] for i, d in enumerate(dates[:-1])}


def actual_range_by_day(daily: pd.DataFrame) -> dict[str, tuple]:
    """{交易日 -> (low, high)}，脏值转 None。"""
    out: dict[str, tuple] = {}
    for _, r in daily.iterrows():
        out[str(r["date"])] = (_f(r["low"]), _f(r["high"]))
    return out


def review_one(pred: dict, next_day: Optional[str],
               actual: Optional[tuple]) -> dict:
    """单条预测的复盘结果。

    next_day / actual 缺失（最新一条预测的次日还没走出来）→ status='pending'，
    保留预测本身供展示，但不参与命中率统计。
    """
    code, as_of = pred.get("code"), pred.get("as_of")
    L, H, close = _f(pred.get("l_hat")), _f(pred.get("h_hat")), _f(pred.get("close"))
    base = {
        "code": code, "as_of": as_of, "next_day": next_day, "close": close,
        "l_hat": L, "h_hat": H,
        "actual_low": None, "actual_high": None,
        "hit": None, "miss_side": None, "miss_pct": None,
        "width_pct": _f(pred.get("width_pct")), "status": "pending",
    }
    if next_day is None or actual is None:
        return base
    # 实际值也要过 _f：NaN 不会等于 None，不清洗会漏过下面的缺失判定，
    # 而 NaN 的比较恒为 False → above/below 均 <=0 → 误判成"命中"或"miss"。
    lo, hi = _f(actual[0]), _f(actual[1])
    base["actual_low"], base["actual_high"] = lo, hi
    if None in (L, H, lo, hi):
        base["status"] = "no_data"
        return base

    above = hi - H          # >0：真实高戳破上沿
    below = L - lo          # >0：真实低戳破下沿
    hit = above <= 0 and below <= 0
    base["hit"] = hit
    base["status"] = "hit" if hit else "miss"
    if hit:
        base["miss_side"], base["miss_pct"] = None, 0.0
    else:
        if above > 0 and below > 0:
            side = "both"
        elif above > 0:
            side = "above"
        else:
            side = "below"
        base["miss_side"] = side
        # 戳出幅度按 close 归一（跨标的可比）；close 不可用时退回按上沿价归一
        denom = close if close else (H or 1.0)
        base["miss_pct"] = round(max(above, below) / denom * 100, 2)
    return base


def review_predictions(preds: Iterable[dict], daily: pd.DataFrame) -> list[dict]:
    """一支标的的全部预测 vs 实际。preds 需同属一个 code；按 as_of 升序返回。"""
    nxt = next_trading_day_map(daily)
    act = actual_range_by_day(daily)
    out = []
    for p in preds:
        as_of = str(p.get("as_of"))
        nd = p.get("target_session") or (sessions.next_session(p["code"], as_of) if p.get("code") else nxt.get(as_of))
        out.append(review_one(p, nd, act.get(nd) if nd else None))
    return sorted(out, key=lambda r: str(r["as_of"]))


def hit_rate(rows: Iterable[dict]) -> Optional[float]:
    """已结算（hit/miss）行的命中率；无样本 → None。"""
    done = [r for r in rows if r.get("hit") is not None]
    if not done:
        return None
    return sum(1 for r in done if r["hit"]) / len(done)


def summarize(rows: Iterable[dict]) -> dict:
    """复盘汇总：样本数、命中率、上/下破次数、平均戳出幅度。"""
    rows = list(rows)
    done = [r for r in rows if r.get("hit") is not None]
    missed = [r for r in done if not r["hit"]]
    misses = [r["miss_pct"] for r in missed if r.get("miss_pct") is not None]
    n_above = sum(1 for r in missed if r["miss_side"] in ("above", "both"))
    n_below = sum(1 for r in missed if r["miss_side"] in ("below", "both"))
    return {
        "n_total": len(rows),
        "n_settled": len(done),
        "n_pending": sum(1 for r in rows if r.get("status") == "pending"),
        "hit_rate": hit_rate(done),
        "n_miss_above": n_above,
        "n_miss_below": n_below,
        "avg_miss_pct": round(sum(misses) / len(misses), 2) if misses else None,
    }
