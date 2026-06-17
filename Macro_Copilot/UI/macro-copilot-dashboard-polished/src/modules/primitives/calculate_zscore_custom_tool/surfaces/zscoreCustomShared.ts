// ============================================================================
// zscoreCustomShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx and the Monitor widget for ``calculate_zscore_custom_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of breakeven's breakevenShared.ts).
// This module knows what a "custom-window rolling z-score" means —
// specifically that ``z_score_window_days`` is the tool's CENTRAL knob
// (the one input that defines what the tool IS, per config.yaml A13)
// while ``min_periods`` / ``ddof`` are YAML-locked and merely ECHOED on
// the wire; the shared shells do not.
//
// All three surfaces fetch the SAME data (per rendering_density.md §1.1 —
// every view consumes the same typed-detail endpoint at
// /api/v1/rates/detail/zscore-custom; the compact view just renders
// less).  Headline KPIs + formatting + tone logic live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailZscoreCustom,
  type ZscoreCustomDetailParams,
} from '@/services/ratesApi';
import type { ZscoreCustomOutput } from '@/types/rates';
import {
  observationCount,
  regimeForZScore,
  signedFixed,
  toneForZScore,
  unsignedFixed,
  type ChartPoint,
  type KPIDescriptor,
  type MethodologyRow,
  type ReferenceBand,
  type StretchContext,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Sovereign curve-family metadata — flags + short labels for the identity
// rows.  Keyed by the SAME curve_family codes the backend schema documents
// ('UST', 'DE_BUND', … 'JGB' — NOT the scanner's 'JP_JGB' alias) and the
// canonical CURVE_OPTIONS enum in src/lib/monitorParamOptions.ts uses.
// Finance-aware → lives in this per-tool layer, NOT the shared registry
// (countryCaveats.ts is linker-domain only).
// ---------------------------------------------------------------------------

export interface ZscoreFamilyMeta {
  /** curve_family code (e.g. 'UST'). */
  family: string;
  /** Country label (e.g. 'US'). */
  country: string;
  /** Short curve label for chips (e.g. 'UST', 'Bund'). */
  curveShort: string;
  /** Country flag emoji. */
  flag: string;
}

const FAMILY_REGISTRY: Record<string, ZscoreFamilyMeta> = {
  UST:         { family: 'UST',         country: 'US',        curveShort: 'UST',  flag: '🇺🇸' },
  DE_BUND:     { family: 'DE_BUND',     country: 'Germany',   curveShort: 'Bund', flag: '🇩🇪' },
  UK_GILT:     { family: 'UK_GILT',     country: 'UK',        curveShort: 'Gilt', flag: '🇬🇧' },
  JGB:         { family: 'JGB',         country: 'Japan',     curveShort: 'JGB',  flag: '🇯🇵' },
  FR_OAT:      { family: 'FR_OAT',      country: 'France',    curveShort: 'OAT',  flag: '🇫🇷' },
  IT_BTP:      { family: 'IT_BTP',      country: 'Italy',     curveShort: 'BTP',  flag: '🇮🇹' },
  ES_BONO:     { family: 'ES_BONO',     country: 'Spain',     curveShort: 'Bono', flag: '🇪🇸' },
  AU_GOVT:     { family: 'AU_GOVT',     country: 'Australia', curveShort: 'AUS',  flag: '🇦🇺' },
  CANADA_GOVT: { family: 'CANADA_GOVT', country: 'Canada',    curveShort: 'CAN',  flag: '🇨🇦' },
};

/** Resolve display metadata for a curve_family.  Returns null for an
 *  unknown family (caller renders a neutral fallback). */
export function familyForCurve(curveFamily: string): ZscoreFamilyMeta | null {
  return FAMILY_REGISTRY[curveFamily] ?? null;
}

// ---------------------------------------------------------------------------
// Option enums — single source of truth for the extended view's controls
// AND the Monitor widget's paramFields (module.ts value-imports
// ZSCORE_WINDOW_OPTIONS so the catalog form matches the Build controls).
// Curve families come from the canonical shared enum at
// src/lib/monitorParamOptions.ts (CURVE_OPTIONS) — not duplicated here.
// ---------------------------------------------------------------------------

/** Full sovereign tenor grid per the backend schema's documented examples
 *  ('1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y').  Wider than the
 *  Monitor-form TENOR_OPTIONS subset because the Build view is the
 *  investigation surface. */
export const ZSCORE_TENOR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  ['1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'].map((t) => ({
    value: t,
    label: t,
  }));

/** The CENTRAL knob — desk-canonical rolling-window lengths from the
 *  backend schema docstring (60 tactical / 126 quarterly / 252 annual /
 *  504 two-year) plus the schema's [20, 1260] bounds as endpoints. */
export const ZSCORE_WINDOW_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '20', label: '20d · floor' },
  { value: '60', label: '60d · tactical' },
  { value: '126', label: '126d · quarterly' },
  { value: '252', label: '252d · annual' },
  { value: '504', label: '504d · two-year' },
  { value: '1260', label: '1260d · five-year' },
];

/** Displayed-history window (calendar days).  Display only — does NOT
 *  control the rolling window (that is z_score_window_days). */
export const ZSCORE_LOOKBACK_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

export const ZSCORE_FIELD_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID' },
  { value: 'YLD_YTM_BID', label: 'YLD_YTM_BID' },
  { value: 'YLD_YTM_ASK', label: 'YLD_YTM_ASK' },
];

export const ZSCORE_DEFAULTS = {
  curve_family: 'UST',
  tenor: '10Y',
  z_score_window_days: '252',
  lookback_days: '365',
  field_name: 'YLD_YTM_MID',
} as const;

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseZscoreCustomArgs {
  curveFamily: string;
  tenor: string;
  /** The central knob — REQUIRED on the wire (no backend default). */
  zScoreWindowDays: number;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseZscoreCustomResult {
  data: ZscoreCustomOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useZscoreCustomData(
  args: UseZscoreCustomArgs,
): UseZscoreCustomResult {
  const [data, setData] = useState<ZscoreCustomOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: ZscoreCustomDetailParams = {
    curve_family: args.curveFamily,
    tenor: args.tenor,
    z_score_window_days: args.zScoreWindowDays,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (
      !args.curveFamily
      || !args.tenor
      || !Number.isFinite(args.zScoreWindowDays)
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailZscoreCustom(params)
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
    args.zScoreWindowDays,
    args.lookbackDays,
    args.fieldName,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Chart helpers — the main chart IS the z-score series.
// ---------------------------------------------------------------------------

/** Chart points from the CANONICAL ``time_series_zscore`` field, falling
 *  back to the legacy ``time_series`` alias (identical payload by
 *  construction — see the backend ZscoreCustomOutput docstring on why
 *  both exist).  Nulls (rolling-stat warm-up gaps) map to NaN per the
 *  shared-chart convention. */
export function zscoreSeriesPoints(
  data: ZscoreCustomOutput | null,
): ChartPoint[] {
  const series = data?.time_series_zscore ?? data?.time_series;
  return (series?.rows ?? []).map((r) => ({
    date: r.date,
    value: r.value ?? NaN,
  }));
}

/** Reference bands at the SHARED regime thresholds (±1.5σ elevated,
 *  ±2σ extreme).  Unlike level-unit tools (breakeven, real yield) the
 *  chart's y-axis is ALREADY in z units, so the bands are fixed
 *  constants — no mean/std re-estimation from the displayed window. */
export const ZSCORE_REFERENCE_BANDS: ReadonlyArray<ReferenceBand> = [
  { value: 2.0, label: '+2σ', tone: 'extreme', style: 'dashed' },
  { value: 1.5, label: '+1.5σ', tone: 'elevated', style: 'dashed' },
  { value: -1.5, label: '-1.5σ', tone: 'elevated', style: 'dashed' },
  { value: -2.0, label: '-2σ', tone: 'extreme', style: 'dashed' },
];

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *    1. YIELD              (latest raw level, %, neutral, primary)
 *    2. Z-SCORE (<W>D)     (signed value + regime caption, toneForZScore)
 *    3. OBS                (displayed-window observation count)
 *  THESIS Q3 documents why these vs alternatives (e.g. percentile —
 *  not on this tool's wire; daily change — not on this tool's wire). */
export function compactKPIs(
  data: ZscoreCustomOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'YIELD',
      value: unsignedFixed(cm.current_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
    },
    {
      label: `Z-SCORE (${cm.z_score_window_days_used}D)`,
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
    {
      label: 'OBS',
      value: observationCount(cm.observation_count),
      tone: 'neutral',
    },
  ];
}

/** The extended view's FULL KPI strip — headline pair plus the echoed
 *  rolling-stat parameters (window / min_periods / ddof are WIRE fields,
 *  surfaced for transparency per the backend schema's "echoed for
 *  transparency" contract). */
export function extendedKPIs(
  data: ZscoreCustomOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'CURRENT YIELD',
      value: unsignedFixed(cm.current_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `Z-SCORE (${cm.z_score_window_days_used}D)`,
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
    {
      label: 'WINDOW USED',
      value: String(cm.z_score_window_days_used),
      unit: 'd',
      tone: 'neutral',
    },
    {
      label: 'MIN PERIODS',
      value: String(cm.z_score_min_periods_used),
      tone: 'neutral',
      caption: 'YAML-locked',
    },
    {
      label: 'DDOF',
      value: String(cm.z_score_ddof_used),
      tone: 'neutral',
      caption: cm.z_score_ddof_used === 1 ? 'sample std' : 'population std',
    },
    {
      label: 'OBSERVATIONS',
      value: observationCount(cm.observation_count),
      tone: 'neutral',
    },
  ];
}

/** '—' placeholder triple shown while the compact fetch is in flight. */
export const COMPACT_PLACEHOLDER_KPIS: ReadonlyArray<KPIDescriptor> = [
  { label: 'YIELD', value: '—' },
  { label: 'Z-SCORE', value: '—' },
  { label: 'OBS', value: '—' },
];

// ---------------------------------------------------------------------------
// Compact caveat — threaded from the response's echoed parameters (P5),
// with a static fallback only while the fetch is in flight.
// ---------------------------------------------------------------------------

export const ZSCORE_CAVEAT_FALLBACK =
  'Stretch vs own trailing window — not a directional signal.';

/** One-line methodology caveat for the compact footer.  The rolling-stat
 *  parameters come off the WIRE (z_score_window_days_used /
 *  z_score_min_periods_used), never hardcoded — the YAML's min_periods
 *  could change without a frontend release. */
export function compactCaveat(data: ZscoreCustomOutput | null): string {
  if (!data) return ZSCORE_CAVEAT_FALLBACK;
  const cm = data.current_metrics;
  return (
    `${cm.z_score_window_days_used}d rolling z-score · min_periods `
    + `${cm.z_score_min_periods_used} regardless of window (YAML-locked). `
    + `Stretch vs own history, not a directional signal.`
  );
}

// ---------------------------------------------------------------------------
// Stretch-context builder
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: ZscoreCustomOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  return {
    zScoreRegime: {
      value: cm.current_z_score,
      regime,
      bands: { amber: 1.5, coral: 2.0 },
    },
    interpretation: interpretationFor(regime, cm.current_z_score, cm.z_score_window_days_used),
  };
}

function interpretationFor(
  regime: 'Normal' | 'Elevated' | 'Extreme',
  z: number,
  windowDays: number,
): string {
  const direction = z > 0 ? 'above' : 'below';
  if (regime === 'Extreme') {
    return (
      `The yield sits at an extreme stretch ${direction} its ${windowDays}d rolling mean.  ` +
      `Remember the z-score is a mean-reversion DIAGNOSTIC against the chosen window, ` +
      `not a directional signal — a shorter window re-anchors faster after regime shifts.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `The yield is elevated ${direction} its ${windowDays}d rolling mean.  ` +
      `Cross-check against a longer window before reading this as stretch — ` +
      `the window length is the central methodological choice of this tool.`
    );
  }
  return (
    `The yield is within its ${windowDays}d rolling norm; ` +
    `no notable stretch in either direction at this window length.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows — every parameter row threads the WIRE echo fields
// (P5: methodology from the response, never TSX literals).
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: ZscoreCustomOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const series = data.time_series_zscore ?? data.time_series;
  return [
    {
      label: 'Statistic',
      value: series?.description ?? 'Rolling z-score of a single sovereign yield series.',
    },
    {
      label: 'Window (central knob)',
      value: `${cm.z_score_window_days_used} trading days, set per request — defines what this run IS`,
    },
    {
      label: 'Min periods',
      value: `${cm.z_score_min_periods_used} valid observations before a value prints (YAML-locked; fixed regardless of window)`,
    },
    {
      label: 'Std ddof',
      value: `${cm.z_score_ddof_used} (${cm.z_score_ddof_used === 1 ? 'sample / Bessel-corrected' : 'population'})`,
    },
    {
      label: 'Display window',
      value: `${effectiveLookbackDays} calendar days · ${cm.observation_count} trading-day observations after cleaning + ffill`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (Bloomberg observation field; backend default when omitted)`,
    },
    {
      label: 'Units',
      value: series ? series.units : 'z_score',
    },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixed, unsignedFixed };
