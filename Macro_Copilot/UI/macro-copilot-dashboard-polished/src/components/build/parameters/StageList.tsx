// ============================================================================
// StageList — left rail listing every stage so the user can pick one.
// ----------------------------------------------------------------------------
// Topologically-ordered list of stage rows.  Each row shows the
// stage number, category-colored badge, tool/operator name, and a
// small bubble counting how many pending overrides target this
// stage's slots (so the user can see at a glance which stages have
// unsaved edits).
// ============================================================================

import { useMemo } from 'react';
import { cn } from '@/utils/cn';
import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
import {
  prettyStageTitle,
  stageKindLabel,
} from '@/components/build/lib/stageDisplay';
import {
  badgeClassesForStage,
  stageCategoryForNode,
} from '@/components/build/lib/stageCategory';
import { topologicalOrder } from '@/components/build/lib/topologicalOrder';
import type { OverrideMap } from './lib/controlSchema';

type Props = {
  detail: WorkspaceDetail;
  activeNodeId: string | null;
  onSelect: (nodeId: string) => void;
  overrides: OverrideMap;
};

export function StageList({
  detail,
  activeNodeId,
  onSelect,
  overrides,
}: Props) {
  const ordered = useMemo(
    () => topologicalOrder(detail.nodes, detail.edges),
    [detail.nodes, detail.edges],
  );

  // Count overrides per stage.  Override keys are either ``slot`` or
  // ``slot.field``; we look up the node whose params contain the
  // slot to scope the count.  Approximation: count overrides that
  // address a slot whose name appears in the node's params dict.
  const overrideCountByNode = useMemo(
    () => countOverridesPerStage(ordered, overrides),
    [ordered, overrides],
  );

  return (
    <ul className="flex flex-col gap-1 p-2">
      {ordered.map((node, i) => (
        <StageRow
          key={node.node_id}
          index={i + 1}
          node={node}
          workspace={detail}
          isActive={activeNodeId === node.node_id}
          onSelect={() => onSelect(node.node_id)}
          overrideCount={overrideCountByNode.get(node.node_id) ?? 0}
        />
      ))}
    </ul>
  );
}

function StageRow({
  index,
  node,
  workspace,
  isActive,
  onSelect,
  overrideCount,
}: {
  index: number;
  node: NodeSummary;
  workspace: WorkspaceDetail;
  isActive: boolean;
  onSelect: () => void;
  overrideCount: number;
}) {
  const category = stageCategoryForNode(node, workspace);
  const badgeCls = badgeClassesForStage(category);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className={cn(
          'group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors',
          isActive
            ? 'bg-ice-500/10 ring-1 ring-inset ring-ice-400/30'
            : 'hover:bg-white/[0.02]',
        )}
      >
        <span
          className={cn(
            'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border font-mono text-[10.5px] font-semibold',
            badgeCls.border,
            badgeCls.bg,
            badgeCls.text,
          )}
        >
          {index}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-medium tracking-[-0.005em] text-fg-primary">
            {prettyStageTitle(node.name ?? node.node_id)}
          </div>
          <div className="text-[10px] uppercase tracking-[0.16em] text-fg-faint">
            {stageKindLabel(node.kind)}
          </div>
        </div>
        {overrideCount > 0 && (
          <span
            title={`${overrideCount} pending override${overrideCount === 1 ? '' : 's'}`}
            className="rounded-sm border border-violet-400/45 bg-violet-500/15 px-1 py-px text-[9.5px] font-semibold text-violet-100"
          >
            {overrideCount}
          </span>
        )}
      </button>
    </li>
  );
}

/** Bucket pending overrides by the stage whose params they patch.
 *  Used by the StageRow's count badge.  An override addressing
 *  ``{slot}`` belongs to the node whose params blob contains that
 *  slot at top level; ``{slot.field}`` belongs to the same node. */
function countOverridesPerStage(
  nodes: NodeSummary[],
  overrides: OverrideMap,
): Map<string, number> {
  const counts = new Map<string, number>();
  if (Object.keys(overrides).length === 0) return counts;

  for (const node of nodes) {
    const raw = (node.params ?? {}) as Record<string, unknown>;
    const inner =
      typeof raw.params === 'object' && raw.params !== null
        ? (raw.params as Record<string, unknown>)
        : raw;
    const slotsOnThisNode = new Set([
      ...Object.keys(raw),
      ...Object.keys(inner),
    ]);

    let count = 0;
    for (const key of Object.keys(overrides)) {
      const slot = key.includes('.') ? key.split('.')[0] : key;
      if (slotsOnThisNode.has(slot)) count++;
    }
    if (count > 0) counts.set(node.node_id, count);
  }
  return counts;
}
