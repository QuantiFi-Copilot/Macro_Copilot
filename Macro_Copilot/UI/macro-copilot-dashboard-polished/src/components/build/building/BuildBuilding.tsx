// ============================================================================
// BuildBuilding — the Build canvas while a workflow is mid-execute.
// ----------------------------------------------------------------------------
// Rendered after the user sends a prompt from the empty-state
// composer and before the workflow result lands.  PR A keeps this
// simple: an animated banner + a status line + the original prompt
// echoed back so the user has continuity.  PR B will replace the
// banner with a stage-by-stage progress trace (one chip per executed
// node) sourced from the workflow_status / workflow_result events.
//
// When the runner emits ``workflow_result`` with a persisted
// ``workspace.slug``, ``BuildShell`` navigates to ``/workspace/:slug``
// — at which point this component unmounts and ``BuildCompleted``
// takes over.
// ============================================================================

import { Loader2 } from 'lucide-react';

type Props = {
  /** The prompt the user just sent; echoed back so the canvas
   *  doesn't feel empty during the wait. */
  prompt: string;
  /** Latest status string from the workflow_status event.  When
   *  null we show a generic "Running…" — PR A doesn't yet wire
   *  the per-node telemetry. */
  status?: string | null;
};

export function BuildBuilding({ prompt, status }: Props) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 flex-col gap-1 border-b border-line-subtle px-6 py-4">
        <div className="flex items-center gap-2 text-[10.5px] uppercase tracking-[0.16em] text-fg-faint">
          <Loader2 size={11} className="animate-spin text-ice-300" />
          <span>Running</span>
        </div>
        <h1 className="font-serif-display text-[22px] font-light leading-tight tracking-[-0.012em] text-fg-primary">
          Building your analysis…
        </h1>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[860px] flex-col gap-6 px-6 py-8">
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

          <section className="flex flex-col gap-2">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
              Status
            </p>
            <div className="research-card relative flex items-center gap-3 px-5 py-4">
              <span aria-hidden className="research-card-rail" />
              <Loader2 size={14} className="shrink-0 animate-spin text-ice-300" />
              <p className="text-[12.5px] leading-[1.55] text-fg-secondary">
                {status ?? (
                  <>
                    Routing your prompt through the workflow registry,
                    running the matched workflow against TimescaleDB,
                    and persisting the result as a slug-stable
                    workspace.  Streaming updates from the orchestrator
                    will replace this card the moment a node completes.
                  </>
                )}
              </p>
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
