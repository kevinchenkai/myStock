"""Flask 应用入口（server.sh 调用）。

只读 SQLite，渲染页面与提供 JSON 查询接口。不触发任何抓取。

接口：
  GET /                        页面（持仓 / 交易 Tab + 个股下钻）
  GET /api/positions           最新快照的持仓
  GET /api/orders?code=...     历史订单（可按 code 过滤）
  GET /api/deals?code=...      历史成交（可按 code 过滤）
  GET /api/quotes?code=...&start=...&end=...   某代码日线
  GET /api/stock/<code>        聚合：该股票行情 + 订单 + 成交
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from ..config import CONFIG

app = Flask(__name__)


def get_db() -> sqlite3.Connection:
    path = CONFIG.db_path
    if not Path(path).exists():
        raise FileNotFoundError(
            f"数据库不存在: {path}。请先运行 `bash scripts/init.sh` 初始化。"
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_list(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


@app.errorhandler(FileNotFoundError)
def handle_no_db(e):
    return jsonify({"error": str(e)}), 503


# ---------------- 页面 ----------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------- API ----------------
@app.route("/api/positions")
def api_positions():
    conn = get_db()
    try:
        # 取最新快照日期
        cur = conn.execute("SELECT MAX(snapshot_date) AS d FROM positions")
        row = cur.fetchone()
        latest = row["d"] if row else None
        if not latest:
            return jsonify({"snapshot_date": None, "positions": []})
        cur = conn.execute(
            "SELECT * FROM positions WHERE snapshot_date = ? ORDER BY market, code",
            (latest,),
        )
        return jsonify({"snapshot_date": latest, "positions": rows_to_list(cur)})
    finally:
        conn.close()


@app.route("/api/orders")
def api_orders():
    code = request.args.get("code")
    conn = get_db()
    try:
        if code:
            cur = conn.execute(
                "SELECT * FROM orders WHERE code = ? ORDER BY create_time DESC", (code,)
            )
        else:
            cur = conn.execute("SELECT * FROM orders ORDER BY create_time DESC")
        return jsonify(rows_to_list(cur))
    finally:
        conn.close()


@app.route("/api/deals")
def api_deals():
    code = request.args.get("code")
    conn = get_db()
    try:
        if code:
            cur = conn.execute(
                "SELECT * FROM deals WHERE code = ? ORDER BY create_time DESC", (code,)
            )
        else:
            cur = conn.execute("SELECT * FROM deals ORDER BY create_time DESC")
        return jsonify(rows_to_list(cur))
    finally:
        conn.close()


@app.route("/api/quotes")
def api_quotes():
    code = request.args.get("code")
    start = request.args.get("start")
    end = request.args.get("end")
    if not code:
        return jsonify({"error": "缺少 code 参数"}), 400
    conn = get_db()
    try:
        sql = "SELECT * FROM daily_quotes WHERE futu_code = ?"
        params: list = [code]
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        sql += " ORDER BY date ASC"
        cur = conn.execute(sql, params)
        return jsonify(rows_to_list(cur))
    finally:
        conn.close()


@app.route("/api/stock/<path:code>")
def api_stock(code: str):
    """聚合某只股票：行情 + 订单 + 成交。"""
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT * FROM daily_quotes WHERE futu_code = ? ORDER BY date ASC", (code,)
        )
        quotes = rows_to_list(cur)

        cur = conn.execute(
            "SELECT * FROM orders WHERE code = ? ORDER BY create_time DESC", (code,)
        )
        orders = rows_to_list(cur)

        cur = conn.execute(
            "SELECT * FROM deals WHERE code = ? ORDER BY create_time DESC", (code,)
        )
        deals = rows_to_list(cur)

        # 名称：优先持仓/订单/成交里的 name
        name = None
        for table in ("positions", "orders", "deals"):
            cur = conn.execute(
                f"SELECT name FROM {table} WHERE code = ? AND name IS NOT NULL LIMIT 1",
                (code,),
            )
            r = cur.fetchone()
            if r and r["name"]:
                name = r["name"]
                break

        return jsonify(
            {
                "code": code,
                "name": name,
                "quotes": quotes,
                "orders": orders,
                "deals": deals,
            }
        )
    finally:
        conn.close()


def main() -> None:
    host = CONFIG.web_host
    port = CONFIG.web_port
    # 启动前检查数据库
    if not Path(CONFIG.db_path).exists():
        print(
            f"[warn] 数据库不存在: {CONFIG.db_path}\n"
            f"       页面可打开，但数据为空。请先运行 `bash scripts/init.sh`。"
        )
    print(f"myStock Web 服务启动: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
