// ============================================================================
// __smoke_test_tool/surfaces/AskCard.tsx
// ----------------------------------------------------------------------------
// Stage 5 acceptance test fixture for the Ask-side dispatch.  Proves
// that ``ConversationCanvas.resolveAssistantCard`` walks to the
// module's ``surfaces.ask`` shelf when an assistant message's terminal
// tool is this module's toolName — without any edits to
// ``ConversationCanvas`` or ``AssistantResearchCard``.
// ============================================================================

import type { CopilotMessage } from '@/types/copilot';

function SmokeAskCard({ message }: { message: CopilotMessage }) {
  return (
    <div data-smoke-surface="ask">
      <p>SMOKE: ask card · {message.id}</p>
    </div>
  );
}

export default SmokeAskCard;
