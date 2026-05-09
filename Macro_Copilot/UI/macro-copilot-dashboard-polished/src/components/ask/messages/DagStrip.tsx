// ============================================================================
// DagStrip — the executed graph, rendered visually
// ----------------------------------------------------------------------------
// Two states:
//   - COLLAPSED (default): a single-line pill-chain
//                          [primitive] →series→ [operator] →eventset→ ...
//                          Wire colors come from the substrate's artifact-
//                          type taxonomy.
//   - EXPANDED: a 2-row layout with each node's slot labels visible (V1
//               keeps it row-wrapped; V2 will switch to a properly
//               edge-routed graph).
//
// This is one of the highest-leverage anti-black-box affordances on the
// Ask page — every assistant turn shows the user the graph that ran,
// not just the prose the LLM generated.  Collapsed-by-default keeps it
// out of the way; one click and the topology is on screen.
// ============================================================================

import { useState } from 'react';
import { ChevronDown, ChevronRight, Network } from 'lucide-react';
import type { CopilotMessage } from '@/types/copilot';
import {
  deriveDagNodes,
  type DagNode,
  type DagWireType,
} from '@/components/ask/lib/deriveDagNodes';
import { cn } from '@/utils/cn';

type Props = {
  message: CopilotMessage;
};

export function DagStrip({ message }: Props) {
  const nodes = deriveDagNodes(message);
  const [expanded, setExpanded] = useState(false);

  if (nodes.length === 0) return null;

  return (
    <div className="border-y border-line-subtle bg-white/[0.008]">
      <div className="flex items-center justify-between gap-3 px-5 py-2.5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-2 text-left transition-colors hover:text-fg-primary"
        >
          {expanded ? (
            <ChevronDown size={11} className="shrink-0 text-fg-faint" />
          ) : (
            <ChevronRight size={11} className="shrink-0 text-fg-faint" />
          )}
          <Network size={11} className="shrink-0 text-lineage-300" />
          <span className="kicker text-fg-muted">
            DAG · {nodes.length} {nodes.length === 1 ? 'node' : 'nodes'}
          </span>
        </button>
        {!expanded && <CollapsedChain nodes={nodes} />}
      </div>
      {expanded && <ExpandedChain nodes={nodes} />}
    </div>
  );
}

// ----------------------------------------------------------------------------

function CollapsedChain({ nodes }: { nodes: DagNode[] }) {
  // Show up to 4 nodes inline in the collapsed strip; truncate the
  // middle if the graph is longer.  Wire color reflects the OUTPUT
  // type of the upstream node (the artifact crossing that edge).
  const visible = nodes.length > 5 ? collapseMiddle(nodes) : nodes;
  return (
    <div className="flex min-w-0 flex-1 items-center justify-end gap-1.5 overflow-hidden">
      {visible.map((entry, i) =>
        entry === null ? (
          <span key={`gap-${i}`} className="text-[11px] text-fg-faint">
            …
          </span>
        ) : (
          <span key={`n-${i}`} className="flex items-center gap-1.5">
            <NodePill node={entry} />
            {i < visible.length - 1 && (
              <Wire type={entry.outputType} />
            )}
          </span>
        ),
      )}
    </div>
  );
}

function ExpandedChain({ nodes }: { nodes: DagNode[] }) {
  return (
    <div className="border-t border-line-subtle px-5 py-3.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-3">
        {nodes.map((node, i) => (
          <span key={i} className="flex items-center gap-2">
            <NodePill node={node} expanded />
            {i < nodes.length - 1 && (
              <span className="flex items-center gap-1.5">
                <Wire type={node.outputType} long />
                <span
                  className={cn(
                    'mono text-[9.5px] uppercase tracking-[0.12em]',
                    wireTextClass(node.outputType),
                  )}
                >
                  {node.outputType}
                </span>
                <Wire type={node.outputType} long />
              </span>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------

function NodePill({
  node,
  expanded,
}: {
  node: DagNode;
  expanded?: boolean;
}) {
  const isOperator = node.kind === 'operator';
  const isTerminal = node.kind === 'terminal';
  return (
    <span
      className={cn(
        'dag-pill',
        isOperator && 'dag-pill-operator',
        isTerminal &&
          'border-mint-400/30 bg-mint-400/[0.07] text-mint-200',
        expanded && 'h-7 px-2.5 text-[11px]',
      )}
      title={node.name}
    >
      {node.name}
    </span>
  );
}

function Wire({ type, long }: { type: DagWireType; long?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-block h-px',
        long ? 'w-6' : 'w-3',
        wireBgClass(type),
      )}
    />
  );
}

function wireBgClass(t: DagWireType): string {
  switch (t) {
    case 'series':    return 'bg-wire-series';
    case 'eventset':  return 'bg-wire-eventset';
    case 'panel':     return 'bg-wire-panel';
    case 'windowed':  return 'bg-wire-windowed';
    case 'seriesset': return 'bg-wire-seriesset';
    default:          return 'bg-fg-faint';
  }
}

function wireTextClass(t: DagWireType): string {
  switch (t) {
    case 'series':    return 'text-wire-series';
    case 'eventset':  return 'text-wire-eventset';
    case 'panel':     return 'text-wire-panel';
    case 'windowed':  return 'text-wire-windowed';
    case 'seriesset': return 'text-wire-seriesset';
    default:          return 'text-fg-faint';
  }
}

function collapseMiddle(nodes: DagNode[]): Array<DagNode | null> {
  // Show first 2 + ellipsis + last 2.
  return [nodes[0], nodes[1], null, nodes[nodes.length - 2], nodes[nodes.length - 1]];
}
