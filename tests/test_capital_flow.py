"""资金流向规整的纯函数测试（不触网 / 不连 OpenD）。

样例金额为虚构值，仅测规整逻辑。
"""
import os
import tempfile

import pandas as pd

from mystock import db
from mystock.collectors.futu_client import capital_flow_rows


def test_capital_flow_rows_normalizes_daily():
    df = pd.DataFrame([
        {"capital_flow_item_time": "2026-07-20 00:00:00",
         "in_flow": 3.6e8, "main_in_flow": 4.3e8, "super_in_flow": 5.4e8,
         "big_in_flow": -1.1e8, "mid_in_flow": -5.8e7, "sml_in_flow": -1.4e7,
         "last_valid_time": "N/A"},   # 额外列不入库
        {"capital_flow_item_time": "2026-07-21 00:00:00",
         "in_flow": -8.0e8, "main_in_flow": -1.6e8, "super_in_flow": 1.2e8,
         "big_in_flow": -2.9e8, "mid_in_flow": -3.4e8, "sml_in_flow": -2.8e8},
    ])
    rows = capital_flow_rows(df, "HK.00700", "2026-07-21 20:00:00")
    assert len(rows) == 2
    r0 = rows[0]
    # 只含表列（不含 last_valid_time 等原始列）
    assert set(r0.keys()) == {
        "code", "date", "in_flow", "main_in_flow", "super_in_flow",
        "big_in_flow", "mid_in_flow", "sml_in_flow", "synced_at",
    }
    assert r0["code"] == "HK.00700"
    # 'YYYY-MM-DD HH:MM:SS' → 'YYYY-MM-DD'（与 daily_quotes.date 对齐可 JOIN）
    assert r0["date"] == "2026-07-20"
    assert r0["main_in_flow"] == 4.3e8
    assert r0["synced_at"] == "2026-07-21 20:00:00"
    # 净流出为负值，符号需保留
    assert rows[1]["main_in_flow"] < 0


def test_capital_flow_rows_handles_na_and_missing_time():
    df = pd.DataFrame([
        {"capital_flow_item_time": "", "in_flow": 1.0},          # 无时间 → 跳过（无主键）
        {"capital_flow_item_time": "2026-07-21 00:00:00",
         "in_flow": "N/A", "main_in_flow": 1.0e7},
    ])
    rows = capital_flow_rows(df, "US.AAPL", "now")
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-07-21"
    assert rows[0]["in_flow"] is None        # 'N/A' → None
    assert rows[0]["main_in_flow"] == 1.0e7
    # 富途未返回的档位列 → None，不抛 KeyError
    assert rows[0]["sml_in_flow"] is None


def test_capital_flow_rows_empty():
    assert capital_flow_rows(pd.DataFrame(), "HK.00700", "now") == []
    assert capital_flow_rows(None, "HK.00700", "now") == []


def test_capital_flow_upsert_overwrites_same_day():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    conn = db.get_connection(path)
    try:
        row = {
            "code": "HK.00700", "date": "2026-07-21",
            "in_flow": 1.0e8, "main_in_flow": 2.0e8, "super_in_flow": 3.0e8,
            "big_in_flow": -1.0e8, "mid_in_flow": -5.0e7, "sml_in_flow": -2.0e7,
            "synced_at": "now",
        }
        db.upsert_capital_flow(conn, [row])
        # 盘中重抓：同 code+date 覆盖，不新增行
        db.upsert_capital_flow(conn, [dict(row, main_in_flow=9.9e8)])
        cur = conn.execute("SELECT COUNT(*) AS c, main_in_flow FROM capital_flow")
        r = cur.fetchone()
        assert r["c"] == 1
        assert r["main_in_flow"] == 9.9e8
        # 不同日期各占一行
        db.upsert_capital_flow(conn, [dict(row, date="2026-07-20")])
        assert conn.execute("SELECT COUNT(*) c FROM capital_flow").fetchone()["c"] == 2
    finally:
        conn.close()
        os.remove(path)


def test_purge_code_removes_capital_flow():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    conn = db.get_connection(path)
    try:
        db.upsert_capital_flow(conn, [{
            "code": "US.GONE", "date": "2026-07-21",
            "in_flow": 1.0, "main_in_flow": 1.0, "super_in_flow": 1.0,
            "big_in_flow": 1.0, "mid_in_flow": 1.0, "sml_in_flow": 1.0,
            "synced_at": "now",
        }])
        deleted = db.purge_code(conn, "US.GONE")
        assert deleted["capital_flow"] == 1
        assert conn.execute(
            "SELECT COUNT(*) c FROM capital_flow WHERE code='US.GONE'"
        ).fetchone()["c"] == 0
    finally:
        conn.close()
        os.remove(path)
