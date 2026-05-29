// ============================================================================
// realYieldShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``get_real_yield_level_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (per docs_revamped/02_components/frontend_module/README.md FM4
// + the rendering-density standard's standalone-module contract).  This
// module knows what "real yield" means; the shared shells do not.
//
// Why a per-tool helper file vs duplicating in each wrapper:
//   - Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data
//     (per docs_revamped/03_standards/rendering_density.md §1.1 — both
//     views consume the same typed-detail endpoint; the compact view
//     just renders less of the payload).
//   - The "canonical first three numbers" the compact view shows are
//     ALSO part of the extended view's KPI strip; the formatting +
//     tone-coloring logic should live in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailRealYield,
  type RealYieldDetailParams,
} from '@/services/ratesApi';
import type { RealYieldLevelOutput } from '@/types/rates';
import {
  bpsAsPercentSubtext,
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
// Data hook
// ---------------------------------------------------------------------------

export interface UseRealYieldArgs {
  curveFamily: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
  zScoreWindowDays?: number;
  zScoreMinPeriods?: number;
  zScoreDdof?: number;
}

export interface UseRealYieldResult {
  data: RealYieldLevelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Errors are stringified
 *  and surfaced verbatim — both views render them through their shell's
 *  ``errorMessage`` slot. */
export function useRealYieldLevel(args: UseRealYieldArgs): UseRealYieldResult {
  const [data, setData] = useState<RealYieldLevelOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: RealYieldDetailParams = {
    curve_family: args.curveFamily,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    z_score_window_days: args.zScoreWindowDays,
    z_score_min_periods: args.zScoreMinPeriods,
    z_score_ddof: args.zScoreDdof,
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
    fetchDetailRealYield(params)
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
    args.tenor,
    args.lookbackDays,
    args.fieldName,
    args.zScoreWindowDays,
    args.zScoreMinPeriods,
    args.zScoreDdof,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders — one source of truth for which numbers go
// where + how they're formatted + toned.
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  docs_revamped/03_standards/rendering_density.md §2.2 + the mockup
 *  design at this module's mockups/Compact.png:
 *
 *    1. REAL YIELD          (signed %, neutral tone, primary emphasis)
 *    2. 1D CHANGE           (signed bps + secondary % subtext, toneForChange)
 *    3. Z-SCORE (252D)      (signed value + regime caption, toneForZScore)
 *
 *  These three are the desk-canonical "first three numbers" a PM reads
 *  off a real-yield level snapshot.  THESIS Q3 documents why these vs
 *  alternatives (e.g. 252d percentile, weekly change). */
export function compactKPIs(
  data: RealYieldLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const zRegime = regimeForZScore(cm.z_score);
  return [
    {
      label: 'REAL YIELD',
      value: signedFixed(cm.real_yield_pct, 2),
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
      caption: zRegime,
    },
  ];
}

/** The extended view's FULL KPI strip — every observable in
 *  current_metrics + an OBSERVATIONS count.  Order matches the mockup
 *  design at this module's mockups/Extended.png. */
export function extendedKPIs(
  data: RealYieldLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'REAL YIELD',
      value: signedFixed(cm.real_yield_pct, 2),
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
      label: 'OBSERVATIONS',
      value: cm.observation_count != null ? `${cm.observation_count}` : '—',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — the chart's ±2σ z-score envelope
// translated to real-yield % levels.
// ---------------------------------------------------------------------------

/** Reasonable sanity bound for sovereign real yields.  Empirically
 *  bounded ~[-6%, +6%] across every market in the playbook universe
 *  (UK pre-2008 ~3%, post-COVID ~-3%, etc.).  Anything outside this
 *  band is almost certainly bad data — typically a Bloomberg generic-
 *  ticker roll artifact when the underlying bond approaches maturity.
 *
 *  We apply this DEFENSIVELY at the per-tool data-prep layer, so even
 *  if the backend cleaning pipeline misses an outlier, the chart
 *  renders sensibly.  The real fix lives in the data pipeline
 *  (per docs/technical_debt.md → linker generic-roll outlier
 *  rejection); this is a frontend safety net.
 *
 *  Values outside the band are treated as null (gap in the series) so
 *  recharts skips them and the area/line render continues uninterrupted. */
const REAL_YIELD_SANITY_MIN = -6;
const REAL_YIELD_SANITY_MAX = 6;

/** Apply the sanity bound to the raw time-series.  Returns a new
 *  array with out-of-bound values nulled.  Caller hands the cleaned
 *  array to both the chart AND the reference-band builder so neither
 *  is distorted by outliers. */
export function sanitiseTimeSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < REAL_YIELD_SANITY_MIN
        || r.value > REAL_YIELD_SANITY_MAX
        ? null
        : r.value,
  }));
}

/** Count how many rows were rejected by the sanity filter — used to
 *  decide whether to surface a "data quality warning" banner.  Returns
 *  0 when no rejections happened. */
export function countOutliersRejected(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): number {
  return rows.reduce((n, r) => {
    if (r.value == null || Number.isNaN(r.value)) return n;
    return r.value < REAL_YIELD_SANITY_MIN || r.value > REAL_YIELD_SANITY_MAX
      ? n + 1
      : n;
  }, 0);
}

/** Compute z-score envelope reference bands at the per-tool defaults:
 *  ±2σ extreme + ±1.5σ elevated.  Returns 4 bands.  Mean + std are
 *  computed from the SANITISED time-series (per sanitiseTimeSeries
 *  above) so outliers don't distort the bands.  This is the
 *  defensive layer that catches data-quality issues like the UK
 *  linker generic-roll artifact at Feb 20–27, 2026. */
export function buildReferenceBands(
  data: RealYieldLevelOutput,
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
  data: RealYieldLevelOutput,
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
      ? 'consistent with tightening real-rate conditions'
      : 'consistent with easing real-rate conditions';

  if (regime === 'Extreme') {
    return (
      `Real yields are ${regime.toLowerCase()} ${direction} their trailing-year mean.  ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${directionPhrase}.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Real yields are elevated vs. their trailing-year history.  ` +
      `The recent move is ${directionPhrase}.`
    );
  }
  return (
    `Real yields are within their trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references — sourced from config.yaml conventions
// + the DB tool_metadata.theoretical_reference.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: RealYieldLevelOutput,
  effectiveFieldName: string,
  effectiveZWindow: number,
  effectiveZMinPeriods: number,
  effectiveZDdof: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const caveat = countryCaveatFor(cm.curve_family);
  return [
    {
      label: 'Series',
      value: `Bloomberg generic benchmark real yield (${cm.curve_family} ${cm.tenor})`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (Real Yield to Maturity)`,
    },
    {
      label: 'Z-score model',
      value: `${effectiveZWindow}d rolling window, min periods ${effectiveZMinPeriods}, ddof ${effectiveZDdof}`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile)',
    },
    {
      label: 'Data cleaning',
      value: 'Weekdays only · Forward-fill up to 5 days',
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
    { label: 'US TreasuryDirect' },
    { label: 'UK DMO' },
    { label: 'Agence France Trésor' },
    { label: 'Bank of Canada' },
  ];
}

// ---------------------------------------------------------------------------
// Display-window utility (for the compact card's short caveat).
// ---------------------------------------------------------------------------

export function compactCaveatText(curveFamily: string): string {
  const entry = countryCaveatFor(curveFamily);
  return entry
    ? entry.caveat
    : 'Generic real-yield benchmark; refer to methodology for details.';
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (so they only have ONE
// import line from this helper file).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
