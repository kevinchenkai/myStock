"""P1 校准 — 用真实 orders 回放 1h 撮合规则，核对吻合率。

docs/ML_PLAN.md §4.3：拿真实挂价 + order_status，用 match_limit_order 在挂单当日
的 1h bars 上回放，看"模拟是否成交"与"真实是否成交"的吻合率。达标后再训练。

真实是否成交：order_status in (FILLED_ALL, CANCELLED_PART) 且 dealt_qty>0。
注意：人类挂单可能跨日有效、可能盘前/盘后，1h 只覆盖常规时段 → 吻合率不会 100%，
本模块输出混淆矩阵与吻合率供判断与标注局限。
"""
from __future__ import annotations

from . import data as mldata
from .simulator import match_limit_order


def _real_filled(status: str, dealt_qty: float) -> bool:
    return status in ("FILLED_ALL", "CANCELLED_PART") and (dealt_qty or 0) > 0


def calibrate_code(code: str, db_path=None) -> dict:
    """对单个富途代码做校准，返回统计 dict。"""
    orders = mldata.load_orders(code, db_path)
    bars_by_day = mldata.intraday_bars_by_day(code, db_path)

    tp = tn = fp = fn = 0      # 以"成交"为正类
    no_bars = 0               # 挂单当日无 1h 数据（覆盖窗外/非交易日）
    rows = []

    for _, o in orders.iterrows():
        day = str(o["create_time"])[:10]
        bars = bars_by_day.get(day)
        if not bars:
            no_bars += 1
            continue
        sim = match_limit_order(o["trd_side"], float(o["price"]), bars)
        real = _real_filled(o["order_status"], o["dealt_qty"])
        if sim.filled and real:
            tp += 1
        elif (not sim.filled) and (not real):
            tn += 1
        elif sim.filled and not real:
            fp += 1
        else:
            fn += 1
        rows.append({
            "order_id": o["order_id"], "day": day, "side": o["trd_side"],
            "limit": float(o["price"]), "sim_filled": sim.filled, "real_filled": real,
        })

    n = tp + tn + fp + fn
    agree = (tp + tn) / n if n else 0.0
    return {
        "code": code, "n_evaluated": n, "n_no_bars": no_bars,
        "agreement": round(agree, 4),
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "rows": rows,
    }


def calibrate_all(codes, db_path=None) -> list[dict]:
    return [calibrate_code(c, db_path) for c in codes]


if __name__ == "__main__":
    from . import config as mlcfg
    for r in calibrate_all(mlcfg.TARGETS):
        print(f"{r['code']}: 吻合率={r['agreement']:.1%}  "
              f"评估={r['n_evaluated']} (无bar={r['n_no_bars']})  "
              f"TP={r['tp']} TN={r['tn']} FP={r['fp']} FN={r['fn']}")
