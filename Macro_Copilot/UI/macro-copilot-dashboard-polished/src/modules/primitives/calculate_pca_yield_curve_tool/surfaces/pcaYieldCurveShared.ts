// ============================================================================
// pcaYieldCurveShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``calculate_pca_yield_curve_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the breakeven pilot's
// breakevenShared.ts, lifted onto the rich-model grammar).  This module
// knows what a "loading", a "variance share", and a "factor score"
// mean; the shared grammar components at @/components/shared/build/model
// do not (FP13).
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint at /api/v1/rates/detail/pca-yield-curve; the compact view
// just renders less).  The Output→grammar mappers, KPI builders, and
// the honesty caveat live here, in ONE place.
//
// FP9 — no client-side statistics: every mapper below is a display-only
// reshaping of backend values (key extraction, ordering, formatting).
// Nothing is recomputed.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailPcaYieldCurve,
  type PcaYieldCurveDetailParams,
} from '@/services/ratesApi';
import type {
  PcaYieldCurveMetrics,
  PcaYieldCurveOutput,
} from '@/types/rates';
import {
  qualityLevelForFlag,
  type DecompositionEntry,
  type MatrixRow,
  type MetricItem,
  type ModelSeries,
  type QualityBadgeProps,
} from '@/components/shared/build/model';
import {
  signedFixed,
  unsignedFixed,
  type KPIDescriptor,
  type MethodologyRow,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Curve-family universe — mirrors PcaYieldCurveInput.curve_family's
// documented playbook universe (rates_agent/sovereign_bonds/tools/
// pca_yield_curve/schemas.py).  The math is curve-family agnostic; the
// option list is the ingested tenor-keyed playbook set.
// ---------------------------------------------------------------------------

export const PCA_CURVE_FAMILY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  // Sovereign benchmarks
  { value: 'UST', label: 'UST (US)' },
  { value: 'DE_BUND', label: 'DE_BUND (Germany)' },
  { value: 'IT_BTP', label: 'IT_BTP (Italy)' },
  { value: 'FR_OAT', label: 'FR_OAT (France)' },
  { value: 'ES_BONO', label: 'ES_BONO (Spain)' },
  { value: 'UK_GILT', label: 'UK_GILT (UK)' },
  { value: 'JGB', label: 'JGB (Japan)' },
  { value: 'CANADA_GOVT', label: 'CANADA_GOVT (Canada)' },
  { value: 'AU_GOVT', label: 'AU_GOVT (Australia)' },
  // OIS curves
  { value: 'USD_SOFR_OIS', label: 'USD_SOFR_OIS' },
  { value: 'EUR_ESTR_OIS', label: 'EUR_ESTR_OIS' },
  { value: 'GBP_SONIA_OIS', label: 'GBP_SONIA_OIS' },
  { value: 'JPY_OIS', label: 'JPY_OIS' },
  { value: 'AUD_OIS', label: 'AUD_OIS' },
  { value: 'CAD_OIS', label: 'CAD_OIS' },
  // Inflation swaps
  { value: 'USD_ZCIS', label: 'USD_ZCIS' },
  { value: 'EUR_ZCIS', label: 'EUR_ZCIS' },
  { value: 'GBP_ZCIS', label: 'GBP_ZCIS' },
  // Linker real-yield curves
  { value: 'USD_TIPS', label: 'USD_TIPS' },
  { value: 'GBP_LINKER', label: 'GBP_LINKER' },
  { value: 'EUR_FR_LINKER', label: 'EUR_FR_LINKER' },
  { value: 'CAD_RRB', label: 'CAD_RRB' },
];

export const PCA_LOOKBACK_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '730', label: '2Y' },
  { value: '1095', label: '3Y' },
  { value: '1825', label: '5Y (default)' },
  { value: '2555', label: '7Y' },
  { value: '3650', label: '10Y' },
];

export const PCA_N_COMPONENTS_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  ['1', '2', '3', '4', '5', '6', '7', '8'].map((n) => ({
    value: n,
    label: n === '3' ? '3 (level/slope/curvature)' : n,
  }));

export const PCA_CHANGE_FREQUENCY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'daily', label: 'Daily (1d diff)' },
  { value: 'weekly', label: 'Weekly (5d diff)' },
];

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact footer + the extended methodology.  The backend labels
 *  components pc1/pc2/pc3 deliberately (schemas.py "Honest level/
 *  slope/curvature handling"): the canonical interpretation is a
 *  property of the data, not enforced by the tool. */
export const PCA_COMPACT_CAVEAT =
  'pc1/pc2/pc3 are statistical factors — the level/slope/curvature read holds on normal curve panels, not by construction.';

// ---------------------------------------------------------------------------
// PM-facing interpretation copy — carried forward from the retired
// modelMetadata.interpretationCards block (THESIS Q3).  Imported by
// module.ts (spec-level ``interpretationCards``) AND rendered in the
// Extended surface's methodology zone, clearly labelled as canonical
// interpretation (NOT response-threaded methodology).
// ---------------------------------------------------------------------------

export const PCA_INTERPRETATION_CARDS: ReadonlyArray<{ headline: string; body: string }> = [
  {
    headline: 'PC1 = Level',
    body: 'The first principal component on a normal sovereign curve loads positive across all tenors — it captures parallel shifts in the entire curve. Daily PC1 score moves correspond to broad rate-level moves.',
  },
  {
    headline: 'PC2 = Slope',
    body: 'PC2 typically loads positive at the long end and negative at the short end — it captures steepening vs flattening. PC2 moves track 2s10s and 5s30s dynamics.',
  },
  {
    headline: 'PC3 = Curvature',
    body: 'PC3 typically loads positive at the belly and negative at the wings — it captures butterfly moves. Watch this when belly-rich/cheap views are in play.',
  },
  {
    headline: 'Variance explained',
    body: 'The first three components typically capture >97% of yield-change variance on a developed sovereign. If they do not, the curve is in an atypical regime and the residuals are themselves the signal.',
  },
];

// ---------------------------------------------------------------------------
// Param parsing — dual-view params arrive as Record<string, string>.
// ---------------------------------------------------------------------------

/** Parse the comma-joined ``tenors`` param ("2Y,5Y,10Y") into the
 *  repeated-query-param list the typed-detail endpoint expects.
 *  Empty / missing → undefined (full playbook universe). */
export function parseTenorsParam(raw: string | undefined): string[] | undefined {
  if (!raw) return undefined;
  const parts = raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  return parts.length > 0 ? parts : undefined;
}

export function parseOptionalNumber(raw: string | undefined): number | undefined {
  if (raw == null || raw === '') return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

// ---------------------------------------------------------------------------
// Data hook — single-source for both views.
// ---------------------------------------------------------------------------

export interface UsePcaYieldCurveArgs {
  curveFamily: string;
  /** Comma-joined tenor subset; empty → full universe. */
  tenorsCsv?: string;
  lookbackDays?: number;
  nComponents?: number;
  changeFrequency?: string;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UsePcaYieldCurveResult {
  data: PcaYieldCurveOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function usePcaYieldCurveData(
  args: UsePcaYieldCurveArgs,
): UsePcaYieldCurveResult {
  const [data, setData] = useState<PcaYieldCurveOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!args.curveFamily) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    const params: PcaYieldCurveDetailParams = {
      curve_family: args.curveFamily,
      tenors: parseTenorsParam(args.tenorsCsv),
      lookback_days: args.lookbackDays,
      n_components: args.nComponents,
      change_frequency:
        args.changeFrequency === 'weekly' || args.changeFrequency === 'daily'
          ? args.changeFrequency
          : undefined,
      field_name: args.fieldName || undefined,
      as_of_date: args.asOfDate || undefined,
    };
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailPcaYieldCurve(params)
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
  }, [
    args.curveFamily,
    args.tenorsCsv,
    args.lookbackDays,
    args.nComponents,
    args.changeFrequency,
    args.fieldName,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Output → grammar mappers (display-only reshaping; FP9).
// ---------------------------------------------------------------------------

function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** Component names in fit order — sourced from variance_explained,
 *  which the backend emits in pc1..pcN order. */
export function componentNames(cm: PcaYieldCurveMetrics): string[] {
  return cm.variance_explained.map((v) => v.component_name);
}

/** Loadings rows (one per tenor, in tenors_used order) → MatrixRow[]
 *  for the shared MatrixTable. */
export function loadingsMatrixRows(cm: PcaYieldCurveMetrics): MatrixRow[] {
  const cols = componentNames(cm);
  return cm.loadings.map((row, ri) => ({
    key: String(row.tenor ?? cm.tenors_used[ri] ?? `#${ri}`),
    cells: Object.fromEntries(cols.map((c) => [c, numOrNull(row[c])])),
  }));
}

/** Per-component quality chips for the MatrixTable column headers,
 *  keyed by component name.  Flags map onto the closed visual
 *  vocabulary via the shared qualityLevelForFlag. */
export function loadingsColumnBadges(
  cm: PcaYieldCurveMetrics,
): Record<string, QualityBadgeProps> {
  const badges: Record<string, QualityBadgeProps> = {};
  for (const meta of cm.component_metadata) {
    badges[meta.component_name] = {
      level: qualityLevelForFlag(meta.quality_flag),
      label:
        meta.quality_flag === 'ok'
          ? 'ok'
          : meta.quality_flag.replace(/_/g, ' '),
      note: meta.quality_note ?? undefined,
    };
  }
  return badges;
}

/** variance_explained → DecompositionEntry[] (mode "share", with the
 *  per-row Σ cumulative), quality threaded from component_metadata. */
export function varianceEntries(cm: PcaYieldCurveMetrics): DecompositionEntry[] {
  const flagByName = new Map(
    cm.component_metadata.map((m) => [m.component_name, m] as const),
  );
  return cm.variance_explained.map((v) => {
    const meta = flagByName.get(v.component_name);
    return {
      label: v.component_name,
      value: numOrNull(v.variance_share),
      cumulative: numOrNull(v.cumulative_share),
      quality: meta ? qualityLevelForFlag(meta.quality_flag) : undefined,
      qualityNote: meta?.quality_note ?? undefined,
    };
  });
}

/** Extract the pcN key from the backend series_name convention
 *  '<curve_family>_pc<k>_factor_<change_frequency>'.  Falls back to
 *  the raw name for any non-conforming series (rendered as-is, never
 *  dropped). */
export function factorKeyForSeriesName(seriesName: string): string {
  const m = seriesName.match(/_(pc\d+)_/);
  return m ? m[1] : seriesName;
}

/** time_series_factors → ModelSeries[] keyed pcN for the shared
 *  ModelSeriesPanel.  Values pass through untouched. */
export function factorModelSeries(data: PcaYieldCurveOutput): ModelSeries[] {
  return (data.time_series_factors ?? []).map((f) => ({
    key: factorKeyForSeriesName(f.series_name),
    label: factorKeyForSeriesName(f.series_name),
    rows: f.rows.map((r) => ({ date: r.date, value: r.value })),
  }));
}

/** The pc1 factor series as compact-card chart points (the compact
 *  sparkline is the first factor's score path). */
export function pc1ChartPoints(
  data: PcaYieldCurveOutput,
): Array<{ date: string; value: number }> {
  const pc1 = (data.time_series_factors ?? []).find(
    (f) => factorKeyForSeriesName(f.series_name) === 'pc1',
  );
  return (pc1?.rows ?? []).map((r) => ({
    date: r.date,
    value: r.value ?? NaN,
  }));
}

// ---------------------------------------------------------------------------
// KPI builders
// ---------------------------------------------------------------------------

/** Extended hero strip (ModelKpiStrip items) — the 4 numbers a PM
 *  reads first off a PCA fit. */
export function heroKpis(cm: PcaYieldCurveMetrics): MetricItem[] {
  return [
    {
      label: 'Components',
      value: String(cm.n_components_returned),
    },
    {
      label: 'Variance explained',
      value: unsignedFixed(cm.total_variance_explained * 100, 1),
      unit: '%',
      emphasis: true,
    },
    {
      label: 'Current PC1 level',
      value: signedFixed(cm.current_factor_levels['pc1'] ?? null, 2),
    },
    {
      label: 'Observations',
      value: String(cm.observation_count),
    },
  ];
}

/** Diagnostics strip (ModelKpiStrip items) — fit-health provenance
 *  reads, rendered LAST per the ModelResultLayout contract. */
export function diagnosticsKpis(cm: PcaYieldCurveMetrics): MetricItem[] {
  return [
    {
      label: 'Fit window',
      value: `${cm.fit_window_start} → ${cm.fit_window_end}`,
    },
    {
      label: 'Change frequency',
      value: cm.change_frequency_used,
    },
    {
      label: 'Sign anchor',
      value: cm.sign_anchor_used,
    },
    {
      label: 'Lookback requested',
      value: String(cm.lookback_days_used),
      unit: 'd',
    },
  ];
}

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *    1. VAR EXPLAINED  (total share, primary emphasis)
 *    2. PC1 LEVEL      (latest first-factor score)
 *    3. COMPONENTS     (how many factors this fit returned)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: PcaYieldCurveOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'VAR EXPLAINED',
      value: unsignedFixed(cm.total_variance_explained * 100, 1),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
    },
    {
      label: 'PC1 LEVEL',
      value: signedFixed(cm.current_factor_levels['pc1'] ?? null, 2),
      tone: 'neutral',
    },
    {
      label: 'COMPONENTS',
      value: String(cm.n_components_returned),
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Methodology rows — threaded from the response's *_used echo fields
// (P5).  The PCA Output has no prose methodology field; the card is
// assembled from the echoes and labelled as such.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  cm: PcaYieldCurveMetrics,
): ReadonlyArray<MethodologyRow> {
  return [
    {
      label: 'Model',
      value: `PCA on the centered yield-changes panel of ${cm.curve_family} (${cm.change_frequency_used} differences)`,
    },
    {
      label: 'Tenors used',
      value: cm.tenors_used.join(', '),
    },
    {
      label: 'Fit window',
      value: `${cm.fit_window_start} → ${cm.fit_window_end} (${cm.observation_count} change observations; ${cm.lookback_days_used} calendar days requested)`,
    },
    {
      label: 'Sign anchor',
      value: cm.sign_anchor_used,
    },
    {
      label: 'Components',
      value: `${cm.n_components_returned} returned · ${unsignedFixed(cm.total_variance_explained * 100, 1)}% of change variance explained`,
    },
    {
      label: 'Disclosure',
      value: PCA_COMPACT_CAVEAT,
    },
    {
      label: 'Source',
      value:
        'Assembled from this response’s *_used echo fields (sign_anchor_used, change_frequency_used, lookback_days_used, fit_window_*) — the wire carries no prose methodology field.',
    },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixed, unsignedFixed };
