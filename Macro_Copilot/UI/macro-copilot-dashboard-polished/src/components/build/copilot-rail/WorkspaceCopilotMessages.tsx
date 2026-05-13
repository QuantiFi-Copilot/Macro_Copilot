// ============================================================================
// WorkspaceCopilotMessages — compact message feed for the rail.
// ----------------------------------------------------------------------------
// Reads the singleton ``CopilotContext`` and renders the most recent
// 6 messages.  Intentionally compact (no rich tool traces / DAG
// strips) — the rail is for ongoing dialogue, not deep inspection.
// For a full conversation view, the user is one click away in Ask.
//
// Special-case rendering:
//   - When an assistant message carries a workflow result with a
//     ``workspace.slug``, surface a small "→ Opens this workspace"
//     link so the user can jump.
//   - When an assistant message carries ``proposedOverrides`` (the
//     Phase 4 chat-driven override channel), render the chips
//     inline so a single click queues the suggestion into the
//     shared ``WorkspaceOverridesProvider``.
// ============================================================================

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Loader2 } from 'lucide-react';
import { useCopilotContext } from '@/context/CopilotContext';
import type { CopilotMessage } from '@/types/copilot';
import { cn } from '@/utils/cn';
import { ProposedOverridesChips } from './ProposedOverridesChips';

const TAIL = 6;

export function WorkspaceCopilotMessages() {
  const { messages, isThinking } = useCopilotContext();
  const tail = useMemo(() => messages.slice(-TAIL), [messages]);

  if (tail.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-line-soft bg-white/[0.005] px-3 py-4 text-center text-[11px] text-fg-faint">
        No messages yet — send a prompt below.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {tail.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      {isThinking && (
        <div className="flex items-center gap-1.5 px-2 text-[11px] text-fg-muted">
          <Loader2 size={11} className="animate-spin" />
          <span>thinking…</span>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message }: { message: CopilotMessage }) {
  const navigate = useNavigate();
  const isUser = message.role === 'user';
  const workspaceSlug = message.workflow?.workspace?.slug ?? null;
  const proposals = message.proposedOverrides ?? null;

  return (
    <div
      className={cn(
        'flex flex-col gap-1 rounded-md border px-2.5 py-1.5',
        isUser
          ? 'border-ice-400/25 bg-ice-500/[0.05]'
          : 'border-line-soft bg-white/[0.012]',
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span
          className={cn(
            'kicker',
            isUser ? 'text-ice-200' : 'text-fg-muted',
          )}
        >
          {isUser ? 'You' : 'Copilot'}
        </span>
        {message.totalDurationMs != null && (
          <span className="font-mono text-[9.5px] text-fg-faint">
            {Math.round(message.totalDurationMs)}ms
          </span>
        )}
      </div>
      <p className="whitespace-pre-wrap text-[11.5px] leading-[1.5] text-fg-secondary">
        {trimContent(message.content)}
      </p>
      {workspaceSlug && (
        <button
          type="button"
          onClick={() => navigate(`/workspace/${workspaceSlug}`)}
          className="mt-1 flex items-center gap-1 self-start rounded-sm border border-ice-400/30 px-1.5 py-0.5 text-[10px] font-medium text-ice-200 transition-colors hover:bg-ice-500/[0.08]"
        >
          <ArrowRight size={10} />
          <span>Opens /workspace/{workspaceSlug}</span>
        </button>
      )}
      {proposals && proposals.length > 0 && (
        <ProposedOverridesChips proposals={proposals} />
      )}
    </div>
  );
}

function trimContent(content: string, max = 380): string {
  if (content.length <= max) return content;
  return `${content.slice(0, max)}…`;
}
