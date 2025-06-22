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
    raw_text_content TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- NEW: This constraint prevents duplicate logs for the same filing.
    UNIQUE (ticker, fiscal_date, doc_type)
);


-- The "Core" table for universally comparable financial data.
CREATE TABLE IF NOT EXISTS earnings_data.quarterly_fundamentals (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    period VARCHAR(10) NOT NULL,
    filing_date DATE,
    standard_revenue BIGINT,
    standard_net_income BIGINT,
    total_assets BIGINT,
    total_liabilities BIGINT,
    operating_cash_flow BIGINT,
    source VARCHAR(50) NOT NULL, -- e.g., 'XBRL_NSE', 'PDF_OCR_LLM', 'MANUAL_VERIFIED'
    version INT DEFAULT 1 NOT NULL, -- For handling restatements
    raw_document_id INTEGER REFERENCES earnings_data.raw_documents(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, fiscal_date, version)
);

-- The "Satellite" table for all non-standard and industry-specific data.
CREATE TABLE IF NOT EXISTS earnings_data.custom_kpis (
    id SERIAL PRIMARY KEY,
    fundamental_id INTEGER NOT NULL REFERENCES earnings_data.quarterly_fundamentals(id) ON DELETE CASCADE,
    kpi_data JSONB NOT NULL, -- Stores all other metrics, e.g., {"gross_npa": 2.1, "source_label_for_revenue": "Interest Earned"}
    UNIQUE(fundamental_id)
);
