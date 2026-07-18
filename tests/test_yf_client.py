"""yf_client 纯函数测试（不触网）。"""
from unittest import mock

from mystock.collectors.yf_client import _end_inclusive, _is_rate_limited, _retry_sleep


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


def test_is_rate_limited_detects_429_variants():
    assert _is_rate_limited(Exception("Too Many Requests. Rate limited. Try after a while."))
    assert _is_rate_limited(Exception("HTTP 429"))
    assert _is_rate_limited(Exception("rate limit exceeded"))
    # 普通错误不算限频
    assert not _is_rate_limited(Exception("Connection reset by peer"))
    assert not _is_rate_limited(ValueError("bad date"))


def test_retry_sleep_backoff_longer_when_rate_limited():
    # 限频用指数退避（4/8/16s），远长于普通线性退避（1/2/3s）
    with mock.patch("mystock.collectors.yf_client.time.sleep") as sl:
        _retry_sleep(1, 1.0, rate_limited=False)
        _retry_sleep(2, 1.0, rate_limited=False)
        assert [c.args[0] for c in sl.call_args_list] == [1.0, 2.0]
    with mock.patch("mystock.collectors.yf_client.time.sleep") as sl:
        _retry_sleep(1, 1.0, rate_limited=True)
        _retry_sleep(2, 1.0, rate_limited=True)
        _retry_sleep(3, 1.0, rate_limited=True)
        assert [c.args[0] for c in sl.call_args_list] == [4.0, 8.0, 16.0]
