export type FXScannerRow = {
  pair: string;
  ticker: string;
  as_of_date: string;
  current_spot: number;
  daily_change_pct: number | null;
  weekly_change_pct: number | null;
  monthly_change_pct: number | null;
  z_score: number | null;
  momentum_1m_pct: number | null;
  momentum_3m_pct: number | null;
  signal: string;
};

export type FXScannerResponse = {
  rows: FXScannerRow[];
};

export type FXSpotLevelMetrics = {
  as_of_date: string;
  pair: string;
  current_spot: number;
  daily_change_pct: number | null;
  weekly_change_pct: number | null;
  monthly_change_pct: number | null;
  z_score: number | null;
  high_252d: number | null;
  low_252d: number | null;
  percentile_252d: number | null;
  observation_count: number;
};

export type FXSpotLevelResponse = {
  current_metrics: FXSpotLevelMetrics;
};

export type FXPageData = {
  scanner: FXScannerResponse;
  eurusd: FXSpotLevelResponse;
};