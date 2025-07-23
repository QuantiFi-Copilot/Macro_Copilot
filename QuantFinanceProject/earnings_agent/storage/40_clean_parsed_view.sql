-- ================================================================================================
-- View: v_parsed_document_summary
-- Version: 2.0
-- Description: This version FORCES the `content` column to be treated as plain text
--              to prevent Metabase's auto-expansion feature.
--
-- Change: Casts the `content` JSONB column to TEXT using `::TEXT`.
-- ================================================================================================

CREATE OR REPLACE VIEW earnings_data.v_parsed_document_summary AS
SELECT
    pd.doc_id,
    pd.asset_id,
    ij.job_id,
    cm.company_name,
    ij.ticker,
    ij.fiscal_year,
    ij.quarter,
    ij.consolidation_status,
    pd.parse_status,
    pd.parser_version,
    pd.parsed_at,
    pd.error_details,
    -- ▼▼▼ THE ONLY CHANGE IS ON THIS LINE ▼▼▼
    pd.content::TEXT AS content
FROM
    earnings_data.parsed_documents pd
JOIN
    earnings_data.job_asset_link jal ON pd.asset_id = jal.asset_id
JOIN
    earnings_data.ingestion_jobs ij ON jal.job_id = ij.job_id
JOIN
    earnings_data.company_master cm ON ij.ticker = cm.ticker
ORDER BY
    pd.parsed_at DESC;

