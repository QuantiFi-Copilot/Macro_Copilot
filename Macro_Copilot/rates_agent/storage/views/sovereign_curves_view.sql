DROP VIEW IF EXISTS macro_data.v_sovereign_curves;

CREATE VIEW macro_data.v_sovereign_curves AS
SELECT 
    ts.trade_date,
    im.country,              -- Added this so Python can filter dynamically
    im.ticker,
    im.tenor,
    (im.attributes->>'issue_date')::DATE AS issue_date,
    (im.attributes->>'maturity_date')::DATE AS maturity_date,
    (im.attributes->>'coupon_rate')::NUMERIC AS coupon_rate,
    
    MAX(CASE WHEN ts.field_name = 'YLD_YTM_MID' THEN ts.field_value END) AS yield_to_maturity,
    MAX(CASE WHEN ts.field_name = 'PX_MID' THEN ts.field_value END) AS clean_price,
    MAX(CASE WHEN ts.field_name = 'DUR_ADJ_MID' THEN ts.field_value END) AS modified_duration,
    MAX(CASE WHEN ts.field_name = 'CONVEXITY_MID' THEN ts.field_value END) AS convexity

FROM 
    macro_data.market_data_timeseries ts
JOIN 
    macro_data.instrument_master im ON ts.ticker = im.ticker
WHERE 
    im.asset_class = 'Sovereign Bond' -- Filters out Swaps, FX, etc.
GROUP BY 
    ts.trade_date,
    im.country,
    im.ticker,
    im.tenor,
    im.attributes
ORDER BY 
    ts.trade_date DESC, 
    im.country,
    CASE im.tenor 
        WHEN '1M' THEN 1 WHEN '3M' THEN 2 WHEN '6M' THEN 3
        WHEN '1Y' THEN 4 WHEN '2Y' THEN 5 WHEN '3Y' THEN 6 
        WHEN '5Y' THEN 7 WHEN '7Y' THEN 8 WHEN '10Y' THEN 9 
        WHEN '20Y' THEN 10 WHEN '30Y' THEN 11
        ELSE 99 
    END;
