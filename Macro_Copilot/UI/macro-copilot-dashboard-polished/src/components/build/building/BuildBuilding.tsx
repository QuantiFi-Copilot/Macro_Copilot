// ============================================================================
// BuildBuilding — the Build canvas while a workflow is mid-execute.
// ----------------------------------------------------------------------------
// Rendered after the user sends a prompt from the empty-state
// composer and before the workflow result lands.  PR D upgrades the
// previous opaque-spinner treatment to a live progress strip:
//
//   1. Header banner — "Running" + the running phase label.
//   2. The user's prompt echoed back (continuity).
//   3. The workflow route decision (template + a one-line rationale)
//      so the user can confirm the right archetype was matched while
//      the run is mid-flight.
//   4. A live per-tool progress strip — one row per traceStep, status
//      dot keyed to ``step.status`` (running → amber, complete →
//      emerald, error → rose), with the tool name + duration.  This
//      mirrors what Ask shows in its assistant bubble's trace.
//   5. A small "what happens next" footer so the user knows what to
//      expect when the run completes.
//
// When the runner emits ``workflow_result`` with a persisted
// ``workspace.slug``, ``BuildShell`` navigates to ``/workspace/:slug``
// — at which point this component unmounts and ``BuildCompleted``
// takes over.  When it lands without a slug, the parent renders
// ``BuildResultStalled`` instead.
// ============================================================================

import { Loader2 } from 'lucide-react';
import type { CopilotMessage } from '@/types/copilot';

type Props = {
  /** The prompt the user just sent; echoed back so the canvas
   *  doesn't feel empty during the wait. */
  prompt: string;
  /** The streaming assistant message, if any.  When present we
   *  render its workflow route decision + per-tool trace chips so
   *  the canvas shows real progress.  Null until the first server
   *  event lands. */
  message?: CopilotMessage | null;
};

const PHASE_LABEL: Record<string, string> = {
  thinking: 'Thinking',
  running_tools: 'Running tools',
  synthesising: 'Synthesising response',
  done: 'Finalising',
  error: 'Error',
};

export function BuildBuilding({ prompt, message }: Props) {
  const phase = message?.phase ?? 'thinking';
  const phaseLabel = PHASE_LABEL[phase] ?? 'Running';
  const route = message?.workflow?.routeDecision ?? null;
  const traceSteps = message?.traceSteps ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 flex-col gap-1 border-b border-line-subtle px-6 py-4">
        <div className="flex items-center gap-2 text-[10.5px] uppercase tracking-[0.16em] text-fg-faint">
          <Loader2 size={11} className="animate-spin text-ice-300" />
          <span>Running · {phaseLabel}</span>
        </div>
        <h1 className="font-serif-display text-[22px] font-light leading-tight tracking-[-0.012em] text-fg-primary">
          Building your analysis…
        </h1>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[860px] flex-col gap-6 px-6 py-8">
          {/* Your prompt */}
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

          {/* Route decision (when the workflow router has resolved) */}
          {route && (
            <section className="flex flex-col gap-2">
              <p className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
                Workflow matched
              </p>
              <div
                className="research-card relative px-5 py-4"
                style={{ ['--rail-color' as string]: 'rgba(196,181,253,0.65)' }}
              >
                <span aria-hidden className="research-card-rail" />
                <div className="flex items-baseline gap-2">
                  <span className="text-[10.5px] uppercase tracking-[0.16em] text-fg-faint">
                    Template
                  </span>
                  <span className="font-mono text-[12.5px] text-fg-primary">
                    {route.template_id}
                  </span>
                </div>
                {route.rationale && (
                  <p className="mt-2 text-[11.5px] leading-[1.55] text-fg-secondary">
                    {route.rationale}
                  </p>
                )}
                {Object.keys(route.slot_values ?? {}).length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[10.5px]">
                    {Object.entries(route.slot_values).map(([k, v]) => (
                      <span key={k} className="flex items-baseline gap-1.5">
                        <span className="font-medium uppercase tracking-[0.14em] text-fg-faint">
                          {k}
                        </span>
                        <span className="font-mono text-fg-secondary">
                          {formatSlotValue(v)}
                        </span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Live per-tool trace */}
          <section className="flex flex-col gap-2">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
              Execution trace
              {traceSteps.length > 0 && (
                <span className="ml-2 text-fg-faint/70">
                  ({traceSteps.length})
                </span>
              )}
            </p>
            <div className="research-card relative flex flex-col gap-2 px-5 py-4">
              <span aria-hidden className="research-card-rail" />
              {traceSteps.length === 0 ? (
                <div className="flex items-center gap-3 text-[12px] text-fg-secondary">
                  <Loader2
                    size={14}
                    className="shrink-0 animate-spin text-ice-300"
                  />
                  <span>
                    Routing your prompt through the workflow registry…
                  </span>
                </div>
              ) : (
                traceSteps.map((step) => (
                  <div
                    key={step.id}
                    className="flex items-center justify-between gap-3 text-[11.5px]"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <StatusDot status={step.status} />
                      <span className="truncate font-mono text-fg-primary">
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
                ))
              )}
            </div>
          </section>

          <section className="flex flex-col gap-2 opacity-60">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
              When the run completes
            </p>
            <ul className="flex flex-col gap-1.5 text-[11.5px] leading-[1.55] text-fg-muted">
              <li>· The Build canvas reloads at the new workspace URL.</li>
              <li>· The DAG view shows every primitive and operator used.</li>
              <li>· The Results view renders each artifact as its own card.</li>
              <li>· The sidebar's "Recent" list updates with the new entry.</li>
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: 'running' | 'complete' | 'error' }) {
  const cls =
    status === 'error'
      ? 'bg-rose-400'
      : status === 'running'
        ? 'bg-amber-400 animate-pulse'
        : 'bg-emerald-400';
  return (
    <span
      aria-hidden
      className={`h-1.5 w-1.5 shrink-0 rounded-full ${cls}`}
    />
  );
}

function formatSlotValue(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'string' || typeof v === 'number') return String(v);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (Array.isArray(v)) return `[${v.length}]`;
  if (typeof v === 'object') return `{…}`;
  return String(v);
}
