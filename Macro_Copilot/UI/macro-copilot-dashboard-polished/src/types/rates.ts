// ============================================================================
// Rates API Response Types
// These types mirror the FastAPI Pydantic response models exactly.
// Any change to the API schemas must be reflected here.
// ============================================================================
import {
  FXCarryResponse,
  FXForwardCurveResponse,
  FXRealizedVolResponse,
  FXScannerResponse,
  FXSpotLevelResponse,
  FXTradeSetupResponse,
} from './fx';  // Assure-toi que le chemin est correct
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
  front_yield_current: number | null;
  back_yield_current: number | null;
  front_yield_prior: number | null;
  back_yield_prior: number | null;
  front_change_bps: number | null;
  back_change_bps: number | null;
  spread_current_bps: number | null;
  spread_prior_bps: number | null;
  spread_change_bps: number | null;
};

export type RegimeOutput = {
  current_metrics: RegimeCurrentMetrics;
};

// ============================================================================
// WORKSPACE VIEW MODEL
// Discriminated union the views render against.  WorkspacePage parses the
// URL into one of these and passes it down.
// ============================================================================

export type WorkspaceViewType =
  | 'spread'
  | 'cross_market'
  | 'butterfly'
  | 'yield'
  | 'forward'
  | 'regime'
  | 'scanner'
  | 'fx_spot'
  | 'fx_carry'
  | 'fx_forward_curve'
  | 'fx_scanner'
  | 'fx_realized_vol'
  | 'fx_trade_setup';

export type WorkspaceParams = Record<string, string>;

// ============================================================================
// WorkspaceData Type
// Adding fx_spot and fx_carry to the WorkspaceData type
export type WorkspaceData =
  | { kind: 'spread'; data: CurveSpreadOutput }
  | { kind: 'cross_market'; data: CrossMarketSpreadOutput }
  | { kind: 'butterfly'; data: ButterflyOutput }
  | { kind: 'yield'; data: YieldLevelOutput }
  | { kind: 'regime'; data: RegimeOutput }
  | { kind: 'scanner'; data: ScannerResponse }
  | { kind: 'fx_spot'; data: FXSpotLevelResponse }  // Ajouter cette ligne
  | { kind: 'fx_carry'; data: FXCarryResponse }   // Ajouter cette ligne
  | { kind: 'fx_forward_curve'; data: FXForwardCurveResponse }
  | { kind: 'fx_scanner'; data: FXScannerResponse }
  | { kind: 'fx_realized_vol'; data: FXRealizedVolResponse }
  | { kind: 'fx_trade_setup'; data: FXTradeSetupResponse };
