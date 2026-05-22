// ============================================================================
// CopilotReadOnlyComposer — disabled composer at the bottom of the rail.
// ----------------------------------------------------------------------------
// Rendered only on the empty Build surface (no workspace open).  The
// rail still shows a composer so the right column keeps the same
// vertical rhythm it'll have under the live ``WorkspaceCopilotComposer``
// in completed mode, but the empty-state composer is intentionally
// disabled: the centre-column composer is the primary entry point
// there and a second active composer next to it would be ambiguous.
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
          placeholder="Open a workspace to chat with it…"
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
          Use the centre composer to start an analysis. Workspace
          chat opens here once a workspace is loaded.
        </span>
      </div>
    </div>
  );
}
