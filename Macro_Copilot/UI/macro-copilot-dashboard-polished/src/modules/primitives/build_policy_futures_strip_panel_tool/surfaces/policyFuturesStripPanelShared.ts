// ============================================================================
// policyFuturesStripPanelShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for
// ``build_policy_futures_strip_panel_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the sovereign sibling's
// sovereignYieldPanelShared.ts).  This module knows what a policy-futures
// STRIP PANEL is — a wide multi-instrument matrix of
// '<CURVE_FAMILY>|<STRIP_POSITION>' columns of IMPLIED RATES (percent,
// 100 − raw_price on inverse-priced cells) assembled for workflow /
// backtest consumption — and, critically, that the WIRE carries the
// panel's METADATA CONTRACT only (dims / column keys / date range /
// units / methodology card).  The assembled cell matrix is a
// workflow-side ``Panel`` artifact the MCP layer (and the detail route,
// which replicates the drop) strips before serialisation.  Neither view
// pretends otherwise.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME typed-detail
// endpoint (rendering_density.md §1.1 — the compact view just renders less).
// KPI / roster / methodology-row builders live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailPolicyFuturesStripPanel,
  type PolicyFuturesStripPanelDetailParams,
} from '@/services/ratesApi';
import type { BuildPolicyFuturesStripPanelOutput } from '@/types/rates';
import type {
  KPIDescriptor,
  MethodologyRow,
  ReferenceChip,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Curve-family metadata.  Policy-futures families are disjoint from the
// linker / sovereign registries — flag + label + regime live here in the
// per-tool layer (sibling precedent:
// policy_futures_get_futures_strip_snapshot_tool's
// policyFuturesStripSnapshotShared.ts keeps its own copy for the same
// reason; no cross-module imports).
// ---------------------------------------------------------------------------

export interface PolicyFuturesCurveMeta {
  family: string;
  flag: string;
  /** Short market label for the identity chip (e.g. 'SOFR'). */
  shortLabel: string;
  /** Long human-facing market name (e.g. 'US Fed SOFR strip'). */
  longLabel: string;
  /** 'RFR' (SOFR / SONIA compounded daily) or 'IBOR' (3M Euribor). */
  regime: 'RFR' | 'IBOR';
  /** Master-stem prefix for the strip slots (e.g. 'SFR' → 'SFR1'). */
  stripStemPrefix: string;
}

const CURVE_REGISTRY: Record<string, PolicyFuturesCurveMeta> = {
  SOFR_FUT: {
    family: 'SOFR_FUT',
    flag: '🇺🇸',
    shortLabel: 'SOFR',
    longLabel: 'US Fed SOFR strip',
    regime: 'RFR',
    stripStemPrefix: 'SFR',
  },
  EUR_SHORT_RATE_FUT: {
    family: 'EUR_SHORT_RATE_FUT',
    flag: '🇪🇺',
    shortLabel: 'Euribor',
    longLabel: 'ECB Euribor strip',
    regime: 'IBOR',
    stripStemPrefix: 'ER',
  },
  SONIA_FUT: {
    family: 'SONIA_FUT',
    flag: '🇬🇧',
    shortLabel: 'SONIA',
    longLabel: 'BOE SONIA strip',
    regime: 'RFR',
    stripStemPrefix: 'SFI',
  },
};

export function curveMetaFor(curveFamily: string): PolicyFuturesCurveMeta | null {
  return CURVE_REGISTRY[curveFamily] ?? null;
}

/** Whites (1-4) / Reds (5-8) segment tag per the V1 universe. */
export function stripSegmentLabel(stripPosition: number): 'WHITES' | 'REDS' | 'GREENS' {
  if (stripPosition <= 4) return 'WHITES';
  if (stripPosition <= 8) return 'REDS';
  return 'GREENS';
}

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

/** Default scope — BLANK on purpose.  Unlike the sovereign sibling
 *  (where the leg list is a REQUIRED Input field), ``curve_families``
 *  and ``strip_positions`` are optional on the backend Input: omitting
 *  them resolves to the FULL universe (SOFR_FUT / SONIA_FUT /
 *  EUR_SHORT_RATE_FUT × positions 1..8 = the 24-column cross-CB
 *  panel).  A blank Library invocation therefore renders the full
 *  honest contract instead of a 422. */
export const DEFAULT_CURVE_FAMILIES_CSV = '';
export const DEFAULT_STRIP_POSITIONS_CSV = '';

/** YAML defaults surfaced on the controls so an empty override reads
 *  honestly ("YAML default (…)" — the Optional→None passthrough never
 *  shadows config.yaml). */
export const YAML_DEFAULT_FIELD_NAME = 'PX_LAST';
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
  'assembled implied-rate matrix is a workflow-side Panel artifact and ' +
  'never crosses this wire.';

/** Static fallback caveat for the compact footer before data lands.
 *  Once the response arrives the VERBATIM wire caveat replaces it
 *  (P5 / FP9 — the methodology_card disclosures are the point of this
 *  tool; never TSX literals once the wire has spoken). */
export const STRIP_PANEL_COMPACT_CAVEAT_FALLBACK =
  'Policy-futures families only; panel cells stay workflow-side.';

/** The sharpest wire caveat for the compact footer — the backend's
 *  ``rolling_generic_strip_caveat`` (strip slots mix contracts across
 *  rolls; NOT a single underlying's price history), VERBATIM.  Falls
 *  back to the static line pre-data. */
export function compactCaveat(
  data: BuildPolicyFuturesStripPanelOutput | null,
): string {
  const caveat = data?.methodology_card?.rolling_generic_strip_caveat;
  return caveat || STRIP_PANEL_COMPACT_CAVEAT_FALLBACK;
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

/** Decompose one flat '<CURVE_FAMILY>|<STRIP_POSITION>' column key
 *  (split at the LAST '|' — family names never carry the separator,
 *  but lastIndexOf keeps the parse honest against forward-compatible
 *  keys).  Returns null on a key with no separator. */
export function parseColumnKey(
  columnKey: string,
): { curveFamily: string; stripPosition: number | null } | null {
  const i = columnKey.lastIndexOf('|');
  if (i <= 0) return null;
  const pos = Number.parseInt(columnKey.slice(i + 1), 10);
  return {
    curveFamily: columnKey.slice(0, i),
    stripPosition: Number.isNaN(pos) ? null : pos,
  };
}

/** Desk-recognised strip-slot stem for one column key — e.g.
 *  'SOFR_FUT|3' → 'SFR3', 'EUR_SHORT_RATE_FUT|8' → 'ER8'.  Falls back
 *  to the raw key for unknown families (display-only labelling; the
 *  wire key stays the addressing truth). */
export function stemLabelForColumnKey(columnKey: string): string {
  const parsed = parseColumnKey(columnKey);
  if (!parsed || parsed.stripPosition == null) return columnKey;
  const meta = curveMetaFor(parsed.curveFamily);
  if (!meta) return columnKey;
  return `${meta.stripStemPrefix}${parsed.stripPosition}`;
}

// ---------------------------------------------------------------------------
// Data hook — single source for both views.
// ---------------------------------------------------------------------------

export interface UsePolicyFuturesStripPanelArgs {
  /** Comma-joined curve families.  Empty → omitted → full universe. */
  curveFamiliesCsv: string;
  /** Comma-joined strip positions (ints 1..8).  Empty → omitted → full strip. */
  stripPositionsCsv: string;
  startDate: string;
  endDate?: string;
  /** Empty → omitted → YAML default (PX_LAST). */
  fieldName?: string;
  /** Empty → omitted → YAML default (business_days). */
  calendarPolicy?: string;
  /** Empty → omitted → YAML default (forward_fill_only). */
  missingDataPolicy?: string;
}

export interface UsePolicyFuturesStripPanelResult {
  data: BuildPolicyFuturesStripPanelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Fetches the typed-detail endpoint; re-fetches when any input changes.
 *  Pre-validates the strip-position CSV client-side so a non-integer
 *  entry reads as an actionable message instead of a raw 422 (the
 *  route's closed Literal enforces 1..8 authoritatively). */
export function usePolicyFuturesStripPanel(
  args: UsePolicyFuturesStripPanelArgs,
): UsePolicyFuturesStripPanelResult {
  const [data, setData] = useState<BuildPolicyFuturesStripPanelOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    const families = parseCsvParam(args.curveFamiliesCsv);
    const positionTokens = parseCsvParam(args.stripPositionsCsv);

    if (!args.startDate) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    const positions = positionTokens.map((t) => Number.parseInt(t, 10));
    if (positions.some((p) => Number.isNaN(p))) {
      setData(null);
      setIsLoading(false);
      setErrorMessage(
        `Strip positions must be integers 1..8; got '${args.stripPositionsCsv}'.`,
      );
      return;
    }

    const params: PolicyFuturesStripPanelDetailParams = {
      start_date: args.startDate,
      end_date: args.endDate || undefined,
      curve_families: families.length > 0 ? families : undefined,
      strip_positions: positions.length > 0 ? positions : undefined,
      field_name: args.fieldName || undefined,
      calendar_policy: args.calendarPolicy || undefined,
      missing_data_policy: args.missingDataPolicy || undefined,
    };

    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailPolicyFuturesStripPanel(params)
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
    args.stripPositionsCsv,
    args.startDate,
    args.endDate,
    args.fieldName,
    args.calendarPolicy,
    args.missingDataPolicy,
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
  data: BuildPolicyFuturesStripPanelOutput,
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
 *  FAMILIES.  Families read off the wire's ``curve_families`` echo
 *  directly (no client-side derivation needed — unlike the sovereign
 *  sibling, the families list IS a wire field). */
export function heroKPIs(
  data: BuildPolicyFuturesStripPanelOutput,
): ReadonlyArray<KPIDescriptor> {
  const familyLabels = data.curve_families.map(
    (f) => curveMetaFor(f)?.shortLabel ?? f,
  );
  return [
    {
      label: 'COLUMNS',
      value: fmtInt(data.column_count),
      tone: 'neutral',
      emphasis: 'primary',
      caption: 'family × strip slot',
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
      caption: familyLabels.join(' · ') || undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Column roster
// ---------------------------------------------------------------------------

export interface StripPanelRosterRow {
  /** Flat '<CURVE_FAMILY>|<STRIP_POSITION>' Panel column key. */
  columnKey: string;
  curveFamily: string;
  /** Desk stem label for the slot (e.g. 'SFR3'); raw key on unknowns. */
  stemLabel: string;
  /** Whites (1-4) / Reds (5-8) strip segment; '—' when unparsed. */
  segment: string;
  /** Closed-enum TimeSeriesUnits tag from ``units_by_column``. */
  unit: string;
}

export function buildColumnRoster(
  data: BuildPolicyFuturesStripPanelOutput,
): ReadonlyArray<StripPanelRosterRow> {
  return data.column_keys.map((col) => {
    const parsed = parseColumnKey(col);
    return {
      columnKey: col,
      curveFamily: parsed?.curveFamily ?? col,
      stemLabel: stemLabelForColumnKey(col),
      segment:
        parsed?.stripPosition != null
          ? stripSegmentLabel(parsed.stripPosition)
          : '—',
      unit: data.units_by_column[col] ?? '—',
    };
  });
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Methodology card rows.  The backend ``methodology_card``'s caveat
 *  fields thread through VERBATIM (P5 / FP9 — the inverse-pricing,
 *  rolling-generic and cross-region disclosures plus the per-family
 *  RFR/IBOR + Buba-mix lines are the point of this tool).  The
 *  contract / column-key fallback rows describe the wire shape, not
 *  finance. */
export function buildMethodologyRows(
  data: BuildPolicyFuturesStripPanelOutput,
  requestedFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const mc = data.methodology_card ?? {};
  const rows: MethodologyRow[] = [
    {
      label: 'Contract',
      value: PANEL_CONTRACT_NOTE,
    },
    {
      label: 'Column key',
      value:
        mc.column_axis_encoding ||
        "'<CURVE_FAMILY>|<STRIP_POSITION>' flat keys (e.g. 'SOFR_FUT|3') — one column per (family, slot) cell.",
    },
    {
      label: 'Value field',
      value: `${mc.panel_value_field ?? 'implied_rate_pct'} — cells are implied RATES in percent, not raw futures prices.`,
    },
    {
      label: 'Field',
      value: requestedFieldName
        ? `${requestedFieldName} (per-query override, all cells)`
        : `YAML default (${YAML_DEFAULT_FIELD_NAME})`,
    },
    {
      label: 'Units',
      value: distinctUnits(data).join(' · ') || '—',
    },
    {
      label: 'Calendar',
      value: [
        mc.calendar_policy ?? '—',
        mc.ffill_limit_days != null ? `ffill ≤ ${mc.ffill_limit_days}d` : null,
        mc.missing_data_policy ?? null,
      ]
        .filter((v): v is string => v != null)
        .join(' · '),
    },
  ];
  // VERBATIM wire caveats (P5) — pushed in wire order, skipped when the
  // backend omits one (forward-compatible; never substituted with a
  // TSX literal).
  if (mc.inverse_pricing_handling) {
    rows.push({ label: 'Inverse pricing', value: mc.inverse_pricing_handling });
  }
  if (mc.rolling_generic_strip_caveat) {
    rows.push({ label: 'Rolling generics', value: mc.rolling_generic_strip_caveat });
  }
  if (mc.cross_region_business_days_caveat) {
    rows.push({
      label: 'Cross-region calendar',
      value: mc.cross_region_business_days_caveat,
    });
  }
  // Per-family regime + Buba-mix disclosures (curve_family_reference),
  // VERBATIM, in the wire's curve_families order.
  const ref = mc.curve_family_reference ?? {};
  for (const family of data.curve_families) {
    const famRef = ref[family];
    if (!famRef) continue;
    if (famRef.regime_caveat) {
      rows.push({ label: family, value: famRef.regime_caveat });
    }
    if (famRef.buba_mix_caveat) {
      rows.push({ label: `${family} (Buba mix)`, value: famRef.buba_mix_caveat });
    }
  }
  return rows;
}

function distinctUnits(data: BuildPolicyFuturesStripPanelOutput): string[] {
  return [...new Set(Object.values(data.units_by_column))];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'build_policy_futures_strip_panel/config.yaml' },
    { label: 'shared/artifacts/types.py::Panel' },
    { label: 'ADR 0013 (policy_futures domain)' },
    { label: 'methodology_exposure.md §5' },
  ];
}
