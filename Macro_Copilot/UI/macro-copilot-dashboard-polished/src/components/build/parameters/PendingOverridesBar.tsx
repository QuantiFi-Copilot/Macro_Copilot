// ============================================================================
// PendingOverridesBar — top affordance summarising queued overrides.
// ----------------------------------------------------------------------------
// When the user has 1+ pending overrides, this bar slides in at the
// top of the Parameters tab.  Shows:
//   - count + "discard all" affordance
//   - per-override "was → is" chips (compact)
//   - "Apply" button → calls the fork endpoint, navigates to the new
//     variant slug on success
//
// On legacy workspaces (template_id / bound_slot_values NULL), the
// "Apply" button is replaced by a static "Forkable workspaces only"
// caption — keeps the surface coherent without throwing.
// ============================================================================

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, Loader2, RotateCcw } from 'lucide-react';
import { cn } from '@/utils/cn';
import { forkWorkspace } from '@/services/workspaceApi';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import {
  hasPendingOverrides,
  overridesToServerPatch,
} from './lib/overridesState';
import {
  overrideKey,
  type OverrideMap,
  type ParamOverride,
} from './lib/controlSchema';

type Props = {
  workspace: WorkspaceDetail;
  overrides: OverrideMap;
  onDiscardAll: () => void;
};

export function PendingOverridesBar({
  workspace,
  overrides,
  onDiscardAll,
}: Props) {
  const navigate = useNavigate();
  const pending = hasPendingOverrides(overrides);
  const entries = Object.values(overrides);
  const isForkable = Boolean(
    workspace.template_id && workspace.bound_slot_values,
  );

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!pending) return null;

  const handleApply = async () => {
    if (!isForkable) return;
    setSubmitting(true);
    setError(null);
    try {
      const patch = overridesToServerPatch(overrides);
      const res = await forkWorkspace(workspace.slug, {
        slot_overrides: patch.slot_overrides,
        slot_dict_overrides: patch.slot_dict_overrides,
      });
      navigate(`/workspace/${res.slug}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="sticky top-0 z-10 flex shrink-0 flex-col gap-2 border-b border-violet-400/30 bg-violet-500/[0.05] px-5 py-3 backdrop-blur-sm">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="rounded-sm border border-violet-400/45 bg-violet-500/15 px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-[0.18em] text-violet-100">
            {entries.length} pending
          </span>
          <span className="text-[11.5px] text-fg-secondary">
            {entries.length === 1
              ? '1 override'
              : `${entries.length} overrides`}{' '}
            queued. Apply to fork a variant workspace.
          </span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={onDiscardAll}
            disabled={submitting}
            className="flex items-center gap-1 rounded-sm border border-line-soft px-2 py-0.5 text-[10.5px] font-medium text-fg-secondary transition-colors hover:border-coral-400/30 hover:text-coral-200 disabled:opacity-60"
          >
            <RotateCcw size={10} strokeWidth={2} />
            <span>Discard all</span>
          </button>
          {isForkable ? (
            <button
              type="button"
              onClick={handleApply}
              disabled={submitting}
              className={cn(
                'composer-send-active flex items-center gap-1.5 rounded-md px-3 py-1 text-[11.5px] font-semibold text-ink-900 transition-opacity',
                submitting && 'opacity-60',
              )}
            >
              {submitting ? (
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
              title="This workspace pre-dates the PR-B fork substrate; the original prompt needs to be re-run to make it forkable."
              className="rounded-md border border-line-soft px-2 py-0.5 text-[10.5px] text-fg-faint"
            >
              Forkable workspaces only
            </span>
          )}
        </div>
      </div>

      <ChipsRow entries={entries} />

      {error && (
        <div className="mt-1 flex items-start gap-2 rounded-md border border-coral-400/30 bg-coral-500/[0.06] px-3 py-2 text-[11px] text-coral-200">
          <AlertCircle size={12} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}

function ChipsRow({ entries }: { entries: ParamOverride[] }) {
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map((o) => (
        <span
          key={overrideKey(o.path)}
          className="inline-flex items-center gap-1 rounded-md border border-violet-400/35 bg-violet-500/10 px-1.5 py-0.5 font-mono text-[10px] text-violet-100"
        >
          <span className="text-violet-200">{labelFor(o)}</span>
          <span className="text-violet-100/50">·</span>
          <span className="text-violet-100/70">{formatVal(o.previousValue)}</span>
          <span className="text-violet-100/70">→</span>
          <span className="text-violet-50">{formatVal(o.value)}</span>
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
