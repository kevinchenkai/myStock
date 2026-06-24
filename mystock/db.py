"""数据库连接、建表与 UPSERT 封装。

所有写入幂等：
  - positions:    PRIMARY KEY (snapshot_date, market, code)  → 当天覆盖
  - orders:       PRIMARY KEY (order_id)                      → UPSERT
  - deals:        PRIMARY KEY (deal_id)                       → UPSERT
  - daily_quotes: PRIMARY KEY (yf_symbol, date)               → 覆盖
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .config import CONFIG

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """返回 SQLite 连接（行可按列名访问）。会自动创建父目录。"""
    path = db_path or CONFIG.db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """执行 schema.sql 建表（IF NOT EXISTS，可重复执行）。"""
    conn = get_connection(db_path)
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _upsert(
    conn: sqlite3.Connection,
    table: str,
    rows: Sequence[dict],
    conflict_keys: Sequence[str],
) -> int:
    """通用 UPSERT：INSERT ... ON CONFLICT(keys) DO UPDATE。

    rows 中每个 dict 的 key 必须是表的列名。返回写入行数。
    """
    if not rows:
        return 0

    columns = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_list = ", ".join(columns)
    # 冲突时更新除主键外的所有列
    update_cols = [c for c in columns if c not in conflict_keys]
    update_clause = ", ".join([f"{c}=excluded.{c}" for c in update_cols])
    conflict_clause = ", ".join(conflict_keys)

    if update_cols:
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_clause}) DO UPDATE SET {update_clause}"
        )
    else:
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_clause}) DO NOTHING"
        )

    params = [tuple(row[c] for c in columns) for row in rows]
    conn.executemany(sql, params)
    conn.commit()
    return len(rows)


def upsert_positions(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    return _upsert(conn, "positions", rows, ["snapshot_date", "market", "code"])


def upsert_orders(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    return _upsert(conn, "orders", rows, ["order_id"])


def upsert_deals(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    return _upsert(conn, "deals", rows, ["deal_id"])


def upsert_quotes(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    return _upsert(conn, "daily_quotes", rows, ["yf_symbol", "date"])


def upsert_profiles(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    return _upsert(conn, "stock_profiles", rows, ["futu_code"])


def upsert_fx_rates(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    return _upsert(conn, "fx_rates", rows, ["pair", "date"])


def get_profile(conn: sqlite3.Connection, futu_code: str) -> Optional[dict]:
    """读取某代码的通用信息（无则返回 None）。"""
    cur = conn.execute(
        "SELECT * FROM stock_profiles WHERE futu_code = ?", (futu_code,)
    )
    row = cur.fetchone()
    return dict(row) if row else None


def replace_position_snapshot(
    conn: sqlite3.Connection, snapshot_date: str, rows: Sequence[dict]
) -> int:
    """覆盖某天的持仓快照：先删除当天，再插入。

    用于当天重复抓取时确保旧条目（已清仓的标的）不残留。
    """
    conn.execute("DELETE FROM positions WHERE snapshot_date = ?", (snapshot_date,))
    return upsert_positions(conn, rows)


def write_sync_log(
    conn: sqlite3.Connection,
    source: str,
    range_start: Optional[str],
    range_end: Optional[str],
    row_count: int,
    status: str,
    message: str = "",
) -> None:
    conn.execute(
        "INSERT INTO sync_log (source, range_start, range_end, row_count, status, message, run_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source, range_start, range_end, row_count, status, message, now_str()),
    )
    conn.commit()


def last_sync_point(conn: sqlite3.Connection, source: str) -> Optional[str]:
    """读取某数据源最近一次成功同步的 range_end，用于增量。"""
    cur = conn.execute(
        "SELECT range_end FROM sync_log WHERE source = ? AND status = 'ok' "
        "ORDER BY id DESC LIMIT 1",
        (source,),
    )
    row = cur.fetchone()
    return row["range_end"] if row and row["range_end"] else None


def all_traded_codes(conn: sqlite3.Connection) -> list[str]:
    """返回 positions / orders / deals 中出现过的全部富途代码（去重）。"""
    codes: set[str] = set()
    for table in ("positions", "orders", "deals"):
        cur = conn.execute(f"SELECT DISTINCT code FROM {table} WHERE code IS NOT NULL")
        for row in cur.fetchall():
            if row["code"]:
                codes.add(row["code"])
    return sorted(codes)


# ---------------- 行情跳过名单 ----------------

# 连续抓到空数据多少次后加入跳过名单（达到即跳过，避免无效重试）
SKIP_THRESHOLD = 2


def get_quote_skiplist(conn: sqlite3.Connection) -> set[str]:
    """返回已确认跳过（empty_count >= 阈值）的富途代码集合。"""
    cur = conn.execute(
        "SELECT futu_code FROM quote_skiplist WHERE empty_count >= ?",
        (SKIP_THRESHOLD,),
    )
    return {row["futu_code"] for row in cur.fetchall()}


def record_quote_empty(
    conn: sqlite3.Connection, futu_code: str, yf_symbol: str, reason: str = "no data"
) -> int:
    """记录某代码本次抓到空数据，empty_count +1。返回累计次数。"""
    now = now_str()
    conn.execute(
        "INSERT INTO quote_skiplist (futu_code, yf_symbol, empty_count, reason, first_seen, updated_at) "
        "VALUES (?, ?, 1, ?, ?, ?) "
        "ON CONFLICT(futu_code) DO UPDATE SET "
        "empty_count = empty_count + 1, yf_symbol = excluded.yf_symbol, "
        "reason = excluded.reason, updated_at = excluded.updated_at",
        (futu_code, yf_symbol, reason, now, now),
    )
    conn.commit()
    cur = conn.execute(
        "SELECT empty_count FROM quote_skiplist WHERE futu_code = ?", (futu_code,)
    )
    row = cur.fetchone()
    return row["empty_count"] if row else 0


def clear_quote_skip(conn: sqlite3.Connection, futu_code: str) -> None:
    """某代码重新抓到数据时，从跳过名单移除（计数清零）。"""
    conn.execute("DELETE FROM quote_skiplist WHERE futu_code = ?", (futu_code,))
    conn.commit()
