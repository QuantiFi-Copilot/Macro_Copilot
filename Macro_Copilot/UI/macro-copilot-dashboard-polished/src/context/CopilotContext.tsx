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
  sendMessage: (content: string) => void;
  connectionStatus: ConnectionStatus;
  isThinking: boolean;
  clearMessages: () => void;
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
