// ============================================================================
// butterflyShared.ts — Per-tool helpers shared between BuildExtended.tsx and
// BuildCompact.tsx for ``calculate_butterfly_tool`` (sovereign butterfly).
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "same-curve
// sovereign butterfly" is — a 3-point curvature on a SINGLE sovereign yield
// curve (e.g. UST 2-5-10) with fixed (-1, +2, -1) weights.
// Sign convention: POSITIVE = belly CHEAP versus the linearly interpolated
// wings; NEGATIVE = belly RICH.  The wire ships the butterfly + 1d change +
// 252d range + wing spreads ALREADY IN BPS.  Per-leg endpoint yields ship in
// PERCENT (the natural unit for a sovereign yield level).
//
// Both Build surfaces fetch the SAME typed-detail endpoint
// ``/api/v1/rates/detail/butterfly`` (per rendering_density.md §1.1 + §10 —
// both views consume the same bridge; the compact view just renders less of
// it).  KPI builders + formatting + tone logic + the sovereign-vs-OIS caveat
// live here in ONE place to prevent drift across surfaces.
//
// NB on backend Output shape — distinct from the linker / ZCIS butterfly
// siblings the backend Output does NOT carry ``methodology_label``,
// ``weekly_change_bps`` / ``monthly_change_bps``, ``observation_count``, or
// ``short_tenor`` / ``belly_tenor`` / ``long_tenor`` strings (only the
// combined ``butterfly_label``).  Trailing 5d / 1m / observation_count are
// re-derived CLIENT-SIDE from ``time_series_butterfly.rows`` so the mockup's
// extended KPI strip is honoured without inventing wire data.  The
// methodology card sources the canonical sovereign caveat from this file
// with a ``TODO(PR10)`` marker for when the backend ships
// ``methodology_label`` (then the "Disclosure" row switches to consume the
// wire — one-line edit).
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailButterfly,
  type ButterflyDetailParams,
} from '@/services/ratesApi';
import type { ButterflyOutput } from '@/types/rates';
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
// Sovereign curve-family metadata.  Same-curve butterflies are single-family
// objects; the registry keys off curve_family because that uniquely
// determines the sovereign identity (UST / Bund / Gilt / JGB / OAT / BTP /
// Bono / AUS / CAN).  Mirrors the registry in the curve_spread sibling
// (kept in lockstep so a new sovereign curve is a one-line addition in BOTH
// places).
// ---------------------------------------------------------------------------

export interface SovereignFamilyMeta {
  /** Curve family identifier (e.g. 'UST'). */
  family: string;
  /** Short market label used in identity chips (e.g. 'UST'). */
  shortLabel: string;
  /** Full descriptive name (e.g. 'US Treasuries'). */
  longLabel: string;
  /** Country flag emoji. */
  flag: string;
}

const FAMILY_REGISTRY: Record<string, SovereignFamilyMeta> = {
  UST: { family: 'UST', shortLabel: 'UST', longLabel: 'US Treasuries', flag: '🇺🇸' },
  DE_BUND: { family: 'DE_BUND', shortLabel: 'Bund', longLabel: 'German Bunds', flag: '🇩🇪' },
  UK_GILT: { family: 'UK_GILT', shortLabel: 'Gilt', longLabel: 'UK Gilts', flag: '🇬🇧' },
  JGB: { family: 'JGB', shortLabel: 'JGB', longLabel: 'Japan Government Bonds', flag: '🇯🇵' },
  FR_OAT: { family: 'FR_OAT', shortLabel: 'OAT', longLabel: 'French OATs', flag: '🇫🇷' },
  IT_BTP: { family: 'IT_BTP', shortLabel: 'BTP', longLabel: 'Italian BTPs', flag: '🇮🇹' },
  ES_BONO: { family: 'ES_BONO', shortLabel: 'Bono', longLabel: 'Spanish Bonos', flag: '🇪🇸' },
  AU_GOVT: { family: 'AU_GOVT', shortLabel: 'AUS', longLabel: 'Australian Govt Bonds', flag: '🇦🇺' },
  CANADA_GOVT: { family: 'CANADA_GOVT', shortLabel: 'CAN', longLabel: 'Canadian Govt Bonds', flag: '🇨🇦' },
};

export function sovereignFamilyFor(family: string): SovereignFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The single "Sovereign Curve" dropdown options.  Single-curve primitive
 *  — no nominal-pair concept (distinct from
 *  ``calculate_cross_market_spread_tool`` which crosses two sovereign
 *  families). */
export const BUTTERFLY_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.shortLabel} · ${m.longLabel}`,
}));

/** Tenor triplet presets per curve_family — the desk-canonical (short,
 *  belly, long) tuples on the ingested sovereign pillar grid.  Surfacing
 *  only registered triplets makes invalid orderings unreachable.  Mockup
 *  defaults UST to the 2s5s10s canonical curvature focus.  Other families
 *  follow the standard 2/5/10 + 5/10/30 + 2/10/30 menu where the underlying
 *  pillars are ingested. */
export interface ButterflyTriplet {
  short: string;
  belly: string;
  long: string;
  label: string; // e.g. "2s5s10s"
}

export const BUTTERFLY_TRIPLETS_BY_CURVE: Record<
  string,
  ReadonlyArray<ButterflyTriplet>
> = {
  UST: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '2Y', belly: '10Y', long: '30Y', label: '2s10s30s' },
    { short: '2Y', belly: '5Y', long: '30Y', label: '2s5s30s' },
  ],
  DE_BUND: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '2Y', belly: '10Y', long: '30Y', label: '2s10s30s' },
  ],
  UK_GILT: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '2Y', belly: '10Y', long: '30Y', label: '2s10s30s' },
  ],
  JGB: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '20Y', label: '5s10s20s' },
    { short: '10Y', belly: '20Y', long: '30Y', label: '10s20s30s' },
  ],
  FR_OAT: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
  ],
  IT_BTP: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
  ],
  ES_BONO: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
  ],
  AU_GOVT: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
  ],
  CANADA_GOVT: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
  ],
};

/** Hyphen-separated tenor label (e.g. "2-5-10") used in the identity rows
 *  per ``mockups/Compact.png`` + ``mockups/Extended.png``.  Pure-year
 *  tenors strip the 'Y'; sub-year tenors retain their unit. */
export function tripletHyphenLabel(
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): string {
  const pureYear =
    shortTenor.endsWith('Y')
    && bellyTenor.endsWith('Y')
    && longTenor.endsWith('Y');
  if (pureYear) {
    const strip = (t: string) => t.replace(/Y$/i, '');
    return `${strip(shortTenor)}-${strip(bellyTenor)}-${strip(longTenor)}`;
  }
  return `${shortTenor}-${bellyTenor}-${longTenor}`;
}

/** Compact "2s5s10s" form used to derive a butterfly_label when the wire
 *  does not echo the canonical short form. */
export function tripletShortLabel(
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): string {
  const strip = (t: string) => t.replace(/Y$/i, '').replace(/M$/i, 'm');
  return `${strip(shortTenor)}s${strip(bellyTenor)}s${strip(longTenor)}s`;
}

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact footer + the extended methodology card.  Sovereign curves
 *  carry a sovereign-vs-OIS basis (FRA-OIS, asset-swap, repo specialness)
 *  that an OIS butterfly does not; the canonical cross-check is the OIS
 *  butterfly on the same triplet to isolate the swap-spread component.
 *  Mockup-faithful one-liner.
 *
 *  TODO(PR10): when the backend ships ``current_metrics.methodology_label``
 *  (the sub-domain has not caught up to PR10 yet — siblings in
 *  inflation_indexed_bonds / inflation_swaps already carry it), switch the
 *  methodology card's "Disclosure" row to source from the wire.  One-line
 *  edit. */
export const SOVEREIGN_BUTTERFLY_COMPACT_CAVEAT =
  'Sovereign fly; cross-check OIS fly for swap-spread component.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseButterflyArgs {
  curveFamily: string;
  shortTenor: string;
  bellyTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseButterflyResult {
  data: ButterflyOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the OIS butterfly
 *  hook shape so the per-tool surfaces look the same shape file-for-file. */
export function useButterfly(args: UseButterflyArgs): UseButterflyResult {
  const [data, setData] = useState<ButterflyOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: ButterflyDetailParams = {
    curve_family: args.curveFamily,
    short_tenor: args.shortTenor || undefined,
    belly_tenor: args.bellyTenor || undefined,
    long_tenor: args.longTenor || undefined,
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
    fetchDetailButterfly(params)
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
// Sign-convention captions.  POSITIVE = belly CHEAP vs wings.  NEGATIVE =
// belly RICH.  Surfaced in the compact + extended KPI captions.
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
 *  RICH.  Mockup-faithful — at z=+1.84 the compact caption is
 *  "Elevated Cheap". */
export function zScoreCaptionForButterfly(
  z: number | null | undefined,
): string {
  const regime = regimeForZScore(z);
  if (z == null || Number.isNaN(z)) return '—';
  if (regime === 'Normal') return 'Neutral';
  const direction = z > 0 ? 'Cheap' : 'Rich';
  return `${regime} ${direction}`;
}

// ---------------------------------------------------------------------------
// Wire-derived helpers — 5d / 1m trailing change + observation count are
// computed CLIENT-SIDE from time_series because the sovereign butterfly
// Output is leaner than the linker / ZCIS butterfly siblings (no wire
// fields for these).  Mirrors the pattern from curveSpreadShared.
// ---------------------------------------------------------------------------

function butterflyValues(data: ButterflyOutput): number[] {
  const canonical = data.time_series_butterfly?.rows;
  if (canonical && canonical.length > 0) {
    return canonical
      .map((r) => r.value)
      .filter((v): v is number => v != null && !Number.isNaN(v));
  }
  return (data.time_series ?? [])
    .map((r) => r.butterfly_bps)
    .filter((v): v is number => v != null && !Number.isNaN(v));
}

function changeOverRows(
  data: ButterflyOutput,
  rowOffset: number,
): number | null {
  const values = butterflyValues(data);
  if (values.length <= rowOffset) return null;
  const latest = values[values.length - 1];
  const past = values[values.length - 1 - rowOffset];
  return latest - past;
}

/** 5-trading-day change in bps (mockup Extended.png "5D CHANGE"). */
export function change5dBps(data: ButterflyOutput): number | null {
  return changeOverRows(data, 5);
}

/** 1-month change in bps (~21 trading days, mockup "1M CHANGE"). */
export function change1mBps(data: ButterflyOutput): number | null {
  return changeOverRows(data, 21);
}

export function observationCount(data: ButterflyOutput): number {
  return butterflyValues(data).length;
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the butterfly (bps).
// ---------------------------------------------------------------------------

// Sanity bounds for sovereign butterflies (bps).  Across the playbook
// universe sovereign butterflies cluster within ±100 bps with stress
// excursions to ±200 bps.  Anything well outside is almost certainly a
// generic-ticker roll artifact on one leg.  Mirrors the OIS / linker
// sanity bounds.
const SOVEREIGN_BUTTERFLY_SANITY_MIN_BPS = -400;
const SOVEREIGN_BUTTERFLY_SANITY_MAX_BPS = 400;

/** Sanity-bound the canonical butterfly series rows for the chart layer.
 *  Values arrive in bps; clamp outliers to null so a stray bad observation
 *  doesn't blow up the y-axis. */
export function sanitiseButterflySeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < SOVEREIGN_BUTTERFLY_SANITY_MIN_BPS
        || r.value > SOVEREIGN_BUTTERFLY_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: ButterflyOutput,
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
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** Belly-direction tone — POSITIVE bps (belly cheap) leans coral; NEGATIVE
 *  bps (belly rich) leans mint per the mockup's compact KPI colouring. */
function toneForFly(bps: number | null | undefined): KPIDescriptor['tone'] {
  if (bps == null || Number.isNaN(bps) || bps === 0) return 'neutral';
  return bps > 0 ? 'negative' : 'positive';
}

/** Compute bps move as percent of belly yield, for the compact subtext.
 *  bps / (belly_pct * 100) → percent.  Returns null when either input is
 *  missing or belly is zero. */
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

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. FLY (bps)        — signed bps, primary emphasis; subtext shows
 *                          the equivalent percent of belly yield (mockup
 *                          omitted at +8.4 bp on a ~4% belly).
 *    2. 1D CHANGE        — signed bps, toneForChange; subtext shows the
 *                          equivalent percent of belly yield (mockup:
 *                          "(+0.23%)" at +1.9 bp).
 *    3. Z-SCORE (252D)   — signed value + regime+direction caption
 *                          (mockup: "Elevated Cheap" at z=+1.84).
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: ButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const dailyPctOfBelly = bpsRelativeToBelly(
    cm.daily_change_bps,
    cm.belly_tenor_yield,
  );
  return [
    {
      label: 'FLY (bps)',
      value: signedFixed(cm.current_butterfly_bps, 1),
      unit: 'bps',
      tone: toneForFly(cm.current_butterfly_bps),
      emphasis: 'primary',
      caption: bellyRegimeCaption(cm.current_butterfly_bps),
    },
    {
      label: '1D CHANGE (bps)',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bps',
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

/** The extended view's headline KPI strip (mockups/Extended.png).  The
 *  backend ships current butterfly + daily change + 252d high/low + wing
 *  spreads already in BPS — no unit conversion needed.  5D / 1M / OBS are
 *  re-derived client-side from time_series_butterfly because the wire is
 *  leaner than the linker / ZCIS butterfly siblings. */
export function extendedKPIs(
  data: ButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const c5d = change5dBps(data);
  const c1m = change1mBps(data);
  const obs = observationCount(data);
  return [
    {
      label: 'BUTTERFLY',
      value: signedFixed(cm.current_butterfly_bps, 1),
      unit: 'bps',
      tone: 'neutral',
      caption: bellyRegimeCaption(cm.current_butterfly_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: '5D CHANGE',
      value: signedFixed(c5d, 1),
      unit: 'bps',
      tone: toneForChange(c5d),
    },
    {
      label: '1M CHANGE',
      value: signedFixed(c1m, 1),
      unit: 'bps',
      tone: toneForChange(c1m),
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
      unit: 'bps',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_bps, 1),
      unit: 'bps',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: obs > 0 ? String(obs) : '—',
      tone: 'neutral',
    },
  ];
}

/** Decomposition row — three endpoint sovereign yields (PERCENT) + two wing
 *  spreads (BPS) so the desk can audit ``(2 × belly − short − long) × 100``
 *  on the same screen.  Mirrors the mockup's "BUTTERFLY DECOMPOSITION" row.
 *  Per-tenor labels are reconstructed from the request triplet (the
 *  backend Output does NOT carry separate ``short_tenor`` / ``belly_tenor``
 *  / ``long_tenor`` strings — only the combined ``butterfly_label``). */
export function decompositionKPIs(
  data: ButterflyOutput,
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `SHORT YLD (${shortTenor})`,
      value: signedFixed(cm.short_tenor_yield, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `BELLY YLD (${bellyTenor})`,
      value: signedFixed(cm.belly_tenor_yield, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `LONG YLD (${longTenor})`,
      value: signedFixed(cm.long_tenor_yield, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'WING SHORT (belly − short)',
      value: signedFixed(cm.wing_short_bps, 1),
      unit: 'bps',
      tone: 'neutral',
    },
    {
      label: 'WING LONG (long − belly)',
      value: signedFixed(cm.wing_long_bps, 1),
      unit: 'bps',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Stretch-context builder
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: ButterflyOutput,
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
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'cheap' : 'rich';
  const meta = sovereignFamilyFor(curveFamily);
  const family = meta ? meta.longLabel : curveFamily;
  const cavLine =
    ' Sovereign-curve curvature; cross-check the OIS butterfly on the same triplet to isolate the sovereign-vs-OIS swap-spread component.';
  if (regime === 'Extreme') {
    return (
      `${family} butterfly is extreme ${direction} versus its trailing-year mean — `
      + `the belly yield is ${direction === 'cheap' ? 'high' : 'low'} relative to the linearly interpolated wings.  `
      + `Sits in the ${bucket.toLowerCase()}-end of the 252d range.`
      + cavLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${family} butterfly is elevated ${direction} versus trailing-year history — `
      + `belly is ${direction === 'cheap' ? 'cheaper' : 'richer'} than the linearly interpolated wings on a normalised basis.`
      + cavLine
    );
  }
  return (
    `${family} butterfly is within its trailing-year norm; `
    + 'no extreme richness or cheapness in the belly.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + request context.
 *  Per the BUILD_GUIDE Stage-1 contract, the wire-honesty disclosure should
 *  flow from ``current_metrics.methodology_label``; the sovereign butterfly
 *  backend Output currently does NOT carry that field (the sub-domain has
 *  not caught up to PR10 yet — siblings in inflation_indexed_bonds /
 *  inflation_swaps already do).  Until the backend ships it, the
 *  "Disclosure" row sources from the per-tool canonical caveat below
 *  (one-line edit to switch when the wire lands the field). */
export function buildMethodologyRows(
  data: ButterflyOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = sovereignFamilyFor(cm.curve_family);
  return [
    {
      label: 'Construction',
      value:
        `butterfly_bps = (2 × belly_yield − short_yield − long_yield) × 100, single curve `
        + `(${cm.curve_family}); raw yield space — no basis subtraction, no convexity adjustment, no fitted curve`,
    },
    {
      label: 'Sign convention',
      value: 'POSITIVE = belly CHEAP vs linearly-interpolated wings · NEGATIVE = belly RICH',
    },
    {
      label: 'Triplet',
      value: `${shortTenor} / ${bellyTenor} / ${longTenor} (${cm.butterfly_label})`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (Bloomberg mid yield-to-maturity, all three endpoint series)`,
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
      label: 'Sovereign curve',
      value: meta
        ? `${meta.shortLabel} · ${meta.longLabel} (${cm.curve_family})`
        : cm.curve_family,
    },
    {
      label: 'Weighting',
      value: 'FIXED simple-butterfly (−1, +2, −1) on (short, belly, long) in raw yield space — a.k.a. "50-50 wings". NOT DV01-neutral / NOT PCA-neutral (separate primitives if/when desk demand justifies).',
    },
    {
      label: 'Same-curve invariant',
      value: 'All three legs share curve_family — cross-curve sovereign butterflies are forbidden at the input schema layer (would compose on calculate_cross_market_spread_tool).',
    },
    {
      label: 'Disclosure',
      value: SOVEREIGN_BUTTERFLY_COMPACT_CAVEAT,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.5' },
    { label: 'Fabozzi Bond Markets' },
    { label: 'Bloomberg YLD_YTM_MID' },
    { label: 'US TreasuryDirect' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
