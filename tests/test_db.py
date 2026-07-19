"""db 层幂等性 / 覆盖语义测试（使用临时 SQLite 文件）。"""
import os
import tempfile

from mystock import db


def _conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    return db.get_connection(path), path


def test_orders_upsert_idempotent():
    conn, path = _conn()
    try:
        row = {
            "order_id": "O1", "market": "US", "code": "US.AAPL", "name": "Apple",
            "trd_side": "BUY", "order_type": "NORMAL", "order_status": "FILLED_ALL",
            "price": 100.0, "qty": 10, "dealt_qty": 10, "dealt_avg_price": 100.0,
            "create_time": "2025-01-02 10:00:00", "updated_time": "2025-01-02 10:01:00",
            "currency": "USD", "raw_json": "{}", "synced_at": "now",
        }
        db.upsert_orders(conn, [row])
        # 再次写入同一 order_id（状态变化）
        row2 = dict(row, order_status="CANCELLED")
        db.upsert_orders(conn, [row2])
        cur = conn.execute("SELECT COUNT(*) AS c, order_status FROM orders")
        r = cur.fetchone()
        assert r["c"] == 1
        assert r["order_status"] == "CANCELLED"
    finally:
        conn.close()
        os.remove(path)


def test_quotes_upsert_overwrites_same_day():
    conn, path = _conn()
    try:
        q = {
            "yf_symbol": "AAPL", "futu_code": "US.AAPL", "date": "2025-01-02",
            "open": 1, "high": 2, "low": 0.5, "close": 1.5, "adj_close": 1.5,
            "volume": 1000, "dividends": 0, "stock_splits": 0, "synced_at": "now",
        }
        db.upsert_quotes(conn, [q])
        db.upsert_quotes(conn, [dict(q, close=9.9)])
        cur = conn.execute("SELECT COUNT(*) AS c, close FROM daily_quotes")
        r = cur.fetchone()
        assert r["c"] == 1
        assert r["close"] == 9.9
    finally:
        conn.close()
        os.remove(path)


def test_fx_rates_upsert_overwrites_same_day():
    conn, path = _conn()
    try:
        row = {
            "pair": "USDCNY", "date": "2025-01-02",
            "open": 7.30, "high": 7.31, "low": 7.29, "close": 7.30,
            "synced_at": "now",
        }
        db.upsert_fx_rates(conn, [row])
        # 当天重抓覆盖（close 修正）
        db.upsert_fx_rates(conn, [dict(row, close=7.35)])
        cur = conn.execute("SELECT COUNT(*) AS c, close FROM fx_rates WHERE pair='USDCNY'")
        r = cur.fetchone()
        assert r["c"] == 1
        assert r["close"] == 7.35
    finally:
        conn.close()
        os.remove(path)


def test_position_snapshot_replace():
    conn, path = _conn()
    try:
        day = "2025-06-21"
        rows = [
            {"snapshot_date": day, "market": "US", "code": "US.AAPL", "name": "Apple",
             "qty": 10, "can_sell_qty": 10, "cost_price": 1, "nominal_price": 1,
             "market_val": 10, "pl_val": 0, "pl_ratio": 0, "currency": "USD",
             "updated_at": "now"},
            {"snapshot_date": day, "market": "US", "code": "US.TSLA", "name": "Tesla",
             "qty": 5, "can_sell_qty": 5, "cost_price": 1, "nominal_price": 1,
             "market_val": 5, "pl_val": 0, "pl_ratio": 0, "currency": "USD",
             "updated_at": "now"},
        ]
        db.replace_position_snapshot(conn, day, rows)
        # 第二次抓取当天，只剩 AAPL（TSLA 已清仓）→ 不应残留 TSLA
        db.replace_position_snapshot(conn, day, [rows[0]])
        cur = conn.execute("SELECT code FROM positions WHERE snapshot_date = ?", (day,))
        codes = [r["code"] for r in cur.fetchall()]
        assert codes == ["US.AAPL"]
    finally:
        conn.close()
        os.remove(path)


def test_reset_quote_skiplist_all_and_selective():
    conn, path = _conn()
    try:
        for code in ("US.A", "US.B", "US.C"):
            db.record_quote_empty(conn, code, code.split(".")[1])
        # 只重置指定代码
        n = db.reset_quote_skiplist(conn, ["US.A"])
        assert n == 1
        left = {r["futu_code"] for r in conn.execute("SELECT futu_code FROM quote_skiplist")}
        assert left == {"US.B", "US.C"}
        # 清空全部
        n = db.reset_quote_skiplist(conn)
        assert n == 2
        assert conn.execute("SELECT COUNT(*) c FROM quote_skiplist").fetchone()["c"] == 0
    finally:
        conn.close()
        os.remove(path)


def test_purge_code_removes_from_all_tables():
    conn, path = _conn()
    try:
        # 在 orders / deals / skiplist 里放 US.YY 的记录
        db.upsert_orders(conn, [{
            "order_id": "O_YY", "market": "US", "code": "US.YY", "name": "YY",
            "trd_side": "SELL", "order_type": None, "order_status": None,
            "price": 50, "qty": 10, "dealt_qty": 10, "dealt_avg_price": 50,
            "create_time": "2025-01-24 10:00:00", "updated_time": None,
            "currency": "USD", "raw_json": "{}", "synced_at": "now",
        }])
        db.upsert_deals(conn, [{
            "deal_id": "D_YY", "order_id": "O_YY", "market": "US", "code": "US.YY",
            "name": "YY", "trd_side": "SELL", "price": 50, "qty": 10,
            "create_time": "2025-01-24 10:00:00", "counter_broker_id": None,
            "raw_json": "{}", "synced_at": "now",
        }])
        db.record_quote_empty(conn, "US.YY", "YY")
        # 另放一条无关代码，确认不被误删
        db.record_quote_empty(conn, "US.KEEP", "KEEP")

        deleted = db.purge_code(conn, "US.YY")
        assert deleted["orders"] == 1
        assert deleted["deals"] == 1
        assert deleted["quote_skiplist"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM orders WHERE code='US.YY'").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM deals WHERE code='US.YY'").fetchone()["c"] == 0
        # 无关代码仍在
        assert conn.execute("SELECT COUNT(*) c FROM quote_skiplist WHERE futu_code='US.KEEP'").fetchone()["c"] == 1
    finally:
        conn.close()
        os.remove(path)


def test_account_funds_upsert_overwrites_same_day():
    conn, path = _conn()
    try:
        row = {
            "snapshot_date": "2026-07-18", "report_currency": "HKD",
            "total_assets": 1000000.0, "market_val": 700000.0, "cash": 300000.0,
            "frozen_cash": 0.0, "avl_withdrawal_cash": 300000.0, "power": 1500000.0,
            "hkd_assets": 800000.0, "hk_cash": 250000.0,
            "usd_assets": 25000.0, "us_cash": 6400.0,
            "risk_status": "LEVEL3", "updated_at": "now",
        }
        db.upsert_account_funds(conn, [row])
        # 当天重抓覆盖（总资产变化）
        db.upsert_account_funds(conn, [dict(row, total_assets=1100000.0)])
        cur = conn.execute("SELECT COUNT(*) AS c, total_assets FROM account_funds")
        r = cur.fetchone()
        assert r["c"] == 1
        assert r["total_assets"] == 1100000.0
    finally:
        conn.close()
        os.remove(path)


def test_column_migration_adds_missing_snapshot_columns():
    # 模拟旧库：手建一个缺盘面列的 stock_profiles，再跑 init_db 应自动补齐
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = db.get_connection(path)
        conn.execute(
            "CREATE TABLE stock_profiles (futu_code TEXT PRIMARY KEY, long_name TEXT, synced_at TEXT)"
        )
        conn.commit()
        conn.close()
        # init_db 应通过列迁移补齐 turnover_rate/amplitude/week52_high/week52_low/snap_synced_at
        db.init_db(path)
        conn = db.get_connection(path)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(stock_profiles)")}
        for c in ("turnover_rate", "amplitude", "week52_high", "week52_low", "snap_synced_at"):
            assert c in cols, f"缺列 {c}"
        # 旧列保留
        assert "long_name" in cols
        conn.close()
    finally:
        os.remove(path)


def test_all_traded_codes_dedup():
    conn, path = _conn()
    try:
        db.upsert_orders(conn, [{
            "order_id": "O1", "market": "US", "code": "US.AAPL", "name": None,
            "trd_side": "BUY", "order_type": None, "order_status": None,
            "price": None, "qty": None, "dealt_qty": None, "dealt_avg_price": None,
            "create_time": None, "updated_time": None, "currency": None,
            "raw_json": "{}", "synced_at": "now",
        }])
        db.upsert_deals(conn, [{
            "deal_id": "D1", "order_id": "O1", "market": "HK", "code": "HK.00700",
            "name": None, "trd_side": "BUY", "price": None, "qty": None,
            "create_time": None, "counter_broker_id": None, "raw_json": "{}",
            "synced_at": "now",
        }])
        codes = db.all_traded_codes(conn)
        assert codes == ["HK.00700", "US.AAPL"]
    finally:
        conn.close()
        os.remove(path)
