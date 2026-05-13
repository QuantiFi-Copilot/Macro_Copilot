// ============================================================================
// PendingOverridesBar — top affordance summarising queued overrides.
// ----------------------------------------------------------------------------
// When the user has 1+ pending overrides, this bar slides in at the
// top of the Parameters tab.  Shows:
//   - count + "discard all" affordance
//   - per-override "was → is" chips (compact, click to clear)
//   - "Apply & fork" button → calls the fork endpoint, navigates to
//     the new variant slug on success
//
// Phase 4 — consumes the shared ``WorkspaceOverridesProvider`` so the
// state mirrors the same queue the copilot rail dispatches into.
// Clicking a chip removes that specific override (was previously
// read-only).
//
// On legacy workspaces (template_id / bound_slot_values NULL), the
// "Apply" button is replaced by a static "Forkable workspaces only"
// caption — keeps the surface coherent without throwing.
// ============================================================================

import { AlertCircle, Loader2, RotateCcw, X } from 'lucide-react';
import { cn } from '@/utils/cn';
import { useWorkspaceOverrides } from '@/components/build/lib/workspaceOverridesContext';
import {
  overrideKey,
  type ParamOverride,
} from './lib/controlSchema';

export function PendingOverridesBar() {
  const {
    workspace,
    overrides,
    hasPending,
    isApplying,
    error,
    apply,
    resetAll,
    clearError,
    dispatch,
  } = useWorkspaceOverrides();

  if (!hasPending) return null;

  const entries = Object.values(overrides);
  const isForkable = Boolean(
    workspace.template_id && workspace.bound_slot_values,
  );

  return (
    <div className="sticky top-0 z-10 flex shrink-0 flex-col gap-2 border-b border-lineage-400/30 bg-lineage-500/[0.06] px-5 py-3 backdrop-blur-sm">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="rounded-sm border border-lineage-400/45 bg-lineage-500/15 px-1.5 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.18em] text-lineage-200">
            {entries.length} pending
          </span>
          <span className="text-[11.5px] text-fg-secondary">
            {entries.length === 1
              ? '1 override'
              : `${entries.length} overrides`}{' '}
            queued.{' '}
            <span className="text-fg-muted">
              Apply to fork a variant workspace.
            </span>
          </span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={resetAll}
            disabled={isApplying}
            className="flex items-center gap-1 rounded-sm border border-line-soft px-2 py-0.5 text-[10.5px] font-medium text-fg-secondary transition-colors hover:border-coral-400/30 hover:text-coral-200 disabled:opacity-60"
          >
            <RotateCcw size={10} strokeWidth={2} />
            <span>Discard all</span>
          </button>
          {isForkable ? (
            <button
              type="button"
              onClick={apply}
              disabled={isApplying}
              className={cn(
                'composer-send-active flex items-center gap-1.5 rounded-md px-3 py-1 text-[11.5px] font-semibold text-ink-900 transition-opacity',
                isApplying && 'opacity-60',
              )}
            >
              {isApplying ? (
                <>
                  <Loader2 size={11} className="animate-spin" />
                  <span>Forking…</span>
                </>
              ) : (
                <span>Apply &amp; fork →</span>
              )}
            </button>
          ) : (
            <span
              title="This workspace pre-dates the fork substrate; re-run the original prompt to make it forkable."
              className="rounded-md border border-line-soft px-2 py-0.5 text-[10.5px] text-fg-faint"
            >
              Forkable workspaces only
            </span>
          )}
        </div>
      </div>

      <ChipsRow
        entries={entries}
        onClear={(o) => dispatch({ type: 'clear', descriptor: descriptorForOverride(o) })}
      />

      {error && (
        <div className="mt-1 flex items-start gap-2 rounded-md border border-coral-400/30 bg-coral-500/[0.06] px-3 py-2 text-[11px] text-coral-200">
          <AlertCircle size={12} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1">{error}</span>
          <button
            type="button"
            onClick={clearError}
            aria-label="Dismiss"
            className="shrink-0 text-coral-200/70 transition-colors hover:text-coral-100"
          >
            <X size={12} strokeWidth={2} />
          </button>
        </div>
      )}
    </div>
  );
}

function ChipsRow({
  entries,
  onClear,
}: {
  entries: ParamOverride[];
  onClear: (o: ParamOverride) => void;
}) {
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map((o) => (
        <span
          key={overrideKey(o.path)}
          className="group inline-flex items-center gap-1 rounded-md border border-lineage-400/35 bg-lineage-500/10 px-1.5 py-0.5 font-mono text-[10px] text-lineage-200"
        >
          <span className="text-lineage-200">{labelFor(o)}</span>
          <span className="text-lineage-200/50">·</span>
          <span className="text-lineage-200/70">{formatVal(o.previousValue)}</span>
          <span className="text-lineage-200/70">→</span>
          <span className="text-lineage-200">{formatVal(o.value)}</span>
          <button
            type="button"
            onClick={() => onClear(o)}
            aria-label={`Clear override for ${labelFor(o)}`}
            className="ml-0.5 flex h-3 w-3 items-center justify-center rounded-sm text-lineage-200/40 transition-colors hover:bg-lineage-500/25 hover:text-lineage-100"
          >
            <X size={9} strokeWidth={2.5} />
          </button>
        </span>
      ))}
    </div>
  );
}

function labelFor(o: ParamOverride): string {
  return o.path.length === 1 ? o.path[0] : `${o.path[0]}.${o.path[1]}`;
}

function formatVal(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

/** Re-assemble a minimal ``ParamControlDescriptor`` from a stored
 *  override so the reducer's ``clear`` action can address it.  Only
 *  the ``path`` is meaningful for ``clear``; the rest are placeholders
 *  so we don't have to plumb full descriptors through the override
 *  store. */
function descriptorForOverride(
  o: ParamOverride,
): import('./lib/controlSchema').ParamControlDescriptor {
  return {
    path: o.path,
    label: labelFor(o),
    meta: { kind: 'readonly_json' },
    currentValue: o.previousValue,
  };
}
