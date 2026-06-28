"""Flask 应用入口（server.sh 调用）。

只读 SQLite，渲染页面与提供 JSON 查询接口。不触发任何抓取。

接口：
  GET /                        页面（持仓 / 交易 Tab + 个股下钻）
  GET /api/positions           最新快照的持仓
  GET /api/orders?code=...     历史订单（可按 code 过滤）
  GET /api/deals?code=...      历史成交（可按 code 过滤）
  GET /api/quotes?code=...&start=...&end=...   某代码日线
  GET /api/stock/<code>        聚合：该股票行情 + 订单 + 成交
  GET /api/stock/<code>/profile    通用信息（公司/估值，读自 stock_profiles）
  GET /api/stock/<code>/analysis   交易复盘（成交明细 + FIFO 回合 + 复盘统计）
  GET /api/pnl                 交易盈亏（已实现，每股一行）
  GET /api/finance?year=2026   年度财务统计（现金流口径，按美股/港股分别汇总）
  GET /api/fx?pair=USDCNY      外汇日线（默认美元兑人民币）
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from ..config import CONFIG
from .. import db as dbmod
from ..pnl import compute_pnl, analyze_stock, yearly_finance

app = Flask(__name__)

# stock_profiles 列名 -> 前端展示用中文标签（顺序即展示顺序）
_PROFILE_LABELS = [
    ("long_name", "公司名"),
    ("sector", "板块"),
    ("industry", "行业"),
    ("exchange", "交易所"),
    ("market_cap_mm", "市值(百万)"),
    ("shares_mm", "流通股本(百万)"),
    ("trailing_pe", "市盈率(TTM)"),
    ("forward_pe", "预期市盈率"),
    ("price_to_book", "市净率"),
    ("trailing_eps", "每股收益(TTM)"),
    ("dividend_yield", "股息率%"),
    ("beta", "Beta"),
    ("target_mean_price", "目标均价"),
    ("recommendation", "分析师评级"),
    ("currency", "货币"),
    ("website", "官网"),
]


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


@app.route("/api/pnl")
def api_pnl():
    """交易盈亏（已实现）：按成交数据，移动平均成本法 + 持仓成本兜底。"""
    conn = get_db()
    try:
        cur = conn.execute("SELECT * FROM deals")
        deals = rows_to_list(cur)
        # 成本兜底：positions 最新快照里每只股的 cost_price
        cur = conn.execute("SELECT MAX(snapshot_date) AS d FROM positions")
        row = cur.fetchone()
        latest = row["d"] if row else None
        cost_fallback: dict = {}
        if latest:
            cur = conn.execute(
                "SELECT code, cost_price FROM positions WHERE snapshot_date = ?",
                (latest,),
            )
            cost_fallback = {r["code"]: r["cost_price"] for r in cur.fetchall()}
        return jsonify({"rows": compute_pnl(deals, cost_fallback)})
    finally:
        conn.close()


@app.route("/api/finance")
def api_finance():
    """年度财务统计：现金流口径（当年卖出额 - 买入额），按美股/港股分别汇总。"""
    from datetime import datetime
    year = request.args.get("year") or str(datetime.now().year)
    conn = get_db()
    try:
        cur = conn.execute("SELECT * FROM deals")
        deals = rows_to_list(cur)
        return jsonify(yearly_finance(deals, year))
    finally:
        conn.close()


@app.route("/api/stock/<code>/analysis")
def api_stock_analysis(code: str):
    """单只股票交易复盘：成交明细 + FIFO 配对回合 + 复盘统计 + 客观观察。"""
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT * FROM deals WHERE code = ? ORDER BY create_time ASC", (code,)
        )
        deals = rows_to_list(cur)
        # 成本兜底：positions 最新快照该股 cost_price
        cur = conn.execute("SELECT MAX(snapshot_date) AS d FROM positions")
        row = cur.fetchone()
        latest = row["d"] if row else None
        fb = None
        if latest:
            cur = conn.execute(
                "SELECT cost_price FROM positions WHERE snapshot_date = ? AND code = ?",
                (latest, code),
            )
            r = cur.fetchone()
            fb = r["cost_price"] if r else None
        analysis = analyze_stock(deals, fb)
        return jsonify({"code": code, "deals": deals, "analysis": analysis})
    finally:
        conn.close()


@app.route("/api/fx")
def api_fx():
    """外汇日线（默认 USDCNY，美元兑人民币）。

    返回 {pair, rows:[{date, open, high, low, close}, ...]}，按日期升序。
    """
    pair = request.args.get("pair", "USDCNY")
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT date, open, high, low, close FROM fx_rates "
            "WHERE pair = ? ORDER BY date ASC",
            (pair,),
        )
        return jsonify({"pair": pair, "rows": rows_to_list(cur)})
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


@app.route("/api/stock/<code>")
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


@app.route("/api/stock/<code>/profile")
def api_stock_profile(code: str):
    """单只股票的通用信息（公司 / 估值），读取自 db.stock_profiles。

    数据由 init.sh / update.sh 抓取入库；此处仅读库，不触发网络。
    """
    conn = get_db()
    try:
        row = dbmod.get_profile(conn, code)
    finally:
        conn.close()
    if not row:
        return jsonify({"code": code, "profile": None})
    profile = {label: row.get(col) for col, label in _PROFILE_LABELS}
    return jsonify({"code": code, "profile": profile})


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
