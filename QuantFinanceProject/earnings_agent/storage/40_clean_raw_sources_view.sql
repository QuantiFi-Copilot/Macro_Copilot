-- earnings_agent/storage/40_raw_data_assets_clean_view.sql

-- This VIEW creates a user-friendly representation of the raw_data_assets table,
-- linking back to the ingestion job to provide context like ticker and date.
-- It prevents BI tools from auto-expanding the JSONB column.
-- Version 2.0: Adapted to the new modular schema with JOINs.

CREATE OR REPLACE VIEW earnings_data.v_raw_data_assets_clean AS
SELECT
    -- Select key columns from the raw asset and the job
    rda.asset_id,
    ij.ticker,
    ij.fiscal_year,
    ij.quarter,
    rda.source_type,
    rda.raw_data_hash, -- The true unique identifier of the asset
    rda.storage_location,
    rda.first_seen_at,
    ij.job_id, -- Including the job_id for full traceability

    -- Explicitly cast the JSONB column to JSON to signal to BI tools
    -- that it should be displayed as a single, contained object.
    rda.data_content::json
    
FROM
    -- Start with the assets table
    earnings_data.raw_data_assets rda

-- Use a LEFT JOIN to find the associated job information.
-- This ensures that even if an asset somehow exists without a job link, it still appears.
LEFT JOIN earnings_data.job_asset_link jal ON rda.asset_id = jal.asset_id
LEFT JOIN earnings_data.ingestion_jobs ij ON jal.job_id = ij.job_id

ORDER BY
    rda.first_seen_at DESC;

-- Add a comment to the VIEW for discoverability within your database.
COMMENT ON VIEW earnings_data.v_raw_data_assets_clean IS 'Version 2.0: A clean, user-friendly view of unique raw data assets, joined with job info for context. Keeps the data_content JSONB column collapsed for easy viewing in BI tools.';