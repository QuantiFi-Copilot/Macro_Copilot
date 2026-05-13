// ============================================================================
// BuildResultStalled — the canvas when a Build prompt didn't materialise a
// workspace.
// ----------------------------------------------------------------------------
// PR D — the empty-state composer in Build pipes the user's prompt
// through the shared chat WebSocket.  The backend has two paths:
//
//   - Workflow path  — router matched a template, runner executed it,
//                      runner persisted a workspace, ``workspace.slug``
//                      flows back on the wire.  BuildShell navigates
//                      to ``/workspace/:slug``.
//
//   - Supervisor path — supervisor ran ad-hoc tool calls (or the
//                       router rejected the prompt as clarify /
//                       out_of_scope, or persistence failed).  No
//                       workspace is created; no slug arrives.
//
// Before PR D, the second path silently dropped the user back to the
// empty state with no explanation — they saw "let me do it" → spinner
// → empty state, indistinguishable from a no-op.  PR D adds this
// component so the user gets a clear "your prompt was answered but it
// didn't materialise as a saved workspace" surface, with the assistant
// response visible and a retry affordance.
//
// Composition (top → bottom):
//   1. Header banner — "Answer ready · no workspace created" with the
//      reason chip pulled from the message.
//   2. User prompt echo (mirrors BuildBuilding).
//   3. Assistant response card — content + any tool calls collapsed.
//   4. Footer actions — "Refine and retry" (re-seeds composer),
//      "Open in Ask" (jumps to the thread), "New prompt" (clears).
// ============================================================================

import { AlertCircle, ArrowUpRight, RotateCcw, Wand2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { CopilotMessage } from '@/types/copilot';

type Props = {
  /** The user's original prompt. */
  prompt: string;
  /** The assistant message that came back without a workspace.slug. */
  message: CopilotMessage;
  /** Called when the user clicks "Refine and retry" — parent should
   *  re-seed the composer with the original prompt. */
  onRefine: () => void;
  /** Called when the user clicks "New prompt" — parent should clear
   *  state and drop back to the empty state. */
  onDismiss: () => void;
};

export function BuildResultStalled({
  prompt,
  message,
  onRefine,
  onDismiss,
}: Props) {
  const reason = classifyStallReason(message);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 flex-col gap-1 border-b border-line-subtle px-6 py-4">
        <div className="flex items-center gap-2 text-[10.5px] uppercase tracking-[0.16em] text-fg-faint">
          <AlertCircle size={11} className="text-amber-300" />
          <span>Answer ready · no workspace created</span>
        </div>
        <h1 className="font-serif-display text-[22px] font-light leading-tight tracking-[-0.012em] text-fg-primary">
          {reason.title}
        </h1>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[860px] flex-col gap-6 px-6 py-8">
          {/* Reason / explanation card */}
          <section className="flex flex-col gap-2">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
              What happened
            </p>
            <div className="research-card relative px-5 py-4">
              <span
                aria-hidden
                className="research-card-rail"
                style={{ ['--rail-color' as string]: 'rgba(251,191,36,0.55)' }}
              />
              <p className="text-[12.5px] leading-[1.6] text-fg-secondary">
                {reason.explanation}
              </p>
            </div>
          </section>

          {/* Echo the user's prompt for continuity */}
          <section className="flex flex-col gap-2">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
              Your prompt
            </p>
            <div className="research-card relative px-5 py-4">
              <span aria-hidden className="research-card-rail" />
              <p className="text-[13px] leading-[1.55] text-fg-primary">
                {prompt}
              </p>
            </div>
          </section>

          {/* Assistant response if any prose came back */}
          {message.content.trim().length > 0 && (
            <section className="flex flex-col gap-2">
              <p className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                Assistant response
              </p>
              <div className="research-card relative px-5 py-4">
                <span aria-hidden className="research-card-rail" />
                <p className="whitespace-pre-wrap text-[12.5px] leading-[1.6] text-fg-secondary">
                  {message.content}
                </p>
              </div>
            </section>
          )}

          {/* Tool calls (if any) so the user sees what actually ran */}
          {message.traceSteps.length > 0 && (
            <section className="flex flex-col gap-2">
              <p className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                Tools used ({message.traceSteps.length})
              </p>
              <div className="research-card relative flex flex-col gap-2 px-5 py-4">
                <span aria-hidden className="research-card-rail" />
                {message.traceSteps.map((step) => (
                  <div
                    key={step.id}
                    className="flex items-center justify-between gap-3 text-[11.5px]"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span
                        aria-hidden
                        className={
                          step.status === 'error'
                            ? 'h-1.5 w-1.5 rounded-full bg-rose-400'
                            : step.status === 'running'
                              ? 'h-1.5 w-1.5 rounded-full bg-amber-400'
                              : 'h-1.5 w-1.5 rounded-full bg-emerald-400'
                        }
                      />
                      <span className="font-mono text-fg-secondary">
                        {step.tool}
                      </span>
                      {step.error && (
                        <span className="truncate text-rose-300">
                          — {step.error}
                        </span>
                      )}
                    </div>
                    {step.durationMs != null && (
                      <span className="shrink-0 font-mono text-fg-faint">
                        {step.durationMs}ms
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Action footer */}
          <section className="flex flex-wrap gap-2 pt-2">
            <button
              type="button"
              onClick={onRefine}
              className="flex items-center gap-1.5 rounded-md border border-ice-400/35 bg-ice-500/[0.06] px-3 py-1.5 text-[11.5px] font-medium text-ice-100 hover:border-ice-400/55 hover:bg-ice-500/[0.10]"
            >
              <Wand2 size={12} />
              <span>Refine and retry</span>
            </button>
            <Link
              to="/ask"
              className="flex items-center gap-1.5 rounded-md border border-line-soft bg-white/[0.012] px-3 py-1.5 text-[11.5px] font-medium text-fg-secondary hover:border-line-strong hover:bg-white/[0.025]"
            >
              <ArrowUpRight size={12} />
              <span>Continue in Ask</span>
            </Link>
            <button
              type="button"
              onClick={onDismiss}
              className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[11.5px] font-medium text-fg-muted hover:text-fg-secondary"
            >
              <RotateCcw size={12} />
              <span>New prompt</span>
            </button>
          </section>
        </div>
      </div>
    </div>
  );
}

interface StallReason {
  title: string;
  explanation: string;
}

function classifyStallReason(message: CopilotMessage): StallReason {
  // 1. Workflow path was taken but persistence failed (workspace null).
  if (message.workflow && message.workflow.result?.ok === false) {
    return {
      title: 'Workflow returned an error',
      explanation:
        message.workflow.result.error ??
        'The workflow ran but the runner reported an error. ' +
          'See the response below and refine the prompt before retrying.',
    };
  }
  if (message.workflow && !message.workflow.workspace) {
    return {
      title: 'Workflow ran but was not saved',
      explanation:
        'The workflow completed but the substrate did not persist a ' +
        'workspace for it (typically a missing object-storage or ' +
        'Postgres connection in the API server).  The result is ' +
        'visible below; restart the API server to enable persistence ' +
        'and rerun the prompt to materialise a slug-routed workspace.',
    };
  }
  // 2. Supervisor path — the router didn't match a workflow template.
  if (message.workspaceContext) {
    return {
      title: 'Answered with ad-hoc tools',
      explanation:
        'Your prompt routed to the supervisor (ad-hoc tool calls) ' +
        'rather than a saved workflow template, so no workspace was ' +
        'created.  Try a prompt that matches one of the workflow ' +
        'archetypes (event study, screen, compare regimes) to ' +
        'materialise a workspace — or continue the conversation in Ask.',
    };
  }
  // 3. Default — assistant responded but with no actionable bits.
  return {
    title: 'Answered without a workspace',
    explanation:
      'The assistant returned a response but did not run a workflow ' +
      'or surface tool calls.  Try a more specific prompt — for ' +
      'example "Run an event study on UST 2s10s widening above 1.5σ ' +
      'over the last 5 years."',
  };
}
