// ============================================================================
// UserMessage — right-aligned typographic block with hover edit affordance
// ----------------------------------------------------------------------------
// Two visual states:
//   1. STATIC — the user's prompt as a soft surface block; on hover, a
//      pencil affordance fades in on the top-right.  Click → enter
//      edit mode.
//   2. EDITING — replaces the static block with an inline auto-growing
//      textarea + Save / Cancel buttons.  Save submits the edited
//      content via the parent-supplied callback (which forks the
//      conversation from this turn).  ⌘/Ctrl+Enter saves; Escape
//      cancels.
//
// Editing while a stream is in flight is forbidden by the parent
// (AskPage) — when `disabled` is true the edit chrome is hidden so
// the affordance doesn't suggest an action that would no-op.
// ============================================================================

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { Check, Pencil, X } from 'lucide-react';
import { cn } from '@/utils/cn';

type Props = {
  messageId: string;
  content: string;
  timestamp: Date;
  isEditing: boolean;
  /** True while a turn is mid-stream OR while another message is being
   *  edited.  In both cases we suppress the edit affordance to avoid a
   *  half-active interaction. */
  disabled: boolean;
  onEditStart: (messageId: string) => void;
  onEditCancel: () => void;
  onEditSubmit: (messageId: string, newContent: string) => void;
};

export function UserMessage({
  messageId,
  content,
  timestamp,
  isEditing,
  disabled,
  onEditStart,
  onEditCancel,
  onEditSubmit,
}: Props) {
  return (
    <div className="flex flex-col items-end">
      {isEditing ? (
        <UserMessageEditor
          messageId={messageId}
          initialContent={content}
          onCancel={onEditCancel}
          onSubmit={onEditSubmit}
        />
      ) : (
        <UserMessageStatic
          content={content}
          disabled={disabled}
          onEdit={() => onEditStart(messageId)}
        />
      )}
      <span className="mt-1.5 mr-1 font-mono text-[10px] tracking-[0.02em] text-fg-faint">
        {formatTime(timestamp)}
      </span>
    </div>
  );
}

// ----------------------------------------------------------------------------

function UserMessageStatic({
  content,
  disabled,
  onEdit,
}: {
  content: string;
  disabled: boolean;
  onEdit: () => void;
}) {
  return (
    <div className="group relative max-w-[640px]">
      <div
        className={cn(
          'rounded-[12px] px-4 py-2.5 text-[14px] leading-[1.55] tracking-[-0.005em] text-fg-primary',
          // Soft layered surface — a touch warmer than the assistant
          // card so the conversational direction reads at a glance.
          'bg-[linear-gradient(180deg,rgba(255,255,255,0.045),rgba(255,255,255,0.012)_60%)]',
          'shadow-[inset_0_1px_0_rgba(255,255,255,0.05),inset_0_0_0_1px_rgba(148,163,184,0.08)]',
        )}
      >
        {content}
      </div>

      {!disabled && (
        <button
          type="button"
          onClick={onEdit}
          aria-label="Edit message"
          title="Edit message"
          className={cn(
            'absolute -left-9 top-1/2 -translate-y-1/2 flex h-7 w-7 items-center justify-center rounded-md',
            'text-fg-faint opacity-0 transition-all duration-200 ease-[cubic-bezier(0.2,0.8,0.2,1)]',
            'group-hover:opacity-100 hover:!text-ice-300 hover:!bg-white/[0.04]',
          )}
        >
          <Pencil size={11} strokeWidth={2} />
        </button>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------

function UserMessageEditor({
  messageId,
  initialContent,
  onCancel,
  onSubmit,
}: {
  messageId: string;
  initialContent: string;
  onCancel: () => void;
  onSubmit: (messageId: string, newContent: string) => void;
}) {
  const [value, setValue] = useState(initialContent);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Focus + place caret at end on mount.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(initialContent.length, initialContent.length);
  }, [initialContent]);

  // Auto-grow up to ~6 lines (180px) before falling back to scroll.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value]);

  const trimmed = value.trim();
  const canSubmit = trimmed.length > 0 && trimmed !== initialContent.trim();

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
      return;
    }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (canSubmit) onSubmit(messageId, trimmed);
      return;
    }
  };

  return (
    <div className="w-[640px] max-w-full">
      <div
        className={cn(
          'rounded-[12px] px-4 py-3',
          'bg-[linear-gradient(180deg,rgba(255,255,255,0.05),rgba(255,255,255,0.014)_60%)]',
          'shadow-[inset_0_1px_0_rgba(255,255,255,0.06),inset_0_0_0_1px_rgba(122,162,255,0.30),0_0_0_4px_rgba(122,162,255,0.06)]',
        )}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKey}
          rows={1}
          className="w-full resize-none bg-transparent text-[14px] leading-[1.55] tracking-[-0.005em] text-fg-primary placeholder:text-fg-faint focus:outline-none"
          style={{ maxHeight: '180px' }}
        />
        <div className="mt-2 flex items-center justify-between gap-3">
          <span className="font-mono text-[10px] tracking-[0.04em] text-fg-faint">
            ⌘↩ to save · esc to cancel · saving forks the conversation here
          </span>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={onCancel}
              className="flex h-7 items-center gap-1 rounded-md px-2.5 text-[11.5px] font-medium text-fg-secondary transition-colors hover:bg-white/[0.03] hover:text-fg-primary"
            >
              <X size={11} />
              <span>Cancel</span>
            </button>
            <button
              type="button"
              disabled={!canSubmit}
              onClick={() => canSubmit && onSubmit(messageId, trimmed)}
              className={cn(
                'flex h-7 items-center gap-1 rounded-md px-2.5 text-[11.5px] font-medium transition-all duration-200 ease-sleek',
                canSubmit
                  ? 'composer-send-active'
                  : 'cursor-not-allowed border border-line-soft bg-white/[0.02] text-fg-faint',
              )}
            >
              <Check size={11} strokeWidth={2.25} />
              <span>Save & resubmit</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------

function formatTime(d: Date): string {
  return d.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}
