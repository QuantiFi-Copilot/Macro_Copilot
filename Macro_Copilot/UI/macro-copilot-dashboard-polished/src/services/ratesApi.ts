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
  ForwardBreakevenSimpleOutput,
  RealYieldButterflyOutput,
  RealYieldCurveSpreadOutput,
  CrossMarketInflationSwapSpreadOutput,
  InflationSwapButterflyOutput,
  OisButterflyOutput,
  OisCrossMarketSpreadOutput,
  OisCurveSpreadOutput,
  SwapSpreadOutput,
  OisRateLevelOutput,
  OisForwardRateOutput,
  InflationSwapForwardOutput,
  InflationSwapRateLevelOutput,
  InflationSwapCurveSpreadOutput,
  ScanExtremesOutput,
  ScanInflationSwapsExtremesOutput,
  ScanInflationLinkersExtremesOutput,
  SwapBreakevenBasisSimpleOutput,
  ScanBondFuturesExtremesOutput,
  ScanPolicyFuturesExtremesOutput,
  PolicyFuturesPriceLevelOutput,
  BondFuturesPriceLevelOutput,
  FuturesButterflySimpleOutput,
  FuturesCalendarSpreadOutput,
  FuturesCrossMarketSpreadOutput,
  FuturesPackAverageSimpleOutput,
  CrossCountryBreakevenSpreadSimpleOutput,
  CrossCountryRealYieldSpreadSimpleOutput,
  FinancingRateDetailResponse,
  OtrOfrSpreadOutput,
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
  asOfDate?: string,
): Promise<YieldSnapshotResponse> {
  const qs = new URLSearchParams();
  if (params?.tenors) qs.set('tenors', params.tenors);
  if (params?.curves) qs.set('curves', params.curves);
  if (asOfDate) qs.set('as_of_date', asOfDate);
  const query = qs.toString();
  return fetchJSON(`${RATES_PREFIX}/yield-snapshot${query ? `?${query}` : ''}`);
}

export function fetchCurveShapes(asOfDate?: string): Promise<CurveShapesResponse> {
  const qs = new URLSearchParams();
  if (asOfDate) qs.set('as_of_date', asOfDate);
  const query = qs.toString();
  return fetchJSON(`${RATES_PREFIX}/curve-shapes${query ? `?${query}` : ''}`);
}

export function fetchScanner(
  params?: { top_n?: number; min_abs_z_score?: number },
  asOfDate?: string,
): Promise<ScannerResponse> {
  const qs = new URLSearchParams();
  if (params?.top_n) qs.set('top_n', String(params.top_n));
  if (params?.min_abs_z_score) qs.set('min_abs_z_score', String(params.min_abs_z_score));
  if (asOfDate) qs.set('as_of_date', asOfDate);
  const query = qs.toString();
  return fetchJSON(`${RATES_PREFIX}/scanner${query ? `?${query}` : ''}`);
}

export function fetchCrossMarket(asOfDate?: string): Promise<CrossMarketResponse> {
  const qs = new URLSearchParams();
  if (asOfDate) qs.set('as_of_date', asOfDate);
  const query = qs.toString();
  return fetchJSON(`${RATES_PREFIX}/cross-market${query ? `?${query}` : ''}`);
}

export function fetchRegimes(asOfDate?: string): Promise<RegimeResponse> {
  const qs = new URLSearchParams();
  if (asOfDate) qs.set('as_of_date', asOfDate);
  const query = qs.toString();
  return fetchJSON(`${RATES_PREFIX}/regimes${query ? `?${query}` : ''}`);
}

// ---------------------------------------------------------------------------
// Workspace detail endpoints (full output incl. time_series for charting)
// ---------------------------------------------------------------------------

function buildQuery(
  params: Record<
    string,
    string | number | undefined | ReadonlyArray<string | number>
  >,
): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    // Array values render as REPEATED params (?tenors=2Y&tenors=10Y) —
    // FastAPI's List[str] Query convention used by the PCA /
    // rolling-regression bridges.  Additive: scalar callers unchanged.
    if (Array.isArray(v)) {
      for (const item of v) qs.append(k, String(item));
      continue;
    }
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
  as_of_date?: string;
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
  as_of_date?: string;
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
  as_of_date?: string;
};

export function fetchDetailBreakeven(
  params: BreakevenDetailParams,
): Promise<BreakevenInflationSimpleOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/breakeven${buildQuery(params)}`);
}

// ---------------------------------------------------------------------------
// /detail/forward-breakeven  — same-country forward bond-implied breakeven bridge
// ---------------------------------------------------------------------------
// Year-weighted linear forward bond-implied breakeven inflation between two
// same-country curve points (e.g. UST/USD_TIPS 5Y5Y, FR_OAT/EUR_FR_LINKER
// 5Y10Y).  Own typed helper per the standalone-bridge contract; consumed by
// BOTH Build views and the Monitor tile.  Rolling-z-score conventions are
// YAML-locked on this primitive — only ``lookback_days`` + ``field_name`` are
// exposed at the API layer (mirrors the sibling breakeven-curve-spread /
// breakeven-butterfly bridges).  Same-country invariant inherited
// transitively from the spot breakeven primitive.

export type ForwardBreakevenDetailParams = {
  nominal_curve_family: string;
  linker_curve_family: string;
  /** Start tenor of the forward window (e.g. '5Y' for 5Y5Y). */
  start_tenor: string;
  /** End tenor of the forward window (e.g. '10Y' for 5Y5Y) — must be strictly
   *  longer than start_tenor. */
  end_tenor: string;
  lookback_days?: number;
  field_name?: string;
  as_of_date?: string;
};

export function fetchDetailForwardBreakeven(
  params: ForwardBreakevenDetailParams,
): Promise<ForwardBreakevenSimpleOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/forward-breakeven${buildQuery(params)}`,
  );
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
  as_of_date?: string;
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
  as_of_date?: string;
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
  as_of_date?: string;
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
  as_of_date?: string;
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
  as_of_date?: string;
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
  as_of_date?: string;
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
  as_of_date?: string;
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
  as_of_date?: string;
};

export function fetchDetailOisCrossMarketSpread(
  params: OisCrossMarketSpreadDetailParams,
): Promise<OisCrossMarketSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/ois-cross-market-spread${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/swap-spread  — cross-domain sovereign-vs-OIS swap spread bridge
// ---------------------------------------------------------------------------
// Sovereign yield leg minus OIS rate leg at the same tenor in the same currency
// (e.g. UST 10Y minus USD_SOFR_OIS 10Y).  Own typed helper per the
// standalone-bridge contract; consumed by BOTH Build views and the Monitor
// tile.  Rolling-z-score conventions are YAML-locked on this primitive (no
// input-layer overrides — mirrors the sibling OIS curve_spread / butterfly /
// cross-market bridges).  The two legs use DIFFERENT Bloomberg field-name
// mnemonics; the wire defaults to the per-leg YAML conventions
// (``sovereign_leg_default_field`` = YLD_YTM_MID, ``ois_leg_default_field`` =
// PX_LAST) when the optional ``sovereign_field_name`` / ``ois_field_name``
// query params are omitted.  Currency-match invariant is enforced at the
// backend schema layer (cross-currency pairings rejected).

export type SwapSpreadDetailParams = {
  sovereign_curve_family: string;
  ois_curve_family: string;
  tenor: string;
  lookback_days?: number;
  sovereign_field_name?: string;
  ois_field_name?: string;
  as_of_date?: string;
};

export function fetchDetailSwapSpread(
  params: SwapSpreadDetailParams,
): Promise<SwapSpreadOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/swap-spread${buildQuery(params)}`);
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
  as_of_date?: string;
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
  as_of_date?: string;
};

export function fetchDetailOisForwardRate(
  params: OisForwardRateDetailParams,
): Promise<OisForwardRateOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/ois-forward-rate${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/inflation-swap-forward  — same-curve ZCIS forward rate snapshot bridge
// ---------------------------------------------------------------------------
// Forward inflation-swap rate between two pillars on the SAME ZCIS curve
// family (USD_ZCIS / EUR_ZCIS / GBP_ZCIS) — e.g. USD_ZCIS 5Y5Y, EUR_ZCIS
// 5Y5Y, GBP_ZCIS 2Y3Y.  Own typed helper per the standalone-bridge contract;
// consumed by BOTH Build views and the Monitor tile.  Same-curve invariant
// is enforced by the input layer (single ``curve_family`` field) — cross-
// curve forward combinations are NOT in scope and belong to a separate
// primitive.  Rolling-z-score conventions are YAML-locked on this primitive
// (no input-layer overrides — mirrors the OIS forward_rate / ZCIS rate_level
// / curve_spread siblings); only ``lookback_days`` + ``field_name`` are
// exposed.

export type InflationSwapForwardDetailParams = {
  curve_family: string;
  /** Start tenor of the forward window (e.g. '5Y' for 5Y5Y).  Must be a
   *  supported pillar on this curve_family. */
  start_tenor: string;
  /** End tenor of the forward window (e.g. '10Y' for 5Y5Y).  Must map to
   *  a strictly larger year fraction than start_tenor. */
  end_tenor: string;
  lookback_days?: number;
  field_name?: string;
  as_of_date?: string;
};

export function fetchDetailInflationSwapForward(
  params: InflationSwapForwardDetailParams,
): Promise<InflationSwapForwardOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/inflation-swap-forward${buildQuery(params)}`,
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
  as_of_date?: string;
};

export function fetchDetailInflationSwapRateLevel(
  params: InflationSwapRateLevelDetailParams,
): Promise<InflationSwapRateLevelOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/inflation-swap-rate-level${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/swap-breakeven-basis  — same-tenor swap-vs-bond inflation basis bridge
// ---------------------------------------------------------------------------
// Same-currency, single-tenor object composing the ZCIS rate-level + the
// bond-implied breakeven primitives at one pillar (e.g. USD_ZCIS 10Y minus
// UST/USD_TIPS 10Y breakeven).  Own typed helper per the standalone-bridge
// contract; consumed by BOTH Build views and the Monitor tile.  Rolling-
// z-score conventions are YAML-locked on this primitive — only
// ``lookback_days`` + ``field_name`` are exposed at the API layer (mirrors
// the sibling ZCIS rate_level / curve_spread / forward / cross-market
// bridges).  Same-currency invariant inherited transitively from the inner
// breakeven leg's same-country guard.

export type SwapBreakevenBasisDetailParams = {
  zcis_curve_family: string;
  nominal_curve_family: string;
  linker_curve_family: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
  as_of_date?: string;
};

export function fetchDetailSwapBreakevenBasis(
  params: SwapBreakevenBasisDetailParams,
): Promise<SwapBreakevenBasisSimpleOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/swap-breakeven-basis${buildQuery(params)}`,
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
  as_of_date?: string;
};

export function fetchDetailInflationSwapCurveSpread(
  params: InflationSwapCurveSpreadDetailParams,
): Promise<InflationSwapCurveSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/inflation-swap-curve-spread${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/scanner  — universe-wide SOVEREIGN yield-extremes scanner bridge
// ---------------------------------------------------------------------------
// SCANNER-shape primitive under the standalone-bridge contract.  Wire returns
// a ranked LIST of (curve_family, tenor) sovereign-benchmark extremes — the
// per-tool BuildCompact renders a top-N table (NOT a sparkline), the
// BuildExtended renders the universe scan + full ranked detail, and the
// preserved ``ScannerWidget`` keeps reading the pre-aggregated RatesPage feed
// (legacy, non-parameterised).  Rolling-z-score conventions are YAML-locked
// on this primitive (mirrors the sibling ZCIS / linker / bond-futures /
// policy-futures scanners); ``curve_families`` / ``top_n`` / ``min_abs_z_score``
// / ``field_name`` remain exposed.
//
// Distinct from the legacy ``fetchScanner`` above — that targets the
// pre-aggregated dashboard endpoint at ``/api/v1/rates/scanner``; this hits
// the typed-detail endpoint at ``/api/v1/rates/detail/scanner`` and returns
// the full ``ScannerOutput`` Pydantic mirror.

export type ScanExtremesDetailParams = {
  /** Comma-separated list of sovereign curve families to scan (e.g.
   *  "UST,DE_BUND,UK_GILT"). Omit for the full sovereign-benchmark universe. */
  curve_families?: string;
  /** Number of extreme stems to return; omit for the YAML default (10). */
  top_n?: number;
  /** Minimum absolute z-score threshold; omit for the YAML default (1.5). */
  min_abs_z_score?: number;
  /** Bloomberg observation field to scan.  Omit for the YAML default
   *  (YLD_YTM_MID). */
  field_name?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailScanner(
  params: ScanExtremesDetailParams,
): Promise<ScanExtremesOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/scanner${buildQuery(params)}`,
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
// /detail/linkers-scanner  — universe-wide linker REAL-YIELD extremes bridge
// ---------------------------------------------------------------------------
// SCANNER-shape primitive under the standalone-bridge contract.  Wire
// returns a ranked LIST of extremes — the per-tool BuildCompact renders a
// top-N table (NOT a sparkline), the BuildExtended renders the universe
// scan + full ranked detail.  Rolling-z-score conventions are YAML-locked
// on this primitive; only scope / threshold / anchor inputs are exposed.

export type LinkersScannerDetailParams = {
  /** Comma-separated list of linker curve families (e.g.
   *  "USD_TIPS,GBP_LINKER"). Omit for the full universe. */
  curve_families?: string;
  /** Number of extreme stems to return; omit for the YAML default. */
  top_n?: number;
  /** Minimum absolute z-score threshold; omit for the YAML default. */
  min_abs_z_score?: number;
  /** ISO-format date (YYYY-MM-DD) anchoring the scan; omit for the
   *  most-recent shared trading day in the DB. */
  as_of_date?: string;
};

export function fetchDetailLinkersScanner(
  params: LinkersScannerDetailParams,
): Promise<ScanInflationLinkersExtremesOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/linkers-scanner${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/bond-futures-scanner — universe-wide bond-futures extremes bridge
// ---------------------------------------------------------------------------
// SCANNER-shape primitive under the standalone-bridge contract.  Wire
// returns a MULTI-METRIC ranked LIST (top-N per metric across price LEVEL,
// 1-day price CHANGE, volume LEVEL, open-interest LEVEL — each ranked by
// absolute 252d-rolling z-score) — the per-tool BuildCompact renders a top-N
// table (NOT a sparkline), the BuildExtended renders the universe scan +
// full multi-metric ranked detail.  Rolling-z-score conventions are YAML-
// locked on this primitive; only scope / threshold / anchor inputs are
// exposed.

export type BondFuturesScannerDetailParams = {
  /** Comma-separated list of bond-futures curve families (e.g.
   *  "UST_FUT,DE_FUT"). Omit for the full universe. */
  curve_families?: string;
  /** Number of extreme stems to return PER METRIC; omit for the YAML default. */
  top_n?: number;
  /** Minimum absolute z-score threshold; omit for the YAML default. */
  min_abs_z_score?: number;
  /** ISO-format date (YYYY-MM-DD) anchoring the scan; omit for the
   *  most-recent shared trading day in the DB. */
  as_of_date?: string;
};

export function fetchDetailBondFuturesScanner(
  params: BondFuturesScannerDetailParams,
): Promise<ScanBondFuturesExtremesOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/bond-futures-scanner${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/policy-futures-scanner — universe-wide STIR extremes bridge
// ---------------------------------------------------------------------------
// SCANNER-shape primitive under the standalone-bridge contract.  Wire
// returns a MULTI-METRIC ranked LIST (top-N per metric across implied-rate
// LEVEL, 1-day implied-rate CHANGE in bps, volume LEVEL, open-interest
// LEVEL — each ranked by absolute 252d-rolling z-score) — the per-tool
// BuildCompact renders a top-N table (NOT a sparkline), the BuildExtended
// renders the universe scan + full multi-metric ranked detail.  Rolling-
// z-score conventions are YAML-locked on this primitive; only scope /
// threshold / anchor / metric-subset inputs are exposed.

export type PolicyFuturesScannerDetailParams = {
  /** Comma-separated list of policy-futures curve families (e.g.
   *  "SOFR_FUT,EUR_SHORT_RATE_FUT"). Omit for the full universe. */
  curve_families?: string;
  /** Number of extreme stems to return PER METRIC; omit for the YAML default. */
  top_n?: number;
  /** Minimum absolute z-score threshold; omit for the YAML default. */
  min_abs_z_score?: number;
  /** Comma-separated subset of the four metrics to rank (e.g.
   *  "implied_rate_level,volume_level"). Omit to rank all four. */
  metrics?: string;
  /** ISO-format date (YYYY-MM-DD) anchoring the scan; omit for the
   *  most-recent shared trading day in the DB. */
  as_of_date?: string;
};

export function fetchDetailPolicyFuturesScanner(
  params: PolicyFuturesScannerDetailParams,
): Promise<ScanPolicyFuturesExtremesOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/policy-futures-scanner${buildQuery(params)}`,
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
  as_of_date?: string;
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
  as_of_date?: string;
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
  as_of_date?: string;
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
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
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

// ---------------------------------------------------------------------------
// /detail/bond-futures-price  — bond_futures rolling-generic price level
// ---------------------------------------------------------------------------
// Standalone bridge for the bond_futures futures_price_level primitive
// (TY1 / UXY1 / US1 / WN1 / TU1 / FV1 on UST_FUT; RX1 / UB1 / DU1 / OE1
// on DE_FUT; G1 on UK_FUT; JB1 on JP_FUT; OAT1 on FR_FUT; IK1 / BTS1 on
// IT_FUT; KOA1 on ES_FUT; CN1 on CA_FUT; YM1 / XM1 on AU_FUT).  Keyed
// by ``(curve_family, contract_code)`` per TD#11 — the rolling-generic
// stem is the canonical disambiguator (TY1 vs UXY1 are both UST_FUT 10Y;
// US1 vs WN1 both UST_FUT 30Y).  DISTINCT from the policy_futures cousin
// above (same MCP function NAME ``get_futures_price_level_tool`` registered
// in a different MCP server; different sub-package; different schema).
//
// Per ADR 0013 V1 there are no LLM-facing override paths for conventions
// (z_score_window_days etc. are YAML-locked); only the structural
// ``(curve_family, contract_code)`` keys plus ``lookback_days`` /
// ``field_name`` are exposed.  No ``as_of_date`` — the bond_futures
// Pydantic Input does NOT accept that parameter (unlike the policy_futures
// sibling); the backend anchors at the universe's last observed trade_date.

export type BondFuturesPriceDetailParams = {
  curve_family: string;
  contract_code: string;
  lookback_days?: number;
  field_name?: string;
  as_of_date?: string;
};

export function fetchDetailBondFuturesPrice(
  params: BondFuturesPriceDetailParams,
): Promise<BondFuturesPriceLevelOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/bond-futures-price${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/policy-futures-butterfly  — policy_futures same-curve simple butterfly
// ---------------------------------------------------------------------------
// Three-strip-slot curvature on a SINGLE policy-futures curve family (e.g.
// SOFR_FUT SFR1-SFR2-SFR3 front-pack curvature, EUR_SHORT_RATE_FUT
// ER1-ER2-ER4 whites/reds curvature).  Own typed helper per the standalone-
// bridge contract; consumed by BOTH Build views and the Monitor tile.
// Rolling-z-score conventions are YAML-locked on this primitive (mirrors the
// sovereign / OIS / linker / ZCIS butterfly bridges); only the structural
// strip-position keys plus ``lookback_days`` / ``as_of_date`` / ``field_name``
// are exposed at the API layer.  The schema layer enforces
// ``strip_position_wing_short < strip_position_body < strip_position_wing_long``
// so the desk-recognised positive-butterfly direction (belly cheap) is
// unambiguous on the wire.

export type FuturesButterflySimpleDetailParams = {
  curve_family: string;
  strip_position_wing_short: number;
  strip_position_body: number;
  strip_position_wing_long: number;
  lookback_days?: number;
  /** YYYY-MM-DD; omit to anchor at the universe's last observed trade_date
   *  on the intersection of the three legs (post-fetch data-max anchor). */
  as_of_date?: string;
  field_name?: string;
};

export function fetchDetailPolicyFuturesButterfly(
  params: FuturesButterflySimpleDetailParams,
): Promise<FuturesButterflySimpleOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/policy-futures-butterfly${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/policy-futures-calendar — policy_futures same-curve calendar spread
// ---------------------------------------------------------------------------
// Two-strip-slot calendar spread on a SINGLE policy-futures curve family
// (e.g. SOFR_FUT SFR1-SFR2 front-pack slope, EUR_SHORT_RATE_FUT ER1-ER4
// whites slope).  Own typed helper per the standalone-bridge contract;
// consumed by BOTH Build views and the Monitor tile.  Rolling-z-score
// conventions are YAML-locked on this primitive (mirrors the sibling
// sovereign / OIS / linker / ZCIS curve-spread bridges); only the
// structural strip-position keys plus ``lookback_days`` / ``as_of_date`` /
// ``field_name`` are exposed at the API layer.  The schema layer enforces
// ``strip_position_short < strip_position_long`` so the desk-recognised
// sign convention is unambiguous on the wire (FRONT − BACK in PERCENT
// POINTS).  The frontend display layer flips the sign to BACK − FRONT in
// bps so a positive display value reads as steeper policy path.

export type FuturesCalendarSpreadDetailParams = {
  curve_family: string;
  strip_position_short: number;
  strip_position_long: number;
  lookback_days?: number;
  /** YYYY-MM-DD; omit to anchor at the universe's last observed trade_date
   *  on the intersection of both legs (post-fetch data-max anchor). */
  as_of_date?: string;
  field_name?: string;
};

export function fetchDetailPolicyFuturesCalendar(
  params: FuturesCalendarSpreadDetailParams,
): Promise<FuturesCalendarSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/policy-futures-calendar${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/policy-futures-cross-market — policy_futures cross-market spread
// ---------------------------------------------------------------------------
// Matched-strip implied-rate differential between TWO different
// policy_futures curve families at ONE strip position (e.g. SOFR_FUT vs
// SONIA_FUT strip 1 = SFR1 − SFI1, SOFR_FUT vs EUR_SHORT_RATE_FUT strip 4
// = SFR4 − ER4).  Own typed helper per the standalone-bridge contract;
// consumed by BOTH Build views and the Monitor tile.  Rolling-z-score
// conventions are YAML-locked (mirrors the sibling sovereign /
// inflation_swaps cross-market bridges); only the structural pair-leg
// + strip-position keys plus ``lookback_days`` / ``as_of_date`` /
// ``field_name`` are exposed at the API layer.  The schema layer
// enforces ``curve_family_a != curve_family_b`` (a self-spread is
// mathematically zero and operationally not a real desk object).

export type FuturesCrossMarketSpreadDetailParams = {
  curve_family_a: string;
  curve_family_b: string;
  strip_position: number;
  lookback_days?: number;
  /** YYYY-MM-DD; omit to anchor at the universe's last observed trade_date
   *  on the intersection of both legs (post-fetch data-max anchor). */
  as_of_date?: string;
  field_name?: string;
};

export function fetchDetailPolicyFuturesCrossMarket(
  params: FuturesCrossMarketSpreadDetailParams,
): Promise<FuturesCrossMarketSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/policy-futures-cross-market${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/policy-futures-pack-average — policy_futures same-curve pack average
// ---------------------------------------------------------------------------
// Pack-average implied rate (arithmetic mean across 4 consecutive quarterly
// STIR contracts) on a SINGLE policy-futures curve_family (e.g. SOFR_FUT
// whites = SFR1..SFR4, SONIA_FUT reds = SFI5..SFI8).  Own typed helper per
// the standalone-bridge contract; consumed by BOTH Build views and the
// Monitor tile.  Rolling-z-score conventions are YAML-locked on this
// primitive (mirrors the sibling policy_futures bridges); only the
// structural ``curve_family`` + ``pack`` keys plus ``lookback_days`` /
// ``as_of_date`` / ``field_name`` are exposed at the API layer.  Pack
// composition (whites = 1-4, reds = 5-8) is YAML-locked and NOT user-
// overridable; greens / blues are PR11 planned-extension territory.
// ``curve_family='EUR_SHORT_RATE_FUT'`` is admitted at the schema layer
// but returns a clean controlled-error envelope from compute() per ADR
// 0013 V1 scope (IBOR regime, missing ``delivery_month_type`` playbook
// metadata).

export type FuturesPackAverageSimpleDetailParams = {
  curve_family: string;
  /** Pack identifier: 'whites' (positions 1-4) or 'reds' (positions
   *  5-8). */
  pack: string;
  lookback_days?: number;
  /** YYYY-MM-DD; omit to anchor at the universe's last observed
   *  trade_date on the intersection of the four legs (post-fetch data-
   *  max anchor). */
  as_of_date?: string;
  field_name?: string;
};

export function fetchDetailPolicyFuturesPackAverage(
  params: FuturesPackAverageSimpleDetailParams,
): Promise<FuturesPackAverageSimpleOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/policy-futures-pack-average${buildQuery(params)}`,
  );
}

export type RegimeDetailParams = {
  curve_family: string;
  front_tenor?: string;
  back_tenor?: string;
  lookback_period?: string;
  field_name?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailRegime(
  params: RegimeDetailParams,
): Promise<RegimeOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/regime${buildQuery(params)}`);
}

// ---------------------------------------------------------------------------
// /detail/cross-country-breakeven-spread  — same-tenor cross-country
// bond-implied breakeven spread bridge
// ---------------------------------------------------------------------------
// Two-country, single-tenor primitive (e.g. UK 10Y BE minus US 10Y BE,
// FR 10Y BE minus US 10Y BE, CA 10Y BE minus US 10Y BE).  Each country
// contributes a (nominal, linker) pair at the shared tenor.  Sign
// convention POSITIVE = country_a > country_b breakeven.  Output is a
// SPREAD object — ships in BPS.  Own typed helper per the standalone-
// bridge contract; consumed by BOTH Build views and the Monitor tile.
// Cross-country invariant enforced at the schema layer
// (country_a_nominal_pair != country_b_nominal_pair AND
// country_a_linker_pair != country_b_linker_pair).  Rolling-z-score
// conventions are YAML-locked — only ``lookback_days`` + ``field_name``
// are exposed at the API layer (mirrors the sibling
// cross-market-zcis / swap-breakeven-basis bridges).

export type CrossCountryBreakevenSpreadDetailParams = {
  country_a_nominal_pair: string;
  country_a_linker_pair: string;
  country_b_nominal_pair: string;
  country_b_linker_pair: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
  as_of_date?: string;
};

export function fetchDetailCrossCountryBreakevenSpread(
  params: CrossCountryBreakevenSpreadDetailParams,
): Promise<CrossCountryBreakevenSpreadSimpleOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/cross-country-breakeven-spread${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/cross-country-real-yield-spread  — same-tenor cross-country
// linker REAL-YIELD differential bridge
// ---------------------------------------------------------------------------
// Two-curve, single-tenor primitive (e.g. USD_TIPS 10Y real yield minus
// GBP_LINKER 10Y real yield).  Each leg is a sovereign linker real-yield
// level at the shared tenor.  Sign convention POSITIVE = first_curve real
// yield > second_curve real yield; wire-locked at first minus second.
// Output spread is in PERCENT (not BPS — real yields are quoted in
// PERCENT); daily / weekly / monthly *changes* are reported in BPS per
// desk convention.  Own typed helper per the standalone-bridge contract;
// consumed by BOTH Build views and the Monitor tile.  Cross-country
// invariant enforced at the schema layer (first_curve_family !=
// second_curve_family).  Rolling-z-score conventions are YAML-locked —
// only ``lookback_days`` + ``field_name`` are exposed at the API layer.

export type CrossCountryRealYieldSpreadDetailParams = {
  first_curve_family: string;
  second_curve_family: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
  as_of_date?: string;
};

export function fetchDetailCrossCountryRealYieldSpread(
  params: CrossCountryRealYieldSpreadDetailParams,
): Promise<CrossCountryRealYieldSpreadSimpleOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/cross-country-real-yield-spread${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/financing-rate — standalone bridge with ROUTE-SIDE SYNTHESIS
// ---------------------------------------------------------------------------
// FIRST-OF-ITS-KIND architectural deviation in this factory.  The backend
// ``FinancingRateOutput`` is Panel-shaped (single-column daily-rate
// DataFrame) — NOT the standard ``current_metrics`` + ``time_series``
// snapshot shape.  Per the 2026-06-08 human resolution (Option (a)) the
// route handler synthesizes the snapshot shape on the fly from
// ``result.panel.payload``; the backend Output is preserved AS-IS for
// the ``evaluate_trades`` workflow consumer.  This fetcher consumes the
// SYNTHESIZED shape; above the typed-detail boundary the financing-rate
// surfaces are structurally identical to other snapshot tools.

export type FinancingRateDetailParams = {
  method?: string;
  proxy_curve: string;
  lookback_days?: number;
  as_of_date?: string;
};

export function fetchDetailFinancingRate(
  params: FinancingRateDetailParams,
): Promise<FinancingRateDetailResponse> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/financing-rate${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/otr-ofr-spread  — sovereign cash-bond OTR/OFR yield-spread bridge
// ---------------------------------------------------------------------------
// On-the-run vs first-off-the-run yield spread for one (country, tenor)
// sovereign cash-bond slot (e.g. US 10Y OTR/OFR).  Own typed helper per the
// standalone-bridge contract; consumed by BOTH Build views and the Monitor
// tile.  Sign convention POSITIVE = OTR yield ABOVE OFR (OTR cheap to OFR —
// the inverted-liquidity-premium signature); typical signature is NEGATIVE
// (OTR rich, freshly auctioned premium).  Rolling-z-score conventions are
// YAML-locked on this primitive (no input-layer overrides); only structural
// (country, tenor) plus ``lookback_days`` + ``field_name`` are exposed.

export type OtrOfrSpreadDetailParams = {
  country: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
  as_of_date?: string;
};

export function fetchDetailOtrOfrSpread(
  params: OtrOfrSpreadDetailParams,
): Promise<OtrOfrSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/otr-ofr-spread${buildQuery(params)}`,
  );
}

// ============================================================================
// Consolidation wave — standalone-bridge fetch helpers.
// One typed helper per /api/v1/rates/detail/<kind> route, consumed by
// the owning module's Shared.ts hook (BOTH Build views + Monitor read
// the SAME endpoint — methodology_exposure.md §5).
// ============================================================================

import type {
  BetaAdjustedSpreadOutput,
  BuildLinkerPanelOutput,
  BuildPolicyFuturesStripPanelOutput,
  BuildZcisPanelOutput,
  CpiSurpriseOutput,
  FuturesStripSnapshotOutput,
  FuturesVolumeOiOutput,
  HalfLifeOutput,
  NfpSurpriseOutput,
  OtrHistoryOutput,
  ScanOisExtremesOutput,
  PcaYieldCurveOutput,
  RollingRegressionOutput,
  SovereignYieldPanelOutput,
  VolumeOpenInterestSnapshotOutput,
  WirpMeetingPricingOutput,
  YieldChangeAttributionPcaOutput,
  ZscoreCustomOutput,
} from '@/types/rates';

// --- /detail/half-life (calculate_half_life_tool) ---
// ``curve_family_2`` set → PAIR mode (the (cf1 − cf2) spread at
// ``tenor``); omitted → single-series mode.  The pasted-series mode is
// NOT bridged over GET (orchestrator/MCP affordance only).
export type HalfLifeDetailParams = {
  curve_family: string;
  tenor: string;
  curve_family_2?: string;
  lookback_days?: number;
  field_name?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailHalfLife(
  params: HalfLifeDetailParams,
): Promise<HalfLifeOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/half-life${buildQuery(params)}`);
}

// --- /detail/cpi-surprise (calculate_cpi_surprise_tool) ---
export type CpiSurpriseDetailParams = {
  country: string;
  lookback_releases?: number;
};

export function fetchDetailCpiSurprise(
  params: CpiSurpriseDetailParams,
): Promise<CpiSurpriseOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/cpi-surprise${buildQuery(params)}`,
  );
}

// --- /detail/nfp-surprise (calculate_nfp_surprise_tool) ---
// US NFP locked on the backend — display window is the only knob.
export type NfpSurpriseDetailParams = {
  lookback_releases?: number;
};

export function fetchDetailNfpSurprise(
  params: NfpSurpriseDetailParams,
): Promise<NfpSurpriseOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/nfp-surprise${buildQuery(params)}`,
  );
}

// --- /detail/zscore-custom (calculate_zscore_custom_tool) ---
export type ZscoreCustomDetailParams = {
  curve_family: string;
  tenor: string;
  z_score_window_days: number;
  lookback_days?: number;
  field_name?: string;
  as_of_date?: string;
};

export function fetchDetailZscoreCustom(
  params: ZscoreCustomDetailParams,
): Promise<ZscoreCustomOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/zscore-custom${buildQuery(params)}`,
  );
}

// --- /detail/wirp-meeting-pricing (calculate_wirp_meeting_pricing_tool) ---
export type WirpMeetingPricingDetailParams = {
  central_bank: string;
  selection_mode?: 'next_n_meetings' | 'specific_meeting_date';
  n_meetings?: number;
  meeting_date?: string;
  as_of_date?: string;
};

export function fetchDetailWirpMeetingPricing(
  params: WirpMeetingPricingDetailParams,
): Promise<WirpMeetingPricingOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/wirp-meeting-pricing${buildQuery(params)}`,
  );
}

// --- /detail/pca-yield-curve (calculate_pca_yield_curve_tool) ---
// ``tenors`` renders as repeated params (?tenors=2Y&tenors=10Y).
export type PcaYieldCurveDetailParams = {
  curve_family: string;
  tenors?: ReadonlyArray<string>;
  lookback_days?: number;
  n_components?: number;
  change_frequency?: 'daily' | 'weekly';
  field_name?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailPcaYieldCurve(
  params: PcaYieldCurveDetailParams,
): Promise<PcaYieldCurveOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/pca-yield-curve${buildQuery(params)}`,
  );
}

// --- /detail/rolling-regression (calculate_rolling_regression_tool) ---
// Regressors flatten to PAIRED repeated lists —
// ``regressor_curve_families[i]`` pairs with ``regressor_tenors[i]``
// (the route 422s on a length mismatch).  One ``field_name`` applies
// to the target AND every regressor leg.
export type RollingRegressionDetailParams = {
  target_curve_family: string;
  target_tenor: string;
  regressor_curve_families: ReadonlyArray<string>;
  regressor_tenors: ReadonlyArray<string>;
  regression_window_days: number;
  lookback_days?: number;
  field_name?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailRollingRegression(
  params: RollingRegressionDetailParams,
): Promise<RollingRegressionOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/rolling-regression${buildQuery(params)}`,
  );
}

// --- /detail/futures-volume-oi (get_futures_volume_oi_tool) ---
export type FuturesVolumeOiDetailParams = {
  curve_family: string;
  contract_code: string;
  lookback_days?: number;
  as_of_date?: string;
};

export function fetchDetailFuturesVolumeOi(
  params: FuturesVolumeOiDetailParams,
): Promise<FuturesVolumeOiOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/futures-volume-oi${buildQuery(params)}`,
  );
}

// --- /detail/beta-adjusted-spread (calculate_beta_adjusted_spread_tool) ---
export type BetaAdjustedSpreadDetailParams = {
  target_curve_family: string;
  target_tenor: string;
  regressor_curve_family: string;
  regressor_tenor: string;
  regression_window_days: number;
  lookback_days?: number;
  field_name?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailBetaAdjustedSpread(
  params: BetaAdjustedSpreadDetailParams,
): Promise<BetaAdjustedSpreadOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/beta-adjusted-spread${buildQuery(params)}`,
  );
}

// --- /detail/yield-change-attribution
//     (calculate_yield_change_attribution_pca_tool) ---
// Inline-fit bridge only — the ``pasted_loadings`` mode stays on the
// generic run endpoint / MCP (a loadings matrix doesn't fit query
// params).
export type YieldChangeAttributionDetailParams = {
  curve_family: string;
  target_tenor: string;
  start_date: string;
  end_date: string;
  pca_lookback_days?: number;
  n_components?: number;
  change_frequency?: 'daily' | 'weekly';
  tenors?: ReadonlyArray<string>;
  field_name?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailYieldChangeAttribution(
  params: YieldChangeAttributionDetailParams,
): Promise<YieldChangeAttributionPcaOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/yield-change-attribution${buildQuery(params)}`,
  );
}
// === END TEMP SIBLING STUBS ===

// ---------------------------------------------------------------------------
// /detail/otr-history  — sovereign cash-bond OTR transition-log bridge
// ---------------------------------------------------------------------------
// CATEGORICAL TIMELINE shape — the wire carries the SCD2 on-the-run window
// list for one (country, tenor) slot plus the current-OTR snapshot, NOT a
// numeric time series.  Own typed helper per the standalone-bridge contract;
// consumed by BOTH Build views (no Monitor tile — the read cadence is
// auction-event-driven, not daily-glance).  ``lookback_days`` is the single
// central methodology knob (PR8).

export type OtrHistoryDetailParams = {
  country: string;
  tenor: string;
  lookback_days?: number;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailOtrHistory(
  params: OtrHistoryDetailParams,
): Promise<OtrHistoryOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/otr-history${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/ois-scanner  — universe-wide OIS rate-extremes scanner bridge
// ---------------------------------------------------------------------------
// SCANNER-shape primitive under the standalone-bridge contract.  Wire returns
// a ranked LIST of (curve_family, tenor) OIS par-swap-rate extremes — the
// per-tool BuildCompact renders a top-N table (NOT a sparkline), the
// BuildExtended renders the universe scan + full ranked detail.  Rolling-
// z-score conventions are YAML-locked on this primitive (mirrors the
// sovereign / ZCIS / linker / bond-futures / policy-futures scanners);
// ``curve_families`` / ``top_n`` / ``min_abs_z_score`` / ``field_name``
// remain exposed.  OIS analogue of ``fetchDetailScanner``.

export type OisScannerDetailParams = {
  /** Comma-separated list of OIS curve families to scan (e.g.
   *  "USD_SOFR_OIS,EUR_ESTR_OIS"). Omit for the full OIS universe. */
  curve_families?: string;
  /** Number of extreme stems to return; omit for the schema default (10). */
  top_n?: number;
  /** Minimum absolute z-score threshold; omit for the schema default (1.5). */
  min_abs_z_score?: number;
  /** Bloomberg observation field to scan.  Omit for the schema default
   *  (PX_LAST — mid par swap rate). */
  field_name?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/undefined → latest live data. */
  as_of_date?: string;
};

export function fetchDetailOisScanner(
  params: OisScannerDetailParams,
): Promise<ScanOisExtremesOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/ois-scanner${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/policy-futures-strip-snapshot — policy_futures whole-strip snapshot
// ---------------------------------------------------------------------------
// One row per configured strip position (V1: 1..8) on ONE policy-futures
// curve_family, all aligned to a single as_of_date (intersection-of-trading-
// days anchor — the date on which "the strip is steep / flat / inverted"
// makes sense).  Own typed helper per the standalone-bridge contract;
// consumed by BOTH Build views.  Conventions (z window, strip-positions
// list, rounding) are YAML-locked; only curve_family / as_of_date plus the
// two Bloomberg field-name overrides are exposed (mirrors the MCP wrapper).

export type PolicyFuturesStripSnapshotDetailParams = {
  curve_family: string;
  /** YYYY-MM-DD; omit to anchor at the universe's last observed
   *  trade_date where ALL configured strip positions have a value
   *  (post-fetch data-max anchor). */
  as_of_date?: string;
  /** Bloomberg price-field override; omit for the YAML default (PX_LAST). */
  last_price_field_name?: string;
  /** Bloomberg OI-field override; omit for the YAML default (OPEN_INT). */
  open_interest_field_name?: string;
};

export function fetchDetailPolicyFuturesStripSnapshot(
  params: PolicyFuturesStripSnapshotDetailParams,
): Promise<FuturesStripSnapshotOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/policy-futures-strip-snapshot${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/policy-futures-voi-snapshot — policy_futures volume + OI snapshot
// ---------------------------------------------------------------------------
// Daily volume + open interest + ΔOI + OI z-score + percentile-of-range +
// 22d rolling volume context for ONE (curve_family, strip_position) pair —
// the positioning / flow read on the STIR strip slot (the price / implied-
// rate read lives on the sibling policy-futures-price bridge).  Own typed
// helper per the standalone-bridge contract; consumed by BOTH Build views.
// Conventions (OI z window 252d, volume window 22d, field mnemonics) are
// YAML-locked; NO field_name input by design (PR9 — the volume / OI
// mnemonics are owned by the YAML, not per-query knobs).

export type PolicyFuturesVoiSnapshotDetailParams = {
  curve_family: string;
  strip_position: number;
  lookback_days?: number;
  /** YYYY-MM-DD; omit to anchor at the universe's last observed
   *  trade_date for the requested strip (post-fetch data-max anchor). */
  as_of_date?: string;
};

export function fetchDetailPolicyFuturesVoiSnapshot(
  params: PolicyFuturesVoiSnapshotDetailParams,
): Promise<VolumeOpenInterestSnapshotOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/policy-futures-voi-snapshot${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/sovereign-yield-panel — panel-builder standalone bridge
// ---------------------------------------------------------------------------
// Per docs_revamped/03_standards/methodology_exposure.md §5 every new tool
// ships its OWN typed-detail endpoint + service helper.  Consumed by BOTH the
// extended and compact Build views (rendering_density dual-view).  The Input's
// nested leg-spec list flattens to PAIRED repeated query lists
// (leg_curve_families[i] ↔ leg_tenors[i]) — same flattening discipline as the
// /detail/rolling-regression regressor specs.  The wire carries the panel
// CONTRACT only (the Panel artifact itself is dropped route-side, mirroring
// the MCP layer).

export type SovereignYieldPanelDetailParams = {
  /** One entry per leg, paired index-wise with ``leg_tenors``. */
  leg_curve_families: ReadonlyArray<string>;
  /** One entry per leg, paired index-wise with ``leg_curve_families``. */
  leg_tenors: ReadonlyArray<string>;
  /** Earliest trade_date to include (inclusive, YYYY-MM-DD). */
  start_date: string;
  /** Latest trade_date (inclusive).  Omit → latest in the DB. */
  end_date?: string;
  /** Bloomberg field mnemonic applied to ALL legs.  Omit → YAML default. */
  field_name?: string;
  /** 'raise' | 'forward_fill_only' | 'drop_rows_any_missing'.  Omit → YAML. */
  missing_data_policy?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailSovereignYieldPanel(
  params: SovereignYieldPanelDetailParams,
): Promise<SovereignYieldPanelOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/sovereign-yield-panel${buildQuery(params)}`,
  );
}

// ---------------------------------------------------------------------------
// /detail/linker-panel — panel-builder standalone bridge
// ---------------------------------------------------------------------------
// Per docs_revamped/03_standards/methodology_exposure.md §5.  Consumed by
// BOTH the extended and compact Build views (rendering_density dual-view).
// ``curve_families`` renders as repeated query params via buildQuery.  The
// wire carries the panel CONTRACT only (Panel artifact dropped route-side,
// mirroring the MCP layer).  NO tenors knob — linkers are specific-maturity
// bonds, not tenor-pillar swaps.

export type LinkerPanelDetailParams = {
  /** Earliest trade_date to include (inclusive, YYYY-MM-DD). */
  start_date: string;
  /** Latest trade_date (inclusive).  Omit → latest in the DB. */
  end_date?: string;
  /** Subset of USD_TIPS | GBP_LINKER | EUR_FR_LINKER | CAD_RRB.
   *  Omit → full universe. */
  curve_families?: ReadonlyArray<string>;
  /** Bloomberg field mnemonic.  Omit → YAML default ('YLD_YTM_MID'). */
  field_name?: string;
  /** 'business_days' | 'instrument_native'.  Omit → YAML default. */
  calendar_policy?: string;
  /** 'raise' | 'forward_fill_only' | 'drop_rows_any_missing'.  Omit → YAML. */
  missing_data_policy?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailLinkerPanel(
  params: LinkerPanelDetailParams,
): Promise<BuildLinkerPanelOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/linker-panel${buildQuery(params)}`);
}

// ---------------------------------------------------------------------------
// /detail/zcis-panel — panel-builder standalone bridge
// ---------------------------------------------------------------------------
// Per docs_revamped/03_standards/methodology_exposure.md §5.  Consumed by
// BOTH the extended and compact Build views (rendering_density dual-view).
// ``curve_families`` / ``tenors`` render as repeated query params via
// buildQuery.  The wire carries the panel CONTRACT only (Panel artifact
// dropped route-side, mirroring the MCP layer).

export type ZcisPanelDetailParams = {
  /** Earliest trade_date to include (inclusive, YYYY-MM-DD). */
  start_date: string;
  /** Latest trade_date (inclusive).  Omit → latest in the DB. */
  end_date?: string;
  /** Subset of USD_ZCIS | EUR_ZCIS | GBP_ZCIS.  Omit → full universe. */
  curve_families?: ReadonlyArray<string>;
  /** Tenor pillars (e.g. ['1Y','5Y','10Y']).  Omit → every tenor present.
   *  Tenors absent on a family are silently dropped backend-side; the wire
   *  echoes the resolved set. */
  tenors?: ReadonlyArray<string>;
  /** Bloomberg field mnemonic.  Omit → YAML default ('PX_MID'). */
  field_name?: string;
  /** 'business_days' | 'instrument_native'.  Omit → YAML default. */
  calendar_policy?: string;
  /** 'raise' | 'forward_fill_only' | 'drop_rows_any_missing'.  Omit → YAML. */
  missing_data_policy?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailZcisPanel(
  params: ZcisPanelDetailParams,
): Promise<BuildZcisPanelOutput> {
  return fetchJSON(`${RATES_PREFIX}/detail/zcis-panel${buildQuery(params)}`);
}

// ---------------------------------------------------------------------------
// /detail/policy-futures-strip-panel — panel-builder standalone bridge
// ---------------------------------------------------------------------------
// Per docs_revamped/03_standards/methodology_exposure.md §5.  Consumed by
// BOTH the extended and compact Build views (rendering_density dual-view).
// ``curve_families`` / ``strip_positions`` render as repeated query params
// via buildQuery.  The wire carries the panel CONTRACT only (Panel artifact
// dropped route-side, mirroring the MCP layer).

export type PolicyFuturesStripPanelDetailParams = {
  /** Earliest trade_date to include (inclusive, YYYY-MM-DD). */
  start_date: string;
  /** Latest trade_date (inclusive).  Omit → latest in the DB. */
  end_date?: string;
  /** Subset of SOFR_FUT | SONIA_FUT | EUR_SHORT_RATE_FUT.
   *  Omit → full universe. */
  curve_families?: ReadonlyArray<string>;
  /** Strip positions 1..8.  Omit → the full strip. */
  strip_positions?: ReadonlyArray<number>;
  /** Bloomberg field mnemonic.  Omit → YAML default ('PX_LAST'). */
  field_name?: string;
  /** 'business_days' | 'instrument_native'.  Omit → YAML default. */
  calendar_policy?: string;
  /** 'raise' | 'forward_fill_only' | 'drop_rows_any_missing'.  Omit → YAML. */
  missing_data_policy?: string;
  /** As-of trade date (YYYY-MM-DD).  Omit/empty → latest live data. */
  as_of_date?: string;
};

export function fetchDetailPolicyFuturesStripPanel(
  params: PolicyFuturesStripPanelDetailParams,
): Promise<BuildPolicyFuturesStripPanelOutput> {
  return fetchJSON(
    `${RATES_PREFIX}/detail/policy-futures-strip-panel${buildQuery(params)}`,
  );
}
