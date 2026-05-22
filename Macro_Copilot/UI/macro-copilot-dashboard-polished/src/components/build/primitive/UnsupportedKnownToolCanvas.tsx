// ============================================================================
// UnsupportedKnownToolCanvas — explicit state for tools Build hasn't wired yet.
// ----------------------------------------------------------------------------
// PR1 — Build Routing Coverage.  When the user clicks "Open in Build"
// (or hits a deep link / Ask handoff) for a tool the backend registers
// but Build doesn't have a renderer for yet, we land here instead of
// dropping the user in the orange decode-error card.
//
// Renders three pieces of information:
//   1. The tool name (canonical, monospace) + a "known but unsupported"
//      pill so the user understands the difference from "unknown".
//   2. A per-tool reason ("why not yet") from
//      ``UNSUPPORTED_KNOWN_REASONS`` in ``lib/toolNames.ts``.
//   3. A "what works now" hint pointing to Ask / Library / the closest
//      working Build surface.
//
// The card is intentionally NOT silent — every entry surfaces the
// honest gap so the user trusts the page.
// ============================================================================

import { Construction, MessageSquare, BookOpen } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { unsupportedKnownReasonFor } from '@/lib/toolNames';

type Props = {
  /** Canonical tool name (already normalised by ``decodePrimitiveContext``
   *  upstream). */
  toolName: string;
  /** Original params the user invoked the tool with.  Preserved so the
   *  "Try in Ask" affordance can hand the call back to the chat with
   *  the intended args. */
  params: Record<string, string>;
};

export function UnsupportedKnownToolCanvas({ toolName, params }: Props) {
  const navigate = useNavigate();
  const reason = unsupportedKnownReasonFor(toolName);

  const handleTryInAsk = () => {
    // Seed the Ask composer with a clean run prompt — the same channel
    // ToolDetailDrawer's "Try in Ask" CTA uses.
    const argSummary = formatArgsForPrompt(params);
    const prompt = argSummary
      ? `Run ${toolName} ${argSummary} and explain the output.`
      : `Run ${toolName} with default params and explain the output.`;
    window.dispatchEvent(
      new CustomEvent('copilot:set-input', { detail: prompt }),
    );
    navigate('/ask');
  };

  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6 py-10">
      <div className="card flex max-w-[560px] flex-col gap-5 px-6 py-5">
        <header className="flex items-start gap-3">
          <span
            aria-hidden
            className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-amber-400/30 bg-amber-500/10 text-amber-300"
          >
            <Construction size={16} strokeWidth={1.75} />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="rounded-sm border border-amber-400/35 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.18em] text-amber-200">
                known · unsupported in build
              </span>
            </div>
            <h2 className="mt-1.5 text-[15.5px] font-semibold tracking-[-0.012em] text-fg-primary">
              {reason.label}
            </h2>
            <div className="mt-0.5 font-mono text-[10.5px] text-fg-muted">
              {toolName}
            </div>
          </div>
        </header>

        <section className="flex flex-col gap-2">
          <p className="kicker text-fg-muted">Why not yet</p>
          <p className="text-[12.5px] leading-[1.55] text-fg-secondary">
            {reason.reason}
          </p>
        </section>

        <section className="flex flex-col gap-2">
          <p className="kicker text-fg-muted">What works today</p>
          <p className="text-[12.5px] leading-[1.55] text-fg-secondary">
            {reason.whatWorksNow}
          </p>
        </section>

        {Object.keys(params).length > 0 && (
          <section className="flex flex-col gap-1.5">
            <p className="kicker text-fg-muted">Your call</p>
            <div className="flex flex-wrap gap-1">
              {Object.entries(params).map(([k, v]) => (
                <span
                  key={k}
                  className="inline-flex items-center gap-1 rounded-sm border border-line-soft bg-white/[0.025] px-1.5 py-0.5 font-mono text-[10px] text-fg-secondary"
                >
                  <span className="text-fg-faint">{k}</span>
                  <span className="text-fg-muted">=</span>
                  <span>{v}</span>
                </span>
              ))}
            </div>
          </section>
        )}

        <div className="mt-1 flex flex-wrap items-center gap-2 border-t border-line-subtle pt-4">
          <button
            type="button"
            onClick={handleTryInAsk}
            className="flex items-center gap-1.5 rounded-md border border-line-soft bg-white/[0.025] px-3 py-1.5 text-[11.5px] font-medium text-fg-secondary transition-colors hover:border-ice-400/35 hover:text-ice-200"
          >
            <MessageSquare size={11} strokeWidth={1.75} />
            <span>Try this in Ask</span>
          </button>
          <button
            type="button"
            onClick={() => navigate('/library')}
            className="flex items-center gap-1.5 rounded-md border border-line-soft bg-white/[0.025] px-3 py-1.5 text-[11.5px] font-medium text-fg-secondary transition-colors hover:border-ice-400/35 hover:text-ice-200"
          >
            <BookOpen size={11} strokeWidth={1.75} />
            <span>Read the Library card</span>
          </button>
        </div>
      </div>
    </div>
  );
}

/** Render the params dict as a short " key=value key=value …" suffix
 *  for the Ask prompt.  Used when seeding the composer so the LLM
 *  sees the user's intended args. */
function formatArgsForPrompt(params: Record<string, string>): string {
  const pairs = Object.entries(params).filter(([, v]) => v && v.length > 0);
  if (pairs.length === 0) return '';
  return 'with ' + pairs.map(([k, v]) => `${k}=${v}`).join(' ');
}
