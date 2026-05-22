// ============================================================================
// ProposedOverridesChips — chip strip for chat-driven parameter overrides.
// ----------------------------------------------------------------------------
// When an assistant message in the copilot rail carries
// ``proposedOverrides`` (the Phase 4 wire-extension on the ``done``
// event), this component renders each suggestion as a clickable chip.
// Approving a chip dispatches into the shared
// ``WorkspaceOverridesProvider`` — the same queue the Parameters tab
// reads — so the chip click is indistinguishable from a manual edit
// in the editor pane.  The user can then click "Apply & fork" in the
// PendingOverridesBar to commit them.
//
// Provider scope
// --------------
// The provider only exists under the slug-bound shell, so chips
// rendered on the empty shell are a no-op.  We use the optional
// hook + degrade gracefully (chip stays disabled with a tooltip)
// instead of throwing, so chip rendering never breaks a chat
// bubble on the empty surface.
// ============================================================================

import { AlertTriangle, Check, Lightbulb } from 'lucide-react';
import { useTools, useWorkflow } from '@/hooks/useWorkflows';
import { useOptionalWorkspaceOverrides } from '@/components/build/lib/workspaceOverridesContext';
import type { ParamControlDescriptor } from '@/components/build/parameters/lib/controlSchema';
import { isProposedOverrideValid } from '@/components/build/parameters/lib/validateOverrides';
import type { ProposedOverride } from '@/types/copilot';
import { cn } from '@/utils/cn';

type Props = {
  proposals: ProposedOverride[];
};

export function ProposedOverridesChips({ proposals }: Props) {
  const ctx = useOptionalWorkspaceOverrides();
  // PR3 — pull the workflow card + tool catalogue so each chip
  // can be validated against the workspace's slot schema BEFORE
  // dispatching.  An unmapped chip (unknown slot, unknown tool,
  // tool/output-field mismatch) renders as a disabled "can't apply
  // safely" tile rather than firing into the override queue.
  const { data: card } = useWorkflow(ctx?.workspace.template_id ?? null);
  const { data: tools } = useTools();

  if (proposals.length === 0) return null;
  const provider = ctx;
  const queued = new Set(Object.keys(provider?.overrides ?? {}));
  const knownSlotNames = card
    ? new Set(card.slot_schema.map((s) => s.name))
    : null;

  return (
    <div className="mt-2 flex flex-col gap-1.5 rounded-md border border-lineage-400/25 bg-lineage-500/[0.05] px-2.5 py-2">
      <div className="flex items-center gap-1.5">
        <Lightbulb
          size={11}
          strokeWidth={1.75}
          className="text-lineage-300"
          aria-hidden
        />
        <span className="kicker text-lineage-200">
          Suggested overrides
        </span>
        <span className="font-mono text-[10px] text-fg-faint">
          {proposals.length}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {proposals.map((p, i) => {
          const key = chipKey(p, i);
          const isQueued = queued.has(key);
          const label = formatChipLabel(p);
          const descriptor = descriptorFor(p);
          // PR3 — provider may be null (chips rendered on the empty
          // shell).  In that case we can't validate against a real
          // workspace yet; treat as "needs workspace" and disable.
          const validation = provider
            ? isProposedOverrideValid({
                descriptor,
                value: p.value,
                boundSlotValues: provider.workspace.bound_slot_values,
                tools,
                knownSlotNames,
              })
            : { ok: false, errors: [] };
          const invalidReason = validation.errors[0]?.message ?? null;
          const isInvalid = !validation.ok && provider !== null;
          const titleText = isInvalid
            ? `Cannot apply this suggestion safely: ${invalidReason}`
            : (p.rationale ?? undefined);
          return (
            <button
              key={key}
              type="button"
              title={titleText}
              disabled={!provider || isInvalid}
              onClick={() => {
                if (!provider || isInvalid) return;
                provider.setOverride(descriptor, p.value);
              }}
              data-testid={`proposed-chip:${key}`}
              data-validation={isInvalid ? 'invalid' : 'ok'}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10px] transition-colors',
                isInvalid
                  ? 'border-coral-400/40 bg-coral-500/10 text-coral-200 cursor-not-allowed'
                  : isQueued
                    ? 'border-mint-400/40 bg-mint-500/15 text-mint-300'
                    : 'border-lineage-400/35 bg-lineage-500/10 text-lineage-200 hover:border-lineage-300/55 hover:bg-lineage-500/20',
                !provider && !isInvalid && 'opacity-50 cursor-not-allowed',
              )}
            >
              {isInvalid && (
                <AlertTriangle size={9} strokeWidth={2.5} aria-hidden />
              )}
              {!isInvalid && isQueued && (
                <Check size={9} strokeWidth={2.5} aria-hidden />
              )}
              <span>{label}</span>
            </button>
          );
        })}
      </div>
      {!provider && (
        <span className="text-[10px] leading-[1.4] text-fg-faint">
          Open a workspace to apply these suggestions.
        </span>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

function chipKey(p: ProposedOverride, index: number): string {
  return p.path.length === 1
    ? p.path[0]
    : `${p.path[0]}.${p.path[1]}`
      || p.id
      || `idx_${index}`;
}

function formatChipLabel(p: ProposedOverride): string {
  const path = p.path.length === 1 ? p.path[0] : `${p.path[0]}.${p.path[1]}`;
  const rendered =
    p.valueLabel ??
    (typeof p.value === 'string'
      ? p.value
      : JSON.stringify(p.value));
  return `${path}: ${truncate(rendered, 28)}`;
}

function truncate(s: string, max: number): string {
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

/** Materialise a minimal ``ParamControlDescriptor`` from a wire
 *  ``ProposedOverride``.  ``setOverride`` only needs ``path`` and
 *  ``currentValue`` to compute "is this a no-op against the
 *  parent?"; we fill the rest with safe defaults.  When the wire
 *  payload includes ``previousValue``, surface it as ``currentValue``
 *  so the reducer's "set value back to parent's value clears the
 *  override" path keeps working. */
function descriptorFor(p: ProposedOverride): ParamControlDescriptor {
  return {
    path: p.path,
    label: p.path.join('.'),
    meta: { kind: 'readonly_json' },
    currentValue: p.previousValue,
  };
}
