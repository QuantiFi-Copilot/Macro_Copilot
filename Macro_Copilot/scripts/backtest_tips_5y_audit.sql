-- Deterministic audit for:
-- UST 2Y yield 252d z-score signal > threshold, long USD_TIPS 5Y,
-- short UST 2Y, 20 business-day hold, SOFR financing proxy.
--
-- This mirrors the repo's current backtest stack:
--   macro_data.v_market_data_daily_enriched
--   zscore_custom: 252-row rolling z-score, min_periods=60, ddof=1
--   threshold_events: event_rule below defaults to abs_above because
--     rates_agent/workflows/backtest/template.yaml locks rule=abs_above
--   construct_trades: Mon-Fri BDay exit, no holiday calendar
--   sovereign_yield_panel: YLD_YTM_MID, ffill up to 5 observed panel rows
--   financing_rate: USD_SOFR_OIS 1W PX_LAST
--   evaluate_trades/summarize_trades: final non-null P&L per trade

DROP TABLE IF EXISTS _bt_params;
CREATE TEMP TABLE _bt_params AS
SELECT
  DATE '2026-05-12' AS as_of_date,
  DATE '2019-01-01' AS price_start_date,
  DATE '2024-12-31' AS price_end_date,
  'UST'::text AS signal_curve_family,
  '2Y'::text AS signal_tenor,
  'YLD_YTM_MID'::text AS signal_field_name,
  252::int AS z_window_days,
  60::int AS z_min_periods,
  1825::int AS signal_lookback_days,
  1.5::double precision AS signal_threshold,
  'abs_above'::text AS event_rule,
  'USD_TIPS'::text AS long_curve_family,
  '5Y'::text AS long_tenor,
  'USD_TIPS_5Y'::text AS long_instrument_key,
  -1.0::double precision AS long_weight,
  'UST'::text AS short_curve_family,
  '2Y'::text AS short_tenor,
  'UST_2Y'::text AS short_instrument_key,
  1.0::double precision AS short_weight,
  20::int AS holding_window_business_days,
  'USD_SOFR_OIS'::text AS financing_proxy_curve,
  '1W'::text AS financing_proxy_tenor,
  'PX_LAST'::text AS financing_field_name,
  360.0::double precision AS financing_basis_days,
  5::int AS ffill_limit_rows;

DROP TABLE IF EXISTS _bt_source_coverage;
CREATE TEMP TABLE _bt_source_coverage AS
WITH required_series AS (
  SELECT 'signal_ust_2y' AS series_role, signal_curve_family AS curve_family, signal_tenor AS tenor, signal_field_name AS field_name FROM _bt_params
  UNION ALL SELECT 'long_tips_5y', long_curve_family, long_tenor, signal_field_name FROM _bt_params
  UNION ALL SELECT 'short_ust_2y', short_curve_family, short_tenor, signal_field_name FROM _bt_params
  UNION ALL SELECT 'financing_sofr_1w', financing_proxy_curve, financing_proxy_tenor, financing_field_name FROM _bt_params
)
SELECT
  r.series_role,
  r.curve_family,
  r.tenor,
  r.field_name,
  count(v.trade_date) AS row_count,
  min(v.trade_date) AS min_trade_date,
  max(v.trade_date) AS max_trade_date
FROM required_series r
LEFT JOIN macro_data.v_market_data_daily_enriched v
  ON v.curve_family = r.curve_family
 AND v.tenor = r.tenor
 AND v.field_name = r.field_name
GROUP BY r.series_role, r.curve_family, r.tenor, r.field_name
ORDER BY r.series_role;

DROP TABLE IF EXISTS _bt_trade_audit;
CREATE TEMP TABLE _bt_trade_audit AS
WITH signal_raw AS (
  SELECT v.trade_date::date AS trade_date, v.field_value::double precision AS y
  FROM macro_data.v_market_data_daily_enriched v
  CROSS JOIN _bt_params p
  WHERE v.curve_family = p.signal_curve_family
    AND v.tenor = p.signal_tenor
    AND v.field_name = p.signal_field_name
    AND v.trade_date >= p.as_of_date - (p.signal_lookback_days + (p.z_window_days * 1.5)::int)
),
signal_clean AS (
  SELECT trade_date, y
  FROM (
    SELECT trade_date, y, row_number() OVER (PARTITION BY trade_date ORDER BY trade_date) AS rn
    FROM signal_raw
    WHERE y IS NOT NULL
  ) x
  WHERE rn = 1
),
signal_roll AS (
  SELECT
    trade_date,
    y,
    count(y) OVER (ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS obs_in_window,
    avg(y) OVER (ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS rolling_mean,
    stddev_samp(y) OVER (ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS rolling_std
  FROM signal_clean
),
signal_z AS (
  SELECT
    r.trade_date,
    r.y AS signal_yield_pct,
    CASE
      WHEN r.obs_in_window >= p.z_min_periods AND r.rolling_std IS NOT NULL AND r.rolling_std <> 0
      THEN round(((r.y - r.rolling_mean) / r.rolling_std)::numeric, 4)::double precision
      ELSE NULL
    END AS z_score
  FROM signal_roll r
  CROSS JOIN _bt_params p
  WHERE r.trade_date >= p.as_of_date - p.signal_lookback_days
),
events AS (
  SELECT
    row_number() OVER (ORDER BY z.trade_date) - 1 AS trade_idx,
    z.trade_date AS entry_date,
    z.signal_yield_pct,
    z.z_score
  FROM signal_z z
  CROSS JOIN _bt_params p
  WHERE z.z_score IS NOT NULL
    AND CASE p.event_rule
      WHEN 'above' THEN z.z_score > p.signal_threshold
      WHEN 'abs_above' THEN abs(z.z_score) > p.signal_threshold
      ELSE false
    END
),
business_calendar AS (
  SELECT d::date AS cal_date
  FROM _bt_params p,
       generate_series(p.price_start_date, p.price_end_date + interval '90 days', interval '1 day') AS d
  WHERE extract(isodow FROM d) < 6
),
trades AS (
  SELECT
    e.trade_idx,
    e.entry_date,
    x.cal_date AS exit_date,
    e.signal_yield_pct,
    e.z_score
  FROM events e
  CROSS JOIN _bt_params p
  LEFT JOIN LATERAL (
    SELECT b.cal_date
    FROM business_calendar b
    WHERE b.cal_date > e.entry_date
    ORDER BY b.cal_date
    OFFSET (p.holding_window_business_days - 1)
    LIMIT 1
  ) x ON true
),
leg_specs AS (
  SELECT long_instrument_key AS leg_key, long_curve_family AS curve_family, long_tenor AS tenor, signal_field_name AS field_name, long_weight AS weight FROM _bt_params
  UNION ALL
  SELECT short_instrument_key, short_curve_family, short_tenor, signal_field_name, short_weight FROM _bt_params
),
leg_obs AS (
  SELECT
    v.trade_date::date AS trade_date,
    l.leg_key,
    v.field_value::double precision AS value
  FROM macro_data.v_market_data_daily_enriched v
  JOIN leg_specs l
    ON v.curve_family = l.curve_family
   AND v.tenor = l.tenor
   AND v.field_name = l.field_name
  CROSS JOIN _bt_params p
  WHERE v.trade_date BETWEEN p.price_start_date AND p.price_end_date
    AND extract(isodow FROM v.trade_date) < 6
),
panel_dates AS (
  SELECT DISTINCT trade_date FROM leg_obs
),
panel_grid AS (
  SELECT d.trade_date, l.leg_key
  FROM panel_dates d
  CROSS JOIN leg_specs l
),
panel_base AS (
  SELECT g.trade_date, g.leg_key, o.value
  FROM panel_grid g
  LEFT JOIN leg_obs o
    ON o.trade_date = g.trade_date
   AND o.leg_key = g.leg_key
),
panel_grouped AS (
  SELECT
    trade_date,
    leg_key,
    value,
    count(value) OVER (PARTITION BY leg_key ORDER BY trade_date) AS fill_group
  FROM panel_base
),
panel_filled AS (
  SELECT
    trade_date,
    leg_key,
    CASE
      WHEN fill_group = 0 THEN NULL
      WHEN row_number() OVER (PARTITION BY leg_key, fill_group ORDER BY trade_date) - 1 <= (SELECT ffill_limit_rows FROM _bt_params)
      THEN max(value) OVER (PARTITION BY leg_key, fill_group)
      ELSE value
    END AS value
  FROM panel_grouped
),
price_panel AS (
  SELECT
    trade_date,
    max(value) FILTER (WHERE leg_key = (SELECT long_instrument_key FROM _bt_params)) AS long_px,
    max(value) FILTER (WHERE leg_key = (SELECT short_instrument_key FROM _bt_params)) AS short_px
  FROM panel_filled
  GROUP BY trade_date
),
financing AS (
  SELECT v.trade_date::date AS trade_date, v.field_value::double precision AS rate_pct
  FROM macro_data.v_market_data_daily_enriched v
  CROSS JOIN _bt_params p
  WHERE v.curve_family = p.financing_proxy_curve
    AND v.tenor = p.financing_proxy_tenor
    AND v.field_name = p.financing_field_name
    AND v.trade_date BETWEEN p.price_start_date AND p.price_end_date
),
trades_priced AS (
  SELECT
    t.*,
    ep.long_px AS entry_long_px,
    ep.short_px AS entry_short_px
  FROM trades t
  LEFT JOIN price_panel ep ON ep.trade_date = t.entry_date
),
trade_rows AS (
  SELECT
    t.trade_idx,
    t.entry_date,
    t.exit_date,
    t.signal_yield_pct,
    t.z_score,
    t.entry_long_px,
    t.entry_short_px,
    p.trade_date AS pnl_date,
    p.long_px,
    p.short_px,
    coalesce(f.rate_pct, 0.0) AS financing_rate_pct
  FROM trades_priced t
  LEFT JOIN price_panel p
    ON p.trade_date BETWEEN t.entry_date AND t.exit_date
  LEFT JOIN financing f
    ON f.trade_date = p.trade_date
),
trade_daily AS (
  SELECT
    r.*,
    CASE
      WHEN r.entry_long_px IS NULL OR r.entry_short_px IS NULL OR r.pnl_date IS NULL THEN NULL
      ELSE
        (SELECT long_weight FROM _bt_params) * coalesce(r.long_px - r.entry_long_px, 0.0)
        + (SELECT short_weight FROM _bt_params) * coalesce(r.short_px - r.entry_short_px, 0.0)
        + sum(
            CASE
              WHEN r.pnl_date = r.entry_date THEN 0.0
              ELSE -1.0 * ((SELECT long_weight FROM _bt_params) + (SELECT short_weight FROM _bt_params))
                   * r.financing_rate_pct / (SELECT financing_basis_days FROM _bt_params)
            END
          ) OVER (PARTITION BY r.trade_idx ORDER BY r.pnl_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    END AS pnl_app_value
  FROM trade_rows r
),
final_pnl AS (
  SELECT DISTINCT ON (trade_idx)
    trade_idx,
    pnl_date AS final_pnl_date,
    pnl_app_value AS final_pnl_app_value,
    count(pnl_app_value) OVER (PARTITION BY trade_idx) AS nonnull_pnl_observations
  FROM trade_daily
  WHERE pnl_app_value IS NOT NULL
  ORDER BY trade_idx, pnl_date DESC
)
SELECT
  t.trade_idx,
  t.entry_date,
  t.exit_date,
  t.signal_yield_pct,
  t.z_score,
  t.entry_long_px,
  t.entry_short_px,
  f.final_pnl_date,
  f.final_pnl_app_value,
  f.final_pnl_app_value * 100.0 AS final_pnl_true_bps_if_yields_are_percent,
  f.nonnull_pnl_observations,
  CASE
    WHEN f.final_pnl_app_value IS NOT NULL THEN 'valid'
    WHEN t.entry_long_px IS NULL OR t.entry_short_px IS NULL THEN 'missing_entry_price'
    WHEN t.exit_date IS NULL THEN 'missing_exit_date'
    ELSE 'no_pnl_observations'
  END AS trade_status
FROM trades_priced t
LEFT JOIN final_pnl f USING (trade_idx)
ORDER BY t.trade_idx;

SELECT * FROM _bt_source_coverage;

WITH valid AS (
  SELECT * FROM _bt_trade_audit WHERE final_pnl_app_value IS NOT NULL
), ordered AS (
  SELECT
    trade_idx,
    final_pnl_app_value,
    sum(final_pnl_app_value) OVER (ORDER BY trade_idx ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_pnl
  FROM valid
), drawdowns AS (
  SELECT
    cumulative_pnl - max(cumulative_pnl) OVER (ORDER BY trade_idx ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS drawdown
  FROM ordered
), stats AS (
  SELECT
    count(*) AS valid_trades,
    avg(CASE WHEN final_pnl_app_value >= 0 THEN 1.0 ELSE 0.0 END) AS hit_rate,
    avg(final_pnl_app_value) AS mean_pnl,
    stddev_samp(final_pnl_app_value) AS std_pnl,
    percentile_cont(0.10) WITHIN GROUP (ORDER BY final_pnl_app_value) AS p10_pnl,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY final_pnl_app_value) AS p50_pnl,
    percentile_cont(0.90) WITHIN GROUP (ORDER BY final_pnl_app_value) AS p90_pnl
  FROM valid
)
SELECT
  CASE WHEN EXISTS (SELECT 1 FROM _bt_source_coverage WHERE row_count = 0)
       THEN 'MISSING_REQUIRED_MARKET_DATA'
       ELSE 'OK'
  END AS data_status,
  (SELECT event_rule FROM _bt_params) AS event_rule_used,
  (SELECT z_window_days FROM _bt_params) AS z_window_days,
  (SELECT holding_window_business_days FROM _bt_params) AS holding_window_business_days,
  (SELECT count(*) FROM _bt_trade_audit) AS constructed_trades,
  stats.valid_trades,
  round((100.0 * stats.hit_rate)::numeric, 6) AS hit_rate_pct,
  stats.mean_pnl AS mean_pnl_app_reported_value,
  stats.mean_pnl * 100.0 AS mean_pnl_true_bps_if_yields_are_percent,
  CASE WHEN stats.std_pnl IS NOT NULL AND stats.std_pnl > 0
       THEN (stats.mean_pnl / stats.std_pnl) * sqrt(252.0 / (SELECT holding_window_business_days FROM _bt_params))
       ELSE NULL
  END AS sharpe_annualized,
  (SELECT min(drawdown) FROM drawdowns) AS max_drawdown_app_reported_value,
  (SELECT min(drawdown) * 100.0 FROM drawdowns) AS max_drawdown_true_bps_if_yields_are_percent,
  stats.p10_pnl AS p10_pnl_app_reported_value,
  stats.p50_pnl AS p50_pnl_app_reported_value,
  stats.p90_pnl AS p90_pnl_app_reported_value,
  stats.p10_pnl * 100.0 AS p10_true_bps_if_yields_are_percent,
  stats.p50_pnl * 100.0 AS p50_true_bps_if_yields_are_percent,
  stats.p90_pnl * 100.0 AS p90_true_bps_if_yields_are_percent
FROM stats;

SELECT * FROM _bt_trade_audit ORDER BY trade_idx;
