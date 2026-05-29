// ============================================================================
// curveSpreadShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``calculate_real_yield_curve_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of get_real_yield_level_tool's
// realYieldShared.ts).  This module knows what a "real-yield curve spread"
// means — the term structure of REAL yields (long − short), reported in
// PERCENT (changes in bps).  The shared shells stay finance-blind.
//
// Both Build views fetch the SAME typed-detail endpoint; the compact view
// renders less.  Headline KPIs + formatting + tone logic live here once.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailRealYieldCurveSpread,
  type RealYieldCurveSpreadDetailParams,
} from '@/services/ratesApi';
import type { RealYieldCurveSpreadOutput } from '@/types/rates';
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
// Curve-family + tenor vocabularies (finance-aware; per-tool layer).
// ---------------------------------------------------------------------------

export const CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'USD_TIPS', label: 'USD_TIPS' },
  { value: 'GBP_LINKER', label: 'GBP_LINKER' },
  { value: 'EUR_FR_LINKER', label: 'EUR_FR_LINKER' },
  { value: 'CAD_RRB', label: 'CAD_RRB' },
];

export const TENOR_OPTIONS_BY_CURVE: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  USD_TIPS: ['5Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t })),
  GBP_LINKER: ['1Y', '2Y', '3Y', '5Y', '10Y', '15Y', '20Y', '30Y', '50Y'].map(
    (t) => ({ value: t, label: t }),
  ),
  EUR_FR_LINKER: ['2Y', '5Y', '7Y', '10Y', '15Y'].map((t) => ({ value: t, label: t })),
  CAD_RRB: ['5Y', '10Y', '15Y', '20Y', '25Y', '30Y'].map((t) => ({ value: t, label: t })),
};

/** Minimal tenor → years parser (mirrors the backend's tenor_to_years for
 *  the common <n>W / <n>M / <n>Y forms).  Used CLIENT-SIDE to filter the
 *  long-tenor dropdown to strictly-longer tenors so the "long > short"
 *  validity rule (mockup ExtendedCurveSpread) is enforced at the input
 *  layer — the backend re-validates it regardless. */
export function tenorToYears(tenor: string): number {
  const m = /^(\d+(?:\.\d+)?)\s*([WMY])$/i.exec(tenor.trim());
  if (!m) return NaN;
  const n = parseFloat(m[1]);
  const unit = m[2].toUpperCase();
  if (unit === 'W') return n / 52;
  if (unit === 'M') return n / 12;
  return n;
}

/** Short label for a tenor pair, e.g. ('5Y','10Y') → '5s10s'. */
export function spreadShortLabel(shortTenor: string, longTenor: string): string {
  return `${shortTenor.replace(/Y$/i, '')}s${longTenor.replace(/Y$/i, '')}s`;
}

/** The desk-canonical compact caveat for this tool (mockup CompactCurveSpread). */
export const CURVE_SPREAD_COMPACT_CAVEAT =
  'Real-rate curve shape. Long real yields vs short.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseCurveSpreadArgs {
  curveFamily: string;
  shortTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
  zScoreWindowDays?: number;
  zScoreMinPeriods?: number;
  zScoreDdof?: number;
}

export interface UseCurveSpreadResult {
  data: RealYieldCurveSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useRealYieldCurveSpread(
  args: UseCurveSpreadArgs,
): UseCurveSpreadResult {
  const [data, setData] = useState<RealYieldCurveSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: RealYieldCurveSpreadDetailParams = {
    curve_family: args.curveFamily,
    short_tenor: args.shortTenor,
    long_tenor: args.longTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    z_score_window_days: args.zScoreWindowDays,
    z_score_min_periods: args.zScoreMinPeriods,
    z_score_ddof: args.zScoreDdof,
  };

  useEffect(() => {
    if (!args.curveFamily || !args.shortTenor || !args.longTenor) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailRealYieldCurveSpread(params)
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
    args.longTenor,
    args.lookbackDays,
    args.fieldName,
    args.zScoreWindowDays,
    args.zScoreMinPeriods,
    args.zScoreDdof,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** Compact view's THREE canonical headline KPIs (mockups/Compact.png):
 *    1. SPREAD       (signed %, neutral, primary — the curve-shape value)
 *    2. 1D CHANGE    (signed bps, toneForChange)
 *    3. Z-SCORE      (signed value + regime, toneForZScore) */
export function compactKPIs(
  data: RealYieldCurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_pct, 2),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
  ];
}

/** Extended view's FULL KPI strip (mockups/Extended.png) — spread + changes
 *  + z + percentile + range + the two endpoint real yields + curve geometry. */
export function extendedKPIs(
  data: RealYieldCurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_pct, 2),
      unit: '%',
      tone: 'neutral',
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
      caption: regimeForZScore(cm.current_z_score),
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
      value: signedFixed(cm.high_252d_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'SHORT REAL YIELD',
      value: signedFixed(cm.short_real_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
      caption: cm.short_tenor,
    },
    {
      label: 'LONG REAL YIELD',
      value: signedFixed(cm.long_real_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
      caption: cm.long_tenor,
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread (percent).
// ---------------------------------------------------------------------------

// Sanity bound for a real-yield curve spread (percent).  A 5s30s / 2s10s
// real-yield spread sits well within ±3% across the playbook universe;
// anything outside is a generic-roll artifact on one endpoint.  Defensive
// frontend net (the real fix is the data pipeline, TD #26 / #31).
const SPREAD_SANITY_MIN_PCT = -3;
const SPREAD_SANITY_MAX_PCT = 3;

export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < SPREAD_SANITY_MIN_PCT
        || r.value > SPREAD_SANITY_MAX_PCT
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: RealYieldCurveSpreadOutput,
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
  data: RealYieldCurveSpreadOutput,
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
  const direction = z > 0 ? 'above' : 'below';
  const directionPhrase =
    z > 0
      ? 'a steeper real-yield curve than its trailing norm (long real yields rich vs short)'
      : 'a flatter / more-inverted real-yield curve than its trailing norm';

  if (regime === 'Extreme') {
    return (
      `The real-yield curve spread is ${regime.toLowerCase()} ${direction} its trailing-year mean — ` +
      `${directionPhrase}.  The current observation sits in the ${bucket.toLowerCase()}-end of the ` +
      `252d range.  This is the shape of the REAL-yield curve, distinct from the nominal curve.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `The real-yield curve spread is elevated vs. its trailing-year history — ${directionPhrase}.`
    );
  }
  return (
    `The real-yield curve spread is within its trailing-year norm; ` +
    `no extreme steepening / flattening stretch.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: RealYieldCurveSpreadOutput,
  effectiveFieldName: string,
  effectiveZWindow: number,
  effectiveZMinPeriods: number,
  effectiveZDdof: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const caveat = countryCaveatFor(cm.curve_family);
  return [
    {
      label: 'Construction',
      value: `spread = long real yield (${cm.long_tenor}) − short real yield (${cm.short_tenor}), same ${cm.curve_family} curve`,
    },
    {
      label: 'Valid tenor pair',
      value: `long tenor (${cm.long_tenor}) > short tenor (${cm.short_tenor}) — enforced at the input + compute layers`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (real yield-to-maturity, both endpoints)`,
    },
    {
      label: 'Z-score model',
      value: `${effectiveZWindow}d rolling window on the spread, min periods ${effectiveZMinPeriods}, ddof ${effectiveZDdof}`,
    },
    {
      label: 'Units',
      value: 'Spread in PERCENT (same as the underlying real yields); changes in bps.',
    },
    {
      label: 'Identity',
      value: cm.country && cm.currency ? `${cm.country} / ${cm.currency} linker curve` : '—',
    },
    {
      label: 'Country caveat',
      value: caveat ? caveat.caveat : '—',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.22' },
    { label: 'Tuckman 4e Ch.5' },
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
