// ============================================================================
// ForwardView
// ----------------------------------------------------------------------------
// Placeholder for OIS forward-rate analysis.  The hook intentionally throws
// when this view is requested (no /detail/forward endpoint yet), so the
// WorkspacePage error fallback usually handles this — but if a future
// endpoint ships and the hook is updated, this component is the entry point.
// For now it simply renders an explanatory empty state.
// ============================================================================

import { Construction } from 'lucide-react';

export function ForwardView() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-16">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-line-soft bg-white/[0.02] text-amber-300">
          <Construction size={18} />
        </div>
        <div>
          <h2 className="text-[14px] font-semibold tracking-[-0.01em] text-fg-primary">
            Forward-rate workspace not yet wired
          </h2>
          <p className="mt-1.5 text-[12px] leading-relaxed text-fg-secondary">
            The OIS forward-rate detail endpoint isn't shipped yet. Use the
            chat copilot for forward-rate queries — it will route to the OIS
            tools directly. This page will fill in once the backend exposes
            <span className="mono"> /api/v1/rates/detail/forward</span>.
          </p>
        </div>
      </div>
    </div>
  );
}
