// ============================================================================
// StageParameterEditor — right pane: per-stage parameter inspection.
// ----------------------------------------------------------------------------
// PR5 — the per-stage panel is now READ-ONLY by discipline.  Node
// params are execution detail (substrate-bound values + bridge-
// resolved fields) and CANNOT be patched through the fork endpoint;
// the canonical mutation surface is ``WorkspaceSlotsPanel``, which
// edits ``workspace.bound_slot_values`` against the template's
// ``slot_schema``.  Editable controls live there.
//
// This view stays useful: it surfaces what the substrate actually
// bound for a given stage so the user can verify the slot edits
// produced sensible per-node values.  The "Read-only" banner up top
// explains the discipline so a user doesn't expect an inline editor
// to take.
//
// Header reuses the same column vocabulary as the DAG / Results
// surfaces (Primitive / Operator / Output) plus a category-rail
// gradient on top so the inspector pane carries the same visual
// identity as the stage card in the DAG strip.
// ============================================================================

import { useMemo } from 'react';
import { Lock } from 'lucide-react';
import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
import { prettyStageTitle } from '@/components/build/lib/stageDisplay';
import {
  railColorForStage,
  stageCategoryForNode,
} from '@/components/build/lib/stageCategory';
import { columnLabelForCategory } from '@/components/build/lib/stageColumn';
import { ParameterControlSwitch } from './ParameterControlSwitch';
import { deriveControlsForStage } from './lib/deriveControlsForStage';
import {
  overrideKey,
  type OverrideMap,
  type ParamControlDescriptor,
} from './lib/controlSchema';
import type { OverridesAction } from './lib/overridesState';

type Props = {
  node: NodeSummary;
  workspace: WorkspaceDetail;
  overrides: OverrideMap;
  dispatch: (action: OverridesAction) => void;
};

export function StageParameterEditor({
  node,
  workspace,
  overrides,
  dispatch,
}: Props) {
  // PR5 — derive descriptors as before, but force EVERY descriptor to
  // be ``readOnly: true``.  Node-param keys aren't slot names, so
  // letting the user edit them would produce slot-override patches
  // the backend can't apply (silent no-op fork).  The
  // ``WorkspaceSlotsPanel`` is the canonical mutation surface.
  const descriptors = useMemo<ParamControlDescriptor[]>(
    () =>
      deriveControlsForStage(node).map((d) => ({ ...d, readOnly: true })),
    [node],
  );
  const category = stageCategoryForNode(node, workspace);
  const railColor = railColorForStage(category);
  const columnLabel = columnLabelForCategory(category);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header
        className="flex shrink-0 flex-col gap-0.5 border-b border-line-subtle px-5 py-3.5"
        style={{
          backgroundImage: `linear-gradient(90deg, transparent 0%, ${railColor} 18%, ${railColor} 82%, transparent 100%)`,
          backgroundRepeat: 'no-repeat',
          backgroundSize: '100% 1px',
          backgroundPosition: 'top',
        }}
      >
        <span className="kicker text-fg-muted">{columnLabel}</span>
        <h3 className="truncate text-[14px] font-semibold tracking-[-0.012em] text-fg-primary">
          {prettyStageTitle(node.name ?? node.node_id)}
        </h3>
      </header>

      <ReadOnlyBanner />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="flex flex-col gap-6 px-5 py-5">
          {descriptors.length === 0 ? (
            <EmptyEditor />
          ) : (
            <Section title="Stage details" meta={`${descriptors.length}`}>
              {descriptors.map((d) => (
                <ControlRow
                  key={overrideKey(d.path)}
                  descriptor={d}
                  overrides={overrides}
                  dispatch={dispatch}
                />
              ))}
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}

function ReadOnlyBanner() {
  return (
    <div className="flex shrink-0 items-start gap-2 border-b border-line-subtle bg-white/[0.012] px-5 py-2.5">
      <Lock
        size={11}
        strokeWidth={1.75}
        aria-hidden
        className="mt-0.5 shrink-0 text-fg-faint"
      />
      <p className="text-[10.5px] leading-[1.45] text-fg-secondary">
        Stage details are read-only — they reflect execution detail
        (substrate-bound values).  To produce a variant workspace, edit
        the workspace slots above and click{' '}
        <span className="font-mono text-fg-muted">Apply &amp; fork</span>.
      </p>
    </div>
  );
}

function ControlRow({
  descriptor,
  overrides,
  dispatch,
}: {
  descriptor: ParamControlDescriptor;
  overrides: OverrideMap;
  dispatch: (action: OverridesAction) => void;
}) {
  const k = overrideKey(descriptor.path);
  const current = overrides[k];

  return (
    <ParameterControlSwitch
      descriptor={descriptor}
      override={current}
      onChange={(value) => {
        if (value === undefined) {
          dispatch({ type: 'clear', descriptor });
        } else {
          dispatch({ type: 'set', descriptor, value });
        }
      }}
    />
  );
}

function Section({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h4 className="kicker text-fg-muted">{title}</h4>
        {meta && (
          <span className="font-mono text-[10px] text-fg-faint">{meta}</span>
        )}
      </div>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  );
}

function EmptyEditor() {
  return (
    <div className="rounded-md border border-dashed border-line-soft bg-white/[0.005] px-4 py-6 text-center text-[11.5px] text-fg-faint">
      This stage has no exposed parameters.
    </div>
  );
}
