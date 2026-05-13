// ============================================================================
// ParametersPlaceholder — visible "coming in PR B" content for the
// Parameters tab in PR A.
// ----------------------------------------------------------------------------
// The Parameters tab will let the user click a stage card, edit its
// bound parameters, and "Update & rerun downstream" to fork a
// variant.  PR A ships only the empty state for the tab so the tab
// strip is functional from day one and the visual review can confirm
// the placement before the interactive behaviour lands.
// ============================================================================

import { Settings2 } from 'lucide-react';

export function ParametersPlaceholder() {
  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6 py-12">
      <div className="max-w-[480px] text-center">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-lg border border-line-soft bg-white/[0.02]">
          <Settings2 size={16} className="text-ice-300" />
        </div>
        <h3 className="text-[14px] font-semibold tracking-[-0.008em] text-fg-primary">
          Parameter editing arrives in PR B
        </h3>
        <p className="mt-2 text-[12px] leading-[1.6] text-fg-secondary">
          You'll be able to click any stage on the DAG, override its
          methodology knobs (z-score window, lookback days, financing
          method, …), and rerun just the downstream portion of the DAG
          as a new variant.  The original workspace stays intact for
          side-by-side comparison.
        </p>
      </div>
    </div>
  );
}
