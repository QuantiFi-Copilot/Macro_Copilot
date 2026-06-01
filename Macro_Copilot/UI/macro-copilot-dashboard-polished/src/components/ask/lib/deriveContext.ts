// ============================================================================
// deriveContext
// ----------------------------------------------------------------------------
// Aggregates everything the right-hand context rail needs from the
// active conversation: the working set of artifacts produced this
// thread, the methodology / convention surface in play, and the
// step-walk for the most recent answer.
//
// All three are *derived* from `messages` rather than being a separate
// state — the substrate's responses already carry the structural
// metadata (terminal_artifact + lineage summary + tool params); we
// just present them in a glance-able way.
//
// V1 limitations (called out where they matter):
//   - Methodology conventions: the chat WS doesn't ship per-tool
//     conventions today, so the methodology panel surfaces only the
//     tool names that ran + a count.  When the backend extends
//     `tool_result` to include the resolved config-hash and convention
//     summary, this layer is where to plug it in.
//   - Latest-lineage panel: derived from the most recent assistant
//     message's `workflow_lineage_summary` if present, else from its
//     traceSteps as a degraded approximation.
// ============================================================================

import type { CopilotMessage } from '@/types/copilot';
import type { WorkflowTerminalArtifact } from '@/types/workflows';

// ----------------------------------------------------------------------------
// Working set — every artifact produced this thread

export type WorkingSetArtifact = {
  /** Stable id we mint for React keys + cross-references.  Not a real
   *  content-addressed hash — V2 will replace these with actual lineage
   *  hashes from the backend. */
  id: string;
  /** Human-friendly name.  For workflow terminals: "<template_id>
   *  output".  For trace primitives: the tool name. */
  name: string;
  /** "Series<bps>", "EventSet", "WindowedPanel(47×11)", etc. */
  typeLabel: string;
  /** Optional unit hint surfaced as a meta chip. */
  units?: string | null;
  /** Origin: which message did this artifact come from.  Lets the rail
   *  scroll-into-view when an artifact is clicked. */
  fromMessageId: string;
  /** A short (6-char) "hash-like" suffix derived deterministically from
   *  message id + artifact name.  V1 only — V2 will plumb the real
   *  lineage hash from the backend. */
  shortHash: string;
};

export function deriveWorkingSet(
  messages: CopilotMessage[],
): WorkingSetArtifact[] {
  const out: WorkingSetArtifact[] = [];
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue;

    // Workflow path — prefer the typed terminal artifact + its key.
    if (msg.workflow?.result?.terminal_artifact) {
      const a = msg.workflow.result.terminal_artifact;
      out.push({
        id: `${msg.id}:terminal`,
        name: artifactDisplayName(a, msg.workflow.routeDecision.template_id),
        typeLabel: artifactTypeLabel(a),
        units: a.units ?? undefined,
        fromMessageId: msg.id,
        shortHash: stableShortHash(msg.id, a.series_key ?? a.type),
      });
    }

    // Supervisor path — every successful tool call produced an
    // intermediate Series.  We surface them as working-set items so
    // the user can see "this answer touched these primitives".
    for (const step of msg.traceSteps) {
      if (step.status !== 'complete') continue;
      out.push({
        id: `${msg.id}:trace:${step.id}`,
        name: step.tool,
        typeLabel: 'Series',
        units: undefined,
        fromMessageId: msg.id,
        shortHash: stableShortHash(msg.id, step.tool + step.id),
      });
    }
  }
  return out;
}

function artifactDisplayName(
  a: WorkflowTerminalArtifact,
  templateId: string | null,
): string {
  if (a.series_key) return a.series_key;
  // PR-11B: open-DAG turns carry ``template_id === null`` (no recipe).
  // Fall back to a generic "open dag" label so the working-set chip
  // still reads cleanly instead of "null · scalarmetric".
  const owner = templateId ?? 'open dag';
  return `${owner} · ${a.type.toLowerCase()}`;
}

function artifactTypeLabel(a: WorkflowTerminalArtifact): string {
  if (a.type === 'Series') {
    const u = a.units ? `<${a.units}>` : '';
    const tag =
      a.index_kind === 'event_relative_offset' ? 'Series (event-relative)' : 'Series';
    return `${tag}${u}`;
  }
  if (a.type === 'SeriesSet') {
    const n = a.keys?.length ?? 0;
    return `SeriesSet(${n})`;
  }
  if (a.type === 'EventSet') {
    return `EventSet(${a.n_events ?? 0} events)`;
  }
  return a.type;
}

// ----------------------------------------------------------------------------
// Methodology — tools used + (V2) conventions

export type MethodologyEntry = {
  toolName: string;
  /** Number of distinct invocations this thread.  Surfaces as "×N" in
   *  the rail. */
  callCount: number;
  /** Always 'unknown' in V1 — the WS doesn't ship per-call source
   *  metadata yet.  V2: backend stamps tool_result with a
   *  source-tag distribution per call. */
  sourceTagSummary: 'unknown' | 'industry_standard' | 'mixed';
};

export function deriveMethodology(
  messages: CopilotMessage[],
): MethodologyEntry[] {
  const counts = new Map<string, number>();
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue;
    for (const step of msg.traceSteps) {
      if (step.status !== 'complete') continue;
      counts.set(step.tool, (counts.get(step.tool) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([toolName, callCount]) => ({
      toolName,
      callCount,
      sourceTagSummary: 'unknown' as const,
    }))
    .sort((a, b) => b.callCount - a.callCount);
}

// ----------------------------------------------------------------------------
// Latest lineage — the step walk for the most recent assistant turn

export type LineageStep = {
  index: number;
  /** "primitive" | "operator" | "fetch" | "terminal" — drives the
   *  step's badge color in the rail. */
  kind: 'primitive' | 'operator' | 'terminal';
  name: string;
  /** Optional duration if known. */
  durationMs?: number;
};

export function deriveLatestLineage(
  messages: CopilotMessage[],
): LineageStep[] {
  // Walk backwards to find the most recent assistant message with
  // either a workflow lineage or a tool trace.
  const last = [...messages]
    .reverse()
    .find(
      (m) =>
        m.role === 'assistant' &&
        (m.workflow?.result?.workflow_lineage_summary ||
          m.traceSteps.length > 0),
    );
  if (!last) return [];

  const lineage = last.workflow?.result?.workflow_lineage_summary;
  if (lineage) {
    const parts = lineage
      .replace(/^\s*workflow\s+\S+:\s*/i, '')
      .split(/[→>]/)
      .map((s) => s.trim())
      .filter(Boolean);
    return parts.map((name, i) => ({
      index: i + 1,
      kind:
        i === parts.length - 1
          ? 'terminal'
          : isPrimitiveName(name)
            ? 'primitive'
            : 'operator',
      name,
    }));
  }

  // Supervisor fallback: trace steps as a flat primitive sequence.
  return last.traceSteps.map((s, i) => ({
    index: i + 1,
    kind: 'primitive' as const,
    name: s.tool,
    durationMs: s.durationMs,
  }));
}

function isPrimitiveName(name: string): boolean {
  return /(_tool$|^calculate_|^get_|^scan_|^classify_)/.test(name);
}

// ----------------------------------------------------------------------------
// Deterministic 6-char "hash" for V1 lineage chips.  Not cryptographic —
// just stable so the same pair (messageId, key) always renders the
// same chip across re-renders.  V2 replaces this with the backend's
// actual content-addressed hash.

function stableShortHash(messageId: string, key: string): string {
  const seed = `${messageId}::${key}`;
  let h = 5381;
  for (let i = 0; i < seed.length; i++) {
    h = ((h << 5) + h + seed.charCodeAt(i)) | 0;
  }
  // Convert to unsigned, base-16, padded.  Slice 6 chars from the end
  // so the visible part responds to small input changes.
  const hex = (h >>> 0).toString(16).padStart(8, '0');
  return hex.slice(-6);
}
