-- Create a dedicated schema for the Earnings Agent to keep data logically separated.
CREATE SCHEMA IF NOT EXISTS earnings_data;

-- A table to store the raw source documents for audit and reprocessing.
CREATE TABLE IF NOT EXISTS earnings_data.raw_documents (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    doc_type VARCHAR(50) NOT NULL, -- 'QUARTERLY_RESULTS_PDF', 'XBRL_INSTANCE'
    source_url TEXT,
    local_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, fiscal_date, doc_type)
);


-- Staging table to store the raw JSON output from any parser.
CREATE TABLE IF NOT EXISTS earnings_data.parsed_earnings (
    id SERIAL PRIMARY KEY,
    raw_document_id INTEGER REFERENCES earnings_data.raw_documents(id) ON DELETE CASCADE, -- Can be NULL for non-file sources
    
    -- Added to explicitly store the source type for easier querying and filtering.
    source_type VARCHAR(50) NOT NULL, -- e.g., 'XBRL', 'SCRAPED_NSE', 'PDF_LLM'
    
    parser_version VARCHAR(20) NOT NULL,
    parsed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content JSONB NOT NULL,

    -- Added ticker and fiscal_date to make the unique constraint more robust
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    
    -- A filing for a specific ticker and date should only be parsed once per source and parser version.
    UNIQUE(ticker, fiscal_date, source_type, parser_version)
);


-- The "Core" table for universally comparable financial data - Version 1.0
CREATE TABLE IF NOT EXISTS earnings_data.quarterly_fundamentals (
    id SERIAL PRIMARY KEY,
    -- ===== Metadata =====
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    period VARCHAR(10) NOT NULL,
    filing_date DATE,
    source VARCHAR(50) NOT NULL,
    version INT DEFAULT 1 NOT NULL,
    raw_document_id INTEGER REFERENCES earnings_data.raw_documents(id),

    -- ===== Income Statement =====
    revenue BIGINT,
    cost_of_goods_sold BIGINT,
    gross_profit BIGINT,
    operating_expenses BIGINT,
    ebitda BIGINT,
    depreciation_and_amortization BIGINT,
    ebit BIGINT,
    interest_expense BIGINT,
    profit_before_tax BIGINT,
    tax_expense BIGINT,
    net_income BIGINT,
    earnings_per_share_basic NUMERIC(18, 4),
    earnings_per_share_diluted NUMERIC(18, 4),

    -- ===== Balance Sheet =====
    cash_and_equivalents BIGINT,
    accounts_receivable BIGINT,
    inventory BIGINT,
    total_current_assets BIGINT,
    property_plant_equipment_net BIGINT,
    total_non_current_assets BIGINT,
    total_assets BIGINT,
    accounts_payable BIGINT,
    total_current_liabilities BIGINT,
    total_long_term_debt BIGINT,
    total_non_current_liabilities BIGINT,
    total_liabilities BIGINT,
    shareholders_equity BIGINT,
    total_liabilities_and_equity BIGINT,

    -- ===== Cash Flow Statement =====
    cash_flow_from_operating BIGINT,
    cash_flow_from_investing BIGINT,
    cash_flow_from_financing BIGINT,
    net_change_in_cash BIGINT,

    -- ===== Timestamps =====
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ===== Constraints =====
    UNIQUE(ticker, fiscal_date, version)
);

-- The "Satellite" table for all non-standard and industry-specific data.
CREATE TABLE IF NOT EXISTS earnings_data.custom_kpis (
    id SERIAL PRIMARY KEY,
    fundamental_id INTEGER NOT NULL REFERENCES earnings_data.quarterly_fundamentals(id) ON DELETE CASCADE,
    kpi_data JSONB NOT NULL,
    UNIQUE(fundamental_id)
);
