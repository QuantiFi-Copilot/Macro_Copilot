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
  | { type: 'done'; workspace_context: WorkspaceContext | null; tool_calls: ToolCallSummary[]; total_duration_ms: number }
  | { type: 'error'; message: string }
  // PR 10 — workflow events.  Forward-compatible: existing handler
  // ignores unknown types, so older clients work unchanged.
  | WorkflowRouteDecisionEvent
  | WorkflowStatusEvent
  | WorkflowResultEvent;

export type ToolCallSummary = {
  tool: string;
  duration_ms: number | null;
  // Mirrors tool_result.error; populated in the final `done` event's
  // tool_calls array so the UI can render a final failure state even if
  // the streaming tool_result was missed.
  error?: string | null;
};

export type WorkspaceContext = {
  tools: Array<{ tool: string; params: Record<string, unknown> }>;
  tool_count: number;
};

// --- Outgoing client events ---

export type ClientMessage = {
  type: 'user_message';
  content: string;
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
};

// PR 10 — workflow turn state.  Aggregates the events the WS streams
// for one workflow execution into a single struct the chat bubble
// renders.
export type WorkflowTurnPayload = {
  routeDecision: {
    template_id: string;
    slot_values: Record<string, unknown>;
    rationale: string;
  };
  status: 'running' | 'complete' | 'error';
  result?: {
    ok: boolean;
    template_id: string;
    terminal_artifact?: WorkflowTerminalArtifact;
    workflow_lineage_summary?: string;
    error?: string;
  };
};

// --- Connection state ---

export type ConnectionStatus = 'disconnected' | 'connecting' | 'ready' | 'error';
