// ============================================================================
// WorkflowsCataloguePage  (route: /workflows)
// ----------------------------------------------------------------------------
// Discoverable grid of every workflow template registered with the substrate.
// Click a tile to open the 4-tab InspectionPanel.  A "Run via copilot"
// button on each tile drops a starter prompt into the chat drawer (the same
// path the LLM router takes when a free-form prompt fits the template).
// ============================================================================

import { useSearchParams } from 'react-router-dom';
import { Workflow, AlertCircle, Sparkles } from 'lucide-react';
import { useWorkflows, useWorkflow } from '@/hooks/useWorkflows';
import { InspectionPanel } from './InspectionPanel';
import type { WorkflowTemplateCard } from '@/types/workflows';

// ---------------------------------------------------------------------------
// Per-template canonical "try-me" prompt — the same desk question each
// template was designed to answer.  Clicking the suggestion focuses the chat
// composer (via the existing copilot:focus-input event) and pre-fills it.
// ---------------------------------------------------------------------------
const TEMPLATE_STARTER_PROMPTS: Record<string, string> = {
  event_study:
    "Over the last 5 years, when the 2Y OIS-Treasury spread widens by more than 1.5σ in a single day, what's the average 5-day forward move in the 10Y UST yield, and how does it compare to the unconditional 5-day move?",
  regime_conditioned_relationship:
    "Estimate the rolling beta of the 10Y UST yield change to the 2Y OIS rate change, and report how that beta differs in steepening vs flattening regimes of the 2s10s curve over the last 3 years.",
};

export function WorkflowsCataloguePage() {
  const { data, isLoading, error } = useWorkflows();
  const [searchParams, setSearchParams] = useSearchParams();
  const selected = searchParams.get('template');
  const { data: cardDetail } = useWorkflow(selected);

  const closePanel = () => {
    const next = new URLSearchParams(searchParams);
    next.delete('template');
    setSearchParams(next, { replace: true });
  };

  const openTemplate = (templateId: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('template', templateId);
    setSearchParams(next, { replace: false });
  };

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-[1080px]">
        <PageHeader totalCount={data?.length ?? 0} />

        {error && (
          <div className="card mt-4 flex items-start gap-3 px-4 py-3 text-coral-300">
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <div className="text-[12px]">
              <div className="font-semibold">
                Failed to load workflows catalogue
              </div>
              <div className="mt-1 text-fg-secondary">{error.message}</div>
            </div>
          </div>
        )}

        {isLoading && (
          <div className="card mt-6 flex h-[280px] items-center justify-center text-[12px] text-fg-muted">
            Loading workflows catalogue…
          </div>
        )}

        {!isLoading && !error && data && (
          <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
            {data.map((wf) => (
              <WorkflowTile
                key={wf.template_id}
                workflow={wf}
                onInspect={() => openTemplate(wf.template_id)}
              />
            ))}
          </div>
        )}

        <ArchitectureCallout />
      </div>

      <InspectionPanel
        open={!!selected}
        onClose={closePanel}
        workflow={cardDetail}
      />
    </div>
  );
}

// ----------------------------------------------------------------------------

function PageHeader({ totalCount }: { totalCount: number }) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div>
        <p className="kicker text-fg-muted">Library</p>
        <h1 className="mt-1 text-[20px] font-semibold tracking-[-0.01em] text-fg-primary">
          Workflows
        </h1>
        <p className="mt-1.5 max-w-[640px] text-[12.5px] leading-[1.55] text-fg-secondary">
          Topology-locked DAG-shaped analyses.  Each workflow composes
          finance-blind operators over the rates primitives — same numbers
          every time, full lineage walk back to source.  Ask the copilot a
          workflow-shaped question, or click "Inspect" to see the slot
          schema, archetype cues, and operator chain.
        </p>
      </div>
      <div className="text-right text-[10.5px] uppercase tracking-[0.12em] text-fg-faint">
        <div>{totalCount}</div>
        <div className="text-fg-muted">templates</div>
      </div>
    </div>
  );
}

function WorkflowTile({
  workflow,
  onInspect,
}: {
  workflow: WorkflowTemplateCard;
  onInspect: () => void;
}) {
  const tryMePrompt = TEMPLATE_STARTER_PROMPTS[workflow.template_id];

  const sendToChat = () => {
    // Synchronous: write to the input via a custom event, then focus.
    // Mirrors the existing copilot:focus-input pattern in ChatDrawer.tsx.
    window.dispatchEvent(
      new CustomEvent('copilot:set-input', { detail: tryMePrompt }),
    );
    window.dispatchEvent(new Event('copilot:focus-input'));
  };

  return (
    <article className="card flex flex-col gap-4 px-5 py-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-line-soft bg-gradient-to-br from-ice-500/20 to-ice-700/20 text-ice-300">
            <Workflow size={15} />
          </div>
          <div className="min-w-0">
            <p className="kicker text-fg-muted">
              ARCHETYPE · {workflow.archetype}
            </p>
            <h3 className="mt-0.5 truncate text-[14px] font-semibold tracking-[-0.005em] text-fg-primary">
              {workflow.template_id}
            </h3>
          </div>
        </div>
        <span className="mono shrink-0 rounded-md border border-line-soft bg-white/[0.02] px-2 py-[2px] text-[10px] text-fg-muted">
          {workflow.node_count}n · {workflow.edge_count}e
        </span>
      </div>

      <p className="line-clamp-3 text-[12px] leading-[1.55] text-fg-secondary">
        {workflow.description}
      </p>

      <div className="flex flex-wrap gap-1.5">
        {workflow.operators_used.map((op) => (
          <span
            key={op}
            className="mono rounded-md border border-line-soft bg-white/[0.014] px-1.5 py-[2px] text-[10px] text-ice-200"
          >
            {op}
          </span>
        ))}
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-line-subtle pt-4">
        <div className="text-[10.5px] uppercase tracking-[0.12em] text-fg-faint">
          terminal · {workflow.terminal_artifact_type}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onInspect}
            className="rounded-md border border-line-soft bg-white/[0.02] px-3 py-1.5 text-[11.5px] font-medium text-fg-secondary transition-colors hover:border-line-strong hover:bg-white/[0.05] hover:text-fg-primary"
          >
            Inspect
          </button>
          {tryMePrompt && (
            <button
              type="button"
              onClick={sendToChat}
              className="flex items-center gap-1.5 rounded-md border border-ice-400/30 bg-gradient-to-b from-ice-500/15 to-ice-700/15 px-3 py-1.5 text-[11.5px] font-semibold text-ice-100 transition-colors hover:border-ice-400/50 hover:from-ice-500/25 hover:to-ice-700/25"
            >
              <Sparkles size={12} />
              Try via copilot
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

function ArchitectureCallout() {
  return (
    <div className="card mt-8 flex items-start gap-3 px-5 py-4">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line-soft bg-white/[0.02] text-ice-300">
        <Sparkles size={13} />
      </div>
      <div>
        <p className="text-[12.5px] font-semibold text-fg-primary">
          Why workflows
        </p>
        <p className="mt-1 text-[11.5px] leading-[1.55] text-fg-secondary">
          A primitive answers one question (e.g.{' '}
          <span className="mono text-ice-300">calculate_curve_spread_tool</span>{' '}
          returns one spread series).  A workflow composes many primitives
          into a DAG that answers a shape — "event study around X moves" or
          "regime-conditioned beta of A on B."  Same archetype shape, any
          binding: ask about UST 2s10s, BUND, JGB — the workflow runs
          unchanged.
        </p>
      </div>
    </div>
  );
}
