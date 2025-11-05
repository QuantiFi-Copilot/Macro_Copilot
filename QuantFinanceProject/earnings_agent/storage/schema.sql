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
CREATE TABLE IF NOT EXISTS earnings_data.label_mapping_cache (
    id BIGSERIAL PRIMARY KEY,
    raw_label TEXT NOT NULL,
    ticker TEXT NOT NULL,
    statement_key TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING_REVIEW',
    mapping_type TEXT NOT NULL,
    normalized_label TEXT NOT NULL,
    approved_by TEXT,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- This line is changed to add a specific name to the constraint
    CONSTRAINT uq_label_mapping_context UNIQUE (raw_label, ticker, statement_key)
);

COMMENT ON TABLE earnings_data.label_mapping_cache IS 'Stores human-approved, context-specific mappings for raw labels to prevent repeat LLM calls.';

-- ================================================================================================
-- STAGE 3: QUALITY ENGINE
-- ================================================================================================
CREATE TABLE IF NOT EXISTS earnings_data.quality_engine_runs (
    -- Core Fields
    run_id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL UNIQUE REFERENCES earnings_data.parsed_documents(doc_id) ON DELETE CASCADE,
    -- === NEW: Data & History Columns ===
    working_content JSONB,
    run_history JSONB,
    -- === Stage-by-Stage Status Columns ===
    stage_1_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    stage_2_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    stage_3_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    stage_4_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    -- === Per-Stage Versioning Columns ===
    stage_1_version VARCHAR(20),
    stage_2_version VARCHAR(20),
    stage_3_version VARCHAR(20),
    stage_4_version VARCHAR(20),
    -- === Audit & Debugging Fields ===
    failure_reason TEXT,
    playbook_config_hash VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_loaded_to_golden_record BOOLEAN NOT NULL DEFAULT FALSE
);
-- Comments for the new columns
COMMENT ON COLUMN earnings_data.quality_engine_runs.working_content
IS 'The working copy of the parsed document content, which is modified at each QE stage.';
COMMENT ON COLUMN earnings_data.quality_engine_runs.run_history
IS 'A JSONB array that serves as an immutable audit log of all transformations and checks.';

-- Index for fast querying by the orchestrator script
CREATE INDEX IF NOT EXISTS idx_quality_engine_runs_statuses
ON earnings_data.quality_engine_runs (stage_1_status, stage_2_status, stage_3_status, stage_4_status);

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

-- In schema.sql, after the quality_engine_runs table
CREATE TABLE IF NOT EXISTS earnings_data.quality_engine_rule_variants (
    id BIGSERIAL PRIMARY KEY,
    parent_playbook_id TEXT NOT NULL,
    issuer_ticker VARCHAR(20) NOT NULL,
    industry TEXT, -- For industry-wide rules later
    variant_definition JSONB NOT NULL,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parent_playbook_id, issuer_ticker)
);
COMMENT ON TABLE earnings_data.quality_engine_rule_variants 
IS 'Stores human-verified or learned calculation rule overrides for specific issuers.';

-- ================================================================================================
-- STAGE 4: FINAL "GOLDEN RECORD" TABLES
-- ================================================================================================
CREATE TABLE IF NOT EXISTS earnings_data.fundamental_records (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    period VARCHAR(10) NOT NULL,
    -- ADD THIS COLUMN
    consolidation_status VARCHAR(50) NOT NULL,
    filing_date DATE,
    version INT DEFAULT 1 NOT NULL,
    source_playbook VARCHAR(50) NOT NULL,
    source_run_id BIGINT REFERENCES earnings_data.quality_engine_runs(run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- UPDATE THE CONSTRAINT TO INCLUDE THE NEW COLUMN
    CONSTRAINT uq_fundamental_record UNIQUE(ticker, fiscal_date, version, consolidation_status)
);

COMMENT ON TABLE earnings_data.fundamental_records IS 'Central hub table for each unique financial record (company + period). Links all golden record tables together.';

CREATE TABLE IF NOT EXISTS earnings_data.fundamentals_banking (
    -- Primary key also links to the master record
    record_id BIGINT PRIMARY KEY REFERENCES earnings_data.fundamental_records(id) ON DELETE CASCADE,
    -- Profit & Loss (Banks)
    interest_or_discount_on_advances_or_bills BIGINT,
    revenue_on_investments BIGINT,
    interest_on_balances_with_reserve_bank_of_india_and_other_inter_bank_funds BIGINT,
    other_interest BIGINT,
    interest_earned BIGINT,
    other_income BIGINT,
    income BIGINT,
    interest_expended BIGINT,
    employees_cost BIGINT,
    other_operating_expenses BIGINT,
    operating_expenses BIGINT,
    total_expenditure_excluding_provisions_and_contingencies BIGINT,
    operating_profit_before_provision_and_contingencies BIGINT,
    provisions_other_than_tax_and_contingencies BIGINT,
    exceptional_items BIGINT,
    net_profit_loss_from_ordinary_activities_before_tax BIGINT,
    tax_expense BIGINT,
    profit_loss_from_ordinary_activities_after_tax BIGINT,
    extraordinary_items BIGINT,
    net_profit_loss_for_the_period BIGINT,
    share_of_profit_loss_of_associates BIGINT,
    net_profit_loss_after_taxes_before_minority_interest BIGINT,
    profit_loss_of_minority_interest BIGINT,
    profit_loss_after_taxes_minority_interest_and_share_of_profit_loss_of_associates BIGINT,
    paid_up_value_of_equity_share_capital BIGINT,
    face_value_of_equity_share_capital NUMERIC(18, 4),
    reserve_excluding_revaluation_reserves BIGINT,
    percentage_of_share_held_by_government_of_india NUMERIC(18, 4),
    cet1_ratio NUMERIC(18, 4),
    additional_tier1_ratio NUMERIC(18, 4),
    basic_earnings_per_share_before_extraordinary_items NUMERIC(18, 4),
    diluted_earnings_per_share_before_extraordinary_items NUMERIC(18, 4),
    basic_earnings_per_share_after_extraordinary_items NUMERIC(18, 4),
    diluted_earnings_per_share_after_extraordinary_items NUMERIC(18, 4),
    gross_non_performing_assets BIGINT,
    percentage_of_gross_npa NUMERIC(18, 4),
    net_non_performing_assets BIGINT,
    percentage_of_net_npa NUMERIC(18, 4),
    return_on_assets NUMERIC(18, 4),
    -- Balance Sheet
    capital BIGINT,
    reserves_and_surplus BIGINT,
    deposits BIGINT,
    borrowings BIGINT,
    other_liabilities_and_provisions BIGINT,
    total_capital_and_liabilities BIGINT,
    policyholder_funds BIGINT,
    cash_and_balances_with_reserve_bank_of_india BIGINT,
    balances_with_banks_and_money_at_call_and_short_notice BIGINT,
    investments BIGINT,
    advances BIGINT,
    fixed_assets BIGINT,
    other_assets BIGINT,
    total_assets BIGINT,
    -- Cash Flow (Indirect)
    profit_before_tax BIGINT,
    adjustments_for_depreciation_and_amortisation_expense BIGINT,
    profit_loss_on_revaluation_of_investments BIGINT,
    amortisation_of_premium_on_investments BIGINT,
    profit_loss_on_sale_of_fixed_assets BIGINT,
    profit_loss_on_sale_of_subsidiaries BIGINT,
    profit_loss_on_sale_of_investments BIGINT,
    provision_for_non_performing_assets BIGINT,
    provision_for_floating_provisions BIGINT,
    provision_for_standard_assets_and_contingencies BIGINT,
    dividend_income_from_subsidiaries BIGINT,
    adjustments_for_sharebased_payments BIGINT,
    other_adjustments_for_noncash_items BIGINT,
    adjustments_for_decrease_increase_in_advances BIGINT,
    adjustments_for_increase_decrease_in_deposits BIGINT,
    adjustments_for_decrease_increase_in_other_current_assets BIGINT,
    adjustments_for_increase_decrease_in_other_current_liabilities BIGINT,
    adjustments_for_decrease_increase_in_investments BIGINT,
    interest_received_classified_as_operating_activities BIGINT,
    interest_paid_classified_as_operating_activities BIGINT,
    income_taxes_paid_refund_classified_as_operating_activities BIGINT,
    net_cash_from_used_in_operating_activities BIGINT,
    purchase_of_tangible_assets_classified_as_investing_activities BIGINT,
    proceeds_from_sale_of_tangible_assets_classified_as_investing_activities BIGINT,
    purchase_of_investments_classified_as_investing_activities BIGINT,
    proceeds_from_sale_of_investments_classified_as_investing_activities BIGINT,
    dividends_received_classified_as_investing_activities BIGINT,
    other_inflows_outflows_of_cash_classified_as_investing_activities BIGINT,
    net_cash_from_used_in_investing_activities BIGINT,
    proceeds_from_issuing_shares BIGINT,
    proceeds_from_issuing_other_equity_instruments BIGINT,
    proceeds_from_issue_of_tier_1_and_tier_2_capital_instruments BIGINT,
    redemption_of_tier_1_and_tier_2_capital_instruments BIGINT,
    repayments_of_borrowings_classified_as_financing_activities BIGINT,
    proceeds_from_borrowings_classified_as_financing_activities BIGINT,
    dividends_paid_classified_as_financing_activities BIGINT,
    other_inflows_outflows_of_cash_classified_as_financing_activities BIGINT,
    net_cash_from_used_in_financing_activities BIGINT,
    effect_of_exchange_rate_changes_on_cash_and_cash_equivalents BIGINT,
    increase_decrease_in_cash_and_cash_equivalents BIGINT,
    cash_and_cash_equivalents_at_beginning_of_period BIGINT,
    cash_and_cash_equivalents_at_end_of_period BIGINT,
    -- Metadata
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS earnings_data.custom_kpis (
    id BIGSERIAL PRIMARY KEY,
    record_id BIGINT NOT NULL UNIQUE REFERENCES earnings_data.fundamental_records(id) ON DELETE CASCADE,
    kpi_data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE earnings_data.custom_kpis IS 'Flexible store for all non-standard KPIs, linked to a master fundamental record.';
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
    bse_code VARCHAR(10) UNIQUE,
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