// ============================================================================
// NotesPlaceholder — empty content for the Notes tab in PR A.
// ----------------------------------------------------------------------------
// Notes ship as a simple persistent markdown textarea in PR B.  PR A
// keeps the tab functional with a visible "coming soon" affordance
// so the tab strip's hit-targets are correct.
// ============================================================================

import { NotebookPen } from 'lucide-react';

export function NotesPlaceholder() {
  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6 py-12">
      <div className="max-w-[440px] text-center">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-lg border border-line-soft bg-white/[0.02]">
          <NotebookPen size={16} className="text-ice-300" />
        </div>
        <h3 className="text-[14px] font-semibold tracking-[-0.008em] text-fg-primary">
          Notes arrive in PR B
        </h3>
        <p className="mt-2 text-[12px] leading-[1.6] text-fg-secondary">
          A simple markdown notebook attached to each workspace, for
          the analyst's running commentary alongside the DAG.  Saved
          on the same slug-stable URL as the workspace itself.
        </p>
      </div>
    </div>
  );
}
