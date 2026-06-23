"""ML 训练库读写封装（独立于 mystock/db.py）。

全部 UPSERT 幂等。生产库只读、绝不写。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from . import config as mlcfg


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_ml_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """ML 训练库连接（可写）。自动建父目录。"""
    path = str(db_path or mlcfg.ML_DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_prod_connection_readonly() -> sqlite3.Connection:
    """生产库**只读**连接（URI mode=ro，写操作会直接报错）。"""
    uri = f"file:{mlcfg.PROD_DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def init_ml_db(db_path: Optional[str] = None) -> None:
    """执行 ml/schema.sql 建表（IF NOT EXISTS，可重复执行）。"""
    conn = get_ml_connection(db_path)
    try:
        with open(mlcfg.SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[dict]) -> int:
    """通用 UPSERT（按表主键冲突时覆盖）。返回写入行数。"""
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    conn.commit()
    return len(rows)


def log_sync(
    conn: sqlite3.Connection,
    source: str,
    *,
    symbol: str = "",
    range_start: str = "",
    range_end: str = "",
    row_count: int = 0,
    status: str = "ok",
    message: str = "",
) -> None:
    conn.execute(
        "INSERT INTO ml_sync_log "
        "(source, symbol, range_start, range_end, row_count, status, message, run_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (source, symbol, range_start, range_end, row_count, status, message, now_str()),
    )
    conn.commit()
