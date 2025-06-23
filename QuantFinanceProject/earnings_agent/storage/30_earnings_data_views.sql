-- earnings_agent/storage/30_earnings_data_views.sql

-- This VIEW unnests the JSONB content from parsed_earnings into a flat,
-- easy-to-query table for BI tools like Metabase.
-- Version 1.1: Added a two-step cast (::NUMERIC::BIGINT) to handle source
--              data that may contain decimal points for integer values.

CREATE OR REPLACE VIEW earnings_data.v_parsed_earnings_flat AS
SELECT
    -- Base table columns
    p.id,
    p.ticker,
    p.fiscal_date,
    p.parser_version,
    p.parsed_at,

    -- Unnesting the validation_summary
    p.content -> 'validation_summary' ->> 'status' AS validation_status,
    p.content -> 'parsing_summary' ->> 'status' AS parsing_status,

    -- Unnesting the core_metrics and casting to correct types
    -- CORRECTED: Cast to NUMERIC first to handle decimals, then to BIGINT.
    (p.content -> 'core_metrics' ->> 'revenue')::NUMERIC::BIGINT AS revenue,
    (p.content -> 'core_metrics' ->> 'net_income')::NUMERIC::BIGINT AS net_income,
    (p.content -> 'core_metrics' ->> 'ebitda')::NUMERIC::BIGINT AS ebitda,
    (p.content -> 'core_metrics' ->> 'profit_before_tax')::NUMERIC::BIGINT AS profit_before_tax,
    (p.content -> 'core_metrics' ->> 'earnings_per_share_diluted')::NUMERIC(18, 4) AS eps_diluted,

    -- Balance Sheet items
    (p.content -> 'core_metrics' ->> 'total_assets')::NUMERIC::BIGINT AS total_assets,
    (p.content -> 'core_metrics' ->> 'total_liabilities')::NUMERIC::BIGINT AS total_liabilities,
    (p.content -> 'core_metrics' ->> 'shareholders_equity')::NUMERIC::BIGINT AS shareholders_equity,
    (p.content -> 'core_metrics' ->> 'cash_and_equivalents')::NUMERIC::BIGINT AS cash_and_equivalents
    
FROM
    earnings_data.parsed_earnings p;

-- Add a comment to the VIEW for discoverability
COMMENT ON VIEW earnings_data.v_parsed_earnings_flat IS 'A flattened, user-friendly view of the parsed_earnings table, unnestiong the JSONB content for easy use in BI tools.';