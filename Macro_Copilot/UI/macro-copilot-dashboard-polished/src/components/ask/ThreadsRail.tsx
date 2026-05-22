// ============================================================================
// ThreadsRail — the left rail of the Ask surface
// ----------------------------------------------------------------------------
// Three stacked sections:
//   - "+ New thread" full-width ghost button
//   - Filter pills: Today · Pinned · All  (V1: visual only, no filter)
//   - Grouped thread list with relative-date headers
//
// V1 reality:
//   - Threads come from `useThreads`, which today derives a single
//     thread from the live message buffer.  When `messages.length === 0`
//     the rail shows its empty state (a quiet hint about new threads).
//   - "+ New thread" calls `clearMessages` via the hook.
//   - "Today / Pinned / All" pills are rendered inactive in V1 (no
//     filtering until persistent multi-thread arrives in V2); structure
//     is in place so wiring is a single-file change later.
// ============================================================================

import { useState } from 'react';
import { Plus, Pin } from 'lucide-react';
import type { GroupedThreads, ThreadSummary } from '@/hooks/useThreads';
import { cn } from '@/utils/cn';

type Props = {
  groupedThreads: GroupedThreads[];
  activeThreadId: string | null;
  onSelectThread: (id: string) => void;
  onNewThread: () => void;
};

type Filter = 'today' | 'pinned' | 'all';

export function ThreadsRail({
  groupedThreads,
  activeThreadId,
  onSelectThread,
  onNewThread,
}: Props) {
  const [filter, setFilter] = useState<Filter>('today');

  const hasThreads = groupedThreads.length > 0;

  return (
    <aside className="relative flex h-full min-h-0 w-full flex-col overflow-hidden border-r border-line-subtle">
      {/* Header */}
      <div className="px-4 pt-5">
        <p className="kicker text-fg-muted">ASK · THREADS</p>
        <button
          type="button"
          onClick={onNewThread}
          className="mt-3 flex w-full items-center gap-2 rounded-md border border-line-soft bg-white/[0.012] px-3 py-2 text-[12.5px] text-fg-secondary transition-colors duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.025] hover:text-fg-primary"
        >
          <Plus size={12} className="text-fg-muted" />
          <span>New thread</span>
        </button>
      </div>

      {/* Filters */}
      <div className="mt-4 flex items-center gap-3 px-5 text-[11px]">
        {(['today', 'pinned', 'all'] as Filter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={cn(
              'relative pb-1 font-medium uppercase tracking-[0.06em] transition-colors',
              filter === f
                ? 'text-fg-primary'
                : 'text-fg-muted hover:text-fg-secondary',
            )}
          >
            {f}
            {filter === f && (
              <span className="absolute inset-x-0 bottom-0 h-[2px] rounded bg-ice-400" />
            )}
          </button>
        ))}
      </div>

      <div className="mt-3 border-t border-line-subtle" />

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {hasThreads ? (
          <ul className="space-y-3">
            {groupedThreads.map((g) => (
              <li key={g.group}>
                <p className="px-3 pb-1 text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                  {g.label}
                </p>
                <ul className="space-y-px">
                  {g.threads.map((t) => (
                    <ThreadRow
                      key={t.id}
                      thread={t}
                      active={t.id === activeThreadId}
                      onClick={() => onSelectThread(t.id)}
                    />
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        ) : (
          <div className="px-3 pt-6 text-[11.5px] leading-[1.55] text-fg-muted">
            <p>Threads stay listed here as you ask questions.</p>
            <p className="mt-2 text-fg-faint">
              Persistent multi-thread storage lands in V2.
            </p>
          </div>
        )}
      </div>
    </aside>
  );
}

function ThreadRow({
  thread,
  active,
  onClick,
}: {
  thread: ThreadSummary;
  active: boolean;
  onClick: () => void;
}) {
  const time = new Date(thread.lastActivityAt).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'group relative flex w-full flex-col gap-0.5 rounded-lg px-3 py-2.5 text-left transition-all duration-200 ease-sleek',
          active
            ? // Active state: gradient sweep from left + soft inner highlight
              'bg-[linear-gradient(90deg,rgba(122,162,255,0.08)_0%,rgba(122,162,255,0.025)_55%,transparent_100%)] shadow-[inset_0_1px_0_rgba(255,255,255,0.04),inset_0_0_0_1px_rgba(122,162,255,0.16)]'
            : 'hover:bg-white/[0.022]',
        )}
      >
        {active && (
          <span
            aria-hidden
            className="absolute inset-y-1.5 left-0 w-[2px] rounded-r-full bg-gradient-to-b from-transparent via-ice-400 to-transparent shadow-[0_0_10px_rgba(122,162,255,0.45)]"
          />
        )}
        <span
          className={cn(
            'line-clamp-1 text-[12.5px] leading-[1.4] tracking-[-0.005em] transition-colors',
            active ? 'text-fg-primary' : 'text-fg-secondary group-hover:text-fg-primary',
          )}
        >
          {thread.title}
        </span>
        <span className="flex items-center gap-1.5 font-mono text-[10px] text-fg-muted">
          {thread.lastWorkflowTemplateId ? (
            <>
              <span className="truncate">{thread.lastWorkflowTemplateId}</span>
              <span className="text-fg-faint">·</span>
            </>
          ) : null}
          <span>
            {thread.messageCount} {thread.messageCount === 1 ? 'turn' : 'turns'}
          </span>
          <span className="text-fg-faint">·</span>
          <span>{time}</span>
          {/* Pin slot — not yet wired in V1, kept for design continuity. */}
          <Pin
            size={9}
            className="ml-auto text-fg-faint opacity-0 transition-opacity group-hover:opacity-60"
          />
        </span>
      </button>
    </li>
  );
}
