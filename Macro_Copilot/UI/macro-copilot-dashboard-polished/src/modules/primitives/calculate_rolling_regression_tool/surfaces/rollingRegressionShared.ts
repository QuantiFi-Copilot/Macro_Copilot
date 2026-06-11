// ============================================================================
// rollingRegressionShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for ``calculate_rolling_regression_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the breakeven pilot's
// breakevenShared.ts).  This module knows what a rolling OLS beta IS —
// the trailing-window partial elasticity of the target on a regressor —
// and what a condition_flag of 1 means (near-singular design matrix;
// the fit was suppressed).  The shared grammar components at
// @/components/shared/build/model do not (FP13).
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint ``GET /api/v1/rates/detail/rolling-regression``; the compact
// view just renders less).  The Output→grammar-shape mappers + KPI
// builders + caveat copy live here, in ONE place.
//
// Flat-param wire convention (documented here, the single source)
// ---------------------------------------------------------------
// Build surfaces receive ``params: Record<string, string>`` (URL-flat).
// The regressor list — a List[SeriesSpec] on the backend Input — is
// carried as TWO COMMA-JOINED strings, paired index-wise:
//
//   target_curve_family=UST & target_tenor=10Y
//   & regressor_curve_families=DE_BUND,UK_GILT
//   & regressor_tenors=10Y,10Y
//   & regression_window_days=60 & lookback_days=730 & field_name=...
//
// ``parseListParam`` splits on ','.  The typed-detail endpoint takes the
// SAME names as REPEATED query params (FastAPI ``List[str]``), which
// ``fetchDetailRollingRegression`` re-expands.  Ask-handoff contexts
// carry the nested ``target_spec`` / ``regressor_specs`` objects only in
// ``decoded.paramsStructured`` (the flat dict drops them) —
// ``resolveRollingRegressionParams`` falls back to those before the
// defaults so an Ask handoff lands hydrated.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailRollingRegression,
  type RollingRegressionDetailParams,
} from '@/services/ratesApi';
import type { RollingRegressionOutput, TimeSeries } from '@/types/rates';
import type {
  ChartPoint,
  KPIDescriptor,
  MethodologyRow,
} from '@/components/shared/build';
import { signedFixed, unsignedFixed } from '@/components/shared/build';
import {
  modelToneAt,
  qualityLevelForConditionFlag,
  type MetricItem,
  type ModelSeries,
  type QualityLevel,
} from '@/components/shared/build/model';

// ---------------------------------------------------------------------------
// Param conventions + defaults
// ---------------------------------------------------------------------------

/** Flat-wire defaults — mirror the retired modelMetadata defaults
 *  (target UST 10Y on UST 5Y, tactical 60d window, 2y of display
 *  history) so deep links that predate the dual-view migration land on
 *  the same fit. */
export const ROLLING_REGRESSION_DEFAULTS = {
  target_curve_family: 'UST',
  target_tenor: '10Y',
  regressor_curve_families: 'UST',
  regressor_tenors: '5Y',
  regression_window_days: '60',
  lookback_days: '730',
  field_name: 'YLD_YTM_MID',
} as const;

/** Max regressor slots the controls strip exposes.  The backend accepts
 *  any N ≥ 1; three paired selects cover the desk's realistic use
 *  (THESIS Q4 documents the trigger for widening). */
export const MAX_REGRESSOR_SLOTS = 3;

/** Split a comma-joined list param.  Returns null (NOT []) for a
 *  missing/empty param so callers can distinguish "absent → fall back"
 *  from "present but empty". */
export function parseListParam(raw: string | undefined): string[] | null {
  if (raw == null || raw.trim() === '') return null;
  const items = raw.split(',').map((s) => s.trim()).filter(Boolean);
  return items.length > 0 ? items : null;
}

/** Join back to the comma-joined wire form. */
export function joinListParam(items: ReadonlyArray<string>): string {
  return items.join(',');
}

export interface RollingRegressionResolvedParams {
  targetCurveFamily: string;
  targetTenor: string;
  /** Paired index-wise with ``regressorTenors`` — always equal length
   *  (zipped to the shorter list on a malformed deep link; the honest
   *  fix is upstream, but a silent 422 on every render is worse). */
  regressorCurveFamilies: string[];
  regressorTenors: string[];
  regressionWindowDays: string;
  lookbackDays: string;
  fieldName: string;
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

function seriesSpecListFrom(v: unknown): StructuredSeriesSpec[] {
  if (!Array.isArray(v)) return [];
  return v
    .map(seriesSpecFrom)
    .filter((s): s is StructuredSeriesSpec => s != null);
}

/** Resolve the effective params: flat URL params first, Ask-handoff
 *  structured specs second, defaults last.  Pure — both views call it
 *  with whatever they were handed. */
export function resolveRollingRegressionParams(
  params: Record<string, string>,
  structured?: Record<string, unknown>,
): RollingRegressionResolvedParams {
  const sTarget = seriesSpecFrom(structured?.target_spec);
  const sRegressors = seriesSpecListFrom(structured?.regressor_specs);

  const targetCurveFamily =
    params.target_curve_family
    || sTarget?.curveFamily
    || ROLLING_REGRESSION_DEFAULTS.target_curve_family;
  const targetTenor =
    params.target_tenor
    || sTarget?.tenor
    || ROLLING_REGRESSION_DEFAULTS.target_tenor;

  let families =
    parseListParam(params.regressor_curve_families)
    ?? (sRegressors.length > 0 ? sRegressors.map((r) => r.curveFamily) : null)
    ?? parseListParam(ROLLING_REGRESSION_DEFAULTS.regressor_curve_families)!;
  let tenors =
    parseListParam(params.regressor_tenors)
    ?? (sRegressors.length > 0 ? sRegressors.map((r) => r.tenor) : null)
    ?? parseListParam(ROLLING_REGRESSION_DEFAULTS.regressor_tenors)!;

  // Pairing guard — the endpoint 422s on a length mismatch; zip to the
  // shorter list so a hand-mangled deep link still renders something.
  const n = Math.min(families.length, tenors.length);
  families = families.slice(0, n);
  tenors = tenors.slice(0, n);

  return {
    targetCurveFamily,
    targetTenor,
    regressorCurveFamilies: families,
    regressorTenors: tenors,
    regressionWindowDays:
      params.regression_window_days
      || ROLLING_REGRESSION_DEFAULTS.regression_window_days,
    lookbackDays:
      params.lookback_days || ROLLING_REGRESSION_DEFAULTS.lookback_days,
    fieldName: params.field_name || ROLLING_REGRESSION_DEFAULTS.field_name,
  };
}

/** Re-encode resolved params back onto the flat wire shape (URL /
 *  compact-card params).  Inverse of ``resolveRollingRegressionParams``
 *  modulo the structured fallback. */
export function flattenRollingRegressionParams(
  p: RollingRegressionResolvedParams,
): Record<string, string> {
  return {
    target_curve_family: p.targetCurveFamily,
    target_tenor: p.targetTenor,
    regressor_curve_families: joinListParam(p.regressorCurveFamilies),
    regressor_tenors: joinListParam(p.regressorTenors),
    regression_window_days: p.regressionWindowDays,
    lookback_days: p.lookbackDays,
    field_name: p.fieldName,
  };
}

// ---------------------------------------------------------------------------
// Data hook — single source for BOTH views (rendering_density.md §1.1)
// ---------------------------------------------------------------------------

export interface UseRollingRegressionResult {
  data: RollingRegressionOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useRollingRegressionData(
  p: RollingRegressionResolvedParams,
): UseRollingRegressionResult {
  const [data, setData] = useState<RollingRegressionOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Stable dep keys for the list params (array identity churns per render).
  const familiesKey = joinListParam(p.regressorCurveFamilies);
  const tenorsKey = joinListParam(p.regressorTenors);

  useEffect(() => {
    if (
      !p.targetCurveFamily
      || !p.targetTenor
      || p.regressorCurveFamilies.length === 0
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    const params: RollingRegressionDetailParams = {
      target_curve_family: p.targetCurveFamily,
      target_tenor: p.targetTenor,
      regressor_curve_families: p.regressorCurveFamilies,
      regressor_tenors: p.regressorTenors,
      regression_window_days: Number(p.regressionWindowDays),
      lookback_days: Number(p.lookbackDays),
      field_name: p.fieldName || undefined,
    };
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailRollingRegression(params)
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
    familiesKey,
    tenorsKey,
    p.regressionWindowDays,
    p.lookbackDays,
    p.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Output → grammar-shape mappers (FP9 — values pass through unchanged)
// ---------------------------------------------------------------------------

/** ``time_series_betas`` → ``ModelSeries[]``, keyed by regressor label
 *  (the same keys ``current_metrics.current_betas`` uses) so the panel
 *  legend's current-level read lines up.  Order follows
 *  ``regressor_labels`` (= input regressor order); tones follow the
 *  shared model cycle. */
export function betaModelSeries(data: RollingRegressionOutput): ModelSeries[] {
  const labels = data.current_metrics.regressor_labels;
  return data.time_series_betas.map((ts, i) => ({
    key: labels[i] ?? ts.series_name,
    label: labels[i] ?? ts.series_name,
    rows: ts.rows,
    tone: modelToneAt(i),
  }));
}

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

/** First beta series as compact-card chart points (nulls → NaN per the
 *  MiniChart contract). */
export function firstBetaChartPoints(
  data: RollingRegressionOutput | null,
): ChartPoint[] {
  const ts = data?.time_series_betas?.[0];
  if (!ts) return [];
  return ts.rows.map((r) => ({ date: r.date, value: r.value ?? NaN }));
}

// ---------------------------------------------------------------------------
// KPI builders
// ---------------------------------------------------------------------------

/** Hero strip (extended view).  TARGET identity + the per-regressor β
 *  read (each β when ≤2 regressors; first β + count when more — the
 *  full set always lives in the betas panel legend) + R² (emphasis) +
 *  the latest residual. */
export function heroMetrics(data: RollingRegressionOutput): MetricItem[] {
  const cm = data.current_metrics;
  const betas: MetricItem[] =
    cm.regressor_labels.length <= 2
      ? cm.regressor_labels.map((label) => ({
          label: `β · ${label}`,
          value: unsignedFixed(cm.current_betas[label], 3),
        }))
      : [
          {
            label: `β · ${cm.regressor_labels[0]}`,
            value: unsignedFixed(cm.current_betas[cm.regressor_labels[0]], 3),
            subtext: `first of ${cm.regressor_labels.length} regressors`,
          },
        ];
  return [
    { label: 'TARGET', value: cm.target_label },
    ...betas,
    {
      label: 'R² (IN-WINDOW)',
      value: unsignedFixed(cm.current_r_squared, 3),
      emphasis: true,
    },
    {
      label: 'RESIDUAL',
      value: signedFixed(cm.current_residual_pct, 4),
      unit: '%',
    },
  ];
}

/** Diagnostics strip (extended view) — every fit knob echoed by the
 *  backend (P5: the conventions used THIS run, never TSX literals). */
export function diagnosticsMetrics(data: RollingRegressionOutput): MetricItem[] {
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
export function conditionQuality(data: RollingRegressionOutput | null): {
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
        : 'Latest design matrix was near-singular (collinear regressors) — coefficients for that row were suppressed and should not be leaned on.',
  };
}

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *    1. β (first regressor)  — the hedge-ratio read
 *    2. R²                   — is the linear fit even holding?
 *    3. RESIDUAL             — today's miss vs the fitted line
 *  THESIS Q3 documents why these vs alternatives (alpha, observation
 *  count, per-regressor β set). */
export function compactKPIs(
  data: RollingRegressionOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const firstLabel = cm.regressor_labels[0] ?? '—';
  return [
    {
      label: `β · ${firstLabel}`,
      value: unsignedFixed(cm.current_betas[firstLabel], 3),
      tone: 'neutral',
      emphasis: 'primary',
    },
    {
      label: 'R²',
      value: unsignedFixed(cm.current_r_squared, 3),
      tone: 'neutral',
    },
    {
      label: 'RESIDUAL',
      value: signedFixed(cm.current_residual_pct, 4),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Methodology + caveat copy
// ---------------------------------------------------------------------------

/** Compact-footer caveat — the one honest line a PM must not skip.
 *  Lifted from the schema's condition_flag semantics. */
export const ROLLING_REGRESSION_COMPACT_CAVEAT =
  'Trailing-window OLS — betas move with the window; near-singular fits are suppressed.';

/** Methodology rows, threaded from the response's ``*_used`` echo
 *  fields (P5 — never hardcode what the backend already discloses). */
export function buildMethodologyRows(
  data: RollingRegressionOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  return [
    {
      label: 'Model',
      value: `Rolling OLS of ${cm.target_label} on ${cm.regressor_labels.join(' + ')} (numpy.linalg.lstsq per window)`,
    },
    {
      label: 'Window',
      value: `${cm.regression_window_days_used} trading-day rows per fit (the central knob), min periods ${cm.regression_min_periods_used}`,
    },
    {
      label: 'Intercept',
      value: cm.add_constant_used
        ? 'Included (α reported in yield-percent)'
        : 'Excluded (regression through the origin)',
    },
    {
      label: 'Units',
      value:
        'β unitless (yield-percent per yield-percent) · α and residual in yield-percent · R² in [0, 1]',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (target and every regressor leg)`,
    },
    {
      label: 'Quality gate',
      value:
        'Rows where the design matrix exceeds the YAML condition-number threshold are suppressed and flagged (condition_flag = 1) — mask those regions from interpretation.',
    },
    {
      label: 'Reading β',
      value:
        'Each β is the partial elasticity of the target on that regressor with the others held flat; β crossing 1 means one-for-one moves inside the window.',
    },
    {
      label: 'Reading R²',
      value:
        'A sharp drop in rolling R² is a structural-break tell — the linear hedge ratio is losing predictive power for that horizon.',
    },
  ];
}

// ---------------------------------------------------------------------------
// Re-export the formatting helpers per-tool wrappers also need
// (single import line in the surface files).
// ---------------------------------------------------------------------------

export { signedFixed, unsignedFixed };
