// ============================================================================
// ParametersView — Parameters tab content.
// ----------------------------------------------------------------------------
// Two-column layout: stage list (left) + active-stage editor (right).
// A sticky PendingOverridesBar appears at the top whenever 1+
// overrides are queued.
//
// Phase 4 — the override state machine moved up to
// ``WorkspaceOverridesProvider`` (wrapping the slug-bound shell) so
// the parameters surface and the copilot rail share one queue.  This
// view consumes the context instead of owning a local reducer.
// ============================================================================

import { useMemo, useState } from 'react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { topologicalOrder } from '@/components/build/lib/topologicalOrder';
import { useWorkspaceOverrides } from '@/components/build/lib/workspaceOverridesContext';
import { PendingOverridesBar } from './PendingOverridesBar';
import { StageList } from './StageList';
import { StageParameterEditor } from './StageParameterEditor';

type Props = {
  detail: WorkspaceDetail;
};

export function ParametersView({ detail }: Props) {
  const { overrides, dispatch } = useWorkspaceOverrides();
  const ordered = useMemo(
    () => topologicalOrder(detail.nodes, detail.edges),
    [detail.nodes, detail.edges],
  );

  const defaultActiveId = useMemo(
    () =>
      detail.focus_node ??
      ordered[0]?.node_id ??
      null,
    [detail.focus_node, ordered],
  );

  const [activeNodeId, setActiveNodeId] = useState<string | null>(
    defaultActiveId,
  );

  const activeNode =
    activeNodeId != null
      ? ordered.find((n) => n.node_id === activeNodeId) ?? ordered[0] ?? null
      : ordered[0] ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PendingOverridesBar />

      <div
        className="grid min-h-0 flex-1 overflow-hidden"
        style={{ gridTemplateColumns: 'clamp(240px, 24%, 300px) minmax(0, 1fr)' }}
      >
        <aside className="min-h-0 overflow-y-auto border-r border-line-subtle">
          <div className="flex items-baseline justify-between px-3 pt-4 pb-2">
            <h3 className="kicker text-fg-muted">Stages</h3>
            <span className="font-mono text-[10px] text-fg-faint">
              {ordered.length}
            </span>
          </div>
          <StageList
            detail={detail}
            activeNodeId={activeNode?.node_id ?? null}
            onSelect={setActiveNodeId}
            overrides={overrides}
          />
        </aside>

        <section className="min-h-0 overflow-hidden">
          {activeNode ? (
            <StageParameterEditor
              node={activeNode}
              workspace={detail}
              overrides={overrides}
              dispatch={dispatch}
            />
          ) : (
            <div className="flex h-full items-center justify-center text-[12px] text-fg-faint">
              This workspace has no stages.
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
