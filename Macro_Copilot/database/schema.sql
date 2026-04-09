-- ================================================================================================
-- MACRO COPILOT - UPDATED SCHEMA DEFINITION
-- Description: Core schema for a multi-asset macro stack with richer instrument identity,
--              daily market observations, and load/extraction lineage.
-- ================================================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE SCHEMA IF NOT EXISTS macro_data;

-- ================================================================================================
-- 1. INSTRUMENT MASTER
-- Goal: define what each instrument is in a structured, queryable way.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.instrument_master (
    instrument_id BIGSERIAL PRIMARY KEY,
    vendor VARCHAR(32) NOT NULL DEFAULT 'BLOOMBERG',
    vendor_ticker VARCHAR(128) NOT NULL,
    asset_class VARCHAR(64) NOT NULL,
    instrument_type VARCHAR(64) NOT NULL,     -- e.g. sovereign, ois, irs, future, linker, swaption
    curve_family VARCHAR(64),                 -- e.g. UST, SOFR_OIS, EUR_IRS
    country VARCHAR(16),
    currency VARCHAR(16),
    tenor VARCHAR(16),                        -- e.g. 2Y, 5Y, 10Y
    underlying_index VARCHAR(32),             -- e.g. SOFR, SONIA, ESTR
    contract_code VARCHAR(32),                -- useful for futures/options if needed
    expiry_date DATE,
    maturity_date DATE,
    is_rolling_contract BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    attributes JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_instrument_vendor UNIQUE (vendor, vendor_ticker)
);

CREATE INDEX IF NOT EXISTS idx_instrument_master_lookup
    ON macro_data.instrument_master (asset_class, instrument_type, country, currency, tenor);

CREATE INDEX IF NOT EXISTS idx_instrument_master_curve_family
    ON macro_data.instrument_master (curve_family);

CREATE INDEX IF NOT EXISTS idx_instrument_master_attributes
    ON macro_data.instrument_master USING GIN (attributes);

-- ================================================================================================
-- 2. LOAD AUDIT
-- Goal: track what actually ran: playbook/version, extraction window, source file, and status.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.load_audit (
    load_id BIGSERIAL PRIMARY KEY,
    playbook_name VARCHAR(256) NOT NULL,
    playbook_version VARCHAR(64),
    playbook_hash VARCHAR(128),
    git_commit_hash VARCHAR(128),
    extractor_version VARCHAR(128),
    source_file_name VARCHAR(512),
    source_file_hash VARCHAR(128),
    dataset_name VARCHAR(128),
    requested_start_date DATE,
    requested_end_date DATE,
    extracted_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(32) NOT NULL DEFAULT 'SUCCESS',   -- e.g. SUCCESS / FAILED / PARTIAL
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_load_audit_playbook
    ON macro_data.load_audit (playbook_name, ingested_at DESC);

CREATE INDEX IF NOT EXISTS idx_load_audit_dataset
    ON macro_data.load_audit (dataset_name, ingested_at DESC);

CREATE INDEX IF NOT EXISTS idx_load_audit_source_file_hash
    ON macro_data.load_audit (source_file_hash);

-- ================================================================================================
-- 3. DAILY MARKET DATA
-- Goal: one row per daily observation for a given instrument + field.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.market_data_daily (
    trade_date DATE NOT NULL,
    instrument_id BIGINT NOT NULL REFERENCES macro_data.instrument_master(instrument_id),
    field_name VARCHAR(64) NOT NULL,          -- e.g. YLD_YTM_MID, PX_LAST, OPEN_INT
    field_value NUMERIC(20, 8),
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trade_date, instrument_id, field_name)
);

SELECT create_hypertable('macro_data.market_data_daily', 'trade_date', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_market_data_daily_instrument_date
    ON macro_data.market_data_daily (instrument_id, trade_date DESC);

CREATE INDEX IF NOT EXISTS idx_market_data_daily_field
    ON macro_data.market_data_daily (field_name, trade_date DESC);

-- ================================================================================================
-- 4. ENRICHED DAILY VIEW
-- Goal: make querying easier by joining observations to instrument metadata.
-- ================================================================================================
CREATE OR REPLACE VIEW macro_data.v_market_data_daily_enriched AS
SELECT
    d.trade_date,
    d.instrument_id,
    i.vendor,
    i.vendor_ticker,
    i.asset_class,
    i.instrument_type,
    i.curve_family,
    i.country,
    i.currency,
    i.tenor,
    i.underlying_index,
    i.contract_code,
    i.expiry_date,
    i.maturity_date,
    i.is_rolling_contract,
    i.is_active,
    d.field_name,
    d.field_value,
    d.load_id,
    d.created_at,
    i.attributes
FROM macro_data.market_data_daily d
JOIN macro_data.instrument_master i
  ON d.instrument_id = i.instrument_id;
