"""code_map 纯函数单元测试（含 HK / US 实测样例）。"""
from mystock.code_map import (
    futu_to_yf,
    yf_to_futu,
    normalize_hk_number,
    futu_market_of,
)


def test_futu_to_yf_hk():
    # 港股：去 HK. → 4 位 → 加 .HK
    assert futu_to_yf("HK.00700") == "0700.HK"   # 腾讯
    assert futu_to_yf("HK.09988") == "9988.HK"   # 阿里
    assert futu_to_yf("HK.00005") == "0005.HK"   # 汇丰
    assert futu_to_yf("HK.01810") == "1810.HK"   # 小米


def test_futu_to_yf_us():
    assert futu_to_yf("US.AAPL") == "AAPL"
    assert futu_to_yf("US.NVDA") == "NVDA"
    assert futu_to_yf("us.tsla") == "TSLA"


def test_normalize_hk_number():
    assert normalize_hk_number("00700") == "0700"
    assert normalize_hk_number("0700") == "0700"
    assert normalize_hk_number("700") == "0700"
    assert normalize_hk_number("09988") == "9988"
    assert normalize_hk_number("9988") == "9988"
    # 超过 4 位保持
    assert normalize_hk_number("100000") == "100000"


def test_yf_to_futu_roundtrip_hk():
    assert yf_to_futu("0700.HK") == "HK.00700"
    assert yf_to_futu("9988.HK") == "HK.09988"
    # 往返一致性
    assert futu_to_yf(yf_to_futu("0700.HK")) == "0700.HK"


def test_yf_to_futu_us():
    assert yf_to_futu("AAPL") == "US.AAPL"


def test_market_of():
    assert futu_market_of("HK.00700") == "HK"
    assert futu_market_of("US.AAPL") == "US"
    assert futu_market_of("SH.600000") == ""
    assert futu_market_of("garbage") == ""


def test_unrecognized_passthrough():
    assert futu_to_yf("600000") == "600000"
