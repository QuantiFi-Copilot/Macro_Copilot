// ============================================================================
// Rates API Client
// Thin fetch wrappers against the FastAPI backend.
// ============================================================================

import type {
  YieldSnapshotResponse,
  CurveShapesResponse,
  ScannerResponse,
  CrossMarketResponse,
  RegimeResponse,
} from '@/types/rates';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const RATES_PREFIX = `${API_BASE}/api/v1/rates`;

// ---------------------------------------------------------------------------
// Generic fetcher with error handling
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Card endpoints (pre-aggregated, for the Rates page)
// ---------------------------------------------------------------------------

export function fetchYieldSnapshot(
  params?: { tenors?: string; curves?: string },
): Promise<YieldSnapshotResponse> {
  const qs = new URLSearchParams();
  if (params?.tenors) qs.set('tenors', params.tenors);
  if (params?.curves) qs.set('curves', params.curves);
  const query = qs.toString();
  return fetchJSON(`${RATES_PREFIX}/yield-snapshot${query ? `?${query}` : ''}`);
}

export function fetchCurveShapes(): Promise<CurveShapesResponse> {
  return fetchJSON(`${RATES_PREFIX}/curve-shapes`);
}

export function fetchScanner(
  params?: { top_n?: number; min_abs_z_score?: number },
): Promise<ScannerResponse> {
  const qs = new URLSearchParams();
  if (params?.top_n) qs.set('top_n', String(params.top_n));
  if (params?.min_abs_z_score) qs.set('min_abs_z_score', String(params.min_abs_z_score));
  const query = qs.toString();
  return fetchJSON(`${RATES_PREFIX}/scanner${query ? `?${query}` : ''}`);
}

export function fetchCrossMarket(): Promise<CrossMarketResponse> {
  return fetchJSON(`${RATES_PREFIX}/cross-market`);
}

export function fetchRegimes(): Promise<RegimeResponse> {
  return fetchJSON(`${RATES_PREFIX}/regimes`);
}
