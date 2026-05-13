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
// Layout (matches mocks B/C):
//
//   PRIMITIVE       OPERATOR        OPERATOR        OUTPUT      + Add step
//   ┌────────┐  ►   ┌────────┐  ►   ┌────────┐  ►   ┌────────┐  ┌╴╴╴╴╴╴╴╴┐
//   │ stage  │      │ stage  │      │ stage  │      │ stage  │  ╵ ghost  ╵
//   └────────┘      └────────┘      └────────┘      └────────┘  └╴╴╴╴╴╴╴╴┘
//
// Each stage carries its own column-kicker on top so the user can
// scan the pipeline type at a glance.  An "Add step" ghost tile
// closes the strip — non-interactive in PR A, opens the operator
// picker in PR B.
//
// Scrolls horizontally on narrow viewports; stage cards have a fixed
// max-width so cards never collapse to unreadable widths on a long
// DAG.
// ============================================================================

import { useMemo } from 'react';
import { Plus } from 'lucide-react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { StageCard } from './StageCard';
import { EdgeConnector } from './EdgeConnector';
import { topologicalOrder } from '../lib/topologicalOrder';
import { stageCategoryForNode } from '../lib/stageCategory';
import { columnLabelForCategory } from '../lib/stageColumn';
import type { StageCategory } from '../lib/buildTypes';

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
        className="-mx-1 flex min-w-0 items-stretch gap-1 overflow-x-auto px-1 pb-2"
      >
        {ordered.map((node, i) => {
          const next = ordered[i + 1];
          const slotHint = next
            ? slotByEdge.get(`${node.node_id}→${next.node_id}`)
            : undefined;
          const isTerminal = node.node_id === detail.focus_node;
          const category = stageCategoryForNode(node, detail);
          return (
            <div key={node.node_id} className="flex items-stretch">
              <StageColumn category={category}>
                <StageCard
                  node={node}
                  workspace={detail}
                  index={i + 1}
                  isTerminal={isTerminal}
                />
              </StageColumn>
              {next && (
                <EdgeConnector
                  hint={slotHint ? `slot: ${slotHint}` : undefined}
                />
              )}
            </div>
          );
        })}
        <AddStepPlaceholder />
      </div>
    </div>
  );
}

/** Column wrapper around each stage card.  Renders the column kicker
 *  on top so the strip reads "PRIMITIVE  OPERATOR  OPERATOR  OUTPUT"
 *  at a glance, matching Mockup B/C. */
function StageColumn({
  category,
  children,
}: {
  category: StageCategory;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="kicker px-1 text-fg-muted">
        {columnLabelForCategory(category)}
      </span>
      {children}
    </div>
  );
}

/** Dashed-border tile at the end of the strip — non-interactive in
 *  PR A.  Tells the user the DAG is extensible without committing to
 *  the operator-picker UX. */
function AddStepPlaceholder() {
  return (
    <div className="flex flex-col gap-1.5 self-stretch">
      <span className="kicker px-1 text-fg-faint">&nbsp;</span>
      <div
        className="flex min-w-[150px] flex-1 flex-col items-center justify-center gap-1 rounded-[14px] border border-dashed border-line-soft px-4 py-6 text-center text-fg-faint"
        aria-hidden
      >
        <Plus size={14} strokeWidth={1.5} className="text-fg-faint" />
        <span className="text-[11px] font-medium text-fg-muted">Add step</span>
        <span className="text-[10px] leading-[1.4] text-fg-faint">
          Drop an operator
          <br /> or primitive here
        </span>
      </div>
    </div>
  );
}

function SectionHeader({ nodeCount }: { nodeCount: number }) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <h2 className="kicker text-fg-muted">DAG · execution order</h2>
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
