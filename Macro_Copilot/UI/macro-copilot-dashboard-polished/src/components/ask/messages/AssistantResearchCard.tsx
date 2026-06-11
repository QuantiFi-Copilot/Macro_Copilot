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
import type { CopilotMessage, TraceStep } from '@/types/copilot';
import { RoutingStrip } from './RoutingStrip';
import { ProseAnswer } from './ProseAnswer';
import { ResultCanvas } from './ResultCanvas';
import { DagStrip } from './DagStrip';
import { ProvenanceRow } from './ProvenanceRow';
import { ActionRow } from './ActionRow';
import { FollowUps } from './FollowUps';
import { ToolTrace } from './ToolTrace';
import { ThinkingState } from './ThinkingState';

// Consolidation target #3 — open-DAG statuses that are HONEST halts
// (the gate / composer / assembler / router refused or asked to
// clarify, and the streamed prose carries that message verbatim).
// These must NOT render the coral "EXECUTION FAILED" panel — that
// panel is reserved for genuine pipeline errors.  Closed set mirrors
// the backend's PipelineStatus family.
const HONEST_HALT_STATUSES = new Set([
  'GATE_REFUSE',
  'GATE_CLARIFY',
  'COMPOSER_REFUSE',
  'ASSEMBLY_REFUSE',
  'ROUTER_CLARIFY',
]);

/** Consolidation target #3 — synthesize step chips for the open-DAG
 *  lane from the executor's ``workflow_lineage_summary`` string
 *  (``"workflow <id>: leaf_a → align → correlation"``).  The open-DAG
 *  lane emits no per-tool ``tool_call`` events (the substrate executes
 *  the whole DAG in one call), so the ToolTrace zone would otherwise
 *  sit empty while the direct lane shows its chip strip — the two
 *  lanes must read identically.  Display-only: each chip is a
 *  completed node in topological execution order. */
function synthesizeTraceFromLineage(
  summary: string | undefined,
): TraceStep[] {
  if (!summary) return [];
  const colon = summary.indexOf(':');
  const path = colon >= 0 ? summary.slice(colon + 1) : summary;
  return path
    .split(/→|->/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map((nodeId, i) => ({
      id: `lineage-${i}-${nodeId}`,
      tool: nodeId,
      label: nodeId,
      status: 'complete' as const,
      startedAt: 0,
    }));
}

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
  const hasWorkflow = !!message.workflow;

  // Consolidation target #3 — an honest open-DAG refusal /
  // clarification is NOT an execution failure: the prose already
  // carries the gate's message, so the card renders it like any
  // other answer (no coral panel, no error rail).
  const isHonestHalt =
    message.openDagStatus != null &&
    HONEST_HALT_STATUSES.has(message.openDagStatus);

  const isErrored =
    !isHonestHalt &&
    (message.phase === 'error' ||
      message.workflow?.result?.ok === false ||
      message.workflow?.status === 'error');

  // Open-DAG lane: no tool_call events stream (the substrate executes
  // the DAG in one call) — synthesize the step-chip strip from the
  // executor's topological lineage summary so both lanes show the
  // same trace zone.
  const traceSteps =
    message.traceSteps.length > 0
      ? message.traceSteps
      : synthesizeTraceFromLineage(
          message.workflow?.result?.workflow_lineage_summary,
        );
  const hasTrace = traceSteps.length > 0;

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
      : hasTrace
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

        {/* Tool trace (collapsed-by-default once done).  For the
            open-DAG lane the steps are synthesized from the lineage
            summary — same chip strip either way (one Ask card). */}
        {hasTrace && (
          <>
            {hasContent && <Divider />}
            <ToolTrace steps={traceSteps} phase={message.phase} />
          </>
        )}

        {/* Workflow / artifact result canvas */}
        {hasWorkflow && message.workflow?.result?.ok && (
          <ResultCanvas message={message} />
        )}

        {/* Errored workflow block.  De-duplicated (consolidation
            target #3): when the streamed prose already explains the
            failure (open-DAG turns stream the structured failure
            message as content), the panel is a slim banner pointing
            at the prose instead of repeating the same text. */}
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
                  {hasContent && message.openDagStatus != null
                    ? 'The pipeline halted with an error — details above.'
                    : message.workflow?.result?.error ??
                      'The orchestrator hit an error before producing a result.'}
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
