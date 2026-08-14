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

运行：python -m mystock.ml.backfill
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config as mlcfg
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
    n = run()
    print(f"回填预测 {n} 条 → ml_predictions")
