// ============================================================================
// ResultsView — per-node widget grid + terminal highlight.
// ----------------------------------------------------------------------------
// Renders the workspace's nodes as a 12-column bento grid of widget
// cards, with the terminal node lifted into its own visually-larger
// "Terminal" section above the rest.  Layout rules:
//
//   - Terminal node: ``wide`` size (full row) so the final output
//     gets visual emphasis.
//   - Other nodes: ``medium`` (half row) by default; nodes with a
//     small artifact (no preview values) collapse to ``small`` so
//     four can fit per row.
//
// Cards inherit the WidgetCard chrome from Monitor, so the visual
// register is consistent between the home dashboard and Build's
// Results tab.
// ============================================================================

import { useMemo } from 'react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { topologicalOrder } from '../lib/topologicalOrder';
import { NodeWidgetCard } from './NodeWidgetCard';
import type { WidgetSize } from '@/components/monitor/registry';

type Props = {
  detail: WorkspaceDetail;
};

export function ResultsView({ detail }: Props) {
  const ordered = useMemo(
    () => topologicalOrder(detail.nodes, detail.edges),
    [detail.nodes, detail.edges],
  );

  const terminalId = detail.focus_node;
  const terminalNode =
    terminalId != null
      ? ordered.find((n) => n.node_id === terminalId)
      : null;
  const otherNodes = terminalNode
    ? ordered.filter((n) => n.node_id !== terminalNode.node_id)
    : ordered;

  if (ordered.length === 0) {
    return <EmptyResults />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-6 px-6 py-6">
      {terminalNode && (
        <section className="flex flex-col gap-3">
          <SectionHeader
            label="Terminal output"
            description="The workflow's final artifact — what the analysis ultimately produced."
          />
          <div className="grid grid-cols-12 gap-4 lg:gap-5">
            <NodeWidgetCard
              node={terminalNode}
              workspace={detail}
              size="wide"
            />
          </div>
        </section>
      )}

      {otherNodes.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionHeader
            label={terminalNode ? 'Intermediate stages' : 'All stages'}
            description={
              terminalNode
                ? 'Per-stage artifacts produced on the way to the terminal output.'
                : 'Every artifact this workspace produced.'
            }
          />
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
        </section>
      )}
    </div>
  );
}

function SectionHeader({
  label,
  description,
}: {
  label: string;
  description: string;
}) {
  return (
    <div>
      <h2 className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-muted">
        {label}
      </h2>
      <p className="mt-0.5 text-[11px] text-fg-faint">{description}</p>
    </div>
  );
}

function EmptyResults() {
  return (
    <div className="px-6 py-10">
      <div className="card flex h-[200px] items-center justify-center text-[12px] text-fg-muted">
        No results to display — this workspace has no nodes.
      </div>
    </div>
  );
}

/** Choose a widget size based on artifact properties.  Nodes whose
 *  artifact has no preview values (snapshots, scalar metrics)
 *  collapse to ``small`` so a row of four can fit; nodes with full
 *  time-series previews get the default ``medium``. */
function sizeForNode(node: WorkspaceDetail['nodes'][number]): WidgetSize {
  const a = node.artifact;
  if (!a) return 'small';
  const hasPreview = (a.preview_values ?? []).some(
    (v) => v != null && !Number.isNaN(v),
  );
  if (!hasPreview) return 'small';
  return 'medium';
}
