// ============================================================================
// StageParameterEditor — right pane: per-stage parameter form.
// ----------------------------------------------------------------------------
// Renders every editable parameter for the active stage using the
// derived descriptors + the registered control components.  All
// edits flow through the parent's override dispatcher; this
// component holds no state of its own.
// ============================================================================

import { useMemo } from 'react';
import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
import {
  prettyStageTitle,
  stageKindLabel,
} from '@/components/build/lib/stageDisplay';
import {
  railColorForStage,
  stageCategoryForNode,
} from '@/components/build/lib/stageCategory';
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
  const descriptors = useMemo(() => deriveControlsForStage(node), [node]);
  const category = stageCategoryForNode(node, workspace);
  const railColor = railColorForStage(category);

  // Split descriptors into "editable" and "read-only / topology"
  // groups so the form puts the user's editing surface up top + the
  // read-only metadata at the bottom.
  const editable = descriptors.filter((d) => !d.readOnly);
  const readOnly = descriptors.filter((d) => d.readOnly);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header
        className="flex shrink-0 items-baseline gap-2 border-b border-line-subtle px-5 py-3"
        style={{
          backgroundImage: `linear-gradient(90deg, transparent 0%, ${railColor} 18%, ${railColor} 82%, transparent 100%)`,
          backgroundRepeat: 'no-repeat',
          backgroundSize: '100% 1px',
          backgroundPosition: 'top',
        }}
      >
        <span className="text-[9.5px] font-semibold uppercase tracking-[0.18em] text-fg-faint">
          {stageKindLabel(node.kind)}
        </span>
        <h3 className="truncate text-[13.5px] font-semibold tracking-[-0.008em] text-fg-primary">
          {prettyStageTitle(node.name ?? node.node_id)}
        </h3>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="flex flex-col gap-5 px-5 py-5">
          {editable.length === 0 ? (
            <EmptyEditor />
          ) : (
            <Section title="Editable parameters">
              {editable.map((d) => (
                <ControlRow
                  key={overrideKey(d.path)}
                  descriptor={d}
                  overrides={overrides}
                  dispatch={dispatch}
                />
              ))}
            </Section>
          )}

          {readOnly.length > 0 && (
            <Section title="Stage identity">
              {readOnly.map((d) => (
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
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h4 className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-muted">
        {title}
      </h4>
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
