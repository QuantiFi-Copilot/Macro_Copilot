// ============================================================================
// GenericResultsDashboard — payload-backed artifact grid.
// ----------------------------------------------------------------------------
// PR7 — extracted from the pre-PR7 ``ResultsView`` so it can be:
//   (a) the default Results-tab rendering for unsupported workflows,
//   (b) the "Intermediate stages" toggle target underneath every
//       specialised dashboard (vocabulary aligned with the surface
//       contract §6 operator visibility policy).
//
// Layout is identical to the pre-PR7 grid:
//   - Terminal node lifted into its own ``wide`` row
//   - Remaining nodes laid out in a 12-column bento grid
//   - Card size collapses to ``small`` when the artifact has no
//     preview values (snapshots, scalars)
// ============================================================================

import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
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

  // PR-11C — intermediate-stages toggle.
  // Plan §C.2: defaults ON for open-DAG workspaces (template_id=null);
  // toggling OFF hides operator artifacts, leaving only the terminal.
  // For legacy template-less workspaces (template_id non-null but
  // unknown) and for direct GenericResultsDashboard mounts inside a
  // specialised dashboard's fallback, we also default ON because
  // those callers pass ``showTerminalSection=false`` and need every
  // stage visible.
  const isOpenDag = detail.template_id === null;
  const [showIntermediate, setShowIntermediate] = useState<boolean>(
    isOpenDag || !showTerminalSection,
  );

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

      {/* Intermediate-stages toggle (plan §C.2).  Renders only when:
        *   - there ARE intermediate stages to hide/show, AND
        *   - we have a terminal section above (otherwise everything
        *     IS the all-stages view; no point showing a toggle).
        * Hidden inside specialised dashboards that pass
        * ``showTerminalSection=false`` because those wrap their own
        * toggle (ResultsView.SpecialisedShell). */}
      {terminalNode && otherNodes.length > 0 && (
        <div className="border-t border-line-subtle pt-4">
          <button
            type="button"
            onClick={() => setShowIntermediate((v) => !v)}
            className="flex items-center gap-1.5 rounded-md border border-line-soft bg-white/[0.025] px-2.5 py-1 text-[11px] font-medium text-fg-secondary transition-colors hover:border-ice-400/35 hover:text-ice-200"
            aria-expanded={showIntermediate}
            title="Reveal every per-node artifact — including operator outputs (align_series, threshold_events, conditional_aggregate, etc.) — as widget cards. Same per-artifact widget registry as the terminal output."
          >
            {showIntermediate ? (
              <ChevronDown size={11} strokeWidth={1.75} aria-hidden />
            ) : (
              <ChevronRight size={11} strokeWidth={1.75} aria-hidden />
            )}
            <span>
              {showIntermediate
                ? 'Hide intermediate stages'
                : `Show intermediate stages (${otherNodes.length})`}
            </span>
          </button>
          <p className="mt-2 text-[10.5px] leading-[1.5] text-fg-faint">
            Intermediate stages reveal every per-node artifact —
            primitives, operators (align_series, event_windows,
            conditional_aggregate, …), and any other workflow step —
            rendered through the same widget registry the terminal
            output uses.
          </p>
        </div>
      )}

      {otherNodes.length > 0 && showIntermediate && (
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
