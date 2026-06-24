"""增量更新入口（update.sh 调用）。

流程：
  1) 读取 sync_log 得到上次同步点；无则回退到 start_date。
  2) 抓取自上次同步至今的新数据。
  3) 当天数据按覆盖处理（持仓快照覆盖当天、行情覆盖当天、订单/成交按主键 UPSERT）。
  4) 写 sync_log。

为保证当天数据正确覆盖且不漏单，订单/成交从「上次同步点当天 00:00」重抓
（UPSERT 天然幂等）；行情从「上次同步 range_end 当天」重抓（PK 覆盖）。
"""
from __future__ import annotations

from datetime import datetime

from .. import db
from ..config import CONFIG
from . import init_load


def _incremental_start(conn, source: str, fmt: str) -> str:
    """计算某数据源增量起点。

    取上次成功同步的 range_end 的「日期部分」当天 00:00，
    以保证当天数据被重抓覆盖；无记录则回退 start_date。
    fmt: 'datetime' -> 'YYYY-MM-DD HH:MM:SS'；'date' -> 'YYYY-MM-DD'
    """
    last = db.last_sync_point(conn, source)
    if last:
        day = last[:10]  # 取 YYYY-MM-DD
    else:
        day = CONFIG.start_date
    return f"{day} 00:00:00" if fmt == "datetime" else day


def run() -> int:
    now_dt = datetime.now()
    end = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_date = now_dt.strftime("%Y-%m-%d")

    print("=" * 50)
    print(f"myStock 增量更新  截至 {end_date}")
    print(f"市场: {', '.join(CONFIG.markets)}  数据库: {CONFIG.db_path}")
    print("=" * 50)

    db.init_db()
    conn = db.get_connection()
    try:
        # 持仓：始终覆盖当天快照
        init_load.collect_positions(conn)

        order_start = _incremental_start(conn, "futu_order", "datetime")
        init_load.collect_orders(conn, order_start, end)

        deal_start = _incremental_start(conn, "futu_deal", "datetime")
        init_load.collect_deals(conn, deal_start, end)

        quote_start = _incremental_start(conn, "yfinance", "date")
        init_load.collect_quotes(conn, quote_start, end_date)

        # 美元-人民币汇率：增量起点取上次同步当天（当天覆盖）
        fx_start = _incremental_start(conn, "fx_usdcny", "date")
        init_load.collect_fx(conn, fx_start, end_date)

        # 通用信息：每日全量刷新（UPSERT 覆盖）
        init_load.collect_profiles(conn)
    finally:
        conn.close()

    print("增量更新完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
