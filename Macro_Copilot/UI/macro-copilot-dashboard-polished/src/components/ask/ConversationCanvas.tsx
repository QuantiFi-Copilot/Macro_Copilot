// ============================================================================
// ConversationCanvas — the center spine of the Ask surface
// ----------------------------------------------------------------------------
// Two states:
//   - empty:   render <EmptyState> centered
//   - active:  render the message sequence, auto-scroll to bottom on
//              new tokens / messages, with breadcrumb on top + composer
//              pinned to bottom.
//
// Layout:
//   - Maximum content width: 960px (left-aligned, not centered like
//     ChatGPT — this gives the conversation a document-y, editorial
//     feeling instead of a chat-bubble feeling).
//   - 24px vertical gap between turns; 16px between user message and
//     assistant card within a turn.
//   - Composer pinned to bottom; soft top fade indicates scrollable
//     content above.
//
// The canvas itself owns the scroll-to-bottom behaviour and the
// breadcrumb — everything else is delegated to children.
// ============================================================================

import {
  useEffect,
  useRef,
  type ComponentType,
  type RefObject,
} from 'react';
import type { CopilotMessage } from '@/types/copilot';
import { UserMessage } from './messages/UserMessage';
import { AssistantResearchCard } from './messages/AssistantResearchCard';
import { EmptyState } from './EmptyState';
import { getPrimitiveModule } from '@/modules';
import { normalizeToolName } from '@/lib/toolNames';

// ----------------------------------------------------------------------------
// Stage 5 — module-first assistant-card dispatch
// ----------------------------------------------------------------------------
//
// For each assistant message we ask: was this turn driven by exactly
// one primitive tool, and does that tool's module ship a bespoke
// ``surfaces.ask`` component?  If yes, mount the module's card.  If
// no, fall through to the default ``AssistantResearchCard``.
//
// Conservative — defer to the default card whenever the turn is
// multi-tool, workflow-template-driven, or not yet completed.  Those
// cases need the structured assembly ``AssistantResearchCard``
// provides (RoutingStrip + ToolTrace + ResultCanvas + DagStrip +
// etc.); a single per-tool card can't carry that composition.

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AssistantCardComponent = ComponentType<any>;

function extractTerminalToolName(message: CopilotMessage): string | null {
  // Workflow-template turns assemble multiple primitives; render via
  // the default card.
  if (message.workflow != null) return null;
  const completed = message.traceSteps.filter((s) => s.status === 'complete');
  if (completed.length === 0) return null;
  const tools = new Set(completed.map((s) => s.tool));
  if (tools.size !== 1) return null;
  return [...tools][0];
}

function resolveAssistantCard(
  message: CopilotMessage,
): AssistantCardComponent {
  const rawTool = extractTerminalToolName(message);
  if (rawTool) {
    const moduleSpec = getPrimitiveModule(normalizeToolName(rawTool));
    const AskSurface = moduleSpec?.surfaces?.ask;
    if (AskSurface) return AskSurface as AssistantCardComponent;
  }
  return AssistantResearchCard as AssistantCardComponent;
}

type Props = {
  messages: CopilotMessage[];
  /** Forwarded to EmptyState + FollowUps + ActionRow so all of those
   *  paths drop their prompts into the same composer. */
  onSeedComposer: (prompt: string) => void;
  /** Composer-portal target — passed up so a ref can be attached.  We
   *  don't render the composer here; it's a sibling pinned outside the
   *  scroll container by AskPage. */
  scrollRef?: RefObject<HTMLDivElement | null>;
  // Edit-mode props.  AskPage owns the editing state so only one
  // message can be edited at a time and the in-flight stream can
  // disable the affordance globally.
  editingMessageId: string | null;
  isThinking: boolean;
  onEditStart: (messageId: string) => void;
  onEditCancel: () => void;
  onEditSubmit: (messageId: string, newContent: string) => void;
};

export function ConversationCanvas({
  messages,
  onSeedComposer,
  scrollRef,
  editingMessageId,
  isThinking,
  onEditStart,
  onEditCancel,
  onEditSubmit,
}: Props) {
  const internalRef = useRef<HTMLDivElement>(null);
  const ref = scrollRef ?? internalRef;

  // Auto-scroll to bottom on new messages or streaming token updates.
  // We only scroll if the user is already near the bottom (within
  // 120px) so reading earlier history doesn't get hijacked.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom < 120) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, ref]);

  if (messages.length === 0) {
    return (
      <div ref={ref as RefObject<HTMLDivElement>} className="h-full overflow-y-auto">
        <EmptyState onSelect={onSeedComposer} />
      </div>
    );
  }

  // Pair each assistant message with its preceding user prompt so
  // FollowUps + Rerun can seed the composer with the original prompt.
  const turns = pairTurns(messages);

  return (
    <div ref={ref as RefObject<HTMLDivElement>} className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[960px] px-6 py-6">
        <p className="kicker mb-6 text-fg-muted">
          THREAD · {messages.filter((m) => m.role === 'user').length} TURNS
        </p>
        <div className="space-y-6">
          {turns.map((turn, i) => (
            <div key={i} className="space-y-4">
              {turn.user && (
                <UserMessage
                  messageId={turn.user.id}
                  content={turn.user.content}
                  timestamp={turn.user.timestamp}
                  isEditing={editingMessageId === turn.user.id}
                  // Disable the edit affordance while *any* edit is
                  // open OR while a turn is mid-stream — both states
                  // would make a click no-op.
                  disabled={
                    isThinking ||
                    (editingMessageId !== null &&
                      editingMessageId !== turn.user.id)
                  }
                  onEditStart={onEditStart}
                  onEditCancel={onEditCancel}
                  onEditSubmit={onEditSubmit}
                />
              )}
              {turn.assistant && (() => {
                const AssistantCard = resolveAssistantCard(turn.assistant);
                return (
                  <AssistantCard
                    message={turn.assistant}
                    pairedUserPrompt={turn.user?.content}
                    onSeedComposer={onSeedComposer}
                  />
                );
              })()}
            </div>
          ))}
        </div>
        {/* Bottom spacer so the composer doesn't visually hug the last
            card; the composer itself sits below the canvas in a
            sibling container. */}
        <div className="h-8" />
      </div>
    </div>
  );
}

type Turn = {
  user?: CopilotMessage;
  assistant?: CopilotMessage;
};

function pairTurns(messages: CopilotMessage[]): Turn[] {
  // The orchestrator alternates user → assistant.  We walk the buffer
  // and group each user message with the next assistant message.  An
  // unpaired trailing user message gets its own turn (we just haven't
  // received the assistant placeholder yet).
  const turns: Turn[] = [];
  let pending: Turn | null = null;
  for (const m of messages) {
    if (m.role === 'user') {
      if (pending) turns.push(pending);
      pending = { user: m };
    } else {
      if (!pending) pending = {};
      pending.assistant = m;
      turns.push(pending);
      pending = null;
    }
  }
  if (pending) turns.push(pending);
  return turns;
}
