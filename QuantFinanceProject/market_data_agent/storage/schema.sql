CREATE SCHEMA IF NOT EXISTS market_data;

-- 1) Daily OHLCV
CREATE TABLE IF NOT EXISTS market_data.daily_ohlcv (
    time DATE NOT NULL,
    symbol TEXT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume BIGINT,
    PRIMARY KEY (symbol, time)
);
SELECT create_hypertable('market_data.daily_ohlcv', 'time', if_not_exists => TRUE);

-- 2) Intraday 5-min OHLCV
CREATE TABLE IF NOT EXISTS market_data.intraday_5min_ohlcv (
    time TIMESTAMP NOT NULL,
    symbol TEXT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume BIGINT,
    PRIMARY KEY (symbol, time)
);
SELECT create_hypertable('market_data.intraday_5min_ohlcv', 'time', if_not_exists => TRUE);
SELECT add_retention_policy('market_data.intraday_5min_ohlcv', INTERVAL '10 days');

-- 3) Intraday 1-min live buffer
CREATE TABLE IF NOT EXISTS market_data.intraday_1min_live (
    time TIMESTAMP NOT NULL,
    symbol TEXT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume BIGINT,
    PRIMARY KEY (symbol, time)
);
SELECT create_hypertable('market_data.intraday_1min_live', 'time', if_not_exists => TRUE);
SELECT add_retention_policy('market_data.intraday_1min_live', INTERVAL '1 day');
