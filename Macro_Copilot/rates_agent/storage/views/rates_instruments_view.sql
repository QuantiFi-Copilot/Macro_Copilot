-- ================================================================================================
-- VIEW 1: RATES INSTRUMENT UNIVERSE
-- Description: Flattens the JSONB metadata and restricts the universe to Rates instruments.
-- ================================================================================================

DROP VIEW IF EXISTS macro_data.v_rates_instruments CASCADE;

CREATE VIEW macro_data.v_rates_instruments AS
SELECT 
    ticker,
    asset_class,
    sub_class,
    country,
    currency,
    tenor,
    is_active,
    -- Flatten the JSONB attributes into standard SQL columns for the Python Agent
    (attributes->>'issue_date')::DATE AS issue_date,
    (attributes->>'maturity_date')::DATE AS maturity_date,
    (attributes->>'coupon_rate')::NUMERIC AS coupon_rate,
    (attributes->>'coupon_frequency')::VARCHAR AS coupon_frequency,
    (attributes->>'day_count_convention')::VARCHAR AS day_count_convention,
    (attributes->>'bloomberg_figi')::VARCHAR AS bloomberg_figi
FROM 
    macro_data.instrument_master
WHERE 
    -- This acts as the "security fence" for the agent. 
    -- Add 'Interest Rate Swap', 'OIS', etc., here as your playbook expands.
    asset_class IN ('Sovereign Bond'); 

COMMENT ON VIEW macro_data.v_rates_instruments IS 'Flattened metadata view restricted to fixed income instruments for the Rates Agent.';