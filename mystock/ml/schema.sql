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
    data_source     TEXT NOT NULL DEFAULT 'yfinance',
    source_ref      TEXT,                 -- SHA256 of original fallback-provider evidence
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

-- 每日次日区间预测的历史留档（报告生成时写入，供「近期预测复盘」回看）
-- 口径：predict_next_day 的输出——**全历史 fit**、对 as_of 收盘后预测 as_of 次一交易日
-- 的 [l_hat, h_hat]。与 backtest 里 walk-forward 的历史预测**不是同一个模型**，勿混用。
-- PK (code, as_of)：同一基准日重复生成（当天多跑几次报告）覆盖为最后一次。
CREATE TABLE IF NOT EXISTS ml_predictions (
    code            TEXT NOT NULL,        -- 富途代码，如 US.NVDA
    as_of           TEXT NOT NULL,        -- 预测基准日 T（该日收盘后预测 T+1）
    close           REAL,                 -- T 日收盘价（还原区间用的基准）
    l_hat           REAL,                 -- 预测区间下沿
    h_hat           REAL,                 -- 预测区间上沿
    width_pct       REAL,                 -- 区间宽 / close（%）
    low_alpha       REAL,                 -- 生成时的分位档（便于回看口径变更）
    high_alpha      REAL,
    conformal       INTEGER,              -- 是否启用 CQR 校准（0/1）
    q_ret           REAL,                 -- CQR 半宽（ret 空间）
    target_coverage REAL,                 -- CQR 目标覆盖率
    backend         TEXT,                 -- lightgbm / sklearn
    source          TEXT,                 -- live=报告实时生成 / backfill=历史 HTML 回填
    generated_at    TEXT,                 -- 写入时间
    PRIMARY KEY (code, as_of)
);

CREATE INDEX IF NOT EXISTS idx_ml_pred_asof ON ml_predictions(as_of);
CREATE INDEX IF NOT EXISTS idx_ml_q1d_futu ON ml_quotes_1d(futu_code);
CREATE INDEX IF NOT EXISTS idx_ml_q1h_futu ON ml_quotes_1h(futu_code);
CREATE INDEX IF NOT EXISTS idx_ml_deals_code ON ml_deals(code);
CREATE INDEX IF NOT EXISTS idx_ml_orders_code ON ml_orders(code);

-- Immutable prediction content. Lifecycle timestamps may be attached separately.
CREATE TABLE IF NOT EXISTS ml_prediction_versions (
    prediction_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    as_of TEXT NOT NULL,
    target_session TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    generated_at TEXT,
    decision_at TEXT,
    published_at TEXT,
    manifest_path TEXT,
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE(run_id, code, as_of, target_session)
);
CREATE INDEX IF NOT EXISTS idx_ml_versions_target ON ml_prediction_versions(code,target_session);
CREATE TRIGGER IF NOT EXISTS ml_versions_no_content_update
BEFORE UPDATE OF prediction_id,run_id,code,as_of,target_session,source,generated_at,decision_at,manifest_path,payload_json,content_hash
ON ml_prediction_versions BEGIN SELECT RAISE(ABORT, 'immutable prediction content'); END;
CREATE TRIGGER IF NOT EXISTS ml_versions_no_delete BEFORE DELETE ON ml_prediction_versions
BEGIN SELECT RAISE(ABORT, 'immutable prediction history'); END;
