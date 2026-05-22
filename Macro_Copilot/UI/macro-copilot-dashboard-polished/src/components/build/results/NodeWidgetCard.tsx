// ============================================================================
// NodeWidgetCard — single per-node card on the Results tab.
// ----------------------------------------------------------------------------
// Wraps Monitor's ``WidgetCard`` shell so every node card on Build has
// the exact same chrome as Monitor (gradient rail, layered shadow,
// provenance footer).  Picks the body renderer from the node
// renderer registry; falls back to the registry's fallback when the
// artifact type is unrecognised.
//
// Card layout (matches mockups B/C):
//
//   ┌─────────────────────────────────────────────────────┐
//   │ PRIMITIVE                                <lineage>  │  ← kicker + chip
//   │ <Pretty Stage Title>                                │
//   │ ─────────────────────────────────────────────────── │
//   │ <renderer body — sparkline / panel / scalar …>     │
//   │ ─────────────────────────────────────────────────── │
//   │ lineage <hash> · tool_name · 2026-05-12 · 12ms      │  ← provenance footer
//   └─────────────────────────────────────────────────────┘
//
// The header kicker uses the same column vocabulary the DAG strip
// emits ("Primitive" / "Operator" / "Output") so a stage's identity
// reads consistently between the DAG tab and the Results tab.  The
// lineage chip in the top-right is a quick "this is the artifact
// behind this card" anchor — clicking it (PR follow-up) opens the
// node's artifact in the inspector.
//
// When ``artifact`` is null (node persisted without an artifact —
// shouldn't happen for completed workflows, but the substrate
// allows it), we render a "no artifact" placeholder so the layout
// stays intact.
// ============================================================================

import { useMemo } from 'react';
import {
  WidgetCard,
  WidgetHeader,
} from '@/components/monitor/WidgetCard';
import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
import {
  resolveNodeRenderer,
} from '@/components/build/lib/nodeRendererRegistry';
import {
  stageCategoryForNode,
  widgetCategoryForStage,
} from '@/components/build/lib/stageCategory';
import { prettyStageTitle } from '@/components/build/lib/stageDisplay';
import { columnLabelForCategory } from '@/components/build/lib/stageColumn';
import { ArtifactMeta } from '@/components/build/widgets/shared/ArtifactMeta';
import type { WidgetSize } from '@/components/monitor/registry';

type Props = {
  node: NodeSummary;
  workspace: WorkspaceDetail;
  size: WidgetSize;
};

export function NodeWidgetCard({ node, workspace, size }: Props) {
  const stageCategory = stageCategoryForNode(node, workspace);
  const widgetCategory = widgetCategoryForStage(stageCategory);

  const title = prettyStageTitle(node.name ?? node.node_id);
  const kicker = columnLabelForCategory(stageCategory);

  // Always read from the registry — branchless w.r.t. artifact type.
  const Renderer = useMemo(
    () =>
      node.artifact
        ? resolveNodeRenderer(node, node.artifact)
        : null,
    [node],
  );

  // Short lineage hash for the header meta slot.  Trimmed to the
  // conventional 8 chars so the chip stays compact; the full hash is
  // still in the provenance footer.
  const headerLineage = node.artifact?.hash
    ? node.artifact.hash.slice(0, 8)
    : null;

  return (
    <WidgetCard
      category={widgetCategory}
      size={size}
      isEditing={false}
    >
      <WidgetHeader
        kicker={kicker}
        title={title}
        meta={
          headerLineage ? (
            <span
              className="lineage-chip"
              title={`Artifact lineage ${node.artifact?.hash}`}
            >
              <span className="opacity-70">lineage</span>
              <span>{headerLineage}</span>
            </span>
          ) : null
        }
      />

      {Renderer && node.artifact ? (
        <Renderer
          node={node}
          artifact={node.artifact}
          category={stageCategory}
          workspace={workspace}
          size={size}
        />
      ) : (
        <MissingArtifact />
      )}

      {node.artifact && (
        <ArtifactMeta node={node} artifact={node.artifact} />
      )}
    </WidgetCard>
  );
}

function MissingArtifact() {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-5 py-4 text-[11px] text-fg-faint">
      No artifact recorded for this node.
    </div>
  );
}
