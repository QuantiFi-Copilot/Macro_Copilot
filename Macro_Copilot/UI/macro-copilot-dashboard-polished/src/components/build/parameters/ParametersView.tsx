// ============================================================================
// ParametersView — Parameters tab content.
// ----------------------------------------------------------------------------
// Two-column layout: stage list (left) + active-stage editor (right).
// A sticky PendingOverridesBar appears at the top whenever 1+
// overrides are queued.
//
// Owns the override-state machine via ``useReducer``.  The reducer
// + descriptor derivation are pure functions (see
// ``lib/overridesState.ts`` and ``lib/deriveControlsForStage.ts``)
// so this component is the thin orchestration layer.
// ============================================================================

import { useMemo, useReducer, useState } from 'react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { topologicalOrder } from '@/components/build/lib/topologicalOrder';
import { PendingOverridesBar } from './PendingOverridesBar';
import { StageList } from './StageList';
import { StageParameterEditor } from './StageParameterEditor';
import {
  overridesReducer,
} from './lib/overridesState';
import type { OverrideMap } from './lib/controlSchema';

type Props = {
  detail: WorkspaceDetail;
};

export function ParametersView({ detail }: Props) {
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

  const [overrides, dispatch] = useReducer<
    React.Reducer<OverrideMap, Parameters<typeof overridesReducer>[1]>
  >(overridesReducer, {});

  const activeNode =
    activeNodeId != null
      ? ordered.find((n) => n.node_id === activeNodeId) ?? ordered[0] ?? null
      : ordered[0] ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PendingOverridesBar
        workspace={detail}
        overrides={overrides}
        onDiscardAll={() => dispatch({ type: 'reset' })}
      />

      <div
        className="grid min-h-0 flex-1 overflow-hidden"
        style={{ gridTemplateColumns: 'clamp(220px, 22%, 280px) minmax(0, 1fr)' }}
      >
        <aside className="min-h-0 overflow-y-auto border-r border-line-subtle">
          <div className="px-3 pt-3 pb-1 text-[9.5px] font-semibold uppercase tracking-[0.18em] text-fg-faint">
            Stages
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
