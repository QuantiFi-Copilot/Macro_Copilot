// ============================================================================
// halfLifeShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``calculate_half_life_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the pilot's breakevenShared.ts).
// This module knows what an OU / AR(1) half-life IS — that ``β < 0`` is a
// strict structural test (not a stationarity hypothesis test), that the
// half-life CI is a delta-method rough-sizing tool, and that the native
// units of every ``*_native`` scalar come from ``series_units`` — the
// shared grammar components do not.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint at /api/v1/rates/detail/half-life; the compact view just
// renders less).  The KPI builders + formatting + tone logic live here,
// in ONE place.
//
// Wire honesty (FP9): every number rendered comes from the backend
// response unchanged — no unit conversion, no recomputation.  The
// backend already applies its own rounding conventions
// (``half_life_round_decimals`` / ``ou_beta_round_decimals``); the
// formatters here only fix display decimals.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailHalfLife,
  type HalfLifeDetailParams,
} from '@/services/ratesApi';
import type { HalfLifeMetrics, HalfLifeOutput } from '@/types/rates';
import {
  signedFixed,
  unsignedFixed,
  type MethodologyRow,
  type ReferenceChip,
} from '@/components/shared/build';
import type {
  DecompositionEntry,
  MatrixRow,
  MetricItem,
  QualityLevel,
} from '@/components/shared/build/model';

// ---------------------------------------------------------------------------
// Defaults — single source of truth for both views + module.ts.
// Mirror the GET /detail/half-life bridge defaults (lookback_days=1825,
// field_name → YAML default 'YLD_YTM_MID').
// ---------------------------------------------------------------------------

export const HALF_LIFE_DEFAULTS = {
  curve_family: 'UST',
  tenor: '10Y',
  lookback_days: '1825',
  field_name: 'YLD_YTM_MID',
} as const;

/** Default second leg when the user flips the mode toggle to PAIR —
 *  UST−Bund is the canonical cross-market spread starting point. */
export const PAIR_SECOND_LEG_DEFAULT = 'DE_BUND';

/** Lookback options honouring the backend bound (ge=252, le=7300
 *  CALENDAR days) and the desk guidance in HalfLifeInput.lookback_days:
 *  OU CIs at ~1 year of observations are very wide; ~5 years is the
 *  default. */
export const HALF_LIFE_LOOKBACK_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = [
  { value: '730', label: '2Y · 730d (wide CI)' },
  { value: '1095', label: '3Y · 1095d' },
  { value: '1825', label: '5Y · 1825d (default)' },
  { value: '2555', label: '7Y · 2555d' },
  { value: '3650', label: '10Y · 3650d' },
];

export const HALF_LIFE_FIELD_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = [
  { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID' },
  { value: 'YLD_YTM_BID', label: 'YLD_YTM_BID' },
  { value: 'YLD_YTM_ASK', label: 'YLD_YTM_ASK' },
];

// ---------------------------------------------------------------------------
// Mode — the bridge's two GET-able input shapes.  ``pasted_series`` (the
// tool's third input variant) is intentionally NOT offered here: caller-
// supplied rows don't fit query params, so that path stays on
// MCP / the orchestrator's generic run endpoint (honest note threaded
// into the methodology card below).
// ---------------------------------------------------------------------------

export type HalfLifeMode = 'single' | 'pair';

export function modeForParams(params: Record<string, string>): HalfLifeMode {
  return params.curve_family_2 ? 'pair' : 'single';
}

export const HALF_LIFE_MODE_OPTIONS: ReadonlyArray<{
  value: HalfLifeMode;
  label: string;
}> = [
  { value: 'single', label: 'Single series' },
  { value: 'pair', label: 'Pair spread (cf1 − cf2)' },
];

// ---------------------------------------------------------------------------
// Unit-label helper — ``series_units`` is the closed TimeSeriesUnits enum
// from shared.schemas; it drives the display unit of every ``*_native``
// scalar (the consumer MUST read it per the backend schema docstring).
// ---------------------------------------------------------------------------

export function unitLabelForSeriesUnits(units: string | undefined): string {
  switch (units) {
    case 'percent':
      return '%';
    case 'bps':
      return 'bp';
    case 'z_score':
      return 'z';
    case 'pct_rank':
      return 'th';
    case 'ratio':
    case 'factor_level':
    case 'count':
      return '';
    default:
      // Unknown enum value — render the raw string rather than hide it
      // (P6: never silently render an unknown state as something else).
      return units ?? '';
  }
}

// ---------------------------------------------------------------------------
// Data hook — single-source hook used by BOTH views.
// ---------------------------------------------------------------------------

export interface UseHalfLifeArgs {
  curveFamily: string;
  tenor: string;
  /** When set → PAIR mode: half-life of (curveFamily − curveFamily2)
   *  at ``tenor`` in bps. */
  curveFamily2?: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseHalfLifeResult {
  data: HalfLifeOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Fetches the typed-detail endpoint; re-fetches when any input changes. */
export function useHalfLifeData(args: UseHalfLifeArgs): UseHalfLifeResult {
  const [data, setData] = useState<HalfLifeOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: HalfLifeDetailParams = {
    curve_family: args.curveFamily,
    tenor: args.tenor,
    curve_family_2: args.curveFamily2,
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
    fetchDetailHalfLife(params)
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
    args.curveFamily2,
    args.lookbackDays,
    args.fieldName,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Confidence / half-life formatting helpers.
// ---------------------------------------------------------------------------

/** 0.95 → "95%".  The level is the backend's YAML-locked echo
 *  (``confidence_level_used``) — threaded, never hardcoded (P5). */
export function formatConfidencePct(
  level: number | null | undefined,
): string {
  if (level == null || !Number.isFinite(level)) return '—';
  return `${Math.round(level * 100)}%`;
}

/** Half-life CI subtext for the hero KPI — honest em-dash absence when
 *  the CI is undefined (β std-error undefined / half-life None). */
export function halfLifeCiSubtext(cm: HalfLifeMetrics): string | undefined {
  if (cm.half_life_ci_lower_days == null || cm.half_life_ci_upper_days == null) {
    return undefined;
  }
  return `CI [${unsignedFixed(cm.half_life_ci_lower_days, 1)} – ${unsignedFixed(cm.half_life_ci_upper_days, 1)}]d @ ${formatConfidencePct(cm.confidence_level_used)}`;
}

/** The honest one-liner for a None half-life — mirrors the backend
 *  schema docstring's three causes verbatim, because the wire does not
 *  distinguish which one fired. */
export const HALF_LIFE_UNDEFINED_NOTE =
  'Undefined: β ≥ 0 (no reversion), β ≤ −1 (oscillating divergence), or |β| below the stability floor.';

// ---------------------------------------------------------------------------
// Quality — map ``is_mean_reverting`` onto the closed QualityLevel
// vocabulary for the shared QualityBadge (ok when true, degraded when
// false per the migration contract).
// ---------------------------------------------------------------------------

export interface MeanReversionQuality {
  level: QualityLevel;
  label: string;
  note: string;
}

export function meanReversionQuality(
  cm: HalfLifeMetrics,
): MeanReversionQuality {
  if (cm.is_mean_reverting) {
    return {
      level: 'ok',
      label: 'mean-reverting',
      note: 'β < 0 — strict structural test only; NOT a stationarity hypothesis test (ADF/KPSS is a sibling tool).',
    };
  }
  return {
    level: 'degraded',
    label: 'not mean-reverting',
    note: 'β ≥ 0 — random walk / divergent under the structural test; half-life and long-run mean are undefined.',
  };
}

// ---------------------------------------------------------------------------
// KPI builders
// ---------------------------------------------------------------------------

/** Extended hero — the four numbers a PM reads first off an OU fit:
 *    1. HALF-LIFE        (trading days; emphasis; honest '—' + note when
 *                         not mean-reverting)
 *    2. MEAN-REVERTING?  (✓/✗, mint/coral)
 *    3. CURRENT DEVIATION (native units from series_units; tone by sign)
 *    4. LONG-RUN MEAN    (native units)
 *  THESIS Q3 documents why these vs alternatives (β, R²). */
export function heroKpis(data: HalfLifeOutput): MetricItem[] {
  const cm = data.current_metrics;
  const unit = unitLabelForSeriesUnits(cm.series_units);
  return [
    {
      label: 'HALF-LIFE',
      value:
        cm.half_life_days != null ? unsignedFixed(cm.half_life_days, 1) : '—',
      unit: cm.half_life_days != null ? 'days' : undefined,
      tone: 'neutral',
      emphasis: true,
      subtext:
        cm.half_life_days != null
          ? halfLifeCiSubtext(cm)
          : HALF_LIFE_UNDEFINED_NOTE,
    },
    {
      label: 'MEAN-REVERTING?',
      value: cm.is_mean_reverting ? '✓ Yes' : '✗ No',
      tone: cm.is_mean_reverting ? 'positive' : 'negative',
      subtext: 'Structural test: β < 0',
    },
    {
      label: 'CURRENT DEVIATION',
      value: signedFixed(cm.current_deviation_native, 2),
      unit: cm.current_deviation_native != null ? unit : undefined,
      tone: toneForSign(cm.current_deviation_native),
      subtext: 'current − long-run mean',
    },
    {
      label: 'LONG-RUN MEAN',
      value:
        cm.long_run_mean_native != null
          ? unsignedFixed(cm.long_run_mean_native, 2)
          : '—',
      unit: cm.long_run_mean_native != null ? unit : undefined,
      tone: 'neutral',
      subtext: 'OU −α / β',
    },
  ];
}

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *    1. HALF-LIFE      (trading days, primary emphasis)
 *    2. DEVIATION      (native units, tone by sign)
 *    3. MEAN-REVERTING (✓/✗)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKpis(data: HalfLifeOutput): MetricItem[] {
  const cm = data.current_metrics;
  const unit = unitLabelForSeriesUnits(cm.series_units);
  return [
    {
      label: 'HALF-LIFE',
      value:
        cm.half_life_days != null ? unsignedFixed(cm.half_life_days, 1) : '—',
      unit: cm.half_life_days != null ? 'days' : undefined,
      tone: 'neutral',
      emphasis: true,
    },
    {
      label: 'DEVIATION',
      value: signedFixed(cm.current_deviation_native, 2),
      unit: cm.current_deviation_native != null ? unit : undefined,
      tone: toneForSign(cm.current_deviation_native),
    },
    {
      label: 'MEAN-REVERTING',
      value: cm.is_mean_reverting ? '✓ Yes' : '✗ No',
      tone: cm.is_mean_reverting ? 'positive' : 'negative',
    },
  ];
}

/** Diagnostics strip — fit-health reads, rendered LAST per the
 *  ModelResultLayout zone contract (P5). */
export function diagnosticsKpis(
  data: HalfLifeOutput,
  lookbackDaysEcho: string,
): MetricItem[] {
  const cm = data.current_metrics;
  return [
    {
      label: 'R² (OU FIT)',
      value: cm.r_squared != null ? unsignedFixed(cm.r_squared, 3) : '—',
      tone: 'neutral',
      subtext: cm.r_squared == null ? 'degenerate target (SS_tot = 0)' : undefined,
    },
    {
      label: 'OBSERVATIONS',
      value: String(cm.observation_count),
      tone: 'neutral',
      subtext: 'after dropna',
    },
    {
      label: 'CONFIDENCE LEVEL',
      value: formatConfidencePct(cm.confidence_level_used),
      tone: 'neutral',
      subtext: 'YAML-locked, echoed',
    },
    {
      label: 'LOOKBACK',
      value: `${lookbackDaysEcho}d`,
      tone: 'neutral',
      subtext: 'calendar days requested',
    },
  ];
}

function toneForSign(
  value: number | null | undefined,
): MetricItem['tone'] {
  if (value == null || !Number.isFinite(value)) return 'neutral';
  if (value > 0) return 'positive';
  if (value < 0) return 'negative';
  return 'neutral';
}

// ---------------------------------------------------------------------------
// β block — MatrixTable one-row mapping (β, CI lower, CI upper).
// ---------------------------------------------------------------------------

export const BETA_COLUMNS = ['beta', 'ci_lower', 'ci_upper'];

export const BETA_COLUMN_LABELS: Record<string, string> = {
  beta: 'β',
  ci_lower: 'CI lower',
  ci_upper: 'CI upper',
};

export function betaMatrixRows(data: HalfLifeOutput): MatrixRow[] {
  const cm = data.current_metrics;
  return [
    {
      key: 'β (Δx on x_{t−1})',
      cells: {
        beta: cm.beta,
        ci_lower: cm.beta_ci_lower,
        ci_upper: cm.beta_ci_upper,
      },
    },
  ];
}

export function betaBlockDescription(data: HalfLifeOutput): string {
  const pct = formatConfidencePct(data.current_metrics.confidence_level_used);
  return (
    `OLS β on Δx_t = α + β · x_{t−1} + ε_t; β < 0 → mean-reverting.  ` +
    `Two-sided ${pct} CI from OLS standard errors — a rough sizing tool, not a hypothesis test.`
  );
}

// ---------------------------------------------------------------------------
// Deviation read — DecompositionBars (mode='signed') entries.  No time
// series exists on the wire (the tool is a pure snapshot by contract,
// pinned by the backend's test_no_time_series_output), so the primary
// zone is a snapshot visual of the one signed quantity the model owns:
// current_value − long_run_mean.
// ---------------------------------------------------------------------------

export function deviationEntries(data: HalfLifeOutput): DecompositionEntry[] {
  const cm = data.current_metrics;
  const q = meanReversionQuality(cm);
  return [
    {
      label: 'Current − mean',
      value: cm.current_deviation_native,
      quality: cm.is_mean_reverting ? undefined : q.level,
      qualityNote: cm.is_mean_reverting ? undefined : q.note,
    },
  ];
}

export function deviationDescription(data: HalfLifeOutput): string {
  const cm = data.current_metrics;
  const unit = unitLabelForSeriesUnits(cm.series_units);
  if (!cm.is_mean_reverting || cm.half_life_days == null) {
    return (
      `Current value ${unsignedFixed(cm.current_value_native, 2)}${unit}.  ` +
      `No mean-reversion read: the fit found no expected half-life for this gap (${HALF_LIFE_UNDEFINED_NOTE.toLowerCase()})`
    );
  }
  return (
    `Current ${unsignedFixed(cm.current_value_native, 2)}${unit} vs long-run mean ` +
    `${cm.long_run_mean_native != null ? unsignedFixed(cm.long_run_mean_native, 2) : '—'}${unit}.  ` +
    `The OU fit expects this gap to halve every ${unsignedFixed(cm.half_life_days, 1)} trading days.  ` +
    `Sign interpretation (rich/cheap) depends on the series — the bar shows direction + magnitude only.`
  );
}

// ---------------------------------------------------------------------------
// Caveats — compact footer one-liner.  The confidence level is the
// backend echo, never a TSX literal (P5).
// ---------------------------------------------------------------------------

export function compactCaveat(data: HalfLifeOutput | null): string {
  if (data == null) return 'AR(1) OLS fit; delta-method CI.';
  return `AR(1) fit; CI at ${formatConfidencePct(data.current_metrics.confidence_level_used)}.`;
}

// ---------------------------------------------------------------------------
// Methodology rows + references — threaded from the response's echo
// fields (confidence_level_used, series_units) and mirroring the
// backend schema docstrings / config.yaml methodology block wording.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: HalfLifeOutput,
  mode: HalfLifeMode,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const pct = formatConfidencePct(cm.confidence_level_used);
  return [
    {
      label: 'Model',
      value:
        'Discretized Ornstein-Uhlenbeck / AR(1) via OLS: Δx_t = α + β · x_{t−1} + ε_t',
    },
    {
      label: 'Half-life',
      value:
        '−ln(2) / ln(1+β), trading-day units — emitted only when −1 < β < 0 AND |β| ≥ the YAML stability floor; otherwise honest None.',
    },
    {
      label: 'Mean-reversion test',
      value:
        'Strict structural test (β < 0).  NOT a stationarity hypothesis test (ADF / KPSS) — that is a sibling tool.',
    },
    {
      label: 'Confidence intervals',
      value: `Two-sided ${pct} on β from OLS standard errors; half-life CI via the delta method.  OLS SEs assume homoskedastic, serially uncorrelated residuals — interpret as a rough sizing tool, not a hypothesis test.`,
    },
    {
      label: 'Units',
      value: `${cm.series_units} — native units of every *_native scalar (declared by the wire's series_units; ${mode === 'pair' ? 'pair mode: (cf1 − cf2) × 100 spread in bps' : 'single-series mode: sovereign yield in percent'}).`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (both legs in pair mode)`,
    },
    {
      label: 'Pasted-series mode',
      value:
        'The tool’s third input variant (caller-supplied rows, e.g. a prior tool’s residual) is NOT offered in this builder — rows don’t fit GET query params.  Available via MCP / the orchestrator’s generic run endpoint.',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  // Mirrors config.yaml:methodology.citations.
  return [
    { label: 'Vasicek (1977) JFE 5(2)' },
    { label: 'Hamilton (1994) Ch.17' },
  ];
}

// ---------------------------------------------------------------------------
// Identity helpers
// ---------------------------------------------------------------------------

/** Construct the series label locally while loading (the wire's
 *  ``series_label`` replaces it once data lands — same convention). */
export function constructedSeriesLabel(
  curveFamily: string,
  tenor: string,
  curveFamily2?: string,
): string {
  return curveFamily2
    ? `${curveFamily}-${curveFamily2}_${tenor}`
    : `${curveFamily}_${tenor}`;
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixed, unsignedFixed };
