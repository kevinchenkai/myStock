"""fetch 增量窗口选择逻辑单测（纯函数，无网络/无库）。"""
from datetime import date, timedelta

import pytest

pytest.importorskip("pandas")

from mystock.ml.fetch import D_TIERS, H_TIERS, _gap_days, _period_for


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def test_gap_days():
    assert _gap_days(_days_ago(0)) == 0
    assert _gap_days(_days_ago(7)) == 7
    assert _gap_days(None) is None
    assert _gap_days("garbage") is None
    # 带时间部分的 ts（1h 表切出的前 10 位场景）
    assert _gap_days(_days_ago(3) + " 15:30:00") == 3


@pytest.mark.parametrize("gap,expect", [
    (0, "1mo"), (25, "1mo"),      # 新鲜 → 短窗
    (26, None), (400, None),      # 缺口大 → 全量
    (None, None),                 # 无数据 → 全量
])
def test_period_for_daily(gap, expect):
    assert _period_for(gap, D_TIERS) == expect


@pytest.mark.parametrize("gap,expect", [
    (0, "5d"), (4, "5d"),         # 很新鲜 → 5d
    (5, "1mo"), (25, "1mo"),      # 中等缺口 → 1mo（旧实现此处只抓 5d 会留洞）
    (26, None), (None, None),     # 大缺口/无数据 → 全量 730d
])
def test_period_for_hourly(gap, expect):
    assert _period_for(gap, H_TIERS) == expect
