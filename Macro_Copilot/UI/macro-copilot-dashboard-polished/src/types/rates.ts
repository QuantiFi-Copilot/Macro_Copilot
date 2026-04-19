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
