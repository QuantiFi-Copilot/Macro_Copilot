// ============================================================================
// oisRateLevelShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the Monitor widget for ``get_ois_rate_level_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what an "OIS rate level"
// is — a single-tenor par-swap-rate observation on ONE OIS curve family
// (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS /
// CAD_OIS).  OIS quotes are par swap rates, not bond yields (no coupon, no
// accrued, no principal) — so the wire field is ``current_rate_pct`` rather
// than ``current_yield_pct``.
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch the
// SAME typed-detail endpoint ``/api/v1/rates/detail/ois-rate-level`` (per
// rendering_density.md §1.1 + §10 — both views consume the same bridge; the
// compact view just renders less of it).  KPI builders + formatting + tone
// logic + the risk-neutral-implied-policy-path caveat live here in ONE
// place to prevent drift across surfaces.
//
// Rolling-z-score conventions are YAML-locked on this primitive (mirrors the
// OIS curve_spread / butterfly siblings).  Only ``lookback_days`` +
// ``field_name`` are exposed at the controls layer — no "Advanced" panel.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailOisRateLevel,
  type OisRateLevelDetailParams,
} from '@/services/ratesApi';
import type { OisRateLevelOutput } from '@/types/rates';
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
// OIS curve-family metadata.  The registry keys off curve_family because
// that uniquely determines the overnight-index identity (SOFR / ESTR /
// SONIA / TONA / AONIA / CORRA) the surfaces render.  Curve families
// enumerated mirror the backend's closed ``OIS_CURVE_FAMILY`` enum sourced
// from rates_agent/playbooks/ois.yml.
//
// NB: the shared ``countryCaveatFor`` registry only covers the linker
// domain (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) — OIS curve
// families are a disjoint universe, so we maintain a per-tool registry
// here (mirrors the sibling calculate_ois_curve_spread_tool's pattern).
// ---------------------------------------------------------------------------

export interface OisFamilyMeta {
  /** Curve family identifier (e.g. 'USD_SOFR_OIS'). */
  family: string;
  /** Market short code used in headers (e.g. 'USD'). */
  marketShort: string;
  /** Short overnight-index label (e.g. 'SOFR', 'ESTR', 'SONIA'). */
  indexShort: string;
  /** Central bank that anchors the overnight reference rate. */
  centralBank: string;
  /** Country flag emoji for the identity chip + caveat footer. */
  flag: string;
  /** Full subtitle for the extended identity row. */
  subtitle: string;
}

const FAMILY_REGISTRY: Record<string, OisFamilyMeta> = {
  USD_SOFR_OIS: {
    family: 'USD_SOFR_OIS',
    marketShort: 'USD',
    indexShort: 'SOFR',
    centralBank: 'Federal Reserve',
    flag: '🇺🇸',
    subtitle: 'Secured Overnight Financing Rate (SOFR) · Par swap rate',
  },
  EUR_ESTR_OIS: {
    family: 'EUR_ESTR_OIS',
    marketShort: 'EUR',
    indexShort: 'ESTR',
    centralBank: 'European Central Bank',
    flag: '🇪🇺',
    subtitle: 'Euro Short-Term Rate (€STR) · Par swap rate',
  },
  GBP_SONIA_OIS: {
    family: 'GBP_SONIA_OIS',
    marketShort: 'GBP',
    indexShort: 'SONIA',
    centralBank: 'Bank of England',
    flag: '🇬🇧',
    subtitle: 'Sterling Overnight Index Average (SONIA) · Par swap rate',
  },
  JPY_OIS: {
    family: 'JPY_OIS',
    marketShort: 'JPY',
    indexShort: 'TONA',
    centralBank: 'Bank of Japan',
    flag: '🇯🇵',
    subtitle: 'Tokyo Overnight Average Rate (TONA) · Par swap rate',
  },
  AUD_OIS: {
    family: 'AUD_OIS',
    marketShort: 'AUD',
    indexShort: 'AONIA',
    centralBank: 'Reserve Bank of Australia',
    flag: '🇦🇺',
    subtitle: 'Australian Overnight Index Average (AONIA) · Par swap rate',
  },
  CAD_OIS: {
    family: 'CAD_OIS',
    marketShort: 'CAD',
    indexShort: 'CORRA',
    centralBank: 'Bank of Canada',
    flag: '🇨🇦',
    subtitle: 'Canadian Overnight Repo Rate Average (CORRA) · Par swap rate',
  },
};

/** Resolve the per-family meta.  Returns null for an unknown family (caller
 *  renders a neutral fallback). */
export function oisFamilyFor(family: string): OisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The "OIS Curve" dropdown options.  Single-curve primitive — no
 *  cross-family concept (distinct from ``calculate_ois_cross_market_spread``
 *  which crosses two families). */
export const OIS_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.marketShort} · ${m.indexShort}`,
}));

/** Per-curve tenor grids.  OIS curves have a dense short-end (1W / 1M / 3M
 *  / 6M / 9M / 1Y) plus a sparser long-end grid; matches the backend's
 *  fetch_single_tenor input space.  JPY / AUD / CAD start at 3M because
 *  those markets don't quote sub-3M OIS in bulk. */
export const OIS_TENOR_OPTIONS_BY_CURVE: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  USD_SOFR_OIS: [
    '1W', '1M', '2M', '3M', '6M', '9M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y',
  ].map((t) => ({ value: t, label: t })),
  EUR_ESTR_OIS: [
    '1W', '1M', '2M', '3M', '6M', '9M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y',
  ].map((t) => ({ value: t, label: t })),
  GBP_SONIA_OIS: [
    '1W', '1M', '3M', '6M', '1Y', '2Y', '3Y', '5Y', '10Y', '20Y', '30Y',
  ].map((t) => ({ value: t, label: t })),
  JPY_OIS: ['3M', '6M', '1Y', '2Y', '5Y', '10Y', '20Y', '30Y'].map((t) => ({
    value: t,
    label: t,
  })),
  AUD_OIS: ['3M', '6M', '1Y', '2Y', '5Y', '10Y'].map((t) => ({
    value: t,
    label: t,
  })),
  CAD_OIS: ['3M', '6M', '1Y', '2Y', '5Y', '10Y'].map((t) => ({
    value: t,
    label: t,
  })),
};

/** Default tenor set when curve_family hasn't been selected yet (Monitor
 *  add-widget initial render). */
export const OIS_DEFAULT_TENORS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '2Y', label: '2Y' },
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: '30Y', label: '30Y' },
];

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact footer + the extended methodology card.  OIS curves price the
 *  RISK-NEUTRAL EXPECTED POLICY PATH; the rate level at a tenor is the
 *  par-swap rate the market would pay to exchange floating overnight
 *  fixings for a fixed coupon over that horizon, NOT a forecast of
 *  realised central-bank decisions.  Mockup-faithful one-liner.
 *
 *  TODO(PR10): when the backend ships ``current_metrics.methodology_label``
 *  on this primitive's Output (today the schema lacks it), switch the
 *  methodology card's "Disclosure" row to source from the wire. */
export const OIS_RATE_LEVEL_COMPACT_CAVEAT_PREFIX =
  'OIS (risk-neutral implied policy path)';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseOisRateLevelArgs {
  curveFamily: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseOisRateLevelResult {
  data: OisRateLevelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three surfaces.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the OIS curve_spread
 *  hook shape so the per-tool surfaces look the same file-for-file. */
export function useOisRateLevel(args: UseOisRateLevelArgs): UseOisRateLevelResult {
  const [data, setData] = useState<OisRateLevelOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: OisRateLevelDetailParams = {
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
    fetchDetailOisRateLevel(params)
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
 *    1. RATE                (signed %, neutral tone, primary emphasis)
 *    2. 1D CHANGE (bps)     (signed bps + secondary % subtext, toneForChange)
 *    3. Z-SCORE (252D)      (signed value + regime caption, toneForZScore)
 *
 *  These three are the desk-canonical "first three numbers" a PM reads off
 *  an OIS rate-level snapshot.  THESIS Q3 documents why these vs
 *  alternatives. */
export function compactKPIs(
  data: OisRateLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const zRegime = regimeForZScore(cm.z_score);
  return [
    {
      label: 'RATE',
      value: signedFixed(cm.current_rate_pct, 4),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
      caption: 'OIS Par Swap Rate',
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
  data: OisRateLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'RATE',
      value: signedFixed(cm.current_rate_pct, 4),
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
      value: signedFixed(cm.high_252d_pct, 4),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_pct, 4),
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
// OIS-rate % levels.
// ---------------------------------------------------------------------------

/** Empirical sanity bound for sovereign OIS par-swap rates.  Across every
 *  market in the playbook universe rates fit comfortably inside [-1.5%, 12%]
 *  (BoJ pre-2024 ≈ 0%, post-2022 Fed cycle peak ≈ 5.5%, 1980s STIR
 *  historical highs were materially higher but our ingest window only goes
 *  back to ~2010 so outside band is bad data).  Values outside this band
 *  are nulled so recharts skips them and the area/line render continues
 *  uninterrupted.  Defensive layer; the real fix lives in the data pipeline. */
const OIS_RATE_SANITY_MIN = -1.5;
const OIS_RATE_SANITY_MAX = 12;

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
        || r.value < OIS_RATE_SANITY_MIN
        || r.value > OIS_RATE_SANITY_MAX
        ? null
        : r.value,
  }));
}

/** Compute z-score envelope reference bands at the per-tool defaults:
 *  ±2σ extreme + ±1.5σ elevated.  Mean + std are computed from the
 *  SANITISED time-series (per sanitiseTimeSeries above) so outliers don't
 *  distort the bands. */
export function buildReferenceBands(
  data: OisRateLevelOutput,
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
  data: OisRateLevelOutput,
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
      ? 'consistent with the implied policy path repricing higher (hawkish stretch)'
      : 'consistent with the implied policy path repricing lower (dovish stretch)';

  if (regime === 'Extreme') {
    return (
      `OIS rate is ${regime.toLowerCase()} ${direction} its trailing-year mean.  ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${directionPhrase}.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `OIS rate is elevated vs. its trailing-year history.  ` +
      `The recent move is ${directionPhrase}.`
    );
  }
  return (
    `OIS rate is within its trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references — sourced from config.yaml conventions.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: OisRateLevelOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = oisFamilyFor(cm.curve_family);
  return [
    {
      label: 'Series',
      value: `${cm.curve_family} ${cm.tenor} OIS par swap rate`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (mid par-swap rate)`,
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
      label: 'Disclosure',
      value: meta
        ? `Risk-neutral implied policy path anchored to ${meta.centralBank}'s ${meta.indexShort} overnight reference. OIS prices the EXPECTED policy path, not realised central-bank decisions.`
        : 'Risk-neutral implied policy path. OIS prices the EXPECTED policy path, not realised central-bank decisions.',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.18' },
    { label: 'Federal Reserve · SOFR' },
    { label: 'ECB · €STR' },
    { label: 'BoE · SONIA' },
    { label: 'BoJ · TONA' },
  ];
}

// ---------------------------------------------------------------------------
// Display-window utility (for the compact card's short caveat).
// ---------------------------------------------------------------------------

/** Per-family compact-card caveat.  Combines the central-bank anchor with
 *  the canonical risk-neutral policy-path disclosure.  Designed to fit on
 *  a single line in the compact footer. */
export function compactCaveatText(curveFamily: string): string {
  const meta = oisFamilyFor(curveFamily);
  if (!meta) {
    return `OIS (risk-neutral implied policy path)`;
  }
  return `${meta.centralBank} · ${OIS_RATE_LEVEL_COMPACT_CAVEAT_PREFIX}`;
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need.
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
