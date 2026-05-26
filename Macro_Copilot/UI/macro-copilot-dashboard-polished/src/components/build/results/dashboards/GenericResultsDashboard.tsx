// ============================================================================
// GenericResultsDashboard — payload-backed artifact grid.
// ----------------------------------------------------------------------------
// PR7 — extracted from the pre-PR7 ``ResultsView`` so it can be:
//   (a) the default Results-tab rendering for unsupported workflows,
//   (b) the "All artifacts" toggle target underneath every
//       specialised dashboard.
//
// Layout is identical to the pre-PR7 grid:
//   - Terminal node lifted into its own ``wide`` row
//   - Remaining nodes laid out in a 12-column bento grid
//   - Card size collapses to ``small`` when the artifact has no
//     preview values (snapshots, scalars)
// ============================================================================

import { useMemo } from 'react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { topologicalOrder } from '@/components/build/lib/topologicalOrder';
import { NodeWidgetCard } from '../NodeWidgetCard';
import { DashboardSection } from '../lib/DashboardSection';
import type { WidgetSize } from '@/components/monitor/registry';

type Props = {
  detail: WorkspaceDetail;
  /** When true (the default), include the terminal node section.
   *  Specialised dashboards may set this false to suppress the
   *  duplicate when the terminal is already rendered above. */
  showTerminalSection?: boolean;
  /** Section label override.  Defaults to "All stages" / "Intermediate
   *  stages" depending on whether a terminal is broken out. */
  intermediateLabel?: string;
  intermediateDescription?: string;
};

export function GenericResultsDashboard({
  detail,
  showTerminalSection = true,
  intermediateLabel,
  intermediateDescription,
}: Props) {
  const ordered = useMemo(
    () => topologicalOrder(detail.nodes, detail.edges),
    [detail.nodes, detail.edges],
  );

  const terminalId = detail.focus_node;
  const terminalNode =
    terminalId != null && showTerminalSection
      ? ordered.find((n) => n.node_id === terminalId) ?? null
      : null;
  const otherNodes = terminalNode
    ? ordered.filter((n) => n.node_id !== terminalNode.node_id)
    : ordered;

  if (ordered.length === 0) {
    return <EmptyResults />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-7">
      {terminalNode && (
        <DashboardSection
          label="Terminal output"
          description="The workflow’s final artifact — what the analysis ultimately produced."
          count={1}
          countLabel="stage"
        >
          <div className="grid grid-cols-12 gap-4 lg:gap-5">
            <NodeWidgetCard
              node={terminalNode}
              workspace={detail}
              size="wide"
            />
          </div>
        </DashboardSection>
      )}

      {otherNodes.length > 0 && (
        <DashboardSection
          label={
            intermediateLabel ??
            (terminalNode ? 'Intermediate stages' : 'All stages')
          }
          description={
            intermediateDescription ??
            (terminalNode
              ? 'Per-stage artifacts produced on the way to the terminal output.'
              : 'Every artifact this workspace produced.')
          }
          count={otherNodes.length}
          countLabel={otherNodes.length === 1 ? 'stage' : 'stages'}
        >
          <div className="grid grid-cols-12 gap-4 lg:gap-5">
            {otherNodes.map((n) => (
              <NodeWidgetCard
                key={n.node_id}
                node={n}
                workspace={detail}
                size={sizeForNode(n)}
              />
            ))}
          </div>
        </DashboardSection>
      )}
    </div>
  );
}

function EmptyResults() {
  return (
    <div className="card flex h-[200px] items-center justify-center text-[12px] text-fg-muted">
      No results to display — this workspace has no nodes.
    </div>
  );
}

/** Same heuristic the pre-PR7 grid used. */
function sizeForNode(node: WorkspaceDetail['nodes'][number]): WidgetSize {
  const a = node.artifact;
  if (!a) return 'small';
  const hasPreview = (a.preview_values ?? []).some(
    (v) => v != null && !Number.isNaN(v),
  );
  if (!hasPreview) return 'small';
  return 'medium';
}
