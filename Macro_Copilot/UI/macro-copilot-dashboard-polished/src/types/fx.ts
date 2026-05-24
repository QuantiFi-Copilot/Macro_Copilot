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

export type FXCarryRow = {
  pair: string;
  spot_date: string;
  forward_date: string;
  spot: number;
  tenor: string;
  forward_points: number;
  forward_points_spot_units: number;
  outright_forward: number;
  carry_bps_spot: number;
  carry_annualized_pct: number;
  carry_signal: string;
  // Scanner / z-score fields added in backend Phase A step 6 — every
  // row carries a rolling 252-day z-score / percentile / range on its
  // OWN carry_annualized_pct historical series, plus a 1-indexed
  // rank within the ordered output.  Optional for backward
  // compatibility with any pre-step-6 cached payload.
  carry_z_score: number | null;
  carry_percentile_252d: number | null;
  carry_high_252d: number | null;
  carry_low_252d: number | null;
  carry_observation_count: number;
  rank: number;
};

export type FXCarryResponse = {
  tenor: string;
  rows: FXCarryRow[];
};

export type FXForwardCurveRow = {
  tenor: string;
  tenor_days: number;
  spot: number;
  spot_date: string;
  forward_date: string;
  forward_points: number;
  forward_points_spot_units: number;
  outright_forward: number;
  carry_bps_spot: number;
  carry_annualized_pct: number;
  z_score: number | null;
  high_252d: number | null;
  low_252d: number | null;
  percentile_252d: number | null;
  observation_count: number;
};

export type FXForwardCurveResponse = {
  pair: string;
  as_of_date: string;
  rows: FXForwardCurveRow[];
};

export type FXPageData = {
  scanner: FXScannerResponse;
  eurusd: FXSpotLevelResponse;
  carry: FXCarryResponse;
};