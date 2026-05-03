// ============================================================================
// Types pour les données FX
// ============================================================================

// Type pour une ligne de scanner FX
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

// Type pour la réponse du scanner FX
export type FXScannerResponse = {
  rows: FXScannerRow[];
};

// Type pour les métriques du FX Spot Level
export type FXSpotLevelMetrics = {
  as_of_date: string;  // Date des données
  pair: string;  // Paire de devises (ex : EURUSD)
  current_spot: number;  // Niveau actuel du spot
  daily_change_pct: number | null;  // Changement quotidien en pourcentage
  weekly_change_pct: number | null;  // Changement hebdomadaire en pourcentage
  monthly_change_pct: number | null;  // Changement mensuel en pourcentage
  z_score: number | null;  // Z-Score
  high_252d: number | null;  // Haut sur 252 jours
  low_252d: number | null;  // Bas sur 252 jours
  percentile_252d: number | null;  // Percentile sur 252 jours
  observation_count: number;  // Nombre d'observations
};

// Type pour la réponse du FX Spot Level
export type FXSpotLevelResponse = {
  current_metrics: FXSpotLevelMetrics;  // Données de métriques du FX Spot
};

// Type pour une ligne de données du FX Carry
export type FXCarryRow = {
  pair: string;  // Paire de devises (ex : EURUSD)
  spot_date: string;  // Date du spot
  forward_date: string;  // Date du forward
  spot: number;  // Valeur du spot
  tenor: string;  // Terme (ex : 1M, 3M, etc.)
  forward_points: number;  // Points de forward
  forward_points_spot_units: number;  // Points de forward en unités de spot
  outright_forward: number;  // Forward direct
  carry_bps_spot: number;  // Carry en points de base au spot
  carry_annualized_pct: number;  // Carry annualisé en pourcentage
  carry_signal: string;  // Signal de carry (ex : "positive", "negative")
};

// Type pour la réponse du FX Carry
export type FXCarryResponse = {
  tenor: string;  // Terme (ex : 1M, 3M, etc.)
  rows: FXCarryRow[];  // Liste des lignes de données du carry
};

export type FXForwardCurveRow = {
  pair: string;
  spot_date: string;
  forward_date: string;
  spot: number;
  tenor: string;
  tenor_days: number;
  forward_points: number;
  forward_points_spot_units: number;
  outright_forward: number;
  carry_bps_spot: number;
  carry_annualized_pct: number;
};

export type FXForwardCurveResponse = {
  pair: string;
  rows: FXForwardCurveRow[];
};

export type FXRealizedVolMetrics = {
  as_of_date: string;
  pair: string;
  spot: number;
  window_observations: number;
  realized_vol_annualized_pct: number | null;
  realized_vol_z_score: number | null;
  daily_return_pct: number | null;
  observation_count: number;
};

export type FXRealizedVolTimeSeriesRow = {
  date: string;
  realized_vol_annualized_pct: number | null;
};

export type FXRealizedVolResponse = {
  current_metrics: FXRealizedVolMetrics;
  time_series: FXRealizedVolTimeSeriesRow[];
};

export type FXTradeSetupSignal = {
  name: string;
  score: number;
  stance: 'bullish' | 'bearish' | 'neutral';
  description: string;
};

export type FXTradeSetupResponse = {
  pair: string;
  as_of_date: string;
  tenor: string;
  direction: 'bullish' | 'bearish' | 'neutral';
  confidence: 'high' | 'medium' | 'low';
  total_score: number;
  summary: string;
  key_drivers: string[];
  risks: string[];
  follow_up_questions: string[];
  signals: FXTradeSetupSignal[];
  spot_snapshot: FXSpotLevelMetrics;
  carry_snapshot: FXCarryRow | null;
  forward_curve: FXForwardCurveRow[];
  realized_vol_snapshot: FXRealizedVolMetrics;
};

// Type global pour toutes les données de la page FX
export type FXPageData = {
  scanner: FXScannerResponse;  // Données du scanner FX
  eurusd: FXSpotLevelResponse;  // Données FX Spot Level pour EUR/USD
  carry: FXCarryResponse;  // Données FX Carry
};
