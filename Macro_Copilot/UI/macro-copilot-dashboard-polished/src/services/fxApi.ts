import type {
  FXCarryResponse,
  FXScannerResponse,
  FXSpotLevelResponse,
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