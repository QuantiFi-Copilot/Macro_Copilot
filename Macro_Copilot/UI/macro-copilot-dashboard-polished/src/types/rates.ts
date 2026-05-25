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

