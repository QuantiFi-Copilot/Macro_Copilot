DROP VIEW IF EXISTS macro_data.v_sovereign_curves CASCADE;

CREATE VIEW macro_data.v_sovereign_curves AS
SELECT
    trade_date,
    country,
    currency,
    curve_family,
    vendor_ticker AS ticker,
    tenor,
    maturity_date,
    attributes ->> 'security_name' AS security_name,
    MAX(CASE WHEN field_name = 'YLD_YTM_MID' THEN field_value END) AS yield_to_maturity
FROM macro_data.v_market_data_daily_enriched
WHERE instrument_type = 'sovereign_benchmark'
GROUP BY
    trade_date,
    country,
    currency,
    curve_family,
    vendor_ticker,
    tenor,
    maturity_date,
    attributes ->> 'security_name';