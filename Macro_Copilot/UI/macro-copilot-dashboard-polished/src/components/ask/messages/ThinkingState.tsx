// ============================================================================
// ThinkingState — pre-content placeholder while the orchestrator routes
// ----------------------------------------------------------------------------
// Rendered inside a research card when:
//   - The assistant message exists (status: thinking)
//   - No tool trace yet, no prose yet, no workflow decision yet
//
// Once a routing decision lands or the first tool call fires, the
// research card swaps this for the real content; the user perceives a
// continuous render rather than a flash of empty card.
// ============================================================================

import { Loader2 } from 'lucide-react';

type Props = {
  label?: string;
};

export function ThinkingState({ label }: Props) {
  return (
    <div className="flex items-center gap-2.5 px-5 py-4">
      <Loader2 size={13} className="animate-spin text-lineage-300" />
      <span className="text-[13px] text-fg-secondary">
        {label ?? 'Routing your question through the orchestrator…'}
      </span>
    </div>
  );
}
