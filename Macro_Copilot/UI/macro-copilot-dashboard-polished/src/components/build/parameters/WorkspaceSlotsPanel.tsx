// ============================================================================
// WorkspaceSlotsPanel — PR5 editable-slot surface.
// ----------------------------------------------------------------------------
// The canonical mutation surface for a persisted workflow workspace.
// Lists every template slot declared in the workspace's bound
// schema, renders an appropriate control per slot, and shows which
// stages each slot affects.
//
// Mutation flow
// -------------
// User edits a slot →
//   dispatch({type:'set', descriptor, value}) on the shared
//   WorkspaceOverridesContext →
//   PendingOverridesBar enables the "Apply & fork" button →
//   on apply, ``overridesToServerPatch(overrides)`` → backend's
//   ``POST /workspace/{slug}/fork`` body shape:
//   ``{slot_overrides, slot_dict_overrides}``.
//
// This panel does NOT own state — the reducer + dispatcher come
// from the context provider higher up the tree (slug-bound shell).
//
// What this panel handles
// -----------------------
//   - Loading the template card via the existing ``useWorkflow``
//     hook (no new API surface).
//   - Slot-schema-driven descriptor derivation (``deriveSlotControls``).
//   - Per-slot "Affected stages" chip strip (``findStagesForSlot``).
//   - Non-forkable workspace banner when ``template_id`` is null.
//   - Graceful loading + error states for the template fetch.
//   - Read-only fall-through for list / nested / unmapped slots.
// ============================================================================

import { useMemo } from 'react';
import { AlertCircle, Boxes, Lock } from 'lucide-react';
import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
import { useWorkflow } from '@/hooks/useWorkflows';
import { useWorkspaceOverrides } from '@/components/build/lib/workspaceOverridesContext';
import {
  overrideKey,
  type ParamControlDescriptor,
} from './lib/controlSchema';
import { deriveSlotControlsFromCard } from './lib/deriveSlotControls';
import { findStagesForSlot } from './lib/findStagesForSlot';
import { ParameterControlSwitch } from './ParameterControlSwitch';

type Props = {
  detail: WorkspaceDetail;
};

export function WorkspaceSlotsPanel({ detail }: Props) {
  const { data: card, isLoading: cardLoading, error: cardError } =
    useWorkflow(detail.template_id);
  const { overrides, dispatch } = useWorkspaceOverrides();

  const isForkable =
    !!detail.template_id && detail.bound_slot_values !== null;

  const descriptors = useMemo<ParamControlDescriptor[]>(
    () =>
      isForkable
        ? deriveSlotControlsFromCard({
            boundSlotValues: detail.bound_slot_values,
            card,
          })
        : [],
    [isForkable, detail.bound_slot_values, card],
  );

  if (!isForkable) {
    return <NotForkableBanner />;
  }

  return (
    <section
      className="flex flex-col gap-3 px-5 py-4"
      aria-labelledby="workspace-slots-heading"
    >
      <header className="flex items-baseline justify-between gap-2">
        <div className="flex items-center gap-2">
          <Boxes
            size={11}
            strokeWidth={1.75}
            aria-hidden
            className="text-ice-300"
          />
          <h3
            id="workspace-slots-heading"
            className="kicker text-fg-muted"
          >
            Workspace slots
          </h3>
        </div>
        <span className="font-mono text-[10px] text-fg-faint">
          {detail.template_id} · {descriptors.length} slot
          {descriptors.length === 1 ? '' : 's'}
        </span>
      </header>

      {cardError ? (
        <SchemaFetchError message={cardError.message} />
      ) : null}

      <p className="text-[10.5px] leading-[1.5] text-fg-faint">
        These are the editable template slots that define this workflow.
        Changing one and applying the overrides forks the workspace via{' '}
        <code className="font-mono text-[10.5px] text-fg-muted">
          POST /workspace/{'{slug}'}/fork
        </code>{' '}
        using <code className="font-mono">slot_overrides</code> +{' '}
        <code className="font-mono">slot_dict_overrides</code>.  The
        original workspace stays unchanged.
      </p>

      {descriptors.length === 0 ? (
        <EmptySlots loading={cardLoading} />
      ) : (
        <ul className="flex flex-col gap-3">
          {descriptors
            .filter((d) => !d.hidden)
            .map((d) => (
              <SlotRow
                key={overrideKey(d.path)}
                descriptor={d}
                nodes={detail.nodes}
                overrideValue={overrides[overrideKey(d.path)]}
                onChange={(value) => {
                  if (value === undefined) {
                    dispatch({ type: 'clear', descriptor: d });
                  } else {
                    dispatch({ type: 'set', descriptor: d, value });
                  }
                }}
              />
            ))}
        </ul>
      )}
    </section>
  );
}

// ----------------------------------------------------------------------------
// One slot row — descriptor control + affected-stage chips.
// ----------------------------------------------------------------------------

function SlotRow({
  descriptor,
  nodes,
  overrideValue,
  onChange,
}: {
  descriptor: ParamControlDescriptor;
  nodes: NodeSummary[];
  overrideValue: ReturnType<typeof useWorkspaceOverrides>['overrides'][string];
  onChange: (value: unknown | undefined) => void;
}) {
  const stages = useMemo(
    () =>
      findStagesForSlot({
        path: descriptor.path,
        boundValue: descriptor.currentValue,
        nodes,
      }),
    [descriptor.path, descriptor.currentValue, nodes],
  );

  return (
    <li className="flex flex-col gap-1.5 rounded-md border border-line-soft bg-white/[0.012] px-3 py-3">
      <ParameterControlSwitch
        descriptor={descriptor}
        override={overrideValue}
        onChange={onChange}
      />

      <div className="flex flex-wrap items-center gap-1.5 pt-1.5 text-[10px]">
        {descriptor.readOnly && (
          <span className="inline-flex items-center gap-1 rounded-sm border border-line-soft bg-white/[0.025] px-1.5 py-0.5 text-fg-faint">
            <Lock size={9} strokeWidth={1.75} aria-hidden />
            <span>Read-only</span>
          </span>
        )}
        {stages.length === 0 ? (
          <span className="font-mono text-fg-faint">
            Affects: (computed during binding)
          </span>
        ) : (
          <>
            <span className="font-mono text-fg-faint">Affects:</span>
            {stages.map((m) => (
              <span
                key={m.nodeId}
                className={
                  m.strength === 'exact'
                    ? 'inline-flex items-center rounded-sm border border-ice-400/30 bg-ice-500/10 px-1.5 py-0.5 font-mono text-ice-200'
                    : m.strength === 'value'
                      ? 'inline-flex items-center rounded-sm border border-line-soft bg-white/[0.025] px-1.5 py-0.5 font-mono text-fg-secondary'
                      : 'inline-flex items-center rounded-sm border border-dashed border-line-soft px-1.5 py-0.5 font-mono text-fg-faint'
                }
                title={`Match strength: ${m.strength}`}
              >
                {m.label}
              </span>
            ))}
          </>
        )}
      </div>
    </li>
  );
}

// ----------------------------------------------------------------------------
// State banners
// ----------------------------------------------------------------------------

function NotForkableBanner() {
  return (
    <section className="flex items-start gap-2 border-b border-line-subtle px-5 py-3">
      <AlertCircle
        size={13}
        className="mt-0.5 shrink-0 text-amber-300"
        aria-hidden
      />
      <div className="min-w-0">
        <div className="text-[12px] font-semibold text-fg-primary">
          This workspace is not forkable
        </div>
        <p className="mt-1 text-[10.5px] leading-[1.5] text-fg-secondary">
          The workspace pre-dates the PR-B persistence schema and has no{' '}
          <code className="font-mono text-fg-muted">template_id</code> or{' '}
          <code className="font-mono text-fg-muted">bound_slot_values</code>,
          so it can&apos;t be forked.  Re-run the original prompt to
          produce a fresh workspace whose slots are editable.
        </p>
      </div>
    </section>
  );
}

function EmptySlots({ loading }: { loading: boolean }) {
  return (
    <div className="rounded-md border border-dashed border-line-soft bg-white/[0.005] px-3 py-4 text-center text-[10.5px] text-fg-faint">
      {loading
        ? 'Loading template slot schema…'
        : 'This workflow template declares no editable slots.'}
    </div>
  );
}

function SchemaFetchError({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-coral-400/30 bg-coral-500/[0.05] px-3 py-2">
      <AlertCircle
        size={12}
        className="mt-0.5 shrink-0 text-coral-300"
        aria-hidden
      />
      <div className="min-w-0">
        <div className="text-[10.5px] font-semibold text-coral-200">
          Couldn&apos;t load template slot schema
        </div>
        <div className="mt-0.5 text-[10px] leading-[1.5] text-fg-secondary">
          {message}.  Slot controls fall back to the bound values&apos;
          shape; affected-stages chips may be less specific.
        </div>
      </div>
    </div>
  );
}
