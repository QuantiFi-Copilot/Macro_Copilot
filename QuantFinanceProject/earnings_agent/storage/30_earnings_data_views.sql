-- earnings_agent/storage/30_earnings_data_views.sql

-- This VIEW creates a comprehensive, flattened view of the entire pipeline.
-- It joins ingestion jobs, parsed documents, and validation results to provide
-- a single, easy-to-query source for BI tools and analysis.
-- Version 2.0: Adapted to the new modular schema.

CREATE OR REPLACE VIEW earnings_data.v_pipeline_results_flat AS
SELECT
    -- Metadata from the Ingestion Job (The "Expectation")
    ij.job_id,
    ij.ticker,
    ij.fiscal_year,
    ij.quarter,
    ij.source_type,
    ij.ingestion_script_version,

    -- IDs from the rest of the pipeline for traceability
    pd.asset_id,
    pd.doc_id,
    vr.validation_id,

    -- Status from each stage
    ij.status AS ingestion_status,
    pd.parse_status,
    vr.status AS validation_status,

    -- Versioning from each stage
    pd.parser_version,
    vr.validation_script_version,

    -- Timestamps
    pd.parsed_at,
    vr.validated_at,

    -- Unnesting the core_metrics from the parsed document's JSONB content
    -- Preserving the robust NUMERIC cast to handle source variations
    (pd.content -> 'core_metrics' ->> 'revenue')::NUMERIC::BIGINT AS revenue,
    (pd.content -> 'core_metrics' ->> 'net_income')::NUMERIC::BIGINT AS net_income,
    (pd.content -> 'core_metrics' ->> 'ebitda')::NUMERIC::BIGINT AS ebitda,
    (pd.content -> 'core_metrics' ->> 'profit_before_tax')::NUMERIC::BIGINT AS profit_before_tax,
    (pd.content -> 'core_metrics' ->> 'earnings_per_share_diluted')::NUMERIC(18, 4) AS eps_diluted,

    -- Balance Sheet items
    (pd.content -> 'core_metrics' ->> 'total_assets')::NUMERIC::BIGINT AS total_assets,
    (pd.content -> 'core_metrics' ->> 'total_liabilities')::NUMERIC::BIGINT AS total_liabilities,
    (pd.content -> 'core_metrics' ->> 'shareholders_equity')::NUMERIC::BIGINT AS shareholders_equity,
    (pd.content -> 'core_metrics' ->> 'cash_and_equivalents')::NUMERIC::BIGINT AS cash_and_equivalents

FROM
    -- Start with parsed documents as the central point
    earnings_data.parsed_documents pd
    
-- Join backwards to get the raw data asset and the original job metadata
LEFT JOIN earnings_data.raw_data_assets rda ON pd.asset_id = rda.asset_id
LEFT JOIN earnings_data.job_asset_link jal ON rda.asset_id = jal.asset_id
LEFT JOIN earnings_data.ingestion_jobs ij ON jal.job_id = ij.job_id

-- Join forwards to get the results of the validation stage
LEFT JOIN earnings_data.validation_results vr ON pd.doc_id = vr.doc_id;


-- Update the comment on the VIEW for discoverability
COMMENT ON VIEW earnings_data.v_pipeline_results_flat IS 'Version 2.0: A flattened, comprehensive view of the entire earnings pipeline, joining jobs, parsed docs, and validation results. Ideal for BI tools.';