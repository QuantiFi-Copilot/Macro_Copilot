// ============================================================================
// inflationSwapButterflyShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``calculate_inflation_swap_butterfly_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "same-curve ZCIS
// butterfly" is — a 3-point curvature on a SINGLE ZCIS curve family (no
// cross-curve mixing — distinct from cross_market_inflation_swap_spread).
// Sign convention: POSITIVE = belly CHEAP versus the half-weighted wings;
// NEGATIVE = belly RICH.  The wire ships the butterfly + period changes +
// 252d range + wing spreads ALREADY IN BPS (the inflation_swaps domain BPS
// convention).  Per-leg endpoint ZCIS rates ship in PERCENT (the natural
// rate unit) — display layer keeps them in %.
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch
// the SAME typed-detail endpoint (per rendering_density.md §1.1 + §10 —
// both views consume the same bridge; the compact view just renders less
// of it).  KPI builders + formatting + tone logic + index-family caveat
// resolution live here in ONE place to prevent drift.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailInflationSwapButterfly,
  type InflationSwapButterflyDetailParams,
} from '@/services/ratesApi';
import type { InflationSwapButterflyOutput } from '@/types/rates';
import {
  bucketForPercentile,
  regimeForZScore,
  signedFixed,
  signedFixedWithUnit,
  toneForChange,
  toneForZScore,
  unsignedFixed,
  type KPIDescriptor,
  type MethodologyRow,
  type ReferenceBand,
  type ReferenceChip,
  type StretchContext,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// ZCIS curve-family metadata.  Same-curve butterflies are single-family
// objects; the registry keys off curve_family because that uniquely
// determines the inflation-index family / lag / flag the surfaces render.
// Mirrors the cross_market_inflation_swap_spread sibling's registry shape
// so the two inflation_swaps tools share a consistent identity vocabulary,
// but is owned per-tool (no cross-imports) per the dual-view contract.
// ---------------------------------------------------------------------------

export interface ZcisFamilyMeta {
  /** Curve family identifier (e.g. 'USD_ZCIS'). */
  family: string;
  /** Market short code (e.g. 'USD'). */
  marketShort: string;
  /** Short inflation-index label (e.g. 'CPI-U', 'HICPxT', 'RPI'). */
  indexShort: string;
  /** Country flag emoji for the identity chip + caveat footer. */
  flag: string;
}

const FAMILY_REGISTRY: Record<string, ZcisFamilyMeta> = {
  USD_ZCIS: {
    family: 'USD_ZCIS',
    marketShort: 'USD',
    indexShort: 'CPI-U',
    flag: '🇺🇸',
  },
  EUR_ZCIS: {
    family: 'EUR_ZCIS',
    marketShort: 'EUR',
    indexShort: 'HICPxT',
    flag: '🇪🇺',
  },
  GBP_ZCIS: {
    family: 'GBP_ZCIS',
    marketShort: 'GBP',
    indexShort: 'RPI',
    flag: '🇬🇧',
  },
};

/** Resolve the per-family meta.  Returns null for an unknown family
 *  (caller renders a neutral fallback). */
export function zcisFamilyFor(family: string): ZcisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The single "ZCIS Curve" dropdown options.  Single-curve primitive —
 *  no nominal-pair concept (distinct from cross_market_inflation_swap_spread
 *  which crosses two families).  One curve, one set of pillars. */
export const INFLATION_SWAP_BUTTERFLY_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.marketShort} · ${m.indexShort}`,
}));

/** Tenor triplet presets per curve_family — the desk-canonical (short, belly,
 *  long) tuples on the ingested ZCIS grid (1Y / 2Y / 3Y / 5Y / 10Y / 20Y /
 *  30Y on every family per playbooks/inflation_swaps.yml).  Surfacing only
 *  registered triplets makes invalid orderings unreachable.  The mockup
 *  defaults the USD canonical triplet to 2s5s10s (CPI-U term-structure
 *  curvature focus). */
export interface ZcisButterflyTriplet {
  short: string;
  belly: string;
  long: string;
  label: string; // e.g. "2s5s10s"
}

export const INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE: Record<
  string,
  ReadonlyArray<ZcisButterflyTriplet>
> = {
  USD_ZCIS: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '2Y', belly: '10Y', long: '30Y', label: '2s10s30s' },
    { short: '10Y', belly: '20Y', long: '30Y', label: '10s20s30s' },
  ],
  EUR_ZCIS: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '2Y', belly: '10Y', long: '30Y', label: '2s10s30s' },
  ],
  GBP_ZCIS: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '2Y', belly: '10Y', long: '30Y', label: '2s10s30s' },
  ],
};

/** Format the displayed triplet label, e.g. "2s5s10s" or "5s10s30s". */
export function tripletLabel(
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): string {
  const strip = (t: string) => t.replace(/Y$/i, '').replace(/M$/i, 'm');
  return `${strip(shortTenor)}s${strip(bellyTenor)}s${strip(longTenor)}s`;
}

/** Mockup-shape variant — hyphen-separated tenor years (e.g. "2-5-10") used
 *  in the identity row per ``mockups/Compact.png`` + ``mockups/Extended.png``. */
export function tripletHyphenLabel(
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): string {
  const strip = (t: string) => t.replace(/Y$/i, '').replace(/M$/i, 'm');
  return `${strip(shortTenor)}-${strip(bellyTenor)}-${strip(longTenor)}`;
}

/** The desk-canonical honesty caveat for this tool — surfaced in the compact
 *  footer + the extended methodology card.  ZCIS = zero-coupon inflation
 *  swap; the three families reference DIFFERENT inflation measures so a
 *  butterfly on one curve is a curvature of one index family, NOT fungible
 *  across families.  Sourced as the canonical compact one-liner. */
export const INFLATION_SWAP_BUTTERFLY_COMPACT_CAVEAT =
  'ZCIS = zero-coupon inflation swap. CPI-U vs HICPxT vs RPI are not fungible.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseInflationSwapButterflyArgs {
  curveFamily: string;
  shortTenor: string;
  bellyTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseInflationSwapButterflyResult {
  data: InflationSwapButterflyOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views + the Monitor widget.  Fetches
 *  the typed-detail endpoint; re-fetches when any input changes. */
export function useInflationSwapButterfly(
  args: UseInflationSwapButterflyArgs,
): UseInflationSwapButterflyResult {
  const [data, setData] = useState<InflationSwapButterflyOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: InflationSwapButterflyDetailParams = {
    curve_family: args.curveFamily,
    short_tenor: args.shortTenor,
    belly_tenor: args.bellyTenor,
    long_tenor: args.longTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (
      !args.curveFamily
      || !args.shortTenor
      || !args.bellyTenor
      || !args.longTenor
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailInflationSwapButterfly(params)
      .then((p) => {
        if (cancelled) return;
        setData(p);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setErrorMessage(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    args.curveFamily,
    args.shortTenor,
    args.bellyTenor,
    args.longTenor,
    args.lookbackDays,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Sign-convention caption.  POSITIVE = belly CHEAP vs wings.  NEGATIVE =
// belly RICH.  Surfaced in the compact KPI caption + the extended fly cell.
// ---------------------------------------------------------------------------

export function bellyRegimeCaption(
  butterflyBps: number | null | undefined,
): string {
  if (butterflyBps == null || Number.isNaN(butterflyBps)) return '—';
  if (butterflyBps > 0) return 'Belly Cheap';
  if (butterflyBps < 0) return 'Belly Rich';
  return 'Flat';
}

/** Z-score caption combining stretch regime + belly direction.  Extreme
 *  positive z = belly extreme-CHEAP; extreme negative z = belly extreme-
 *  RICH.  Mockup-faithful — at z=-0.86 the compact caption is "Neutral"
 *  (Normal regime); the directional suffix only attaches when the regime
 *  is Elevated / Extreme. */
export function zScoreCaptionForButterfly(
  z: number | null | undefined,
): string {
  const regime = regimeForZScore(z);
  if (z == null || Number.isNaN(z) || regime === 'Normal') {
    return regime === 'Normal' ? 'Neutral' : '—';
  }
  const direction = z > 0 ? 'Cheap' : 'Rich';
  return `${regime} ${direction}`;
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. FLY (BPS)        — signed bps, primary emphasis; subtext shows the
 *                          equivalent percent (mockup: "(-0.32%)" caption).
 *    2. 1D CHANGE        — signed bps, toneForChange; subtext shows the
 *                          equivalent percent (mockup: "(-0.53%)" caption).
 *    3. Z-SCORE (252D)   — signed value + regime+direction caption
 *                          (mockup: "Neutral" at z=-0.86).
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: InflationSwapButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  // Express the bps move as an approximate percent of CURRENT belly ZCIS
  // rate so the desk gets a sense of relative size — mockup shows
  // "(-0.32%)" subtext for a -3.2bp fly when the belly ZCIS sits ~1%.
  const flyPctOfBelly = bpsRelativeToBelly(
    cm.current_butterfly_bps,
    cm.belly_zcis_rate_pct,
  );
  const dailyPctOfBelly = bpsRelativeToBelly(
    cm.daily_change_bps,
    cm.belly_zcis_rate_pct,
  );
  return [
    {
      label: 'FLY (BPS)',
      value: signedFixed(cm.current_butterfly_bps, 1),
      unit: 'bp',
      tone: toneForFly(cm.current_butterfly_bps),
      emphasis: 'primary',
      subtext:
        flyPctOfBelly != null ? `(${signedFixed(flyPctOfBelly, 2)}%)` : undefined,
      caption: bellyRegimeCaption(cm.current_butterfly_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      subtext:
        dailyPctOfBelly != null
          ? `(${signedFixed(dailyPctOfBelly, 2)}%)`
          : undefined,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zScoreCaptionForButterfly(cm.current_z_score),
    },
  ];
}

/** Belly-direction tone — POSITIVE bps (belly cheap) leans coral; NEGATIVE
 *  bps (belly rich) leans mint per the mockup's compact KPI colouring.  The
 *  fly cell carries 'neutral' tone when zero or unavailable. */
function toneForFly(bps: number | null | undefined): KPIDescriptor['tone'] {
  if (bps == null || Number.isNaN(bps) || bps === 0) return 'neutral';
  return bps > 0 ? 'negative' : 'positive';
}

/** Compute bps move as percent of belly ZCIS rate, for the compact
 *  subtext.  bps / (belly_pct * 100) → percent.  Returns null when either
 *  input is missing or belly is zero. */
function bpsRelativeToBelly(
  bps: number | null | undefined,
  bellyPct: number | null | undefined,
): number | null {
  if (
    bps == null
    || bellyPct == null
    || !Number.isFinite(bps)
    || !Number.isFinite(bellyPct)
    || bellyPct === 0
  ) {
    return null;
  }
  return bps / (bellyPct * 100);
}

/** The extended view's FULL KPI strip (mockups/Extended.png).  All butterfly
 *  / change / range fields are already BPS from the backend per the
 *  inflation_swaps domain BPS convention — no unit conversion needed. */
export function extendedKPIs(
  data: InflationSwapButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'BUTTERFLY',
      value: signedFixed(cm.current_butterfly_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      caption: bellyRegimeCaption(cm.current_butterfly_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: '5D CHANGE',
      value: signedFixed(cm.weekly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.weekly_change_bps),
    },
    {
      label: '1M CHANGE',
      value: signedFixed(cm.monthly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.monthly_change_bps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zScoreCaptionForButterfly(cm.current_z_score),
    },
    {
      label: 'PERCENTILE (252D)',
      value:
        cm.percentile_252d != null
          ? `${Math.round(cm.percentile_252d)}`
          : '—',
      unit: 'th',
      caption: bucketForPercentile(cm.percentile_252d),
    },
    {
      label: '252D HIGH',
      value: signedFixed(cm.high_252d_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: `${cm.observation_count}`,
      tone: 'neutral',
    },
  ];
}

/** Decomposition row — three endpoint ZCIS rates (PERCENT) + two wing
 *  spreads (BPS) so the desk can audit the butterfly construction without a
 *  second tool call.  Mirrors the mockup's "ZCIS 2-5-10 DECOMPOSITION" row. */
export function decompositionKPIs(
  data: InflationSwapButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `SHORT ZCIS (${cm.short_tenor})`,
      value: signedFixed(cm.short_zcis_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `BELLY ZCIS (${cm.belly_tenor})`,
      value: signedFixed(cm.belly_zcis_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `LONG ZCIS (${cm.long_tenor})`,
      value: signedFixed(cm.long_zcis_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'WING SHORT (belly − short)',
      value: signedFixed(cm.wing_short_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: 'WING LONG (long − belly)',
      value: signedFixed(cm.wing_long_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the butterfly (bps).
// ---------------------------------------------------------------------------

// Sanity bounds for ZCIS butterflies (bps).  Across the playbook universe
// ZCIS butterflies cluster around zero with stress excursions to ±200 bps
// or so.  Anything well outside is almost certainly a generic-ticker roll
// artifact on one leg.  Mirrors the realYield / breakeven sanity bounds.
const ZCIS_BUTTERFLY_SANITY_MIN_BPS = -400;
const ZCIS_BUTTERFLY_SANITY_MAX_BPS = 400;

/** Sanity-bound the canonical ZCIS butterfly series rows for the chart layer.
 *  Values already arrive in bps; clamp outliers to null. */
export function sanitiseButterflySeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < ZCIS_BUTTERFLY_SANITY_MIN_BPS
        || r.value > ZCIS_BUTTERFLY_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: InflationSwapButterflyOutput,
): ReadonlyArray<ReferenceBand> {
  const rawRows = data.time_series_butterfly?.rows ?? [];
  const sanitised = sanitiseButterflySeries(rawRows);
  const values = sanitised
    .map((r) => r.value)
    .filter((v): v is number => v != null && !Number.isNaN(v));
  if (values.length < 10) return [];

  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance =
    values.reduce((acc, v) => acc + (v - mean) ** 2, 0) / (values.length - 1);
  const std = Math.sqrt(variance);
  if (!Number.isFinite(std) || std === 0) return [];

  return [
    { value: mean + 2 * std, label: '+2σ', tone: 'extreme', style: 'dashed' },
    { value: mean + 1.5 * std, label: '+1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean - 1.5 * std, label: '-1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean - 2 * std, label: '-2σ', tone: 'positive', style: 'dashed' },
  ];
}

// ---------------------------------------------------------------------------
// Stretch-context builder
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: InflationSwapButterflyOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.current_z_score,
    bucket,
    cm.curve_family,
    cm.inflation_index_family,
  );

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.current_z_score != null
        ? {
            value: cm.current_z_score,
            regime,
            bands: { amber: 1.5, coral: 2.0 },
          }
        : undefined,
    interpretation: interp,
  };
}

function interpretationFor(
  regime: 'Normal' | 'Elevated' | 'Extreme',
  z: number | null | undefined,
  bucket: 'Low' | 'Normal' | 'High',
  curveFamily: string,
  indexFamily: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'cheap' : 'rich';
  const meta = zcisFamilyFor(curveFamily);
  const family = meta ? `${meta.marketShort} ${meta.indexShort}` : curveFamily;
  const indexLine = indexFamily ? ` Curvature of the ${indexFamily} ZCIS term structure — read it as ${meta?.indexShort ?? indexFamily} expectations, not a generic "inflation" curvature.` : '';
  if (regime === 'Extreme') {
    return (
      `${family} ZCIS butterfly is extreme ${direction} versus its trailing-year mean — `
      + `the belly ZCIS rate is ${direction === 'cheap' ? 'high' : 'low'} relative to the half-weighted wings.  `
      + `Sits in the ${bucket.toLowerCase()}-end of the 252d range.`
      + indexLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${family} ZCIS butterfly is elevated ${direction} versus trailing-year history — `
      + `belly is ${direction === 'cheap' ? 'cheaper' : 'richer'} than the half-weighted wings on a normalised basis.`
      + indexLine
    );
  }
  return (
    `${family} ZCIS butterfly is within its trailing-year norm; `
    + `no extreme richness or cheapness in the belly.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: InflationSwapButterflyOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = zcisFamilyFor(cm.curve_family);
  return [
    {
      label: 'Construction',
      value:
        `butterfly_bps = (belly_zcis − 0.5 × (short_zcis + long_zcis)) × 100, single curve `
        + `(${cm.curve_family}); raw ZCIS par-rate space — no basis subtraction, no IRP adjustment, no fitted curve`,
    },
    {
      label: 'Sign convention',
      value: 'POSITIVE = belly CHEAP vs half-weighted wings · NEGATIVE = belly RICH',
    },
    {
      label: 'Tenors',
      value: `${cm.short_tenor} / ${cm.belly_tenor} / ${cm.long_tenor} (${cm.short_years.toFixed(2)}y / ${cm.belly_years.toFixed(2)}y / ${cm.long_years.toFixed(2)}y)`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (ZCIS quoted rate, all three endpoint series)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the bps butterfly series (YAML-locked — no input-layer override on this primitive)`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile, bps)',
    },
    {
      label: 'Window',
      value: `${effectiveLookbackDays}d displayed history (lookback_days)`,
    },
    {
      label: 'Inflation index family',
      value: `${cm.inflation_index_family}${meta ? ` (${meta.indexShort} — ${meta.marketShort})` : ''}`,
    },
    {
      label: 'Index lag · interpolation',
      value: `${cm.index_lag} · ${cm.interpolation}`,
    },
    {
      label: 'Underlying index',
      value: cm.underlying_index ?? '—',
    },
    {
      label: 'Same-curve invariant',
      value: 'All three legs share inflation_index_family / index_lag / interpolation / underlying_index — cross-curve butterflies forbidden at the input layer.',
    },
    {
      label: 'Disclosure',
      value: cm.methodology_label || '—',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.22' },
    { label: 'BLS CPI-U' },
    { label: 'Eurostat HICP' },
    { label: 'UK ONS RPI' },
    { label: 'Bloomberg ZCIS' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
