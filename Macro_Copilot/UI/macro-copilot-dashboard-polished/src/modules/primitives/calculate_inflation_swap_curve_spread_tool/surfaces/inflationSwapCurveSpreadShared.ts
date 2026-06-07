// ============================================================================
// inflationSwapCurveSpreadShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``calculate_inflation_swap_curve_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (shape-twin of breakevenCurveSpreadShared.ts +
// near-sibling of inflationSwapRateLevelShared.ts).  This module knows what a
// "same-curve ZCIS tenor spread" means — specifically that it is the term
// structure of OTC ZERO-COUPON INFLATION SWAP (ZCIS) rates between two
// pillars of one ZCIS curve family (e.g. USD_ZCIS 5s10s, EUR_ZCIS 5s30s,
// GBP_ZCIS 2s10s).
//
// POSITIVE spread = upward-sloping forward inflation curve (long-tenor
// implied inflation HIGHER than short-tenor); NEGATIVE = inverted (front-
// end higher).  Distinct from a breakeven curve spread (bond-implied
// inflation compensation × 2 legs, carries IRP + liquidity premia at each
// endpoint) — the ZCIS object is the OTC swap-implied inflation curve,
// NOT bond-implied.  The shared shells stay finance-blind.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailInflationSwapCurveSpread,
  type InflationSwapCurveSpreadDetailParams,
} from '@/services/ratesApi';
import type { InflationSwapCurveSpreadOutput } from '@/types/rates';
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
// ZCIS curve-family metadata.  Keyed by curve_family (same-curve invariant:
// a single ``curve_family`` is shared by both legs).  Mirrors the near-
// sibling inflation_swap_rate_level's FAMILY_REGISTRY but is maintained
// locally so the curve-spread surfaces don't have to import a sibling
// module's private state.
// ---------------------------------------------------------------------------

export interface ZcisCurveSpreadFamilyMeta {
  /** Curve family identifier (e.g. 'USD_ZCIS'). */
  family: string;
  /** Market short code used in headers (e.g. 'USD'). */
  marketShort: string;
  /** Country label for the identity row + flag. */
  country: string;
  /** Short reference-index label (e.g. 'CPI-U', 'HICPxT', 'RPI'). */
  indexShort: string;
  /** Full inflation index family name as on the wire. */
  inflationIndexFamily: string;
  /** Canonical index lag (e.g. '3M' / '2M'). */
  indexLag: string;
  /** Index-fixing interpolation convention. */
  interpolation: string;
  /** Country flag emoji for the identity chip + caveat footer. */
  flag: string;
}

const FAMILY_REGISTRY: Record<string, ZcisCurveSpreadFamilyMeta> = {
  USD_ZCIS: {
    family: 'USD_ZCIS',
    marketShort: 'USD',
    country: 'US',
    indexShort: 'CPI-U',
    inflationIndexFamily: 'US_CPI_URBAN',
    indexLag: '3M',
    interpolation: 'Daily',
    flag: '🇺🇸',
  },
  EUR_ZCIS: {
    family: 'EUR_ZCIS',
    marketShort: 'EUR',
    country: 'EUR',
    indexShort: 'HICPxT',
    inflationIndexFamily: 'EU_HICP',
    indexLag: '3M',
    interpolation: 'Monthly',
    flag: '🇪🇺',
  },
  GBP_ZCIS: {
    family: 'GBP_ZCIS',
    marketShort: 'GBP',
    country: 'UK',
    indexShort: 'RPI',
    inflationIndexFamily: 'UK_RPI',
    indexLag: '2M',
    interpolation: 'Monthly',
    flag: '🇬🇧',
  },
};

/** Resolve the per-family meta.  Returns null for an unknown family. */
export function zcisCurveFamilyFor(
  family: string,
): ZcisCurveSpreadFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The single "ZCIS Curve" dropdown options. */
export const ZCIS_CURVE_SPREAD_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.marketShort} · ${m.indexShort}`,
}));

/** Per-curve tenor grids.  Mirrors the backend's currently ingested grid:
 *  1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y on every ZCIS curve family. */
export const ZCIS_CURVE_SPREAD_TENORS_BY_CURVE: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  USD_ZCIS: ['1Y', '2Y', '3Y', '5Y', '10Y', '20Y', '30Y'].map((t) => ({
    value: t,
    label: t,
  })),
  EUR_ZCIS: ['1Y', '2Y', '3Y', '5Y', '10Y', '20Y', '30Y'].map((t) => ({
    value: t,
    label: t,
  })),
  GBP_ZCIS: ['1Y', '2Y', '3Y', '5Y', '10Y', '20Y', '30Y'].map((t) => ({
    value: t,
    label: t,
  })),
};

/** Minimal tenor → years parser (mirrors backend's tenor_to_years for the
 *  common <n>W / <n>M / <n>Y forms).  Used client-side to filter the
 *  long-tenor dropdown to strictly-longer tenors so the long > short
 *  validity rule is enforced at the input layer (the backend re-validates). */
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

/** Desk-canonical short-form honesty caveat — surfaced in the compact
 *  footer.  ZCIS curve spreads price RISK-NEUTRAL IMPLIED FORWARD
 *  INFLATION SHAPE via the OTC zero-coupon inflation swap curve; the
 *  spread is the per-trade-date difference of two endpoint ZCIS rates,
 *  NOT a forecast of realised CPI prints.  Distinct from a bond-implied
 *  breakeven curve spread (that primitive is the inflation-compensation
 *  term structure × 2 bond legs, carrying IRP + liquidity premia at each
 *  endpoint). */
export const ZCIS_CURVE_SPREAD_COMPACT_CAVEAT =
  'OTC inflation swap curve; basis to bond BE is a separate primitive.';

// ---------------------------------------------------------------------------
// Sign-convention caption.  POSITIVE = upward-sloping forward inflation
// curve (long-tenor implied higher than short-tenor); NEGATIVE = inverted.
// The mockup labels the headline caption with the curve-shape framing.
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

export interface UseInflationSwapCurveSpreadArgs {
  curveFamily: string;
  shortTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseInflationSwapCurveSpreadResult {
  data: InflationSwapCurveSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useInflationSwapCurveSpread(
  args: UseInflationSwapCurveSpreadArgs,
): UseInflationSwapCurveSpreadResult {
  const [data, setData] = useState<InflationSwapCurveSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: InflationSwapCurveSpreadDetailParams = {
    curve_family: args.curveFamily,
    short_tenor: args.shortTenor,
    long_tenor: args.longTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
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
    fetchDetailInflationSwapCurveSpread(params)
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
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** Local rolling-σ estimator for the 1D-change → σ caption.  The backend
 *  does not (yet) ship the rolling std directly; reconstruct it from
 *  ``|spread_bps - mean| = |z| × σ`` assuming mean ≈ 0.  Coarse, suitable
 *  only for a "size-of-move" subtext on the compact card. */
function approxSigmaForChange(
  cm: InflationSwapCurveSpreadOutput['current_metrics'],
): number | null {
  const z = cm.z_score_252d;
  const v = cm.spread_bps;
  if (z == null || v == null || !Number.isFinite(z) || !Number.isFinite(v)) return null;
  if (Math.abs(z) < 1e-6) return null;
  const sigma = Math.abs(v / z);
  return Number.isFinite(sigma) && sigma > 0 ? sigma : null;
}

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (<pair>) (signed bps, neutral, primary emphasis, slope caption)
 *    2. 1D CHANGE       (signed bps, toneForChange, σ subtext)
 *    3. Z-SCORE (252D)  (signed value + regime caption, toneForZScore)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: InflationSwapCurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const sigma = approxSigmaForChange(cm);
  const pairLabel = spreadShortLabel(cm.short_tenor, cm.long_tenor);
  return [
    {
      label: `SPREAD (${pairLabel})`,
      value: signedFixed(cm.spread_bps, 0),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: slopeRegimeCaption(cm.spread_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.change_1d_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1d_bps),
      subtext:
        sigma != null && cm.change_1d_bps != null
          ? `(${cm.change_1d_bps >= 0 ? '+' : ''}${(cm.change_1d_bps / sigma).toFixed(2)}σ)`
          : undefined,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_252d, 2),
      tone: toneForZScore(cm.z_score_252d),
      caption: zScoreSlopeCaption(cm.z_score_252d),
    },
  ];
}

/** The extended view's FULL KPI strip (mockups/Extended.png — bps-scale
 *  strip + z/percentile + 252d range, then the two endpoint ZCIS rates
 *  for the decomposition). */
export function extendedKPIs(
  data: InflationSwapCurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const pairLabel = spreadShortLabel(cm.short_tenor, cm.long_tenor);
  return [
    {
      label: `SPREAD (${pairLabel})`,
      value: signedFixed(cm.spread_bps, 0),
      unit: 'bp',
      tone: 'neutral',
      caption: slopeRegimeCaption(cm.spread_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.change_1d_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1d_bps),
    },
    {
      label: '5D CHANGE',
      value: signedFixed(cm.change_1w_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1w_bps),
    },
    {
      label: '1M CHANGE',
      value: signedFixed(cm.change_1m_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1m_bps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_252d, 2),
      tone: toneForZScore(cm.z_score_252d),
      caption: zScoreSlopeCaption(cm.z_score_252d),
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
    {
      label: 'OBSERVATIONS',
      value: `${cm.observation_count}`,
      tone: 'neutral',
    },
  ];
}

/** The decomposition row — short + long endpoint ZCIS rates (PERCENT),
 *  surfaced on the extended view so the desk can audit the curve-spread
 *  construction without a second tool call. */
export function decompositionKPIs(
  data: InflationSwapCurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `SHORT ZCIS (${cm.short_tenor})`,
      value: signedFixed(cm.short_zcis_rate_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `LONG ZCIS (${cm.long_tenor})`,
      value: signedFixed(cm.long_zcis_rate_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

// Sanity bound for ZCIS curve spreads (bps).  Defensive frontend safety net —
// the playbook universe sits comfortably within ±500bps; anything outside is
// a roll / data artifact on one endpoint.
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
  data: InflationSwapCurveSpreadOutput,
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
  data: InflationSwapCurveSpreadOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score_252d == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score_252d);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(regime, cm.z_score_252d, bucket);

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.z_score_252d != null
        ? {
            value: cm.z_score_252d,
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
      ? 'a steeper ZCIS curve than its trailing norm (long-tenor implied inflation rich vs short-tenor)'
      : 'a flatter / more-inverted ZCIS curve than its trailing norm';

  if (regime === 'Extreme') {
    return (
      `ZCIS curve spread is ${regime.toLowerCase()} ${z > 0 ? 'above' : 'below'} its trailing-year mean — ` +
      `${directionPhrase}.  The current observation sits in the ${bucket.toLowerCase()}-end of the ` +
      `252d range.  This is OTC ZCIS-implied forward inflation shape, not bond-implied breakeven shape.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `ZCIS curve spread is elevated vs. trailing-year history — ${directionPhrase}.  ` +
      `Forward inflation curve shape.`
    );
  }
  return (
    `ZCIS curve spread is within its trailing-year norm; ` +
    `no extreme steepening / flattening stretch.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: InflationSwapCurveSpreadOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = zcisCurveFamilyFor(cm.curve_family);
  return [
    {
      label: 'Construction',
      value:
        `spread_bps = (long_zcis_pct (${cm.long_tenor}) − short_zcis_pct (${cm.short_tenor})) × 100; ` +
        `each leg = ZCIS par rate on the ${cm.curve_family} curve`,
    },
    {
      label: 'Sign convention',
      value: 'POSITIVE = upward-sloping forward inflation curve (long > short) · NEGATIVE = inverted',
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
      value: `${effectiveFieldName} (mid quoted ZCIS rate, both endpoint series)`,
    },
    {
      label: 'Reference index',
      value: cm.underlying_index
        ? `${cm.inflation_index_family} (${cm.underlying_index})`
        : cm.inflation_index_family,
    },
    {
      label: 'Index lag · interpolation',
      value: `${cm.index_lag} · ${cm.interpolation} (shared by both legs by the same-curve invariant)`,
    },
    {
      label: 'Z-score model',
      value: '252d rolling window on the bps spread, min periods 60, ddof 1 (YAML-locked)',
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
      label: 'Curve family',
      value: meta ? `${meta.country} (${meta.marketShort} · ${meta.indexShort})` : cm.curve_family,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ISDA · ZCIS conventions' },
    { label: 'BLS · CPI-U' },
    { label: 'Eurostat · HICP ex-tobacco' },
    { label: 'ONS · UK RPI' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need.
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
