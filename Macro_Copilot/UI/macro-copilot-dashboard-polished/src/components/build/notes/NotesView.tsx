// ============================================================================
// NotesView — per-workspace markdown textarea (localStorage-backed).
// ----------------------------------------------------------------------------
// V1 = plain monospace textarea with autosave to localStorage.
// Persistence is local-only so PR B doesn't introduce a new
// substrate table; PR C will lift to a shared backend when notes
// become sharable across users.
//
// The composer styling matches Ask's composer so the rhythm reads
// consistently — textarea on the surface card, footer with last-
// saved hint + "Clear" affordance.
// ============================================================================

import { useEffect, useState } from 'react';
import { NotebookPen, Trash2 } from 'lucide-react';
import { cn } from '@/utils/cn';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { useWorkspaceNotes } from '@/hooks/useWorkspaceNotes';

type Props = {
  detail: WorkspaceDetail;
};

export function NotesView({ detail }: Props) {
  const { value, setValue, clear } = useWorkspaceNotes(detail.slug);

  // Track "last saved at" purely cosmetically — the storage layer
  // is debounced (see useWorkspaceNotes); we display a friendly
  // ticker so the user knows their typing is being persisted.
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
  useEffect(() => {
    if (value.length === 0) {
      setLastSavedAt(null);
      return;
    }
    const t = setTimeout(() => setLastSavedAt(new Date()), 450);
    return () => clearTimeout(t);
  }, [value]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center justify-between gap-2 border-b border-line-subtle px-5 py-3">
        <div className="flex items-center gap-1.5">
          <NotebookPen size={11} className="text-ice-300" />
          <h3 className="text-[10.5px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
            Notes
          </h3>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] tracking-[0.02em] text-fg-faint">
            {lastSavedAt
              ? `Saved ${formatTime(lastSavedAt)} · local-only`
              : 'Local-only · autosaved'}
          </span>
          {value.length > 0 && (
            <button
              type="button"
              onClick={() => {
                if (
                  window.confirm(
                    'Clear notes for this workspace?  This is a local-only delete; nothing is sent server-side.',
                  )
                ) {
                  clear();
                }
              }}
              className="flex items-center gap-1 rounded-sm border border-line-soft px-2 py-0.5 text-[10.5px] font-medium text-fg-secondary transition-colors hover:border-coral-400/30 hover:text-coral-200"
            >
              <Trash2 size={10} />
              <span>Clear</span>
            </button>
          )}
        </div>
      </header>

      {/* Phase D / D9 — the run's bounded self-correction trace ("the
          system caught + fixed its own mistake"), rendered from the
          persisted run_audit sidecar.  Absent on runs that composed
          clean on the first attempt. */}
      {(detail.run_audit?.recompose_trace?.length ?? 0) > 0 && (
        <section className="shrink-0 border-b border-line-subtle px-5 py-3">
          <p className="kicker mb-1.5 text-amber-300">SELF-CORRECTION TRACE</p>
          <p className="mb-2 text-[11px] leading-snug text-fg-muted">
            The first composition failed a deterministic check; the system
            re-composed once with the failure reason before executing —
            the floor held: a wrong DAG never ran.
          </p>
          <ol className="space-y-1.5">
            {(detail.run_audit?.recompose_trace ?? []).map((step, i) => (
              <li
                key={step.attempt_index ?? i}
                className="rounded-md border border-amber-400/15 bg-amber-400/[0.04] px-3 py-2 text-[11.5px] leading-snug text-fg-secondary"
              >
                <span className="mono text-amber-300">
                  attempt {step.attempt_index ?? i + 1}
                </span>
                <span className="text-fg-faint"> failed </span>
                <span className="mono text-fg-primary">
                  {step.failed_status ?? 'UNKNOWN'}
                </span>
                {step.reason && (
                  <span className="block text-[11px] text-fg-muted">
                    {step.reason}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}

      <div className="min-h-0 flex-1 overflow-hidden px-5 py-5">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={
            'Quick scratch space for the analyst.  Markdown welcome — ' +
            'lives in your browser only until shared-notes ships.'
          }
          className={cn(
            'h-full w-full resize-none rounded-md border border-line-soft bg-white/[0.012] p-4 font-mono text-[12.5px] leading-[1.55] text-fg-primary placeholder:text-fg-faint',
            'focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
          )}
        />
      </div>
    </div>
  );
}

function formatTime(d: Date): string {
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}
