DROP VIEW IF EXISTS macro_data.v_rates_instruments CASCADE;

CREATE VIEW macro_data.v_rates_instruments AS
SELECT
    instrument_id,
    vendor,
    vendor_ticker AS ticker,
    asset_class,
    instrument_type,
    curve_family,
    country,
    currency,
    tenor,
    underlying_index,
    contract_code,
    expiry_date,
    maturity_date,
    is_rolling_contract,
    is_active,
    attributes ->> 'security_name' AS security_name,
    attributes ->> 'contract_size' AS contract_size,
    attributes ->> 'quote_units' AS quote_units,
    attributes ->> 'bucket_label' AS bucket_label,
    created_at,
    updated_at
FROM macro_data.instrument_master
WHERE asset_class = 'rates';