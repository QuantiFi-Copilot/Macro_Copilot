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

