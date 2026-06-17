// ============================================================================
// zcisPanelShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``build_zcis_panel_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the pilot's breakevenShared.ts and
// the sibling sovereignYieldPanelShared.ts).  This module knows what a ZCIS
// PANEL is — a wide multi-instrument matrix of zero-coupon inflation swap
// rates keyed by ``vendor_ticker`` across the USD_ZCIS / EUR_ZCIS / GBP_ZCIS
// universe, assembled for cross-curve regression / PCA / RV-scanning
// operators — and, critically, that the WIRE carries the panel's METADATA
// CONTRACT only (dims / vendor_ticker columns / date range / units /
// methodology_card).  The assembled cell matrix is a workflow-side ``Panel``
// artifact the MCP layer (and the detail route, which replicates the drop)
// strips before serialisation.  Neither view pretends otherwise.
//
// Two disclosures on the methodology_card are LOAD-BEARING and thread
// through VERBATIM (P5):
//   * ``security_name_caveat`` — columns are keyed by vendor_ticker because
//     security_name is universally NULL on the live SCD2 rows (no-proxy).
//   * ``index_family_caveat``  — CPI-U / HICP-xT / RPI are three different
//     inflation regimes side-by-side, NOT a harmonised expected-inflation
//     surface; per-curve index_lag / interpolation reference rides in
//     ``curve_family_reference``.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME typed-detail
// endpoint (rendering_density.md §1.1 — the compact view just renders less).
// KPI / roster / reference / methodology-row builders live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailZcisPanel,
  type ZcisPanelDetailParams,
} from '@/services/ratesApi';
import type { BuildZcisPanelOutput } from '@/types/rates';
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
 *  "is this panel populated?" check without dragging the full ZCIS
 *  history through the assembly path on first paint. */
export function defaultWindow(): { start: string; end: string } {
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 365);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { start: fmt(start), end: fmt(today) };
}

/** Default family scope — the FULL closed ZCIS universe, written out
 *  explicitly (instead of the blank → omit shorthand) so the control
 *  reads honestly on first paint.  Blank is still valid: omit → the
 *  backend resolves the same full universe. */
export const DEFAULT_CURVE_FAMILIES_CSV = 'USD_ZCIS,EUR_ZCIS,GBP_ZCIS';

/** Default tenor scope — the desk-canonical pillar set.  Blank → every
 *  tenor present in the DB for the resolved families (a much wider
 *  panel); the starter pillars keep the first paint readable.  Tenors
 *  absent on a family are silently dropped backend-side; the wire
 *  echoes the resolved set. */
export const DEFAULT_TENORS_CSV = '1Y,2Y,5Y,10Y';

/** YAML defaults surfaced on the controls so an empty override reads
 *  honestly ("YAML default (…)" — the Optional→None passthrough never
 *  shadows build_zcis_panel/config.yaml). */
export const YAML_DEFAULT_FIELD_NAME = 'PX_MID';
export const YAML_DEFAULT_CALENDAR_POLICY = 'business_days';
export const YAML_DEFAULT_MISSING_DATA_POLICY = 'forward_fill_only';

export const CALENDAR_POLICY_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = [
  { value: '', label: `YAML default (${YAML_DEFAULT_CALENDAR_POLICY})` },
  { value: 'business_days', label: 'business_days' },
  { value: 'instrument_native', label: 'instrument_native' },
];

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
 *  Once the response arrives the VERBATIM wire ``index_family_caveat``
 *  replaces it (P5 — the disclosure block is the point of this tool). */
export const ZCIS_PANEL_COMPACT_CAVEAT_FALLBACK =
  'USD/EUR/GBP ZCIS reference different inflation indices; panel cells ' +
  'stay workflow-side.';

/** The sharpest wire caveat for the compact footer — the backend's
 *  ``index_family_caveat``, VERBATIM (CPI-U / HICP-xT / RPI are not a
 *  harmonised surface).  Falls back to the static line pre-data. */
export function compactCaveat(data: BuildZcisPanelOutput | null): string {
  const caveat = data?.methodology_card?.index_family_caveat;
  return caveat && caveat.length > 0
    ? caveat
    : ZCIS_PANEL_COMPACT_CAVEAT_FALLBACK;
}

// ---------------------------------------------------------------------------
// Param parsing — dual-view params arrive as Record<string, string>.
// ---------------------------------------------------------------------------

/** Parse one comma-joined CSV param into a trimmed list.  Empty /
 *  missing → [] (→ omitted on the wire → backend default universe). */
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

// ---------------------------------------------------------------------------
// Data hook — single source for both views.
// ---------------------------------------------------------------------------

export interface UseZcisPanelArgs {
  /** Comma-joined ZCIS families (subset of USD_ZCIS | EUR_ZCIS |
   *  GBP_ZCIS).  Empty → omitted → full universe.  Non-ZCIS families
   *  are 422-refused backend-side (closed Literal) — the error
   *  threads verbatim into ``errorMessage``. */
  curveFamiliesCsv: string;
  /** Comma-joined tenor pillars.  Empty → omitted → every tenor
   *  present for the resolved families. */
  tenorsCsv: string;
  startDate: string;
  endDate?: string;
  /** Empty → omitted → YAML default (PX_MID). */
  fieldName?: string;
  /** Empty → omitted → YAML default (business_days). */
  calendarPolicy?: string;
  /** Empty → omitted → YAML default (forward_fill_only). */
  missingDataPolicy?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseZcisPanelResult {
  data: BuildZcisPanelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Fetches the typed-detail endpoint; re-fetches when any input changes.
 *  Unlike the sovereign panel's paired-leg lists, families and tenors
 *  are INDEPENDENT optional scopes here — no client-side pairing
 *  invariant to pre-validate; the closed family set is enforced
 *  authoritatively backend-side (422 surfaces verbatim). */
export function useZcisPanel(args: UseZcisPanelArgs): UseZcisPanelResult {
  const [data, setData] = useState<BuildZcisPanelOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!args.startDate) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }

    const families = parseCsvParam(args.curveFamiliesCsv);
    const tenors = parseCsvParam(args.tenorsCsv);

    const params: ZcisPanelDetailParams = {
      start_date: args.startDate,
      end_date: args.endDate || undefined,
      curve_families: families.length > 0 ? families : undefined,
      tenors: tenors.length > 0 ? tenors : undefined,
      field_name: args.fieldName || undefined,
      calendar_policy: args.calendarPolicy || undefined,
      missing_data_policy: args.missingDataPolicy || undefined,
      as_of_date: args.asOfDate || undefined,
    };

    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailZcisPanel(params)
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
    args.curveFamiliesCsv,
    args.tenorsCsv,
    args.startDate,
    args.endDate,
    args.fieldName,
    args.calendarPolicy,
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
  data: BuildZcisPanelOutput,
): ReadonlyArray<KPIDescriptor> {
  return [
    {
      label: 'COLUMNS',
      value: fmtInt(data.column_count),
      tone: 'neutral',
      emphasis: 'primary',
    },
    { label: 'ROWS', value: fmtInt(data.row_count), tone: 'neutral' },
    {
      label: 'DATE RANGE',
      value: data.start_date,
      subtext: `→ ${data.end_date}`,
      tone: 'neutral',
    },
  ];
}

/** The extended view's hero KPI strip: COLUMNS / ROWS / DATE RANGE /
 *  FAMILIES×TENORS.  The fourth cell is the resolved SCOPE — note the
 *  product can exceed COLUMNS (tenors absent on a family are silently
 *  dropped; the grid is sparse, and the caption says so). */
export function heroKPIs(
  data: BuildZcisPanelOutput,
): ReadonlyArray<KPIDescriptor> {
  return [
    {
      label: 'COLUMNS',
      value: fmtInt(data.column_count),
      tone: 'neutral',
      emphasis: 'primary',
      caption: 'one per swap (vendor_ticker)',
    },
    {
      label: 'ROWS',
      value: fmtInt(data.row_count),
      tone: 'neutral',
      caption: 'trade dates',
    },
    {
      label: 'DATE RANGE',
      value: `${data.start_date} → ${data.end_date}`,
      tone: 'neutral',
      caption: 'resolved (inclusive)',
    },
    {
      label: 'FAMILIES × TENORS',
      value: `${fmtInt(data.curve_families.length)} × ${fmtInt(data.tenors.length)}`,
      tone: 'neutral',
      caption: data.curve_families.join(' · ') || undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Column roster — vendor_ticker keys + per-column units.
// ---------------------------------------------------------------------------

export interface ZcisPanelRosterRow {
  /** Canonical Bloomberg vendor_ticker Panel column key
   *  (e.g. 'USSWIT10 Curncy'). */
  columnKey: string;
  /** Closed-enum TimeSeriesUnits tag from ``units_by_column``. */
  unit: string;
}

/** One roster row per Panel column, in the wire's deterministic
 *  (curve_family, tenor, vendor_ticker) sort order.  The wire does NOT
 *  carry a per-column family/tenor mapping — deriving one by parsing
 *  ticker mnemonics would be client-side inference (FP9); the per-FAMILY
 *  reference lives in ``buildFamilyReference`` instead. */
export function buildColumnRoster(
  data: BuildZcisPanelOutput,
): ReadonlyArray<ZcisPanelRosterRow> {
  return data.vendor_tickers.map((ticker) => ({
    columnKey: ticker,
    unit: data.units_by_column[ticker] ?? '—',
  }));
}

// ---------------------------------------------------------------------------
// Curve-family reference — per-curve index conventions, from the wire.
// ---------------------------------------------------------------------------

export interface ZcisFamilyReferenceRow {
  curveFamily: string;
  /** e.g. 'US_CPI' / 'EUR_HICPXT' / 'UK_RPI'. */
  inflationIndexFamily: string;
  /** Indexation lag convention (e.g. '3M'). */
  indexLag: string;
  interpolation: string;
  underlyingIndex: string;
}

/** Per-family index-convention rows read off the methodology_card's
 *  ``curve_family_reference`` block — VERBATIM values, '—' for nulls.
 *  Order follows the wire's resolved ``curve_families``; reference-only
 *  keys (defensive) append after. */
export function buildFamilyReference(
  data: BuildZcisPanelOutput,
): ReadonlyArray<ZcisFamilyReferenceRow> {
  const ref = data.methodology_card?.curve_family_reference ?? {};
  const ordered = [
    ...data.curve_families.filter((f) => f in ref),
    ...Object.keys(ref).filter((f) => !data.curve_families.includes(f)),
  ];
  return ordered.map((family) => {
    const r = ref[family] ?? {};
    const show = (v: string | null | undefined) =>
      v != null && v !== '' ? v : '—';
    return {
      curveFamily: family,
      inflationIndexFamily: show(r.inflation_index_family),
      indexLag: show(r.index_lag),
      interpolation: show(r.interpolation),
      underlyingIndex: show(r.underlying_index),
    };
  });
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Methodology card rows.  The backend's ``security_name_caveat`` and
 *  ``index_family_caveat`` thread through VERBATIM, one row each (P5 —
 *  the disclosure block is the point of this tool).  The contract /
 *  column-key rows describe the wire shape; the policy rows echo the
 *  RESOLVED values off the wire (never re-derived client-side). */
export function buildMethodologyRows(
  data: BuildZcisPanelOutput,
  requestedFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const card = data.methodology_card ?? {};
  const rows: MethodologyRow[] = [
    {
      label: 'Contract',
      value: PANEL_CONTRACT_NOTE,
    },
    {
      label: 'Column key',
      value:
        "vendor_ticker (e.g. 'USSWIT10 Curncy') — one canonical Bloomberg " +
        'identifier per swap; columns sorted by (curve_family, tenor, ' +
        'vendor_ticker).',
    },
    {
      label: 'Field',
      value: card.field_name
        ? `${card.field_name}${
            requestedFieldName
              ? ' (per-query override, all columns)'
              : ` (YAML default${card.field_name === YAML_DEFAULT_FIELD_NAME ? '' : ' — resolved'})`
          }`
        : requestedFieldName || `YAML default (${YAML_DEFAULT_FIELD_NAME})`,
    },
    {
      label: 'Calendar',
      value: card.calendar_policy ?? `YAML default (${YAML_DEFAULT_CALENDAR_POLICY})`,
    },
    {
      label: 'Missing data',
      value: `${card.missing_data_policy ?? `YAML default (${YAML_DEFAULT_MISSING_DATA_POLICY})`}${
        card.ffill_limit_days != null ? ` · ffill ≤ ${card.ffill_limit_days}d` : ''
      }`,
    },
    {
      label: 'Units',
      value: distinctUnits(data).join(' · ') || '—',
    },
  ];
  // VERBATIM disclosures (P5).
  if (card.security_name_caveat) {
    rows.push({ label: 'Column-key caveat', value: card.security_name_caveat });
  }
  if (card.index_family_caveat) {
    rows.push({ label: 'Index-family caveat', value: card.index_family_caveat });
  }
  if (card.methodology_label) {
    rows.push({ label: 'Methodology', value: card.methodology_label });
  }
  return rows;
}

function distinctUnits(data: BuildZcisPanelOutput): string[] {
  return [...new Set(Object.values(data.units_by_column))];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'build_zcis_panel/config.yaml' },
    { label: 'shared/artifacts/types.py::Panel' },
    { label: 'methodology_exposure.md §5' },
  ];
}
