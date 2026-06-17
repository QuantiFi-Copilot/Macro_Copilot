// ============================================================================
// inflationSwapRateLevelShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``calculate_inflation_swap_rate_level_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "ZCIS rate level"
// is — a single-pillar zero-coupon inflation swap rate observation on ONE
// inflation-swap curve family (USD_ZCIS / EUR_ZCIS / GBP_ZCIS).  ZCIS rates
// are par rates the swap pays for inflation compensation over the tenor —
// NOT a bond yield, NOT an OIS par-swap rate, NOT pure expected inflation.
// The wire field is ``zcis_rate_pct`` (distinct from sovereign
// ``current_yield_pct``, OIS ``current_rate_pct``, and linker
// ``real_yield_pct`` so operator panels cannot silently mix instrument
// families).
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch the
// SAME typed-detail endpoint
// ``/api/v1/rates/detail/inflation-swap-rate-level`` (per
// rendering_density.md §1.1 + §10 — both views consume the same bridge; the
// compact view just renders less of it).  KPI builders + formatting + tone
// logic + the load-bearing index-family caveat live here in ONE place to
// prevent drift across surfaces.
//
// Rolling-z-score conventions are YAML-locked on this primitive (mirrors
// the OIS rate_level / sibling level tools).  Only ``lookback_days`` +
// ``field_name`` are exposed at the controls layer — no "Advanced" panel.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailInflationSwapRateLevel,
  type InflationSwapRateLevelDetailParams,
} from '@/services/ratesApi';
import type { InflationSwapRateLevelOutput } from '@/types/rates';
import {
  bpsAsPercentSubtext,
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
// ZCIS curve-family metadata.  The registry keys off curve_family because
// that uniquely determines the (inflation_index_family, index_lag,
// interpolation) triple the surfaces render.  Curve families enumerated
// mirror the backend's ``inflation_swaps.yml`` playbook universe.
//
// NB: the shared ``countryCaveatFor`` registry only covers the linker
// domain (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) — ZCIS curve
// families are a disjoint universe, so we maintain a per-tool registry
// here (mirrors the sibling OIS rate_level + inflation_swap_curve_spread
// patterns).
// ---------------------------------------------------------------------------

export interface ZcisFamilyMeta {
  /** Curve family identifier (e.g. 'USD_ZCIS'). */
  family: string;
  /** Market short code used in headers (e.g. 'USD'). */
  marketShort: string;
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
  /** Full subtitle for the extended identity row. */
  subtitle: string;
}

const FAMILY_REGISTRY: Record<string, ZcisFamilyMeta> = {
  USD_ZCIS: {
    family: 'USD_ZCIS',
    marketShort: 'USD',
    indexShort: 'CPI-U',
    inflationIndexFamily: 'US_CPI_URBAN',
    indexLag: '3M',
    interpolation: 'Daily',
    flag: '🇺🇸',
    subtitle: 'Zero-Coupon Inflation Swap Rate · CPI-U NSA reference',
  },
  EUR_ZCIS: {
    family: 'EUR_ZCIS',
    marketShort: 'EUR',
    indexShort: 'HICPxT',
    inflationIndexFamily: 'EU_HICP',
    indexLag: '3M',
    interpolation: 'Monthly',
    flag: '🇪🇺',
    subtitle: 'Zero-Coupon Inflation Swap Rate · Eurozone HICP ex-tobacco reference',
  },
  GBP_ZCIS: {
    family: 'GBP_ZCIS',
    marketShort: 'GBP',
    indexShort: 'RPI',
    inflationIndexFamily: 'UK_RPI',
    indexLag: '2M',
    interpolation: 'Monthly',
    flag: '🇬🇧',
    subtitle: 'Zero-Coupon Inflation Swap Rate · UK RPI reference',
  },
};

/** Resolve the per-family meta.  Returns null for an unknown family (caller
 *  renders a neutral fallback). */
export function zcisFamilyFor(family: string): ZcisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The "ZCIS Curve" dropdown options. */
export const ZCIS_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.marketShort} · ${m.indexShort}`,
}));

/** Per-curve tenor grids.  Matches the backend's currently ingested grid:
 *  1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y on every ZCIS curve family
 *  (USD_ZCIS / EUR_ZCIS / GBP_ZCIS).  See
 *  ``rates_agent/playbooks/inflation_swaps.yml``. */
export const ZCIS_TENOR_OPTIONS_BY_CURVE: Record<
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

/** Default tenor set when curve_family hasn't been selected yet (Monitor
 *  add-widget initial render). */
export const ZCIS_DEFAULT_TENORS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '2Y', label: '2Y' },
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: '30Y', label: '30Y' },
];

/** The desk-canonical short-form honesty caveat for this tool — surfaced in
 *  the compact footer.  ZCIS rates price RISK-NEUTRAL IMPLIED INFLATION
 *  COMPENSATION via the OTC zero-coupon inflation swap market; the rate is
 *  the par-rate the swap would pay for inflation compensation over the
 *  tenor, NOT a forecast of realised CPI prints.  The methodology_label
 *  from the wire carries the full disclosure; this string is the compact
 *  footer truncation only. */
export const ZCIS_RATE_LEVEL_COMPACT_CAVEAT_PREFIX =
  'OTC ZCIS rate (risk-neutral implied inflation)';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseInflationSwapRateLevelArgs {
  curveFamily: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseInflationSwapRateLevelResult {
  data: InflationSwapRateLevelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three surfaces.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the OIS rate_level
 *  hook shape so the per-tool surfaces look the same file-for-file. */
export function useInflationSwapRateLevel(
  args: UseInflationSwapRateLevelArgs,
): UseInflationSwapRateLevelResult {
  const [data, setData] = useState<InflationSwapRateLevelOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: InflationSwapRateLevelDetailParams = {
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
    fetchDetailInflationSwapRateLevel(params)
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
// KPI descriptor builders — one source of truth for which numbers go where
// + how they're formatted + toned.
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  docs_revamped/03_standards/rendering_density.md §2.2 + the mockup design
 *  at this module's mockups/Compact.png:
 *
 *    1. RATE (%)             (signed %, neutral tone, primary emphasis)
 *    2. 1D CHANGE (bps)      (signed bps + secondary % subtext, toneForChange)
 *    3. Z-SCORE (252D)       (signed value + regime caption, toneForZScore)
 *
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: InflationSwapRateLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const zRegime = regimeForZScore(cm.z_score);
  return [
    {
      label: 'RATE',
      value: signedFixed(cm.zcis_rate_pct, 3),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
      caption: 'Zero-Coupon Inflation Swap Rate',
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

/** The extended view's FULL KPI strip — every observable in current_metrics
 *  + an OBSERVATIONS count.  Order matches the mockup design at this
 *  module's mockups/Extended.png. */
export function extendedKPIs(
  data: InflationSwapRateLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'RATE',
      value: signedFixed(cm.zcis_rate_pct, 3),
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
      value: signedFixed(cm.high_252d_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_pct, 3),
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
// Reference-band builder — the chart's ±2σ z-score envelope translated to
// ZCIS-rate % levels.
// ---------------------------------------------------------------------------

/** Empirical sanity bound for ZCIS rates.  Across the playbook universe
 *  (USD_ZCIS / EUR_ZCIS / GBP_ZCIS) rates fit comfortably inside [-2%, 8%]
 *  (EUR ZCIS dipped below zero across parts of post-2014 / post-2020;
 *  post-2022 inflation cycle pushed USD / GBP ZCIS towards the 5–6%
 *  range).  Values outside this band are nulled so recharts skips them
 *  and the area/line render continues uninterrupted.  Defensive layer;
 *  the real fix lives in the data pipeline. */
const ZCIS_RATE_SANITY_MIN = -2;
const ZCIS_RATE_SANITY_MAX = 8;

/** Apply the sanity bound to the raw time-series.  Returns a new array with
 *  out-of-bound values nulled.  Caller hands the cleaned array to both the
 *  chart AND the reference-band builder so neither is distorted by
 *  outliers. */
export function sanitiseTimeSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < ZCIS_RATE_SANITY_MIN
        || r.value > ZCIS_RATE_SANITY_MAX
        ? null
        : r.value,
  }));
}

/** Compute z-score envelope reference bands at the per-tool defaults:
 *  ±2σ extreme + ±1.5σ elevated.  Mean + std are computed from the
 *  SANITISED time-series (per sanitiseTimeSeries above) so outliers don't
 *  distort the bands. */
export function buildReferenceBands(
  data: InflationSwapRateLevelOutput,
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
    { value: mean, label: 'Mean', tone: 'neutral', style: 'dashed' },
    { value: mean - 1.5 * std, label: '-1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean - 2 * std, label: '-2σ', tone: 'positive', style: 'dashed' },
  ];
}

// ---------------------------------------------------------------------------
// Stretch-context builder
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: InflationSwapRateLevelOutput,
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
      ? 'consistent with implied inflation compensation repricing higher (hawkish stretch)'
      : 'consistent with implied inflation compensation repricing lower (dovish stretch)';

  if (regime === 'Extreme') {
    return (
      `ZCIS rate is ${regime.toLowerCase()} ${direction} its trailing-year mean.  ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${directionPhrase}.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `ZCIS rate is elevated vs. its trailing-year history.  ` +
      `The recent move is ${directionPhrase}.`
    );
  }
  return (
    `ZCIS rate is within its trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows — surfaces the WIRE ``methodology_label`` (PR10 / P5
// threading) plus the load-bearing reference-metadata triple
// (inflation_index_family / index_lag / interpolation / underlying_index).
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: InflationSwapRateLevelOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  return [
    {
      label: 'Series',
      value: `${cm.curve_family} ${cm.tenor} ZCIS par rate`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (mid quoted ZCIS rate)`,
    },
    {
      label: 'Reference index',
      value: cm.underlying_index
        ? `${cm.inflation_index_family} (${cm.underlying_index})`
        : cm.inflation_index_family,
    },
    {
      label: 'Index lag · interpolation',
      value: `${cm.index_lag} · ${cm.interpolation}`,
    },
    {
      label: 'Z-score model',
      value: '252d rolling window, min periods 60, ddof 1 (YAML-locked)',
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
      // PR10 / P5 — wire-honesty disclosure threaded from
      // config.yaml:methodology.what_it_does.  NEVER a hardcoded TS literal.
      label: 'Disclosure',
      value: cm.methodology_label,
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
// Display-window utility (for the compact card's short caveat).
// ---------------------------------------------------------------------------

/** Per-family compact-card caveat.  Combines the reference-index short label
 *  with the canonical risk-neutral implied-inflation disclosure.  Designed
 *  to fit on a single line in the compact footer. */
export function compactCaveatText(curveFamily: string): string {
  const meta = zcisFamilyFor(curveFamily);
  if (!meta) {
    return `OTC ZCIS rate (risk-neutral implied inflation)`;
  }
  return `${meta.indexShort} · ${ZCIS_RATE_LEVEL_COMPACT_CAVEAT_PREFIX}`;
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need.
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
