// ============================================================================
// AssistantResearchCard — the assembled assistant response
// ----------------------------------------------------------------------------
// Composes the seven zones described in the visual brief, in order:
//
//   1. RoutingStrip       — TEMPLATE/PRIMITIVES badge + as-of timestamp
//   2. ProseAnswer        — the LLM's text answer with inline data tokens
//   3. ToolTrace          — collapsed-by-default tool execution list
//   4. ResultCanvas       — the chart/table for the terminal artifact
//   5. DagStrip           — the executed graph (collapsed pill chain →
//                            expanded node graph)
//   6. ProvenanceRow      — convention dots + lineage hash + duration
//   7. ActionRow          — Open in Build · Save · Export · Rerun with…
//
// Followed (OUTSIDE the card chrome) by:
//   - Error state (if errored)
//   - FollowUps           — three suggested next prompts
//
// Edge cases the card handles gracefully:
//   - Streaming with no content yet → ThinkingState
//   - Errored workflow → red routing strip + error block, no canvas
//   - No tool trace AND no prose (early stream) → ThinkingState
// ============================================================================

import { AlertCircle } from 'lucide-react';
import type { CopilotMessage } from '@/types/copilot';
import { RoutingStrip } from './RoutingStrip';
import { ProseAnswer } from './ProseAnswer';
import { ResultCanvas } from './ResultCanvas';
import { DagStrip } from './DagStrip';
import { ProvenanceRow } from './ProvenanceRow';
import { ActionRow } from './ActionRow';
import { FollowUps } from './FollowUps';
import { ToolTrace } from './ToolTrace';
import { ThinkingState } from './ThinkingState';

type Props = {
  message: CopilotMessage;
  /** Drops a prompt into the composer.  Used by FollowUps + Rerun. */
  onSeedComposer: (prompt: string) => void;
  /** When the user clicks "Rerun with…", we look up the user prompt
   *  that paired with this assistant turn and seed the composer with
   *  it. */
  pairedUserPrompt?: string;
};

export function AssistantResearchCard({
  message,
  onSeedComposer,
  pairedUserPrompt,
}: Props) {
  const hasContent = message.content.length > 0;
  const hasTrace = message.traceSteps.length > 0;
  const hasWorkflow = !!message.workflow;
  const isErrored =
    message.phase === 'error' ||
    message.workflow?.result?.ok === false ||
    message.workflow?.status === 'error';

  const showThinkingState =
    !hasContent && !hasTrace && !hasWorkflow && !isErrored;

  // Route-keyed gradient top-rule — surfaces the route at peripheral
  // glance without needing a full-width divider.  Ice for templates,
  // violet for ad-hoc composed DAGs, neutral lineage tone otherwise,
  // coral when errored.
  const railColor = isErrored
    ? 'rgba(255, 107, 126, 0.55)'
    : message.workflow?.routeDecision.template_id
      ? 'rgba(122, 162, 255, 0.55)'
      : message.traceSteps.length > 0
        ? 'rgba(155, 140, 255, 0.45)'
        : 'rgba(155, 140, 255, 0.30)';

  return (
    <div className="space-y-3">
      <article
        className="research-card overflow-hidden"
        style={{ ['--rail-color' as string]: railColor }}
      >
        <span aria-hidden className="research-card-rail" />
        <RoutingStrip message={message} />

        {showThinkingState && (
          <>
            <Divider />
            <ThinkingState />
          </>
        )}

        {/* Prose answer */}
        {hasContent && (
          <>
            <Divider />
            <ProseAnswer text={message.content} isStreaming={message.isStreaming} />
          </>
        )}

        {/* Tool trace (collapsed-by-default once done) */}
        {hasTrace && (
          <>
            {hasContent && <Divider />}
            <ToolTrace steps={message.traceSteps} phase={message.phase} />
          </>
        )}

        {/* Workflow / artifact result canvas */}
        {hasWorkflow && message.workflow?.result?.ok && (
          <ResultCanvas message={message} />
        )}

        {/* Errored workflow block */}
        {isErrored && (
          <div className="border-t border-line-subtle px-5 py-3">
            <div className="flex items-start gap-2.5 rounded-md border border-coral-400/25 bg-coral-400/[0.05] px-3 py-2.5">
              <AlertCircle
                size={13}
                className="mt-px shrink-0 text-coral-400"
              />
              <div className="min-w-0">
                <p className="kicker text-coral-300">EXECUTION FAILED</p>
                <p className="mt-1 text-[12px] leading-[1.55] text-coral-300/85">
                  {message.workflow?.result?.error ??
                    "The orchestrator hit an error before producing a result."}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* DAG strip — show when we have a workflow lineage OR a tool trace */}
        {(hasWorkflow || hasTrace) && !showThinkingState && (
          <DagStrip message={message} />
        )}

        {/* Provenance row */}
        {!showThinkingState && <ProvenanceRow message={message} />}

        {/* Actions */}
        {!message.isStreaming && (
          <ActionRow
            message={message}
            onSeedComposer={() => {
              if (pairedUserPrompt) onSeedComposer(pairedUserPrompt);
            }}
          />
        )}
      </article>

      {/* Suggested follow-ups — outside the card chrome */}
      {!message.isStreaming && (
        <FollowUps message={message} onSelect={onSeedComposer} />
      )}
    </div>
  );
}

function Divider() {
  return <div className="research-card-divider" />;
}
