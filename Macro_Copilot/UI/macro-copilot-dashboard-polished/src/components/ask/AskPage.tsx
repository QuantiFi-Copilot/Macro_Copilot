// ============================================================================
// AskPage — the dedicated /ask surface
// ----------------------------------------------------------------------------
// Three-column layout under the global TopNav:
//
//   ┌──────────┬───────────────────────────────────┬──────────┐
//   │ Threads  │  Conversation canvas              │ Context  │
//   │ rail     │  (empty state OR turns)           │ rail     │
//   │ 260 px   │  ──────────────────────────────── │ 340 px   │
//   │          │  Composer (pinned to bottom)      │          │
//   └──────────┴───────────────────────────────────┴──────────┘
//
// State sourced from the singleton CopilotContext (the same WS / message
// buffer the legacy ChatDrawer reads on other pages).  Threads come
// from `useThreads` which today derives a single thread from the
// active session.  Composer is wired directly via a ref so FollowUps,
// EmptyState starting points, and the ActionRow's "Rerun with…" all
// drop prompts into the same input without going through window
// custom events.
//
// AskPage also owns the in-line edit state for previous user messages.
// Only one message can be in edit mode at a time, and the affordance
// is suppressed while any turn is mid-stream.
// ============================================================================

import { useCallback, useRef, useState } from 'react';
import { useCopilotContext } from '@/context/CopilotContext';
import { useThreads } from '@/hooks/useThreads';
import { ThreadsRail } from './ThreadsRail';
import { ConversationCanvas } from './ConversationCanvas';
import { ContextRail } from './ContextRail';
import { Composer, type ComposerHandle } from './Composer';

export function AskPage() {
  const {
    messages,
    sendMessage,
    clearMessages,
    connectionStatus,
    isThinking,
    editAndResubmit,
  } = useCopilotContext();

  const { groupedThreads, activeThreadId, selectThread, newThread } = useThreads({
    messages,
    clearMessages,
  });

  const composerRef = useRef<ComposerHandle>(null);

  // Edit mode — only one user message can be edited at a time.  The
  // edit affordance is suppressed inside ConversationCanvas while
  // `isThinking` is true.
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);

  const handleEditStart = useCallback((id: string) => {
    setEditingMessageId(id);
  }, []);

  const handleEditCancel = useCallback(() => {
    setEditingMessageId(null);
  }, []);

  const handleEditSubmit = useCallback(
    (messageId: string, newContent: string) => {
      editAndResubmit(messageId, newContent);
      setEditingMessageId(null);
    },
    [editAndResubmit],
  );

  // Single seed-composer entry point — used by EmptyState (starting
  // point clicked), FollowUps (chip clicked), and ActionRow's
  // Rerun-with action.  All call sites converge here so the ref is
  // the only source of truth for composer state mutation.
  const seedComposer = useCallback((prompt: string) => {
    composerRef.current?.setValue(prompt);
  }, []);

  const handleSend = useCallback(
    (content: string) => {
      sendMessage(content);
    },
    [sendMessage],
  );

  return (
    <div
      className="ambient-grid grid h-full min-h-0 w-full"
      style={{
        gridTemplateColumns:
          'clamp(220px, 16vw, 280px) minmax(0, 1fr) clamp(300px, 20vw, 360px)',
      }}
    >
      <ThreadsRail
        groupedThreads={groupedThreads}
        activeThreadId={activeThreadId}
        onSelectThread={selectThread}
        onNewThread={newThread}
      />

      {/* Center column: scrollable canvas + pinned composer.  We split
          this into two stacked regions so the composer can stay sticky
          at the bottom without needing position:absolute math. */}
      <div className="flex min-h-0 min-w-0 flex-col">
        <div className="min-h-0 flex-1">
          <ConversationCanvas
            messages={messages}
            onSeedComposer={seedComposer}
            editingMessageId={editingMessageId}
            isThinking={isThinking}
            onEditStart={handleEditStart}
            onEditCancel={handleEditCancel}
            onEditSubmit={handleEditSubmit}
          />
        </div>
        <div className="shrink-0 border-t border-line-subtle bg-ink-900/50 backdrop-blur-md">
          <Composer
            ref={composerRef}
            connectionStatus={connectionStatus}
            isThinking={isThinking}
            onSend={handleSend}
          />
        </div>
      </div>

      <ContextRail messages={messages} />
    </div>
  );
}
