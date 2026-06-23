-- myStock ML 训练库 schema（独立于 mystock/schema.sql）
-- 设计见 docs/ML_PLAN.md §2.3。绝不与 web 生产库共用。

-- 扩抓日线（5 年），auto_adjust=False 保留 close + adj_close
CREATE TABLE IF NOT EXISTS ml_quotes_1d (
    symbol          TEXT NOT NULL,        -- yfinance 代码，如 NVDA
    futu_code       TEXT,                 -- 富途代码，如 US.NVDA
    date            TEXT NOT NULL,        -- YYYY-MM-DD（交易日，美东日期）
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    adj_close       REAL,
    volume          REAL,
    dividends       REAL,
    splits          REAL,
    synced_at       TEXT,
    PRIMARY KEY (symbol, date)
);

-- 扩抓 1 小时线（约 2 年），用于盘中限价撮合路径
CREATE TABLE IF NOT EXISTS ml_quotes_1h (
    symbol          TEXT NOT NULL,        -- yfinance 代码
    futu_code       TEXT,                 -- 富途代码
    ts_utc          TEXT NOT NULL,        -- UTC 时间戳（ISO，主键之一）
    ts_et           TEXT,                 -- 美东本地时间（便于核对，非主键）
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          REAL,
    synced_at       TEXT,
    PRIMARY KEY (symbol, ts_utc)
);

-- 生产库交易事实的只读快照拷贝（冻结，保证训练可复现）
CREATE TABLE IF NOT EXISTS ml_deals (
    deal_id         TEXT PRIMARY KEY,
    order_id        TEXT,
    market          TEXT,
    code            TEXT,                 -- 富途代码
    name            TEXT,
    trd_side        TEXT,                 -- BUY / SELL
    price           REAL,
    qty             REAL,
    create_time     TEXT,
    snapshot_taken_at TEXT                -- 本快照拷贝时间
);

CREATE TABLE IF NOT EXISTS ml_orders (
    order_id        TEXT PRIMARY KEY,
    market          TEXT,
    code            TEXT,
    name            TEXT,
    trd_side        TEXT,
    order_status    TEXT,
    price           REAL,
    qty             REAL,
    dealt_qty       REAL,
    dealt_avg_price REAL,
    create_time     TEXT,
    updated_time    TEXT,
    snapshot_taken_at TEXT
);

CREATE TABLE IF NOT EXISTS ml_positions (
    snapshot_date   TEXT NOT NULL,
    market          TEXT NOT NULL,
    code            TEXT NOT NULL,        -- 富途代码
    name            TEXT,
    qty             REAL,
    can_sell_qty    REAL,
    cost_price      REAL,
    nominal_price   REAL,
    pl_ratio        REAL,
    snapshot_taken_at TEXT,
    PRIMARY KEY (snapshot_date, market, code)
);

-- ML 自己的同步日志（不碰生产 sync_log）
CREATE TABLE IF NOT EXISTS ml_sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT,                 -- yf_1d / yf_1h / prod_deals / prod_orders / prod_positions
    symbol          TEXT,                 -- 标的（汇总行可空）
    range_start     TEXT,
    range_end       TEXT,
    row_count       INTEGER,
    status          TEXT,                 -- ok / error
    message         TEXT,
    run_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_ml_q1d_futu ON ml_quotes_1d(futu_code);
CREATE INDEX IF NOT EXISTS idx_ml_q1h_futu ON ml_quotes_1h(futu_code);
CREATE INDEX IF NOT EXISTS idx_ml_deals_code ON ml_deals(code);
CREATE INDEX IF NOT EXISTS idx_ml_orders_code ON ml_orders(code);
