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
  BreakevenCurveSpreadOutput,
  RealYieldButterflyOutput,
  RealYieldCurveSpreadOutput,
  CrossMarketInflationSwapSpreadOutput,
  InflationSwapButterflyOutput,
  OisButterflyOutput,
  OisCrossMarketSpreadOutput,
  OisCurveSpreadOutput,
  OisRateLevelOutput,
  OisForwardRateOutput,
  InflationSwapRateLevelOutput,
  InflationSwapCurveSpreadOutput,
  ScanInflationSwapsExtremesOutput,
  PolicyFuturesPriceLevelOutput,
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
// /detail/breakeven-curve-spread  — standalone bridge for the same-country
// bond-implied breakeven curve spread (2-point tenor spread on a single
// nominal/linker pair, e.g. UST/USD_TIPS 2s10s breakeven).  Own typed
// helper per the standalone-bridge contract; consumed by BOTH Build
// views and the Monitor tile.  Rolling-z-score conventions are YAML-
// locked on this primitive — only ``lookback_days`` + ``field_name`` are
// exposed at the input layer (mirrors the sibling breakeven-butterfly
// bridge).
// ---------------------------------------------------------------------------

export type BreakevenCurveSpreadDetailParams = {
  nominal_curve_family: string;
  linker_curve_family: string;
  short_tenor: string;
  long_tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailBreakevenCurveSpread(
  params: BreakevenCurveSpreadDetailParams,
): Promise<BreakevenCurveSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/breakeven-curve-spread${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/real-yield-butterfly  — same-country linker real-yield butterfly
// ---------------------------------------------------------------------------
// Three-point curvature on a SINGLE linker curve (no nominal pair — distinct
// from breakeven-butterfly).  Own typed helper per the standalone-bridge
// contract; consumed by BOTH Build views and the Monitor tile.  The
// rolling-z-score conventions are YAML-locked on this primitive (no
// input-layer overrides); field_name remains overridable.

export type RealYieldButterflyDetailParams = {
  curve_family: string;
  short_tenor: string;
  belly_tenor: string;
  long_tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailRealYieldButterfly(
  params: RealYieldButterflyDetailParams,
): Promise<RealYieldButterflyOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/real-yield-butterfly${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/cross-market-zcis  — same-tenor cross-market ZCIS spread bridge
// ---------------------------------------------------------------------------
// Two-curve, single-tenor primitive (e.g. USD_ZCIS 5Y minus EUR_ZCIS 5Y).
// Own typed helper per the standalone-bridge contract; consumed by BOTH
// Build views and the Monitor tile.  The rolling-z-score conventions are
// YAML-locked on this primitive (no input-layer overrides); ``field_name``
// + ``lookback_days`` remain exposed.

export type CrossMarketZcisDetailParams = {
  leg_a_curve_family: string;
  leg_b_curve_family: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailCrossMarketZcis(
  params: CrossMarketZcisDetailParams,
): Promise<CrossMarketInflationSwapSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/cross-market-zcis${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/zcis-butterfly  — same-curve ZCIS butterfly bridge
// ---------------------------------------------------------------------------
// Three-point curvature on a SINGLE ZCIS curve family (e.g. USD_ZCIS 2s5s10s,
// EUR_ZCIS 5s10s30s, GBP_ZCIS 2s10s30s).  Own typed helper per the standalone-
// bridge contract; consumed by BOTH Build views and the Monitor tile.  The
// rolling-z-score conventions are YAML-locked on this primitive (no input-
// layer overrides — mirrors the sibling breakeven-butterfly / real-yield-
// butterfly bridges); ``field_name`` + ``lookback_days`` remain exposed.

export type InflationSwapButterflyDetailParams = {
  curve_family: string;
  short_tenor: string;
  belly_tenor: string;
  long_tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailInflationSwapButterfly(
  params: InflationSwapButterflyDetailParams,
): Promise<InflationSwapButterflyOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/zcis-butterfly${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/ois-butterfly  — same-curve OIS butterfly bridge
// ---------------------------------------------------------------------------
// Three-point curvature on a SINGLE OIS curve family (e.g. USD_SOFR_OIS
// 2s5s10s, EUR_ESTR_OIS 2s5s10s, GBP_SONIA_OIS 2s5s10s).  Own typed helper
// per the standalone-bridge contract; consumed by BOTH Build views and the
// Monitor tile.  Rolling-z-score conventions are YAML-locked on this
// primitive (no input-layer overrides — mirrors the sibling sovereign /
// linker / ZCIS butterfly bridges); ``field_name`` + ``lookback_days``
// remain exposed.

export type OisButterflyDetailParams = {
  curve_family: string;
  short_tenor: string;
  belly_tenor: string;
  long_tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailOisButterfly(
  params: OisButterflyDetailParams,
): Promise<OisButterflyOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/ois-butterfly${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/ois-curve-spread  — same-curve OIS tenor spread bridge
// ---------------------------------------------------------------------------
// Two-point spread on a SINGLE OIS curve family (e.g. USD_SOFR_OIS 2s10s,
// EUR_ESTR_OIS 1s5s, GBP_SONIA_OIS 5s30s).  Own typed helper per the
// standalone-bridge contract; consumed by BOTH Build views and the Monitor
// tile.  Rolling-z-score conventions are YAML-locked on this primitive (no
// input-layer overrides — mirrors the sibling OIS butterfly bridge);
// ``lookback_days`` + ``field_name`` remain exposed.

export type OisCurveSpreadDetailParams = {
  curve_family: string;
  short_tenor: string;
  long_tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailOisCurveSpread(
  params: OisCurveSpreadDetailParams,
): Promise<OisCurveSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/ois-curve-spread${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/ois-cross-market-spread  — same-tenor cross-market OIS spread bridge
// ---------------------------------------------------------------------------
// Two-curve, single-tenor primitive (e.g. SOFR 2Y minus ESTR 2Y).  Own typed
// helper per the standalone-bridge contract; consumed by BOTH Build views and
// the Monitor tile.  Rolling-z-score conventions are YAML-locked on this
// primitive (no input-layer overrides — mirrors the sibling OIS curve_spread
// / butterfly bridges); ``lookback_days`` + ``field_name`` remain exposed.
// The schema layer rejects ``curve_family_1 == curve_family_2`` (same-curve
// tenor spreads belong to ``/detail/ois-curve-spread``).

export type OisCrossMarketSpreadDetailParams = {
  curve_family_1: string;
  curve_family_2: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailOisCrossMarketSpread(
  params: OisCrossMarketSpreadDetailParams,
): Promise<OisCrossMarketSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/ois-cross-market-spread${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/ois-rate-level  — single-tenor OIS par-swap-rate snapshot bridge
// ---------------------------------------------------------------------------
// Per-tenor OIS par-swap-rate snapshot (e.g. USD_SOFR_OIS 2Y, EUR_ESTR_OIS
// 10Y, GBP_SONIA_OIS 5Y).  Own typed helper per the standalone-bridge
// contract; consumed by BOTH Build views and the Monitor tile.  Rolling-
// z-score conventions are YAML-locked on this primitive (no input-layer
// overrides — mirrors the sibling OIS curve_spread / butterfly bridges);
// ``field_name`` + ``lookback_days`` remain exposed.

export type OisRateLevelDetailParams = {
  curve_family: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailOisRateLevel(
  params: OisRateLevelDetailParams,
): Promise<OisRateLevelOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/ois-rate-level${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/ois-forward-rate  — implied OIS forward-rate snapshot bridge
// ---------------------------------------------------------------------------
// Implied forward rate spanning a (start, end) window on one OIS curve
// (e.g. SOFR 1Y1Y, 5Y5Y ESTR, 2Y1Y SONIA).  Own typed helper per the
// standalone-bridge contract; consumed by BOTH Build views and the
// Monitor tile.  Two equivalent input modes — tenor-pair (start_tenor +
// end_tenor) OR date-pair (start_date + end_date); supply exactly ONE.
// Rolling-z-score conventions are YAML-locked on this primitive (no
// input-layer overrides — mirrors the OIS rate_level / curve_spread /
// butterfly siblings); only ``lookback_days`` + ``field_name`` are
// exposed at the API layer.

export type OisForwardRateDetailParams = {
  curve_family: string;
  /** Start tenor of the forward window (e.g. '1Y' for 1Y1Y).  Mutually
   *  exclusive with start_date. */
  start_tenor?: string;
  /** End tenor of the forward window (e.g. '2Y' for 1Y1Y).  Mutually
   *  exclusive with end_date. */
  end_tenor?: string;
  /** Start date of the forward window (YYYY-MM-DD).  Mutually exclusive
   *  with start_tenor. */
  start_date?: string;
  /** End date of the forward window (YYYY-MM-DD).  Mutually exclusive
   *  with end_tenor. */
  end_date?: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailOisForwardRate(
  params: OisForwardRateDetailParams,
): Promise<OisForwardRateOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/ois-forward-rate${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/inflation-swap-rate-level  — single-pillar ZCIS rate snapshot bridge
// ---------------------------------------------------------------------------
// Per-pillar zero-coupon inflation swap (ZCIS) rate snapshot (e.g. USD_ZCIS
// 5Y, EUR_ZCIS 10Y, GBP_ZCIS 2Y).  Own typed helper per the standalone-bridge
// contract; consumed by BOTH Build views and the Monitor tile.  Rolling-
// z-score conventions are YAML-locked on this primitive (no input-layer
// overrides — mirrors the OIS rate_level / sibling level tools);
// ``field_name`` + ``lookback_days`` remain exposed.

export type InflationSwapRateLevelDetailParams = {
  curve_family: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailInflationSwapRateLevel(
  params: InflationSwapRateLevelDetailParams,
): Promise<InflationSwapRateLevelOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/inflation-swap-rate-level${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/inflation-swap-curve-spread  — same-curve ZCIS tenor spread bridge
// ---------------------------------------------------------------------------
// Same-curve, two-tenor ZCIS spread (e.g. USD_ZCIS 5s10s, EUR_ZCIS 5s30s).  Own
// typed helper per the standalone-bridge contract; consumed by BOTH Build
// views and the Monitor tile.  Rolling-z-score conventions are YAML-locked on
// this primitive (no input-layer overrides — mirrors the sibling ZCIS
// rate_level + breakeven_curve_spread tools); only ``lookback_days`` +
// ``field_name`` are exposed.  Cross-curve combinations belong to the
// separate cross-market-zcis primitive.

export type InflationSwapCurveSpreadDetailParams = {
  curve_family: string;
  short_tenor: string;
  long_tenor: string;
  lookback_days?: number;
  field_name?: string;
};

export function fetchDetailInflationSwapCurveSpread(
  params: InflationSwapCurveSpreadDetailParams,
): Promise<InflationSwapCurveSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/inflation-swap-curve-spread${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/zcis-scanner  — universe-wide ZCIS rate-extremes scanner bridge
// ---------------------------------------------------------------------------
// First SCANNER-shape primitive under the standalone-bridge contract.  Wire
// returns a ranked LIST of extremes — the per-tool BuildCompact renders a
// top-N table (NOT a sparkline), the BuildExtended renders the universe
// scan + full ranked detail.  Rolling-z-score conventions are YAML-locked
// on this primitive; only scope / threshold / anchor inputs are exposed.

export type ZcisScannerDetailParams = {
  /** Comma-separated list of ZCIS curve families (e.g.
   *  "USD_ZCIS,EUR_ZCIS"). Omit for the full universe. */
  curve_families?: string;
  /** Number of extreme stems to return; omit for the YAML default. */
  top_n?: number;
  /** Minimum absolute z-score threshold; omit for the YAML default. */
  min_abs_z_score?: number;
  /** ISO-format date (YYYY-MM-DD) anchoring the scan; omit for the
   *  most-recent shared trading day in the DB. */
  as_of_date?: string;
};

export function fetchDetailZcisScanner(
  params: ZcisScannerDetailParams,
): Promise<ScanInflationSwapsExtremesOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/zcis-scanner${buildQuery(params)}`,
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

// ---------------------------------------------------------------------------
// /detail/policy-futures-price  — policy_futures strip-position price level
// ---------------------------------------------------------------------------
// Standalone bridge for the policy_futures futures_price_level primitive
// (SFR1 / SFR2 / ER1 / SFI1 / ... strip slots on SOFR_FUT / SONIA_FUT /
// EUR_SHORT_RATE_FUT).  Keyed by ``(curve_family, strip_position)`` per
// ADR 0013 — strip-position-keyed monitors.  Consumed by BOTH the
// extended and compact Build views and the Monitor tile (single payload,
// different rendering density per rendering_density.md §1.1).
//
// Per ADR 0013 V1 there are no LLM-facing override paths for conventions
// (z_score_window_days / trailing_range_window_days etc. are YAML-locked);
// only the structural ``(curve_family, strip_position)`` keys plus
// ``lookback_days`` / ``as_of_date`` / ``field_name`` are exposed.

export type PolicyFuturesPriceDetailParams = {
  curve_family: string;
  strip_position: number;
  lookback_days?: number;
  /** YYYY-MM-DD; omit to anchor at the universe's last observed
   *  trade_date for the requested strip (post-fetch data-max anchor). */
  as_of_date?: string;
  field_name?: string;
};

export function fetchDetailPolicyFuturesPrice(
  params: PolicyFuturesPriceDetailParams,
): Promise<PolicyFuturesPriceLevelOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/policy-futures-price${buildQuery(params)}`,
  );
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
