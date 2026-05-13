// ============================================================================
// WorkspaceCopilotRail — right rail on every Build canvas state.
// ----------------------------------------------------------------------------
// Vertical stack:
//   ┌────────────────────────┐
//   │ CopilotIntroCard       │
//   │ ────────────────────── │
//   │ CopilotStarterChips    │
//   │ (mode-dependent)       │
//   │ ────────────────────── │
//   │ CopilotReadOnlyComposer│
//   └────────────────────────┘
//
// The rail's content is mode-dependent:
//   - ``empty``     — seeds the centre-column composer when a chip is
//                     clicked.
//   - ``completed`` — chips render as disabled affordances + a
//                     "PR B" toast.
//
// Why a single component for both modes:
// The visual layout (intro card + chip strip + composer) is identical
// across modes; only the content differs.  Keeping it in one file
// means a future style adjustment to the rail's spacing / typography
// lands in exactly one place.
// ============================================================================

import { useCallback } from 'react';
import { CopilotIntroCard } from './CopilotIntroCard';
import { CopilotStarterChips } from './CopilotStarterChips';
import { CopilotReadOnlyComposer } from './CopilotReadOnlyComposer';

type Props = {
  mode: 'empty' | 'completed';
  /** Title shown on the intro card.  Only used in ``completed``
   *  mode; ignored otherwise. */
  workspaceTitle?: string;
  /** Called when a starter chip is clicked.  PR A wires this only
   *  for ``empty`` mode (chips in completed mode are disabled). */
  onChipSeed?: (text: string) => void;
};

export function WorkspaceCopilotRail({
  mode,
  workspaceTitle,
  onChipSeed,
}: Props) {
  const handleChip = useCallback(
    (text: string) => {
      if (mode !== 'empty') return;
      onChipSeed?.(text);
    },
    [mode, onChipSeed],
  );

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-line-subtle bg-ink-900/30 backdrop-blur-sm">
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pt-4 pb-3">
        <CopilotIntroCard mode={mode} workspaceTitle={workspaceTitle} />

        <div className="mt-4">
          <p className="px-1 pb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.18em] text-fg-faint">
            {mode === 'empty' ? 'Try one of these' : 'Coming in PR B'}
          </p>
          <CopilotStarterChips
            mode={mode}
            onChip={handleChip}
            disabled={mode !== 'empty'}
          />
        </div>
      </div>

      <CopilotReadOnlyComposer />
    </aside>
  );
}
