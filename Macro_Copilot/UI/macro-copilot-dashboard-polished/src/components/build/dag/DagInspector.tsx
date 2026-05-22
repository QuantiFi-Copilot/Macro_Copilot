// ============================================================================
// DagInspector — compact node-detail panel for the DAG view.
// ----------------------------------------------------------------------------
// PR6 — surfaces the selected node's identity, params, inputs,
// outputs, and artifact reference without leaving the DAG tab.
// Sits BELOW the canvas so the user can pick a node, scan its
// detail, then move on without losing graph context.
//
// Intentionally compact — this is NOT a duplicate of the Results
// tab.  We surface enough metadata to confirm the user is looking
// at the right node + its inputs/outputs; deep payload inspection
// stays on the Results tab.
//
// Empty state (no selection) shows a brief help caption so the
// user knows what clicking a node does.
// ============================================================================

import { X } from 'lucide-react';
import type {
  DagModel,
  DagModelEdge,
  DagModelNode,
} from './lib/buildDagModel';
import { stageParamRows } from '@/components/build/lib/stageDisplay';
import { prettyStageTitle } from '@/components/build/lib/stageDisplay';

type Props = {
  modelNode: DagModelNode | null;
  model: DagModel;
  onClose: () => void;
};

export function DagInspector({ modelNode, model, onClose }: Props) {
  if (!modelNode) {
    return (
      <div className="shrink-0 border-t border-line-subtle px-6 py-3 text-[10.5px] text-fg-faint">
        Click a stage card to inspect its parameters, inputs, and outputs.
      </div>
    );
  }

  const node = modelNode.node;
  const title = prettyStageTitle(node.name ?? node.node_id);
  const params = stageParamRows(node, { maxRows: 12 });
  const incoming = model.edges.filter((e) => e.to === node.node_id);
  const outgoing = model.edges.filter((e) => e.from === node.node_id);
  const artifactType = node.artifact?.artifact_type ?? null;
  const artifactUnits = node.artifact?.units ?? null;
  const shortHash = node.artifact_hash
    ? node.artifact_hash.slice(0, 12) + '…'
    : null;

  return (
    <div className="flex shrink-0 flex-col gap-3 border-t border-line-subtle bg-white/[0.012] px-6 pt-3 pb-4 text-[11px]">
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="kicker text-fg-muted">
            Inspecting · rank {modelNode.rank} · lane {modelNode.lane}
            {modelNode.isTerminal && ' · output'}
            {modelNode.cycleFallback && ' · cycle fallback'}
          </div>
          <h3 className="mt-0.5 truncate text-[13px] font-semibold tracking-[-0.008em] text-fg-primary">
            {title}
          </h3>
          <div className="mt-0.5 font-mono text-[10px] text-fg-faint">
            {node.node_id} · {node.kind}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          title="Close inspector"
          className="flex h-7 w-7 items-center justify-center rounded-md border border-line-soft bg-white/[0.02] text-fg-secondary transition-colors hover:border-ice-400/35 hover:text-ice-200"
        >
          <X size={11} strokeWidth={1.75} aria-hidden />
        </button>
      </header>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Section title="Params" empty="No exposed parameters.">
          {params.length === 0 ? null : (
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
              {params.map((p) => (
                <div key={p.label} className="contents">
                  <dt className="truncate text-fg-muted">{p.label}</dt>
                  <dd className="truncate font-mono text-fg-secondary">
                    {p.value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </Section>

        <Section
          title={`Inputs (${incoming.length})`}
          empty="No inputs — this is a source node."
        >
          <EdgeList edges={incoming} field="from" />
        </Section>

        <Section
          title={`Outputs (${outgoing.length})`}
          empty="No outputs — this is a terminal node."
        >
          <EdgeList edges={outgoing} field="to" />
        </Section>
      </div>

      {(artifactType || shortHash) && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line-subtle pt-2 text-[10.5px]">
          <span className="text-fg-faint">Artifact</span>
          {artifactType && (
            <span className="rounded-sm border border-line-soft bg-white/[0.025] px-1.5 py-0.5 font-mono text-fg-secondary">
              {artifactType}
              {artifactUnits ? ` · ${artifactUnits}` : ''}
            </span>
          )}
          {shortHash && (
            <span className="rounded-sm border border-ice-400/25 bg-ice-500/10 px-1.5 py-0.5 font-mono text-ice-200">
              {shortHash}
            </span>
          )}
          {!shortHash && (
            <span className="text-fg-faint">
              No persisted artifact for this stage.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function Section({
  title,
  empty,
  children,
}: {
  title: string;
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="text-[9.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
        {title}
      </span>
      {children ?? (
        <p className="text-[10.5px] italic text-fg-faint">{empty}</p>
      )}
    </div>
  );
}

function EdgeList({
  edges,
  field,
}: {
  edges: DagModelEdge[];
  field: 'from' | 'to';
}) {
  if (edges.length === 0) {
    return null;
  }
  return (
    <ul className="flex flex-col gap-1">
      {edges.map((e) => (
        <li
          key={`${e.from}->${e.to}::${e.slotName}`}
          className="flex items-baseline justify-between gap-2"
        >
          <span className="truncate font-mono text-[10.5px] text-fg-secondary">
            {e[field]}
          </span>
          {e.slotName && (
            <span className="shrink-0 rounded-sm border border-violet-400/25 bg-violet-500/10 px-1 py-px font-mono text-[9.5px] text-violet-200">
              {e.slotName}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
