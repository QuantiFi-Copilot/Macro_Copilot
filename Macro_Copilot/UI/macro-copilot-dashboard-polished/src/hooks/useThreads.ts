// ============================================================================
// useThreads
// ----------------------------------------------------------------------------
// Thread-level state for the /ask surface.
//
// V1 (this PR):
//   - Backend has no thread persistence; the orchestrator session
//     accepts a single user_message at a time and streams a response.
//   - We model "threads" as a pure UI grouping: while messages exist,
//     we render exactly one thread (the active one).  The thread title
//     auto-derives from the first user message.  "New thread" is a
//     `clearMessages()` call on the copilot context.
//   - No localStorage persistence — refreshing the page resets state,
//     same as today.  This keeps V1 honest about the underlying
//     stateless backend.
//
// V2 (planned):
//   - Backend ships /api/v1/threads (CRUD) + thread_id passed on every
//     WS message.  Swap the implementation here for a real fetch +
//     React Query layer; the consumer surface (the shape returned from
//     this hook) does NOT change.
//
// Consumers should treat the return value as a stable contract:
// `threads`, `activeThreadId`, `selectThread(id)`, `newThread()`.
// ============================================================================

import { useMemo, useCallback } from 'react';
import type { CopilotMessage } from '@/types/copilot';

export type ThreadSummary = {
  id: string;
  title: string;
  /** ISO date string. */
  startedAt: string;
  /** ISO date string of last assistant or user message. */
  lastActivityAt: string;
  messageCount: number;
  /** Workflow template_id of the last workflow turn, when present.
   *  Surfaces in the rail subtitle as a quick "what was this about". */
  lastWorkflowTemplateId?: string;
};

export type RelativeDateGroup =
  | 'today'
  | 'yesterday'
  | 'last_7_days'
  | 'earlier';

export type GroupedThreads = {
  group: RelativeDateGroup;
  label: string;
  threads: ThreadSummary[];
};

type UseThreadsArgs = {
  messages: CopilotMessage[];
  clearMessages: () => void;
};

type UseThreadsResult = {
  threads: ThreadSummary[];
  groupedThreads: GroupedThreads[];
  activeThreadId: string | null;
  selectThread: (id: string) => void;
  newThread: () => void;
};

const ACTIVE_THREAD_ID = 'current';

export function useThreads({
  messages,
  clearMessages,
}: UseThreadsArgs): UseThreadsResult {
  // Derive the active thread's summary from the live message buffer.
  // While there are no messages we expose an empty thread list so the
  // rail renders its empty state instead of an orphan thread row.
  const activeThread = useMemo<ThreadSummary | null>(() => {
    if (messages.length === 0) return null;

    const firstUserMessage = messages.find((m) => m.role === 'user');
    const lastMessage = messages[messages.length - 1];
    const lastWorkflow = [...messages]
      .reverse()
      .find((m) => m.role === 'assistant' && m.workflow?.routeDecision)
      ?.workflow?.routeDecision.template_id;

    return {
      id: ACTIVE_THREAD_ID,
      title:
        truncateTitle(firstUserMessage?.content ?? 'New thread') ||
        'New thread',
      startedAt: (
        messages[0]?.timestamp ?? new Date()
      ).toISOString(),
      lastActivityAt: (
        lastMessage?.timestamp ?? new Date()
      ).toISOString(),
      messageCount: messages.filter((m) => m.role === 'user').length,
      lastWorkflowTemplateId: lastWorkflow,
    };
  }, [messages]);

  const threads = useMemo(
    () => (activeThread ? [activeThread] : []),
    [activeThread],
  );

  const groupedThreads = useMemo(
    () => groupThreadsByRecency(threads),
    [threads],
  );

  // selectThread is a no-op in V1 — there is only ever one thread.
  // The function is in the contract so consumers compile against the
  // V2 surface unchanged.
  const selectThread = useCallback((_id: string) => {
    /* V2: load thread by id, swap copilot context's message buffer */
  }, []);

  const newThread = useCallback(() => {
    clearMessages();
  }, [clearMessages]);

  return {
    threads,
    groupedThreads,
    activeThreadId: activeThread?.id ?? null,
    selectThread,
    newThread,
  };
}

// ----------------------------------------------------------------------------

const TITLE_MAX_LEN = 64;

function truncateTitle(s: string): string {
  const cleaned = s.trim().replace(/\s+/g, ' ');
  if (cleaned.length <= TITLE_MAX_LEN) return cleaned;
  return cleaned.slice(0, TITLE_MAX_LEN - 1).trimEnd() + '…';
}

function groupThreadsByRecency(threads: ThreadSummary[]): GroupedThreads[] {
  if (threads.length === 0) return [];

  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  );
  const startOfYesterday = new Date(startOfToday);
  startOfYesterday.setDate(startOfYesterday.getDate() - 1);
  const startOfLast7 = new Date(startOfToday);
  startOfLast7.setDate(startOfLast7.getDate() - 7);

  const buckets: Record<RelativeDateGroup, ThreadSummary[]> = {
    today: [],
    yesterday: [],
    last_7_days: [],
    earlier: [],
  };

  for (const t of threads) {
    const d = new Date(t.lastActivityAt);
    if (d >= startOfToday) buckets.today.push(t);
    else if (d >= startOfYesterday) buckets.yesterday.push(t);
    else if (d >= startOfLast7) buckets.last_7_days.push(t);
    else buckets.earlier.push(t);
  }

  const labels: Record<RelativeDateGroup, string> = {
    today: 'TODAY',
    yesterday: 'YESTERDAY',
    last_7_days: 'LAST 7 DAYS',
    earlier: 'EARLIER',
  };

  return (Object.keys(buckets) as RelativeDateGroup[])
    .filter((g) => buckets[g].length > 0)
    .map((g) => ({ group: g, label: labels[g], threads: buckets[g] }));
}
