"""yf_client 纯函数测试（不触网）。"""
from mystock.collectors.yf_client import _end_inclusive


def test_end_inclusive_adds_one_day():
    # yfinance end 排他，需 +1 天纳入 end 当天
    assert _end_inclusive("2026-06-29") == "2026-06-30"
    assert _end_inclusive("2025-12-31") == "2026-01-01"  # 跨年
    assert _end_inclusive("2024-02-28") == "2024-02-29"  # 闰年


def test_end_inclusive_none_passthrough():
    # None（到今天）保持 None，交给 yfinance 默认行为
    assert _end_inclusive(None) is None
    assert _end_inclusive("") == ""


def test_end_inclusive_tolerates_datetime_string():
    # 带时间部分也只取日期再 +1
    assert _end_inclusive("2026-06-29 15:30:00") == "2026-06-30"


def test_end_inclusive_bad_format_passthrough():
    # 非法格式原样返回（让 yfinance 自行报错）
    assert _end_inclusive("not-a-date") == "not-a-date"
