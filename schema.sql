-- 1. Dimension Table: Ticker Metadata
CREATE TABLE IF NOT EXISTS tickers (
    ticker_id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL UNIQUE,
    company_name VARCHAR(120) NOT NULL,
    sector VARCHAR(60),
    exchange VARCHAR(30),
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Fact Table: Daily OHLCV Market Data
CREATE TABLE IF NOT EXISTS daily_prices (
    price_id BIGSERIAL PRIMARY KEY,
    ticker_id INT NOT NULL REFERENCES tickers(ticker_id) ON DELETE CASCADE,
    trade_date DATE NOT NULL,
    open_price NUMERIC(12, 4) NOT NULL CHECK (open_price > 0),
    high_price NUMERIC(12, 4) NOT NULL CHECK (high_price > 0),
    low_price NUMERIC(12, 4) NOT NULL CHECK (low_price > 0),
    close_price NUMERIC(12, 4) NOT NULL CHECK (close_price > 0),
    adj_close NUMERIC(12, 4) NOT NULL CHECK (adj_close > 0),
    volume BIGINT NOT NULL CHECK (volume >= 0),
    ingested_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_high_low CHECK (high_price >= low_price),
    CONSTRAINT uq_ticker_date UNIQUE (ticker_id, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_prices_lookup 
    ON daily_prices (ticker_id, trade_date DESC);

-- 3. Fact Table: Model Predictions & Backtest Metrics
CREATE TABLE IF NOT EXISTS model_predictions (
    prediction_id BIGSERIAL PRIMARY KEY,
    ticker_id INT NOT NULL REFERENCES tickers(ticker_id) ON DELETE CASCADE,
    target_date DATE NOT NULL,
    model_version VARCHAR(40) NOT NULL,
    predicted_close NUMERIC(12, 4) NOT NULL,
    actual_close NUMERIC(12, 4),
    rolling_rmse NUMERIC(12, 4),
    sharpe_ratio NUMERIC(8, 4),
    max_drawdown NUMERIC(8, 4),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_prediction_run UNIQUE (ticker_id, target_date, model_version)
);

-- 4. Operational Table: ETL Audit & Pipeline Health Log
CREATE TABLE IF NOT EXISTS etl_audit_log (
    run_id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    records_fetched INT NOT NULL,
    records_dropped INT NOT NULL,
    records_upserted INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    error_message TEXT,
    executed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Analytical View: Rolling Moving Averages & Volatility
CREATE OR REPLACE VIEW vw_ticker_analytics AS
WITH return_calc AS (
    SELECT 
        t.ticker_id,
        t.symbol,
        t.sector,
        p.trade_date,
        p.close_price,
        p.adj_close,
        p.volume,
        (p.adj_close - LAG(p.adj_close, 1) OVER (
            PARTITION BY p.ticker_id ORDER BY p.trade_date
        )) / NULLIF(LAG(p.adj_close, 1) OVER (
            PARTITION BY p.ticker_id ORDER BY p.trade_date
        ), 0) AS daily_return
    FROM daily_prices p
    INNER JOIN tickers t ON p.ticker_id = t.ticker_id
    WHERE t.is_active = TRUE
)
SELECT 
    ticker_id,
    symbol,
    sector,
    trade_date,
    close_price,
    adj_close,
    volume,
    ROUND(daily_return::NUMERIC, 6) AS daily_return,
    ROUND(AVG(adj_close) OVER (
        PARTITION BY ticker_id ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    )::NUMERIC, 4) AS sma_20,
    ROUND(AVG(adj_close) OVER (
        PARTITION BY ticker_id ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
    )::NUMERIC, 4) AS sma_50,
    ROUND(STDDEV(daily_return) OVER (
        PARTITION BY ticker_id ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    )::NUMERIC, 6) AS volatility_20d
FROM return_calc;