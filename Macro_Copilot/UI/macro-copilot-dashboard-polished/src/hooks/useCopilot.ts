import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  AssistantPhase,
  CopilotMessage,
  ConnectionStatus,
  ServerEvent,
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

type UseCopilotResult = {
  messages: CopilotMessage[];
  sendMessage: (content: string) => void;
  connectionStatus: ConnectionStatus;
  isThinking: boolean;
  clearMessages: () => void;
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
          // Create the assistant message placeholder
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
                  status: 'complete' as const,
                  durationMs: event.duration_ms ?? undefined,
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
        updateStreamingMessage((msg) => ({
          ...msg,
          isStreaming: false,
          phase: 'done',
          workspaceContext: event.workspace_context,
          totalDurationMs: event.total_duration_ms,
        }));
        streamingMsgId.current = null;
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
        setIsThinking(false);
        break;
      }
    }
  }, [updateStreamingMessage]);

  // ------------------------------------------------------------------
  // Send message
  // ------------------------------------------------------------------

  const sendMessage = useCallback(
    (content: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

      // Add user message to state
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

      // Send to server
      wsRef.current.send(
        JSON.stringify({ type: 'user_message', content: trimmed }),
      );
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
  };
}
