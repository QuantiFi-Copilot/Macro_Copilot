// ============================================================================
// Copilot WebSocket Protocol Types
// These types mirror the backend SessionEvent protocol exactly.
// ============================================================================

// --- Incoming server events ---

export type ServerEvent =
  | { type: 'ready'; thread_id: string; message: string }
  | { type: 'status'; status: 'thinking' | 'synthesising' }
  | { type: 'tool_call'; tool: string; label: string; params: Record<string, unknown> }
  | { type: 'tool_result'; tool: string; duration_ms: number | null }
  | { type: 'token'; content: string }
  | { type: 'done'; workspace_context: WorkspaceContext | null; tool_calls: ToolCallSummary[]; total_duration_ms: number }
  | { type: 'error'; message: string };

export type ToolCallSummary = {
  tool: string;
  duration_ms: number | null;
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
};

// --- Connection state ---

export type ConnectionStatus = 'disconnected' | 'connecting' | 'ready' | 'error';
