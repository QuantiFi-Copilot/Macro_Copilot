// ============================================================================
// betaAdjustedSpreadShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for ``calculate_beta_adjusted_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the breakeven pilot's
// breakevenShared.ts).  This module knows the desk semantics the shared
// grammar must not (FP13): the residual is ``target − β·regressor − α``
// in bps, and its SIGN CONVENTION is part of the schema contract —
// positive ⇒ the target yield sits ABOVE the regression-implied fair
// value, i.e. the target is CHEAP vs the hedge line; negative ⇒ RICH.
// The residual z-score (252d rolling in V1, YAML-locked) is the stretch
// read a PM acts on.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint ``GET /api/v1/rates/detail/beta-adjusted-spread``; the
// compact view just renders less).
//
// Flat-param wire convention (documented here, the single source)
// ---------------------------------------------------------------
// Build surfaces receive ``params: Record<string, string>`` (URL-flat).
// This tool's Input is already flat scalars, so the params mirror the
// endpoint's query params 1:1 — no list joining needed:
//
//   target_curve_family=IT_BTP & target_tenor=10Y
//   & regressor_curve_family=DE_BUND & regressor_tenor=10Y
//   & regression_window_days=60 & lookback_days=730 & field_name=...
//
// Ask-handoff contexts may carry the legacy nested ``target_spec`` /
// ``hedge_spec`` objects only in ``decoded.paramsStructured`` —
// ``resolveBetaAdjustedSpreadParams`` falls back to those before the
// defaults so an Ask handoff lands hydrated.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailBetaAdjustedSpread,
  type BetaAdjustedSpreadDetailParams,
} from '@/services/ratesApi';
import type { BetaAdjustedSpreadOutput, TimeSeries } from '@/types/rates';
import type {
  ChartPoint,
  KPIDescriptor,
  MethodologyRow,
} from '@/components/shared/build';
import {
  regimeForZScore,
  signedFixed,
  toneForZScore,
  unsignedFixed,
} from '@/components/shared/build';
import {
  modelToneAt,
  qualityLevelForConditionFlag,
  type MetricItem,
  type ModelSeries,
  type QualityLevel,
} from '@/components/shared/build/model';
import {
  toneForZScore as metricToneForZScore,
} from '@/components/build/primitive/PrimitiveMetrics';

// ---------------------------------------------------------------------------
// Param conventions + defaults
// ---------------------------------------------------------------------------

/** Flat-wire defaults — mirror the retired modelMetadata defaults (the
 *  canonical BTP-Bund 10Y hedge, tactical 60d window, 2y of display
 *  history).  Direction convention per the schema: target = peripheral
 *  / cf1, regressor = core / hedge curve. */
export const BETA_ADJUSTED_SPREAD_DEFAULTS = {
  target_curve_family: 'IT_BTP',
  target_tenor: '10Y',
  regressor_curve_family: 'DE_BUND',
  regressor_tenor: '10Y',
  regression_window_days: '60',
  lookback_days: '730',
  field_name: 'YLD_YTM_MID',
} as const;

export interface BetaAdjustedSpreadResolvedParams {
  targetCurveFamily: string;
  targetTenor: string;
  regressorCurveFamily: string;
  regressorTenor: string;
  regressionWindowDays: string;
  lookbackDays: string;
  fieldName: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

interface StructuredSeriesSpec {
  curveFamily: string;
  tenor: string;
}

function seriesSpecFrom(v: unknown): StructuredSeriesSpec | null {
  if (v == null || typeof v !== 'object') return null;
  const o = v as Record<string, unknown>;
  if (typeof o.curve_family === 'string' && typeof o.tenor === 'string') {
    return { curveFamily: o.curve_family, tenor: o.tenor };
  }
  return null;
}

/** Resolve the effective params: flat URL params first, Ask-handoff
 *  structured specs (legacy ``target_spec`` / ``hedge_spec`` naming)
 *  second, defaults last.  Pure — both views call it. */
export function resolveBetaAdjustedSpreadParams(
  params: Record<string, string>,
  structured?: Record<string, unknown>,
): BetaAdjustedSpreadResolvedParams {
  const sTarget = seriesSpecFrom(structured?.target_spec);
  const sHedge = seriesSpecFrom(structured?.hedge_spec)
    ?? seriesSpecFrom(structured?.regressor_spec);

  return {
    targetCurveFamily:
      params.target_curve_family
      || sTarget?.curveFamily
      || BETA_ADJUSTED_SPREAD_DEFAULTS.target_curve_family,
    targetTenor:
      params.target_tenor
      || sTarget?.tenor
      || BETA_ADJUSTED_SPREAD_DEFAULTS.target_tenor,
    regressorCurveFamily:
      params.regressor_curve_family
      || sHedge?.curveFamily
      || BETA_ADJUSTED_SPREAD_DEFAULTS.regressor_curve_family,
    regressorTenor:
      params.regressor_tenor
      || sHedge?.tenor
      || BETA_ADJUSTED_SPREAD_DEFAULTS.regressor_tenor,
    regressionWindowDays:
      params.regression_window_days
      || BETA_ADJUSTED_SPREAD_DEFAULTS.regression_window_days,
    lookbackDays:
      params.lookback_days || BETA_ADJUSTED_SPREAD_DEFAULTS.lookback_days,
    fieldName: params.field_name || BETA_ADJUSTED_SPREAD_DEFAULTS.field_name,
    asOfDate: params.as_of_date || undefined,
  };
}

/** Re-encode resolved params back onto the flat wire shape. */
export function flattenBetaAdjustedSpreadParams(
  p: BetaAdjustedSpreadResolvedParams,
): Record<string, string> {
  return {
    target_curve_family: p.targetCurveFamily,
    target_tenor: p.targetTenor,
    regressor_curve_family: p.regressorCurveFamily,
    regressor_tenor: p.regressorTenor,
    regression_window_days: p.regressionWindowDays,
    lookback_days: p.lookbackDays,
    field_name: p.fieldName,
    // Only round-trip as_of_date when explicitly set — empty → omitted so
    // the wire stays byte-identical to the latest-data default.
    ...(p.asOfDate ? { as_of_date: p.asOfDate } : {}),
  };
}

// ---------------------------------------------------------------------------
// Data hook — single source for BOTH views (rendering_density.md §1.1)
// ---------------------------------------------------------------------------

export interface UseBetaAdjustedSpreadResult {
  data: BetaAdjustedSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useBetaAdjustedSpreadData(
  p: BetaAdjustedSpreadResolvedParams,
): UseBetaAdjustedSpreadResult {
  const [data, setData] = useState<BetaAdjustedSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (
      !p.targetCurveFamily
      || !p.targetTenor
      || !p.regressorCurveFamily
      || !p.regressorTenor
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    const params: BetaAdjustedSpreadDetailParams = {
      target_curve_family: p.targetCurveFamily,
      target_tenor: p.targetTenor,
      regressor_curve_family: p.regressorCurveFamily,
      regressor_tenor: p.regressorTenor,
      regression_window_days: Number(p.regressionWindowDays),
      lookback_days: Number(p.lookbackDays),
      field_name: p.fieldName || undefined,
      as_of_date: p.asOfDate || undefined,
    };
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailBetaAdjustedSpread(params)
      .then((out) => {
        if (cancelled) return;
        setData(out);
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
    p.targetCurveFamily,
    p.targetTenor,
    p.regressorCurveFamily,
    p.regressorTenor,
    p.regressionWindowDays,
    p.lookbackDays,
    p.fieldName,
    p.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Output → grammar-shape mappers (FP9 — values pass through unchanged)
// ---------------------------------------------------------------------------

/** Single named series → one-line ``ModelSeries[]``. */
export function singleModelSeries(
  ts: TimeSeries | undefined,
  key: string,
  label: string,
  toneIndex: number,
): ModelSeries[] {
  if (!ts || ts.rows.length === 0) return [];
  return [{ key, label, rows: ts.rows, tone: modelToneAt(toneIndex) }];
}

/** Residual z-score series as compact-card chart points (nulls → NaN
 *  per the MiniChart contract). */
export function residualZChartPoints(
  data: BetaAdjustedSpreadOutput | null,
): ChartPoint[] {
  const ts = data?.time_series_residual_z_score;
  if (!ts) return [];
  return ts.rows.map((r) => ({ date: r.date, value: r.value ?? NaN }));
}

// ---------------------------------------------------------------------------
// Tone — the residual's cheap/rich read (sign convention from the schema)
// ---------------------------------------------------------------------------

/** Positive residual ⇒ target CHEAP vs the hedge line (mint); negative
 *  ⇒ RICH (coral).  MetricItem tone domain. */
export function metricToneForResidual(
  bps: number | null | undefined,
): MetricItem['tone'] {
  if (bps == null || Number.isNaN(bps) || bps === 0) return 'neutral';
  return bps > 0 ? 'positive' : 'negative';
}

/** One-word cheap/rich caption for the residual cell. */
export function cheapRichCaption(
  bps: number | null | undefined,
): string | undefined {
  if (bps == null || Number.isNaN(bps) || bps === 0) return undefined;
  return bps > 0 ? 'Target cheap vs hedge' : 'Target rich vs hedge';
}

// ---------------------------------------------------------------------------
// KPI builders
// ---------------------------------------------------------------------------

/** Hero strip (extended view).  Residual z-score is the emphasised
 *  desk read (stretch vs own history), then the bps residual with the
 *  cheap/rich tone, then the hedge ratio and fit quality. */
export function heroMetrics(data: BetaAdjustedSpreadOutput): MetricItem[] {
  const cm = data.current_metrics;
  return [
    {
      label: `RESIDUAL Z (${cm.z_score_window_days_used}D)`,
      value: signedFixed(cm.current_residual_z_score, 2),
      tone: metricToneForZScore(cm.current_residual_z_score),
      emphasis: true,
      subtext: regimeForZScore(cm.current_residual_z_score),
    },
    {
      label: 'RESIDUAL',
      value: signedFixed(cm.current_residual_bps, 1),
      unit: 'bp',
      tone: metricToneForResidual(cm.current_residual_bps),
      subtext: cheapRichCaption(cm.current_residual_bps),
    },
    {
      label: 'β (HEDGE RATIO)',
      value: unsignedFixed(cm.current_beta, 3),
    },
    {
      label: 'R² (IN-WINDOW)',
      value: unsignedFixed(cm.current_r_squared, 3),
    },
  ];
}

/** Diagnostics strip (extended view) — every fit knob echoed by the
 *  backend (P5). */
export function diagnosticsMetrics(data: BetaAdjustedSpreadOutput): MetricItem[] {
  const cm = data.current_metrics;
  return [
    {
      label: 'WINDOW USED',
      value: String(cm.regression_window_days_used),
      unit: 'd',
    },
    {
      label: 'MIN PERIODS',
      value: String(cm.regression_min_periods_used),
    },
    {
      label: 'Z-SCORE WINDOW',
      value: String(cm.z_score_window_days_used),
      unit: 'd',
    },
    {
      label: 'INTERCEPT',
      value: cm.add_constant_used ? 'Included' : 'Excluded',
    },
    {
      label: 'OBSERVATIONS',
      value: String(cm.observation_count),
    },
  ];
}

/** Latest-fit condition read for the QualityBadge. */
export function conditionQuality(data: BetaAdjustedSpreadOutput | null): {
  level: QualityLevel;
  label: string;
  note: string;
} {
  const flag = data?.current_metrics.current_condition_flag;
  const level = qualityLevelForConditionFlag(flag);
  return {
    level,
    label: level === 'ok' ? 'CONDITION OK' : 'NEAR-SINGULAR',
    note:
      level === 'ok'
        ? 'Latest design matrix passed the condition-number threshold.'
        : 'Latest fit was suppressed — the regressor was effectively constant in-window (near-singular design matrix); do not lean on the latest β / residual.',
  };
}

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *    1. RESIDUAL Z      — the stretch read (the desk signal)
 *    2. RESIDUAL (BPS)  — today's miss in tradable units
 *    3. β               — the hedge ratio behind the read
 *  THESIS Q3 documents why these vs alternatives (α, R², spread level). */
export function compactKPIs(
  data: BetaAdjustedSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'RESIDUAL Z',
      value: signedFixed(cm.current_residual_z_score, 2),
      tone: toneForZScore(cm.current_residual_z_score),
      caption: regimeForZScore(cm.current_residual_z_score),
      emphasis: 'primary',
    },
    {
      label: 'RESIDUAL',
      value: signedFixed(cm.current_residual_bps, 1),
      unit: 'bp',
      tone:
        cm.current_residual_bps == null || cm.current_residual_bps === 0
          ? 'neutral'
          : cm.current_residual_bps > 0
            ? 'positive'
            : 'negative',
    },
    {
      label: 'β',
      value: unsignedFixed(cm.current_beta, 3),
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Methodology + caveat copy
// ---------------------------------------------------------------------------

/** Compact-footer caveat — the schema's sign convention, surfaced where
 *  it cannot be missed (P5). */
export const BETA_ADJUSTED_COMPACT_CAVEAT =
  'Positive = target cheap vs hedge; negative = rich.';

/** Methodology rows, threaded from the response's ``*_used`` echo
 *  fields + ``spread_label`` (P5 — never hardcode what the backend
 *  already discloses).  The cheap/rich sign convention is stated
 *  verbatim — it is part of the schema contract. */
export function buildMethodologyRows(
  data: BetaAdjustedSpreadOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  return [
    {
      label: 'Spread',
      value: cm.spread_label,
    },
    {
      label: 'Construction',
      value: `residual = ${cm.target_curve_family} ${cm.target_tenor} − β·(${cm.regressor_curve_family} ${cm.regressor_tenor}) − α, ×100 for bps`,
    },
    {
      label: 'Sign convention',
      value:
        'Positive residual ⇒ target yield ABOVE the regression-implied fair value (target CHEAP vs the hedge line); negative ⇒ target RICH.',
    },
    {
      label: 'Hedge-ratio window',
      value: `${cm.regression_window_days_used} trading-day rows per rolling fit (the central knob), min periods ${cm.regression_min_periods_used}`,
    },
    {
      label: 'Residual z-score',
      value: `${cm.z_score_window_days_used}d rolling window on the bps residual (YAML-locked in V1)`,
    },
    {
      label: 'Intercept',
      value: cm.add_constant_used
        ? 'Included (α reported in yield-percent)'
        : 'Excluded (regression through the origin)',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (both legs)`,
    },
    {
      label: 'Quality gate',
      value:
        'Fits where the design matrix exceeds the YAML condition-number threshold are suppressed and flagged (condition_flag = 1).',
    },
    {
      label: 'Direction',
      value:
        'Pass target = peripheral / cf1, regressor = core / cf2 (e.g. target BTP, regressor Bund) to match cross_market_spread’s cf1 − cf2 convention.',
    },
  ];
}

// ---------------------------------------------------------------------------
// Re-export the formatting helpers per-tool wrappers also need
// (single import line in the surface files).
// ---------------------------------------------------------------------------

export { regimeForZScore, signedFixed, toneForZScore, unsignedFixed };
