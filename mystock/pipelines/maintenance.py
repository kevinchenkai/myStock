"""库维护工具（手动运行）。

跳过名单（quote_skiplist）不会自愈：一次限频/网络抖动可能把真实持仓标的
批量抓空并永久拉黑，之后不再请求 → 无从恢复。此工具用于手动止血。

用法（conda activate mk 后）：

  # 查看当前跳过名单
  python -m mystock.pipelines.maintenance skiplist

  # 清空整个跳过名单（下次 update 会重新尝试全部标的）
  python -m mystock.pipelines.maintenance reset-skiplist

  # 只重置指定代码
  python -m mystock.pipelines.maintenance reset-skiplist US.AAPL HK.00700

  # 彻底删除某代码在所有表的数据（退市/清仓且不再关注；不可逆）
  python -m mystock.pipelines.maintenance purge US.YY
"""
from __future__ import annotations

import sys

from .. import db


def _show_skiplist(conn) -> int:
    rows = conn.execute(
        "SELECT futu_code, yf_symbol, empty_count, reason, updated_at "
        "FROM quote_skiplist ORDER BY futu_code"
    ).fetchall()
    if not rows:
        print("跳过名单为空。")
        return 0
    print(f"跳过名单共 {len(rows)} 支（empty_count >= {db.SKIP_THRESHOLD} 才会被真正跳过）：")
    for r in rows:
        flag = "跳过" if r["empty_count"] >= db.SKIP_THRESHOLD else "观察"
        print(f"  [{flag}] {r['futu_code']:12} 空 {r['empty_count']} 次  {r['reason']}  {r['updated_at']}")
    return 0


def _reset_skiplist(conn, codes) -> int:
    n = db.reset_quote_skiplist(conn, codes or None)
    scope = "全部" if not codes else ", ".join(codes)
    print(f"已重置跳过名单（{scope}），移除 {n} 条。下次 update 会重新尝试抓取。")
    return 0


def _purge(conn, codes) -> int:
    if not codes:
        print("purge 需要至少一个代码，如：purge US.YY", file=sys.stderr)
        return 2
    for code in codes:
        deleted = db.purge_code(conn, code)
        total = sum(deleted.values())
        detail = ", ".join(f"{t}={n}" for t, n in deleted.items() if n)
        print(f"已删除 {code}：共 {total} 行" + (f"（{detail}）" if detail else "（无残留）"))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    db.init_db()
    conn = db.get_connection()
    try:
        if cmd == "skiplist":
            return _show_skiplist(conn)
        if cmd == "reset-skiplist":
            return _reset_skiplist(conn, rest)
        if cmd == "purge":
            return _purge(conn, rest)
        print(f"未知命令：{cmd}\n{__doc__}", file=sys.stderr)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
