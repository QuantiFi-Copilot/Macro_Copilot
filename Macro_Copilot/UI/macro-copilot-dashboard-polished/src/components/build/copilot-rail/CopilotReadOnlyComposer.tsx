// ============================================================================
// CopilotReadOnlyComposer — disabled composer at the bottom of the rail.
// ----------------------------------------------------------------------------
// PR A renders the rail's composer as a disabled affordance with a
// clear "coming in PR B" caption.  Keeps the visual layout of the
// rail correct from day one — when PR B replaces this component, the
// rail's grid + spacing stay identical.
// ============================================================================

import { ArrowUp, Lock } from 'lucide-react';

export function CopilotReadOnlyComposer() {
  return (
    <div className="flex shrink-0 flex-col gap-1.5 border-t border-line-subtle bg-ink-900/40 px-3 py-3">
      <div className="composer-shell flex items-end gap-2 rounded-[10px] border border-line-soft bg-white/[0.008] px-3 py-2 opacity-65">
        <textarea
          rows={1}
          disabled
          readOnly
          placeholder="Ask about this workspace…"
          className="min-h-[28px] flex-1 resize-none bg-transparent text-[12.5px] text-fg-primary placeholder:text-fg-faint focus:outline-none disabled:cursor-not-allowed"
        />
        <span
          aria-hidden
          className="flex h-7 w-7 items-center justify-center rounded-md border border-line-soft text-fg-faint"
        >
          <ArrowUp size={12} strokeWidth={2} />
        </span>
      </div>
      <div className="flex items-center gap-1.5 px-1 text-[10px] tracking-[0.04em] text-fg-faint">
        <Lock size={9} strokeWidth={2} />
        <span>
          Read-only in PR A · workspace-scoped chat ships in PR B
        </span>
      </div>
    </div>
  );
}
