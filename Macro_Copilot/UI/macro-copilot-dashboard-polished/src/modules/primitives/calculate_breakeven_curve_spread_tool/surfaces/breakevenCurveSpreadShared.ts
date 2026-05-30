// ============================================================================
// breakevenCurveSpreadShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for
// ``calculate_breakeven_curve_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of breakevenButterflyShared.ts +
// curveSpreadShared.ts).  This module knows what a "same-country breakeven
// curve spread" means — specifically that it is the term structure of
// INFLATION COMPENSATION (not pure expected-inflation term structure)
// between two breakeven tenors of the same nominal/linker pair (e.g.
// UST/USD_TIPS 2s10s breakeven, UK_GILT/GBP_LINKER 5s30s breakeven).
//
// POSITIVE spread = upward-sloping breakeven curve (long-end inflation
// compensation higher than short-end); NEGATIVE = inverted (front-end
// higher).  The shared shells stay finance-blind.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailBreakevenCurveSpread,
  type BreakevenCurveSpreadDetailParams,
} from '@/services/ratesApi';
import type { BreakevenCurveSpreadOutput } from '@/types/rates';
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
// Country-pair metadata.  A bond-implied breakeven curve spread is a same-
// country object (2-point tenor spread on a single nominal/linker pair).
// Keyed by the LINKER curve_family because the linker uniquely determines
// the valid nominal counterparty.  Mirrors breakevenShared's PAIR_BY_LINKER.
// ---------------------------------------------------------------------------

export interface BreakevenCurveSpreadPairMeta {
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

const PAIR_BY_LINKER: Record<string, BreakevenCurveSpreadPairMeta> = {
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
): BreakevenCurveSpreadPairMeta | null {
  return PAIR_BY_LINKER[linkerFamily] ?? null;
}

/** The single "COUNTRY PAIR" dropdown options — keyed by linker family
 *  because the linker determines the valid nominal counterparty (a
 *  breakeven curve spread is a same-country object by construction). */
export const BREAKEVEN_CURVE_SPREAD_PAIR_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(PAIR_BY_LINKER).map((p) => ({
  value: p.linkerFamily,
  label: `${p.country} · ${p.nominalShort} / ${p.linkerShort}`,
}));

/** Tenor sets per pair (intersection of the nominal + linker grids).
 *  Conservative: the tenors where BOTH legs realistically have data. */
export const BREAKEVEN_CURVE_SPREAD_TENORS_BY_PAIR: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  USD_TIPS: ['2Y', '5Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t })),
  GBP_LINKER: ['2Y', '5Y', '10Y', '15Y', '20Y', '30Y'].map((t) => ({ value: t, label: t })),
  EUR_FR_LINKER: ['2Y', '5Y', '10Y', '15Y'].map((t) => ({ value: t, label: t })),
  CAD_RRB: ['5Y', '10Y', '30Y'].map((t) => ({ value: t, label: t })),
};

/** Registered (short, long) tenor presets per pair surfaced as a single
 *  "spread pair" dropdown.  Pre-baking the canonical pairs (2s10s,
 *  5s30s, etc.) lets the user flip between desk-canonical reads without
 *  retyping; the Pydantic strict-ordering validator rejects any invalid
 *  pair if a caller deep-links one. */
export interface CurveSpreadPair {
  short: string;
  long: string;
  label: string; // e.g. "2s10s"
}

export const BREAKEVEN_CURVE_SPREAD_PAIRS_BY_PAIR: Record<
  string,
  ReadonlyArray<CurveSpreadPair>
> = {
  USD_TIPS: [
    { short: '2Y', long: '10Y', label: '2s10s' },
    { short: '5Y', long: '10Y', label: '5s10s' },
    { short: '5Y', long: '30Y', label: '5s30s' },
    { short: '10Y', long: '30Y', label: '10s30s' },
  ],
  GBP_LINKER: [
    { short: '2Y', long: '10Y', label: '2s10s' },
    { short: '5Y', long: '10Y', label: '5s10s' },
    { short: '5Y', long: '30Y', label: '5s30s' },
    { short: '10Y', long: '30Y', label: '10s30s' },
  ],
  EUR_FR_LINKER: [
    { short: '2Y', long: '10Y', label: '2s10s' },
    { short: '5Y', long: '10Y', label: '5s10s' },
    { short: '5Y', long: '15Y', label: '5s15s' },
  ],
  CAD_RRB: [
    { short: '5Y', long: '10Y', label: '5s10s' },
    { short: '5Y', long: '30Y', label: '5s30s' },
    { short: '10Y', long: '30Y', label: '10s30s' },
  ],
};

/** Minimal tenor → years parser (mirrors the backend's tenor_to_years for
 *  the common <n>W / <n>M / <n>Y forms).  Used CLIENT-SIDE to filter the
 *  long-tenor dropdown to strictly-longer tenors so the long > short
 *  validity rule is enforced at the input layer — the backend re-validates
 *  it regardless. */
export function tenorToYears(tenor: string): number {
  const m = /^(\d+(?:\.\d+)?)\s*([WMY])$/i.exec(tenor.trim());
  if (!m) return NaN;
  const n = parseFloat(m[1]);
  const unit = m[2].toUpperCase();
  if (unit === 'W') return n / 52;
  if (unit === 'M') return n / 12;
  return n;
}

/** Short label for a tenor pair, e.g. ('2Y','10Y') → '2s10s'. */
export function spreadShortLabel(shortTenor: string, longTenor: string): string {
  return `${shortTenor.replace(/Y$/i, '')}s${longTenor.replace(/Y$/i, '')}s`;
}

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact footer + the extended methodology.  Inflation-COMPENSATION
 *  term structure, NOT pure expected-inflation term structure; risk
 *  premia + liquidity premia inherited at BOTH endpoints. */
export const BREAKEVEN_CURVE_SPREAD_COMPACT_CAVEAT =
  'Inflation compensation × 2 legs. Risk premia & liquidity premia embedded at each tenor.';

// ---------------------------------------------------------------------------
// Sign-convention caption.  POSITIVE = upward-sloping breakeven curve
// (long-end inflation compensation higher than short-end / "Bear
// Steepening" on a directional read); NEGATIVE = inverted (front-end
// higher / "Bull Flattening").  The mockup labels the headline caption
// "Bull Flattening" / "Bull Steepening" / "Bear Flattening" / "Bear
// Steepening" — we surface the simpler steepening/flattening framing
// since the directional bull/bear interpretation depends on the move,
// not the level alone.
// ---------------------------------------------------------------------------

export function slopeRegimeCaption(spreadBps: number | null | undefined): string {
  if (spreadBps == null || Number.isNaN(spreadBps)) return '—';
  if (spreadBps > 0) return 'Upward Sloping';
  if (spreadBps < 0) return 'Inverted';
  return 'Flat';
}

/** Combined z-score caption: regime + slope direction.  Extreme positive
 *  z = curve extreme-STEEP vs trailing window; extreme negative z =
 *  extreme-FLAT / inverted vs trailing window. */
export function zScoreSlopeCaption(z: number | null | undefined): string {
  const regime = regimeForZScore(z);
  if (z == null || Number.isNaN(z) || regime === 'Normal') return regime;
  const direction = z > 0 ? 'Steep' : 'Flat';
  return `${regime} ${direction}`;
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseBreakevenCurveSpreadArgs {
  nominalCurveFamily: string;
  linkerCurveFamily: string;
  shortTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseBreakevenCurveSpreadResult {
  data: BreakevenCurveSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useBreakevenCurveSpread(
  args: UseBreakevenCurveSpreadArgs,
): UseBreakevenCurveSpreadResult {
  const [data, setData] = useState<BreakevenCurveSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: BreakevenCurveSpreadDetailParams = {
    nominal_curve_family: args.nominalCurveFamily,
    linker_curve_family: args.linkerCurveFamily,
    short_tenor: args.shortTenor,
    long_tenor: args.longTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (
      !args.nominalCurveFamily
      || !args.linkerCurveFamily
      || !args.shortTenor
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
    fetchDetailBreakevenCurveSpread(params)
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
    args.longTenor,
    args.lookbackDays,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (<pair>) (signed bps, neutral, primary emphasis, slope caption)
 *    2. 1D CHANGE       (signed bps, toneForChange, σ subtext)
 *    3. Z-SCORE (252D)  (signed value + regime caption, toneForZScore)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: BreakevenCurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const sigma = approxSigmaForChange(cm);
  const pairLabel = spreadShortLabel(cm.short_tenor, cm.long_tenor);
  return [
    {
      label: `SPREAD (${pairLabel})`,
      value: signedFixed(cm.current_spread_bps, 0),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: slopeRegimeCaption(cm.current_spread_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      // Mockup Compact.png shows the daily change with a "(-0.13σ)"
      // subtext so the desk reads the move both in absolute bps AND in
      // its own rolling-σ units.  σ is reconstructed from
      // |current_spread / z| (a coarse approximation; the backend
      // doesn't ship rolling std).
      subtext:
        sigma != null && cm.daily_change_bps != null
          ? `(${cm.daily_change_bps >= 0 ? '+' : ''}${(cm.daily_change_bps / sigma).toFixed(2)}σ)`
          : undefined,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zScoreSlopeCaption(cm.current_z_score),
    },
  ];
}

/** Local rolling-σ estimator for the 1D-change → σ caption.  The backend
 *  does not (yet) ship the rolling std directly; reconstruct it from
 *  ``|current_spread_bps - mean| = |z| × σ`` assuming mean ≈ 0.  Coarse,
 *  suitable only for a "size-of-move" subtext on the compact card. */
function approxSigmaForChange(
  cm: BreakevenCurveSpreadOutput['current_metrics'],
): number | null {
  const z = cm.current_z_score;
  const v = cm.current_spread_bps;
  if (z == null || v == null || !Number.isFinite(z) || !Number.isFinite(v)) return null;
  if (Math.abs(z) < 1e-6) return null;
  const sigma = Math.abs(v / z);
  return Number.isFinite(sigma) && sigma > 0 ? sigma : null;
}

/** The extended view's FULL KPI strip (mockups/Extended.png — bps-scale
 *  strip + z/percentile + 252d range, then the two endpoint breakevens
 *  for the decomposition). */
export function extendedKPIs(
  data: BreakevenCurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const pairLabel = spreadShortLabel(cm.short_tenor, cm.long_tenor);
  return [
    {
      label: `SPREAD (${pairLabel})`,
      value: signedFixed(cm.current_spread_bps, 0),
      unit: 'bp',
      tone: 'neutral',
      caption: slopeRegimeCaption(cm.current_spread_bps),
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
      caption: zScoreSlopeCaption(cm.current_z_score),
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
      value: signedFixed(cm.high_252d_bps, 0),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_bps, 0),
      unit: 'bp',
      tone: 'neutral',
    },
  ];
}

/** The decomposition row — short + long endpoint breakevens, surfaced on
 *  the extended view so the desk can audit the curve-spread construction
 *  without a second tool call (mockup Extended.png: "232 bp · 244 bp"). */
export function decompositionKPIs(
  data: BreakevenCurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `SHORT BE (${cm.short_tenor})`,
      value: signedFixed(cm.short_breakeven_bps, 0),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: `LONG BE (${cm.long_tenor})`,
      value: signedFixed(cm.long_breakeven_bps, 0),
      unit: 'bp',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

// Sanity bound for bond-implied breakeven curve spreads (bps).  Defensive
// frontend safety net — the curve spread sits well within ±500bps across
// the playbook universe; anything outside is a generic-roll artifact on
// one endpoint.
const SPREAD_SANITY_MIN_BPS = -500;
const SPREAD_SANITY_MAX_BPS = 500;

export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < SPREAD_SANITY_MIN_BPS
        || r.value > SPREAD_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: BreakevenCurveSpreadOutput,
): ReadonlyArray<ReferenceBand> {
  const rawRows = data.time_series_spread?.rows ?? [];
  const sanitised = sanitiseSpreadSeries(rawRows);
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
  data: BreakevenCurveSpreadOutput,
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
  const directionPhrase =
    z > 0
      ? 'a steeper breakeven curve than its trailing norm (long-end inflation compensation rich vs short-end)'
      : 'a flatter / more-inverted breakeven curve than its trailing norm';

  if (regime === 'Extreme') {
    return (
      `Breakeven curve spread is ${regime.toLowerCase()} ${z > 0 ? 'above' : 'below'} its trailing-year mean — ` +
      `${directionPhrase}.  The current observation sits in the ${bucket.toLowerCase()}-end of the ` +
      `252d range.  This is inflation-COMPENSATION curve shape, not pure expected-inflation shape.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Breakeven curve spread is elevated vs. trailing-year history — ${directionPhrase}.  ` +
      `Inflation-compensation curve shape.`
    );
  }
  return (
    `Breakeven curve spread is within its trailing-year norm; ` +
    `no extreme steepening / flattening stretch.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: BreakevenCurveSpreadOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const linkerCaveat = countryCaveatFor(cm.linker_curve_family);
  const pair = pairForLinker(cm.linker_curve_family);
  return [
    {
      label: 'Construction',
      value:
        `spread = long breakeven (${cm.long_tenor}) − short breakeven (${cm.short_tenor}), ` +
        `each leg = nominal yield − linker real yield on the ${cm.nominal_curve_family} / ${cm.linker_curve_family} pair`,
    },
    {
      label: 'Sign convention',
      value: 'POSITIVE = upward-sloping breakeven curve (long-end compensation higher) · NEGATIVE = inverted',
    },
    {
      label: 'Valid tenor pair',
      value: `long tenor (${cm.long_tenor}) > short tenor (${cm.short_tenor}) — enforced at the input + compute layers`,
    },
    {
      label: 'Tenors',
      value: `${cm.short_tenor} / ${cm.long_tenor} (${cm.short_years.toFixed(2)}y / ${cm.long_years.toFixed(2)}y)`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (mid yield-to-maturity, all four underlying series)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the bps spread (YAML default — not exposed)`,
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
    { label: 'Tuckman 4e Ch.8-10' },
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
