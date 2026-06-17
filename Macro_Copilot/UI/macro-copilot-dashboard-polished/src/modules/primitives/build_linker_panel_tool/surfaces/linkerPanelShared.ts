// ============================================================================
// linkerPanelShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``build_linker_panel_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the sibling
// sovereignYieldPanelShared.ts).  This module knows what a LINKER PANEL is —
// a wide multi-instrument matrix of inflation-linker REAL yields keyed by
// ``vendor_ticker`` across the USD_TIPS / GBP_LINKER / EUR_FR_LINKER /
// CAD_RRB universe, assembled for cross-country RV scanning / real-yield
// PCA / operator consumption — and, critically, that the WIRE carries the
// panel's METADATA CONTRACT only (dims / vendor_ticker columns / date range
// / units / methodology_card).  The assembled cell matrix is a workflow-side
// ``Panel`` artifact the MCP layer (and the detail route, which replicates
// the drop) strips before serialisation.  Neither view pretends otherwise.
//
// NO tenors knob — linkers are specific-maturity BONDS, not tenor-pillar
// swaps; columns are bond identifiers, and the per-bond tenor / country /
// maturity reference rides in the methodology_card's
// ``curve_family_reference`` block (read off the wire, never inferred).
//
// FOUR disclosures on the methodology_card are LOAD-BEARING and thread
// through VERBATIM (P5):
//   * ``security_name_caveat`` — columns are keyed by vendor_ticker because
//     security_name is universally NULL on the live SCD2 rows (no-proxy).
//   * ``index_family_caveat``  — US_CPI_URBAN / UK_RPI / EU_HICP / CAN_CPI
//     are four different inflation regimes side-by-side, NOT a harmonised
//     expected-inflation surface.
//   * ``market_structure_caveat`` — cross-country issuance / liquidity /
//     deflation-floor differences for RV consumers.
//   * ``cross_region_business_days_caveat`` — the Mon-Fri union across
//     US/UK/FR/CA sessions is a cross-region approximation.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME typed-detail
// endpoint (rendering_density.md §1.1 — the compact view just renders less).
// KPI / roster / methodology-row builders live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailLinkerPanel,
  type LinkerPanelDetailParams,
} from '@/services/ratesApi';
import type { BuildLinkerPanelOutput } from '@/types/rates';
import type {
  KPIDescriptor,
  MethodologyRow,
  ReferenceChip,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Linker curve-family vocabulary.  Mirrors the scanner sibling's registry
// shape (scanInflationLinkersExtremesShared.ts) so the linker family shares
// an identity vocabulary, but is owned per-tool (no cross-module imports)
// per the dual-view contract.  Family strings match the backend's closed
// Literal (rates.ts mirror: USD_TIPS | GBP_LINKER | EUR_FR_LINKER | CAD_RRB).
// ---------------------------------------------------------------------------

export interface LinkerFamilyMeta {
  family: string;      // 'USD_TIPS' | 'GBP_LINKER' | 'EUR_FR_LINKER' | 'CAD_RRB'
  label: string;       // desk-readable market name
  indexShort: string;  // 'CPI-U' | 'RPI' | 'HICP' | 'CAN CPI'
  flag: string;        // 🇺🇸 / 🇬🇧 / 🇫🇷 / 🇨🇦
}

const FAMILY_REGISTRY: Record<string, LinkerFamilyMeta> = {
  USD_TIPS:      { family: 'USD_TIPS',      label: 'US TIPS',           indexShort: 'CPI-U',   flag: '🇺🇸' },
  GBP_LINKER:    { family: 'GBP_LINKER',    label: 'UK Linkers',        indexShort: 'RPI',     flag: '🇬🇧' },
  EUR_FR_LINKER: { family: 'EUR_FR_LINKER', label: 'France OATi/OATei', indexShort: 'HICP',    flag: '🇫🇷' },
  CAD_RRB:       { family: 'CAD_RRB',       label: 'Canada RRB',        indexShort: 'CAN CPI', flag: '🇨🇦' },
};

export function linkerFamilyFor(family: string): LinkerFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** Flag-decorated family tag for KPI captions / roster cells — falls back
 *  to the raw wire string for forward-compatible families. */
export function familyTag(family: string): string {
  const meta = linkerFamilyFor(family);
  return meta ? `${meta.flag} ${meta.family}` : family;
}

// ---------------------------------------------------------------------------
// Defaults — render-time only (FM7: module.ts stays a pure value; no
// Date.now / new Date() at module-eval inside module.ts).
// ---------------------------------------------------------------------------

/** Default contract window — trailing year ending today, computed at
 *  RENDER time by the surfaces.  A year of rows is the desk-canonical
 *  "is this panel populated?" check without dragging the full linker
 *  history through the assembly path on first paint. */
export function defaultWindow(): { start: string; end: string } {
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 365);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { start: fmt(start), end: fmt(today) };
}

/** Default family scope — the FULL closed linker universe, written out
 *  explicitly (instead of the blank → omit shorthand) so the control
 *  reads honestly on first paint.  Blank is still valid: omit → the
 *  backend resolves the same full universe. */
export const DEFAULT_CURVE_FAMILIES_CSV =
  'USD_TIPS,GBP_LINKER,EUR_FR_LINKER,CAD_RRB';

/** YAML defaults surfaced on the controls so an empty override reads
 *  honestly ("YAML default (…)" — the Optional→None passthrough never
 *  shadows build_linker_panel/config.yaml). */
export const YAML_DEFAULT_FIELD_NAME = 'YLD_YTM_MID';
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
export const LINKER_PANEL_COMPACT_CAVEAT_FALLBACK =
  'US/UK/FR/CA linkers reference different inflation indices; panel ' +
  'cells stay workflow-side.';

/** The sharpest wire caveat for the compact footer — the backend's
 *  ``index_family_caveat``, VERBATIM (US_CPI_URBAN / UK_RPI / EU_HICP /
 *  CAN_CPI are not a harmonised surface).  Falls back to the static
 *  line pre-data. */
export function compactCaveat(data: BuildLinkerPanelOutput | null): string {
  const caveat = data?.methodology_card?.index_family_caveat;
  return caveat && caveat.length > 0
    ? caveat
    : LINKER_PANEL_COMPACT_CAVEAT_FALLBACK;
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

export interface UseLinkerPanelArgs {
  /** Comma-joined linker families (subset of USD_TIPS | GBP_LINKER |
   *  EUR_FR_LINKER | CAD_RRB).  Empty → omitted → full universe.
   *  Non-linker families are 422-refused backend-side (closed Literal)
   *  — the error threads verbatim into ``errorMessage``. */
  curveFamiliesCsv: string;
  startDate: string;
  endDate?: string;
  /** Empty → omitted → YAML default (YLD_YTM_MID). */
  fieldName?: string;
  /** Empty → omitted → YAML default (business_days). */
  calendarPolicy?: string;
  /** Empty → omitted → YAML default (forward_fill_only). */
  missingDataPolicy?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseLinkerPanelResult {
  data: BuildLinkerPanelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Fetches the typed-detail endpoint; re-fetches when any input changes.
 *  Unlike the sovereign panel's paired-leg lists there is NO client-side
 *  pairing invariant to pre-validate — ``curve_families`` is a single
 *  optional scope (and there is no tenors knob at all: linkers are
 *  specific-maturity bonds); the closed family set is enforced
 *  authoritatively backend-side (422 surfaces verbatim). */
export function useLinkerPanel(args: UseLinkerPanelArgs): UseLinkerPanelResult {
  const [data, setData] = useState<BuildLinkerPanelOutput | null>(null);
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

    const params: LinkerPanelDetailParams = {
      start_date: args.startDate,
      end_date: args.endDate || undefined,
      curve_families: families.length > 0 ? families : undefined,
      field_name: args.fieldName || undefined,
      calendar_policy: args.calendarPolicy || undefined,
      missing_data_policy: args.missingDataPolicy || undefined,
      as_of_date: args.asOfDate || undefined,
    };

    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailLinkerPanel(params)
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
  data: BuildLinkerPanelOutput,
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
 *  FAMILIES.  The FAMILIES caption carries the flag-decorated family
 *  tags off the per-tool registry. */
export function heroKPIs(
  data: BuildLinkerPanelOutput,
): ReadonlyArray<KPIDescriptor> {
  return [
    {
      label: 'COLUMNS',
      value: fmtInt(data.column_count),
      tone: 'neutral',
      emphasis: 'primary',
      caption: 'one per bond (vendor_ticker)',
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
      label: 'FAMILIES',
      value: fmtInt(data.curve_families.length),
      tone: 'neutral',
      caption: data.curve_families.map(familyTag).join(' · ') || undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Column roster — vendor_ticker keys + per-bond wire reference.
// ---------------------------------------------------------------------------

export interface LinkerPanelRosterRow {
  /** Canonical Bloomberg vendor_ticker Panel column key. */
  columnKey: string;
  /** Owning linker curve family ('—' when the wire reference lacks it). */
  curveFamily: string;
  /** Per-bond tenor bucket off the wire reference. */
  tenor: string;
  /** Per-bond maturity (YYYY-MM-DD) off the wire reference. */
  maturityDate: string;
  /** Closed-enum TimeSeriesUnits tag from ``units_by_column``. */
  unit: string;
}

/** One roster row per Panel column, in the wire's ``vendor_tickers``
 *  order.  Family / tenor / maturity come from the methodology_card's
 *  ``curve_family_reference`` bond blocks — wire data, NOT client-side
 *  ticker-mnemonic inference (FP9); bonds absent from the reference
 *  render '—'. */
export function buildColumnRoster(
  data: BuildLinkerPanelOutput,
): ReadonlyArray<LinkerPanelRosterRow> {
  const ref = data.methodology_card?.curve_family_reference ?? {};
  const byTicker = new Map<
    string,
    { family: string; tenor: string; maturity: string }
  >();
  for (const [family, famRef] of Object.entries(ref)) {
    for (const bond of famRef?.bonds ?? []) {
      if (!bond.vendor_ticker) continue;
      byTicker.set(bond.vendor_ticker, {
        family,
        tenor: bond.tenor ?? '—',
        maturity: bond.maturity_date ?? '—',
      });
    }
  }
  return data.vendor_tickers.map((ticker) => {
    const bond = byTicker.get(ticker);
    return {
      columnKey: ticker,
      curveFamily: bond?.family ?? '—',
      tenor: bond?.tenor ?? '—',
      maturityDate: bond?.maturity ?? '—',
      unit: data.units_by_column[ticker] ?? '—',
    };
  });
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Methodology card rows.  The backend's FOUR caveats
 *  (security_name / index_family / market_structure /
 *  cross_region_business_days) thread through VERBATIM, one row each
 *  (P5 — the disclosure block is the point of this tool).  The
 *  contract / column-key rows describe the wire shape; the policy rows
 *  echo the RESOLVED values off the wire (never re-derived client-side). */
export function buildMethodologyRows(
  data: BuildLinkerPanelOutput,
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
        'vendor_ticker — one canonical Bloomberg identifier per bond ' +
        '(linkers are specific-maturity bonds; no family|tenor pillar grid).',
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
  if (card.market_structure_caveat) {
    rows.push({
      label: 'Market-structure caveat',
      value: card.market_structure_caveat,
    });
  }
  if (card.cross_region_business_days_caveat) {
    rows.push({
      label: 'Calendar caveat',
      value: card.cross_region_business_days_caveat,
    });
  }
  if (card.methodology_label) {
    rows.push({ label: 'Methodology', value: card.methodology_label });
  }
  return rows;
}

function distinctUnits(data: BuildLinkerPanelOutput): string[] {
  return [...new Set(Object.values(data.units_by_column))];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'build_linker_panel/config.yaml' },
    { label: 'shared/artifacts/types.py::Panel' },
    { label: 'methodology_exposure.md §5' },
  ];
}
