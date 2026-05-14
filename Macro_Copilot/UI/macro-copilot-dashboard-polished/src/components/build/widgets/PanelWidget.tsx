// ============================================================================
// PanelWidget — payload-backed renderer for Panel artifacts.
// ----------------------------------------------------------------------------
// PR4 — loads the persisted Panel body via the PR3 hook and renders
// real columns + rows rather than the pre-PR4 sparkline-of-first-
// column-only proxy.  Two visual modes:
//
//   1. One-row Panel (e.g. a backtest summary / scalar-summary panel):
//        render each column as a labelled metric cell (kicker + value).
//        This is the shape Mockup C's "Backtest Summary" tile uses,
//        only now the values come from the real payload instead of a
//        "summary will surface in the detail view" stub.
//
//   2. Multi-row Panel (e.g. a sovereign yield panel):
//        render a compact preview table — first/last N rows × first
//        few columns — with overflow indicators ("+12 more rows", "+5
//        more columns") so the analyst knows the panel is bigger than
//        the card.  Cell values use ``formatNumberWithUnits`` keyed
//        off the per-column units metadata.
//
// What this widget renders correctly (vs pre-PR4)
// -----------------------------------------------
//   - Real column names from ``payload.columns``.
//   - Real row values from ``payload.data`` indexed by
//     ``(rowIdx, columnIdx)``.
//   - Per-column units from ``metadata.units_by_column`` (bps panels
//     get ``"X bps"``, percent panels get ``"X.YZ%"``).
//   - Real row + column counts.
//   - Dates from ``payload.index`` formatted defensively (no 1970
//     fallback).
//   - Null cells render as ``"—"``, not as ``"0"`` or ``"NaN"``.
//
// What this widget DOES NOT do
// ----------------------------
//   - Never reads the artifact-summary preview-values strip as the
//     body — that was a sparkline of the first column only.
//   - Never invents column labels or row indices.
//   - Never renders 1900/1970-shaped dates for missing inputs.
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { PayloadShell } from './shared/PayloadShell';
import {
  classifyArtifactDate,
  formatDate,
  formatNumberWithUnits,
  panelCell,
  panelColumns,
  panelRowIndex,
  MISSING_VALUE_DASH,
  type PanelPayloadEnvelope,
} from './shared/artifactFormat';

const COLUMN_PREVIEW_LIMIT = 4;
const ROW_PREVIEW_LIMIT_HEAD = 3;
const ROW_PREVIEW_LIMIT_TAIL = 2;

const PanelWidget: NodeRenderer = ({ node, artifact, size }) => {
  return (
    <PayloadShell<PanelPayloadEnvelope>
      artifactHash={node.artifact_hash ?? artifact.hash}
      expectedType="Panel"
      displayName="Panel"
      isEmpty={(p) => panelRowIndex(p).length === 0}
      emptyMessage="The panel persisted but contains no rows."
    >
      {(payload) => <PanelBody payload={payload} size={size} />}
    </PayloadShell>
  );
};

function PanelBody({
  payload,
  size,
}: {
  payload: PanelPayloadEnvelope;
  size: 'small' | 'medium' | 'wide' | 'tall';
}) {
  const columns = panelColumns(payload);
  const rowIndex = panelRowIndex(payload);
  const unitsByColumn = payload.metadata.units_by_column ?? {};

  // One-row summary panel — metric strip.
  if (rowIndex.length === 1) {
    return <OneRowSummary payload={payload} />;
  }

  // Multi-row panel — head/tail preview table.
  return (
    <MultiRowPreview
      payload={payload}
      columns={columns}
      rowIndex={rowIndex}
      unitsByColumn={unitsByColumn}
      compact={size === 'small'}
    />
  );
}

// ---------------------------------------------------------------------------
// One-row summary panel — every column becomes a labelled metric cell.
// ---------------------------------------------------------------------------

function OneRowSummary({ payload }: { payload: PanelPayloadEnvelope }) {
  const columns = panelColumns(payload);
  const unitsByColumn = payload.metadata.units_by_column ?? {};
  const rowDate = payload.payload.index?.[0] ?? null;
  // PR2 — classify the as-of date so the footer doesn't leak
  // ``summarize_series``-style ``1900-01-01`` sentinel rows or other
  // synthetic markers.  Real dates display normally; sentinels +
  // unparseable values suppress the line entirely (the card is a
  // metric-strip already; the date adds no signal in those cases).
  const rowDateClass = classifyArtifactDate(rowDate);
  const showAsOf = rowDateClass.kind === 'real_date';
  return (
    <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-4">
      <div className="kicker text-fg-muted">
        Summary · 1 row · {columns.length} column{columns.length === 1 ? '' : 's'}
        {rowDateClass.kind === 'summary_sentinel' && (
          <span className="ml-2 rounded-sm border border-violet-400/30 bg-violet-500/10 px-1.5 py-0.5 normal-case tracking-normal text-violet-200">
            scalar marker
          </span>
        )}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        {columns.map((col, i) => {
          const value = panelCell(payload, 0, i);
          return (
            <div key={col} className="flex min-w-0 flex-col gap-0.5">
              <span className="text-[9.5px] font-medium uppercase tracking-[0.14em] text-fg-faint">
                {col}
              </span>
              <span className="font-mono text-[13px] tabular-nums text-fg-primary">
                {formatNumberWithUnits(value, unitsByColumn[col])}
              </span>
            </div>
          );
        })}
      </div>
      {showAsOf && (
        <div className="mt-3 font-mono text-[10px] text-fg-faint">
          as-of {formatDate(rowDate)}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Multi-row preview — first N + last M rows × first K columns.
// ---------------------------------------------------------------------------

function MultiRowPreview({
  payload,
  columns,
  rowIndex,
  unitsByColumn,
  compact,
}: {
  payload: PanelPayloadEnvelope;
  columns: string[];
  rowIndex: string[];
  unitsByColumn: Record<string, string>;
  compact: boolean;
}) {
  const visibleColumns = useMemo(
    () => columns.slice(0, COLUMN_PREVIEW_LIMIT),
    [columns],
  );
  const omittedColumns = Math.max(0, columns.length - visibleColumns.length);
  const rowSlots = useMemo(
    () => buildRowSlots(rowIndex.length, compact),
    [rowIndex.length, compact],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-3">
      <div className="kicker text-fg-muted">
        {rowIndex.length.toLocaleString()} rows · {columns.length} column
        {columns.length === 1 ? '' : 's'}
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="min-w-full border-collapse text-[10.5px] tabular-nums">
          <thead>
            <tr className="border-b border-line-subtle">
              <th className="py-1 pr-3 text-left font-medium uppercase tracking-[0.1em] text-fg-faint">
                date
              </th>
              {visibleColumns.map((col) => (
                <th
                  key={col}
                  className="py-1 pr-3 text-right font-medium uppercase tracking-[0.1em] text-fg-faint"
                >
                  {col}
                </th>
              ))}
              {omittedColumns > 0 && (
                <th className="py-1 text-right font-medium text-fg-faint">
                  +{omittedColumns}
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {rowSlots.map((slot) =>
              slot === 'ellipsis' ? (
                <tr key="ellipsis" className="border-t border-line-subtle/60">
                  <td
                    className="py-1 text-fg-faint"
                    colSpan={visibleColumns.length + 1 + (omittedColumns > 0 ? 1 : 0)}
                  >
                    …
                  </td>
                </tr>
              ) : (
                <tr key={`r${slot}`} className="border-t border-line-subtle/40">
                  <td className="py-1 pr-3 font-mono text-fg-secondary">
                    {classifyArtifactDate(rowIndex[slot]).kind === 'real_date'
                      ? formatDate(rowIndex[slot])
                      : MISSING_VALUE_DASH}
                  </td>
                  {visibleColumns.map((col, ci) => {
                    const value = panelCell(payload, slot, ci);
                    return (
                      <td
                        key={col}
                        className="py-1 pr-3 text-right font-mono text-fg-primary"
                      >
                        {formatNumberWithUnits(value, unitsByColumn[col])}
                      </td>
                    );
                  })}
                  {omittedColumns > 0 && (
                    <td className="py-1 text-right font-mono text-fg-faint">
                      {MISSING_VALUE_DASH}
                    </td>
                  )}
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Pick which row indices to render in the preview table: first N
 *  rows + ``"ellipsis"`` + last M rows.  For short panels (<= N + M)
 *  we render every row consecutively. */
function buildRowSlots(
  totalRows: number,
  compact: boolean,
): Array<number | 'ellipsis'> {
  const head = compact ? 2 : ROW_PREVIEW_LIMIT_HEAD;
  const tail = compact ? 1 : ROW_PREVIEW_LIMIT_TAIL;
  if (totalRows <= head + tail) {
    return Array.from({ length: totalRows }, (_, i) => i);
  }
  const slots: Array<number | 'ellipsis'> = [];
  for (let i = 0; i < head; i++) slots.push(i);
  slots.push('ellipsis');
  for (let i = totalRows - tail; i < totalRows; i++) slots.push(i);
  return slots;
}

registerArtifactRenderer('Panel', PanelWidget);
export { PanelWidget };
