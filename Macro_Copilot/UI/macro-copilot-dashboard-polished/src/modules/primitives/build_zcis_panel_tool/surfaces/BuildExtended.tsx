// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// ``build_zcis_panel_tool``.  PANEL-CONTRACT shape.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive claiming
// ``custom_build_surface`` ships an extended view; this is the ZCIS panel's
// full canvas.  Mounted by VirtualPrimitiveCanvas for single-tool queries OR
// by the click-to-expand modal from the compact card.
//
// PANEL shape (settled): the wire carries the panel's METADATA CONTRACT
// (dims / vendor_ticker columns / date range / units / methodology_card) —
// NOT the cell data, which stays a workflow-side Panel artifact (the MCP
// layer and the detail route both drop it).  So this canvas is a CONTRACT
// card: controls → hero KPI strip → column roster → per-family index
// reference → verbatim methodology disclosures → lineage.  No chart — there
// is no series on the wire to draw (THESIS Q3; FP9 forbids fabricating one
// client-side).  Layout composes the shared ControlsStrip / KPIStrip /
// MethodologyCard / LineageFooter / FreshnessPill chrome (scanner-extended
// precedent for non-chart shapes).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { Boxes, Info } from 'lucide-react';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  ControlsStrip,
  FreshnessPill,
  KPIStrip,
  LineageFooter,
  MethodologyCard,
  asOfDateControl,
  type BuildExtendedProps,
  type ControlDescriptor,
} from '@/components/shared/build';
import {
  CALENDAR_POLICY_OPTIONS,
  DEFAULT_CURVE_FAMILIES_CSV,
  DEFAULT_TENORS_CSV,
  MISSING_DATA_POLICY_OPTIONS,
  PANEL_CONTRACT_NOTE,
  YAML_DEFAULT_FIELD_NAME,
  buildColumnRoster,
  buildFamilyReference,
  buildMethodologyRows,
  buildReferenceChips,
  defaultWindow,
  fmtInt,
  heroKPIs,
  useZcisPanel,
  type ZcisFamilyReferenceRow,
  type ZcisPanelRosterRow,
} from './zcisPanelShared';

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  // Date defaults computed at RENDER time (FM7 — no Date.now in module.ts).
  const fallbackWindow = defaultWindow();
  const curveFamiliesCsv =
    params.curve_families || DEFAULT_CURVE_FAMILIES_CSV;
  const tenorsCsv = params.tenors || DEFAULT_TENORS_CSV;
  const startDate = params.start_date || fallbackWindow.start;
  const endDate = params.end_date || fallbackWindow.end;
  const fieldName = params.field_name || '';
  const calendarPolicy = params.calendar_policy || '';
  const missingDataPolicy = params.missing_data_policy || '';

  const { data, isLoading, errorMessage } = useZcisPanel({
    curveFamiliesCsv,
    tenorsCsv,
    startDate,
    endDate,
    fieldName,
    calendarPolicy,
    missingDataPolicy,
    asOfDate: params.as_of_date,
  });

  // ----- URL update on control change (pilot pushParams pattern) -----
  const pushParams = (nextParams: Record<string, string>) => {
    // Stage D — inside the multi-tool DAG expand-to-modal, edits stay
    // local (onParamsChange) instead of navigating the global URL.
    if (onParamsChange) {
      onParamsChange(nextParams);
      return;
    }
    const nextCtx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: toolName, params: nextParams }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${nextCtx}`, { replace: true });
  };

  const handleControlChange = (name: string, value: string) => {
    pushParams({ ...params, [name]: value });
  };

  const handleReset = () => {
    pushParams({
      curve_families: DEFAULT_CURVE_FAMILIES_CSV,
      tenors: DEFAULT_TENORS_CSV,
      start_date: fallbackWindow.start,
      end_date: fallbackWindow.end,
      field_name: '',
      calendar_policy: '',
      missing_data_policy: '',
    });
  };

  // ----- Controls — the exposed Input fields -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_families',
      label: 'ZCIS families (CSV)',
      kind: 'text',
      value: curveFamiliesCsv,
    },
    {
      name: 'tenors',
      label: 'Tenors (CSV, blank → all)',
      kind: 'text',
      value: tenorsCsv,
    },
    {
      name: 'start_date',
      label: 'Start (YYYY-MM-DD)',
      kind: 'text',
      value: startDate,
    },
    {
      name: 'end_date',
      label: 'End (YYYY-MM-DD)',
      kind: 'text',
      value: endDate,
    },
    {
      name: 'field_name',
      label: `field_name (blank → ${YAML_DEFAULT_FIELD_NAME})`,
      kind: 'text',
      value: fieldName,
      advanced: true,
    },
    {
      name: 'calendar_policy',
      label: 'Calendar policy',
      kind: 'enum',
      value: calendarPolicy,
      options: CALENDAR_POLICY_OPTIONS,
      advanced: true,
    },
    {
      name: 'missing_data_policy',
      label: 'Missing-data policy',
      kind: 'enum',
      value: missingDataPolicy,
      options: MISSING_DATA_POLICY_OPTIONS,
      advanced: true,
    },
    asOfDateControl(params.as_of_date),
  ];

  const roster = data ? buildColumnRoster(data) : [];
  const familyReference = data ? buildFamilyReference(data) : [];

  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-y-auto"
      data-testid="zcis-panel-extended"
    >
      {/* ---------- Title section ---------- */}
      <section className="grid grid-cols-1 gap-4 border-b border-line-subtle px-6 pt-5 pb-5 lg:grid-cols-[1fr_auto]">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-fg-muted">
            <Boxes size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
            <span className="font-semibold">ZCIS PANEL</span>
            <span className="text-fg-faint" aria-hidden>•</span>
            <span>PANEL BUILDER</span>
            <span className="text-fg-faint" aria-hidden>•</span>
            <span>CONTRACT</span>
          </div>
          <div className="flex items-baseline gap-3">
            <h1 className="text-[24px] font-semibold leading-tight text-fg-primary">
              ZCIS Panel
            </h1>
            <span className="text-[13px] text-fg-secondary">
              (cross-curve inflation-swap substrate)
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-fg-secondary">
            {data && (
              <>
                <span>
                  {fmtInt(data.column_count)} columns ×{' '}
                  {fmtInt(data.row_count)} rows
                </span>
                <span className="text-fg-faint" aria-hidden>·</span>
                <span>
                  {data.start_date} → {data.end_date}
                </span>
              </>
            )}
            <FreshnessPill freshness="fresh" />
          </div>
        </div>
        <ContractNoteCard />
      </section>

      {/* ---------- Controls strip ---------- */}
      <div className="px-6 pt-4">
        <ControlsStrip
          controls={controls}
          onChange={handleControlChange}
          onReset={handleReset}
        />
      </div>

      {/* ---------- Hero KPI strip ---------- */}
      <div className="px-6 pt-4">
        <KPIStrip
          kpis={
            data
              ? heroKPIs(data)
              : [
                  { label: 'COLUMNS', value: '—' },
                  { label: 'ROWS', value: '—' },
                  { label: 'DATE RANGE', value: '—' },
                  { label: 'FAMILIES × TENORS', value: '—' },
                ]
          }
        />
      </div>

      {/* ---------- Column roster ---------- */}
      <div className="px-6 pt-4">
        <RosterTable
          rows={roster}
          isLoading={isLoading}
          errorMessage={errorMessage ?? undefined}
        />
      </div>

      {/* ---------- Per-family index reference ---------- */}
      <div className="px-6 pt-4">
        <FamilyReferenceTable rows={familyReference} isLoading={isLoading} />
      </div>

      {/* ---------- Methodology ---------- */}
      <div className="px-6 pt-4 pb-6">
        <MethodologyCard
          rows={data ? buildMethodologyRows(data, fieldName) : []}
          references={buildReferenceChips()}
        />
      </div>

      <LineageFooter
        lineage={{
          toolName,
          version: 'v1',
          kind: 'Panel builder (metadata contract)',
          providers: ['TimescaleDB', 'macro_data.v_market_data_daily_enriched'],
          asOf: data?.end_date,
          freshness: 'fresh',
        }}
      />
    </div>
  );
};

export default BuildExtended;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ContractNoteCard() {
  return (
    <div className="card flex max-w-[340px] items-start gap-2.5 px-4 py-3">
      <Info size={14} strokeWidth={1.5} className="mt-0.5 shrink-0 text-ice-300" aria-hidden />
      <div className="flex flex-col gap-1">
        <span className="kicker text-fg-muted">PANEL CONTRACT</span>
        <span className="text-[11.5px] leading-snug text-fg-secondary">
          {PANEL_CONTRACT_NOTE}
        </span>
      </div>
    </div>
  );
}

// The roster carries vendor_ticker + unit ONLY — the wire has no
// per-column family/tenor mapping, and deriving one by parsing ticker
// mnemonics would be client-side inference (FP9).  The per-FAMILY index
// conventions live in FamilyReferenceTable below.
function RosterTable({
  rows,
  isLoading,
  errorMessage,
}: {
  rows: ReadonlyArray<ZcisPanelRosterRow>;
  isLoading?: boolean;
  errorMessage?: string;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="border-b border-line-subtle px-4 py-3">
        <span className="kicker text-fg-muted">
          Column roster — vendor_ticker keys
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] text-[12.5px]">
          <thead>
            <tr className="border-b border-line-subtle text-left text-[10.5px] uppercase tracking-wide text-fg-muted">
              <th className="px-4 py-2 font-normal">#</th>
              <th className="px-3 py-2 font-normal">Column key (vendor_ticker)</th>
              <th className="px-4 py-2 text-right font-normal">Unit</th>
            </tr>
          </thead>
          <tbody>
            {errorMessage ? (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-coral-300">
                  {errorMessage}
                </td>
              </tr>
            ) : isLoading ? (
              [0, 1, 2, 3].map((i) => (
                <tr key={`sk-${i}`} className="border-b border-line-subtle">
                  <td className="px-4 py-2">
                    <div className="h-3 w-4 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2">
                    <div className="h-3 w-32 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="ml-auto h-3 w-12 animate-pulse rounded bg-line-subtle" />
                  </td>
                </tr>
              ))
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-fg-secondary">
                  No columns resolved — check the family / tenor scopes.
                </td>
              </tr>
            ) : (
              rows.map((row, i) => (
                <tr
                  key={row.columnKey}
                  className="border-b border-line-subtle hover:bg-surface-overlay/60"
                >
                  <td className="px-4 py-2 font-mono text-[11.5px] text-fg-muted">
                    {i + 1}
                  </td>
                  <td className="px-3 py-2 font-mono text-[12px] text-fg-primary">
                    {row.columnKey}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-[12px] text-fg-secondary">
                    {row.unit}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Per-family index conventions off the wire's ``curve_family_reference``
// block — VERBATIM values (P5).  This table is the panel's "you are mixing
// three inflation regimes" disclosure made scannable: CPI-U vs HICP-xT vs
// RPI, each with its own index_lag / interpolation convention.
function FamilyReferenceTable({
  rows,
  isLoading,
}: {
  rows: ReadonlyArray<ZcisFamilyReferenceRow>;
  isLoading?: boolean;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="border-b border-line-subtle px-4 py-3">
        <span className="kicker text-fg-muted">
          Index reference — per-family conventions (verbatim)
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-[12.5px]">
          <thead>
            <tr className="border-b border-line-subtle text-left text-[10.5px] uppercase tracking-wide text-fg-muted">
              <th className="px-4 py-2 font-normal">Curve family</th>
              <th className="px-3 py-2 font-normal">Index family</th>
              <th className="px-3 py-2 font-normal">Index lag</th>
              <th className="px-3 py-2 font-normal">Interpolation</th>
              <th className="px-4 py-2 font-normal">Underlying index</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              [0, 1, 2].map((i) => (
                <tr key={`sk-${i}`} className="border-b border-line-subtle">
                  {[0, 1, 2, 3, 4].map((j) => (
                    <td key={j} className={j === 0 || j === 4 ? 'px-4 py-2' : 'px-3 py-2'}>
                      <div className="h-3 w-16 animate-pulse rounded bg-line-subtle" />
                    </td>
                  ))}
                </tr>
              ))
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-fg-secondary">
                  No per-family reference on the methodology card.
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr
                  key={row.curveFamily}
                  className="border-b border-line-subtle hover:bg-surface-overlay/60"
                >
                  <td className="px-4 py-2 font-mono text-[12px] text-fg-primary">
                    {row.curveFamily}
                  </td>
                  <td className="px-3 py-2 font-mono text-[12px] text-fg-secondary">
                    {row.inflationIndexFamily}
                  </td>
                  <td className="px-3 py-2 font-mono text-[12px] text-fg-secondary">
                    {row.indexLag}
                  </td>
                  <td className="px-3 py-2 font-mono text-[12px] text-fg-secondary">
                    {row.interpolation}
                  </td>
                  <td className="px-4 py-2 font-mono text-[12px] text-fg-secondary">
                    {row.underlyingIndex}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
