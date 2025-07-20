-- This VIEW creates a comprehensive, flattened overview of the entire earnings data pipeline.
-- It joins every major table in the workflow, from the initial job to the final quality check,
-- providing a single, easy-to-query source for BI tools, monitoring, and analysis.
-- Version 3.0: Re-architected for the multi-stage ingestion, parsing, normalization, and quality engine workflow.

CREATE OR REPLACE VIEW earnings_data.v_pipeline_overview AS
SELECT
    -- === Stage 1: Ingestion Job (The "Expectation") ===
    ij.job_id,
    ij.ticker,
    ij.fiscal_year,
    ij.quarter,
    ij.source_type,
    ij.ingestion_script_version,
    ij.status AS ingestion_status,
    ij.failure_reason AS ingestion_failure_reason,
    ij.created_at AS job_created_at,

    -- === Stage 2: Parsing (Raw Fact Extraction) ===
    pd.doc_id,
    pd.parse_status,
    pd.parser_version,
    pd.parsed_at,
    (pd.content -> 'parsing_summary' ->> 'total_facts_extracted')::INT AS raw_facts_extracted,

    -- === Stage 3: Normalization (Staging Area) ===
    snd.id AS staged_data_id,
    -- The existence of a staged_data_id indicates normalization was successful for this source.
    CASE
        WHEN snd.id IS NOT NULL THEN 'NORMALIZED'
        ELSE NULL
    END AS normalization_status,
    (jsonb_object_keys(snd.normalized_data)) AS normalized_metric_name,
    snd.normalized_data ->> (jsonb_object_keys(snd.normalized_data)) AS normalized_metric_value,

    -- === Stage 4: Quality Engine (Reconciliation & Validation Summary) ===
    qer.quality_run_id,
    qer.status AS quality_engine_status,
    qer.engine_version AS quality_engine_version,
    qer.summary AS quality_engine_summary,
    qer.completed_at AS quality_engine_completed_at,
    
    -- === Traceability IDs ===
    rda.asset_id,
    rda.raw_data_hash

FROM
    -- Start with the job as the primary record of intent
    earnings_data.ingestion_jobs ij

-- Join "forwards" through the pipeline stages
LEFT JOIN earnings_data.job_asset_link jal ON ij.job_id = jal.job_id
LEFT JOIN earnings_data.raw_data_assets rda ON jal.asset_id = rda.asset_id
LEFT JOIN earnings_data.parsed_documents pd ON rda.asset_id = pd.asset_id
LEFT JOIN earnings_data.staged_normalized_data snd ON pd.doc_id = snd.doc_id

-- The Quality Engine result is linked by the logical entity (ticker + date), not a direct ID.
-- This correctly associates the summary of a run with all the source documents that fed into it.
LEFT JOIN earnings_data.quality_engine_results qer
    ON ij.ticker = qer.ticker
    AND (
        -- This logic correctly reconstructs the fiscal_date from the job info to join with the quality_engine_results
        CASE
            WHEN ij.quarter = 1 THEN make_date(ij.fiscal_year, 6, 30)
            WHEN ij.quarter = 2 THEN make_date(ij.fiscal_year, 9, 30)
            WHEN ij.quarter = 3 THEN make_date(ij.fiscal_year, 12, 31)
            ELSE make_date(ij.fiscal_year + 1, 3, 31)
        END
    ) = qer.fiscal_date;


-- Update the comment on the VIEW for discoverability
COMMENT ON VIEW earnings_data.v_pipeline_overview IS 'Version 3.0: A comprehensive, flattened view of the entire earnings pipeline, from job creation through parsing, normalization, and the final quality engine summary. Ideal for BI tools and operational monitoring.';