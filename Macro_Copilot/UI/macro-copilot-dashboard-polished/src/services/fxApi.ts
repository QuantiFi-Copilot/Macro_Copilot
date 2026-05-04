import type {
  FXCarryResponse,
  FXCorrelationBetaResponse,
  FXCurrencyThesisResponse,
  FXDataHealthResponse,
  FXForwardCurveResponse,
  FXMacroRiskOverlayResponse,
  FXRealizedVolResponse,
  FXRegimeClassifierResponse,
  FXScannerResponse,
  FXSpotLevelResponse,
  FXTradeSetupResponse,
  FXVolRiskPremiumResponse,
} from '@/types/fx';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const FX_PREFIX = `${API_BASE}/api/v1/fx`;

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(
      `API ${res.status}: ${res.statusText}${body ? ` — ${body}` : ''}`,
    );
  }

  return res.json() as Promise<T>;
}

export function fetchFXScanner(
  params?: { top_n?: number; market_scope?: string },
): Promise<FXScannerResponse> {
  const qs = new URLSearchParams();
  if (params?.top_n) qs.set('top_n', String(params.top_n));
  if (params?.market_scope) qs.set('market_scope', params.market_scope);
  const query = qs.toString();
  return fetchJSON(`${FX_PREFIX}/scanner${query ? `?${query}` : ''}`);
}

export function fetchFXSpotLevel(
  params: { pair: string; lookback_days?: number },
): Promise<FXSpotLevelResponse> {
  const qs = new URLSearchParams();
  qs.set('pair', params.pair);
  if (params.lookback_days) qs.set('lookback_days', String(params.lookback_days));
  return fetchJSON(`${FX_PREFIX}/spot-level?${qs.toString()}`);
}

export function fetchFXCarry(
  params?: { tenor?: string },
): Promise<FXCarryResponse> {
  const qs = new URLSearchParams();
  if (params?.tenor) qs.set('tenor', params.tenor);
  const query = qs.toString();
  return fetchJSON(`${FX_PREFIX}/carry${query ? `?${query}` : ''}`);
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();

  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    qs.set(key, String(value));
  }

  const query = qs.toString();
  return query ? `?${query}` : '';
}

export type FXSpotDetailParams = {
  pair: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailFXSpotLevel(
  params: FXSpotDetailParams,
): Promise<FXSpotLevelResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/spot-level${buildQuery(params)}`);
}

export type FXCarryDetailParams = {
  tenor?: string;
};

export function fetchDetailFXCarry(
  params: FXCarryDetailParams,
): Promise<FXCarryResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/carry${buildQuery(params)}`);
}

export type FXDataHealthDetailParams = {
  lookback_days?: number;
  stale_after_days?: number;
  field_name?: string;
};

export function fetchDetailFXDataHealth(
  params: FXDataHealthDetailParams,
): Promise<FXDataHealthResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/data-health${buildQuery(params)}`);
}

export type FXForwardCurveDetailParams = {
  pair: string;
};

export function fetchDetailFXForwardCurve(
  params: FXForwardCurveDetailParams,
): Promise<FXForwardCurveResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/forward-curve${buildQuery(params)}`);
}

export type FXRealizedVolDetailParams = {
  pair: string;
  window_observations?: number;
  lookback_days?: number;
  return_type?: 'log_return' | 'simple_return';
  field_name?: string;
};

export function fetchDetailFXRealizedVol(
  params: FXRealizedVolDetailParams,
): Promise<FXRealizedVolResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/realized-vol${buildQuery(params)}`);
}

export type FXTradeSetupDetailParams = {
  pair: string;
  tenor?: string;
  vol_window_observations?: number;
  lookback_days?: number;
};

export function fetchDetailFXTradeSetup(
  params: FXTradeSetupDetailParams,
): Promise<FXTradeSetupResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/trade-setup${buildQuery(params)}`);
}

export type FXMacroRiskOverlayDetailParams = {
  pair: string;
  lookback_days?: number;
  correlation_window_observations?: number;
  field_name?: string;
};

export function fetchDetailFXMacroRiskOverlay(
  params: FXMacroRiskOverlayDetailParams,
): Promise<FXMacroRiskOverlayResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/macro-risk-overlay${buildQuery(params)}`);
}

export type FXCorrelationBetaDetailParams = {
  pair: string;
  lookback_days?: number;
  window_observations?: number;
  field_name?: string;
};

export function fetchDetailFXCorrelationBeta(
  params: FXCorrelationBetaDetailParams,
): Promise<FXCorrelationBetaResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/correlation-beta${buildQuery(params)}`);
}

export type FXCurrencyThesisDetailParams = {
  currency?: string;
  view?: 'long' | 'short';
  lookback_days?: number;
  top_n?: number;
  field_name?: string;
};

export function fetchDetailFXCurrencyThesis(
  params: FXCurrencyThesisDetailParams,
): Promise<FXCurrencyThesisResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/currency-thesis-monitor${buildQuery(params)}`);
}

export type FXRegimeClassifierDetailParams = {
  anchor_pair?: string;
  tenor?: string;
  lookback_days?: number;
  realized_window_observations?: number;
  correlation_window_observations?: number;
  field_name?: string;
};

export function fetchDetailFXRegimeClassifier(
  params: FXRegimeClassifierDetailParams,
): Promise<FXRegimeClassifierResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/regime-classifier${buildQuery(params)}`);
}

export type FXVolRiskPremiumDetailParams = {
  pair: string;
  tenor?: string;
  realized_window_observations?: number;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailFXVolRiskPremium(
  params: FXVolRiskPremiumDetailParams,
): Promise<FXVolRiskPremiumResponse> {
  return fetchJSON(`${FX_PREFIX}/detail/vol-risk-premium${buildQuery(params)}`);
}
