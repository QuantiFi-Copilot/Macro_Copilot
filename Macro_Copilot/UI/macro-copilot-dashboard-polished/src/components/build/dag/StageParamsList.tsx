// ============================================================================
// StageParamsList — inline parameter summary inside a DAG stage card.
// ----------------------------------------------------------------------------
// PR C — refits to the mockup density: parameters render as inline
// dot-separated chips ("Instrument UST · Tenors 2Y,10Y · Field Mid
// Yield · Source Bloomberg") rather than a two-column grid.  The
// inline layout matches what Mockups B/C show on every stage card and
// reads as "this is the bound config" at a glance.  Rows wrap to a
// second line on overflow, but the chip count caps at ``maxRows``
// (default 4) to keep cards from ballooning vertically on wide-param
// primitives.
// ============================================================================

import { stageParamRows } from '@/components/build/lib/stageDisplay';
import type { NodeSummary } from '@/services/workspaceApi';

type Props = {
  node: NodeSummary;
  maxRows?: number;
};

export function StageParamsList({ node, maxRows = 4 }: Props) {
  const rows = stageParamRows(node, { maxRows });
  if (rows.length === 0) {
    return (
      <div className="px-4 pb-3 pt-2 text-[10.5px] italic text-fg-faint">
        No exposed parameters.
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 px-4 pb-3 pt-2 text-[10.5px]">
      {rows.map((row, i) => (
        <span
          key={row.label}
          className="flex min-w-0 items-baseline gap-1.5"
        >
          <span className="font-medium uppercase tracking-[0.14em] text-fg-faint">
            {row.label}
          </span>
          <span className="min-w-0 truncate font-mono text-fg-secondary">
            {row.value}
          </span>
          {i < rows.length - 1 && (
            <span aria-hidden className="ml-1 text-fg-faint/55">
              ·
            </span>
          )}
        </span>
      ))}
    </div>
  );
}
