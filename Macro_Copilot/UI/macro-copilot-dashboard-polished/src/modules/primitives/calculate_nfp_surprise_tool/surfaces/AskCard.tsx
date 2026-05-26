// ============================================================================
// calculate_nfp_surprise_tool/surfaces/AskCard.tsx
// ----------------------------------------------------------------------------
// Stage 6 — bespoke assistant card for NFP Surprise.  Mirrors the
// CPI Surprise card (Stage 5) with NFP-specific framing.  Resolved
// by ``ConversationCanvas.resolveAssistantCard`` when an assistant
// turn's terminal tool is ``calculate_nfp_surprise_tool``.
// ============================================================================

import type { CopilotMessage } from '@/types/copilot';

type Props = {
  message: CopilotMessage;
  pairedUserPrompt?: string;
  onSeedComposer: (prompt: string) => void;
};

function NfpSurpriseAskCard({ message }: Props) {
  return (
    <article className="research-card overflow-hidden">
      <span aria-hidden className="research-card-rail" />
      <div className="border-b border-line-subtle px-5 py-2">
        <span className="kicker text-fg-muted">
          NFP SURPRISE · monthly release signal
        </span>
      </div>
      <div className="px-5 py-4">
        <p className="text-[13px] leading-[1.55] text-fg-primary whitespace-pre-wrap">
          {message.content}
        </p>
      </div>
      <div className="border-t border-line-subtle px-5 py-2 text-[10.5px] text-fg-faint">
        Bespoke per-tool assistant card · Stage 6 reference (module-first
        dispatch resolved this component from
        <code className="ml-1 font-mono text-fg-muted">
          MODULE.surfaces.ask
        </code>
        ).
      </div>
    </article>
  );
}

export default NfpSurpriseAskCard;
