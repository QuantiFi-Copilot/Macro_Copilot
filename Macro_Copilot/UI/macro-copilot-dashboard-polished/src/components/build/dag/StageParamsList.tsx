// ============================================================================
// StageParamsList — inline parameter summary inside a DAG stage card.
// ----------------------------------------------------------------------------
// Tight label / value rows pulled via ``stageParamRows``.  Stage cards
// are narrow, so each row is single-line with truncation; the full
// param dict is available on the Parameters tab (PR B) when the
// summary isn't enough.
// ============================================================================

import { stageParamRows } from '@/components/build/lib/stageDisplay';
import type { NodeSummary } from '@/services/workspaceApi';

type Props = {
  node: NodeSummary;
  maxRows?: number;
};

export function StageParamsList({ node, maxRows = 5 }: Props) {
  const rows = stageParamRows(node, { maxRows });
  if (rows.length === 0) {
    return (
      <div className="px-4 pb-3 pt-2 text-[10.5px] italic text-fg-faint">
        No exposed parameters.
      </div>
    );
  }
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 px-4 pb-3 pt-2 text-[10.5px]">
      {rows.map((row) => (
        <div key={row.label} className="contents">
          <dt className="font-medium uppercase tracking-[0.12em] text-fg-faint">
            {row.label}
          </dt>
          <dd className="min-w-0 truncate font-mono text-fg-secondary">
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
