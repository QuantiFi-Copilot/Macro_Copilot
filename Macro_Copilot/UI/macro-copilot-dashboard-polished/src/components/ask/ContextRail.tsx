// ============================================================================
// ContextRail — the right-hand auditability surface
// ----------------------------------------------------------------------------
// Three vertically stacked panels separated by hairlines:
//   - WorkingSetPanel   — every artifact this thread has produced
//   - MethodologyPanel  — tools / conventions in play
//   - ProvenancePanel   — step-walk for the latest answer
//
// All three derive their data from the live message buffer; no separate
// state is maintained here.  Collapsing the rail to a sliver is exposed
// as a chevron handle the user can toggle (state owned by AskPage so
// the rail width can be reflected in the page grid).
// ============================================================================

import type { CopilotMessage } from '@/types/copilot';
import {
  deriveWorkingSet,
  deriveMethodology,
  deriveLatestLineage,
} from '@/components/ask/lib/deriveContext';
import { WorkingSetPanel } from './context-rail/WorkingSetPanel';
import { MethodologyPanel } from './context-rail/MethodologyPanel';
import { ProvenancePanel } from './context-rail/ProvenancePanel';

type Props = {
  messages: CopilotMessage[];
};

export function ContextRail({ messages }: Props) {
  const workingSet = deriveWorkingSet(messages);
  const methodology = deriveMethodology(messages);
  const lineage = deriveLatestLineage(messages);

  return (
    <aside className="relative flex h-full min-h-0 w-full flex-col overflow-hidden border-l border-line-subtle bg-white/[0.005]">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <WorkingSetPanel artifacts={workingSet} />
        <Divider />
        <MethodologyPanel entries={methodology} />
        <Divider />
        <ProvenancePanel steps={lineage} />
      </div>
    </aside>
  );
}

function Divider() {
  return <div className="mx-4 my-3 border-t border-line-subtle" />;
}
