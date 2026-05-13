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

import { Check, Lightbulb } from 'lucide-react';
import { useOptionalWorkspaceOverrides } from '@/components/build/lib/workspaceOverridesContext';
import type { ParamControlDescriptor } from '@/components/build/parameters/lib/controlSchema';
import type { ProposedOverride } from '@/types/copilot';
import { cn } from '@/utils/cn';

type Props = {
  proposals: ProposedOverride[];
};

export function ProposedOverridesChips({ proposals }: Props) {
  const ctx = useOptionalWorkspaceOverrides();

  if (proposals.length === 0) return null;
  const provider = ctx;
  const queued = new Set(Object.keys(provider?.overrides ?? {}));

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
          return (
            <button
              key={key}
              type="button"
              title={p.rationale ?? undefined}
              disabled={!provider}
              onClick={() => {
                if (!provider) return;
                const descriptor = descriptorFor(p);
                provider.setOverride(descriptor, p.value);
              }}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10px] transition-colors',
                isQueued
                  ? 'border-mint-400/40 bg-mint-500/15 text-mint-300'
                  : 'border-lineage-400/35 bg-lineage-500/10 text-lineage-200 hover:border-lineage-300/55 hover:bg-lineage-500/20',
                !provider && 'opacity-50 cursor-not-allowed',
              )}
            >
              {isQueued && (
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
