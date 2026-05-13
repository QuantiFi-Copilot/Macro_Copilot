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
import { useCopilotContext } from '@/context/CopilotContext';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { CopilotIntroCard } from './CopilotIntroCard';
import { CopilotStarterChips } from './CopilotStarterChips';
import { CopilotReadOnlyComposer } from './CopilotReadOnlyComposer';
import { WorkspaceCopilotComposer } from './WorkspaceCopilotComposer';
import { WorkspaceCopilotMessages } from './WorkspaceCopilotMessages';
import { VariantComparisonTable } from './VariantComparisonTable';
import { composeScopedMessage } from './lib/workspaceScopedContext';

type Props = {
  mode: 'empty' | 'completed';
  /** Title shown on the intro card.  Only used in ``completed``
   *  mode; ignored otherwise. */
  workspaceTitle?: string;
  /** Active workspace — required in completed mode for the scoped
   *  composer to prepend its preamble.  Null in empty mode. */
  workspace?: WorkspaceDetail | null;
  /** Called when a starter chip is clicked.  In empty mode this
   *  seeds the canvas composer; in completed mode it sends a
   *  scoped message to the chat directly. */
  onChipSeed?: (text: string) => void;
};

export function WorkspaceCopilotRail({
  mode,
  workspaceTitle,
  workspace = null,
  onChipSeed,
}: Props) {
  const { sendMessage } = useCopilotContext();

  const handleChip = useCallback(
    (text: string) => {
      if (mode === 'empty') {
        onChipSeed?.(text);
        return;
      }
      // Completed mode — fire the chip prompt directly through the
      // scoped composer's path.  Same preamble + sendMessage as the
      // composer below, so chip-clicks are first-class messages.
      sendMessage(composeScopedMessage(workspace, text));
    },
    [mode, onChipSeed, sendMessage, workspace],
  );

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-line-subtle bg-ink-900/30 backdrop-blur-sm">
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pt-4 pb-3">
        <CopilotIntroCard mode={mode} workspaceTitle={workspaceTitle} />

        <div className="mt-4">
          <p className="px-1 pb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.18em] text-fg-faint">
            {mode === 'empty' ? 'Try one of these' : 'Quick prompts'}
          </p>
          <CopilotStarterChips
            mode={mode}
            onChip={handleChip}
            // PR B enables chips in completed mode — clicking a chip
            // fires a scoped chat message.
            disabled={false}
          />
        </div>

        {mode === 'completed' && workspace && (
          <div className="mt-5">
            <VariantComparisonTable workspace={workspace} />
          </div>
        )}

        {mode === 'completed' && (
          <div className="mt-5">
            <p className="px-1 pb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.18em] text-fg-faint">
              Conversation
            </p>
            <WorkspaceCopilotMessages />
          </div>
        )}
      </div>

      {/* PR B — empty mode keeps the read-only composer (the canvas-
          top composer is the primary surface there).  Completed mode
          gets the workspace-scoped composer with the preamble
          machinery wired in. */}
      {mode === 'empty' ? (
        <CopilotReadOnlyComposer />
      ) : (
        <WorkspaceCopilotComposer workspace={workspace} />
      )}
    </aside>
  );
}
