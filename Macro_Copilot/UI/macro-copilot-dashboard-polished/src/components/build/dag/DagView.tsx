// ============================================================================
// DagView — horizontal flow of stage cards for a completed workspace.
// ----------------------------------------------------------------------------
// Renders the workspace's nodes as a left-to-right strip of stage
// cards with hairline edge connectors between adjacent stages.  The
// substrate's persisted edges drive the topo-sort; the visual layout
// approximates branching topologies as a single linear flow in V1
// (see ``EdgeConnector`` for the rationale + the PR B note on
// upgrading to a layered renderer).
//
// Scrolls horizontally on narrow viewports; stage cards have a fixed
// max-width so cards never collapse to unreadable widths on a long
// DAG.
// ============================================================================

import { useMemo } from 'react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { StageCard } from './StageCard';
import { EdgeConnector } from './EdgeConnector';
import { topologicalOrder } from '../lib/topologicalOrder';

type Props = {
  detail: WorkspaceDetail;
};

export function DagView({ detail }: Props) {
  const ordered = useMemo(
    () => topologicalOrder(detail.nodes, detail.edges),
    [detail.nodes, detail.edges],
  );

  // Build a quick lookup from (from_node, to_node) → slot_name so
  // each edge connector's hover-tooltip can name the slot.
  const slotByEdge = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of detail.edges) {
      m.set(`${e.from_node}→${e.to_node}`, e.slot_name);
    }
    return m;
  }, [detail.edges]);

  if (ordered.length === 0) {
    return <EmptyDag />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-4 px-6 py-6">
      <SectionHeader nodeCount={ordered.length} />
      <div
        // Horizontal scroll on overflow; pb-1 reserves space for the
        // research-card hover lift so it doesn't get clipped.
        className="-mx-1 flex min-w-0 items-stretch gap-1 overflow-x-auto px-1 pb-1"
      >
        {ordered.map((node, i) => {
          const next = ordered[i + 1];
          const slotHint = next
            ? slotByEdge.get(`${node.node_id}→${next.node_id}`)
            : undefined;
          const isTerminal = node.node_id === detail.focus_node;
          return (
            <div key={node.node_id} className="flex items-stretch">
              <StageCard
                node={node}
                workspace={detail}
                index={i + 1}
                isTerminal={isTerminal}
              />
              {next && (
                <EdgeConnector
                  hint={slotHint ? `slot: ${slotHint}` : undefined}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SectionHeader({ nodeCount }: { nodeCount: number }) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <h2 className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-muted">
          DAG · execution order
        </h2>
        <p className="mt-0.5 text-[11px] text-fg-faint">
          {nodeCount} {nodeCount === 1 ? 'stage' : 'stages'}, left-to-right in
          topological order.
        </p>
      </div>
    </div>
  );
}

function EmptyDag() {
  return (
    <div className="px-6 py-10">
      <div className="card flex h-[200px] items-center justify-center text-[12px] text-fg-muted">
        This workspace has no persisted nodes.
      </div>
    </div>
  );
}
