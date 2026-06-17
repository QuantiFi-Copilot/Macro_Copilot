// ============================================================================
// yieldChangeAttributionShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for
// ``calculate_yield_change_attribution_pca_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the breakeven pilot's
// breakevenShared.ts, lifted onto the rich-model grammar).  This module
// knows what a "contribution", a "residual", and "loadings provenance"
// mean; the shared grammar components at @/components/shared/build/model
// do not (FP13).
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint at /api/v1/rates/detail/yield-change-attribution; the
// compact view just renders less).  PURE SNAPSHOT output — no time
// series on the wire; the compact visual is the signed decomposition
// bars, not a sparkline.
//
// FP9 — no client-side statistics.  The only computation here is
// ``topContributor`` — a display-only sort (max by |contribution_bps|),
// explicitly sanctioned for headline selection.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailYieldChangeAttribution,
  type YieldChangeAttributionDetailParams,
} from '@/services/ratesApi';
import type {
  YieldChangeAttributionComponentContribution,
  YieldChangeAttributionPcaMetrics,
  YieldChangeAttributionPcaOutput,
} from '@/types/rates';
import {
  qualityLevelForFlag,
  type DecompositionEntry,
  type MatrixRow,
  type MetricItem,
  type QualityBadgeProps,
} from '@/components/shared/build/model';
import {
  signedFixed,
  toneForChange,
  unsignedFixed,
  type KPIDescriptor,
  type MethodologyRow,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Input option sets — mirror YieldChangeAttributionPcaInput
// (rates_agent/sovereign_bonds/tools/yield_change_attribution_pca/
// schemas.py).  The documented universe is the sovereign benchmarks.
// ---------------------------------------------------------------------------

export const ATTRIBUTION_CURVE_FAMILY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'UST', label: 'UST (US)' },
  { value: 'DE_BUND', label: 'DE_BUND (Germany)' },
  { value: 'IT_BTP', label: 'IT_BTP (Italy)' },
  { value: 'FR_OAT', label: 'FR_OAT (France)' },
  { value: 'ES_BONO', label: 'ES_BONO (Spain)' },
  { value: 'UK_GILT', label: 'UK_GILT (UK)' },
  { value: 'JGB', label: 'JGB (Japan)' },
];

export const ATTRIBUTION_LOOKBACK_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '730', label: '2Y' },
  { value: '1095', label: '3Y' },
  { value: '1825', label: '5Y (default)' },
  { value: '2555', label: '7Y' },
  { value: '3650', label: '10Y' },
];

export const ATTRIBUTION_N_COMPONENTS_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  ['1', '2', '3', '4', '5', '6', '7', '8'].map((n) => ({
    value: n,
    label: n === '3' ? '3 (level/slope/curvature)' : n,
  }));

export const ATTRIBUTION_CHANGE_FREQUENCY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'daily', label: 'Daily (1d diff)' },
  { value: 'weekly', label: 'Weekly (5d diff)' },
];

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact footer + the extended methodology.  The identity the whole
 *  read hangs on: target Δ = Σ component contributions + residual. */
export const ATTRIBUTION_COMPACT_CAVEAT =
  'Σ contributions + residual = total Δ; loadings fit inline on this call (pasted-loadings runs are not bridged here).';

// ---------------------------------------------------------------------------
// PM-facing interpretation copy — carried forward from the retired
// modelMetadata.interpretationCards block (THESIS Q3).  Imported by
// module.ts (spec-level ``interpretationCards``) AND rendered in the
// Extended surface's methodology zone, clearly labelled as canonical
// interpretation (NOT response-threaded methodology).
// ---------------------------------------------------------------------------

export const ATTRIBUTION_INTERPRETATION_CARDS: ReadonlyArray<{ headline: string; body: string }> = [
  {
    headline: 'Interpretation',
    body: 'Total change at the target tenor = sum of component contributions + residual. A residual >5bp on a 3-component decomposition signals atypical curve behaviour the level/slope/curvature basis cannot capture.',
  },
  {
    headline: 'Sign convention',
    body: "Each PC's loading at the longest tenor is locked non-negative. So a positive PC1 contribution on a 10Y means the level component drove a yield rise; a negative PC2 contribution means the slope component compressed (flattening).",
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

/** Default attribution window — trailing ~quarter ending today.
 *  Computed at RENDER time by the surfaces (module.ts stays a pure
 *  value per FM7; the legacy spec computed this at module-eval). */
export function defaultWindow(): { start: string; end: string } {
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 90);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { start: fmt(start), end: fmt(today) };
}

// ---------------------------------------------------------------------------
// Data hook — single-source for both views.
// ---------------------------------------------------------------------------

export interface UseYieldChangeAttributionArgs {
  curveFamily: string;
  targetTenor: string;
  startDate: string;
  endDate: string;
  pcaLookbackDays?: number;
  nComponents?: number;
  changeFrequency?: string;
  /** Comma-joined tenor subset for the inline fit; empty → full universe. */
  tenorsCsv?: string;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseYieldChangeAttributionResult {
  data: YieldChangeAttributionPcaOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useYieldChangeAttributionData(
  args: UseYieldChangeAttributionArgs,
): UseYieldChangeAttributionResult {
  const [data, setData] = useState<YieldChangeAttributionPcaOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!args.curveFamily || !args.targetTenor || !args.startDate || !args.endDate) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    const params: YieldChangeAttributionDetailParams = {
      curve_family: args.curveFamily,
      target_tenor: args.targetTenor,
      start_date: args.startDate,
      end_date: args.endDate,
      pca_lookback_days: args.pcaLookbackDays,
      n_components: args.nComponents,
      change_frequency:
        args.changeFrequency === 'weekly' || args.changeFrequency === 'daily'
          ? args.changeFrequency
          : undefined,
      tenors: parseTenorsParam(args.tenorsCsv),
      field_name: args.fieldName || undefined,
      as_of_date: args.asOfDate || undefined,
    };
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailYieldChangeAttribution(params)
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
    args.targetTenor,
    args.startDate,
    args.endDate,
    args.pcaLookbackDays,
    args.nComponents,
    args.changeFrequency,
    args.tenorsCsv,
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

/** component_contributions + residual → DecompositionEntry[] (mode
 *  "signed", unit bps).  The residual rides as the de-emphasised
 *  ``isResidual`` entry per the grammar contract. */
export function contributionEntries(
  cm: YieldChangeAttributionPcaMetrics,
): DecompositionEntry[] {
  const entries: DecompositionEntry[] = cm.component_contributions.map((c) => ({
    label: c.component_name,
    value: numOrNull(c.contribution_bps),
    quality: qualityLevelForFlag(c.quality_flag),
    qualityNote:
      c.quality_flag === 'ok'
        ? undefined
        : c.quality_flag === 'degenerate'
          ? 'Degenerate component — loadings suppressed; its share falls into the residual.'
          : 'Sign-anchor tied — component direction is ambiguous under the locked anchor.',
  }));
  entries.push({
    label: 'residual',
    value: numOrNull(cm.residual_bps),
    isResidual: true,
  });
  return entries;
}

/** One-row loadings matrix — the loading each component carries at the
 *  target tenor (the projection weights behind the contributions). */
export function loadingAtTargetMatrixRow(
  cm: YieldChangeAttributionPcaMetrics,
): { columns: string[]; rows: MatrixRow[]; columnBadges: Record<string, QualityBadgeProps> } {
  const columns = cm.component_contributions.map((c) => c.component_name);
  const rows: MatrixRow[] = [
    {
      key: cm.target_tenor,
      cells: Object.fromEntries(
        cm.component_contributions.map((c) => [
          c.component_name,
          numOrNull(c.loading_at_target_tenor),
        ]),
      ),
    },
  ];
  const columnBadges: Record<string, QualityBadgeProps> = {};
  for (const c of cm.component_contributions) {
    columnBadges[c.component_name] = {
      level: qualityLevelForFlag(c.quality_flag),
      label: c.quality_flag === 'ok' ? 'ok' : c.quality_flag.replace(/_/g, ' '),
    };
  }
  return { columns, rows, columnBadges };
}

/** Display-only headline selection: the contributor with the largest
 *  |contribution_bps| (degenerate/null contributions excluded).  NOT a
 *  statistic — a sort over backend-computed values. */
export function topContributor(
  cm: YieldChangeAttributionPcaMetrics,
): YieldChangeAttributionComponentContribution | null {
  let best: YieldChangeAttributionComponentContribution | null = null;
  for (const c of cm.component_contributions) {
    const v = numOrNull(c.contribution_bps);
    if (v == null) continue;
    const bestV = best ? numOrNull(best.contribution_bps) : null;
    if (bestV == null || Math.abs(v) > Math.abs(bestV)) best = c;
  }
  return best;
}

// ---------------------------------------------------------------------------
// KPI builders
// ---------------------------------------------------------------------------

/** Extended hero strip (ModelKpiStrip items) — the 4 numbers a PM
 *  reads first off an attribution. */
export function heroKpis(cm: YieldChangeAttributionPcaMetrics): MetricItem[] {
  const top = topContributor(cm);
  return [
    {
      label: 'Total change',
      value: signedFixed(cm.total_change_bps, 2),
      unit: 'bps',
      tone: toneOf(cm.total_change_bps),
      emphasis: true,
    },
    {
      label: 'Top contributor',
      value: top
        ? `${top.component_name} ${signedFixed(top.contribution_bps, 2)}`
        : '—',
      unit: top ? 'bps' : undefined,
    },
    {
      label: 'Residual',
      value: signedFixed(cm.residual_bps, 2),
      unit: 'bps',
    },
    {
      label: 'Window',
      value: `${cm.start_date_resolved} → ${cm.end_date_resolved}`,
    },
  ];
}

function toneOf(v: number | null | undefined): MetricItem['tone'] {
  if (v == null || !Number.isFinite(v)) return 'neutral';
  if (v > 0) return 'positive';
  if (v < 0) return 'negative';
  return 'neutral';
}

/** Diagnostics strip (ModelKpiStrip items) — loadings provenance, the
 *  audit trail for what the contributions were projected onto. */
export function diagnosticsKpis(cm: YieldChangeAttributionPcaMetrics): MetricItem[] {
  return [
    {
      label: 'Loadings source',
      value: cm.loadings_source === 'fit_inline' ? 'fit inline' : 'pasted',
    },
    {
      label: 'Fit window',
      value: `${cm.loadings_window_start} → ${cm.loadings_window_end}`,
    },
    {
      label: 'Change/fit overlap',
      value: unsignedFixed(cm.loadings_change_window_overlap_pct, 1),
      unit: '%',
      subtext: '0% = fully out-of-sample',
    },
    {
      label: 'Fit observations',
      value: String(cm.loadings_n_observations_in_fit),
    },
  ];
}

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *    1. TOTAL Δ    (the move being decomposed, primary emphasis)
 *    2. TOP FACTOR (largest |contribution|, name + bps caption)
 *    3. RESIDUAL   (what the basis did NOT capture)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: YieldChangeAttributionPcaOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const top = topContributor(cm);
  return [
    {
      label: 'TOTAL Δ',
      value: signedFixed(cm.total_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.total_change_bps),
      emphasis: 'primary',
    },
    {
      label: 'TOP FACTOR',
      value: top ? top.component_name : '—',
      caption: top
        ? `${signedFixed(top.contribution_bps, 1)} bp`
        : undefined,
      tone: 'neutral',
    },
    {
      label: 'RESIDUAL',
      value: signedFixed(cm.residual_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Methodology rows — threaded from the response's window-provenance +
// loadings_* echo fields (P5).  The Output has no prose methodology
// field; the card is assembled from the echoes and labelled as such.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  cm: YieldChangeAttributionPcaMetrics,
): ReadonlyArray<MethodologyRow> {
  return [
    {
      label: 'Decomposition',
      value: `Δ ${cm.curve_family} ${cm.target_tenor} = Σ component contributions + residual (${cm.n_components_used} components used)`,
    },
    {
      label: 'Change window',
      value: `${cm.start_date_resolved} → ${cm.end_date_resolved} (requested ${cm.start_date_requested} → ${cm.end_date_requested}; resolved forward/backward to trading days)`,
    },
    {
      label: 'Loadings source',
      value:
        cm.loadings_source === 'fit_inline'
          ? 'Fit inline on this call via pca_yield_curve'
          : 'Pasted loadings payload (provenance echoed below)',
    },
    {
      label: 'Loadings fit',
      value: `${cm.loadings_window_start} → ${cm.loadings_window_end} · ${cm.loadings_change_frequency_used} changes · ${cm.loadings_n_observations_in_fit} observations`,
    },
    {
      label: 'Sign anchor',
      value: cm.loadings_sign_anchor_used,
    },
    {
      label: 'Change/fit overlap',
      value: `${unsignedFixed(cm.loadings_change_window_overlap_pct, 1)}% of change-window trading days lie inside the loadings fit window (0% = fully out-of-sample attribution)`,
    },
    {
      label: 'Bridge limitation',
      value:
        'The pasted_loadings mode is NOT bridged over this GET endpoint — a loadings matrix does not fit query params.  This view always fits inline; pasted-loadings runs stay on the generic run endpoint / MCP.',
    },
    {
      label: 'Source',
      value:
        'Assembled from this response’s window-provenance + loadings_* echo fields — the wire carries no prose methodology field.',
    },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixed, toneForChange, unsignedFixed };
