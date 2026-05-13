// ============================================================================
// EmptyStateComposer — top composer on the Build empty state.
// ----------------------------------------------------------------------------
// Sits beneath the 6 category tiles.  Forwards the typed prompt to the
// global copilot (same WebSocket Ask uses), then waits for the workflow
// router's decision via the shared ``messages`` buffer.  When the
// router routes to a real workflow + the runner finishes, the chat
// emits ``workflow_result`` carrying the persisted workspace slug; the
// BuildShell observes that and navigates the user to the slug page.
//
// We intentionally use the SAME ``sendMessage`` plumbing as Ask so
// there's one place to fix any chat-side regression.  The empty-state
// composer's only job is "drop the user's intent onto the wire and
// transition the canvas into Building mode."
//
// Styling matches Ask's composer at a smaller density — single-line
// preferred, expands to ~3 rows on overflow.  Keyboard:
//   ⌘+Enter / Ctrl+Enter      send
//   /                          drop into composer (focus + clear)
// ============================================================================

import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { ArrowUp } from 'lucide-react';
import { cn } from '@/utils/cn';

export interface EmptyStateComposerHandle {
  /** Replace the composer's current text and focus it.  Used by the
   *  category tiles to seed a prompt. */
  seed: (next: string) => void;
}

type Props = {
  onSend: (content: string) => void;
  /** When true, the composer is disabled (e.g. the WebSocket is
   *  CLOSED).  We don't auto-open the WS here — the shared
   *  ``CopilotContext`` does that — so this is just the UX hint. */
  disabled?: boolean;
  /** Pre-fill the composer on first mount.  Used by the "Refine and
   *  retry" affordance on ``BuildResultStalled`` so the user returns
   *  to the empty state with their previous prompt ready to edit. */
  initialValue?: string;
};

const PLACEHOLDER =
  'Describe any analysis you want to build…';
const HINT_LINE = '/ for primitives  ·  @ for saved artifacts  ·  ⌘K for library';

export const EmptyStateComposer = forwardRef<
  EmptyStateComposerHandle,
  Props
>(function EmptyStateComposer({ onSend, disabled, initialValue }, ref) {
  const [value, setValue] = useState(initialValue ?? '');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useImperativeHandle(
    ref,
    () => ({
      seed(next: string) {
        setValue(next);
        // Defer focus so the parent's state flush completes first.
        requestAnimationFrame(() => textareaRef.current?.focus());
      },
    }),
    [],
  );

  const send = useCallback(() => {
    const content = value.trim();
    if (!content || disabled) return;
    onSend(content);
    setValue('');
  }, [value, disabled, onSend]);

  const onKey = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        send();
        return;
      }
      // Auto-grow rows up to a soft cap.
      const t = e.currentTarget;
      t.style.height = 'auto';
      t.style.height = `${Math.min(t.scrollHeight, 160)}px`;
    },
    [send],
  );

  return (
    <div className="composer-shell flex flex-col gap-1.5 rounded-[12px] border border-line-soft bg-white/[0.012] px-3 py-2">
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKey}
          rows={1}
          disabled={disabled}
          placeholder={PLACEHOLDER}
          className="min-h-[28px] flex-1 resize-none bg-transparent text-[13px] leading-[1.5] text-fg-primary placeholder:text-fg-faint focus:outline-none disabled:cursor-not-allowed"
        />
        <button
          type="button"
          onClick={send}
          disabled={disabled || value.trim().length === 0}
          aria-label="Send"
          className={cn(
            'composer-send-active flex h-8 w-8 items-center justify-center rounded-md border border-transparent text-ink-900 transition-opacity',
            (disabled || value.trim().length === 0) && 'opacity-40',
          )}
        >
          <ArrowUp size={14} strokeWidth={2.5} />
        </button>
      </div>

      <div className="px-1 text-[10px] tracking-[0.04em] text-fg-faint">
        {HINT_LINE}
      </div>
    </div>
  );
});
