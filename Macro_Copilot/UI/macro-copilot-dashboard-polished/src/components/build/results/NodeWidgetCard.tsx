// ============================================================================
// NodeWidgetCard — single per-node card on the Results tab.
// ----------------------------------------------------------------------------
// Wraps Monitor's ``WidgetCard`` shell so every node card on Build has
// the exact same chrome as Monitor (gradient rail, layered shadow,
// provenance footer).  Picks the body renderer from the node
// renderer registry; falls back to the registry's fallback when the
// artifact type is unrecognised.
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
import {
  prettyStageTitle,
  stageKindLabel,
} from '@/components/build/lib/stageDisplay';
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
  const kicker = stageKindLabel(node.kind);

  // Always read from the registry — branchless w.r.t. artifact type.
  const Renderer = useMemo(
    () =>
      node.artifact
        ? resolveNodeRenderer(node, node.artifact)
        : null,
    [node],
  );

  return (
    <WidgetCard
      category={widgetCategory}
      size={size}
      isEditing={false}
    >
      <WidgetHeader kicker={kicker} title={title} />

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
