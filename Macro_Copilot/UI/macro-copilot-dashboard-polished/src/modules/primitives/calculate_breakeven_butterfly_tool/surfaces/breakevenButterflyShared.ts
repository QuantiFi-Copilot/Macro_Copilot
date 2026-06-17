// ============================================================================
// breakevenButterflyShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for
// ``calculate_breakeven_butterfly_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of breakeven_inflation_simple_tool's
// breakevenShared.ts).  This module knows what a "breakeven butterfly"
// means — specifically that POSITIVE = belly CHEAP vs the half-weighted
// wings, NEGATIVE = belly RICH, and that this is curvature of INFLATION
// COMPENSATION (not pure expected-inflation curvature); the shared shells
// do not.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailBreakevenButterfly,
  type BreakevenButterflyDetailParams,
} from '@/services/ratesApi';
import type { BreakevenButterflyOutput } from '@/types/rates';
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
// Country-pair metadata.  A bond-implied breakeven butterfly is a same-
// country object (3-point curvature on a single nominal/linker pair).
// Keyed by the LINKER curve_family because the linker uniquely determines
// the valid nominal counterparty.  Mirrors breakevenShared's PAIR_BY_LINKER.
// ---------------------------------------------------------------------------

export interface BreakevenButterflyPairMeta {
  /** Linker curve_family (e.g. 'USD_TIPS'). */
  linkerFamily: string;
  /** Nominal sovereign curve_family (e.g. 'UST'). */
  nominalFamily: string;
  /** Country label (e.g. 'US'). */
  country: string;
  /** Short nominal label for the pair chip (e.g. 'UST'). */
  nominalShort: string;
  /** Short linker label for the pair chip (e.g. 'TIPS'). */
  linkerShort: string;
}

const PAIR_BY_LINKER: Record<string, BreakevenButterflyPairMeta> = {
  USD_TIPS: {
    linkerFamily: 'USD_TIPS',
    nominalFamily: 'UST',
    country: 'US',
    nominalShort: 'UST',
    linkerShort: 'TIPS',
  },
  GBP_LINKER: {
    linkerFamily: 'GBP_LINKER',
    nominalFamily: 'UK_GILT',
    country: 'UK',
    nominalShort: 'Gilt',
    linkerShort: 'Linker',
  },
  EUR_FR_LINKER: {
    linkerFamily: 'EUR_FR_LINKER',
    nominalFamily: 'FR_OAT',
    country: 'France',
    nominalShort: 'OAT',
    linkerShort: 'OATei',
  },
  CAD_RRB: {
    linkerFamily: 'CAD_RRB',
    nominalFamily: 'CANADA_GOVT',
    country: 'Canada',
    nominalShort: 'Govt',
    linkerShort: 'RRB',
  },
};

/** Resolve the pair metadata from a linker curve_family.  Returns null
 *  for an unknown family (caller renders a neutral fallback). */
export function pairForLinker(
  linkerFamily: string,
): BreakevenButterflyPairMeta | null {
  return PAIR_BY_LINKER[linkerFamily] ?? null;
}

/** The single "COUNTRY PAIR" dropdown options — keyed by linker family
 *  because the linker determines the valid nominal counterparty (a
 *  breakeven butterfly is a same-country object by construction). */
export const BREAKEVEN_BUTTERFLY_PAIR_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(PAIR_BY_LINKER).map((p) => ({
  value: p.linkerFamily,
  label: `${p.country} · ${p.nominalShort} / ${p.linkerShort}`,
}));

/** Tenor triplet presets per pair (intersection of the nominal + linker
 *  grids).  Mockup default is 2s5s10s; other common presets per pair are
 *  surfaced so the desk can flip without retyping. */
export interface ButterflyTriplet {
  short: string;
  belly: string;
  long: string;
  label: string; // e.g. "2s5s10s"
}

export const BREAKEVEN_BUTTERFLY_TRIPLETS_BY_PAIR: Record<
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

/** Format the displayed triplet label, e.g. "2s5s10s". */
export function tripletLabel(
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): string {
  const strip = (t: string) => t.replace(/Y$/i, '').replace(/M$/i, 'm');
  return `${strip(shortTenor)}s${strip(bellyTenor)}s${strip(longTenor)}s`;
}

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact footer + the extended methodology.  Different framing from
 *  the spot breakeven caveat: this is CURVATURE of inflation
 *  compensation, with risk + liquidity premia at EVERY endpoint. */
export const BREAKEVEN_BUTTERFLY_COMPACT_CAVEAT =
  'Inflation compensation × 3 legs. Risk premia & liquidity premia embedded at every tenor.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseBreakevenButterflyArgs {
  nominalCurveFamily: string;
  linkerCurveFamily: string;
  shortTenor: string;
  bellyTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseBreakevenButterflyResult {
  data: BreakevenButterflyOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useBreakevenButterfly(
  args: UseBreakevenButterflyArgs,
): UseBreakevenButterflyResult {
  const [data, setData] = useState<BreakevenButterflyOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: BreakevenButterflyDetailParams = {
    nominal_curve_family: args.nominalCurveFamily,
    linker_curve_family: args.linkerCurveFamily,
    short_tenor: args.shortTenor,
    belly_tenor: args.bellyTenor,
    long_tenor: args.longTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (
      !args.nominalCurveFamily
      || !args.linkerCurveFamily
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
    fetchDetailBreakevenButterfly(params)
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
    args.nominalCurveFamily,
    args.linkerCurveFamily,
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
// Sign-convention caption.  POSITIVE = belly CHEAP vs wings.  NEGATIVE =
// belly RICH.  Surfaced in the compact KPI caption and the top-right
// stretch card.
// ---------------------------------------------------------------------------

export function bellyRegimeCaption(butterflyBps: number | null | undefined): string {
  if (butterflyBps == null || Number.isNaN(butterflyBps)) return '—';
  if (butterflyBps > 0) return 'Belly Cheap';
  if (butterflyBps < 0) return 'Belly Rich';
  return 'Flat';
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. FLY            (signed bps, neutral, primary emphasis, belly caption)
 *    2. 1D CHANGE      (signed bps, toneForChange)
 *    3. Z-SCORE (252D) (signed value + regime caption, toneForZScore)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: BreakevenButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const sigma = approxSigmaForChange(cm);
  return [
    {
      label: 'FLY',
      value: signedFixed(cm.current_butterfly_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: bellyRegimeCaption(cm.current_butterfly_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      // Mockup Compact.png shows the daily change with a "(+0.24σ)" subtext
      // so the desk reads the move both in absolute bps AND in its own
      // rolling-σ units.  σ is reconstructed from |current_butterfly / z|
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
  cm: BreakevenButterflyOutput['current_metrics'],
): number | null {
  const z = cm.current_z_score;
  const v = cm.current_butterfly_bps;
  if (z == null || v == null || !Number.isFinite(z) || !Number.isFinite(v)) return null;
  if (Math.abs(z) < 1e-6) return null;
  const sigma = Math.abs(v / z);
  return Number.isFinite(sigma) && sigma > 0 ? sigma : null;
}

/** Z-score caption combining stretch regime + belly direction.  Extreme
 *  positive z = belly extreme-CHEAP; extreme negative z = belly extreme-
 *  RICH.  Maps onto the mockup's "Extreme Rich" caption at z=-2.13. */
export function zScoreCaptionForButterfly(z: number | null | undefined): string {
  const regime = regimeForZScore(z);
  if (z == null || Number.isNaN(z) || regime === 'Normal') return regime;
  const direction = z > 0 ? 'Cheap' : 'Rich';
  return `${regime} ${direction}`;
}

/** The extended view's FULL KPI strip (mockups/Extended.png — bps-scale
 *  9-cell strip + the three endpoint breakevens for the decomposition). */
export function extendedKPIs(
  data: BreakevenButterflyOutput,
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

/** The decomposition row — three endpoint breakevens + the two wing
 *  spreads, surfaced on the extended view so the desk can audit the
 *  butterfly construction without a second tool call. */
export function decompositionKPIs(
  data: BreakevenButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `SHORT BE (${cm.short_tenor})`,
      value: signedFixed(cm.short_breakeven_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: `BELLY BE (${cm.belly_tenor})`,
      value: signedFixed(cm.belly_breakeven_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: `LONG BE (${cm.long_tenor})`,
      value: signedFixed(cm.long_breakeven_bps, 1),
      unit: 'bp',
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

// Sanity bound for bond-implied breakeven butterflies (bps).  Defensive
// frontend safety net mirror of breakevenShared's sanity bound.
const BUTTERFLY_SANITY_MIN_BPS = -400;
const BUTTERFLY_SANITY_MAX_BPS = 400;

export function sanitiseButterflySeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < BUTTERFLY_SANITY_MIN_BPS
        || r.value > BUTTERFLY_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: BreakevenButterflyOutput,
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
  data: BreakevenButterflyOutput,
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
      `Breakeven butterfly is ${regime.toLowerCase()} ${direction} versus its trailing-year mean — ` +
      `the belly inflation compensation is ${direction === 'cheap' ? 'high' : 'low'} relative to a half-weighted wings average.  ` +
      `Sits in the ${bucket.toLowerCase()}-end of the 252d range.  ` +
      `Inflation compensation curvature, not pure expected-inflation curvature.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Breakeven butterfly is elevated ${direction} versus trailing-year history — ` +
      `belly inflation compensation is ${direction === 'cheap' ? 'rich-than-wings' : 'cheap-than-wings'} on a normalised basis.  ` +
      `Inflation compensation curvature, not pure expected inflation.`
    );
  }
  return (
    `Breakeven butterfly is within its trailing-year norm; ` +
    `no extreme richness or cheapness in the belly.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: BreakevenButterflyOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const linkerCaveat = countryCaveatFor(cm.linker_curve_family);
  const pair = pairForLinker(cm.linker_curve_family);
  return [
    {
      label: 'Construction',
      value:
        `butterfly = belly_be − 0.5 × (short_be + long_be), where each leg = ` +
        `nominal − linker real yield (${cm.nominal_curve_family} / ${cm.linker_curve_family})`,
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
      value: `${effectiveFieldName} (mid yield-to-maturity, all six underlying series)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the bps butterfly (YAML default — not exposed)`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile, bps)',
    },
    {
      label: 'Disclosure',
      value: cm.methodology_label || '—',
    },
    {
      label: 'Same-country pair',
      value: pair ? `${pair.country} (${pair.nominalShort} / ${pair.linkerShort})` : '—',
    },
    {
      label: 'Linker caveat',
      value: linkerCaveat ? linkerCaveat.caveat : '—',
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
