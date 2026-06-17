// ============================================================================
// sovereignYieldPanelShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for ``build_sovereign_yield_panel_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the pilot's breakevenShared.ts).
// This module knows what a sovereign yield PANEL is — a wide multi-leg
// matrix of '<curve_family>_<tenor>' columns assembled for workflow /
// backtest consumption — and, critically, that the WIRE carries the panel's
// METADATA CONTRACT only (dims / columns / date range / units /
// disclosures).  The assembled cell matrix is a workflow-side ``Panel``
// artifact the MCP layer (and the detail route, which replicates the drop)
// strips before serialisation.  Neither view pretends otherwise.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME typed-detail
// endpoint (rendering_density.md §1.1 — the compact view just renders less).
// KPI / roster / methodology-row builders live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailSovereignYieldPanel,
  type SovereignYieldPanelDetailParams,
} from '@/services/ratesApi';
import type { SovereignYieldPanelOutput } from '@/types/rates';
import type {
  KPIDescriptor,
  MethodologyRow,
  ReferenceChip,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Defaults — render-time only (FM7: module.ts stays a pure value; no
// Date.now / new Date() at module-eval inside module.ts).
// ---------------------------------------------------------------------------

/** Default contract window — trailing year ending today, computed at
 *  RENDER time by the surfaces.  A year of rows is the desk-canonical
 *  "is this panel populated?" check without dragging the full history
 *  through the assembly path on first paint. */
export function defaultWindow(): { start: string; end: string } {
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 365);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { start: fmt(start), end: fmt(today) };
}

/** Default leg scope — a small cross-market starter panel (UST 2Y/10Y +
 *  Bund 10Y) so a blank Library invocation renders a real contract
 *  instead of a 422.  Paired index-wise per the route's flattening
 *  convention (leg_curve_families[i] ↔ leg_tenors[i]). */
export const DEFAULT_LEG_FAMILIES_CSV = 'UST,UST,DE_BUND';
export const DEFAULT_LEG_TENORS_CSV = '2Y,10Y,10Y';

/** YAML defaults surfaced on the controls so an empty override reads
 *  honestly ("YAML default (…)" — the Optional→None passthrough never
 *  shadows config.yaml). */
export const YAML_DEFAULT_FIELD_NAME = 'YLD_YTM_MID';
export const YAML_DEFAULT_MISSING_DATA_POLICY = 'forward_fill_only';

export const MISSING_DATA_POLICY_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = [
  { value: '', label: `YAML default (${YAML_DEFAULT_MISSING_DATA_POLICY})` },
  { value: 'raise', label: 'raise' },
  { value: 'forward_fill_only', label: 'forward_fill_only' },
  { value: 'drop_rows_any_missing', label: 'drop_rows_any_missing' },
];

// ---------------------------------------------------------------------------
// Honest-surface copy
// ---------------------------------------------------------------------------

/** The load-bearing honesty note both views surface: this card is the
 *  panel's CONTRACT, not its cells.  Mirrors the MCP-drop mechanism the
 *  detail route replicates ({k: v for k, v in result.items() if k != "panel"}). */
export const PANEL_CONTRACT_NOTE =
  'Panel data flows to workflows/backtests; this surface is the panel ' +
  'CONTRACT — columns, rows, date range, units and methodology. The ' +
  'assembled cell matrix is a workflow-side Panel artifact and never ' +
  'crosses this wire.';

/** Static fallback caveat for the compact footer before data lands.
 *  Once the response arrives the VERBATIM wire disclosures replace it
 *  (P5 — the disclosure block is the point of this tool). */
export const SOVEREIGN_PANEL_COMPACT_CAVEAT_FALLBACK =
  'Sovereign-family legs only; panel cells stay workflow-side.';

/** The sharpest wire caveat for the compact footer — the first entry of
 *  the backend's ``methodology_disclosures`` block, VERBATIM (V1
 *  calendar limitation).  Falls back to the static line pre-data. */
export function compactCaveat(data: SovereignYieldPanelOutput | null): string {
  const d = data?.methodology_disclosures ?? [];
  return d.length > 0 ? d[0] : SOVEREIGN_PANEL_COMPACT_CAVEAT_FALLBACK;
}

// ---------------------------------------------------------------------------
// Param parsing — dual-view params arrive as Record<string, string>.
// ---------------------------------------------------------------------------

/** Parse one comma-joined CSV param into a trimmed list.  Empty /
 *  missing → []. */
export function parseCsvParam(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

export function fmtInt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  return n.toLocaleString('en-US');
}

/** Distinct curve families read off the wire's '<curve_family>_<tenor>'
 *  column keys (split at the LAST underscore — families themselves carry
 *  underscores, e.g. 'CANADA_GOVT_10Y').  Order-preserving. */
export function familiesFromColumns(
  columns: ReadonlyArray<string>,
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const col of columns) {
    const i = col.lastIndexOf('_');
    const fam = i > 0 ? col.slice(0, i) : col;
    if (!seen.has(fam)) {
      seen.add(fam);
      out.push(fam);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Data hook — single source for both views.
// ---------------------------------------------------------------------------

export interface UseSovereignYieldPanelArgs {
  /** Comma-joined leg families, paired index-wise with ``legTenorsCsv``. */
  legCurveFamiliesCsv: string;
  /** Comma-joined leg tenors, paired index-wise with ``legCurveFamiliesCsv``. */
  legTenorsCsv: string;
  startDate: string;
  endDate?: string;
  /** Empty → omitted → YAML default (YLD_YTM_MID). */
  fieldName?: string;
  /** Empty → omitted → YAML default (forward_fill_only). */
  missingDataPolicy?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseSovereignYieldPanelResult {
  data: SovereignYieldPanelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Fetches the typed-detail endpoint; re-fetches when any input changes.
 *  Pre-validates the paired-CSV convention client-side so a leg-count
 *  mismatch reads as an actionable message instead of a raw 422 (the
 *  route enforces the same invariant authoritatively). */
export function useSovereignYieldPanel(
  args: UseSovereignYieldPanelArgs,
): UseSovereignYieldPanelResult {
  const [data, setData] = useState<SovereignYieldPanelOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    const families = parseCsvParam(args.legCurveFamiliesCsv);
    const tenors = parseCsvParam(args.legTenorsCsv);

    if (families.length === 0 || tenors.length === 0 || !args.startDate) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    if (families.length !== tenors.length) {
      setData(null);
      setIsLoading(false);
      setErrorMessage(
        `Leg families and leg tenors must pair index-wise; got ${families.length} ` +
          `famil${families.length === 1 ? 'y' : 'ies'} vs ${tenors.length} tenor${tenors.length === 1 ? '' : 's'}.`,
      );
      return;
    }

    const params: SovereignYieldPanelDetailParams = {
      leg_curve_families: families,
      leg_tenors: tenors,
      start_date: args.startDate,
      end_date: args.endDate || undefined,
      field_name: args.fieldName || undefined,
      missing_data_policy: args.missingDataPolicy || undefined,
      as_of_date: args.asOfDate || undefined,
    };

    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailSovereignYieldPanel(params)
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
    args.legCurveFamiliesCsv,
    args.legTenorsCsv,
    args.startDate,
    args.endDate,
    args.fieldName,
    args.missingDataPolicy,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact card's THREE canonical contract numbers per
 *  rendering_density.md §2.2: COLUMNS / ROWS / DATE RANGE.  THESIS Q3
 *  documents why these vs alternatives (families count, units). */
export function compactKPIs(
  data: SovereignYieldPanelOutput,
): ReadonlyArray<KPIDescriptor> {
  return [
    {
      label: 'COLUMNS',
      value: fmtInt(data.columns.length),
      tone: 'neutral',
      emphasis: 'primary',
    },
    { label: 'ROWS', value: fmtInt(data.n_observations), tone: 'neutral' },
    {
      label: 'DATE RANGE',
      value: data.as_of_start,
      subtext: `→ ${data.as_of_end}`,
      tone: 'neutral',
    },
  ];
}

/** The extended view's hero KPI strip: COLUMNS / ROWS / DATE RANGE /
 *  FAMILIES. */
export function heroKPIs(
  data: SovereignYieldPanelOutput,
): ReadonlyArray<KPIDescriptor> {
  const families = familiesFromColumns(data.columns);
  return [
    {
      label: 'COLUMNS',
      value: fmtInt(data.columns.length),
      tone: 'neutral',
      emphasis: 'primary',
      caption: 'one per leg',
    },
    {
      label: 'ROWS',
      value: fmtInt(data.n_observations),
      tone: 'neutral',
      caption: 'trade dates',
    },
    {
      label: 'DATE RANGE',
      value: `${data.as_of_start} → ${data.as_of_end}`,
      tone: 'neutral',
      caption: 'resolved (inclusive)',
    },
    {
      label: 'FAMILIES',
      value: fmtInt(families.length),
      tone: 'neutral',
      caption: families.join(' · ') || undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Column roster
// ---------------------------------------------------------------------------

export interface SovereignPanelRosterRow {
  /** '<curve_family>_<tenor>' Panel column key. */
  columnKey: string;
  curveFamily: string;
  tenor: string;
  /** Closed-enum TimeSeriesUnits tag from ``units_by_column``. */
  unit: string;
}

export function buildColumnRoster(
  data: SovereignYieldPanelOutput,
): ReadonlyArray<SovereignPanelRosterRow> {
  return data.columns.map((col) => {
    const i = col.lastIndexOf('_');
    return {
      columnKey: col,
      curveFamily: i > 0 ? col.slice(0, i) : col,
      tenor: i > 0 ? col.slice(i + 1) : '—',
      unit: data.units_by_column[col] ?? '—',
    };
  });
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Methodology card rows.  The backend's ``methodology_disclosures``
 *  block threads through VERBATIM, one row per disclosure (P5 — the V1
 *  limitation declarations are the point of this tool).  The contract /
 *  column-key rows describe the wire shape, not finance. */
export function buildMethodologyRows(
  data: SovereignYieldPanelOutput,
  requestedFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const rows: MethodologyRow[] = [
    {
      label: 'Contract',
      value: PANEL_CONTRACT_NOTE,
    },
    {
      label: 'Column key',
      value:
        "'<curve_family>_<tenor>' (e.g. 'UST_10Y') — one column per leg, order matches the leg list.",
    },
    {
      label: 'Field',
      value: requestedFieldName
        ? `${requestedFieldName} (per-query override, all legs)`
        : `YAML default (${YAML_DEFAULT_FIELD_NAME})`,
    },
    {
      label: 'Units',
      value: distinctUnits(data).join(' · ') || '—',
    },
  ];
  data.methodology_disclosures.forEach((d, i) => {
    rows.push({ label: `Disclosure ${i + 1}`, value: d });
  });
  return rows;
}

function distinctUnits(data: SovereignYieldPanelOutput): string[] {
  return [...new Set(Object.values(data.units_by_column))];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'sovereign_yield_panel/config.yaml' },
    { label: 'shared/artifacts/types.py::Panel' },
    { label: 'methodology_exposure.md §5' },
  ];
}
