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

