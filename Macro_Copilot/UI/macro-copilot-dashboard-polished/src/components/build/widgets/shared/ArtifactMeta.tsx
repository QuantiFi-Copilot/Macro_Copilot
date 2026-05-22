// ============================================================================
// ArtifactMeta — provenance footer adapter for build widgets.
// ----------------------------------------------------------------------------
// Wraps Monitor's ``WidgetProvenance`` so each build widget's footer
// renders identically to Monitor (same lineage chip + tool / date /
// duration row) while sourcing its inputs from the workspace artifact
// summary.
//
// Lives in shared/ rather than inline at each widget so the toString
// formatting + null-handling are centralised — there's exactly one
// place to extend when we want to add (say) a "live" badge on rows
// whose ``as_of_date`` is today.
// ============================================================================

import { WidgetProvenance } from '@/components/monitor/WidgetCard';
import type { ArtifactSummary, NodeSummary } from '@/services/workspaceApi';

type Props = {
  node: NodeSummary;
  artifact: ArtifactSummary;
};

export function ArtifactMeta({ node, artifact }: Props) {
  const toolName = displayToolName(node);
  // ``created_at`` on the artifact summary is an ISO string from the
  // store; trim to YYYY-MM-DD for the footer so it lines up with
  // Monitor's date pill.
  const asOfDate = artifact.created_at
    ? artifact.created_at.slice(0, 10)
    : null;
  return (
    <WidgetProvenance
      toolName={toolName}
      asOfDate={asOfDate}
      lineageHash={artifact.hash.slice(0, 6)}
    />
  );
}

function displayToolName(node: NodeSummary): string {
  // Prefer the substrate's persisted display name; fall back to the
  // node_id if the row pre-dates the name column population.
  return node.name && node.name.trim().length > 0
    ? node.name
    : node.node_id;
}
