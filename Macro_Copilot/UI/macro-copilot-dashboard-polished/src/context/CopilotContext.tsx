// ============================================================================
// CopilotContext
// ----------------------------------------------------------------------------
// Wraps `useCopilot` once at the app root so multiple consumers (the new
// AskPage primary surface AND the legacy ChatDrawer that still flanks
// non-Ask pages) share a single WebSocket + a single conversation buffer.
//
// Without this, mounting `useCopilot()` twice would open two WS
// connections and split the message buffer in two — so navigating from
// /rates (ChatDrawer mounted) to /ask (AskPage mounted) would lose the
// in-flight conversation.  Lifting state to context keeps the chat
// continuous across navigation.
//
// The hook surface is intentionally identical to `useCopilot()` — call
// sites read `messages`, `sendMessage`, etc. without any awareness that
// the implementation is now a singleton context.
// ============================================================================

import { createContext, useContext, type ReactNode } from 'react';
import { useCopilot } from '@/hooks/useCopilot';
import type { CopilotMessage, ConnectionStatus } from '@/types/copilot';

type CopilotContextValue = {
  messages: CopilotMessage[];
  /** Send a user turn through the singleton WebSocket.  R5.4 — when
   *  ``options.workspaceSlug`` is supplied (e.g. the Build copilot rail
   *  composer passing the active workspace's slug), the hook stamps
   *  every message of the resulting turn with that slug so per-
   *  workspace rails can filter the global message buffer. */
  sendMessage: (
    content: string,
    options?: { workspaceSlug?: string | null },
  ) => void;
  connectionStatus: ConnectionStatus;
  isThinking: boolean;
  clearMessages: () => void;
  /** Truncate the message buffer to before the given message id, then
   *  resubmit `newContent` as a fresh turn.  Wired to the in-line edit
   *  affordance on user messages — clicking edit, mutating the
   *  textarea, and saving forks the conversation from that point. */
  editAndResubmit: (messageId: string, newContent: string) => void;
};

const CopilotCtx = createContext<CopilotContextValue | null>(null);

export function CopilotProvider({ children }: { children: ReactNode }) {
  // useCopilot is intentionally instantiated exactly once here.  All
  // downstream consumers read via `useCopilotContext()`.
  const value = useCopilot();
  return <CopilotCtx.Provider value={value}>{children}</CopilotCtx.Provider>;
}

export function useCopilotContext(): CopilotContextValue {
  const ctx = useContext(CopilotCtx);
  if (!ctx) {
    throw new Error(
      'useCopilotContext must be used inside a <CopilotProvider>. ' +
        'Wrap your tree at App.tsx so the singleton WebSocket is shared ' +
        'across pages.',
    );
  }
  return ctx;
}
