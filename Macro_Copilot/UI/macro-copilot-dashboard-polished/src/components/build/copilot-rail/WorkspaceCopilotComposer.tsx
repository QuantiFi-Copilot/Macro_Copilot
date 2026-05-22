// ============================================================================
// WorkspaceCopilotComposer — composer for the workspace-scoped rail.
// ----------------------------------------------------------------------------
// Same composer pattern Ask uses, scoped to the active workspace:
//   - prepends a workspace preamble to outbound messages so the LLM
//     understands which workspace the prompt refers to
//   - autosizes to ~3 rows
//   - ⌘/Ctrl+Enter sends
//
// Owns no state beyond the textarea value.  Sending dispatches to
// the singleton ``CopilotContext`` — the same WebSocket Ask uses —
// so the chat history stays continuous across the two surfaces.
// ============================================================================

import { useCallback, useRef, useState, type KeyboardEvent } from 'react';
import { ArrowUp } from 'lucide-react';
import { useCopilotContext } from '@/context/CopilotContext';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { cn } from '@/utils/cn';
import { composeScopedMessage } from './lib/workspaceScopedContext';

type Props = {
  workspace: WorkspaceDetail | null;
};

export function WorkspaceCopilotComposer({ workspace }: Props) {
  const { sendMessage, connectionStatus, isThinking } =
    useCopilotContext();
  const [value, setValue] = useState('');
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const disabled = connectionStatus !== 'ready' || isThinking;

  const send = useCallback(() => {
    const content = value.trim();
    if (!content || disabled) return;
    // R5.4 — pass the active workspace slug so the resulting turn's
    // messages are filterable in the per-workspace rail.  Falls back
    // to an un-scoped send when no workspace is bound (e.g. the
    // composer hasn't seen a workspace yet — defensive).
    sendMessage(composeScopedMessage(workspace, content), {
      workspaceSlug: workspace?.slug ?? null,
    });
    setValue('');
    if (taRef.current) taRef.current.style.height = 'auto';
  }, [value, disabled, workspace, sendMessage]);

  const onKey = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        send();
        return;
      }
      const t = e.currentTarget;
      t.style.height = 'auto';
      t.style.height = `${Math.min(t.scrollHeight, 140)}px`;
    },
    [send],
  );

  return (
    <div className="flex shrink-0 flex-col gap-1.5 border-t border-line-subtle bg-ink-900/40 px-3 py-3">
      <div className="composer-shell flex items-end gap-2 rounded-[10px] border border-line-soft bg-white/[0.012] px-3 py-2">
        <textarea
          ref={taRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKey}
          rows={1}
          disabled={disabled}
          placeholder={
            workspace
              ? 'Ask about this workspace…'
              : 'Open a workspace to chat with it.'
          }
          className="min-h-[24px] flex-1 resize-none bg-transparent text-[12.5px] leading-[1.5] text-fg-primary placeholder:text-fg-faint focus:outline-none disabled:cursor-not-allowed"
        />
        <button
          type="button"
          onClick={send}
          disabled={disabled || value.trim().length === 0}
          aria-label="Send"
          className={cn(
            'composer-send-active flex h-7 w-7 items-center justify-center rounded-md text-ink-900 transition-opacity',
            (disabled || value.trim().length === 0) && 'opacity-40',
          )}
        >
          <ArrowUp size={12} strokeWidth={2.5} />
        </button>
      </div>
      <div className="px-1 text-[10px] tracking-[0.04em] text-fg-faint">
        Workspace context is auto-prefixed · chat shares the Ask history.
      </div>
    </div>
  );
}
