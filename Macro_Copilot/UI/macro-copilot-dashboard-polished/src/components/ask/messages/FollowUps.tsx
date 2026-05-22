// ============================================================================
// FollowUps — suggested next questions, rendered BELOW the research card
// ----------------------------------------------------------------------------
// Three chip-style suggestions that drop their prompt into the composer
// when clicked.  Always rendered without card chrome — they read as
// inline conversational continuations, not actions on the previous
// card.
//
// Suggestions come from `lib/followUps.ts` which today is rule-based on
// the workflow archetype.  V2 will swap that for an LLM-generated
// context-aware suggestion that knows the user's last analysis.
// ============================================================================

import type { CopilotMessage } from '@/types/copilot';
import { deriveFollowUps } from '@/components/ask/lib/followUps';

type Props = {
  message: CopilotMessage;
  onSelect: (prompt: string) => void;
};

export function FollowUps({ message, onSelect }: Props) {
  const items = deriveFollowUps(message);
  if (items.length === 0) return null;

  return (
    <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 px-1 pl-1">
      {items.map((f) => (
        <button
          key={f.id}
          type="button"
          onClick={() => onSelect(f.prompt)}
          className="text-left text-[13px] font-medium text-ice-300/80 transition-colors duration-150 ease-sleek hover:text-ice-200 hover:underline hover:underline-offset-4"
        >
          {f.label}
        </button>
      ))}
    </div>
  );
}
