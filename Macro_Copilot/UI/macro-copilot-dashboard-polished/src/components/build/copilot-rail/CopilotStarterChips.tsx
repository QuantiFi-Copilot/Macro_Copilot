// ============================================================================
// CopilotStarterChips — example prompts in the workspace copilot rail.
// ----------------------------------------------------------------------------
// Static chip strip beneath the intro card.  In PR A, clicking a chip
// seeds the canvas-top composer (empty state mode) or fires a "soon"
// hint (completed mode — the rail composer is read-only).  PR B
// hooks them up to the workspace-scoped chat for real override /
// explain / extend intents.
// ============================================================================

import { ArrowRight } from 'lucide-react';
import { cn } from '@/utils/cn';

type ChipMode = 'empty' | 'completed';

const CHIPS_EMPTY: string[] = [
  'Show me the UST 2s10s spread over the last 2 years with a 252-day z-score.',
  'Scan G10 sovereigns for instruments above 1.5σ today.',
  'Decompose the recent UST 10Y move into PCA factors.',
  'Compare 10Y UST → 2Y SOFR β across steepening vs flattening 2s10s regimes.',
];

const CHIPS_COMPLETED: string[] = [
  'Change the z-score window to 126d.',
  'Show me a regime overlay on the chart above.',
  'Explain why this node uses ACT/365 day-count.',
  'Compare this to a 5s30s instead.',
];

type Props = {
  mode: ChipMode;
  /** Click handler.  In empty mode this seeds the canvas-top
   *  composer; in completed mode PR A passes a no-op + the parent
   *  surfaces a "coming in PR B" toast. */
  onChip: (text: string) => void;
  /** When true, chips render as disabled affordances with reduced
   *  opacity.  Used in completed mode in PR A. */
  disabled?: boolean;
};

export function CopilotStarterChips({ mode, onChip, disabled }: Props) {
  const chips = mode === 'empty' ? CHIPS_EMPTY : CHIPS_COMPLETED;

  return (
    <div className="flex flex-col gap-1.5">
      {chips.map((c) => (
        <button
          key={c}
          type="button"
          onClick={() => !disabled && onChip(c)}
          disabled={disabled}
          className={cn(
            'group flex items-start gap-2 rounded-md border border-line-soft bg-white/[0.012] px-3 py-2 text-left transition-colors duration-150',
            disabled
              ? 'cursor-not-allowed opacity-55'
              : 'hover:border-ice-400/35 hover:bg-ice-500/[0.04]',
          )}
        >
          <ArrowRight
            size={11}
            strokeWidth={1.75}
            className="mt-0.5 shrink-0 text-fg-faint group-hover:text-ice-300"
          />
          <span className="text-[11.5px] leading-[1.5] text-fg-secondary">
            {c}
          </span>
        </button>
      ))}
    </div>
  );
}
