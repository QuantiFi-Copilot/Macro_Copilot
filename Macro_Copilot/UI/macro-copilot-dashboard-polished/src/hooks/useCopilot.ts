import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  AssistantPhase,
  CopilotMessage,
  ConnectionStatus,
  ServerEvent,
  ToolCallSummary,
  TraceStep,
} from '@/types/copilot';

const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/api/chat';
const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_BASE_DELAY_MS = 2000;

let _messageCounter = 0;
function nextId(): string {
  _messageCounter += 1;
  return `msg-${Date.now()}-${_messageCounter}`;
}

let _traceCounter = 0;
function nextTraceId(): string {
  _traceCounter += 1;
  return `trace-${_traceCounter}`;
}

function reconcileTraceStepsFromDone(
  traceSteps: TraceStep[],
  toolCalls: ToolCallSummary[],
): TraceStep[] {
  if (traceSteps.length === 0) return traceSteps;

  const remaining = [...toolCalls];

  return traceSteps.map((step) => {
    if (step.status !== 'running') {
      return step;
    }

    const matchIndex = remaining.findIndex((toolCall) => toolCall.tool === step.tool);
    if (matchIndex === -1) {
      return {
        ...step,
        status: 'error',
        error:
          step.error ??
          'Streaming finished without a matching final tool result for this step.',
      };
    }

    const [match] = remaining.splice(matchIndex, 1);
    const toolError =
      typeof match.error === 'string' && match.error.length > 0
        ? match.error
        : undefined;

    return {
      ...step,
      status: toolError ? 'error' : 'complete',
      durationMs: match.duration_ms ?? step.durationMs,
      error: toolError,
    };
  });
}

type UseCopilotResult = {
  messages: CopilotMessage[];
  sendMessage: (content: string) => void;
  connectionStatus: ConnectionStatus;
  isThinking: boolean;
  clearMessages: () => void;
  /** Edit a previous user message in-place: truncate the message
   *  buffer to *before* the target message id, then submit a new turn
   *  with the edited content.  Mirrors the conversational rewind that
   *  the Ask UI exposes via the in-line "edit" affordance on user
   *  messages.  V1: the backend has no thread persistence, so this
   *  is effectively "fork from this point" — when V2 ships persistent
   *  threads, the same primitive becomes "edit and replay against the
   *  same thread_id". */
  editAndResubmit: (messageId: string, newContent: string) => void;
};

export function useCopilot(): UseCopilotResult {
  const [messages, setMessages] = useState<CopilotMessage[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected');
  const [isThinking, setIsThinking] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Ref to the current streaming assistant message id
  const streamingMsgId = useRef<string | null>(null);
  // R5.4 — remember the workspace_slug from the most recently-sent
  // user message so the streaming assistant message it produces
  // inherits it.  Cleared on each ``done`` event.
  const pendingWorkspaceSlug = useRef<string | null>(null);

  const updateStreamingMessage = useCallback(
    (updater: (message: CopilotMessage) => CopilotMessage) => {
      const activeId = streamingMsgId.current;
      if (!activeId) return;

      setMessages((prev) =>
        prev.map((msg) => (msg.id === activeId ? updater(msg) : msg)),
      );
    },
    [],
  );

  // ------------------------------------------------------------------
  // Connection management
  // ------------------------------------------------------------------

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setConnectionStatus('connecting');
    const ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      // Wait for the 'ready' event before marking as connected
    };

    ws.onmessage = (evt) => {
      let event: ServerEvent;
      try {
        event = JSON.parse(evt.data) as ServerEvent;
      } catch {
        return;
      }

      handleServerEvent(event);
    };

    ws.onclose = (evt) => {
      wsRef.current = null;

      // Don't reconnect if closed cleanly or max attempts reached
      if (evt.code === 1000 || reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS) {
        setConnectionStatus('disconnected');
        setIsThinking(false);
        return;
      }

      setConnectionStatus('connecting');
      const delay = RECONNECT_BASE_DELAY_MS * Math.pow(2, reconnectAttempts.current);
      reconnectAttempts.current += 1;

      reconnectTimer.current = setTimeout(() => {
        connect();
      }, delay);
    };

    ws.onerror = () => {
      setConnectionStatus('error');
    };

    wsRef.current = ws;
  }, []);

  // ------------------------------------------------------------------
  // Event handler
  // ------------------------------------------------------------------

  const handleServerEvent = useCallback((event: ServerEvent) => {
    switch (event.type) {
      case 'ready':
        setConnectionStatus('ready');
        reconnectAttempts.current = 0;
        break;

      case 'status':
        if (event.status === 'thinking') {
          // Create the assistant message placeholder.  R5.4 — inherit
          // the workspace_slug recorded by the most recent sendMessage
          // call so the per-workspace chat rail can filter correctly.
          const assistantId = nextId();
          streamingMsgId.current = assistantId;

          setMessages((prev) => [
            ...prev,
            {
              id: assistantId,
              role: 'assistant',
              content: '',
              timestamp: new Date(),
              traceSteps: [],
              workspaceContext: null,
              isStreaming: true,
              phase: 'thinking' satisfies AssistantPhase,
              workspaceSlug: pendingWorkspaceSlug.current,
            },
          ]);
        }
        if (event.status === 'synthesising') {
          updateStreamingMessage((msg) => ({
            ...msg,
            phase: 'synthesising',
          }));
        }
        break;

      case 'tool_call': {
        const step: TraceStep = {
          id: nextTraceId(),
          tool: event.tool,
          label: event.label,
          status: 'running',
          startedAt: Date.now(),
        };

        updateStreamingMessage((msg) => ({
          ...msg,
          phase: 'running_tools',
          traceSteps: [...msg.traceSteps, step],
        }));
        break;
      }

      case 'tool_result': {
        // If the backend attached an error string, the tool returned
        // {"error": "..."} from its MCP server. Mark the step as errored
        // and preserve the message so the trace can render it inline
        // instead of pretending the call succeeded.
        const toolErrored =
          typeof event.error === 'string' && event.error.length > 0;

        updateStreamingMessage((msg) => {
          let matchedRunningStep = false;

          return {
            ...msg,
            traceSteps: msg.traceSteps.map((step) => {
              if (
                !matchedRunningStep &&
                step.tool === event.tool &&
                step.status === 'running'
              ) {
                matchedRunningStep = true;
                return {
                  ...step,
                  status: toolErrored ? ('error' as const) : ('complete' as const),
                  durationMs: event.duration_ms ?? undefined,
                  error: toolErrored ? (event.error as string) : undefined,
                };
              }

              return step;
            }),
          };
        });
        break;
      }

      case 'token': {
        updateStreamingMessage((msg) => ({
          ...msg,
          content: msg.content + event.content,
        }));
        break;
      }

      case 'done': {
        // Phase 4 — rebrand the wire's snake-case ``proposed_overrides``
        // to the React message's camelCase ``proposedOverrides``.  Map
        // each entry's wire fields (``value_label`` / ``node_id`` /
        // ``previous_value``) to the camelCase equivalents the chip UI
        // expects.  No-op when the event omits the field, so older
        // backends that don't emit overrides keep working.
        const proposedOverrides =
          event.proposed_overrides && event.proposed_overrides.length > 0
            ? event.proposed_overrides.map((o) => ({
                id: o.id,
                path: o.path,
                value: o.value,
                valueLabel: o.value_label,
                nodeId: o.node_id,
                previousValue: o.previous_value,
                rationale: o.rationale,
              }))
            : null;
        updateStreamingMessage((msg) => ({
          ...msg,
          isStreaming: false,
          phase: 'done',
          traceSteps: reconcileTraceStepsFromDone(
            msg.traceSteps,
            event.tool_calls,
          ),
          workspaceContext: event.workspace_context,
          totalDurationMs: event.total_duration_ms,
          proposedOverrides,
        }));
        streamingMsgId.current = null;
        pendingWorkspaceSlug.current = null;
        setIsThinking(false);
        break;
      }

      case 'error': {
        // If we're mid-stream, mark the message as errored
        if (streamingMsgId.current) {
          updateStreamingMessage((msg) => ({
            ...msg,
            content: msg.content || `Error: ${event.message}`,
            isStreaming: false,
            phase: 'error',
          }));
          streamingMsgId.current = null;
        }
        pendingWorkspaceSlug.current = null;
        setIsThinking(false);
        break;
      }

      // ----------------------------------------------------------------
      // PR 10 — workflow events
      // ----------------------------------------------------------------
      case 'workflow_route_decision': {
        // Promote a workflow_route_decision to the streaming assistant
        // bubble even when no prior 'status: thinking' was emitted
        // (the workflow gate runs BEFORE the supervisor, so the
        // bubble may not exist yet).
        if (!streamingMsgId.current) {
          const id = nextId();
          streamingMsgId.current = id;
          setMessages((prev) => [
            ...prev,
            {
              id,
              role: 'assistant',
              content: '',
              timestamp: new Date(),
              traceSteps: [],
              workspaceContext: null,
              isStreaming: true,
              phase: 'running_tools',
              workflow: null,
              // R5.4 — inherit the pending workspace slug so workflow
              // turns originating from a workspace-scoped composer
              // stay filterable.
              workspaceSlug: pendingWorkspaceSlug.current,
            },
          ]);
        }
        if (event.action !== 'route' || !event.template_id) {
          // CLARIFY / OUT_OF_SCOPE — clear any partial workflow
          // state so the assistant message renders the existing
          // clarification path.
          updateStreamingMessage((msg) => ({ ...msg, workflow: null }));
          break;
        }
        updateStreamingMessage((msg) => ({
          ...msg,
          phase: 'running_tools',
          workflow: {
            routeDecision: {
              template_id: event.template_id!,
              slot_values: event.slot_values,
              rationale: event.rationale,
            },
            status: 'running',
            result: undefined,
          },
        }));
        break;
      }

      case 'workflow_status': {
        updateStreamingMessage((msg) => {
          if (!msg.workflow) return msg;
          return {
            ...msg,
            workflow: { ...msg.workflow, status: event.status },
          };
        });
        break;
      }

      case 'workflow_result': {
        updateStreamingMessage((msg) => {
          if (!msg.workflow) return msg;
          return {
            ...msg,
            workflow: {
              ...msg.workflow,
              status: event.ok ? 'complete' : 'error',
              result: {
                ok: event.ok,
                template_id: event.template_id,
                terminal_artifact: event.terminal_artifact,
                workflow_lineage_summary: event.workflow_lineage_summary,
                error: event.error,
              },
              // PR A — surface the persisted workspace handle so
              // BuildShell can navigate to ``/workspace/:slug``
              // when the user originated the prompt from Build's
              // empty state.  Optional everywhere: if the runner
              // didn't persist (no engine / no object_storage /
              // persist=False), this stays null and the chat
              // renders the result inline as before.
              workspace: event.workspace ?? null,
            },
          };
        });
        break;
      }
    }
  }, [updateStreamingMessage]);

  // ------------------------------------------------------------------
  // Send message
  // ------------------------------------------------------------------

  const sendMessage = useCallback(
    (content: string, options?: { workspaceSlug?: string | null }) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
      const workspaceSlug = options?.workspaceSlug ?? null;

      // Add user message to state — stamped with the workspace slug so
      // the per-workspace chat rail can filter (R5.4).
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'user',
          content: trimmed,
          timestamp: new Date(),
          traceSteps: [],
          workspaceContext: null,
          isStreaming: false,
          workspaceSlug,
        },
      ]);

      setIsThinking(true);
      // Remember the slug so the streaming assistant message stamped
      // from the next ``thinking`` event inherits it.  Cleared in the
      // ``done`` handler so a subsequent un-scoped turn doesn't pick
      // up stale state.
      pendingWorkspaceSlug.current = workspaceSlug;

      // Send to server.  Include workspace_slug so the backend can
      // (eventually) scope LangGraph thread state per workspace.
      const payload: { type: string; content: string; workspace_slug?: string } = {
        type: 'user_message',
        content: trimmed,
      };
      if (workspaceSlug) payload.workspace_slug = workspaceSlug;
      wsRef.current.send(JSON.stringify(payload));
    },
    [],
  );

  // ------------------------------------------------------------------
  // Clear messages
  // ------------------------------------------------------------------

  const clearMessages = useCallback(() => {
    setMessages([]);
    streamingMsgId.current = null;
    setIsThinking(false);
  }, []);

  // ------------------------------------------------------------------
  // Edit & resubmit
  // ------------------------------------------------------------------
  // Truncates the message buffer to *before* the target message id,
  // then sends `newContent` as a new turn.  Both setMessages calls
  // are issued in the same React batch, so the user sees a single
  // smooth update: the old message and everything after disappear,
  // the new edited message appears, and the assistant re-streams
  // from there.
  //
  // Refusing to edit while a turn is mid-stream avoids the
  // race where the WS would still be writing into the soon-to-be-
  // truncated streaming message.

  const editAndResubmit = useCallback(
    (messageId: string, newContent: string) => {
      const trimmed = newContent.trim();
      if (!trimmed) return;
      if (isThinking) return;
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

      // Step 1: truncate locally to the slice before the edited message.
      let truncated = false;
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === messageId);
        if (idx === -1) return prev;
        truncated = true;
        return prev.slice(0, idx);
      });

      // The local truncate above runs synchronously inside the React
      // batch; if the message wasn't in the buffer (already truncated
      // or stale id) we silently no-op rather than send a stray turn.
      if (!truncated) return;

      // Step 2: submit the edited content as a fresh turn.
      // sendMessage() will append the new user message and dispatch
      // to the WS.  React batches the two setState calls so the user
      // sees a single transition.
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'user',
          content: trimmed,
          timestamp: new Date(),
          traceSteps: [],
          workspaceContext: null,
          isStreaming: false,
        },
      ]);
      setIsThinking(true);
      wsRef.current.send(
        JSON.stringify({ type: 'user_message', content: trimmed }),
      );
    },
    [isThinking],
  );

  // ------------------------------------------------------------------
  // Lifecycle
  // ------------------------------------------------------------------

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // Prevent reconnect on cleanup
        wsRef.current.close(1000);
      }
    };
  }, [connect]);

  return {
    messages,
    sendMessage,
    connectionStatus,
    isThinking,
    clearMessages,
    editAndResubmit,
  };
}
