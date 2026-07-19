"""futu_client 账户资金规整的纯函数测试（不触网 / 不连 OpenD）。

样例金额为虚构脱敏值，仅测规整逻辑，不含任何真实账户数据。
"""
import pandas as pd

from mystock.collectors.futu_client import fund_row, _num, snapshot_fields


def test_num_coerces_na_and_strings():
    # 富途对缺失数值返回字符串 'N/A' → None
    assert _num("N/A") is None
    assert _num("n/a") is None
    assert _num("") is None
    assert _num(None) is None
    # 正常数值 / 数字字符串 → float
    assert _num(12.5) == 12.5
    assert _num("100") == 100.0
    # 不可解析 → None（不抛异常）
    assert _num("abc") is None


def test_fund_row_normalizes_consolidated_snapshot():
    # 单行综合账户（虚构脱敏值）
    df = pd.DataFrame([{
        "currency": "HKD",
        "total_assets": 1000000.0,
        "market_val": 700000.0,
        "cash": 300000.0,
        "frozen_cash": 0.0,
        "avl_withdrawal_cash": 300000.0,
        "power": 1500000.0,
        "hkd_assets": 800000.0,
        "hk_cash": 250000.0,
        "usd_assets": 25000.0,
        "us_cash": 6400.0,
        "risk_status": "LEVEL3",
        # 富途常见的 'N/A' 占位字段（此处不入库，仅确认不干扰）
        "unrealized_pl": "N/A",
    }])
    row = fund_row(df, "2026-07-18", "2026-07-18 20:00:00")
    assert row["snapshot_date"] == "2026-07-18"
    assert row["report_currency"] == "HKD"
    assert row["total_assets"] == 1000000.0
    assert row["market_val"] == 700000.0
    assert row["hkd_assets"] == 800000.0
    assert row["usd_assets"] == 25000.0
    assert row["risk_status"] == "LEVEL3"
    assert row["updated_at"] == "2026-07-18 20:00:00"
    # 一致性：持仓市值不应超过总资产
    assert row["market_val"] <= row["total_assets"]


def test_fund_row_handles_na_numeric_fields():
    # 某些数值字段为 'N/A' 时应转 None，不抛异常
    df = pd.DataFrame([{
        "currency": "HKD",
        "total_assets": 500000.0,
        "market_val": "N/A",
        "cash": 500000.0,
        "power": "N/A",
        "risk_status": "LEVEL1",
    }])
    row = fund_row(df, "2026-07-18", "now")
    assert row["market_val"] is None
    assert row["power"] is None
    assert row["cash"] == 500000.0


def test_fund_row_empty_returns_none():
    assert fund_row(pd.DataFrame(), "2026-07-18", "now") is None
    assert fund_row(None, "2026-07-18", "now") is None


# ---------------- 行情快照盘面字段 ----------------

def test_snapshot_fields_extracts_pan_mian():
    df = pd.DataFrame([
        {"code": "HK.00700", "turnover_rate": 0.399, "amplitude": 6.364,
         "highest52weeks_price": 677.70, "lowest52weeks_price": 411.0,
         "pe_ratio": 16.86},  # 额外列不入库
        {"code": "US.AAPL", "turnover_rate": 0.433, "amplitude": 1.797,
         "highest52weeks_price": 334.99, "lowest52weeks_price": 200.70},
    ])
    rows = snapshot_fields(df, "2026-07-18 20:00:00")
    assert len(rows) == 2
    r0 = rows[0]
    # 只含主键 + 盘面列 + snap_synced_at（不含 pe_ratio 等）
    assert set(r0.keys()) == {
        "futu_code", "turnover_rate", "amplitude",
        "week52_high", "week52_low", "snap_synced_at",
    }
    assert r0["futu_code"] == "HK.00700"
    assert r0["turnover_rate"] == 0.399
    assert r0["amplitude"] == 6.364
    assert r0["week52_high"] == 677.70
    assert r0["week52_low"] == 411.0
    assert r0["snap_synced_at"] == "2026-07-18 20:00:00"


def test_snapshot_fields_handles_na_and_missing_code():
    df = pd.DataFrame([
        {"code": "", "turnover_rate": 1.0},                  # 无 code → 跳过
        {"code": "HK.09988", "turnover_rate": "N/A",
         "highest52weeks_price": 120.0, "lowest52weeks_price": "N/A"},
    ])
    rows = snapshot_fields(df, "now")
    assert len(rows) == 1                     # 空 code 行被跳过
    r = rows[0]
    assert r["futu_code"] == "HK.09988"
    assert r["turnover_rate"] is None         # 'N/A' → None
    assert r["week52_high"] == 120.0
    assert r["week52_low"] is None


def test_snapshot_fields_empty():
    assert snapshot_fields(pd.DataFrame(), "now") == []
