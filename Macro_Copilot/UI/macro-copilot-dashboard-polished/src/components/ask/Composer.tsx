// ============================================================================
// Composer — the input shell at the bottom of the conversation canvas
// ----------------------------------------------------------------------------
// Auto-growing textarea (single-line at rest, up to ~7 lines before
// internal scroll), 880px max-width, centered, with:
//   - A `/` glyph hint on the left edge (slash-commands ship in V2)
//   - A 32px arrow-send button on the right edge (filled on focus +
//     non-empty, ghost otherwise; spinner while a turn is mid-flight)
//   - A slim hint row below that conveys command affordances
//
// Connection states:
//   - ready:        normal placeholder, send enabled when non-empty
//   - connecting:   dimmed placeholder ("Connecting to copilot…")
//   - disconnected: coral border, placeholder explains
//
// External integration:
//   - listens for `copilot:set-input` to receive prompts pushed in by
//     other surfaces (FollowUps, starting points, future Library
//     entries).  Same event the legacy ChatDrawer subscribes to, so
//     nothing else has to change.
//   - listens for `copilot:focus-input` to focus the textarea (used by
//     the top nav search button).
// ============================================================================

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  forwardRef,
  useImperativeHandle,
} from 'react';
import { ArrowUp, Loader2 } from 'lucide-react';
import { cn } from '@/utils/cn';
import type { ConnectionStatus } from '@/types/copilot';

type ComposerProps = {
  connectionStatus: ConnectionStatus;
  isThinking: boolean;
  onSend: (content: string) => void;
};

export type ComposerHandle = {
  /** Programmatically inject a prompt into the input and focus.  Used
   *  by the AskPage to wire FollowUps + starting-point clicks without
   *  needing the cross-component custom-event dance. */
  setValue: (value: string) => void;
  focus: () => void;
};

export const Composer = forwardRef<ComposerHandle, ComposerProps>(
  function Composer({ connectionStatus, isThinking, onSend }, ref) {
    const [value, setValue] = useState('');
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    useImperativeHandle(
      ref,
      () => ({
        setValue: (v: string) => {
          setValue(v);
          // Focus + place caret at end so the user can edit immediately.
          requestAnimationFrame(() => {
            const el = textareaRef.current;
            if (!el) return;
            el.focus();
            el.setSelectionRange(v.length, v.length);
          });
        },
        focus: () => textareaRef.current?.focus(),
      }),
      [],
    );

    // Auto-grow.  Cap at 240px before falling back to internal scroll
    // (matches the design brief).
    useEffect(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
    }, [value]);

    // Cross-component event hooks — preserve the same pattern the
    // legacy ChatDrawer uses so existing pages can still drop prompts
    // here.
    useEffect(() => {
      const focusHandler = () => textareaRef.current?.focus();
      const setHandler = (e: Event) => {
        const detail = (e as CustomEvent).detail;
        if (typeof detail === 'string') {
          setValue(detail);
          requestAnimationFrame(() => textareaRef.current?.focus());
        }
      };
      window.addEventListener('copilot:focus-input', focusHandler);
      window.addEventListener('copilot:set-input', setHandler);
      return () => {
        window.removeEventListener('copilot:focus-input', focusHandler);
        window.removeEventListener('copilot:set-input', setHandler);
      };
    }, []);

    const isReady = connectionStatus === 'ready';
    const isConnecting = connectionStatus === 'connecting';
    const canSend = isReady && !isThinking && value.trim().length > 0;

    const placeholder = isReady
      ? 'Ask a question, describe an analysis, or type / for primitives.'
      : isConnecting
        ? 'Connecting to copilot…'
        : 'Copilot disconnected — your message will be lost.';

    const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!canSend) return;
        onSend(value.trim());
        setValue('');
      }
    };

    const handleSendClick = () => {
      if (!canSend) return;
      onSend(value.trim());
      setValue('');
    };

    return (
      <div className="mx-auto w-full max-w-[880px] px-6 pb-6 pt-4">
        <div
          className={cn(
            'composer-shell relative flex items-end gap-2 px-4 py-3',
            !isReady && 'border-coral-400/35 bg-coral-400/[0.03]',
          )}
        >
          {/* Left edge hint */}
          <span className="mono shrink-0 select-none pb-1 pl-px pr-1 text-[12px] text-fg-faint">
            /
          </span>

          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKey}
            placeholder={placeholder}
            rows={1}
            disabled={!isReady}
            className={cn(
              'flex-1 resize-none bg-transparent text-[14px] leading-[1.5] text-fg-primary placeholder:text-fg-faint',
              'focus:outline-none disabled:cursor-not-allowed disabled:opacity-70',
            )}
            style={{ maxHeight: '240px' }}
          />

          <button
            type="button"
            onClick={handleSendClick}
            disabled={!canSend}
            aria-label="Send"
            className={cn(
              'flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-all duration-150 ease-sleek',
              canSend
                ? 'border border-ice-400/40 bg-ice-500/15 text-ice-100 hover:border-ice-400/60 hover:bg-ice-500/25'
                : 'border border-line-soft bg-white/[0.02] text-fg-faint',
            )}
          >
            {isThinking ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <ArrowUp size={13} />
            )}
          </button>
        </div>

        <p className="mt-2 text-center mono text-[10.5px] text-fg-faint">
          / for primitives · @ for saved artifacts · ⌘K for library
        </p>
      </div>
    );
  },
);
