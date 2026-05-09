// ============================================================================
// deriveDagNodes
// ----------------------------------------------------------------------------
// Turns the data attached to an assistant message into a sequence of
// DAG nodes the UI renders as a pill-chain (collapsed) or a node graph
// (expanded).
//
// Two input shapes:
//
//   1. Workflow turns.  `workflow.result.workflow_lineage_summary` is a
//      string of the form "workflow <id>: <node1> → <node2> → ...".
//      We split on arrows and strip the leading `workflow X:` prefix
//      so each node renders as a pill.  Node kind (primitive vs
//      operator) is inferred from the substrate's naming conventions:
//      anything ending in `_tool` or starting with the canonical rates
//      primitive prefixes is a primitive; everything else is an
//      operator.
//
//   2. Supervisor turns (no workflow).  We fall back to the message's
//      `traceSteps` — every running/complete tool call becomes a
//      primitive node.  The user can still see "this answer composed
//      these N tools" even without a registered workflow template.
//
// Edge artifact types are inferred from operator names where possible
// (e.g. `threshold_events` outputs an EventSet) so the wire color
// downstream of that operator is amber.  Unknown edges fall back to
// the neutral `default` wire color.
// ============================================================================

import type { CopilotMessage } from '@/types/copilot';

export type DagNodeKind = 'primitive' | 'operator' | 'terminal';

export type DagWireType =
  | 'series'
  | 'eventset'
  | 'panel'
  | 'windowed'
  | 'seriesset'
  | 'default';

export type DagNode = {
  /** Name as displayed in the UI (mono).  Usually the substrate's
   *  primitive / operator function name. */
  name: string;
  kind: DagNodeKind;
  /** Wire type emitted by this node — drives the color of its
   *  outgoing edge. */
  outputType: DagWireType;
};

const PRIMITIVE_NAME_HINTS = [
  /_tool$/,                  // every primitive registered with rates_agent
  /^calculate_/,             // legacy synonyms — be liberal
  /^get_/,
  /^scan_/,
  /^classify_/,
];

// Operator → output artifact type map.  These are the substrate's
// `OperatorStep` outputs, derived from
// shared/operators/<name>/operator.py.
const OPERATOR_OUTPUT_TYPE: Record<string, DagWireType> = {
  align_series: 'seriesset',
  threshold_events: 'eventset',
  event_windows: 'windowed',
  conditional_aggregate: 'series',
  series_arithmetic: 'series',
  apply_mask: 'series',
  rolling_regression: 'panel',
  select_from_series_set: 'series',
  cross_sectional_rank: 'panel',
  summarize_series: 'series',
};

export function deriveDagNodes(message: CopilotMessage): DagNode[] {
  // Workflow path — prefer the substrate's lineage summary because it
  // captures the actual executed graph, not just the LLM's tool calls.
  const lineage = message.workflow?.result?.workflow_lineage_summary;
  if (lineage) {
    const parsed = parseWorkflowLineage(lineage);
    if (parsed.length > 0) return parsed;
  }

  // Supervisor path — the trace shows which primitives ran in order.
  // Each tool call becomes a primitive node.  No edges between them
  // are inferred (the supervisor calls them in parallel, structurally
  // independent), so the UI renders them as a fan-in to the answer.
  const tools = message.traceSteps
    .filter((s) => s.status !== 'error')
    .map((s) => ({
      name: s.tool,
      kind: 'primitive' as const,
      outputType: 'series' as const,
    }));
  return tools;
}

function parseWorkflowLineage(lineage: string): DagNode[] {
  // Strip the leading "workflow X:" prefix that the substrate's
  // lineage summarizer prepends.
  const normalised = lineage.replace(/^\s*workflow\s+\S+:\s*/i, '');
  const parts = normalised
    .split(/[→>]/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) return [];

  return parts.map((name, idx) => {
    const kind = inferKind(name);
    const isTerminal = idx === parts.length - 1;
    return {
      name,
      kind: isTerminal ? 'terminal' : kind,
      outputType: inferOutputType(name, kind),
    };
  });
}

function inferKind(name: string): DagNodeKind {
  if (PRIMITIVE_NAME_HINTS.some((re) => re.test(name))) return 'primitive';
  if (OPERATOR_OUTPUT_TYPE[name] !== undefined) return 'operator';
  // Conservative fallback — unknown nodes default to operator (the
  // looser category) so we don't accidentally misclassify a custom
  // primitive as a primitive when it doesn't follow the naming
  // convention.
  return 'operator';
}

function inferOutputType(name: string, kind: DagNodeKind): DagWireType {
  if (kind === 'operator') {
    return OPERATOR_OUTPUT_TYPE[name] ?? 'default';
  }
  // Primitives canonically emit Series (the bridge lifts their
  // `time_series` field into a typed Series artifact).
  return 'series';
}
