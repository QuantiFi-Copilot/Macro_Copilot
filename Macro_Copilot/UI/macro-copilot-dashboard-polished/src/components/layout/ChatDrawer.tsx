import { ArrowUpRight, Ellipsis, Paperclip, Sparkles, TerminalSquare } from 'lucide-react';
import type { ChatMessage, ChatSuggestion } from '@/types/dashboard';

type ChatDrawerProps = {
  suggestions: ChatSuggestion[];
  messages: ChatMessage[];
};

export function ChatDrawer({ suggestions, messages }: ChatDrawerProps) {
  const assistantMessage =
    messages.find((m) => m.role === 'assistant')?.content ??
    'Ready. Ask for cross-asset RV, curve dislocations, or event analogs.';

  return (
    <aside className="panel relative flex h-full min-h-0 flex-col">
      <div className="panel-divider-r absolute left-0 top-0 h-full w-px" />

      {/* Header */}
      <div className="flex h-[64px] items-center justify-between px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-[8px] border border-line-soft bg-gradient-to-br from-ice-500/25 to-ice-700/25 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
            <Sparkles size={13} className="text-ice-300" />
          </div>
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="truncate text-[13px] font-semibold text-fg-primary">
              Copilot
            </span>
            <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-fg-muted">
              <span className="h-1 w-1 rounded-full bg-mint-400 shadow-[0_0_8px_rgba(63,214,154,0.5)]" />
              PM Orchestrator · Ready
            </span>
          </div>
        </div>
        <button className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
          <Ellipsis size={14} />
        </button>
      </div>

      <div className="mx-5 border-b border-line-subtle" />

      {/* Conversation area */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 pt-5">
        {/* Intro / last assistant turn */}
        <div className="relative">
          <div className="absolute -left-1 top-2 h-6 w-[2px] rounded-full bg-gradient-to-b from-ice-400 to-transparent" />
          <div className="kicker mb-1.5 pl-2 text-fg-muted">Copilot</div>
          <p className="pl-2 text-[13px] leading-[1.55] text-fg-primary">
            {assistantMessage}
          </p>
        </div>

        {/* Suggestions */}
        <div className="mt-6">
          <div className="kicker mb-2.5 text-fg-muted">Starter prompts</div>
          <div className="space-y-1.5">
            {suggestions.map((s) => (
              <button
                key={s.id}
                type="button"
                className="group flex w-full items-center justify-between gap-3 rounded-lg border border-line-soft bg-white/[0.012] px-3.5 py-2.5 text-left text-[12px] font-medium tracking-[-0.005em] text-fg-secondary transition-all duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.03] hover:text-fg-primary"
              >
                <span className="truncate">{s.label}</span>
                <ArrowUpRight
                  size={13}
                  className="shrink-0 text-fg-faint transition-colors group-hover:text-ice-300"
                />
              </button>
            ))}
          </div>
        </div>

        {/* Capabilities hint */}
        <div className="mt-6 rounded-lg border border-line-subtle bg-white/[0.008] p-3.5">
          <div className="flex items-start gap-2.5">
            <TerminalSquare size={13} className="mt-0.5 text-fg-muted" />
            <div className="flex-1">
              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-fg-muted">
                Deterministic mode
              </p>
              <p className="mt-1 text-[11.5px] leading-[1.55] text-fg-secondary">
                All math routes through the analytics engine. The LLM translates only.
              </p>
            </div>
          </div>
        </div>

        <div className="flex-1" />
      </div>

      {/* Composer */}
      <div className="px-5 pb-5 pt-4">
        <div className="group flex items-center gap-2 rounded-xl border border-line-soft bg-white/[0.018] px-3 py-2.5 transition-colors duration-150 ease-sleek focus-within:border-ice-400/40 focus-within:bg-white/[0.03] focus-within:shadow-[0_0_0_3px_rgba(122,162,255,0.08)]">
          <button className="shrink-0 rounded-md p-1 text-fg-muted transition-colors hover:text-fg-secondary">
            <Paperclip size={13} />
          </button>
          <input
            type="text"
            placeholder="Ask anything, /command, or @agent…"
            className="flex-1 bg-transparent text-[12.5px] text-fg-primary placeholder:text-fg-muted focus:outline-none"
          />
          <button
            type="button"
            aria-label="Send"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-ice-400/30 bg-gradient-to-b from-ice-500/20 to-ice-700/20 text-ice-200 transition-all duration-150 ease-sleek hover:border-ice-400/50 hover:from-ice-500/30 hover:to-ice-700/30"
          >
            <ArrowUpRight size={13} />
          </button>
        </div>
        <div className="mt-2 flex items-center justify-between px-1 text-[10px] text-fg-faint">
          <span className="uppercase tracking-[0.14em]">Shift+Enter newline</span>
          <span className="mono">claude-opus · 4.6</span>
        </div>
      </div>
    </aside>
  );
}
