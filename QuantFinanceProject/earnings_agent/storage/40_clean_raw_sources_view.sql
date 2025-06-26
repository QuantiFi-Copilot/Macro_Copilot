-- earnings_agent/storage/40_clean_raw_sources_view.sql

-- This VIEW creates a user-friendly, clean representation of the raw_sources table
-- specifically for BI tools like Metabase.
-- It prevents the automatic "un-nesting" of the raw_content JSONB column,
-- which makes the table difficult to read.

CREATE OR REPLACE VIEW earnings_data.v_raw_sources_clean AS
SELECT
    -- Select all the standard, non-JSON columns as they are.
    id,
    ticker,
    fiscal_date,
    source_type,
    source_url,
    local_path,
    created_at,

    -- Explicitly cast the JSONB column to JSON to signal to Metabase
    -- that it should display it as a single, contained object rather than
    -- trying to expand all its nested keys into separate columns.
    raw_content::json
    
FROM
    earnings_data.raw_sources
ORDER BY
    id DESC;

-- Add a comment to the VIEW for discoverability within your database.
COMMENT ON VIEW earnings_data.v_raw_sources_clean IS 'A clean, user-friendly view of the raw_sources table that keeps the raw_content JSONB column collapsed for easy viewing in BI tools.';