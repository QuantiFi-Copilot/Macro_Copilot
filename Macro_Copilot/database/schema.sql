-- ================================================================================================
-- MACRO COPILOT - INITIAL SCHEMA DEFINITION
-- Description: Core schema for the Rates Agent and multi-asset time-series storage.
-- ================================================================================================

CREATE SCHEMA IF NOT EXISTS macro_data;

-- ================================================================================================
-- 1. THE INSTRUMENT MASTER (The 'Who')
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.instrument_master (
    ticker VARCHAR(50) PRIMARY KEY,          
    asset_class VARCHAR(50) NOT NULL,        
    sub_class VARCHAR(50),                   
    country VARCHAR(10),                     
    currency VARCHAR(10),                    
    tenor VARCHAR(10),                       
    attributes JSONB,                        
    is_active BOOLEAN DEFAULT TRUE,          
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_instrument_attributes ON macro_data.instrument_master USING GIN (attributes);
CREATE INDEX IF NOT EXISTS idx_instrument_class ON macro_data.instrument_master (asset_class, country);

-- ================================================================================================
-- 2. THE TIME-SERIES HYPERTABLE (The 'What' and 'How Much')
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.market_data_timeseries (
    trade_date DATE NOT NULL,
    ticker VARCHAR(50) NOT NULL REFERENCES macro_data.instrument_master(ticker),
    field_name VARCHAR(50) NOT NULL,         
    field_value NUMERIC(18, 6),              
    UNIQUE (trade_date, ticker, field_name)
);

-- Convert to TimescaleDB Hypertable
SELECT create_hypertable('macro_data.market_data_timeseries', 'trade_date', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_market_data_ticker ON macro_data.market_data_timeseries (ticker, trade_date DESC);