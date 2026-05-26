// ============================================================================
// calculate_cpi_surprise_tool/surfaces/AskCard.tsx
// ----------------------------------------------------------------------------
// Stage 5 reference implementation.  Per-tool assistant-card override
// for CPI Surprise.  ``ConversationCanvas.resolveAssistantCard``
// reaches this component via ``MODULE.surfaces.ask`` when the
// assistant turn's terminal tool is ``calculate_cpi_surprise_tool``.
//
// The minimal-but-real version below renders the assistant's prose
// answer inside a compact CPI-surprise-themed card with a clear
// kicker.  A follow-up PR can expand it to display the release date,
// actual vs consensus, surprise in bps, rolling-z, and a sparkline
// — once the typed-detail endpoint surfaces are wired up.
// ============================================================================

import type { CopilotMessage } from '@/types/copilot';

type Props = {
  message: CopilotMessage;
  pairedUserPrompt?: string;
  onSeedComposer: (prompt: string) => void;
};

function CpiSurpriseAskCard({ message }: Props) {
  return (
    <article className="research-card overflow-hidden">
      <span aria-hidden className="research-card-rail" />
      <div className="border-b border-line-subtle px-5 py-2">
        <span className="kicker text-fg-muted">CPI SURPRISE · per-release signal</span>
      </div>
      <div className="px-5 py-4">
        <p className="text-[13px] leading-[1.55] text-fg-primary whitespace-pre-wrap">
          {message.content}
        </p>
      </div>
      <div className="border-t border-line-subtle px-5 py-2 text-[10.5px] text-fg-faint">
        Bespoke per-tool assistant card · Stage 5 reference (module-first
        dispatch resolved this component from
        <code className="ml-1 font-mono text-fg-muted">
          MODULE.surfaces.ask
        </code>
        ).
      </div>
    </article>
  );
}

export default CpiSurpriseAskCard;
