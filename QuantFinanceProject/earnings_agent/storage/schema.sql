-- ================================================================================================
-- QFinAgent - Earnings Agent Storage Schema
-- Version: 2.3 (Added Staging Table for Normalization)
-- Description: This script completely rebuilds the earnings_data schema to align with the
--              institutional-grade, metadata-driven architecture.
--
-- Key Features:
-- 1. Separation of Concerns: `ingestion_jobs` (expectations) vs. `raw_data_assets` (results).
-- 2. Idempotency via Hashing: `raw_data_hash` ensures data is processed only once.
-- 3. Rich Metadata: Tracks script versions, failures, and status at each stage.
-- 4. Clear Lineage: Dedicated link tables and foreign keys provide a full audit trail.
-- 5. Intelligent Normalization: Includes a cache table for the LLM-augmented label mapping.
-- 6. Staging Area: Includes a staging table for multi-source reconciliation.
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
    raw_data_hash VARCHAR(64) NOT NULL UNIQUE,
    source_type VARCHAR(50),
    storage_location TEXT,
    source_last_modified TIMESTAMPTZ, -- To store the server's Last-Modified timestamp for integrity checks.
    data_content JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE earnings_data.raw_data_assets IS 'A content-addressable library of all unique raw data ever ingested. Identity is based on the data''s hash.';


CREATE TABLE IF NOT EXISTS earnings_data.job_asset_link (
    job_id BIGINT PRIMARY KEY REFERENCES earnings_data.ingestion_jobs(job_id) ON DELETE CASCADE,
    asset_id BIGINT NOT NULL REFERENCES earnings_data.raw_data_assets(asset_id)
);
COMMENT ON TABLE earnings_data.job_asset_link IS 'A simple, crucial link table connecting an IngestionJob (expectation) to a RawDataAsset (result).';


-- ================================================================================================
-- STAGE 2: PARSING
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
COMMENT ON TABLE earnings_data.parsed_documents IS 'Staging table for structured data transformed from a raw asset. Holds raw, un-normalized key-value pairs.';

-- ================================================================================================
-- STAGE 3: NORMALIZATION & RECONCILIATION
-- ================================================================================================
-- CREATE TABLE IF NOT EXISTS earnings_data.label_mapping_cache (
--     -- MODIFIED: Added industry and created a composite primary key
--     raw_label TEXT NOT NULL,
--     industry TEXT NOT NULL,
--     normalized_label TEXT, -- The clean, standardized name from our master list
--     status VARCHAR(20) NOT NULL, -- 'APPROVED', 'PENDING_REVIEW', 'REJECTED'
--     processed BOOLEAN NOT NULL DEFAULT FALSE,
--     source_context JSONB, -- Metadata about where this label was first seen
--     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--     last_reviewed_at TIMESTAMPTZ,
--     reviewed_by TEXT, -- Identifier for the human or system that performed the review
--     PRIMARY KEY (raw_label, industry)
-- );
-- COMMENT ON TABLE earnings_data.label_mapping_cache IS 'The persistent, industry-specific cache for the financial label normalization engine.';

-- -- MODIFIED: Index now includes 'industry' for faster lookups
-- CREATE INDEX IF NOT EXISTS idx_label_mapping_status_processed ON earnings_data.label_mapping_cache(industry, status, processed);
-- -- This is the staging area for the output of the normalization engine
-- CREATE TABLE IF NOT EXISTS earnings_data.staged_normalized_data (
--     id BIGSERIAL PRIMARY KEY,
--     doc_id BIGINT NOT NULL UNIQUE REFERENCES earnings_data.parsed_documents(doc_id) ON DELETE CASCADE,
--     ticker VARCHAR(20) NOT NULL,
--     fiscal_date DATE NOT NULL,
--     normalized_data JSONB NOT NULL,
--     data_hash VARCHAR(64), -- Hash of the normalized_data content to detect changes.
    
--     -- NEW: Three-phase normalization status tracking
--     statement_normalized BOOLEAN NOT NULL DEFAULT FALSE,
--     unit_review_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
--     label_review_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    
--     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
--     -- Status constraints
--     CONSTRAINT ck_unit_review_status CHECK (unit_review_status IN ('PENDING', 'AUTO_APPROVED', 'PENDING_REVIEW', 'APPROVED')),
--     CONSTRAINT ck_label_review_status CHECK (label_review_status IN ('PENDING', 'PENDING_REVIEW', 'APPROVED'))
-- );
-- COMMENT ON TABLE earnings_data.staged_normalized_data IS 'Intermediate staging table holding normalized data from a single source, ready for the Quality Engine.';
-- COMMENT ON COLUMN earnings_data.staged_normalized_data.statement_normalized IS 'Flag indicating whether statement normalization has been applied';
-- COMMENT ON COLUMN earnings_data.staged_normalized_data.unit_review_status IS 'Status of unit normalization: PENDING, AUTO_APPROVED, PENDING_REVIEW, or APPROVED';
-- COMMENT ON COLUMN earnings_data.staged_normalized_data.label_review_status IS 'Status of label normalization: PENDING, PENDING_REVIEW, or APPROVED';


-- -- NEW: Unit review queue for filing-level unit normalization review
-- CREATE TABLE IF NOT EXISTS earnings_data.unit_review_queue (
--     id BIGSERIAL PRIMARY KEY,
--     doc_id BIGINT NOT NULL REFERENCES earnings_data.parsed_documents(doc_id) ON DELETE CASCADE,
--     asset_id BIGINT NOT NULL REFERENCES earnings_data.raw_data_assets(asset_id),
--     ticker VARCHAR(20) NOT NULL,
--     fiscal_date DATE NOT NULL,
    
--     -- The LLM's analysis and raw filing data
--     llm_analysis JSONB NOT NULL,
--     filing_data JSONB NOT NULL,
    
--     -- Human review fields
--     status VARCHAR(20) NOT NULL DEFAULT 'PENDING_REVIEW',
--     reviewed_by TEXT,
--     reviewed_at TIMESTAMPTZ,
--     human_corrections JSONB,
    
--     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
--     CONSTRAINT ck_unit_review_queue_status CHECK (status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')),
--     UNIQUE(doc_id)
-- );
-- COMMENT ON TABLE earnings_data.unit_review_queue IS 'Queue for human review of unit normalization decisions on entire filings.';

-- CREATE INDEX IF NOT EXISTS idx_staged_normalization_status
--     ON earnings_data.staged_normalized_data(statement_normalized, unit_review_status, label_review_status);
-- CREATE INDEX IF NOT EXISTS idx_unit_review_pending 
--     ON earnings_data.unit_review_queue(status, created_at);

-- ================================================================================================
-- STAGE 3: QUALITY ENGINE
-- ================================================================================================
CREATE TABLE IF NOT EXISTS earnings_data.quality_engine_runs (
    -- Core Fields
    run_id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL UNIQUE REFERENCES earnings_data.parsed_documents(doc_id) ON DELETE CASCADE,

    -- === Stage-by-Stage Status Columns ===
    stage_1_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    stage_2_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    stage_3_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    stage_4_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    stage_5_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',

    -- === NEW: Per-Stage Versioning Columns ===
    -- These are stamped upon successful completion of each stage
    stage_1_version VARCHAR(20),
    stage_2_version VARCHAR(20),
    stage_3_version VARCHAR(20),
    stage_4_version VARCHAR(20),
    stage_5_version VARCHAR(20),

    -- === Audit & Debugging Fields ===
    failure_reason TEXT,
    details JSONB,
    playbook_config_hash VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast querying by the orchestrator script
CREATE INDEX IF NOT EXISTS idx_quality_engine_runs_statuses
ON earnings_data.quality_engine_runs (stage_1_status, stage_2_status, stage_3_status, stage_4_status, stage_5_status);

-- Trigger to automatically update the 'last_updated_at' timestamp
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_quality_engine_runs_modtime
BEFORE UPDATE ON earnings_data.quality_engine_runs
FOR EACH ROW EXECUTE FUNCTION update_modified_column();

-- Comments for clarity
COMMENT ON TABLE earnings_data.quality_engine_runs
IS 'State-tracking table for the multi-stage data quality and normalization engine. Drives the execution of the orchestrated validation scripts.';
COMMENT ON COLUMN earnings_data.quality_engine_runs.stage_1_version
IS 'The version of the Stage 1 script that successfully processed this run.';
-- ================================================================================================
-- STAGE 4: FINAL "GOLDEN RECORD" TABLES
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

-- ================================================================================================
-- DIMENSIONS & MASTER DATA
-- ================================================================================================

-- This table defines the unique industry classifications from the official source.
CREATE TABLE IF NOT EXISTS earnings_data.classifications (
    id SERIAL PRIMARY KEY,
    basic_industry_name TEXT NOT NULL UNIQUE,
    basic_industry_code VARCHAR(20),
    industry_name TEXT,
    industry_code VARCHAR(20),
    sector_name TEXT,
    sector_code VARCHAR(20),
    macro_economic_sector_name TEXT,
    mes_code VARCHAR(20),
    source_system TEXT DEFAULT 'NSE_2023', -- To track the source of the classification
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE earnings_data.classifications IS 'Master list of all official industry classifications, a single source of truth for categorization.';


-- This is the master list of all companies in your universe.
CREATE TABLE IF NOT EXISTS earnings_data.company_master (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL UNIQUE,
    company_name TEXT NOT NULL,
    isin_code VARCHAR(20) UNIQUE,
    listing_status VARCHAR(20) NOT NULL DEFAULT 'LISTED', -- e.g., LISTED, DELISTED
    bse_code VARCHAR(10) UNIQUE
    -- A single foreign key to the classifications table for context.
    classification_id INTEGER REFERENCES earnings_data.classifications(id),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE earnings_data.company_master IS 'Master dimension table for company profiles. Links to a classification for industry context.';


-- === INDEXES FOR PERFORMANCE ===

-- Index for fast company lookups by ticker
CREATE INDEX IF NOT EXISTS idx_company_master_ticker ON earnings_data.company_master(ticker);

-- Index for fast JOINs between companies and their classifications
CREATE INDEX IF NOT EXISTS idx_company_master_classification_id ON earnings_data.company_master(classification_id);