// ============================================================================
// ParametersView — Parameters tab content.
// ----------------------------------------------------------------------------
// PR5 — pivots the mutation surface from per-stage node-param edits
// to workspace-slot edits.  Layout:
//
//   ┌───────────────────────────────────────────────────────────┐
//   │ PendingOverridesBar  (sticky)                             │
//   ├───────────────────────────────────────────────────────────┤
//   │ WorkspaceSlotsPanel  — editable template slots            │
//   │   (one row per slot · affected-stages chips)              │
//   ├──────────────────┬────────────────────────────────────────┤
//   │ StageList        │ StageParameterEditor                   │
//   │   (left rail)    │   (read-only stage detail inspector)   │
//   └──────────────────┴────────────────────────────────────────┘
//
// Why this split exists
// ---------------------
// The backend's fork endpoint accepts ``slot_overrides`` +
// ``slot_dict_overrides`` keyed by SLOT NAMES from the template's
// ``slot_schema``.  Persisted ``node.params`` carry execution
// detail (substrate-bound values + bridge-layer enrichment), and
// their keys aren't slot names — patching them produces a silent
// no-op fork.  The slots panel is the canonical mutation surface;
// the per-stage editor stays for context but is read-only.
//
// Backwards compat
// ----------------
// Legacy (pre-PR-B) workspaces with ``template_id === null`` are
// still openable, but the slots panel renders a "not forkable"
// banner.  The per-stage read-only inspector still works on those
// workspaces (it doesn't depend on the slot schema).
// ============================================================================

import { useMemo, useState } from 'react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { topologicalOrder } from '@/components/build/lib/topologicalOrder';
import { useWorkspaceOverrides } from '@/components/build/lib/workspaceOverridesContext';
import { PendingOverridesBar } from './PendingOverridesBar';
import { StageList } from './StageList';
import { StageParameterEditor } from './StageParameterEditor';
import { WorkspaceSlotsPanel } from './WorkspaceSlotsPanel';

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
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <PendingOverridesBar />

      {/* Editable surface — workspace slots.  Always visible regardless
       *  of stage selection so the user can see all slots at once. */}
      <div className="shrink-0 border-b border-line-subtle">
        <WorkspaceSlotsPanel detail={detail} />
      </div>

      {/* Read-only stage detail — left rail picks a stage; right pane
       *  shows its bound node params.  Pure inspection. */}
      <div
        className="grid min-h-0 flex-1 overflow-hidden"
        style={{ gridTemplateColumns: 'clamp(240px, 24%, 300px) minmax(0, 1fr)' }}
      >
        <aside className="min-h-0 overflow-y-auto border-r border-line-subtle">
          <div className="flex items-baseline justify-between px-3 pt-4 pb-2">
            <h3 className="kicker text-fg-muted">Stages (read-only)</h3>
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
