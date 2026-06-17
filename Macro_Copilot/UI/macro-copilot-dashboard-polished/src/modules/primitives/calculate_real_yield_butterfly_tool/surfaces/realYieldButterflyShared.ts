// ============================================================================
// realYieldButterflyShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``calculate_real_yield_butterfly_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "linker real-yield
// butterfly" is — a 3-point curvature on a SINGLE linker curve (no nominal
// pair).  Sign convention: POSITIVE = belly CHEAP vs the half-weighted
// wings, NEGATIVE = belly RICH.  The wire shape is in PERCENT (same units
// as the underlying real yields); desk-canonical display is in BPS so
// every public KPI helper here returns ``× 100``.  Daily / weekly / monthly
// changes are already BPS from the backend.
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch
// the SAME typed-detail endpoint (per rendering_density.md §1.1 + §10
// — both views consume the same bridge; the compact view just renders
// less of it).  KPI builders + formatting + tone logic live here in ONE
// place to prevent drift.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailRealYieldButterfly,
  type RealYieldButterflyDetailParams,
} from '@/services/ratesApi';
import type { RealYieldButterflyOutput } from '@/types/rates';
import {
  bucketForPercentile,
  countryCaveatFor,
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
// Curve-family metadata.  A linker real-yield butterfly is a single-curve
// object (3-point curvature on ONE linker curve_family).  Keyed by the
// curve_family because the curve uniquely determines country / currency /
// the desk caveat to surface.  Mirrors the sibling shape but does NOT carry
// a nominal counterparty (distinct from breakevenButterflyShared's
// PAIR_BY_LINKER).
// ---------------------------------------------------------------------------

export interface RealYieldButterflyCurveMeta {
  /** Linker curve_family code (e.g. 'USD_TIPS'). */
  curveFamily: string;
  /** Country label (e.g. 'US'). */
  country: string;
  /** Short curve label for the identity chip (e.g. 'TIPS', 'Linker',
   *  'OATei', 'RRB'). */
  shortLabel: string;
}

const CURVE_REGISTRY: Record<string, RealYieldButterflyCurveMeta> = {
  USD_TIPS: { curveFamily: 'USD_TIPS', country: 'US', shortLabel: 'TIPS' },
  GBP_LINKER: { curveFamily: 'GBP_LINKER', country: 'UK', shortLabel: 'Linker' },
  EUR_FR_LINKER: { curveFamily: 'EUR_FR_LINKER', country: 'France', shortLabel: 'OATei' },
  CAD_RRB: { curveFamily: 'CAD_RRB', country: 'Canada', shortLabel: 'RRB' },
};

/** Resolve the curve metadata from a linker curve_family.  Returns null
 *  for an unknown family (caller renders a neutral fallback). */
export function curveForFamily(
  curveFamily: string,
): RealYieldButterflyCurveMeta | null {
  return CURVE_REGISTRY[curveFamily] ?? null;
}

/** The single "LINKER CURVE" dropdown options.  Unlike the breakeven
 *  butterfly there is NO nominal-pair concept — one curve, one set of
 *  pillars. */
export const REAL_YIELD_BUTTERFLY_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(CURVE_REGISTRY).map((c) => ({
  value: c.curveFamily,
  label: `${c.country} · ${c.shortLabel}`,
}));

/** Tenor triplet presets per curve_family — the pillars where the linker
 *  series exists in the substrate (per playbooks/inflation_indexed_bonds.yml).
 *  USD_TIPS has no 2Y series so 2s5s10s is not implementable; the canonical
 *  US triplet is 5s10s30s.  Surfacing only registered triplets makes invalid
 *  orderings unreachable. */
export interface ButterflyTriplet {
  short: string;
  belly: string;
  long: string;
  label: string; // e.g. "5s10s30s"
}

export const REAL_YIELD_BUTTERFLY_TRIPLETS_BY_CURVE: Record<
  string,
  ReadonlyArray<ButterflyTriplet>
> = {
  USD_TIPS: [
    { short: '5Y', belly: '10Y', long: '20Y', label: '5s10s20s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '10Y', belly: '20Y', long: '30Y', label: '10s20s30s' },
  ],
  GBP_LINKER: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '2Y', belly: '10Y', long: '30Y', label: '2s10s30s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
  ],
  EUR_FR_LINKER: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '2Y', belly: '5Y', long: '15Y', label: '2s5s15s' },
    { short: '5Y', belly: '10Y', long: '15Y', label: '5s10s15s' },
  ],
  CAD_RRB: [
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
  ],
};

/** Format the displayed triplet label, e.g. "5s10s30s" or "5-10-30". */
export function tripletLabel(
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): string {
  const strip = (t: string) => t.replace(/Y$/i, '').replace(/M$/i, 'm');
  return `${strip(shortTenor)}s${strip(bellyTenor)}s${strip(longTenor)}s`;
}

/** Mockup-shape variant — hyphen-separated tenor years (e.g. "5-10-30")
 *  used in the identity chip per ``mockups/Compact.png`` + the Extended
 *  identity row. */
export function tripletHyphenLabel(
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): string {
  const strip = (t: string) => t.replace(/Y$/i, '').replace(/M$/i, 'm');
  return `${strip(shortTenor)}-${strip(bellyTenor)}-${strip(longTenor)}`;
}

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact footer + the extended methodology card.  Real yields are
 *  real rates; the CPI-indexation-lag on linkers (3-month for US TIPS;
 *  similar for other markets) means the realised real-yield reflects
 *  yesterday's inflation, not today's. */
export const REAL_YIELD_BUTTERFLY_COMPACT_CAVEAT =
  'CPI-lag applies to linkers. Real yields = real rates.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseRealYieldButterflyArgs {
  curveFamily: string;
  shortTenor: string;
  bellyTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseRealYieldButterflyResult {
  data: RealYieldButterflyOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views + the Monitor widget.  Fetches
 *  the typed-detail endpoint; re-fetches when any input changes. */
export function useRealYieldButterfly(
  args: UseRealYieldButterflyArgs,
): UseRealYieldButterflyResult {
  const [data, setData] = useState<RealYieldButterflyOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: RealYieldButterflyDetailParams = {
    curve_family: args.curveFamily,
    short_tenor: args.shortTenor,
    belly_tenor: args.bellyTenor,
    long_tenor: args.longTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
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
    fetchDetailRealYieldButterfly(params)
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
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Unit conversion: backend ships current_butterfly_pct + high/low/wing
// fields in PERCENT (same units as the underlying real yields).  Display
// layer multiplies by 100 for the bps presentation per desk convention.
// Daily / weekly / monthly changes are already BPS from the backend.
// ---------------------------------------------------------------------------

function pctToBps(pct: number | null | undefined): number | null {
  if (pct == null || Number.isNaN(pct)) return null;
  return pct * 100;
}

// ---------------------------------------------------------------------------
// Sign-convention caption.  POSITIVE = belly CHEAP vs wings.  NEGATIVE =
// belly RICH.  Surfaced in the compact KPI caption.
// ---------------------------------------------------------------------------

export function bellyRegimeCaption(butterflyPct: number | null | undefined): string {
  if (butterflyPct == null || Number.isNaN(butterflyPct)) return '—';
  if (butterflyPct > 0) return 'Belly Cheap';
  if (butterflyPct < 0) return 'Belly Rich';
  return 'Flat';
}

/** Z-score caption combining stretch regime + belly direction.  Extreme
 *  positive z = belly extreme-CHEAP; extreme negative z = belly extreme-
 *  RICH.  Maps onto the mockup's "Extreme Rich" caption at z=-2.07. */
export function zScoreCaptionForButterfly(z: number | null | undefined): string {
  const regime = regimeForZScore(z);
  if (z == null || Number.isNaN(z) || regime === 'Normal') return regime;
  const direction = z > 0 ? 'Cheap' : 'Rich';
  return `${regime} ${direction}`;
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. FLY (short-belly-long) — signed bps, primary emphasis, belly caption
 *    2. 1D CHANGE              — signed bps, toneForChange, (±Xσ) subtext
 *    3. Z-SCORE (252D)         — signed value + regime+direction caption
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: RealYieldButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const flyBps = pctToBps(cm.current_butterfly_pct);
  const sigma = approxSigmaForChange(cm);
  return [
    {
      label: `FLY (${tripletHyphenLabel(cm.short_tenor, cm.belly_tenor, cm.long_tenor)})`,
      value: signedFixed(flyBps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: bellyRegimeCaption(cm.current_butterfly_pct),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      // Mockup Compact.png shows the daily change with a "(-0.12σ)" subtext
      // so the desk reads the move both in bps AND in its own rolling-σ
      // units.  σ is reconstructed from |current_butterfly_bps / z|
      // (a coarse approximation; the backend doesn't ship rolling std).
      subtext:
        sigma != null && cm.daily_change_bps != null
          ? `(${cm.daily_change_bps >= 0 ? '+' : ''}${(cm.daily_change_bps / sigma).toFixed(2)}σ)`
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

/** Local rolling-σ estimator for the 1D-change → σ caption.  The backend
 *  does not (yet) ship the rolling std directly; reconstruct it from
 *  ``|current_butterfly_bps - mean| = |z| × σ`` assuming mean ≈ 0 (the
 *  butterfly sits close to zero across the history).  Coarse, suitable
 *  only for a "size-of-move" subtext on the compact card. */
function approxSigmaForChange(
  cm: RealYieldButterflyOutput['current_metrics'],
): number | null {
  const z = cm.current_z_score;
  const v = pctToBps(cm.current_butterfly_pct);
  if (z == null || v == null || !Number.isFinite(z) || !Number.isFinite(v)) return null;
  if (Math.abs(z) < 1e-6) return null;
  const sigma = Math.abs(v / z);
  return Number.isFinite(sigma) && sigma > 0 ? sigma : null;
}

/** The extended view's FULL KPI strip (mockups/Extended.png — bps-scale
 *  9-cell strip).  All percent-units fields converted to bps for the
 *  presentation; period changes and high/low already arrive in bps from
 *  the backend (or via pctToBps for the historical range). */
export function extendedKPIs(
  data: RealYieldButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const flyBps = pctToBps(cm.current_butterfly_pct);
  const highBps = pctToBps(cm.high_252d_pct);
  const lowBps = pctToBps(cm.low_252d_pct);
  return [
    {
      label: 'BUTTERFLY',
      value: signedFixed(flyBps, 1),
      unit: 'bp',
      tone: 'neutral',
      caption: bellyRegimeCaption(cm.current_butterfly_pct),
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
      value: signedFixed(highBps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(lowBps, 1),
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

/** The decomposition row — three endpoint real yields + the two wing
 *  spreads, surfaced on the extended view so the desk can audit the
 *  butterfly construction without a second tool call.  Endpoints are
 *  shown in PERCENT (the real-yield natural units); wing spreads in
 *  BPS per desk convention. */
export function decompositionKPIs(
  data: RealYieldButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `SHORT RY (${cm.short_tenor})`,
      value: signedFixed(cm.short_real_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `BELLY RY (${cm.belly_tenor})`,
      value: signedFixed(cm.belly_real_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `LONG RY (${cm.long_tenor})`,
      value: signedFixed(cm.long_real_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'WING SHORT (belly − short)',
      value: signedFixed(pctToBps(cm.wing_short_pct), 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: 'WING LONG (long − belly)',
      value: signedFixed(pctToBps(cm.wing_long_pct), 1),
      unit: 'bp',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the butterfly (bps).
// ---------------------------------------------------------------------------

// Sanity bound for linker real-yield butterflies (bps after pct→bps).
// Real yields sit roughly [-3%, +5%]; butterflies cluster around zero with
// stress excursions to ±100bps.  Anything well outside is almost certainly
// a generic-ticker roll artifact on one leg.  Defensive frontend safety
// net mirroring the breakevenButterflyShared sanity bound.
const BUTTERFLY_SANITY_MIN_BPS = -300;
const BUTTERFLY_SANITY_MAX_BPS = 300;

/** Convert the canonical TimeSeriesUnits.PERCENT butterfly series rows
 *  to bps for the chart layer (the shells render in whatever unit the
 *  caller declares via ``chartUnit='bp'``).  Sanity-bounds outliers to
 *  null so a stray bad observation doesn't blow up the y-axis. */
export function sanitiseButterflySeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => {
    const bps = r.value != null ? r.value * 100 : null;
    return {
      date: r.date,
      value:
        bps == null
          || Number.isNaN(bps)
          || bps < BUTTERFLY_SANITY_MIN_BPS
          || bps > BUTTERFLY_SANITY_MAX_BPS
          ? null
          : bps,
    };
  });
}

export function buildReferenceBands(
  data: RealYieldButterflyOutput,
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
  data: RealYieldButterflyOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(regime, cm.current_z_score, bucket);

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
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'cheap' : 'rich';
  if (regime === 'Extreme') {
    return (
      `Real-yield butterfly is ${regime.toLowerCase()} ${direction} versus its trailing-year mean — ` +
      `the belly real yield is ${direction === 'cheap' ? 'high' : 'low'} relative to a half-weighted wings average.  ` +
      `Sits in the ${bucket.toLowerCase()}-end of the 252d range.  ` +
      `Curvature of the real-yield curve — distinct from breakeven-curve or nominal-curve curvature.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Real-yield butterfly is elevated ${direction} versus trailing-year history — ` +
      `belly is ${direction === 'cheap' ? 'cheaper' : 'richer'} than the half-weighted wings on a normalised basis.  ` +
      `Curvature of real yields, not curvature of inflation compensation.`
    );
  }
  return (
    `Real-yield butterfly is within its trailing-year norm; ` +
    `no extreme richness or cheapness in the belly.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: RealYieldButterflyOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const curveCaveat = countryCaveatFor(cm.curve_family);
  const curve = curveForFamily(cm.curve_family);
  return [
    {
      label: 'Construction',
      value:
        `butterfly = belly_ry − 0.5 × (short_ry + long_ry), single linker curve `
        + `(${cm.curve_family}); reported in PERCENT on the wire, displayed in BPS`,
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
      value: `${effectiveFieldName} (mid real-yield-to-maturity, all three endpoint series)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the percent butterfly (YAML default — not exposed at the input layer)`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile, percent → bps for display)',
    },
    {
      label: 'Disclosure',
      value: cm.methodology_label || '—',
    },
    {
      label: 'Linker identity',
      value: curve ? `${curve.country} · ${cm.curve_family} (${curve.shortLabel})` : cm.curve_family,
    },
    {
      label: 'Linker caveat',
      value: curveCaveat ? curveCaveat.caveat : '—',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.22' },
    { label: 'US TreasuryDirect' },
    { label: 'UK DMO' },
    { label: 'Agence France Trésor' },
    { label: 'Bank of Canada' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
