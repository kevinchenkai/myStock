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
