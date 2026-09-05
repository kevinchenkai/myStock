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
  GET /api/stock/<code>/capital-flow?days=60  日频资金流向（富途，本币）
  GET /api/pnl                 交易盈亏（已实现，每股一行）
  GET /api/finance?year=2026   年度财务统计（现金流口径，按美股/港股分别汇总）
  GET /api/asset-trend         资产趋势（历史快照聚合，按市场分组的市值/浮盈时序）
  GET /api/account-funds       账户资金（最新快照 + 历史净资产序列，HK+US 综合账户）
  GET /api/fx?pair=USDCNY      外汇日线（默认美元兑人民币）
  GET /api/ml/strategy?codes=&days=30   ML 预测区间挂单回溯（只读 ML 库，实时计算）

ML 接口只**读** data/ml/mystock_ml.db（CLAUDE.md 架构约定），绝不写、不触发训练/抓取。
ML 库不存在时该接口返回 503，其余页面不受影响。
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
from .ml_api import bp as ml_blueprint
app.register_blueprint(ml_blueprint)

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
    # 盘面增量字段（富途快照，yfinance 缺）。52 周高低为本币价格。
    ("turnover_rate", "换手率%"),
    ("amplitude", "振幅%"),
    ("week52_high", "52周最高"),
    ("week52_low", "52周最低"),
]


def get_db() -> sqlite3.Connection:
    path = app.config.get("DB_PATH", CONFIG.db_path)
    if not Path(path).exists():
        raise FileNotFoundError(
            f"数据库不存在: {path}。请先运行 `bash scripts/init.sh` 初始化。"
        )
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_list(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


@app.errorhandler(FileNotFoundError)
def handle_no_db(e):
    return jsonify({"error": "数据库不可用，请联系维护者检查初始化状态。"}), 503


# ---------------- 页面 ----------------
@app.route("/ml-next", strict_slashes=False)
def ml_next():
    return render_template("ml_next.html")


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


# ---- ML 接口（只读 data/ml/mystock_ml.db；不写、不触发训练/抓取）----
ML_DEFAULT_CODES = ["US.NVDA", "US.TSLA", "HK.00700", "HK.09988"]
ML_MAX_DAYS = 400


@app.route("/api/ml/strategy")
def api_ml_strategy():
    """按 ML 预测区间挂单的回溯（实时按 ML 库计算）。

    策略：基准日 T 收盘后拿到 [L̂, Ĥ] → 次一交易日同时挂限价买 L̂ / 限价卖 Ĥ，
    各一手（美股 10 股 / 港股 100 股）。撮合走 1h K 线，假设现金与持仓充足。

    收益率没有天然本金，故给三个分母：成交额收益率（total/平均单边成交额）、
    占款收益率（total/峰值 |净持仓|×收盘）、现金收益率（total/实际垫付现金，
    即逐日累计现金余额的最低点），另附线性年化。口径详见 ml/strategy.py。

    参数：codes=逗号分隔富途代码（默认 4 支）、days=回溯交易日数（默认 30，上限 400）。
    计算在 mystock.ml.strategy（纯函数）里，本处只做参数校验与透传。

    延迟导入 ml 模块：它会拖起 pandas/ml 依赖链，且 ML 库可能未初始化——
    放模块顶层会让整个 web 应用在没跑过 ml.sh 的环境里起不来。
    """
    if request.args.get("mode", "legacy") == "inventory":
        from .ml_api import compare
        try:
            return compare()
        except ValueError as e:
            return jsonify({'schema_version':2,'error':str(e)}),400
        except (FileNotFoundError, sqlite3.OperationalError):
            return jsonify({'schema_version':2,'error':'ML database/schema unavailable'}),503
    if request.args.get("mode", "legacy") != "legacy":
        return jsonify({"error":"invalid mode"}),400
    try:
        from ..ml import config as mlcfg
        from ..ml.strategy import aggregate_returns, run_many
    except ImportError as e:  # ML 子包依赖缺失
        return jsonify({"error": f"ML 模块不可用: {e}"}), 503

    ml_path = app.config.get("ML_DB_PATH", mlcfg.ML_DB_PATH)
    if not Path(ml_path).exists():
        return jsonify({
            "error": f"ML 数据库不存在: {mlcfg.ML_DB_PATH}。"
                     f"请先运行 `bash scripts/ml.sh data`。"
        }), 503

    raw = request.args.get("codes", "")
    want = [c.strip().upper() for c in raw.split(",") if c.strip()]
    # 只接受 TARGETS 内的代码——库里没有其他标的的预测与 1h bars，放行只会得到空结果
    if any(c not in mlcfg.TARGETS for c in want):
        return jsonify({"error": "invalid code"}), 400
    codes = list(dict.fromkeys(want)) or ML_DEFAULT_CODES
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        return jsonify({"error": "invalid days"}), 400
    if not 1 <= days <= ML_MAX_DAYS:
        return jsonify({"error": "days out of range"}), 400

    results = run_many(codes, days, db_path=ml_path)
    return jsonify({
        "schema_version": 1, "mode": "legacy",
        "days": days,
        "codes": codes,
        "targets": list(mlcfg.TARGETS),
        "results": results,
        # 组合收益率按币种分组（USD/HKD 不相加，同资产趋势口径）
        "totals": aggregate_returns(results),
        "note": ("盈亏未扣佣金/印花税/平台费/融券成本/滑点；"
                 "假设现金与持仓充足，净持仓可为负（裸空）。"),
    })


@app.route("/api/asset-trend")
def api_asset_trend():
    """资产趋势：按历史持仓快照，聚合每日每市场的总市值/浮盈/持仓数。

    跨币种不可相加，故按市场（HK→HKD、US→USD）分组返回，前端各画一条线。
    返回 {rows:[{date, market, currency, market_val, pl_val, count}, ...]}，
    按日期、市场升序。
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT snapshot_date AS date, market, "
            "  SUM(market_val) AS market_val, SUM(pl_val) AS pl_val, COUNT(*) AS count "
            "FROM positions GROUP BY snapshot_date, market "
            "ORDER BY snapshot_date ASC, market ASC"
        )
        rows = rows_to_list(cur)
        ccy = {"HK": "HKD", "US": "USD"}
        for r in rows:
            r["currency"] = ccy.get(r["market"])
        return jsonify({"rows": rows})
    finally:
        conn.close()


@app.route("/api/account-funds")
def api_account_funds():
    """账户资金：最新一条快照 + 历史序列（用于组合概览与净资产趋势）。

    账户为 HK+US 综合账户，每天一条。返回
    {latest: {...} | None, history: [{snapshot_date, total_assets, market_val, cash}, ...]}
    history 按日期升序，字段精简（趋势图用）。
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT * FROM account_funds ORDER BY snapshot_date DESC LIMIT 1"
        )
        row = cur.fetchone()
        latest = dict(row) if row else None
        cur = conn.execute(
            "SELECT snapshot_date, report_currency, total_assets, market_val, cash "
            "FROM account_funds ORDER BY snapshot_date ASC"
        )
        history = rows_to_list(cur)
        return jsonify({"latest": latest, "history": history})
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


@app.route("/api/stock/<code>/capital-flow")
def api_stock_capital_flow(code: str):
    """单只股票的日频资金流向（富途，读自 capital_flow 表）。

    默认返回最近 60 个交易日（?days= 可调），按日期升序便于直接画图。
    金额为标的本币（HK→HKD、US→USD），正=净流入。
    """
    try:
        days = int(request.args.get("days", 60))
    except ValueError:
        days = 60
    days = max(1, min(days, 400))
    conn = get_db()
    try:
        # 先按日期倒序取最近 N 条，再翻正序返回
        cur = conn.execute(
            "SELECT date, in_flow, main_in_flow, super_in_flow, big_in_flow, "
            "mid_in_flow, sml_in_flow FROM capital_flow "
            "WHERE code = ? ORDER BY date DESC LIMIT ?",
            (code, days),
        )
        rows = rows_to_list(cur)
        rows.reverse()
        return jsonify({"code": code, "rows": rows})
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

@app.route('/api/data-status')
def api_data_status():
    from . import data_status
    from ..ml.sessions import utc_now
    conn = get_db()
    try:
        return jsonify(data_status.overview(conn, app.config.get('DATA_STATUS_NOW') or utc_now()))
    finally:
        conn.close()


@app.route('/api/stock/<code>/snapshot')
def api_snapshot(code):
    from . import data_status
    from ..ml.sessions import utc_now
    if not data_status.valid_code(code):
        return jsonify(error='无效股票代码'), 400
    conn = get_db()
    try:
        return jsonify(data_status.stock_status(conn, code, app.config.get('DATA_STATUS_NOW') or utc_now()))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
