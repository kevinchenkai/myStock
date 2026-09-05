"""从历史 HTML 报告回填次日区间预测到 ml_predictions（一次性考古，幂等）。

背景：ml_predictions 表是后加的，此前每天的预测只存在于 data/ml/reports/<date>/
index.html 里。本模块把那些已生成的报告解析出来灌库，让「近期预测复盘」立刻
有内容可看，不必等新表自然积累几周。

解析目标是每股 section 里那句（report._stock_section 生成）：
    <b>US.NVDA</b> 截至 2026-08-13 收盘 225.30 → 次日预测区间 <b ...>220.62</b> ~ <b ...>233.26</b>（宽 5.61%）

幂等：PK (code, as_of) 覆盖写。同一 as_of 被多份报告包含（当天没新交易日就重跑）
时，取**报告日期最大**的那份——即该基准日最后一次生成的预测。

回填行只标 source='backfill'，且 low_alpha/q_ret 等口径字段为 NULL（历史 HTML
没记这些）。实时写入的行 source='live'、字段齐全，二者可据此区分。

另有 recompute_gaps()：报告不是每个交易日都跑，留档因此有洞。把日线**截断到
基准日 T** 再跑一次 predict_next_day，即可补出"那天本该给出的预测"。

运行：
    python -m mystock.ml.backfill            # 解析历史 HTML 回填
    python -m mystock.ml.backfill --gaps     # 重算补齐缺失交易日
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from . import config as mlcfg
from . import data as mldata
from . import db as mldb

# <b>CODE</b> 截至 DATE 收盘 CLOSE → 次日预测区间 <b..>L</b> ~ <b..>H</b>
_PAT = re.compile(
    r"<b>([A-Z]{2}\.[0-9A-Za-z]+)</b>\s*截至\s*([0-9-]{10})\s*收盘\s*([\d,.]+)\s*"
    r"→\s*次日预测区间\s*<b[^>]*>([\d,.]+)</b>\s*~\s*<b[^>]*>([\d,.]+)</b>"
)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_report_html(text: str) -> list[dict]:
    """从单份报告 HTML 解析出预测行（纯函数，可单测）。"""
    out = []
    for code, as_of, close, l_hat, h_hat in _PAT.findall(text):
        c, L, H = _num(close), _num(l_hat), _num(h_hat)
        out.append({
            "code": code, "as_of": as_of, "close": c, "l_hat": L, "h_hat": H,
            "width_pct": round((H - L) / c * 100, 2) if c else None,
        })
    return out


def collect(reports_dir: Path | None = None) -> list[dict]:
    """扫描报告归档目录，返回去重后的预测行。

    目录名（报告生成日）升序遍历，同 (code, as_of) 后者覆盖前者 → 天然取到
    最后一次生成的版本。
    """
    reports_dir = reports_dir or mlcfg.REPORTS_DIR
    if not reports_dir.is_dir():
        return []
    dedup: dict[tuple, dict] = {}
    for sub in sorted(p for p in reports_dir.iterdir() if p.is_dir()):
        f = sub / "index.html"
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for row in parse_report_html(text):
            row["source"] = "backfill"
            row["generated_at"] = f"{sub.name} 00:00:00"  # 以报告日期为准（原始时间已不可考）
            dedup[(row["code"], row["as_of"])] = row
    return [dedup[k] for k in sorted(dedup)]


def run(reports_dir: Path | None = None, db_path=None) -> int:
    """回填入库。返回写入行数。已存在的 (code, as_of) 会被覆盖为 HTML 版本，
    故仅在**首次**建表后跑一次即可；重复跑无害（同源同值）。"""
    rows = collect(reports_dir)
    if not rows:
        return 0
    conn = mldb.get_ml_connection(db_path)
    try:
        n = mldb.upsert_predictions(conn, rows)
        mldb.log_sync(conn, "backfill_predictions", row_count=n,
                      range_start=rows[0]["as_of"], range_end=rows[-1]["as_of"])
    finally:
        conn.close()
    return n


def missing_dates(conn, code: str, daily, since: str = "") -> list[str]:
    """该标的在 daily 里、但 ml_predictions 中没有留档的交易日（升序）。

    末日也算缺口——那天确实可以预测（次日未知不影响"给出预测"这个动作），
    复盘时会因无次日而判 pending，不进命中率统计。
    """
    have = {r[0] for r in conn.execute(
        "SELECT as_of FROM ml_predictions WHERE code=?", (code,))}
    dates = [str(d) for d in daily["date"]]
    return [d for d in dates if d not in have and (not since or d >= since)]


def recompute_gaps(since: str = "", db_path=None, *, verbose: bool = True) -> int:
    """重算补齐缺失交易日的预测。返回写入行数。

    做法：把日线**截断到基准日 T**（`daily[date <= T]`）再跑 predict_next_day。
    predict_next_day 用全历史 fit、对末行推理——截断后末行即 T，故产出与"当天
    真的跑一次报告"同口径。

    **无未来函数**：训练集与特征都只到 T，T 之后的行整段不存在，模型无从看到；
    标签 y_high/y_low 来自 T 及更早的次日，同样不越界。已用已有 backfill 留档
    实测对照：重算与当时真实产出差异 ≤0.04（≈0.02%），且来自 yfinance 对历史
    bar 的微幅修订，不是泄漏。

    与 live 行的区别只在 source='recomputed'——诚实标注"这条是事后补的，
    不是当天真的跑出来的"，便于日后甄别。
    """
    conn = mldb.get_ml_connection(db_path)
    total = 0
    try:
        for code in mlcfg.TARGETS:
            daily = mldata.load_daily(code, db_path)
            if daily.empty:
                continue
            gaps = missing_dates(conn, code, daily, since)
            if not gaps:
                if verbose:
                    print(f"  {code}: 无缺口")
                continue
            lo_a, hi_a = mlcfg.alpha_for(code)
            cov = mlcfg.coverage_for(code)
            rows = []
            for as_of in gaps:
                sub = daily[daily["date"] <= as_of].copy()
                sub.attrs["code"] = code
                # 特征需要 ~20 行热身 + CQR 校准集，样本太少直接跳过（宁缺勿造）
                if len(sub) < 60:
                    continue
                try:
                    p = _predict(sub, lo_a, hi_a, cov)
                except Exception as e:  # noqa: BLE001
                    if verbose:
                        print(f"  {code} {as_of}: 跳过（{type(e).__name__}: {e}）")
                    continue
                if p["as_of"] != as_of:   # 截断后末行应恰为 as_of，不符则不写
                    continue
                rows.append({
                    "code": code, "as_of": p["as_of"], "close": p["close"],
                    "l_hat": p["L_hat"], "h_hat": p["H_hat"],
                    "width_pct": p["width_pct"],
                    "low_alpha": lo_a, "high_alpha": hi_a,
                    "conformal": int(bool(p["conformal"])), "q_ret": p["q_ret"],
                    "target_coverage": p["target_coverage"],
                    "backend": _backend(), "source": "recomputed",
                })
            if rows:
                total += mldb.upsert_predictions(conn, rows)
                if verbose:
                    print(f"  {code}: 补 {len(rows)} 条（{rows[0]['as_of']} ~ {rows[-1]['as_of']}）")
        if total:
            mldb.log_sync(conn, "recompute_predictions", row_count=total)
    finally:
        conn.close()
    return total


def _predict(sub, lo_a: float, hi_a: float, cov: float) -> dict:
    """延迟导入 predictor——它会拖起 lightgbm/sklearn，解析 HTML 那条路径用不上。"""
    from .predictor import predict_next_day
    return predict_next_day(sub, high_alpha=hi_a, low_alpha=lo_a,
                            conformal=True, target_coverage=cov, historical=True, code=sub.attrs.get("code"))


def _backend() -> str:
    try:
        import lightgbm  # noqa: F401
        return "lightgbm"
    except Exception:  # noqa: BLE001
        return "sklearn"


def run_if_empty(db_path=None) -> int:
    """仅当 ml_predictions 为空时回填（供 ml.sh 每次执行安全调用）。

    表非空说明已回填过 / 已有实时留档 —— 此时不该再用历史 HTML 覆盖，
    否则会把字段齐全的 live 行退化成字段稀疏的 backfill 行。
    """
    conn = mldb.get_ml_connection(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM ml_predictions").fetchone()[0]
    finally:
        conn.close()
    return 0 if n else run(db_path=db_path)


if __name__ == "__main__":
    mldb.init_ml_db()
    if "--gaps" in sys.argv:
        # 可选：--since YYYY-MM-DD 限定起点（不传则补全部历史缺口）
        since = ""
        if "--since" in sys.argv:
            since = sys.argv[sys.argv.index("--since") + 1]
        print(f"重算补齐缺失交易日的预测{f'（{since} 起）' if since else ''}：")
        n = recompute_gaps(since)
        print(f"共补 {n} 条 → ml_predictions（source=recomputed）")
    else:
        n = run()
        print(f"回填预测 {n} 条 → ml_predictions")
