-- This script completely rebuilds the earnings_data schema to apply the new unified architecture,
-- including the addition of the ingestion_log table.
-- It is designed to be idempotent and safe to run.
-- WARNING: This will delete all existing data within the earnings_data schema.


-- Step 2: Recreate the schema.
CREATE SCHEMA IF NOT EXISTS earnings_data;

-- ================================================================================================
-- TABLE DEFINITIONS
-- ================================================================================================

-- The unified `raw_sources` table remains the same.
CREATE TABLE IF NOT EXISTS earnings_data.raw_sources (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    source_type VARCHAR(50) NOT NULL, -- e.g., 'XBRL_FILE', 'NSE_API', 'PDF_FILE'
    source_url TEXT,
    local_path TEXT,
    raw_content JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, fiscal_date, source_type)
);
COMMENT ON TABLE earnings_data.raw_sources IS 'Unified table for all raw data sources, whether file-based or from APIs.';


-- The `parsed_earnings` table remains the same.
CREATE TABLE IF NOT EXISTS earnings_data.parsed_earnings (
    id SERIAL PRIMARY KEY,
    raw_source_id INTEGER NOT NULL REFERENCES earnings_data.raw_sources(id) ON DELETE CASCADE,
    parser_version VARCHAR(20) NOT NULL,
    parsed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content JSONB NOT NULL,
    UNIQUE(raw_source_id, parser_version)
);
COMMENT ON TABLE earnings_data.parsed_earnings IS 'Staging table for structured data transformed from a raw_source.';


-- The `quarterly_fundamentals` table remains the same.
CREATE TABLE IF NOT EXISTS earnings_data.quarterly_fundamentals (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL, fiscal_date DATE NOT NULL, period VARCHAR(10) NOT NULL,
    filing_date DATE, source VARCHAR(50) NOT NULL, version INT DEFAULT 1 NOT NULL,
    raw_source_id INTEGER REFERENCES earnings_data.raw_sources(id),
    -- Income Statement
    revenue BIGINT, cost_of_goods_sold BIGINT, gross_profit BIGINT, operating_expenses BIGINT,
    ebitda BIGINT, depreciation_and_amortization BIGINT, ebit BIGINT, interest_expense BIGINT,
    profit_before_tax BIGINT, tax_expense BIGINT, net_income BIGINT,
    earnings_per_share_basic NUMERIC(18, 4), earnings_per_share_diluted NUMERIC(18, 4),
    -- Balance Sheet
    cash_and_equivalents BIGINT, accounts_receivable BIGINT, inventory BIGINT,
    total_current_assets BIGINT, property_plant_equipment_net BIGINT, total_non_current_assets BIGINT,
    total_assets BIGINT, accounts_payable BIGINT, total_current_liabilities BIGINT,
    total_long_term_debt BIGINT, total_non_current_liabilities BIGINT, total_liabilities BIGINT,
    shareholders_equity BIGINT, total_liabilities_and_equity BIGINT,
    -- Cash Flow Statement
    cash_flow_from_operating BIGINT, cash_flow_from_investing BIGINT, cash_flow_from_financing BIGINT,
    net_change_in_cash BIGINT,
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, fiscal_date, version)
);
COMMENT ON TABLE earnings_data.quarterly_fundamentals IS 'The "Golden Record" table for final, clean, versioned financial data.';


-- The `custom_kpis` table remains unchanged.
CREATE TABLE IF NOT EXISTS earnings_data.custom_kpis (
    id SERIAL PRIMARY KEY,
    fundamental_id INTEGER NOT NULL REFERENCES earnings_data.quarterly_fundamentals(id) ON DELETE CASCADE,
    kpi_data JSONB NOT NULL,
    UNIQUE(fundamental_id)
);


-- --- NEW: The ingestion_log table to track data coverage ---
CREATE TABLE IF NOT EXISTS earnings_data.ingestion_log (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_year INT NOT NULL,
    quarter INT NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    
    -- Status can be 'FOUND', 'MISSING_AT_SOURCE', 'FETCH_ERROR', etc.
    status VARCHAR(50) NOT NULL,
    
    -- This will be populated only if the status is 'FOUND'
    raw_source_id INTEGER REFERENCES earnings_data.raw_sources(id) ON DELETE SET NULL,
    
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- We should only have one log entry per company/period/source combination.
    UNIQUE(ticker, fiscal_year, quarter, source_type)
);
COMMENT ON TABLE earnings_data.ingestion_log IS 'Tracks the status of data ingestion attempts for every expected filing.';


-- ================================================================================================
-- VIEW DEFINITION (remains unchanged)
-- ================================================================================================

CREATE OR REPLACE VIEW earnings_data.v_parsed_earnings_flat AS
SELECT
    p.id, p.parser_version, p.parsed_at,
    s.ticker, s.fiscal_date, s.source_type,
    p.content -> 'validation_summary' ->> 'status' AS validation_status,
    p.content -> 'parsing_summary' ->> 'status' AS parsing_status,
    (p.content -> 'core_metrics' ->> 'revenue')::NUMERIC::BIGINT AS revenue,
    (p.content -> 'core_metrics' ->> 'net_income')::NUMERIC::BIGINT AS net_income,
    (p.content -> 'core_metrics' ->> 'ebitda')::NUMERIC::BIGINT AS ebitda,
    (p.content -> 'core_metrics' ->> 'profit_before_tax')::NUMERIC::BIGINT AS profit_before_tax,
    (p.content -> 'core_metrics' ->> 'earnings_per_share_diluted')::NUMERIC(18, 4) AS eps_diluted,
    (p.content -> 'core_metrics' ->> 'total_assets')::NUMERIC::BIGINT AS total_assets,
    (p.content -> 'core_metrics' ->> 'total_liabilities')::NUMERIC::BIGINT AS total_liabilities,
    (p.content -> 'core_metrics' ->> 'shareholders_equity')::NUMERIC::BIGINT AS shareholders_equity
FROM
    earnings_data.parsed_earnings p
JOIN
    earnings_data.raw_sources s ON p.raw_source_id = s.id;

COMMENT ON VIEW earnings_data.v_parsed_earnings_flat IS 'A flattened, user-friendly view joining parsed_earnings with raw_sources.';