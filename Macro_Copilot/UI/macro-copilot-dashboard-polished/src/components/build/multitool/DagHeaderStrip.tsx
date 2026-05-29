// ============================================================================
// DagHeaderStrip.tsx — Query-root header for the multi-tool DAG (Stage D).
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §10 the DAG infra owns
// the query-root header.  It surfaces:
//   - the originating prompt (when the supervisor recorded one) — else a
//     generic "From your Ask answer" title;
//   - the DAG summary ("3 tool calls · 1 domain · parallel");
//   - a "Stitched by AI" disclosure chip — multi-tool queries are LLM-stitched
//     ad-hoc plans, and the user should always see that the topology was
//     assembled by the model (shared_mockups/README.md open question R3).
//
// Fully generic — no tool-specific copy.
// ============================================================================

import { Boxes, Sparkles } from 'lucide-react';
import type { DagModel } from './dagModel';

export function DagHeaderStrip({ model }: { model: DagModel }) {
  const hasPrompt = typeof model.prompt === 'string' && model.prompt.length > 0;
  const title = hasPrompt ? (model.prompt as string) : 'From your Ask answer';

  return (
    <header className="flex shrink-0 flex-col gap-1.5 border-b border-line-subtle px-6 pt-5 pb-4">
      <div className="flex items-center gap-2">
        <Boxes size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
        <span className="kicker text-fg-muted">MULTI-TOOL COMPARISON</span>
        <span
          className="inline-flex items-center gap-1 rounded-full border border-line-soft bg-white/[0.03] px-2 py-[1px] text-[9.5px] font-medium uppercase tracking-[0.08em] text-fg-muted"
          title="Multi-tool plans are stitched together by the assistant at query time from individual tool calls."
        >
          <Sparkles size={9} strokeWidth={2} aria-hidden />
          Stitched by AI
        </span>
      </div>
      <h1
        className={
          hasPrompt
            ? 'text-[18px] font-medium leading-snug tracking-[-0.01em] text-fg-primary'
            : 'text-[20px] font-medium tracking-[-0.012em] text-fg-primary'
        }
      >
        {hasPrompt ? `“${title}”` : title}
      </h1>
      <p className="text-[12px] leading-[1.5] text-fg-secondary">
        {model.summary}. Each card pulls live data from its typed-detail
        endpoint; click the expand arrow on a card to open the full view with
        editable parameters.
      </p>
    </header>
  );
}
