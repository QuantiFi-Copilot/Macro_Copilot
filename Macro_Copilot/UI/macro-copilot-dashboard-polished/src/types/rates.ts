// ============================================================================
// Rates API Response Types
// These types mirror the FastAPI Pydantic response models exactly.
// Any change to the API schemas must be reflected here.
// ============================================================================

// --- Shared ---

export type SparklinePoint = {
  date: string;
  value: number;
};

// --- Card 1: Yield Snapshot Grid ---

export type YieldSnapshotRow = {
  curve_family: string;
  tenor: string;
  yield_pct: number | null;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  z_score: number | null;
  high_252d_pct: number | null;
  low_252d_pct: number | null;
  percentile_252d: number | null;
  as_of_date: string | null;
};

export type YieldSnapshotResponse = {
  rows: YieldSnapshotRow[];
  curve_families: string[];
  tenors: string[];
};

// --- Card 2: Curve Shapes ---

export type CurveShapeRow = {
  curve_family: string;
  spread_label: string;
  spread_bps: number | null;
  daily_change_bps: number | null;
  z_score: number | null;
  short_tenor_yield: number | null;
  long_tenor_yield: number | null;
  as_of_date: string | null;
  sparkline: SparklinePoint[];
};

export type CurveShapesResponse = {
  curves: CurveShapeRow[];
};

// --- Card 3: Scanner ---

export type ScannerResultRow = {
  rank: number;
  curve_family: string;
  tenor: string;
  yield_pct: number | null;
  daily_change_bps: number | null;
  z_score: number | null;
  percentile_252d: number | null;
  signal: string;
  as_of_date: string | null;
};

export type ScannerResponse = {
  summary: string;
  results: ScannerResultRow[];
};

// --- Card 4: Cross-Market Spreads ---

export type CrossMarketRow = {
  spread_label: string;
  curve_family_1: string;
  curve_family_2: string;
  tenor: string;
  spread_bps: number | null;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  z_score: number | null;
  percentile_252d: number | null;
  as_of_date: string | null;
  sparkline: SparklinePoint[];
};

export type CrossMarketResponse = {
  pairs: CrossMarketRow[];
};

// --- Card 5: Regime Monitor ---

export type RegimeRow = {
  curve_family: string;
  lookback_period: string;
  regime_tag: string;
  regime_description: string;
  spread_label: string;
  front_tenor: string;
  back_tenor: string;
  front_change_bps: number | null;
  back_change_bps: number | null;
  spread_change_bps: number | null;
  as_of_date: string | null;
};

export type RegimeResponse = {
  regimes: RegimeRow[];
};

// --- Aggregated page data ---

export type RatesPageData = {
  yieldSnapshot: YieldSnapshotResponse;
  curveShapes: CurveShapesResponse;
  scanner: ScannerResponse;
  crossMarket: CrossMarketResponse;
  regimes: RegimeResponse;
};

// ============================================================================
// WORKSPACE DETAIL ENDPOINT TYPES
// Mirror the Pydantic schemas in rates_agent/sovereign_bonds/tools/schemas/.
// Returned by /api/v1/rates/detail/* — these include the full time_series
// the MCP server strips before sending to the LLM.
// ============================================================================

// --- Canonical TimeSeries shape (mirrors shared/schemas/time_series.py) ---
//
// PR-E — added to support the YieldLevelOutput.time_series field that the
// backend has been emitting since the legacy-TimeSeries cleanup but that
// the frontend was not yet declaring or consuming.  Other detail endpoints
// (curve_spread, cross_market, butterfly) still ship bespoke per-tool row
// arrays (see CurveSpreadTimeSeriesRow etc.) — that retro-fit to this
// canonical shape is deferred to a separate cleanup PR (see schemas.py
// docstring on TimeSeries).
//
// ``units`` is the closed enum from shared.schemas.time_series.TimeSeriesUnits.
// Typed here as a wide string so frontend code that just displays a unit
// label (% / bps / etc.) doesn't need to enumerate every Python enum
// value.  When a typed switch is needed downstream, narrow at the call site.

export type TimeSeriesRow = {
  /** Trade date in YYYY-MM-DD form. */
  date: string;
  /**
   * Observation value in the series' ``units``.  ``null`` when the
   * tool emits a gap (e.g., warmup period for a rolling stat or a
   * missing trading-day observation).
   */
  value: number | null;
};

export type TimeSeries = {
  /** Canonical lower-snake-case identifier — e.g. ``ust_10y_yield``. */
  series_name: string;
  /** Unit label from the closed Python enum (e.g. ``percent``, ``bps``). */
  units: string;
  /** One-line human-readable description of what this series represents. */
  description: string;
  /** Observations in chronological order. May be empty when the window has no data. */
  rows: TimeSeriesRow[];
};

// --- /detail/yield ---

export type YieldLevelMetrics = {
  as_of_date: string;
  curve_family: string;
  tenor: string;
  current_yield_pct: number;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  z_score: number | null;
  high_252d_pct: number | null;
  low_252d_pct: number | null;
  percentile_252d: number | null;
  observation_count: number;
};

export type YieldLevelOutput = {
  current_metrics: YieldLevelMetrics;
  /**
   * Historical yield levels at the requested tenor over the display
   * window (last ``lookback_days`` calendar days, cleaned + ffilled,
   * rounded to match ``current_metrics.current_yield_pct`` exactly at
   * the latest row).  Units are ``percent`` (closed-enum
   * ``TimeSeriesUnits.PERCENT`` on the backend).
   *
   * Optional in the TS type for defensive resilience against stale /
   * cached payloads that pre-date the canonical-TimeSeries cleanup —
   * the backend Pydantic schema marks this required, so on the live
   * wire it is always present.
   */
  time_series?: TimeSeries;
};

// --- /detail/inflation-swap-rate-level (standalone bridge) ---
//
// Mirrors rates_agent/inflation_swaps/tools/inflation_swap_rate_level/schemas.py.
// Per the methodology-exposure standalone-bridge contract
// (docs_revamped/03_standards/methodology_exposure.md §5) the ZCIS
// rate-level primitive ships its own typed-detail endpoint at
// /api/v1/rates/detail/inflation-swap-rate-level and its OWN frontend
// type — no reuse of the sovereign YieldLevelOutput or OIS rate-level
// type.  The underlying instrument family is different: a zero-coupon
// inflation swap, NOT a bond yield or an OIS par-swap rate.  The wire
// field is ``zcis_rate_pct`` (not ``current_yield_pct`` or
// ``current_rate_pct``) so downstream operator panels cannot silently
// mix ZCIS rates with nominal yields, OIS rates, or linker real yields.
//
// The wire surface carries the LOAD-BEARING reference metadata
// (``inflation_index_family`` / ``index_lag`` / ``interpolation`` /
// ``underlying_index``) so a desk reader can interpret the level
// honestly: USD_ZCIS references US_CPI_URBAN with a 3M lag and daily
// interpolation; EUR_ZCIS references EU_HICP (ex-tobacco) with a 3M
// lag and monthly interpolation; GBP_ZCIS references UK_RPI with a 2M
// lag and monthly interpolation — these conventions are NOT comparable
// cross-curve without harmonising index family / lag / interpolation.

export type InflationSwapRateLevelMetrics = {
  as_of_date: string;
  curve_family: string;
  tenor: string;
  /** Current zero-coupon inflation swap rate in percent.  Quoted as
   *  the par rate the swap pays for inflation compensation over
   *  ``tenor``; structurally distinct from the linker bond-implied
   *  breakeven and from a pure expected-inflation read. */
  zcis_rate_pct: number;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score; conventions YAML-locked on this
   *  primitive (no input-layer overrides — mirrors the OIS rate_level
   *  / sibling level tools). */
  z_score: number | null;
  high_252d_pct: number | null;
  low_252d_pct: number | null;
  percentile_252d: number | null;
  observation_count: number;
  /** Inflation index family the ZCIS references, from instrument_master
   *  attributes.  Examples: 'US_CPI_URBAN' (USD_ZCIS), 'EU_HICP'
   *  (EUR_ZCIS), 'UK_RPI' (GBP_ZCIS).  Load-bearing: surfaced on the
   *  wire so the desk can interpret the level honestly — these are
   *  distinct inflation references and the rates are NOT directly
   *  comparable cross-curve without harmonising index family / lag /
   *  interpolation. */
  inflation_index_family: string;
  /** Indexation lag the ZCIS references.  Examples: '3M' (USD_ZCIS,
   *  EUR_ZCIS), '2M' (GBP_ZCIS).  Different lags mean the rates are
   *  quoted against differently dated index fixings. */
  index_lag: string;
  /** Index-fixing interpolation convention.  Examples: 'Daily'
   *  (USD_ZCIS), 'Monthly' (EUR_ZCIS, GBP_ZCIS). */
  interpolation: string;
  /** Underlying inflation index Bloomberg ticker (instrument_master's
   *  ``underlying_index`` column).  Examples: 'CPURNSA Index'
   *  (USD_ZCIS), 'CPTFEMU Index' (EUR_ZCIS), 'UKRPI Index'
   *  (GBP_ZCIS).  Optional defensively; present on the live wire. */
  underlying_index: string | null;
  /** Wire-honesty disclosure threaded from the YAML's
   *  ``methodology.what_it_does``.  NOT a hardcoded TS literal — a
   *  YAML edit flows through to runtime. */
  methodology_label: string;
};

export type InflationSwapRateLevelOutput = {
  current_metrics: InflationSwapRateLevelMetrics;
  /** Historical ZCIS rate levels at the requested ``(curve_family,
   *  tenor)`` pillar over the displayed ``lookback_days`` window.
   *  Closed-enum ``TimeSeriesUnits.PERCENT`` units; series_name
   *  follows ``<curve_family_lower>_<tenor_lower>_zcis_rate`` so
   *  downstream operator panels cannot silently mix ZCIS rates with
   *  nominal sovereign yield, OIS, or linker real-yield series.  Each
   *  row rounded with the same ``yield_round_decimals`` convention
   *  the snapshot uses so the latest row matches
   *  ``current_metrics.zcis_rate_pct`` STRICTLY (pinned by
   *  rates_agent/inflation_swaps/tools/inflation_swap_rate_level/compute.py).
   *
   *  Optional in the TS type for defensive resilience against stale /
   *  cached payloads; the backend Pydantic schema marks this required. */
  time_series?: TimeSeries;
};

// --- /detail/inflation-swap-curve-spread (standalone bridge) ---
//
// Mirrors rates_agent/inflation_swaps/tools/inflation_swap_curve_spread/schemas.py
// (InflationSwapCurveSpreadOutput / InflationSwapCurveSpreadCurrentMetrics /
// InflationSwapCurveSpreadTimeSeriesRow).  Per the methodology-exposure
// standalone-bridge contract (docs_revamped/03_standards/methodology_exposure.md
// §5) the ZCIS curve-spread primitive ships its own typed-detail endpoint at
// /api/v1/rates/detail/inflation-swap-curve-spread and its OWN frontend type —
// no reuse of BreakevenCurveSpreadOutput (that's bond-implied breakeven curve
// shape; this is OTC inflation-swap curve shape; the underlying instrument
// families are distinct).  Same-curve, two-tenor primitive: a single
// ``curve_family`` (USD_ZCIS / EUR_ZCIS / GBP_ZCIS) + two strictly-ordered
// tenors.  Cross-curve combinations are a separate primitive
// (CrossMarketInflationSwapSpread).  Units: BPS.

export type InflationSwapCurveSpreadCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  short_tenor: string;
  long_tenor: string;
  /** Human-readable label, e.g. "USD_ZCIS 5s10s". */
  spread_label: string;
  /** Current ZCIS curve spread in BPS.  Computed as
   *  ``(long_zcis_pct - short_zcis_pct) * 100``.  POSITIVE = upward-
   *  sloping forward inflation (long-tenor implied inflation higher
   *  than short-tenor); NEGATIVE = inverted. */
  spread_bps: number;
  change_1d_bps: number | null;
  change_1w_bps: number | null;
  change_1m_bps: number | null;
  /** Rolling 252-trading-day z-score of the spread (bps); conventions
   *  YAML-locked on this primitive (no input-layer overrides — mirrors
   *  the sibling level + curve-spread tools). */
  z_score_252d: number | null;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  /** Endpoint ZCIS rates used to form the spread (PERCENT). */
  short_zcis_rate_pct: number | null;
  long_zcis_rate_pct: number | null;
  short_years: number;
  long_years: number;
  observation_count: number;
  /** Inflation index family the ZCIS curve references (shared by BOTH
   *  legs by the same-curve invariant).  Examples: 'US_CPI_URBAN'
   *  (USD_ZCIS), 'EU_HICP' (EUR_ZCIS), 'UK_RPI' (GBP_ZCIS).  Load-
   *  bearing — surfaced so the desk can interpret the spread honestly. */
  inflation_index_family: string;
  /** Indexation lag shared by both legs.  Examples: '3M' (USD_ZCIS,
   *  EUR_ZCIS), '2M' (GBP_ZCIS). */
  index_lag: string;
  /** Index-fixing interpolation convention shared by both legs.  Examples:
   *  'Daily' (USD_ZCIS), 'Monthly' (EUR_ZCIS, GBP_ZCIS). */
  interpolation: string;
  /** Underlying inflation index Bloomberg ticker shared by both legs. */
  underlying_index: string | null;
  /** Wire-honesty disclosure threaded from the YAML's
   *  ``methodology.what_it_does``.  NOT a hardcoded TS literal. */
  methodology_label: string;
};

/** Bespoke per-row shape (spread bps + z-score in one row). */
export type InflationSwapCurveSpreadTimeSeriesRow = {
  date: string;
  spread_bps: number;
  z_score: number | null;
};

export type InflationSwapCurveSpreadOutput = {
  current_metrics: InflationSwapCurveSpreadCurrentMetrics;
  /** Bespoke wire-frozen shape — spread (bps) + z-score per row. */
  time_series: InflationSwapCurveSpreadTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the spread.  Required —
   *  mirrors the Pydantic Output where the field is non-optional. */
  time_series_spread: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score.
   *  Required — mirrors the Pydantic Output where the field is non-optional. */
  time_series_zscore: TimeSeries;
};

// --- /detail/ois-rate-level (standalone bridge) ---
//
// Mirrors rates_agent/ois/tools/rate_level/schemas.py.  Per the
// methodology-exposure standalone-bridge contract
// (docs_revamped/03_standards/methodology_exposure.md §5) the OIS
// rate-level primitive ships its own typed-detail endpoint at
// /api/v1/rates/detail/ois-rate-level and its OWN frontend type — no
// reuse of the sovereign YieldLevelOutput type (the underlying
// instrument family is different: OIS par-swap rate, NOT a bond yield,
// no coupon / accrued / principal).  The wire field is named
// ``current_rate_pct`` rather than ``current_yield_pct`` so downstream
// operator panels cannot silently mix OIS par-swap rates with nominal
// sovereign yields.

export type OisRateLevelMetrics = {
  as_of_date: string;
  curve_family: string;
  tenor: string;
  /** Current par swap rate in percent.  Distinct from the nominal-
   *  sovereign ``current_yield_pct`` field — units agree (percent) but
   *  the underlying observation is the OIS par swap rate, not a bond
   *  yield-to-maturity. */
  current_rate_pct: number;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score; conventions YAML-locked on this
   *  primitive (no input-layer overrides — mirrors the OIS curve_spread
   *  / butterfly siblings). */
  z_score: number | null;
  high_252d_pct: number | null;
  low_252d_pct: number | null;
  percentile_252d: number | null;
  observation_count: number;
};

export type OisRateLevelOutput = {
  current_metrics: OisRateLevelMetrics;
  /** Historical OIS par-swap-rate levels.  Closed-enum
   *  ``TimeSeriesUnits.PERCENT`` units; series_name follows
   *  ``<curve_family_lower>_<tenor_lower>_ois_rate`` so downstream
   *  operator panels cannot silently mix with nominal sovereign yield
   *  series (suffix is the load-bearing distinction).  Each row
   *  rounded with the same ``yield_round_decimals`` convention the
   *  snapshot uses so the latest row matches
   *  ``current_metrics.current_rate_pct`` STRICTLY (pinned by
   *  rates_agent/ois/tools/rate_level/compute.py). */
  time_series?: TimeSeries;
};

// --- /detail/ois-forward-rate (standalone bridge) ---
//
// Mirrors rates_agent/ois/tools/forward_rate/schemas.py
// (OISForwardRateOutput / OISForwardRateCurrentMetrics).  Per the
// methodology-exposure standalone-bridge contract
// (docs_revamped/03_standards/methodology_exposure.md §5) the OIS
// forward-rate primitive ships its own typed-detail endpoint at
// /api/v1/rates/detail/ois-forward-rate and its OWN frontend type — no
// reuse of the OIS rate-level type.  The underlying observation is an
// IMPLIED forward rate spanning a (start, end) window on the OIS
// par-swap curve, NOT a single-pillar level read.  The wire field is
// ``forward_rate_pct`` rather than ``current_rate_pct`` so downstream
// operator panels cannot silently mix forward observations with
// single-pillar level observations.  Sign convention: forward_rate_pct
// is the absolute implied forward rate; daily_change_bps POSITIVE = the
// forward repriced HIGHER (hawkish implied-policy-path stretch).

export type OisForwardRateMetrics = {
  as_of_date: string;
  curve_family: string;
  /** Human-readable label for the forward window — e.g. "SOFR 5Y5Y",
   *  "SOFR 3M/6M", "SOFR 2026-12-01 to 2027-06-01".  Composed by the
   *  backend's ``_WindowResolver.label()`` from the input mode (tenor-
   *  pair vs date-pair) and curve_family. */
  forward_label: string;
  /** Start of the forward window in years from the curve's as-of date.
   *  Constant for tenor-mode inputs; re-anchors per trade date for
   *  date-mode inputs. */
  start_years: number;
  /** End of the forward window in years from the curve's as-of date. */
  end_years: number;
  /** Implied forward rate in PERCENT (e.g. 2.745 = 2.745%).  Null when
   *  the curve grid for the latest trade date doesn't allow
   *  interpolation across the window. */
  forward_rate_pct: number | null;
  /** 1-day change in the forward rate in BPS (e.g. -3.7 = forward fell
   *  3.7 bps day-over-day).  POSITIVE = hawkish implied-policy-path
   *  repricing (forward sits ABOVE near pillar of yesterday). */
  daily_change_bps: number | null;
  /** Rolling 252-trading-day z-score of the forward rate; conventions
   *  YAML-locked on this primitive (no input-layer overrides — mirrors
   *  the OIS rate_level / curve_spread / butterfly siblings).  Field
   *  name on the wire is ``current_z_score`` per the bespoke legacy
   *  wire-frozen schema. */
  current_z_score: number | null;
  /** Length of the rolling-z-score window in TRADING days (always
   *  252 in V1). */
  rolling_window_days: number;
  /** Trailing 252-day high of the forward rate (percent). */
  high_252d_pct: number | null;
  /** Trailing 252-day low of the forward rate (percent). */
  low_252d_pct: number | null;
  /** Percentile rank of the current forward rate within the trailing
   *  252-day range (0-100). */
  percentile_252d: number | null;
  /** Interpolated par OIS rate at ``start_years`` on the latest curve.
   *  Provides context: the forward rate is the bootstrap-implied rate
   *  that ties the (start, end) DF pair together. */
  start_spot_rate_pct: number | null;
  /** Interpolated par OIS rate at ``end_years`` on the latest curve. */
  end_spot_rate_pct: number | null;
};

/** Bespoke wire-frozen forward-rate time-series row (preserved for
 *  backward-compat with the legacy single-file tool).  The canonical
 *  ``time_series_forward`` (PERCENT) and ``time_series_zscore``
 *  (Z_SCORE) payloads carry the same data in the closed-enum
 *  ``TimeSeries`` shape; both are emitted alongside this bespoke
 *  list — they cannot drift (per compute.py per-day double-write). */
export type OisForwardRateTimeSeriesRow = {
  date: string;
  forward_rate_pct: number;
  z_score: number | null;
};

export type OisForwardRateOutput = {
  current_metrics: OisForwardRateMetrics;
  /** Bespoke wire-frozen forward-rate history.  Preserved for backward-
   *  compat with the legacy single-file tool's output shape. */
  time_series: OisForwardRateTimeSeriesRow[];
  /** Canonical historical forward-rate series.  Closed-enum
   *  ``TimeSeriesUnits.PERCENT``; series_name carries an OIS-specific
   *  ``_ois_forward`` suffix derived from the forward_label slug so
   *  downstream operator panels cannot silently mix forward series
   *  with single-pillar level series.  Values match
   *  ``time_series[i].forward_rate_pct`` 1-to-1. */
  time_series_forward?: TimeSeries;
  /** Canonical historical rolling z-score series.  Closed-enum
   *  ``TimeSeriesUnits.Z_SCORE``; series_name carries an OIS-specific
   *  ``_ois_forward_zscore`` suffix.  Values match
   *  ``time_series[i].z_score`` 1-to-1 (None for rows in the rolling-
   *  window warmup). */
  time_series_zscore?: TimeSeries;
};

// --- /detail/real_yield (Phase-1 pilot, standalone bridge) ---
//
// Mirrors rates_agent/inflation_indexed_bonds/tools/real_yield_level/schemas.py.
// Per the methodology-exposure standalone-bridge contract
// (docs_revamped/03_standards/methodology_exposure.md §5) the linker
// real-yield primitive ships its own typed-detail endpoint at
// /api/v1/rates/detail/real_yield and its OWN frontend type — no
// reuse of the sovereign YieldLevelOutput type (the underlying instrument
// family is different: inflation_linker vs sovereign_benchmark; the
// wire field name is ``real_yield_pct`` not ``current_yield_pct`` so
// operator panels don't silently mix real and nominal series).

export type RealYieldLevelMetrics = {
  as_of_date: string;
  curve_family: string;
  tenor: string;
  /** Current real yield in percent.  Distinct from the nominal-sovereign
   *  ``current_yield_pct`` field — units agree (percent) but the
   *  underlying series is the linker's real-yield-to-maturity, not a
   *  nominal yield.  Can be negative across parts of the post-2008 /
   *  post-2020 history. */
  real_yield_pct: number;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score by default; the window is
   *  overridable per call via the ``z_score_window_days`` Phase-1
   *  exposed convention. */
  z_score: number | null;
  high_252d_pct: number | null;
  low_252d_pct: number | null;
  percentile_252d: number | null;
  observation_count: number;
};

export type RealYieldLevelOutput = {
  current_metrics: RealYieldLevelMetrics;
  /** Historical linker real-yield levels.  Closed-enum
   *  ``TimeSeriesUnits.PERCENT`` units; series_name follows
   *  ``<curve_family_lower>_<tenor_lower>_real_yield`` so downstream
   *  operator panels cannot silently mix with nominal sovereign
   *  series (suffix is the load-bearing distinction).  Each row
   *  rounded with the same ``yield_round_decimals`` convention the
   *  snapshot uses so the latest row matches
   *  ``current_metrics.real_yield_pct`` STRICTLY (not just within
   *  tolerance — pinned by
   *  tests/test_real_yield_level_compute.py::TestCanonicalTimeSeries). */
  time_series?: TimeSeries;
};

// --- /detail/breakeven ---
// Standalone-bridge type for the linker bond-implied breakeven primitive
// (docs_revamped/03_standards/methodology_exposure.md §5).  Own type — NOT
// reused from any sovereign spread type — because the underlying object is
// a nominal-minus-linker differential (inflation compensation, NOT
// expected inflation; carries IRP + liquidity premium).

export type BreakevenInflationSimpleCurrentMetrics = {
  as_of_date: string;
  nominal_curve_family: string;
  linker_curve_family: string;
  tenor: string;
  /** Human-readable label, e.g. "UST-USD_TIPS 10Y breakeven". */
  breakeven_label: string;
  /** Current breakeven inflation in percent (nominal_yield_pct - real_yield_pct).
   *  Inflation compensation, not pure expected inflation — see methodology_label. */
  breakeven_pct: number;
  /** Current breakeven in basis points (breakeven_pct * 100). */
  breakeven_bps: number;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score by default; window overridable per call
   *  via the z_score_window_days Phase-1 exposed convention. */
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  /** The two underlying yields used to form the breakeven — exposed so the
   *  desk can audit the decomposition without a second tool call. */
  nominal_yield_pct: number | null;
  real_yield_pct: number | null;
  /** Wire-honesty disclosure threaded from config.yaml:methodology.what_it_does —
   *  carries the "inflation compensation, not expected inflation" caveat. */
  methodology_label: string;
};

/** Bespoke per-row shape (breakeven + z-score in one row). */
export type BreakevenInflationSimpleTimeSeriesRow = {
  date: string;
  breakeven_bps: number;
  z_score: number | null;
};

export type BreakevenInflationSimpleOutput = {
  current_metrics: BreakevenInflationSimpleCurrentMetrics;
  /** Bespoke wire-frozen shape — breakeven (bps) + z-score per row. */
  time_series: BreakevenInflationSimpleTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the breakeven over the
   *  displayed window.  Optional defensively (cached / LLM-stripped
   *  payloads omit it); the REST detail endpoint always returns it. */
  time_series_breakeven?: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score. */
  time_series_zscore?: TimeSeries;
};

// --- /detail/forward-breakeven ---
// Standalone-bridge type for the same-country forward bond-implied breakeven
// inflation primitive (e.g. UST/USD_TIPS 5Y5Y, FR_OAT/EUR_FR_LINKER 5Y10Y).
// Year-weighted linear forward of two spot breakeven pillars; inherits the
// spot primitive's same-country invariant.  Inflation-compensation FORWARD —
// NOT a clean forward expected-inflation read; the underlying differential
// at each pillar carries IRP + liquidity premia.  Units: BPS (snapshot in
// PCT too) + the two endpoint spot breakevens for decomposition audit.

export type ForwardBreakevenSimpleCurrentMetrics = {
  as_of_date: string;
  nominal_curve_family: string;
  linker_curve_family: string;
  start_tenor: string;
  end_tenor: string;
  /** Human-readable label, e.g. "UST/USD_TIPS 5Y5Y" or "FR_OAT/EUR_FR_LINKER 5Y10Y". */
  forward_window_label: string;
  /** Current forward breakeven inflation in percent. */
  forward_breakeven_pct: number | null;
  /** Current forward breakeven in basis points (year-weighted differential
   *  of the two endpoint breakevens). */
  forward_breakeven_bps: number | null;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score (YAML-locked window). */
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  /** Latest spot bond-implied breakeven (bps) at start_tenor — exposed so the
   *  desk can audit the year-weighted decomposition. */
  start_breakeven_bps: number | null;
  /** Latest spot bond-implied breakeven (bps) at end_tenor. */
  end_breakeven_bps: number | null;
  start_years: number;
  end_years: number;
  /** Wire-honesty disclosure threaded from config.yaml:methodology.what_it_does.
   *  Carries the explicit year-weighted-linear forward formula AND the
   *  "forward inflation compensation; not a clean forward expected-inflation
   *  read" caveat. */
  methodology_label: string;
};

/** Bespoke per-row shape (forward breakeven bps + z-score in one row). */
export type ForwardBreakevenSimpleTimeSeriesRow = {
  date: string;
  forward_breakeven_bps: number;
  z_score: number | null;
};

export type ForwardBreakevenSimpleOutput = {
  current_metrics: ForwardBreakevenSimpleCurrentMetrics;
  /** Bespoke wire-frozen shape — forward breakeven (bps) + z-score per row. */
  time_series: ForwardBreakevenSimpleTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the forward breakeven over the
   *  displayed window. */
  time_series_forward: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score. */
  time_series_zscore: TimeSeries;
};

// --- /detail/breakeven-butterfly ---
// Standalone-bridge type for the same-country bond-implied breakeven butterfly
// primitive (3-point curvature on a single nominal/linker pair).  Own type —
// the object is the CURVATURE of the bond-implied breakeven curve.  Inflation-
// compensation curvature carrying IRP + liquidity premia at each of three
// endpoints (not pure expected-inflation curvature).  Units: BPS.

export type BreakevenButterflyCurrentMetrics = {
  as_of_date: string;
  nominal_curve_family: string;
  linker_curve_family: string;
  short_tenor: string;
  belly_tenor: string;
  long_tenor: string;
  /** Human-readable label, e.g. "UST/USD_TIPS 5s10s30s breakeven". */
  butterfly_label: string;
  /** Current breakeven butterfly in BPS.  Sign convention: POSITIVE =
   *  belly is CHEAP vs the half-weighted wings (belly breakeven HIGH);
   *  NEGATIVE = belly is RICH. */
  current_butterfly_bps: number | null;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score of the butterfly (bps) by default. */
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  /** Component wing spreads (decomposition). */
  wing_short_bps: number | null;
  wing_long_bps: number | null;
  /** Three endpoint breakevens used to form the butterfly. */
  short_breakeven_bps: number | null;
  belly_breakeven_bps: number | null;
  long_breakeven_bps: number | null;
  short_years: number;
  belly_years: number;
  long_years: number;
  observation_count: number;
  /** Wire-honesty disclosure threaded from config.yaml:methodology.what_it_does. */
  methodology_label: string;
};

/** Bespoke per-row shape (butterfly bps + z-score in one row). */
export type BreakevenButterflyTimeSeriesRow = {
  date: string;
  butterfly_bps: number;
  z_score: number | null;
};

export type BreakevenButterflyOutput = {
  current_metrics: BreakevenButterflyCurrentMetrics;
  /** Bespoke wire-frozen shape — butterfly (bps) + z-score per row. */
  time_series: BreakevenButterflyTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the butterfly.  Required —
   *  mirrors the Pydantic Output where the field is non-optional. */
  time_series_butterfly: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score.
   *  Required — mirrors the Pydantic Output where the field is non-optional. */
  time_series_zscore: TimeSeries;
};

// --- /detail/breakeven-curve-spread ---
// Standalone-bridge type for the same-country bond-implied breakeven curve
// spread primitive (2-point tenor spread on a single nominal/linker pair —
// e.g. UST/USD_TIPS 2s10s breakeven).  Own type — the object is the TERM
// STRUCTURE of inflation compensation (long_breakeven_bps -
// short_breakeven_bps), distinct from a breakeven butterfly (3-point
// curvature) and from a real-yield curve spread.  Inflation-compensation
// term structure carrying IRP + liquidity premia at each of two endpoints,
// NOT pure expected-inflation term structure.  Units: BPS.

export type BreakevenCurveSpreadCurrentMetrics = {
  as_of_date: string;
  nominal_curve_family: string;
  linker_curve_family: string;
  short_tenor: string;
  long_tenor: string;
  /** Human-readable label, e.g. "UST/USD_TIPS 2s10s breakeven". */
  spread_label: string;
  /** Current breakeven curve spread in BPS (long − short).  POSITIVE =
   *  upward-sloping breakeven curve (long-end inflation compensation
   *  higher than short-end); NEGATIVE = inverted (front-end higher). */
  current_spread_bps: number | null;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score of the spread (bps) by default. */
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  /** Endpoint breakevens used to form the spread (bps). */
  short_breakeven_bps: number | null;
  long_breakeven_bps: number | null;
  short_years: number;
  long_years: number;
  /** Wire-honesty disclosure threaded from config.yaml:methodology.what_it_does. */
  methodology_label: string;
};

/** Bespoke per-row shape (spread bps + z-score in one row). */
export type BreakevenCurveSpreadTimeSeriesRow = {
  date: string;
  spread_bps: number;
  z_score: number | null;
};

export type BreakevenCurveSpreadOutput = {
  current_metrics: BreakevenCurveSpreadCurrentMetrics;
  /** Bespoke wire-frozen shape — spread (bps) + z-score per row. */
  time_series: BreakevenCurveSpreadTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the spread.  Required —
   *  mirrors the Pydantic Output where the field is non-optional. */
  time_series_spread: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score.
   *  Required — mirrors the Pydantic Output where the field is non-optional. */
  time_series_zscore: TimeSeries;
};

// --- /detail/real-yield-butterfly ---
// Standalone-bridge type for the same-country linker real-yield butterfly
// (3-point curvature on a SINGLE linker curve, e.g. USD_TIPS 5s10s30s
// real-yield butterfly).  Own type — the object is the CURVATURE of the
// REAL-YIELD curve.  Distinct from a real-yield curve spread (2-point
// difference) and from a breakeven butterfly (3-point curvature of bond-
// implied breakevens carrying inflation compensation).  Snapshot units:
// PERCENT (same units as the underlying real yields); period changes
// and the 252d high/low are in BPS per desk convention; daily changes
// of a percent-units series are reported in bps.
//
// Single-curve primitive: a single ``curve_family`` (linker) + three
// strictly-ordered tenors.  No nominal counterparty in the input shape.

export type RealYieldButterflyCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  short_tenor: string;
  belly_tenor: string;
  long_tenor: string;
  /** Human-readable label, e.g. "USD_TIPS 5s10s30s real-yield". */
  butterfly_label: string;
  /** Current real-yield butterfly in PERCENT (belly − 0.5×(short + long)).
   *  Curvature of the REAL-YIELD curve.  Sign convention: POSITIVE = belly
   *  CHEAP versus the half-weighted wings; NEGATIVE = belly RICH.  Display
   *  layer multiplies by 100 for the bps presentation per desk convention. */
  current_butterfly_pct: number | null;
  /** Daily / weekly / monthly change of the percent-units butterfly,
   *  reported in BPS per desk convention. */
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score of the butterfly (percent) by
   *  default; the z-score conventions are YAML-locked on this primitive
   *  (no input-layer overrides). */
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_pct: number | null;
  low_252d_pct: number | null;
  percentile_252d: number | null;
  /** Component wing spreads (decomposition, PERCENT). */
  wing_short_pct: number | null;
  wing_long_pct: number | null;
  /** Three endpoint real yields used to form the butterfly (PERCENT). */
  short_real_yield_pct: number | null;
  belly_real_yield_pct: number | null;
  long_real_yield_pct: number | null;
  short_years: number;
  belly_years: number;
  long_years: number;
  observation_count: number;
  /** Resolved from instrument_master — surfaced so the desk can confirm
   *  the linker identity without a second tool call. */
  country: string;
  currency: string;
  /** Wire-honesty disclosure threaded from config.yaml:methodology.what_it_does —
   *  carries the explicit butterfly formula AND the curvature-of-real-yields
   *  framing so downstream operators cannot misread the sign convention. */
  methodology_label: string;
};

/** Bespoke per-row shape (butterfly % + z-score in one row). */
export type RealYieldButterflyTimeSeriesRow = {
  date: string;
  butterfly_pct: number;
  z_score: number | null;
};

export type RealYieldButterflyOutput = {
  current_metrics: RealYieldButterflyCurrentMetrics;
  /** Bespoke wire-frozen shape — butterfly (percent) + z-score per row. */
  time_series: RealYieldButterflyTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.PERCENT series of the butterfly.  Required —
   *  mirrors the Pydantic Output where the field is non-optional. */
  time_series_butterfly: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score.
   *  Required — mirrors the Pydantic Output where the field is non-optional. */
  time_series_zscore: TimeSeries;
};

// --- /detail/cross-market-zcis ---
// Standalone-bridge type for the same-tenor cross-market zero-coupon inflation
// swap (ZCIS) spread primitive (e.g. USD_ZCIS 5Y minus EUR_ZCIS 5Y).  Two
// distinct ZCIS curve families at a shared pillar.  The two legs reference
// DIFFERENT inflation indices (US CPI-U / Eurozone HICP-xT / UK RPI), so the
// spread captures BOTH inflation-expectation differentials AND structural
// index-family differences — NOT a clean expected-inflation divergence.
// Per-leg metadata (inflation_index_family / index_lag / interpolation /
// underlying_index) is surfaced on current_metrics so the desk reader can
// decompose the spread without a second tool call.  Spread reported in BOTH
// percent (the natural unit) and bps (desk display).  Sign convention:
// spread = leg_a - leg_b (left minus right).

export type CrossMarketInflationSwapSpreadCurrentMetrics = {
  as_of_date: string;
  leg_a_curve_family: string;
  leg_b_curve_family: string;
  tenor: string;
  tenor_years: number;
  /** Human-readable label, e.g. "USD_ZCIS-EUR_ZCIS 5Y". */
  spread_label: string;
  /** Current cross-market ZCIS spread in PERCENT (leg_a_pct - leg_b_pct). */
  spread_pct: number;
  /** Current cross-market ZCIS spread in basis points (spread_pct * 100). */
  spread_bps: number;
  change_1d_bps: number | null;
  change_1w_bps: number | null;
  change_1m_bps: number | null;
  /** Rolling 252-trading-day z-score of the spread (in bps). */
  z_score_252d: number | null;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  /** Latest per-leg ZCIS rates in percent — auditable decomposition. */
  leg_a_pct: number | null;
  leg_b_pct: number | null;
  observation_count: number;
  /** Per-leg index-family metadata — the load-bearing index-family caveat. */
  leg_a_inflation_index_family: string;
  leg_b_inflation_index_family: string;
  /** Derived top-level summary: true iff both legs share the same family. */
  index_families_match: boolean;
  /** Wire-honesty caveat naming both index families verbatim; null when
   *  index_families_match is true (rare in V1 — every cross-market pair
   *  drawn from USD_ZCIS / EUR_ZCIS / GBP_ZCIS has distinct families). */
  index_family_caveat: string | null;
  leg_a_index_lag: string;
  leg_b_index_lag: string;
  leg_a_interpolation: string;
  leg_b_interpolation: string;
  leg_a_underlying_index: string | null;
  leg_b_underlying_index: string | null;
  /** Wire-honesty disclosure threaded from config.yaml:methodology.what_it_does —
   *  carries the spread formula, alignment discipline, the load-bearing
   *  index-family caveat, AND the sign convention so downstream operators
   *  and the LLM cannot misread the output. */
  methodology_label: string;
};

/** Bespoke per-row shape (spread + each leg in one row). */
export type CrossMarketInflationSwapSpreadTimeSeriesRow = {
  date: string;
  spread_pct: number;
  spread_bps: number;
  leg_a_pct: number;
  leg_b_pct: number;
};

export type CrossMarketInflationSwapSpreadOutput = {
  current_metrics: CrossMarketInflationSwapSpreadCurrentMetrics;
  /** Bespoke wire-frozen shape — spread (pct + bps) + each leg per row. */
  time_series: CrossMarketInflationSwapSpreadTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the cross-market ZCIS spread.
   *  Required — mirrors the Pydantic Output where the field is non-optional. */
  time_series_spread: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score.
   *  Required — mirrors the Pydantic Output where the field is non-optional. */
  time_series_zscore: TimeSeries;
};

// --- /detail/zcis-butterfly ---
// Standalone-bridge type for the same-curve zero-coupon inflation swap (ZCIS)
// butterfly primitive (3-point curvature on ONE ZCIS curve family, e.g.
// USD_ZCIS 2s5s10s, EUR_ZCIS 5s10s30s, GBP_ZCIS 2s10s30s).  Three strictly-
// ordered tenors on a single curve_family; no cross-market counterparty.
// Inherits the load-bearing index-family metadata (CPI-U / HICPxT / RPI) from
// the underlying ZCIS curve — the same-curve invariant guarantees all three
// legs share the same inflation_index_family / index_lag / interpolation /
// underlying_index.  Butterfly + 252d high / low / wing spreads ship in BPS
// directly from the backend (the inflation_swaps domain's BPS convention);
// per-leg ZCIS rates ship in PERCENT (the natural rate unit).

export type InflationSwapButterflyCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  short_tenor: string;
  belly_tenor: string;
  long_tenor: string;
  /** Human-readable label, e.g. "USD_ZCIS 2s5s10s" or "EUR_ZCIS 5s10s30s". */
  butterfly_label: string;
  /** Current ZCIS butterfly in BASIS POINTS
   *  ((belly_zcis_pct - 0.5*(short_zcis_pct + long_zcis_pct)) * 100).
   *  Sign convention: POSITIVE = belly CHEAP (belly ZCIS rate high vs the
   *  half-weighted wings); NEGATIVE = belly RICH.  Already bps on the wire
   *  per the inflation_swaps domain BPS convention. */
  current_butterfly_bps: number | null;
  /** Daily / weekly / monthly change of the butterfly, BPS (already-bps
   *  subtraction). */
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score of the butterfly (bps) by default;
   *  the z-score conventions are YAML-locked on this primitive (no input-
   *  layer overrides — mirrors the breakeven-butterfly / real-yield-
   *  butterfly siblings). */
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  /** Component wing spreads (decomposition, BPS):
   *    wing_short_bps = (belly_zcis_pct - short_zcis_pct) * 100
   *    wing_long_bps  = (long_zcis_pct  - belly_zcis_pct) * 100 */
  wing_short_bps: number | null;
  wing_long_bps: number | null;
  /** Three endpoint ZCIS rates used to form the butterfly (PERCENT — the
   *  natural unit for an inflation-swap rate level). */
  short_zcis_rate_pct: number | null;
  belly_zcis_rate_pct: number | null;
  long_zcis_rate_pct: number | null;
  short_years: number;
  belly_years: number;
  long_years: number;
  observation_count: number;
  /** Load-bearing reference metadata (shared by all three legs by the same-
   *  curve invariant).  Surfaced on the wire so a desk reader can interpret
   *  the butterfly honestly (e.g. "this is a CPI-U curvature object, not
   *  HICPxT and not RPI"). */
  inflation_index_family: string;
  index_lag: string;
  interpolation: string;
  underlying_index: string | null;
  /** Wire-honesty disclosure threaded from config.yaml:methodology.what_it_does —
   *  spells out the fixed (-0.5, +1.0, -0.5) weighting, the BPS conversion,
   *  the sign convention (POSITIVE = belly cheap), the raw-inflation-swap-
   *  rate-space promise (no basis subtraction, no IRP adjustment, no fitted
   *  curve), and the same-curve invariant so downstream operators cannot
   *  misread the output. */
  methodology_label: string;
};

/** Bespoke per-row shape (butterfly bps + z-score in one row). */
export type InflationSwapButterflyTimeSeriesRow = {
  date: string;
  butterfly_bps: number;
  z_score: number | null;
};

export type InflationSwapButterflyOutput = {
  current_metrics: InflationSwapButterflyCurrentMetrics;
  /** Bespoke wire-frozen shape — butterfly (bps) + z-score per row. */
  time_series: InflationSwapButterflyTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the ZCIS butterfly.  Required —
   *  mirrors the Pydantic Output where the field is non-optional. */
  time_series_butterfly: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score.
   *  Required — mirrors the Pydantic Output where the field is non-optional. */
  time_series_zscore: TimeSeries;
};

// --- /detail/ois-butterfly ---
// Standalone-bridge type for the same-curve OIS butterfly primitive.  Mirrors
// ``OISButterflyOutput`` from rates_agent/ois/tools/calculate_ois_butterfly/
// schemas.py exactly (snake_case wire fields preserved).  Single-curve
// 3-point curvature in raw OIS par-rate space — the curve_family closed enum
// is sourced from rates_agent/playbooks/ois.yml.  Sign convention: POSITIVE =
// belly CHEAP (belly OIS rate HIGH relative to the linear interpolation of
// the wings); NEGATIVE = belly RICH.  Butterfly + 252d high / low / wing
// spreads ship in BPS directly from the backend; per-leg OIS endpoint rates
// ship in PERCENT (the natural unit for an OIS par-swap rate).
//
// NB the backend Output does NOT carry the optional fields the linker /
// ZCIS butterfly siblings carry (no ``methodology_label``, no
// ``weekly_change_bps`` / ``monthly_change_bps``, no
// ``observation_count``, no ``short_years`` / ``belly_years`` /
// ``long_years``, no ``short_tenor`` / ``belly_tenor`` / ``long_tenor``
// strings — only the ``butterfly_label`` and per-leg rate fields).  The
// frontend re-derives the per-tenor identity from the request params + the
// per-tool registry (see ``oisButterflyShared.ts``).

export type OisButterflyCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  /** Human-readable label, e.g. "2s5s10s" or "3M/2Y/5Y" for sub-year
   *  triplets. */
  butterfly_label: string;
  /** Current OIS butterfly in BASIS POINTS
   *  ((2*belly_rate_pct - short_rate_pct - long_rate_pct) * 100).
   *  Sign convention: POSITIVE = belly CHEAP; NEGATIVE = belly RICH.  Already
   *  bps on the wire — no unit conversion needed at the display layer. */
  current_butterfly_bps: number;
  /** 1-day change of the butterfly (BPS, already-bps subtraction). */
  daily_change_bps: number | null;
  /** Rolling 252-trading-day z-score of the butterfly (bps).  The z-score
   *  conventions are YAML-locked on this primitive — no input-layer
   *  overrides (mirrors the sibling sovereign / linker / ZCIS butterflies). */
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  /** Component wing spreads (decomposition, BPS):
   *    wing_short_bps = (belly_rate - short_rate) * 100
   *    wing_long_bps  = (long_rate  - belly_rate) * 100 */
  wing_short_bps: number | null;
  wing_long_bps: number | null;
  /** Three endpoint OIS par-swap rates used to form the butterfly (PERCENT —
   *  the natural unit for an OIS par-swap rate level). */
  short_tenor_rate: number | null;
  belly_tenor_rate: number | null;
  long_tenor_rate: number | null;
};

export type OisButterflyTimeSeriesRow = {
  date: string;
  butterfly_bps: number;
  z_score: number | null;
};

export type OisButterflyOutput = {
  current_metrics: OisButterflyCurrentMetrics;
  time_series: OisButterflyTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the OIS butterfly. */
  time_series_butterfly: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score. */
  time_series_zscore: TimeSeries;
};

// --- /detail/ois-curve-spread ---
// Standalone-bridge type for the same-curve OIS tenor spread primitive.
// Mirrors ``OISCurveSpreadOutput`` from rates_agent/ois/tools/curve_spread/
// schemas.py exactly (snake_case wire fields preserved).  Two-point spread on
// a SINGLE OIS curve family (long_tenor − short_tenor, in BPS).  The wire is
// LEAN compared to the linker / sovereign curve_spread siblings: NO
// ``methodology_label``, NO ``weekly_change_bps`` / ``monthly_change_bps``,
// NO ``percentile_252d`` / ``high_252d_bps`` / ``low_252d_bps``, NO
// ``observation_count``, NO ``short_tenor`` / ``long_tenor`` strings (only
// the combined ``spread_label`` like '2s10s').  The frontend re-derives
// percentile / 252d high-low / observation_count CLIENT-SIDE from
// ``time_series_spread.rows`` so the mockup's extended KPI strip is honoured
// without inventing wire data.

export type OisCurveSpreadCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  /** Human-readable label, e.g. "2s10s" (pure-year pairs) or "3M/2Y" (sub-year
   *  short legs).  The per-tenor identity is reconstructed from the request
   *  params on the frontend — the wire does not carry separate short_tenor /
   *  long_tenor strings. */
  spread_label: string;
  /** Current OIS curve spread in BASIS POINTS ((long_rate_pct -
   *  short_rate_pct) * 100).  Already bps on the wire — no unit conversion
   *  needed at the display layer.  Can be negative (curve inversion). */
  current_spread_bps: number;
  /** 1-day change in the spread (BPS). */
  daily_change_bps: number | null;
  /** Rolling 252-trading-day z-score of the bps spread.  Z-score conventions
   *  are YAML-locked on this primitive — no input-layer overrides
   *  (z_score_window_days / z_score_min_periods / z_score_ddof live in
   *  config.yaml). */
  current_z_score: number | null;
  rolling_window_days: number;
  /** Latest OIS par-swap rate on the short leg (PERCENT — natural rate unit
   *  for an OIS par-swap rate).  Used for the decomposition row. */
  short_tenor_rate: number | null;
  /** Latest OIS par-swap rate on the long leg (PERCENT). */
  long_tenor_rate: number | null;
};

/** Bespoke per-row shape (spread bps + z-score in one row). */
export type OisCurveSpreadTimeSeriesRow = {
  date: string;
  spread_bps: number;
  z_score: number | null;
};

export type OisCurveSpreadOutput = {
  current_metrics: OisCurveSpreadCurrentMetrics;
  /** Bespoke wire-frozen shape — spread (bps) + z-score per row. */
  time_series: OisCurveSpreadTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the OIS spread.  Required —
   *  mirrors the Pydantic Output where the field is non-optional. */
  time_series_spread: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score. */
  time_series_zscore: TimeSeries;
};

// --- /detail/ois-cross-market-spread ---
// Standalone-bridge type for the same-tenor cross-market OIS spread primitive
// (e.g. SOFR 2Y minus ESTR 2Y).  Two distinct OIS curve families at a shared
// pillar.  The two legs reference DIFFERENT overnight rate indices (SOFR /
// ESTR / SONIA / TONA / AONIA / CORRA — risk-neutral expected-policy-path
// objects priced under each currency's own central-bank reaction function), so
// the spread captures cross-currency POLICY-PATH divergence — the canonical
// G4 read on relative central-bank stance.  Wire field names use "rate"
// terminology (curve_family_1_rate / curve_family_2_rate) because OIS quotes
// are par swap rates, NOT bond yields.  Wire is LEAN compared to the linker
// cross-market ZCIS sibling: NO ``methodology_label``, NO ``observation_count``,
// NO ``leg_a_*`` / ``leg_b_*`` index-family metadata (OIS has a single overnight
// index per curve, surfaced via the per-tool curve-family registry).  Spread
// reported in BPS directly on the wire (the OIS sub-domain BPS convention).
// Sign convention: spread = curve_family_1 - curve_family_2 (left minus right).

export type OisCrossMarketSpreadCurrentMetrics = {
  as_of_date: string;
  curve_family_1: string;
  curve_family_2: string;
  tenor: string;
  /** Human-readable label, e.g. "USD_SOFR_OIS-EUR_ESTR_OIS 2Y". */
  spread_label: string;
  /** Current OIS cross-market spread in BASIS POINTS
   *  ((curve_family_1_rate_pct - curve_family_2_rate_pct) * 100).  Already
   *  bps on the wire — no unit conversion needed at the display layer.  Can
   *  be negative (curve_family_2's central bank pricing more hawkish than
   *  curve_family_1's). */
  current_spread_bps: number;
  /** 1-day change in the spread (BPS). */
  daily_change_bps: number | null;
  /** 5-trading-day change in the spread (BPS, ~1 calendar week). */
  weekly_change_bps: number | null;
  /** 22-trading-day change in the spread (BPS, ~1 calendar month). */
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score of the bps spread.  Z-score conventions
   *  are YAML-locked on this primitive — no input-layer overrides
   *  (z_score_window_days / z_score_min_periods / z_score_ddof live in
   *  config.yaml). */
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  /** Latest par swap rate on curve_family_1 (PERCENT — natural rate unit for
   *  an OIS par-swap rate).  Used for the per-leg decomposition row. */
  curve_family_1_rate: number | null;
  /** Latest par swap rate on curve_family_2 (PERCENT). */
  curve_family_2_rate: number | null;
};

/** Bespoke per-row shape (spread bps + z-score in one row).  Wire-frozen for
 *  backward-compat with the legacy single-file OIS cross_market_spread tool. */
export type OisCrossMarketSpreadTimeSeriesRow = {
  date: string;
  spread_bps: number;
  z_score: number | null;
};

export type OisCrossMarketSpreadOutput = {
  current_metrics: OisCrossMarketSpreadCurrentMetrics;
  /** Bespoke wire-frozen shape — spread (bps) + z-score per row. */
  time_series: OisCrossMarketSpreadTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.BPS series of the OIS cross-market spread.
   *  Required — mirrors the Pydantic Output where the field is non-optional. */
  time_series_spread: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score.
   *  Required — mirrors the Pydantic Output where the field is non-optional. */
  time_series_zscore: TimeSeries;
};

// --- /detail/zcis-scanner ---
// Standalone-bridge type for the universe-wide ZCIS rate-extremes scanner.
// SCANNER shape — the wire returns a ranked LIST of (curve_family, tenor)
// extremes ordered by |z| of the 252d-rolling ZCIS rate LEVEL z-score, NOT
// a single time series.  Mirrors ``ScanInflationSwapsExtremesOutput`` from
// rates_agent/inflation_swaps/tools/scan_inflation_swaps_extremes/schemas.py
// exactly (snake_case wire fields preserved).

/** One ranked extreme on the ZCIS universe scan.  Mirrors
 *  ``ScanInflationSwapsExtremesResultRow``. */
export type ScanInflationSwapsExtremesResultRow = {
  rank: number;
  curve_family: string;
  tenor: string;
  as_of_date: string;
  zcis_rate_pct: number | null;
  daily_change_zcis_rate_bps: number | null;
  monthly_change_zcis_rate_bps: number | null;
  z_score_zcis_rate: number | null;
  /** Closed enum derived from z-score sign on rows that pass the
   *  ``min_abs_z_score`` filter. */
  signal: 'EXTREME_HIGH' | 'EXTREME_LOW';
  maturity_date: string | null;
  underlying_index: string | null;
  vendor_ticker: string | null;
  /** P5 / catalog-guardrail disclosure — REQUIRED on every row (not just
   *  on the response).  Includes the universe-wide ZCIS rate-level label,
   *  the explicit z-score lookback window, the INDEX-FAMILY +
   *  MARKET-STRUCTURE caveats, and the morning-screen scope statement. */
  methodology_disclosure: string;
};

export type ScanInflationSwapsExtremesOutput = {
  /** Human-readable one-line summary (e.g. "Scanned 21 ZCIS stems (21
   *  scoreable). Stems with |z| >= 1.5: 7. Showing top 5..."). */
  scan_summary: string;
  results: ScanInflationSwapsExtremesResultRow[];
  /** Response-level methodology disclosure — full multi-line caveat
   *  flowing through from compute() (NOT a hardcoded TS literal).
   *  Surfaced on the extended view's methodology card. */
  methodology_disclosure: string;
};

// --- /detail/linkers-scanner ---
// Standalone-bridge type for the universe-wide linker REAL-YIELD extremes
// scanner.  SCANNER shape — the wire returns a ranked LIST of
// (curve_family, tenor) extremes ordered by |z| of the 252d-rolling
// REAL-YIELD LEVEL z-score, NOT a single time series.  Mirrors
// ``ScanInflationLinkersExtremesOutput`` from
// rates_agent/inflation_indexed_bonds/tools/scan_inflation_linkers_extremes/
// schemas.py exactly (snake_case wire fields preserved).

/** One ranked extreme on the linker REAL-YIELD universe scan.  Mirrors
 *  ``ScanInflationLinkersExtremesResultRow``. */
export type ScanInflationLinkersExtremesResultRow = {
  rank: number;
  curve_family: string;
  tenor: string;
  as_of_date: string;
  real_yield_pct: number | null;
  daily_change_bps: number | null;
  monthly_change_bps: number | null;
  z_score_real_yield: number | null;
  /** Closed enum derived from z-score sign on rows that pass the
   *  ``min_abs_z_score`` filter. */
  signal: 'EXTREME_HIGH' | 'EXTREME_LOW';
  maturity_date: string | null;
  country: string | null;
  vendor_ticker: string | null;
  /** P5 / catalog-guardrail disclosure — REQUIRED on every row (not just
   *  on the response).  Includes the universe-wide linker real-yield
   *  level label, the explicit z-score lookback window, the INDEX-FAMILY
   *  + MARKET-STRUCTURE caveats, and the morning-screen scope statement. */
  methodology_disclosure: string;
};

export type ScanInflationLinkersExtremesOutput = {
  /** Human-readable one-line summary (e.g. "Scanned 24 linker stems (22
   *  scoreable). Stems with |z| >= 1.5: 7. Showing top 5 by absolute
   *  real-yield z-score. as_of_dates span 2026-04-07 to 2026-04-08."). */
  scan_summary: string;
  results: ScanInflationLinkersExtremesResultRow[];
  /** Response-level methodology disclosure — full multi-line caveat
   *  flowing through from compute() (NOT a hardcoded TS literal).
   *  Surfaced on the extended view's methodology card. */
  methodology_disclosure: string;
};

// --- /detail/bond-futures-scanner ---
// Standalone-bridge type for the universe-wide bond-futures extremes
// scanner.  SCANNER shape — the wire returns a MULTI-METRIC ranked LIST of
// (curve_family, contract_code) extremes across four metrics (price LEVEL,
// 1-day price CHANGE, volume LEVEL, open-interest LEVEL) ordered by |z| of
// the 252d-rolling z-score on each metric independently, NOT a single time
// series.  Mirrors ``ScanBondFuturesExtremesOutput`` from
// rates_agent/bond_futures/tools/scan_bond_futures_extremes/schemas.py
// exactly (snake_case wire fields preserved).

/** Closed enum of the four metrics the bond-futures universe scanner
 *  ranks across — mirrors the backend ``ScanMetric`` ``Literal[...]``. */
export type ScanBondFuturesMetric =
  | 'price'
  | 'price_change'
  | 'volume'
  | 'open_interest';

/** One ranked extreme on the bond-futures universe scan.  Mirrors
 *  ``ScanBondFuturesExtremesResultRow``.  Each row's ``rank`` is WITHIN
 *  its metric's top-N (1 = most extreme by absolute z-score for THIS
 *  metric); ``z_score`` is the z-score of THIS row's metric. */
export type ScanBondFuturesExtremesResultRow = {
  rank: number;
  /** Which of the four metrics this row is ranked on (closed enum). */
  metric: ScanBondFuturesMetric;
  curve_family: string;
  /** Rolling-generic stem (TY1 / UXY1 / RX1 / JB1 / ...) — the canonical
   *  disambiguator per TD#11.  (curve_family, tenor) alone is ambiguous
   *  for TY1/UXY1 (both UST_FUT 10Y) and US1/WN1 (both UST_FUT 30Y). */
  contract_code: string;
  tenor: string;
  as_of_date: string;
  /** Latest cleaned price in the contract's native quote_units (NOT a
   *  yield).  See methodology_disclosure for the rolling-generic-price
   *  caveat. */
  current_price: number | null;
  /** 1-trading-day raw price change in native quote_units (NOT *100, NOT
   *  bps). */
  daily_price_change: number | null;
  /** Latest daily traded volume in CONTRACTS (NOT notional). */
  current_volume: number | null;
  /** Latest end-of-day open interest in CONTRACTS (NOT notional). */
  current_open_interest: number | null;
  /** 1-trading-day raw OI change (NOT *100, NOT bps). */
  delta_open_interest_1d: number | null;
  /** Rolling 252-trading-day z-score of THIS row's ``metric``. */
  z_score: number | null;
  /** Closed enum derived from z-score sign on rows that pass the
   *  ``min_abs_z_score`` filter. */
  signal: 'EXTREME_HIGH' | 'EXTREME_LOW';
  /** P5 / ADR 0013 / catalog-guardrail disclosure — REQUIRED on every
   *  row (not just on the response).  Includes the universe-wide front-
   *  month sweep label, the explicit z-score lookback window, and the
   *  rolling-generic-price / non-DV01-spread caveats. */
  methodology_disclosure: string;
};

export type ScanBondFuturesExtremesOutput = {
  /** Human-readable one-line summary (e.g. "Scanned 19 bond-futures
   *  stems (17 scoreable). Stems with |z| >= 1.5 per metric: price=4,
   *  price_change=3, volume=2, open_interest=5. Showing top 5 per metric
   *  (14 rows). as_of dates span 2026-05-20 to 2026-05-22."). */
  scan_summary: string;
  results: ScanBondFuturesExtremesResultRow[];
  /** Response-level methodology disclosure — full multi-line caveat
   *  flowing through from compute() (NOT a hardcoded TS literal).
   *  Surfaced on the extended view's methodology card. */
  methodology_disclosure: string;
};

// --- /detail/policy-futures-scanner ---
// Standalone-bridge type for the universe-wide policy-futures (STIR) extremes
// scanner.  SCANNER shape — the wire returns a MULTI-METRIC ranked LIST of
// (curve_family, strip_position, contract_code) extremes across four metrics
// (implied-rate LEVEL, 1-day implied-rate CHANGE in bps, volume LEVEL, end-of-
// day open-interest LEVEL) ordered by |z| of the 252d-rolling z-score on each
// metric independently, NOT a single time series.  Mirrors
// ``ScanPolicyFuturesExtremesOutput`` from
// rates_agent/policy_futures/tools/scan_policy_futures_extremes/schemas.py
// exactly (snake_case wire fields preserved).

/** Closed enum of the four metrics the policy-futures (STIR) universe
 *  scanner ranks across — mirrors the backend ``ScanMetric`` ``Literal[...]``. */
export type ScanPolicyFuturesMetric =
  | 'implied_rate_level'
  | 'implied_rate_change'
  | 'volume_level'
  | 'open_interest_level';

/** Closed enum of the policy-futures curve families admitted by the V1
 *  scanner (ADR 0013) — mirrors the backend
 *  ``PolicyFuturesScanCurveFamily`` ``Literal[...]``. */
export type PolicyFuturesScanCurveFamily =
  | 'SOFR_FUT'
  | 'EUR_SHORT_RATE_FUT'
  | 'SONIA_FUT';

/** One ranked extreme on the policy-futures (STIR) universe scan.  Mirrors
 *  ``ScanPolicyFuturesExtremesResultRow``.  Each row's ``rank`` is WITHIN
 *  its metric's top-N (1 = most extreme by absolute z-score for THIS
 *  metric); ``z_score`` is the z-score of THIS row's metric. */
export type ScanPolicyFuturesExtremesResultRow = {
  rank: number;
  /** Which of the four metrics this row is ranked on (closed enum). */
  metric: ScanPolicyFuturesMetric;
  /** Policy-futures curve family (SOFR_FUT / EUR_SHORT_RATE_FUT /
   *  SONIA_FUT). */
  curve_family: PolicyFuturesScanCurveFamily;
  /** 1-based strip position (1 = front contract; 2..8 = quarterly forwards).
   *  The canonical disambiguator for the policy-futures universe — together
   *  with curve_family it uniquely identifies one stem. */
  strip_position: number;
  /** Master rolling-generic stem from instrument_master (e.g. 'SFR1' /
   *  'ER1' / 'SFI1' / 'SFR2' / ...). */
  contract_code: string;
  /** Current-front underlying contract code (e.g. 'SFRH6 COMB').  May be
   *  null when the SCD2 history has no row for this stem on the anchor. */
  underlying_contract_code: string | null;
  security_name: string | null;
  expiry_date: string | null;
  contract_size: number | null;
  /** Per-stem inverse-pricing flag from ``instrument_master.attributes``. */
  inverse_priced: boolean;
  /** Per-row short-rate regime disclosure (ADR 0013): 'RFR' for SOFR /
   *  SONIA futures; 'IBOR' for EUR_SHORT_RATE_FUT (Euribor). */
  short_rate_regime: 'RFR' | 'IBOR';
  /** Quoted-units disclosure for the raw_price axis on this row
   *  ('100 - rate' for inverse-priced strips; 'rate (%)' for direct). */
  quote_units: string;
  /** Most recent trading date with aligned price + volume + OI for this
   *  stem (YYYY-MM-DD). */
  as_of_date: string;
  /** Latest cleaned, ffilled raw price in the contract's native quote
   *  space (e.g. 100 - rate for SOFR_FUT). */
  current_raw_price: number | null;
  /** Latest implied rate in PERCENT, derived from current_raw_price per
   *  the per-stem inverse_priced flag. */
  implied_rate_pct: number | null;
  /** 1-trading-day change on the implied-rate axis in BPS (Δ × 100). */
  daily_change_implied_rate_bps: number | null;
  /** Latest daily traded volume in CONTRACTS (NOT notional). */
  current_volume: number | null;
  /** Latest end-of-day open interest in CONTRACTS (NOT notional). */
  current_open_interest: number | null;
  /** 1-trading-day raw OI change (whole contracts; NOT *100). */
  delta_open_interest_1d: number | null;
  /** Rolling 252-trading-day z-score of THIS row's ``metric``. */
  z_score: number | null;
  /** Closed enum derived from z-score sign on rows that pass the
   *  ``min_abs_z_score`` filter. */
  signal: 'EXTREME_HIGH' | 'EXTREME_LOW';
  /** P5 / ADR 0013 / catalog-guardrail disclosure — REQUIRED on every
   *  row (not just on the response).  Includes the universe-wide strip-
   *  scan label, the explicit z-score lookback window, the per-row RFR-
   *  vs-IBOR regime caveat, the inverse-pricing rule, and the rolling-
   *  generic strip caveat. */
  methodology_disclosure: string;
};

export type ScanPolicyFuturesExtremesOutput = {
  /** Human-readable one-line summary (e.g. "Scanned 24 policy-futures
   *  stems (24 scoreable). Stems with |z| >= 1.5 per metric:
   *  implied_rate_level=4, implied_rate_change=3, volume_level=2,
   *  open_interest_level=5. Showing top 5 per metric (14 rows). as_of
   *  2026-04-08."). */
  scan_summary: string;
  results: ScanPolicyFuturesExtremesResultRow[];
  /** Response-level methodology disclosure — full multi-line caveat
   *  flowing through from compute() (NOT a hardcoded TS literal).
   *  Surfaced on the extended view's methodology card. */
  methodology_disclosure: string;
};

// --- /detail/real_yield_curve_spread ---
// Standalone-bridge type for the same-country linker real-yield curve-spread
// primitive.  Own type — the object is the term structure of REAL YIELDS
// (real-yield curve shape), distinct from a breakeven curve spread and from
// a nominal sovereign curve spread.  The spread is in PERCENT (same units as
// the underlying real yields); changes are in BPS.

export type RealYieldCurveSpreadCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  short_tenor: string;
  long_tenor: string;
  /** Human-readable label, e.g. "USD_TIPS 5s10s real-yield". */
  spread_label: string;
  /** Current real-yield curve spread in PERCENT (long_real_yield_pct -
   *  short_real_yield_pct).  Can be negative (curve inversion). */
  current_spread_pct: number | null;
  /** Daily / weekly / monthly change of the percent-units spread, in BPS. */
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  /** Rolling 252-trading-day z-score of the spread by default; window
   *  overridable per call. */
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_pct: number | null;
  low_252d_pct: number | null;
  percentile_252d: number | null;
  /** The two endpoint real yields used to form the spread (PERCENT). */
  short_real_yield_pct: number | null;
  long_real_yield_pct: number | null;
  short_years: number;
  long_years: number;
  observation_count: number;
  /** Resolved from instrument_master — surfaced so the desk can confirm the
   *  linker identity without a second call. */
  country: string;
  currency: string;
  /** Wire-honesty disclosure threaded from config.yaml:methodology.what_it_does. */
  methodology_label: string;
};

/** Bespoke per-row shape (spread % + z-score in one row). */
export type RealYieldCurveSpreadTimeSeriesRow = {
  date: string;
  spread_pct: number;
  z_score: number | null;
};

export type RealYieldCurveSpreadOutput = {
  current_metrics: RealYieldCurveSpreadCurrentMetrics;
  /** Bespoke wire-frozen shape — spread (percent) + z-score per row. */
  time_series: RealYieldCurveSpreadTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.PERCENT series of the spread over the
   *  displayed window.  Optional defensively; the REST detail endpoint
   *  always returns it. */
  time_series_spread?: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score. */
  time_series_zscore?: TimeSeries;
};

// --- /detail/spread ---

export type CurveSpreadCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  spread_label: string;
  current_spread_bps: number;
  daily_change_bps: number | null;
  current_z_score: number | null;
  rolling_window_days: number;
  short_tenor_yield: number | null;
  long_tenor_yield: number | null;
};

export type CurveSpreadTimeSeriesRow = {
  date: string;
  spread_bps: number;
  z_score: number | null;
};

export type CurveSpreadOutput = {
  current_metrics: CurveSpreadCurrentMetrics;
  time_series: CurveSpreadTimeSeriesRow[];
};

// --- /detail/cross-market ---

export type CrossMarketSpreadCurrentMetrics = {
  as_of_date: string;
  curve_family_1: string;
  curve_family_2: string;
  tenor: string;
  spread_label: string;
  current_spread_bps: number;
  daily_change_bps: number | null;
  weekly_change_bps: number | null;
  monthly_change_bps: number | null;
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  curve_family_1_yield: number | null;
  curve_family_2_yield: number | null;
};

export type CrossMarketSpreadTimeSeriesRow = {
  date: string;
  spread_bps: number;
  z_score: number | null;
};

export type CrossMarketSpreadOutput = {
  current_metrics: CrossMarketSpreadCurrentMetrics;
  time_series: CrossMarketSpreadTimeSeriesRow[];
};

// --- /detail/butterfly ---

export type ButterflyCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  butterfly_label: string;
  current_butterfly_bps: number;
  daily_change_bps: number | null;
  current_z_score: number | null;
  rolling_window_days: number;
  high_252d_bps: number | null;
  low_252d_bps: number | null;
  percentile_252d: number | null;
  wing_short_bps: number | null;
  wing_long_bps: number | null;
  short_tenor_yield: number | null;
  belly_tenor_yield: number | null;
  long_tenor_yield: number | null;
};

export type ButterflyTimeSeriesRow = {
  date: string;
  butterfly_bps: number;
  z_score: number | null;
};

export type ButterflyOutput = {
  current_metrics: ButterflyCurrentMetrics;
  time_series: ButterflyTimeSeriesRow[];
};

// --- /detail/regime --- (no time_series — classification only)

export type RegimeCurrentMetrics = {
  as_of_date: string;
  prior_date: string;
  curve_family: string;
  lookback_period: string;
  spread_label: string;
  regime_tag: string;
  regime_description: string;
  front_tenor: string;
  back_tenor: string;
  // PR14 wire-format honesty (Round 3 A4, post-Codex review):
  // renamed from front_yield_current/etc. to front_level_current/etc.
  // because classify_curve_move is now curve-family-agnostic and the
  // underlying observation may be a sovereign yield, an OIS par rate,
  // an inflation swap rate, or a linker real yield depending on the
  // bound curve_family.  "Level" is the unit-agnostic name; the
  // playbook owns the observation semantics.
  front_level_current: number | null;
  back_level_current: number | null;
  front_level_prior: number | null;
  back_level_prior: number | null;
  front_change_bps: number | null;
  back_change_bps: number | null;
  spread_current_bps: number | null;
  spread_prior_bps: number | null;
  spread_change_bps: number | null;
};

export type RegimeOutput = {
  current_metrics: RegimeCurrentMetrics;
};

// --- /detail/policy-futures-price ---
// Standalone-bridge type for the policy_futures strip-position price-level
// primitive (SFR1 / SFR2 / ER1 / SFI1 / ... — STIR strip slots keyed by
// curve_family + strip_position).  Own type — mirrors the bespoke
// Pydantic schema (FuturesPriceLevelOutput) byte-for-byte.  Two unit
// spaces side-by-side per row: raw_price (contract native quote space,
// e.g. 100 − rate for SFR / ER / SFI) AND implied_rate_pct (desk-
// recognised PERCENT).  Z-score lives on the IMPLIED-RATE axis because
// inverse-priced raw prices would flip the sign of every extreme reading.

export type PolicyFuturesPriceTimeSeriesRow = {
  date: string;
  raw_price: number;
  implied_rate_pct: number;
};

export type PolicyFuturesPriceCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  strip_position: number;
  /** Strip-slot master stem (e.g. 'SFR1', 'ER2', 'SFI1') — stable across rolls. */
  contract_code: string;
  /** Current-front underlying that the strip slot resolves to as_of (e.g. 'SFRM26'). */
  underlying_contract_code: string | null;
  security_name: string | null;
  expiry_date: string | null;
  contract_size: number | null;
  tick_size: number | null;
  tick_value: number | null;
  /** When true (SFR / ER / SFI in V1), implied_rate_pct = 100 − raw_price. */
  inverse_priced: boolean;
  /** 'RFR' (SOFR / SONIA) or 'IBOR' (Euribor) — methodology disclosure label. */
  short_rate_regime: string;
  /** Quote-unit label for raw_price ('100 - rate' for inverse; 'rate (%)' for direct). */
  quote_units: string;
  /** Latest cleaned price in the contract's native quote space (NOT a rate). */
  raw_price: number;
  /** Desk-recognised implied rate in PERCENT (PR14 wire-frozen name). */
  implied_rate_pct: number;
  /** 1-day raw-price change (raw subtraction; NOT *100). */
  daily_change_raw_price: number | null;
  /** 1-day implied-rate change in PERCENT POINTS (NOT bps; multiply by 100 for bps). */
  daily_change_implied_rate_pct: number | null;
  /** Rolling 252-trading-day z-score of the IMPLIED-RATE level. */
  z_score_implied_rate: number | null;
  high_252d_implied_rate_pct: number | null;
  low_252d_implied_rate_pct: number | null;
  mid_252d_implied_rate_pct: number | null;
  high_252d_raw_price: number | null;
  low_252d_raw_price: number | null;
  mid_252d_raw_price: number | null;
  /** Percentile rank of implied_rate_pct within the trailing 252d (0-100). */
  percentile_252d: number | null;
  observation_count: number;
};

export type PolicyFuturesPriceLevelOutput = {
  current_metrics: PolicyFuturesPriceCurrentMetrics;
  /** Bespoke wire shape — each row carries BOTH raw_price + implied_rate_pct
   *  (TimeSeriesUnits has no PRICE member in V1; ADR-gated extension). */
  time_series: PolicyFuturesPriceTimeSeriesRow[];
  /** P5 / ADR 0013 caveat composed at runtime by compute() — includes the
   *  rolling-generic-strip-read label, regime (RFR / IBOR), inverse-pricing
   *  rule, implied-rate computation formula, and z-score lookback window.
   *  Surfaced verbatim on the extended view's methodology card (NOT a
   *  hardcoded TS literal). */
  methodology_disclosure: string;
};

// --- /detail/policy-futures-butterfly ---
// Standalone-bridge type for the policy_futures same-curve 3-leg simple-
// butterfly primitive (e.g. SOFR_FUT SFR1-SFR2-SFR3, EUR_SHORT_RATE_FUT
// ER1-ER2-ER4, SONIA_FUT SFI1-SFI2-SFI3).  Mirrors
// ``FuturesButterflySimpleOutput`` from rates_agent/policy_futures/tools/
// futures_butterfly_simple/schemas.py byte-for-byte (snake_case wire fields
// preserved).  Butterfly is on the IMPLIED-RATE axis in PERCENT POINTS
// (NOT bps — the policy-futures sub-domain stays in PERCENT POINTS on
// implied-rate-derived objects; the sovereign / OIS butterfly ``_bps``
// convention does NOT apply here).  Sign convention:
//   ``butterfly_value_pct = rate_body − 0.5 * (rate_wing_short +
//     rate_wing_long)``
// where rate_* is the per-leg implied rate in PERCENT (derived from
// the leg's raw_price via the per-strip ``inverse_pricing`` flag).
// POSITIVE ⇒ belly CHEAP (body rate above wing average); NEGATIVE ⇒
// belly RICH.  Per-leg disclosure block carries the strip-slot master
// stems (stable across rolls) + current-front underlying contracts
// (rotate at roll) + per-leg SCD2 metadata.

export type FuturesButterflySimpleTimeSeriesRow = {
  date: string;
  butterfly_value_pct: number;
  z_score: number | null;
};

export type FuturesButterflySimpleCurrentMetrics = {
  as_of_date: string;
  curve_family: string;
  strip_position_wing_short: number;
  strip_position_body: number;
  strip_position_wing_long: number;
  /** Human-readable label for the butterfly triple, e.g. "SFR1-SFR2-SFR3". */
  butterfly_label: string;
  /** Strip-slot master stem of the short wing (stable across rolls). */
  contract_code_wing_short: string;
  /** Strip-slot master stem of the body leg. */
  contract_code_body: string;
  /** Strip-slot master stem of the long wing. */
  contract_code_wing_long: string;
  /** Current-front underlying the short wing resolves to as of as_of_date. */
  underlying_contract_code_wing_short: string | null;
  underlying_contract_code_body: string | null;
  underlying_contract_code_wing_long: string | null;
  security_name_wing_short: string | null;
  security_name_body: string | null;
  security_name_wing_long: string | null;
  expiry_date_wing_short: string | null;
  expiry_date_body: string | null;
  expiry_date_wing_long: string | null;
  /** Inverse-pricing flag — when true (SFR / ER / SFI in V1),
   *  implied_rate_pct = 100 − raw_price per leg. */
  inverse_priced: boolean;
  /** 'RFR' (SOFR / SONIA) or 'IBOR' (Euribor) — methodology disclosure label. */
  short_rate_regime: string;
  /** Latest per-leg implied rates in PERCENT. */
  implied_rate_pct_wing_short: number;
  implied_rate_pct_body: number;
  implied_rate_pct_wing_long: number;
  /** Latest butterfly value in PERCENT POINTS:
   *  ``rate_body − 0.5 * (rate_wing_short + rate_wing_long)``.  Positive ⇒
   *  belly CHEAP in rate space.  NOT bps — the policy-futures sub-domain's
   *  unit convention. */
  butterfly_value_pct: number;
  /** 1-trading-day change in butterfly_value_pct (raw subtraction in PERCENT
   *  POINTS; multiply by 100 to render in bps). */
  daily_change_butterfly_value_pct: number | null;
  /** Rolling 252-trading-day z-score of the butterfly series. */
  z_score_butterfly: number | null;
  /** Trailing 252-trading-day range on the butterfly series, PERCENT POINTS. */
  high_252d_butterfly_value_pct: number | null;
  low_252d_butterfly_value_pct: number | null;
  mid_252d_butterfly_value_pct: number | null;
  /** Percentile rank of butterfly_value_pct within the trailing 252-day
   *  butterfly range (0-100). */
  percentile_252d: number | null;
  rolling_window_days: number;
  observation_count: number;
};

export type FuturesButterflySimpleOutput = {
  current_metrics: FuturesButterflySimpleCurrentMetrics;
  /** Bespoke wire-frozen per-row shape — butterfly value + z-score per row. */
  time_series: FuturesButterflySimpleTimeSeriesRow[];
  /** Canonical TimeSeriesUnits.PERCENT series of the butterfly value. */
  time_series_butterfly: TimeSeries;
  /** Canonical TimeSeriesUnits.Z_SCORE series of the rolling z-score. */
  time_series_zscore: TimeSeries;
  /** P5 / ADR 0013 caveat composed at runtime by compute() — includes the
   *  sign convention, fixed 50-50 simple-butterfly weighting, inverse-pricing
   *  rule, per-curve_family regime (RFR vs IBOR), z-score lookback window,
   *  trailing-range window, strip-position keying, and the explicit refusal
   *  of meeting-by-meeting policy-path framing + DV01-neutral / regression-
   *  fitted butterfly variants.  Surfaced verbatim on the extended view's
   *  methodology card (NOT a hardcoded TS literal). */
  methodology_disclosure: string;
};

