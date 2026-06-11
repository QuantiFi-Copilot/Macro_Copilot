// ============================================================================
// Copilot WebSocket Protocol Types
// These types mirror the backend SessionEvent protocol exactly.
// ============================================================================

import type {
  WorkflowResultEvent,
  WorkflowRouteDecisionEvent,
  WorkflowStatusEvent,
  WorkflowTerminalArtifact,
} from '@/types/workflows';

// --- Incoming server events ---

export type ServerEvent =
  | { type: 'ready'; thread_id: string; message: string }
  | { type: 'status'; status: 'thinking' | 'synthesising' }
  | { type: 'tool_call'; tool: string; label: string; params: Record<string, unknown> }
  | {
      type: 'tool_result';
      tool: string;
      duration_ms: number | null;
      // Present and non-null when the tool returned {"error": "..."} from
      // its MCP server; null/undefined on success. Consumers should
      // render per-tool failure state based on this field.
      error?: string | null;
    }
  | { type: 'token'; content: string }
  | {
      type: 'done';
      workspace_context: WorkspaceContext | null;
      tool_calls: ToolCallSummary[];
      total_duration_ms: number;
      // Phase 4 — chat-driven parameter overrides.  Optional;
      // populated only when the assistant identifies that the user's
      // prose implies a parameter change on the active workspace.
      // The wire shape uses snake_case to match the rest of the
      // SessionEvent protocol.  Frontend rebrands to
      // ``proposedOverrides`` on the React message object.
      proposed_overrides?: ServerProposedOverride[] | null;
      // Consolidation target #3 — the open-DAG pipeline's terminal
      // status (PASS / PASS_DRYRUN / GATE_REFUSE / GATE_CLARIFY /
      // COMPOSER_REFUSE / ASSEMBLY_REFUSE / ROUTER_CLARIFY /
      // PIPELINE_ERROR).  Lets the Ask card distinguish an HONEST
      // refusal/clarification (prose carries the message; no
      // "EXECUTION FAILED" panel) from a genuine pipeline error.
      // Absent on direct-lane / template-lane turns.
      open_dag_status?: string | null;
    }
  | { type: 'error'; message: string }
  // PR 10 — workflow events.  Forward-compatible: existing handler
  // ignores unknown types, so older clients work unchanged.
  | WorkflowRouteDecisionEvent
  | WorkflowStatusEvent
  | WorkflowResultEvent;

/** Wire shape of a single ``proposed_overrides`` entry on the
 *  ``done`` event.  Snake-case mirrors the rest of the SessionEvent
 *  protocol; the React message rebrands to ``ProposedOverride``. */
export type ServerProposedOverride = {
  id?: string;
  path: [string] | [string, string];
  value: unknown;
  value_label?: string;
  node_id?: string;
  previous_value?: unknown;
  rationale?: string;
};

export type ToolCallSummary = {
  tool: string;
  duration_ms: number | null;
  // Mirrors tool_result.error; populated in the final `done` event's
  // tool_calls array so the UI can render a final failure state even if
  // the streaming tool_result was missed.
  error?: string | null;
};

export type WorkspaceContext = {
  tools: Array<WorkspaceContextTool>;
  tool_count: number;
  /** Optional originating user prompt — the question that produced this
   *  multi-tool plan.  When present the multi-tool DAG header surfaces it
   *  verbatim; when absent the header falls back to a generic title.
   *  Additive — older ``workspace_context`` payloads omit it, and the
   *  multi-tool DAG degrades gracefully (generic "From your Ask answer"
   *  header). */
  prompt?: string;
  /** Optional dependency edges between tool calls (indices into
   *  ``tools``).  When present the DAG renders a multi-step pipeline
   *  (arrows + dependency labels per the multi_tool.png mockup); when
   *  absent every tool is treated as a parallel sibling (the common
   *  case today — the supervisor emits edges only for genuine
   *  multi-step plans).  Additive + backward-compatible. */
  edges?: Array<WorkspaceContextEdge>;
};

/** One dependency edge in a multi-step multi-tool plan.  ``from`` /
 *  ``to`` are indices into ``WorkspaceContext.tools``.  Emitted by the
 *  supervisor only when one tool's output feeds another; absent for
 *  parallel comparisons.  Consumed by the multi-tool DAG strip to draw
 *  the data-flow arrow + dependency label. */
export type WorkspaceContextEdge = {
  /** Source tool index (the upstream tool whose output is consumed). */
  from: number;
  /** Target tool index (the downstream tool that consumes it). */
  to: number;
  /** Optional human-readable dependency label (e.g. "uses output of").
   *  The DAG strip renders a default when omitted. */
  label?: string;
};

/** One tool entry in the ``workspace_context`` block emitted on the
 *  ``done`` event.  Pre-PR-B-α only ``tool`` and ``params`` were
 *  emitted; PR-B-α added optional ``domain`` / ``status`` / ``error``
 *  / ``duration_ms`` so the Build canvas can render an honest "this
 *  tool failed" tile next to working cards instead of silently
 *  dropping errored entries.  All new fields are optional — pre-PR-B-α
 *  consumers that read only ``tool`` / ``params`` continue to work. */
export type WorkspaceContextTool = {
  tool: string;
  params: Record<string, unknown>;
  /** Originating domain ("rates" / "ois" / …) when the trace recorded
   *  it.  Carried so the Build canvas can group cards by domain or
   *  surface the domain chip on each card.  Optional — older
   *  ``workspace_context`` payloads omit it. */
  domain?: string | null;
  /** "ok" (explicit) or "error" (set when the source trace carried a
   *  non-null ``error`` string).  Absent when the trace had no
   *  status, mirroring the backend's policy of not writing null
   *  fields. */
  status?: 'ok' | 'error';
  /** Tool error message — populated when ``status === 'error'``. */
  error?: string;
  /** Tool execution time in milliseconds, when the trace recorded it. */
  duration_ms?: number;
};

// --- Outgoing client events ---

export type ClientMessage = {
  type: 'user_message';
  content: string;
  // R5.4 — optional Build workspace slug.  When set, the backend
  // stamps each emitted ServerEvent with the same slug so the
  // frontend can filter per-workspace conversation rails.  Sent by
  // the Build copilot rail composer; omitted by the global Ask
  // surface (where no workspace is in scope).
  workspace_slug?: string;
};

// --- Chat message model (React state) ---

export type TraceStep = {
  id: string;
  tool: string;
  label: string;
  status: 'running' | 'complete' | 'error';
  startedAt: number;
  durationMs?: number;
  // When status === 'error', this holds the error message from the MCP
  // tool output so the UI can display it inline under the failed tool.
  error?: string;
};

export type AssistantPhase =
  | 'thinking'
  | 'running_tools'
  | 'synthesising'
  | 'done'
  | 'error';

export type CopilotMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  traceSteps: TraceStep[];
  workspaceContext: WorkspaceContext | null;
  totalDurationMs?: number;
  isStreaming: boolean;
  phase?: AssistantPhase;
  // PR 10 — workflow turn payload.  Set when the supervisor's
  // workflow router took the turn (action=route).  When present, the
  // chat bubble renders a structured workflow result card next to the
  // streamed prose.
  workflow?: WorkflowTurnPayload | null;
  // Phase 4 — chat-driven parameter overrides.  When the assistant
  // identifies that the user's prose implies a parameter change on
  // the active workspace (e.g. "use ACT/365 instead"), the backend
  // emits a structured payload here.  The Build copilot rail renders
  // these as approve/reject chips that dispatch into the workspace
  // overrides queue.  Detection is best-effort: the field is null on
  // every message until the backend ships the emitter.
  proposedOverrides?: ProposedOverride[] | null;
  // R5.4 — when the user message originated from a workspace-scoped
  // composer, the backend stamps this slug onto every event of the
  // turn.  The Build copilot rail filters its per-workspace message
  // view by this slug so messages from other workspaces don't bleed
  // in.  Null on the global Ask surface where no workspace is in
  // scope.
  workspaceSlug?: string | null;
  // Consolidation target #3 — the open-DAG pipeline status carried on
  // the ``done`` event.  Null/absent for direct-lane turns.  The Ask
  // card uses it to render honest refusals/clarifications as prose
  // (no coral "EXECUTION FAILED" panel — that's reserved for genuine
  // pipeline errors).
  openDagStatus?: string | null;
};

/** A single parameter override the assistant suggests.  Mirrors the
 *  shape ``WorkspaceOverridesProvider.setOverride`` consumes so the
 *  rail can dispatch it without a translation step.
 *
 *  ``path`` follows the same convention as ``ParamOverride.path`` —
 *  ``[slot]`` for scalars, ``[slot, field]`` for nested dict-merge
 *  entries.  ``valueLabel`` is an optional pre-rendered string for
 *  the chip (e.g. "ACT/365") when the raw ``value`` would print
 *  awkwardly (a long JSON object). */
export type ProposedOverride = {
  /** Stable id within the message so chips can be approved /
   *  rejected individually.  Server-assigned; falls back to a
   *  hash of (path, value) when absent. */
  id?: string;
  path: [string] | [string, string];
  value: unknown;
  /** Optional human-readable label for the value.  Defaults to the
   *  string form of ``value`` when missing. */
  valueLabel?: string;
  /** The stage's node_id this override targets.  Optional — the
   *  override map is path-keyed and doesn't need it, but carrying it
   *  through lets the rail render "for stage X" context next to the
   *  chip. */
  nodeId?: string;
  /** The substrate's previous value at this path, when known.  Used
   *  by the chip to show "was → is" preview without forcing the
   *  rail to look it up. */
  previousValue?: unknown;
  /** Short rationale the assistant generated for the suggestion —
   *  rendered as the chip's tooltip. */
  rationale?: string;
};

// PR 10 — workflow turn state.  Aggregates the events the WS streams
// for one workflow execution into a single struct the chat bubble
// renders.
//
// PR-11B: ``template_id`` is now nullable to carry the open-DAG variant.
// The template lane sets a real template_id; the open-DAG lane emits
// ``null`` (no template).  The useCopilot reducer admits null as the
// open-DAG route + threads it through; the Ask card renders an
// "Open DAG composition" header when null instead of blank template
// text.
export type WorkflowTurnPayload = {
  routeDecision: {
    template_id: string | null;
    slot_values: Record<string, unknown>;
    rationale: string;
  };
  status: 'running' | 'complete' | 'error';
  result?: {
    ok: boolean;
    template_id: string | null;
    terminal_artifact?: WorkflowTerminalArtifact;
    workflow_lineage_summary?: string;
    error?: string;
  };
  // PR A — persisted workspace handles.  Optional; only populated
  // when the runner opted into ``persist=True`` (the chat path) AND
  // the persist call succeeded.  Drives Build's "Open in Build"
  // affordance + BuildShell's slug-routed handoff.  Ask leaves
  // these undefined and renders the workflow inline as before.
  workspace?: {
    id: string;
    slug: string;
    name: string | null;
    dag_hash: string;
    url: string;
  } | null;
};

// --- Connection state ---

export type ConnectionStatus = 'disconnected' | 'connecting' | 'ready' | 'error';
