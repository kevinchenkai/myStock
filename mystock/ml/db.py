"""ML 训练库读写封装（独立于 mystock/db.py）。

全部 UPSERT 幂等。生产库只读、绝不写。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from . import config as mlcfg


def now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_ml_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """ML 训练库连接（可写）。自动建父目录。"""
    path = str(db_path or mlcfg.ML_DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_ml_connection_readonly(db_path=None):
    path = Path(db_path or mlcfg.ML_DB_PATH).resolve()
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def get_prod_connection_readonly(db_path=None) -> sqlite3.Connection:
    """生产库**只读**连接（URI mode=ro，写操作会直接报错）。"""
    uri = Path(db_path or mlcfg.PROD_DB_PATH).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def init_ml_db(db_path: Optional[str] = None) -> None:
    """执行 ml/schema.sql 建表（IF NOT EXISTS，可重复执行）。"""
    conn = get_ml_connection(db_path)
    try:
        with open(mlcfg.SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        columns = {r[1] for r in conn.execute('PRAGMA table_info(ml_quotes_1h)')}
        if 'data_source' not in columns:
            conn.execute("ALTER TABLE ml_quotes_1h ADD COLUMN data_source TEXT NOT NULL DEFAULT 'yfinance'")
        if 'source_ref' not in columns:
            conn.execute('ALTER TABLE ml_quotes_1h ADD COLUMN source_ref TEXT')
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[dict]) -> int:
    """通用 UPSERT（按表主键冲突时覆盖）。返回写入行数。"""
    if table in ("ml_predictions", "ml_prediction_versions"):
        raise ValueError("Use the versioned prediction write entry")
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


PRED_COLS = [
    "code", "as_of", "close", "l_hat", "h_hat", "width_pct",
    "low_alpha", "high_alpha", "conformal", "q_ret", "target_coverage",
    "backend", "source", "generated_at",
]


def upsert_predictions(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    """写入次日区间预测（PK code+as_of，重复生成覆盖）。缺列补 None。

    统一补齐 PRED_COLS 再走通用 upsert——回填行（历史 HTML 只能解析出
    close/l_hat/h_hat）与实时行（字段齐全）列集不同，不补齐会因 executemany
    的列名取自首行而错位。
    """
    from .versions import append, digest
    from .runs import start, finish
    rows = [dict(r) for r in rows]
    if not rows: return 0
    # Imports/backfills use deterministic source identity, live reports supply run.
    total = 0
    for r in rows:
        rid = r.pop('run_id', None)
        manifest_path = r.pop('manifest_path', None)
        if rid is None:
            rid = 'import-' + digest(r)
        total += append(conn, [r], run_id=rid, manifest_path=manifest_path)
    return total


def load_predictions(
    conn: sqlite3.Connection, code: Optional[str] = None, *, since: str = "",
) -> list[dict]:
    """读预测留档，按 (code, as_of) 升序。code=None 取全部。"""
    sql = f"SELECT {', '.join(PRED_COLS)} FROM ml_predictions WHERE 1=1"
    params: list = []
    if code:
        sql += " AND code=?"
        params.append(code)
    if since:
        sql += " AND as_of>=?"
        params.append(since)
    sql += " ORDER BY code, as_of"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


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
