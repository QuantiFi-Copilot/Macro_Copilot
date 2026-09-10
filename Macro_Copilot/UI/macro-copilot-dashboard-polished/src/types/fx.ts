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

// ---------------------------------------------------------------------------
// Wave 2 widgets (2026-05-28)
// ---------------------------------------------------------------------------

export type FXTimeSeriesRow = { date: string; value: number | null };

export type FXCarryBasketSnapshot = {
  as_of_date: string;
  market_scope: string;
  tenor: string;
  top_n: number;
  basket_construction: string;
  current_cumulative_excess_return_pct: number;
  annualized_return_pct: number | null;
  annualized_volatility_pct: number | null;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
};

export type FXCarryBasketResponse = {
  snapshot: FXCarryBasketSnapshot;
  cumulative_excess_return_series: { rows: FXTimeSeriesRow[] };
  constituent_pairs: string[];
};

export type FXSmilePoint = {
  current_vol_pts: number;
  z_score: number | null;
};

export type FXVolSmileResponse = {
  current_metrics: {
    pair: string;
    tenor: string;
    as_of_date: string;
    atm: FXSmilePoint;
    rr_25: FXSmilePoint;
    bf_25: FXSmilePoint;
    rr_10: FXSmilePoint;
    bf_10: FXSmilePoint;
  };
};

export type FXCrossCurrencyBasisResponse = {
  current_metrics: {
    pair: string;
    tenor: string;
    as_of_date: string;
    sign_convention: string;
    local_ois_curve: string;
    usd_ois_curve: string;
    current_fx_implied_yield_diff_pct: number;
    current_ois_diff_pct: number;
    current_basis_bps: number;
    daily_change_bps: number | null;
    weekly_change_bps: number | null;
    monthly_change_bps: number | null;
    z_score: number | null;
    high_252d_bps: number | null;
    low_252d_bps: number | null;
    percentile_252d: number | null;
  };
};