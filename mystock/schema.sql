-- myStock SQLite schema
-- 持仓快照（同一 snapshot_date + code + market 唯一，重复抓取覆盖当天）
CREATE TABLE IF NOT EXISTS positions (
    snapshot_date   TEXT NOT NULL,        -- YYYY-MM-DD，抓取日期
    market          TEXT NOT NULL,        -- HK / US
    code            TEXT NOT NULL,        -- 富途代码，如 HK.00700 / US.AAPL
    name            TEXT,
    qty             REAL,                 -- 持仓数量
    can_sell_qty    REAL,                 -- 可卖数量
    cost_price      REAL,                 -- 成本价
    nominal_price   REAL,                 -- 市价
    market_val      REAL,                 -- 市值
    pl_val          REAL,                 -- 浮动盈亏
    pl_ratio        REAL,                 -- 盈亏比例(%)
    currency        TEXT,
    updated_at      TEXT,                 -- 入库时间
    PRIMARY KEY (snapshot_date, market, code)
);

-- 历史订单（order_id 唯一）
CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    market          TEXT,                 -- HK / US
    code            TEXT,
    name            TEXT,
    trd_side        TEXT,                 -- BUY / SELL
    order_type      TEXT,
    order_status    TEXT,                 -- 含未成交/部分成交/全部成交/撤单/失败等
    price           REAL,
    qty             REAL,                 -- 委托数量
    dealt_qty       REAL,                 -- 成交数量
    dealt_avg_price REAL,                 -- 成交均价
    create_time     TEXT,                 -- 下单时间
    updated_time    TEXT,                 -- 最后更新时间
    currency        TEXT,
    raw_json        TEXT,                 -- 原始记录（便于排查）
    synced_at       TEXT
);

-- 历史成交（deal_id 唯一）
CREATE TABLE IF NOT EXISTS deals (
    deal_id         TEXT PRIMARY KEY,
    order_id        TEXT,                 -- 关联 orders.order_id
    market          TEXT,
    code            TEXT,
    name            TEXT,
    trd_side        TEXT,                 -- BUY / SELL
    price           REAL,                 -- 成交价
    qty             REAL,                 -- 成交数量
    create_time     TEXT,                 -- 成交时间
    counter_broker_id   TEXT,
    raw_json        TEXT,
    synced_at       TEXT
);

-- 每日行情（yfinance），yf_symbol + date 唯一
CREATE TABLE IF NOT EXISTS daily_quotes (
    yf_symbol       TEXT NOT NULL,        -- yfinance 代码，如 0700.HK / AAPL
    futu_code       TEXT,                 -- 对应的富途代码，便于关联
    date            TEXT NOT NULL,        -- YYYY-MM-DD
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    adj_close       REAL,
    volume          REAL,
    dividends       REAL,
    stock_splits    REAL,
    synced_at       TEXT,
    PRIMARY KEY (yf_symbol, date)
);

-- 行情跳过名单：连续多次抓取为空（如退市 / yfinance 无数据）的代码，
-- 后续直接跳过，避免重复无效请求与库的退市警告噪音。
CREATE TABLE IF NOT EXISTS quote_skiplist (
    futu_code       TEXT PRIMARY KEY,
    yf_symbol       TEXT,
    empty_count     INTEGER DEFAULT 0,    -- 连续抓到空数据的次数
    reason          TEXT,                 -- 备注，如 no data / delisted
    first_seen      TEXT,
    updated_at      TEXT
);

-- 同步日志
CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT,                 -- futu_position / futu_order / futu_deal / yfinance
    range_start     TEXT,
    range_end       TEXT,
    row_count       INTEGER,
    status          TEXT,                 -- ok / error
    message         TEXT,
    run_at          TEXT
);

-- 常用查询索引
CREATE INDEX IF NOT EXISTS idx_orders_code ON orders(code);
CREATE INDEX IF NOT EXISTS idx_deals_code ON deals(code);
CREATE INDEX IF NOT EXISTS idx_deals_order ON deals(order_id);
CREATE INDEX IF NOT EXISTS idx_quotes_futu ON daily_quotes(futu_code);
CREATE INDEX IF NOT EXISTS idx_positions_code ON positions(code);
