// ============================================================================
// yieldLevelShared.ts — Per-tool helpers for ``get_yield_levels_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (FM4) shared between BuildExtended.tsx,
// BuildCompact.tsx, and the preserved Monitor widget at
// surfaces/monitor/YieldLevelWidget.tsx.  This module knows what a single
// sovereign yield observation is — a percent yield-to-maturity on a
// generic-benchmark curve point — and centralises:
//   - the typed-detail data hook (fetchDetailYield via the standalone
//     bridge at /api/v1/rates/detail/yield, per methodology_exposure.md §5)
//   - sovereign curve-family metadata (short label, long label, flag)
//   - KPI descriptor builders for both the compact and extended views
//   - z-score reference-band computation (mean ± 1.5σ / ± 2σ)
//   - stretch-context interpretation
//   - methodology rows
//
// The wire surface (YieldLevelOutput) is leaner than its linker /
// cross_market siblings: ``current_metrics`` carries current_yield_pct
// + 1d/5d/1m changes (bps) + z-score + 252d high/low/percentile +
// observation_count — no ``methodology_label`` yet, so the methodology
// card composes the disclosure from config conventions client-side.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailYield,
  type YieldDetailParams,
} from '@/services/ratesApi';
import type { YieldLevelOutput } from '@/types/rates';
import {
  bpsAsPercentSubtext,
  bucketForPercentile,
  regimeForZScore,
  signedFixed,
  toneForChange,
  toneForZScore,
  type KPIDescriptor,
  type MethodologyRow,
  type ReferenceBand,
  type ReferenceChip,
  type StretchContext,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Sovereign curve-family metadata.  Single-source-of-truth for the short
// label / long label / flag used by the identity bar + the (preserved)
// Monitor widget.  Mirrors the registry inside calculate_curve_spread_tool's
// shared helper — kept per-tool so modules stay independent (MIGRATION_RULES
// §4 step 10: do not cross-import another tool's helper).
// ---------------------------------------------------------------------------

export interface SovereignFamilyMeta {
  family: string;
  shortLabel: string;
  longLabel: string;
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

/** Sovereign curve options for the controls strip.  Same vocabulary as
 *  the legacy CURVE_OPTIONS from @/lib/monitorParamOptions, with a
 *  per-tool label format for the Build identity bar. */
export const SOVEREIGN_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.shortLabel} · ${m.longLabel}`,
}));

/** Tenor grid for the sovereign yield_levels tool.  The ingested curated
 *  series cover the standard pillars; the backend returns an empty series
 *  if a particular family doesn't carry the chosen tenor. */
export const SOVEREIGN_TENOR_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = [
  { value: '2Y', label: '2Y' },
  { value: '3Y', label: '3Y' },
  { value: '5Y', label: '5Y' },
  { value: '7Y', label: '7Y' },
  { value: '10Y', label: '10Y' },
  { value: '20Y', label: '20Y' },
  { value: '30Y', label: '30Y' },
];

// ---------------------------------------------------------------------------
// Data hook — used by both Build views.  Re-fetches when any input changes;
// errors are stringified and surfaced verbatim through each shell's
// ``errorMessage`` slot.
// ---------------------------------------------------------------------------

export interface UseYieldLevelArgs {
  curveFamily: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseYieldLevelResult {
  data: YieldLevelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useYieldLevel(args: UseYieldLevelArgs): UseYieldLevelResult {
  const [data, setData] = useState<YieldLevelOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: YieldDetailParams = {
    curve_family: args.curveFamily,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (!args.curveFamily || !args.tenor) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailYield(params)
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
  }, [args.curveFamily, args.tenor, args.lookbackDays, args.fieldName, args.asOfDate]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI builders — one source of truth for which numbers go where and how
// they are formatted + toned.
// ---------------------------------------------------------------------------

/** Compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + the mockup at mockups/Compact.png:
 *
 *    1. YIELD          (signed %, neutral primary)
 *    2. 1D CHANGE      (signed bps + % subtext, toneForChange)
 *    3. Z-SCORE (252D) (signed value + regime caption, toneForZScore)
 *
 *  These three answer "where are yields now / how much did they move
 *  today / is this stretched?" — the desk-canonical first read off a
 *  yield-level snapshot.  Alternatives considered: 252d percentile
 *  (already conveyed by the z-score regime + chart bands), weekly /
 *  monthly change (lower-frequency reads), 252d high/low (range
 *  context, not headline).  THESIS Q3 documents the choice. */
export function compactKPIs(
  data: YieldLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'YIELD',
      value: cm.current_yield_pct.toFixed(3),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      subtext: bpsAsPercentSubtext(cm.daily_change_bps, 3),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score, 2),
      tone: toneForZScore(cm.z_score),
      caption: regimeForZScore(cm.z_score),
    },
  ];
}

/** Extended view's full KPI strip — every observable in current_metrics
 *  + an OBSERVATIONS count.  Order matches mockups/Extended.png:
 *  YIELD / 1D / 5D / 1M / Z-SCORE / PERCENTILE / 252D HIGH / 252D LOW /
 *  OBSERVATIONS. */
export function extendedKPIs(
  data: YieldLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'YIELD',
      value: cm.current_yield_pct.toFixed(3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      subtext: bpsAsPercentSubtext(cm.daily_change_bps, 3),
    },
    {
      label: '5D CHANGE',
      value: signedFixed(cm.weekly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.weekly_change_bps),
      subtext: bpsAsPercentSubtext(cm.weekly_change_bps, 3),
    },
    {
      label: '1M CHANGE',
      value: signedFixed(cm.monthly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.monthly_change_bps),
      subtext: bpsAsPercentSubtext(cm.monthly_change_bps, 3),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score, 2),
      tone: toneForZScore(cm.z_score),
      caption: regimeForZScore(cm.z_score),
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
      value: cm.high_252d_pct != null ? cm.high_252d_pct.toFixed(3) : '—',
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: cm.low_252d_pct != null ? cm.low_252d_pct.toFixed(3) : '—',
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: cm.observation_count != null ? `${cm.observation_count}` : '—',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Time-series helpers + reference-band builder.
// ---------------------------------------------------------------------------

/** Defensive sanity bound on sovereign yields.  Nominal sovereign yields
 *  empirically sit in ~[-2%, +25%] across the universe (JGB negative
 *  rates, EM-style spike scenarios).  Anything outside is bad data — a
 *  generic-ticker roll artifact when the underlying bond approaches
 *  maturity.  Values outside the band are nulled so recharts skips them
 *  and the line render continues uninterrupted. */
const YIELD_SANITY_MIN = -3;
const YIELD_SANITY_MAX = 30;

export function sanitiseTimeSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < YIELD_SANITY_MIN
        || r.value > YIELD_SANITY_MAX
        ? null
        : r.value,
  }));
}

/** ±2σ / ±1.5σ reference bands computed from the SANITISED time-series so
 *  outliers don't distort the bands. */
export function buildReferenceBands(
  data: YieldLevelOutput,
): ReadonlyArray<ReferenceBand> {
  const rawRows = data.time_series?.rows ?? [];
  const sanitised = sanitiseTimeSeries(rawRows);
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
  data: YieldLevelOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(regime, cm.z_score, bucket);

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.z_score != null
        ? {
            value: cm.z_score,
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
      ? 'consistent with tightening rate conditions'
      : 'consistent with easing rate conditions';

  if (regime === 'Extreme') {
    return (
      `Nominal yields are ${regime.toLowerCase()} ${direction} their trailing-year mean.  `
      + `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range `
      + `and is ${directionPhrase}.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Nominal yields are elevated vs. their trailing-year history.  `
      + `The recent move is ${directionPhrase}.`
    );
  }
  return (
    `Nominal yields are within their trailing-year norm; `
    + `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references.  Sourced from the yield_levels config
// conventions (z-score window fixed at 252; trailing range 252).  When the
// backend later ships ``current_metrics.methodology_label`` (PR10 in the
// inflation_indexed_bonds sub-domain), the Disclosure row will switch to
// consume the wire — one-line edit.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: YieldLevelOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = sovereignFamilyFor(cm.curve_family);
  const marketLabel = meta ? `${meta.shortLabel} (${meta.longLabel})` : cm.curve_family;
  return [
    {
      label: 'Series',
      value: `Bloomberg generic benchmark yield (${marketLabel} ${cm.tenor})`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (Yield to Maturity)`,
    },
    {
      label: 'Z-score model',
      value: '252-day rolling window, sample stdev (ddof=1)',
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile)',
    },
    {
      label: 'Data cleaning',
      value: `Weekdays only · Forward-fill up to 5 days · ${effectiveLookbackDays}-day display window`,
    },
    {
      label: 'Disclosure',
      value:
        'Generic benchmark yield, not a specific bond.  Sovereign curves can drift '
        + 'from OIS on technicals (FRA-OIS, asset-swap basis, repo specialness); '
        + 'cross-check via swap_spread for the sovereign-vs-OIS gap when relevant.',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.4' },
    { label: 'US TreasuryDirect' },
    { label: 'Bundesbank' },
    { label: 'UK DMO' },
    { label: 'Agence France Trésor' },
  ];
}

// ---------------------------------------------------------------------------
// Compact-view footer caveat.  Per rendering_density.md §2.2 methodology
// MUST be reachable in the compact view; this one-liner surfaces inline
// in the BuildCompactShell's footer.
// ---------------------------------------------------------------------------

export function compactCaveatText(curveFamily: string): string {
  const meta = sovereignFamilyFor(curveFamily);
  const market = meta ? meta.longLabel : curveFamily;
  return (
    `Generic ${market} benchmark yield; cross-check via swap_spread for the sovereign-vs-OIS gap.`
  );
}
