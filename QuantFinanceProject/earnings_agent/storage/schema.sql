-- ================================================================================================
-- QFinAgent - Earnings Agent Storage Schema
-- Version: 2.1
-- Description: This script completely rebuilds the earnings_data schema to align with the
--              institutional-grade, metadata-driven architecture.
--
-- Key Features:
-- 1. Separation of Concerns: `ingestion_jobs` (expectations) vs. `raw_data_assets` (results).
-- 2. Idempotency via Hashing: `raw_data_hash` ensures data is processed only once.
-- 3. Rich Metadata: Tracks script versions, failures, and status at each stage.
-- 4. Clear Lineage: Dedicated link tables and foreign keys provide a full audit trail.
--
-- WARNING: This script will drop the entire 'earnings_data' schema and all its data.
-- ================================================================================================

-- Step 1: Drop the old schema to ensure a clean start.
DROP SCHEMA IF EXISTS earnings_data CASCADE;

-- Step 2: Recreate the schema.
CREATE SCHEMA IF NOT EXISTS earnings_data;

-- ================================================================================================
-- STAGE 1: INGESTION - Expectations and Raw Results
-- ================================================================================================

CREATE TABLE IF NOT EXISTS earnings_data.ingestion_jobs (
    job_id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_year INT NOT NULL,
    quarter INT NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    consolidation_status VARCHAR(50) NOT NULL, -- 'Consolidated' or 'Standalone'
    ingestion_script_version VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING', -- PENDING, SUCCESS, MISSING_AT_SOURCE, FETCH_FAILED
    failure_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_attempted_at TIMESTAMPTZ,
    UNIQUE (ticker, fiscal_year, quarter, source_type, consolidation_status, ingestion_script_version)
);
COMMENT ON TABLE earnings_data.ingestion_jobs IS 'The "To-Do List" or manifest. Defines all data we expect to ingest.';

CREATE TABLE IF NOT EXISTS earnings_data.raw_data_assets (
    asset_id BIGSERIAL PRIMARY KEY,
    raw_data_hash VARCHAR(64) NOT NULL UNIQUE, -- SHA-256 hash of the content, the true identifier.
    source_type VARCHAR(50), -- Denormalized for convenience
    storage_location TEXT, -- e.g., S3 URI or local file path
    file_size_bytes BIGINT, -- To store the size of the downloaded file for auditing.
    source_last_modified TIMESTAMPTZ, -- To store the server's Last-Modified timestamp for integrity checks.
    data_content JSONB, -- Used for API responses
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE earnings_data.raw_data_assets IS 'A content-addressable library of all unique raw data ever ingested. Identity is based on the data''s hash.';


CREATE TABLE IF NOT EXISTS earnings_data.job_asset_link (
    job_id BIGINT PRIMARY KEY REFERENCES earnings_data.ingestion_jobs(job_id) ON DELETE CASCADE,
    asset_id BIGINT NOT NULL REFERENCES earnings_data.raw_data_assets(asset_id)
);
COMMENT ON TABLE earnings_data.job_asset_link IS 'A simple, crucial link table connecting an IngestionJob (expectation) to a RawDataAsset (result).';


-- ================================================================================================
-- STAGE 2 & 3: PARSING AND VALIDATION
-- ================================================================================================

CREATE TABLE IF NOT EXISTS earnings_data.parsed_documents (
    doc_id BIGSERIAL PRIMARY KEY,
    asset_id BIGINT NOT NULL REFERENCES earnings_data.raw_data_assets(asset_id) ON DELETE CASCADE,
    parser_version VARCHAR(50) NOT NULL,
    parse_status VARCHAR(50) NOT NULL, -- PARSED_OK, PARSING_ERROR
    error_details TEXT, -- Stores traceback on failure
    parsed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content JSONB, -- The structured data extracted from the raw asset
    UNIQUE (asset_id, parser_version)
);
COMMENT ON TABLE earnings_data.parsed_documents IS 'Staging table for structured data transformed from a raw asset.';


CREATE TABLE IF NOT EXISTS earnings_data.validation_results (
    validation_id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL REFERENCES earnings_data.parsed_documents(doc_id) ON DELETE CASCADE,
    validation_script_version VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL, -- PASSED, FAILED, WARNING
    summary JSONB, -- e.g., {"check_name": "Assets = L + E", "result": "FAILED", "details": "Diff of 1.2M"}
    validated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (doc_id, validation_script_version)
);
COMMENT ON TABLE earnings_data.validation_results IS 'Stores the outcome of running the Validation Engine on a parsed document.';


-- ================================================================================================
-- STAGE 4: FINAL "GOLDEN RECORD" TABLES (Structure from original schema)
-- ================================================================================================

CREATE TABLE IF NOT EXISTS earnings_data.quarterly_fundamentals (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    period VARCHAR(10) NOT NULL,
    filing_date DATE,
    source VARCHAR(50) NOT NULL,
    version INT DEFAULT 1 NOT NULL,
    -- Link back to the primary asset used to generate this record
    primary_asset_id BIGINT REFERENCES earnings_data.raw_data_assets(asset_id),
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
COMMENT ON TABLE earnings_data.quarterly_fundamentals IS 'The "Golden Record" table for final, clean, reconciled, and versioned financial data.';


CREATE TABLE IF NOT EXISTS earnings_data.custom_kpis (
    id BIGSERIAL PRIMARY KEY,
    fundamental_id BIGINT NOT NULL REFERENCES earnings_data.quarterly_fundamentals(id) ON DELETE CASCADE,
    kpi_data JSONB NOT NULL,
    UNIQUE(fundamental_id)
);
COMMENT ON TABLE earnings_data.custom_kpis IS 'Stores custom-calculated KPIs for a given fundamental record.';

-- ================================================================================================
-- UTILITY VIEWS
-- ================================================================================================

CREATE OR REPLACE VIEW earnings_data.v_ingestion_status_report AS
SELECT
    j.job_id,
    j.ticker,
    j.fiscal_year,
    j.quarter,
    j.source_type,
    j.consolidation_status,
    j.status,
    j.ingestion_script_version,
    j.last_attempted_at,
    j.failure_reason,
    l.asset_id,
    a.raw_data_hash
FROM
    earnings_data.ingestion_jobs j
LEFT JOIN
    earnings_data.job_asset_link l ON j.job_id = l.job_id
LEFT JOIN
    earnings_data.raw_data_assets a ON l.asset_id = a.asset_id
ORDER BY
    j.last_attempted_at DESC;

COMMENT ON VIEW earnings_data.v_ingestion_status_report IS 'A user-friendly report for monitoring the status and outcome of all ingestion jobs.';