import type {
  FXCarryResponse,
  FXForwardCurveResponse,
  FXScannerResponse,
  FXSpotLevelResponse,
  FXCarryBasketResponse,
  FXVolSmileResponse,
  FXCrossCurrencyBasisResponse,
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
  params?: {
    tenor?: string;
    rank_by?: string;
    top_n?: number;
    lookback_days?: number;
    field_name?: string;
  },
): Promise<FXCarryResponse> {
  const qs = new URLSearchParams();
  if (params?.tenor) qs.set('tenor', params.tenor);
  if (params?.rank_by) qs.set('rank_by', params.rank_by);
  if (params?.top_n) qs.set('top_n', String(params.top_n));
  if (params?.lookback_days) qs.set('lookback_days', String(params.lookback_days));
  if (params?.field_name) qs.set('field_name', params.field_name);
  const query = qs.toString();
  return fetchJSON(`${FX_PREFIX}/carry${query ? `?${query}` : ''}`);
}

export function fetchFXForwardCurve(
  params: { pair: string; lookback_days?: number; field_name?: string },
): Promise<FXForwardCurveResponse> {
  const qs = new URLSearchParams();
  qs.set('pair', params.pair);
  if (params.lookback_days) qs.set('lookback_days', String(params.lookback_days));
  if (params.field_name) qs.set('field_name', params.field_name);
  return fetchJSON(`${FX_PREFIX}/forward-curve?${qs.toString()}`);
}

export function fetchFXCarryBasket(
  params?: {
    market_scope?: string;
    tenor?: string;
    top_n?: number;
    basket_construction?: string;
    lookback_days?: number;
  },
): Promise<FXCarryBasketResponse> {
  const qs = new URLSearchParams();
  if (params?.market_scope) qs.set('market_scope', params.market_scope);
  if (params?.tenor) qs.set('tenor', params.tenor);
  if (params?.top_n) qs.set('top_n', String(params.top_n));
  if (params?.basket_construction) qs.set('basket_construction', params.basket_construction);
  if (params?.lookback_days) qs.set('lookback_days', String(params.lookback_days));
  const query = qs.toString();
  return fetchJSON(`${FX_PREFIX}/carry-basket${query ? `?${query}` : ''}`);
}

export function fetchFXVolSmile(
  params: { pair: string; tenor?: string; lookback_days?: number },
): Promise<FXVolSmileResponse> {
  const qs = new URLSearchParams();
  qs.set('pair', params.pair);
  if (params.tenor) qs.set('tenor', params.tenor);
  if (params.lookback_days) qs.set('lookback_days', String(params.lookback_days));
  return fetchJSON(`${FX_PREFIX}/vol-smile?${qs.toString()}`);
}

export function fetchFXCrossCurrencyBasis(
  params: { pair: string; tenor?: string; lookback_days?: number },
): Promise<FXCrossCurrencyBasisResponse> {
  const qs = new URLSearchParams();
  qs.set('pair', params.pair);
  if (params.tenor) qs.set('tenor', params.tenor);
  if (params.lookback_days) qs.set('lookback_days', String(params.lookback_days));
  return fetchJSON(`${FX_PREFIX}/cross-currency-basis?${qs.toString()}`);
}