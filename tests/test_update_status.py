"""Routine update failure reporting; temporary databases and mocked collectors."""
from contextlib import nullcontext

import pytest

from mystock import db
from mystock.pipelines import init_load, update_load


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "facts.db"
    db.init_db(str(path))
    connection = db.get_connection(str(path))
    yield connection
    connection.close()


@pytest.mark.parametrize("collector,source", [
    ("collect_quotes", "yfinance"),
    ("collect_profiles", "yf_profile"),
    ("collect_capital_flow", "futu_capflow"),
])
def test_partial_failure_preserves_sync_point(conn, monkeypatch, collector, source):
    db.write_sync_log(conn, source, "2026-09-01", "2026-09-02", 1, "ok")
    monkeypatch.setattr(db, "all_traded_codes", lambda c: ["US.GOOD", "US.FAIL"])
    monkeypatch.setattr(init_load.time, "sleep", lambda seconds: None)

    def fetch(code, **kwargs):
        if code == "US.FAIL":
            raise RuntimeError("synthetic network failure")
        return {"code": code} if collector == "collect_profiles" else [{"code": code}]

    monkeypatch.setattr(init_load.yc, "fetch_daily", fetch)
    monkeypatch.setattr(init_load.yc, "fetch_profile", fetch)
    monkeypatch.setattr(init_load.fc, "quote_ctx", lambda: nullcontext(None))
    monkeypatch.setattr(init_load.fc, "fetch_capital_flow", lambda ctx, code, start, end: fetch(code))
    monkeypatch.setattr(init_load.fc, "capital_flow_rows", lambda rows, *args: rows)
    written = []

    def write_rows(c, rows):
        written.extend(rows)
        return len(rows)

    for name in ["upsert_quotes", "upsert_profiles", "upsert_capital_flow"]:
        monkeypatch.setattr(db, name, write_rows)
    args = () if collector == "collect_profiles" else ("2026-09-02", "2026-09-05")
    getattr(init_load, collector)(conn, *args)
    latest = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    assert latest["status"] == "error"
    assert "1 err" in latest["message"]
    assert written == [{"code": "US.GOOD"}]
    assert db.last_sync_point(conn, source) == "2026-09-02"


@pytest.mark.parametrize("failed", [False, True])
def test_update_exit_status_uses_current_attempt(tmp_path, monkeypatch, capsys, failed):
    path = tmp_path / "facts.db"
    db.init_db(str(path))
    connect = db.get_connection
    with connect(str(path)) as c:
        db.write_sync_log(c, "old_failure", None, None, 0, "error")
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "get_connection", lambda: connect(str(path)))
    seen = []
    names = ["positions", "account_funds", "orders", "deals", "quotes",
             "fx", "profiles", "market_snapshot", "capital_flow"]
    for name in names:
        def collect(c, *args, name=name):
            seen.append(name)
            status = "error" if failed and name == "orders" else "ok"
            db.write_sync_log(c, name, None, None, 0, status)
        monkeypatch.setattr(init_load, "collect_" + name, collect)
    assert update_load.run() == (1 if failed else 0)
    assert seen == names  # a failed source must not prevent independent updates
    output = capsys.readouterr()
    if failed:
        assert "orders" in output.err and "old_failure" not in output.err
        assert "增量更新完成。" not in output.out
    else:
        assert "增量更新完成。" in output.out
