import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import {
  ArrowUpRight,
  Ellipsis,
  Loader2,
  Sparkles,
  TerminalSquare,
  Trash2,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { useCopilot } from '@/hooks/useCopilot';
import { ChatMessageBubble } from '@/components/copilot/ChatMessage';
import { cn } from '@/utils/cn';

const STARTER_PROMPTS = [
  "Summarize today's rate moves",
  'Compare US and EUR 2s10s spreads',
  "What's implied by next FOMC meeting?",
  'Scan for z-score extremes above 2.0',
  'What is the BTP-Bund 10Y spread?',
];

export function ChatDrawer() {
  const { messages, sendMessage, connectionStatus, isThinking, clearMessages } =
    useCopilot();

  const [inputValue, setInputValue] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // ------------------------------------------------------------------
  // Auto-scroll to bottom on new messages / streaming tokens
  // ------------------------------------------------------------------
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    const handleFocusComposer = () => {
      inputRef.current?.focus();
    };

    window.addEventListener('copilot:focus-input', handleFocusComposer);
    return () => {
      window.removeEventListener('copilot:focus-input', handleFocusComposer);
    };
  }, []);

  // ------------------------------------------------------------------
  // Send handler
  // ------------------------------------------------------------------
  const handleSend = () => {
    if (!inputValue.trim() || isThinking) return;
    sendMessage(inputValue);
    setInputValue('');
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStarterClick = (prompt: string) => {
    if (isThinking) return;
    sendMessage(prompt);
  };

  // ------------------------------------------------------------------
  // Connection status
  // ------------------------------------------------------------------
  const isConnected = connectionStatus === 'ready';
  const isConnecting = connectionStatus === 'connecting';
  const statusLabel = isConnected
    ? 'PM Orchestrator · Ready'
    : isConnecting
      ? 'Connecting...'
      : 'Disconnected';
  const statusDot = isConnected
    ? 'bg-mint-400 shadow-[0_0_8px_rgba(63,214,154,0.5)]'
    : isConnecting
      ? 'bg-amber-400 animate-pulse'
      : 'bg-coral-400';

  // Show starter prompts only when no messages
  const showStarters = messages.length === 0;

  return (
    <aside className="panel relative flex h-full min-h-0 flex-col overflow-hidden">
      <div className="panel-divider-r absolute left-0 top-0 h-full w-px" />

      {/* ────────── Header ────────── */}
      <div className="flex h-[64px] shrink-0 items-center justify-between px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-[8px] border border-line-soft bg-gradient-to-br from-ice-500/25 to-ice-700/25 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
            <Sparkles size={13} className="text-ice-300" />
          </div>
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="truncate text-[13px] font-semibold text-fg-primary">
              Copilot
            </span>
            <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-fg-muted">
              <span className={cn('h-1 w-1 rounded-full', statusDot)} />
              {statusLabel}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-1">
          {messages.length > 0 && (
            <button
              onClick={clearMessages}
              title="Clear messages"
              className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary"
            >
              <Trash2 size={13} />
            </button>
          )}
          <button className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
            <Ellipsis size={14} />
          </button>
        </div>
      </div>

      <div className="mx-5 shrink-0 border-b border-line-subtle" />

      {/* ────────── Conversation area ────────── */}
      <div
        ref={scrollRef}
        className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-5 pt-5 pb-4"
      >
        {/* Empty state: starters + mode hint */}
        {showStarters && (
          <>
            {/* Welcome */}
            <div className="relative">
              <div className="absolute -left-1 top-2 h-6 w-[2px] rounded-full bg-gradient-to-b from-ice-400 to-transparent" />
              <div className="kicker mb-1.5 pl-2 text-fg-muted">Copilot</div>
              <p className="pl-2 text-[13px] leading-[1.55] text-fg-primary">
                Ready. Ask for yield levels, curve spreads, cross-market RV, or
                regime analysis.
              </p>
            </div>

            {/* Starter prompts */}
            <div>
              <div className="kicker mb-2.5 text-fg-muted">Starter prompts</div>
              <div className="space-y-1.5">
                {STARTER_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => handleStarterClick(prompt)}
                    disabled={!isConnected || isThinking}
                    className="group flex w-full items-center justify-between gap-3 rounded-lg border border-line-soft bg-white/[0.012] px-3.5 py-2.5 text-left text-[12px] font-medium tracking-[-0.005em] text-fg-secondary transition-all duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.03] hover:text-fg-primary disabled:opacity-50 disabled:hover:border-line-soft disabled:hover:bg-white/[0.012]"
                  >
                    <span className="truncate">{prompt}</span>
                    <ArrowUpRight
                      size={13}
                      className="shrink-0 text-fg-faint transition-colors group-hover:text-ice-300"
                    />
                  </button>
                ))}
              </div>
            </div>

            {/* Mode hint */}
            <div className="rounded-lg border border-line-subtle bg-white/[0.008] p-3.5">
              <div className="flex items-start gap-2.5">
                <TerminalSquare size={13} className="mt-0.5 text-fg-muted" />
                <div className="flex-1">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-fg-muted">
                    Deterministic mode
                  </p>
                  <p className="mt-1 text-[11.5px] leading-[1.55] text-fg-secondary">
                    All math routes through the analytics engine. The LLM
                    translates only.
                  </p>
                </div>
              </div>
            </div>
          </>
        )}

        {/* Messages */}
        {messages.map((msg) => (
          <ChatMessageBubble key={msg.id} message={msg} />
        ))}
      </div>

      {/* ────────── Composer ────────── */}
      <div className="shrink-0 px-5 pb-5 pt-4">
        <div
          className={cn(
            'group flex items-center gap-2 rounded-xl border bg-white/[0.018] px-3 py-2.5 transition-colors duration-150 ease-sleek',
            isConnected
              ? 'border-line-soft focus-within:border-ice-400/40 focus-within:bg-white/[0.03] focus-within:shadow-[0_0_0_3px_rgba(122,162,255,0.08)]'
              : 'border-coral-400/20 bg-coral-400/[0.03]',
          )}
        >
          {/* Connection indicator */}
          <div className="shrink-0 rounded-md p-1 text-fg-muted">
            {isConnected ? (
              <Wifi size={13} className="text-fg-faint" />
            ) : (
              <WifiOff size={13} className="text-coral-400/60" />
            )}
          </div>

          <input
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              isConnected
                ? 'Ask anything about rates...'
                : isConnecting
                  ? 'Connecting to copilot...'
                  : 'Copilot disconnected'
            }
            className="flex-1 bg-transparent text-[12.5px] text-fg-primary placeholder:text-fg-muted focus:outline-none"
          />

          <button
            type="button"
            onClick={handleSend}
            disabled={!isConnected || isThinking || !inputValue.trim()}
            aria-label="Send"
            className={cn(
              'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border transition-all duration-150 ease-sleek',
              isConnected && inputValue.trim() && !isThinking
                ? 'border-ice-400/30 bg-gradient-to-b from-ice-500/20 to-ice-700/20 text-ice-200 hover:border-ice-400/50 hover:from-ice-500/30 hover:to-ice-700/30'
                : 'border-line-soft bg-white/[0.02] text-fg-faint',
            )}
          >
            {isThinking ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <ArrowUpRight size={13} />
            )}
          </button>
        </div>

        <div className="mt-2 flex items-center justify-between px-1 text-[10px] text-fg-faint">
          <span className="uppercase tracking-[0.14em]">Enter to send</span>
          <span className="mono">deterministic · sonnet 4</span>
        </div>
      </div>
    </aside>
  );
}
