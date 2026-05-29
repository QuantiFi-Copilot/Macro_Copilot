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
  YieldLevelOutput,
  CurveSpreadOutput,
  CrossMarketSpreadOutput,
  ButterflyOutput,
  RegimeOutput,
  RealYieldLevelOutput,
  BreakevenInflationSimpleOutput,
  BreakevenButterflyOutput,
  RealYieldCurveSpreadOutput,
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

// ---------------------------------------------------------------------------
// Workspace detail endpoints (full output incl. time_series for charting)
// ---------------------------------------------------------------------------

function buildQuery(params: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : '';
}

export type YieldDetailParams = {
  curve_family: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailYield(
  params: YieldDetailParams,
): Promise<YieldLevelOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/yield${buildQuery(params)}`);
}

// ---------------------------------------------------------------------------
// /detail/real_yield  — Phase-1 pilot standalone bridge
// ---------------------------------------------------------------------------
// Per docs_revamped/03_standards/methodology_exposure.md §5 every new
// primitive ships its OWN typed-detail endpoint + its OWN service
// helper.  Consumed by BOTH the extended Build view (single-tool
// queries) and the compact Build view (multi-tool query DAG nodes) —
// same payload, different rendering density per
// docs_revamped/03_standards/rendering_density.md §1.
//
// The four Phase-1 exposed methodology overrides (z_score_window_days,
// z_score_min_periods, z_score_ddof, field_name) are all optional
// query params; the backend falls through to YAML defaults when omitted.

export type RealYieldDetailParams = {
  curve_family: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
  z_score_window_days?: number;
  z_score_min_periods?: number;
  z_score_ddof?: number;
};

export function fetchDetailRealYield(
  params: RealYieldDetailParams,
): Promise<RealYieldLevelOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/real_yield${buildQuery(params)}`);
}

// ---------------------------------------------------------------------------
// /detail/breakeven  — Stage-B standalone bridge
// ---------------------------------------------------------------------------
// Bond-implied breakeven inflation (nominal − linker real).  Own typed
// helper per the standalone-bridge contract; consumed by BOTH the
// extended and compact Build views (same payload, different density).
// The four exposed methodology overrides are optional query params.

export type BreakevenDetailParams = {
  nominal_curve_family: string;
  linker_curve_family: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
  z_score_window_days?: number;
  z_score_min_periods?: number;
  z_score_ddof?: number;
};

export function fetchDetailBreakeven(
  params: BreakevenDetailParams,
): Promise<BreakevenInflationSimpleOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/breakeven${buildQuery(params)}`);
}

// ---------------------------------------------------------------------------
// /detail/breakeven-butterfly  — standalone bridge for the 3-point
// same-country breakeven butterfly (curvature of the bond-implied
// breakeven curve).  Own typed helper; consumed by BOTH the extended
// and compact Build views and the Monitor tile.  Same four exposed
// methodology overrides as the spot breakeven primitive.
// ---------------------------------------------------------------------------

export type BreakevenButterflyDetailParams = {
  nominal_curve_family: string;
  linker_curve_family: string;
  short_tenor: string;
  belly_tenor: string;
  long_tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailBreakevenButterfly(
  params: BreakevenButterflyDetailParams,
): Promise<BreakevenButterflyOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/breakeven-butterfly${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/real_yield_curve_spread  — Stage-C standalone bridge
// ---------------------------------------------------------------------------
// Same-country linker real-yield curve spread (long − short real yield).
// Own typed helper; consumed by BOTH Build views.  The three rolling-
// z-score overrides apply to the spread's own z-score; field_name flows
// to both endpoint level calls.

export type RealYieldCurveSpreadDetailParams = {
  curve_family: string;
  short_tenor: string;
  long_tenor: string;
  lookback_days?: number;
  field_name?: string;
  z_score_window_days?: number;
  z_score_min_periods?: number;
  z_score_ddof?: number;
};

export function fetchDetailRealYieldCurveSpread(
  params: RealYieldCurveSpreadDetailParams,
): Promise<RealYieldCurveSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/real_yield_curve_spread${buildQuery(params)}`,
  );
}

export type SpreadDetailParams = {
  curve_family: string;
  short_tenor?: string;
  long_tenor?: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailSpread(
  params: SpreadDetailParams,
): Promise<CurveSpreadOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/spread${buildQuery(params)}`);
}

export type CrossMarketDetailParams = {
  curve_family_1: string;
  curve_family_2: string;
  tenor?: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailCrossMarket(
  params: CrossMarketDetailParams,
): Promise<CrossMarketSpreadOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/cross-market${buildQuery(params)}`);
}

export type ButterflyDetailParams = {
  curve_family: string;
  short_tenor?: string;
  belly_tenor?: string;
  long_tenor?: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailButterfly(
  params: ButterflyDetailParams,
): Promise<ButterflyOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/butterfly${buildQuery(params)}`);
}

export type RegimeDetailParams = {
  curve_family: string;
  front_tenor?: string;
  back_tenor?: string;
  lookback_period?: string;
  field_name?: string;
};

export function fetchDetailRegime(
  params: RegimeDetailParams,
): Promise<RegimeOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/regime${buildQuery(params)}`);
}
