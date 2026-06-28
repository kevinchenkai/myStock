"""交易盈亏（已实现）计算。

口径（经确认）：
  - 只统计**已实现盈亏**：卖出时结算，未实现（浮动）盈亏不计入。
  - **移动平均成本法**：每次买入更新加权平均成本；卖出时
        已实现盈亏 += (卖出价 - 当时平均成本) * 卖出数量
    并按比例扣减持仓与成本基数。
  - **成本兜底**：抓取起点（start_date）之前的建仓买入不在库里，导致某些卖出
    在窗口内找不到对应买入（持仓量被扣到 <= 0）。此时用 positions 最新快照的
    cost_price 作为成本基准兜底；若该兜底成本不可用（缺失或 <= 0，例如富途
    把超卖标的的成本记为负数），则该笔卖出不计入已实现盈亏，仅记入“无法计算
    成本的卖出数量”，结果对用户透明。

输入：deals 列表（dict，需含 code/name/market/trd_side/price/qty/create_time/currency）
      cost_fallback：{code: cost_price}（来自 positions 最新快照，仅正值有效）

输出：每只股票一行的汇总 dict 列表，便于前端表格直接渲染与排序。
"""
from __future__ import annotations

from typing import Iterable, Optional


# 成交表无币种列，按市场推断展示用币种。
_MARKET_CCY = {"HK": "HKD", "US": "USD"}


def _num(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_pnl(
    deals: Iterable[dict],
    cost_fallback: Optional[dict] = None,
) -> list[dict]:
    """按股票汇总已实现盈亏（移动平均成本法 + 持仓成本兜底）。

    deals 不要求有序；本函数内部按 create_time 升序处理同一标的的成交。
    """
    cost_fallback = cost_fallback or {}

    # 按 code 分组
    by_code: dict[str, list[dict]] = {}
    meta: dict[str, dict] = {}
    for d in deals:
        code = d.get("code")
        if not code:
            continue
        by_code.setdefault(code, []).append(d)
        # 记录展示用元信息（名称/市场/币种取第一条非空）
        m = meta.setdefault(code, {"name": None, "market": None, "currency": None})
        for k, src in (("name", "name"), ("market", "market"), ("currency", "currency")):
            if not m[k] and d.get(src):
                m[k] = d.get(src)

    rows: list[dict] = []
    for code, ds in by_code.items():
        # 按时间升序（create_time 形如 'YYYY-MM-DD HH:MM:SS.mmm'，字符串排序即可）
        ds = sorted(ds, key=lambda x: x.get("create_time") or "")

        held_qty = 0.0          # 当前持有数量（移动平均法的基数）
        held_cost = 0.0         # 当前持有总成本
        realized = 0.0          # 已实现盈亏
        buy_qty = sell_qty = 0.0
        buy_amt = sell_amt = 0.0
        uncovered_sell_qty = 0.0  # 无法计算成本而未计入盈亏的卖出数量
        last_time = None

        fb_cost = _num(cost_fallback.get(code))
        if fb_cost is not None and fb_cost <= 0:
            fb_cost = None  # 富途超卖标的成本可能为负，视为不可用

        for d in ds:
            side = str(d.get("trd_side") or "").upper()
            price = _num(d.get("price"))
            qty = _num(d.get("qty"))
            if price is None or qty is None or qty <= 0:
                continue
            last_time = d.get("create_time") or last_time

            if side == "BUY":
                held_qty += qty
                held_cost += price * qty
                buy_qty += qty
                buy_amt += price * qty
            elif side == "SELL":
                sell_qty += qty
                sell_amt += price * qty
                remain = qty
                # 先用窗口内持仓（移动平均成本）配平
                if held_qty > 0:
                    avg = held_cost / held_qty
                    matched = min(held_qty, remain)
                    realized += (price - avg) * matched
                    held_cost -= avg * matched
                    held_qty -= matched
                    remain -= matched
                # 剩余部分用持仓快照成本兜底
                if remain > 0:
                    if fb_cost is not None:
                        realized += (price - fb_cost) * remain
                    else:
                        uncovered_sell_qty += remain
                    remain = 0.0

        avg_buy = (buy_amt / buy_qty) if buy_qty > 0 else None
        avg_sell = (sell_amt / sell_qty) if sell_qty > 0 else None

        currency = meta[code]["currency"] or _MARKET_CCY.get(
            meta[code]["market"] or "", None
        )

        rows.append({
            "code": code,
            "name": meta[code]["name"],
            "market": meta[code]["market"],
            "currency": currency,
            "buy_qty": buy_qty,
            "sell_qty": sell_qty,
            "avg_buy_price": avg_buy,
            "avg_sell_price": avg_sell,
            "realized_pnl": realized,
            # 已实现盈亏率：相对“已卖出部分的成本”，避免被未平仓部分稀释
            "realized_pnl_ratio": (
                (realized / (sell_amt - realized)) * 100
                if (sell_amt - realized) > 0 else None
            ),
            "net_qty": buy_qty - sell_qty,
            "uncovered_sell_qty": uncovered_sell_qty,
            "last_deal_time": last_time,
            "deal_count": len(ds),
        })

    # 默认按已实现盈亏降序
    rows.sort(key=lambda r: r["realized_pnl"], reverse=True)
    return rows


# ============================================================
# 年度财务统计（现金流口径：收 - 付）
# ============================================================

def yearly_finance(deals: Iterable[dict], year: str) -> dict:
    """统计某一年度内的成交现金流，按市场（美股 / 港股）分别汇总。

    口径（经确认）：**年度现金流（收 - 付）**——只看 create_time 落在该年度内的
    成交，不跨年配对成本。某市场该年的盈亏 = 当年卖出总额 - 当年买入总额。
    注意：若当年只买未卖（建仓），盈亏会是大额负数，这是支出而非真实亏损——
    前端需如实标注口径。金额为标的本币（HK→HKD、US→USD），两市场不可相加。

    返回：
      year：年度字符串。
      markets：每个市场一行的汇总 dict 列表（仅含该年有成交的市场）。
      available_years：deals 中出现过的全部年份（降序），供前端构建筛选。
    """
    year = str(year)
    # 先扫一遍，收集所有出现过的年份（供前端年份筛选）
    all_years: set[str] = set()
    for d in deals if isinstance(deals, list) else list(deals):
        y = str(d.get("create_time") or "")[:4]
        if len(y) == 4 and y.isdigit():
            all_years.add(y)

    # 当年成交按市场聚合
    agg: dict[str, dict] = {}
    for d in (deals if isinstance(deals, list) else []):
        if str(d.get("create_time") or "")[:4] != year:
            continue
        market = d.get("market") or ""
        if market not in ("US", "HK"):
            continue
        side = str(d.get("trd_side") or "").upper()
        price = _num(d.get("price"))
        qty = _num(d.get("qty"))
        if price is None or qty is None or qty <= 0:
            continue
        amt = price * qty
        a = agg.setdefault(market, {
            "buy_amount": 0.0, "sell_amount": 0.0,
            "buy_qty": 0.0, "sell_qty": 0.0,
            "buy_count": 0, "sell_count": 0,
        })
        if side == "BUY":
            a["buy_amount"] += amt
            a["buy_qty"] += qty
            a["buy_count"] += 1
        elif side == "SELL":
            a["sell_amount"] += amt
            a["sell_qty"] += qty
            a["sell_count"] += 1

    markets: list[dict] = []
    # 固定顺序：美股、港股
    for market in ("US", "HK"):
        if market not in agg:
            continue
        a = agg[market]
        net = a["sell_amount"] - a["buy_amount"]
        markets.append({
            "market": market,
            "currency": _MARKET_CCY.get(market),
            "buy_amount": a["buy_amount"],
            "sell_amount": a["sell_amount"],
            "buy_qty": a["buy_qty"],
            "sell_qty": a["sell_qty"],
            "buy_count": a["buy_count"],
            "sell_count": a["sell_count"],
            "deal_count": a["buy_count"] + a["sell_count"],
            "net_cashflow": net,   # 卖出额 - 买入额（本币）
        })

    return {
        "year": year,
        "markets": markets,
        "available_years": sorted(all_years, reverse=True),
    }


# ============================================================
# 单只股票交易复盘（FIFO 配对，用于独立分析页）
# ============================================================

def _parse_time(s):
    """'YYYY-MM-DD HH:MM:SS.mmm' → datetime；失败返回 None。"""
    from datetime import datetime
    if not s:
        return None
    s = str(s)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:26] if "." in s else s[:19], fmt)
        except ValueError:
            continue
    return None


def analyze_stock(deals: list[dict], cost_fallback: Optional[float] = None) -> dict:
    """对单只股票的成交做交易复盘（FIFO 配对，仅复盘交易行为，不含投资建议）。

    返回：
      summary：汇总指标（成交笔数、买卖量/额/均价、已实现盈亏、净持仓等）。
      round_trips：已平仓的 FIFO 配对回合（买入价/卖出价/数量/持有天数/盈亏）。
      stats：复盘统计（胜率、盈亏比、平均持有天数、最大盈/亏单、平均单笔等）。
      observations：客观交易习惯提示（事实陈述，非买卖建议）。
    """
    fb = _num(cost_fallback)
    if fb is not None and fb <= 0:
        fb = None

    ds = sorted(deals, key=lambda x: x.get("create_time") or "")
    name = market = currency = None
    for d in ds:
        name = name or d.get("name")
        market = market or d.get("market")
        currency = currency or d.get("currency")
    currency = currency or _MARKET_CCY.get(market or "", None)

    # FIFO 买入队列：每个 lot = [剩余数量, 单价, 时间]
    lots: list[list] = []
    round_trips: list[dict] = []
    buy_qty = sell_qty = 0.0
    buy_amt = sell_amt = 0.0
    realized = 0.0
    uncovered_sell_qty = 0.0
    first_time = last_time = None

    for d in ds:
        side = str(d.get("trd_side") or "").upper()
        price = _num(d.get("price"))
        qty = _num(d.get("qty"))
        if price is None or qty is None or qty <= 0:
            continue
        t = _parse_time(d.get("create_time"))
        if first_time is None:
            first_time = d.get("create_time")
        last_time = d.get("create_time")

        if side == "BUY":
            lots.append([qty, price, t])
            buy_qty += qty
            buy_amt += price * qty
        elif side == "SELL":
            sell_qty += qty
            sell_amt += price * qty
            remain = qty
            while remain > 0 and lots:
                lot = lots[0]
                take = min(lot[0], remain)
                pnl = (price - lot[1]) * take
                realized += pnl
                hold_days = None
                if t and lot[2]:
                    hold_days = (t - lot[2]).total_seconds() / 86400.0
                round_trips.append({
                    "buy_time": lot[2].strftime("%Y-%m-%d") if lot[2] else None,
                    "sell_time": t.strftime("%Y-%m-%d") if t else None,
                    "buy_price": lot[1],
                    "sell_price": price,
                    "qty": take,
                    "hold_days": hold_days,
                    "pnl": pnl,
                    "pnl_ratio": ((price - lot[1]) / lot[1] * 100) if lot[1] else None,
                })
                lot[0] -= take
                remain -= take
                if lot[0] <= 1e-9:
                    lots.pop(0)
            # 剩余卖出无对应买入：用兜底成本配一回合，否则记未覆盖
            if remain > 0:
                if fb is not None:
                    pnl = (price - fb) * remain
                    realized += pnl
                    round_trips.append({
                        "buy_time": None, "sell_time": t.strftime("%Y-%m-%d") if t else None,
                        "buy_price": fb, "sell_price": price, "qty": remain,
                        "hold_days": None, "pnl": pnl,
                        "pnl_ratio": ((price - fb) / fb * 100) if fb else None,
                        "fallback": True,
                    })
                else:
                    uncovered_sell_qty += remain

    # ---- 复盘统计 ----
    wins = [r for r in round_trips if r["pnl"] > 0]
    losses = [r for r in round_trips if r["pnl"] < 0]
    closed = len(round_trips)
    win_amt = sum(r["pnl"] for r in wins)
    loss_amt = -sum(r["pnl"] for r in losses)
    holds = [r["hold_days"] for r in round_trips if r["hold_days"] is not None]

    def _best(rs):
        return max(rs, key=lambda r: r["pnl"]) if rs else None

    def _worst(rs):
        return min(rs, key=lambda r: r["pnl"]) if rs else None

    stats = {
        "closed_trips": closed,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": (len(wins) / closed * 100) if closed else None,
        "total_win": win_amt,
        "total_loss": loss_amt,
        # 盈亏比 = 总盈利 / 总亏损（>1 表示赚的比亏的多）
        "profit_factor": (win_amt / loss_amt) if loss_amt > 0 else None,
        "avg_win": (win_amt / len(wins)) if wins else None,
        "avg_loss": (loss_amt / len(losses)) if losses else None,
        "avg_hold_days": (sum(holds) / len(holds)) if holds else None,
        "max_hold_days": max(holds) if holds else None,
        "min_hold_days": min(holds) if holds else None,
        "best_trip": _best(round_trips),
        "worst_trip": _worst(round_trips),
    }

    avg_buy = (buy_amt / buy_qty) if buy_qty > 0 else None
    avg_sell = (sell_amt / sell_qty) if sell_qty > 0 else None
    summary = {
        "code": ds[0].get("code") if ds else None,
        "name": name, "market": market, "currency": currency,
        "deal_count": len(ds),
        "buy_qty": buy_qty, "sell_qty": sell_qty,
        "avg_buy_price": avg_buy, "avg_sell_price": avg_sell,
        "buy_amount": buy_amt, "sell_amount": sell_amt,
        "realized_pnl": realized,
        "net_qty": buy_qty - sell_qty,
        "uncovered_sell_qty": uncovered_sell_qty,
        "first_deal_time": first_time, "last_deal_time": last_time,
    }

    observations = _build_observations(summary, stats, round_trips)

    return {
        "summary": summary,
        "round_trips": round_trips,
        "stats": stats,
        "observations": observations,
    }


def _build_observations(summary: dict, stats: dict, trips: list[dict]) -> list[dict]:
    """生成客观的交易行为复盘提示（事实陈述，非投资/买卖建议）。

    每条 {level, text}：level ∈ good / warn / info，仅描述已发生的交易特征。
    """
    obs: list[dict] = []
    wr = stats["win_rate"]
    pf = stats["profit_factor"]
    closed = stats["closed_trips"]

    if closed == 0:
        obs.append({"level": "info", "text": "暂无已平仓的买卖回合，无法复盘盈亏结构（可能只有单边成交或建仓在抓取起点之前）。"})
        return obs

    # 胜率
    if wr is not None:
        if wr >= 60:
            obs.append({"level": "good", "text": f"胜率 {wr:.0f}%（{stats['win_count']}/{closed} 回合盈利），盈利回合占多数。"})
        elif wr >= 40:
            obs.append({"level": "info", "text": f"胜率 {wr:.0f}%（{stats['win_count']}/{closed}），盈亏回合接近，盈亏比更能说明结果。"})
        else:
            obs.append({"level": "warn", "text": f"胜率偏低 {wr:.0f}%（{stats['win_count']}/{closed}），盈利更多依赖少数大盈利回合。"})

    # 盈亏比
    if pf is not None:
        if pf >= 1.5:
            obs.append({"level": "good", "text": f"盈亏比 {pf:.2f}（总盈利/总亏损），赚的明显多于亏的。"})
        elif pf >= 1.0:
            obs.append({"level": "info", "text": f"盈亏比 {pf:.2f}，整体小幅盈利。"})
        else:
            obs.append({"level": "warn", "text": f"盈亏比 {pf:.2f}（<1），累计亏损大于盈利。"})

    # 平均盈利 vs 平均亏损（是否“截断利润、放任亏损”）
    aw, al = stats["avg_win"], stats["avg_loss"]
    if aw is not None and al is not None and al > 0:
        if aw < al:
            obs.append({"level": "warn", "text": f"平均每笔盈利 {aw:.0f} 小于平均每笔亏损 {al:.0f}，存在“小赚大亏”的形态。"})
        else:
            obs.append({"level": "good", "text": f"平均每笔盈利 {aw:.0f} 大于平均每笔亏损 {al:.0f}，单笔盈亏结构较健康。"})

    # 持有时长
    ah = stats["avg_hold_days"]
    if ah is not None:
        if ah < 5:
            obs.append({"level": "info", "text": f"平均持有约 {ah:.1f} 天，偏短线交易。"})
        elif ah > 60:
            obs.append({"level": "info", "text": f"平均持有约 {ah:.0f} 天，偏中长线持有。"})
        else:
            obs.append({"level": "info", "text": f"平均持有约 {ah:.0f} 天。"})

    # 最大盈/亏单
    bt, wt = stats["best_trip"], stats["worst_trip"]
    if bt and bt["pnl"] > 0:
        obs.append({"level": "info", "text": f"最大盈利回合 +{bt['pnl']:.0f}（{bt.get('buy_time') or '—'} → {bt.get('sell_time') or '—'}）。"})
    if wt and wt["pnl"] < 0:
        obs.append({"level": "info", "text": f"最大亏损回合 {wt['pnl']:.0f}（{wt.get('buy_time') or '—'} → {wt.get('sell_time') or '—'}）。"})

    # 集中度 / 数据完整性提示
    if summary["uncovered_sell_qty"] > 0:
        obs.append({"level": "warn", "text": f"有 {summary['uncovered_sell_qty']:.0f} 股卖出在抓取区间内找不到买入成本，未计入盈亏，复盘结果偏保守。"})
    if summary["net_qty"] > 0:
        obs.append({"level": "info", "text": f"当前仍净持有 {summary['net_qty']:.0f} 股（未平仓部分的浮动盈亏不在本复盘内）。"})

    return obs
