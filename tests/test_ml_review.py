"""预测复盘（review.py）+ 历史回填解析（backfill.py）的单测。

覆盖重点：命中/单边戳出/双破判定、按交易日历取次日（跳周末假期）、
pending（次日未走出）不污染命中率、脏数据不误判、HTML 解析与去重。
"""
import pandas as pd
import pytest

from mystock.ml import backfill, review as rv


def _daily(rows):
    """rows: [(date, low, high)] → 日线 DataFrame（review 只用到这三列）。"""
    return pd.DataFrame(
        [{"date": d, "low": lo, "high": hi} for d, lo, hi in rows]
    )


# 交易日历刻意跳过 07-25/26（周末），验证"次日"不是自然日 +1
DAILY = _daily([
    ("2026-07-23", 100.0, 110.0),
    ("2026-07-24", 101.0, 109.0),
    ("2026-07-27", 95.0, 108.0),
    ("2026-07-28", 103.0, 118.0),
    ("2026-07-29", 90.0, 125.0),
])


def _pred(as_of, l, h, close=100.0):
    return {"code": "US.NVDA", "as_of": as_of, "close": close,
            "l_hat": l, "h_hat": h}


# ---- 次日映射（按交易日历，非自然日）----
def test_next_trading_day_skips_weekend():
    nxt = rv.next_trading_day_map(DAILY)
    assert nxt["2026-07-24"] == "2026-07-27"   # 周五 → 下周一
    assert "2026-07-29" not in nxt             # 最后一日无次日


# ---- 命中判定 ----
def test_hit_when_interval_contains_actual():
    # 次日(07-24) 实际 101~109，预测 100~110 完全包住
    r = rv.review_one(_pred("2026-07-23", 100.0, 110.0), "2026-07-24", (101.0, 109.0))
    assert r["hit"] is True
    assert r["status"] == "hit"
    assert r["miss_side"] is None
    assert r["miss_pct"] == 0.0


def test_miss_above_when_actual_high_breaks_upper():
    # 实际高 118 > 上沿 110 → 上破，戳出 8/100 = 8%
    r = rv.review_one(_pred("2026-07-27", 100.0, 110.0), "2026-07-28", (103.0, 118.0))
    assert r["hit"] is False
    assert r["miss_side"] == "above"
    assert r["miss_pct"] == pytest.approx(8.0)


def test_miss_below_when_actual_low_breaks_lower():
    # 实际低 95 < 下沿 100 → 下破，戳出 5%
    r = rv.review_one(_pred("2026-07-24", 100.0, 110.0), "2026-07-27", (95.0, 108.0))
    assert r["hit"] is False
    assert r["miss_side"] == "below"
    assert r["miss_pct"] == pytest.approx(5.0)


def test_miss_both_sides_takes_larger():
    # 实际 90~125：下破 10、上破 15 → side=both，取较大者 15%
    r = rv.review_one(_pred("2026-07-28", 100.0, 110.0), "2026-07-29", (90.0, 125.0))
    assert r["miss_side"] == "both"
    assert r["miss_pct"] == pytest.approx(15.0)


def test_touching_boundary_counts_as_hit():
    # 恰好贴边（等于上下沿）算命中——闭区间口径
    r = rv.review_one(_pred("2026-07-23", 100.0, 110.0), "2026-07-24", (100.0, 110.0))
    assert r["hit"] is True


# ---- pending / 脏数据 ----
def test_pending_when_next_day_missing():
    r = rv.review_one(_pred("2026-07-29", 100.0, 110.0), None, None)
    assert r["status"] == "pending"
    assert r["hit"] is None


def test_no_data_when_actual_is_none():
    r = rv.review_one(_pred("2026-07-23", 100.0, 110.0), "2026-07-24", (None, 109.0))
    assert r["status"] == "no_data"
    assert r["hit"] is None


def test_nan_actual_treated_as_missing():
    r = rv.review_one(_pred("2026-07-23", 100.0, 110.0), "2026-07-24", (float("nan"), 109.0))
    assert r["status"] == "no_data"


# ---- 端到端 + 汇总 ----
def test_review_predictions_end_to_end():
    preds = [
        _pred("2026-07-23", 100.0, 110.0),   # → 07-24 实际 101~109  命中
        _pred("2026-07-24", 100.0, 110.0),   # → 07-27 实际 95~108   下破
        _pred("2026-07-29", 100.0, 110.0),   # 无次日 → pending
    ]
    rows = rv.review_predictions(preds, DAILY)
    assert [r["status"] for r in rows] == ["hit", "miss", "pending"]
    assert rv.hit_rate(rows) == pytest.approx(0.5)   # pending 不计入分母

    s = rv.summarize(rows)
    assert s["n_total"] == 3
    assert s["n_settled"] == 2
    assert s["n_pending"] == 1
    assert s["n_miss_below"] == 1
    assert s["n_miss_above"] == 0


def test_summarize_empty():
    s = rv.summarize([])
    assert s["n_settled"] == 0
    assert s["hit_rate"] is None
    assert s["avg_miss_pct"] is None


def test_review_predictions_sorted_by_as_of():
    preds = [_pred("2026-07-24", 100.0, 110.0), _pred("2026-07-23", 100.0, 110.0)]
    rows = rv.review_predictions(preds, DAILY)
    assert [r["as_of"] for r in rows] == ["2026-07-23", "2026-07-24"]


# ---- 历史 HTML 解析 ----
_HTML = """<h3>US.NVDA</h3>
<p><b>US.NVDA</b> 截至 2026-08-13 收盘 225.30 → 次日预测区间
<b style='color:#127a3d'>220.62</b> ~ <b style='color:#d33'>233.26</b>（宽 5.61%）</p>
<h3>HK.00700</h3>
<p><b>HK.00700</b> 截至 2026-08-14 收盘 1,440.00 → 次日预测区间
<b style='color:#127a3d'>1,426.41</b> ~ <b style='color:#d33'>1,452.78</b>（宽 1.83%）</p>"""


def test_parse_report_html():
    rows = backfill.parse_report_html(_HTML)
    assert len(rows) == 2
    a, b = rows
    assert a["code"] == "US.NVDA" and a["as_of"] == "2026-08-13"
    assert a["close"] == pytest.approx(225.30)
    assert a["l_hat"] == pytest.approx(220.62)
    assert a["h_hat"] == pytest.approx(233.26)
    # 千分位逗号需正确剥离（港股价位常见）
    assert b["close"] == pytest.approx(1440.00)
    assert b["l_hat"] == pytest.approx(1426.41)


def test_parse_report_html_ignores_unrelated():
    assert backfill.parse_report_html("<p>无预测内容</p>") == []


def test_collect_preserves_each_report_version(tmp_path):
    """同一 as_of 的每份原始报告保留独立版本。"""
    for day, h in (("2026-08-14", "230.00"), ("2026-08-15", "240.00")):
        d = tmp_path / day
        d.mkdir()
        (d / "index.html").write_text(
            "<b>US.NVDA</b> 截至 2026-08-13 收盘 225.30 → 次日预测区间 "
            f"<b>220.62</b> ~ <b>{h}</b>", encoding="utf-8")
    rows = backfill.collect(tmp_path)
    assert len(rows) == 2
    assert rows[0]["h_hat"] == pytest.approx(230.00)
    assert rows[1]["h_hat"] == pytest.approx(240.00)
    assert rows[0]["generated_at"] is None
    assert rows[0]["source"] == "backfill"


def test_collect_empty_dir(tmp_path):
    assert backfill.collect(tmp_path) == []


# ---- 缺口识别（recompute_gaps 的取数依据）----
class _FakeConn:
    """只实现 execute("SELECT as_of ...")，返回给定的已有日期。"""

    def __init__(self, have):
        self._have = [(d,) for d in have]

    def execute(self, sql, params=()):
        return self._have


def test_missing_dates_finds_holes():
    daily = _daily([(d, 1.0, 2.0) for d in
                    ("2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28")])
    conn = _FakeConn({"2026-07-23", "2026-07-27"})
    assert backfill.missing_dates(conn, "US.NVDA", daily) == [
        "2026-07-24", "2026-07-28"]


def test_missing_dates_respects_since():
    daily = _daily([(d, 1.0, 2.0) for d in
                    ("2026-07-23", "2026-07-24", "2026-07-27")])
    conn = _FakeConn(set())
    assert backfill.missing_dates(conn, "US.NVDA", daily, since="2026-07-24") == [
        "2026-07-24", "2026-07-27"]


def test_missing_dates_none_when_complete():
    daily = _daily([(d, 1.0, 2.0) for d in ("2026-07-23", "2026-07-24")])
    conn = _FakeConn({"2026-07-23", "2026-07-24"})
    assert backfill.missing_dates(conn, "US.NVDA", daily) == []
