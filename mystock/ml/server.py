"""ML 回溯的实时访问入口（独立 Flask，只读 ML 库）。

为什么不并进 mystock/web/app.py：CLAUDE.md 的架构边界要求 ML 全程不碰 web
（web 只读生产库 mystock.db，ML 只读自己的 mystock_ml.db）。把 ML 查询塞进
web 应用会让两个库在同一进程里混用，破坏该边界。故起独立服务、独立端口。

与每日 HTML 报告的分工：报告是**当日快照**（cron 产出、可发布、可离线存档）；
本服务是**实时查询**——参数一改立刻按当前库重算，适合调参与临时探查。

接口：
  GET /                                     页面（按股 tab + 逐日明细）
  GET /api/strategy?codes=...&days=30        JSON：按预测区间挂单的回溯结果

运行：bash scripts/ml.sh serve   （默认 127.0.0.1:8899）
"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from . import config as mlcfg
from .strategy import run_many

app = Flask(__name__)

DEFAULT_CODES = ["US.NVDA", "US.TSLA", "HK.00700", "HK.09988"]
MAX_DAYS = 400


def _parse_codes(raw: str) -> list[str]:
    """解析 ?codes=US.NVDA,HK.00700；非法/未知代码丢弃，空则回退默认。

    只接受 config.TARGETS 内的代码——库里没有其他标的的预测与 1h bars，
    放行只会得到空结果，不如直接挡掉。
    """
    if not raw:
        return DEFAULT_CODES
    want = [c.strip().upper() for c in raw.split(",") if c.strip()]
    ok = [c for c in want if c in mlcfg.TARGETS]
    return ok or DEFAULT_CODES


@app.route("/api/strategy")
def api_strategy():
    """按预测区间挂单的回溯（实时按库计算，不读缓存/不落盘）。

    参数：codes=逗号分隔富途代码（默认 4 支）、days=回溯交易日数（默认 30）。
    """
    codes = _parse_codes(request.args.get("codes", ""))
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    days = max(1, min(days, MAX_DAYS))
    results = run_many(codes, days)
    return jsonify({
        "days": days,
        "codes": codes,
        "results": results,
        "note": ("盈亏未扣佣金/印花税/平台费/融券成本/滑点；"
                 "假设现金与持仓充足，净持仓可为负（裸空）。"),
    })


@app.route("/")
def index():
    return render_template("ml_strategy.html",
                           default_codes=",".join(DEFAULT_CODES))


def main() -> None:
    host = os.environ.get("MYSTOCK_ML_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("MYSTOCK_ML_WEB_PORT", "8899"))
    if not mlcfg.ML_DB_PATH.exists():
        print(f"[warn] ML 库不存在: {mlcfg.ML_DB_PATH}\n"
              f"       请先运行 `bash scripts/ml.sh data`。")
    print(f"myStock ML 回溯服务启动: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
