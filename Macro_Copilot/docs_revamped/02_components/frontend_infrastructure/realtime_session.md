# Frontend Infrastructure — Realtime Session

> The singleton WebSocket session, message-buffer reconciliation, turn lifecycle. CopilotContext is the one place WebSocket state lives.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing. Changes to the message-envelope shape are coordinated with backend orchestrator changes.
**Operationalises principles:** [SI1](README.md#si1--domain-blind-dispatch), [SI6](README.md#si6--hooks-are-read-side), P3 (consistency by contract), P4 (determinism — turn lifecycle + replay).
**See also:** [`README.md`](README.md), backend [`../orchestration/`](../orchestration/) (forthcoming) — the supervisor / turn / session backend contract.

---

## Component map

| Piece | Path | Purpose |
|---|---|---|
| `CopilotContext` | `src/context/CopilotContext.tsx` | React context exposing the singleton session state. |
| `CopilotProvider` | (same file) | Provider mounted ONCE in `App.tsx`, above `AppShell`. |
| `useCopilotContext()` | (same file) | Hook every consumer uses. |
| `useCopilot()` | (same file) | Internal hook owning the WebSocket + buffer; exposed via context. |
| WebSocket service | `src/services/copilot.ts` | Thin wrapper around the WebSocket lifecycle (connect, reconnect, parse envelopes). |
| Wire envelope types | `src/types/copilot.ts` | `CopilotMessage`, server-event envelopes, turn-status types. |

## The singleton invariant

CopilotProvider is mounted EXACTLY ONCE in the tree (in `App.tsx`, above AppShell). All surfaces consuming chat state (`AskPage`, `BuildShell.SlugFreeShell`) read from the same context. This is intentional:

- A prompt sent from Build's empty-state composer appears in Ask's history (they share the same buffer).
- A workflow turn in flight surfaces its `isThinking` flag to both surfaces.
- Reconnection is centralised; surfaces never manage their own sockets.

Mounting CopilotProvider twice is a contract violation. The check is mechanical: a grep for `<CopilotProvider` in `src/` returns exactly one match.

## What CopilotContext exposes

```ts
type CopilotContextValue = {
  messages: CopilotMessage[];
  sendMessage: (content: string) => void;
  editAndResubmit: (turnId: string, newContent: string) => void;
  connectionStatus: 'connecting' | 'ready' | 'disconnected' | 'error';
  isThinking: boolean;
  // ...internal helpers omitted...
};
```

The shape is minimal and stable. Adding a field requires an ADR if it changes the buffer-reconciliation behaviour (a new event kind, a new turn-status, etc.).

## Message-buffer reconciliation

The buffer is the local mirror of the server's `recent_turns` for the current session. Every server event either:
- Appends a new message.
- Mutates an existing message in place (e.g. status transitions from `thinking` → `complete`).
- Replaces a partial message with its final form (e.g. a streaming child-message's final text).

The reconciliation function lives in `useCopilot` and is the most subtle piece of the realtime layer. Its invariants:

1. **Append-or-mutate, never delete.** A message in the buffer is never removed.
2. **Idempotent.** Receiving the same event twice produces the same buffer state.
3. **Order-preserving.** Messages stay in the order the server sent them; in-place mutations don't shuffle.
4. **Survives reconnect.** On reconnect, the server replays `recent_turns`; the reconciler dedupes by turn-id and produces the same buffer state.

These invariants are tested in `src/context/__tests__/CopilotContext.spec.tsx` (the canonical contract test for the reconciler).

## What does NOT go in CopilotContext

- **Workspace state.** That lives in `WorkspaceOverridesContext` (scoped to a specific workspace via `useWorkspaceDetail`).
- **Rates data.** That lives in `RatesDataProvider` (lifted to AppShell for sidebar consumption).
- **Library manifest.** That lives in the React Query / SWR cache via `useLibraryManifest`.
- **Per-module state.** That lives in the module's `surfaces/` — modules do NOT push state to CopilotContext.

The singleton WebSocket is for *cross-surface chat state*. Any state that's surface-specific belongs in a surface-specific provider or local state.

## Message kinds (wire shape)

The wire envelope is closed:

```ts
type CopilotMessage =
  | UserMessage
  | AssistantMessage
  | ThinkingMessage
  | ToolTraceMessage
  | RoutingMessage
  | WorkflowResultMessage
  | ProseAnswerMessage
  | ErrorMessage
;
```

The closed family lives in `src/types/copilot.ts` and is the mirror of the backend's chat-event envelope (defined in `orchestrator/contracts.py` and the FastAPI router). Backend additions land coordinated PRs that update both sides.

Per FP13, the realtime layer dispatches on message *kind*, not on the primitive tool name embedded in a ToolTrace or WorkflowResult. Per-tool rendering happens in module surfaces (the `AskCard` if claimed, else the generic `AssistantResearchCard`).

## Turn lifecycle

A "turn" is one user-prompt → assistant-response cycle. The frontend treats turns as ordered groups of messages:

```
UserMessage              (status: complete)
ThinkingMessage          (status: complete → emitted while thinking)
RoutingMessage           (status: complete — which template / domain)
ToolTraceMessage(s)      (one per backend tool call)
WorkflowResultMessage    (the typed terminal artifact)
AssistantMessage         (the prose answer + ActionRow + FollowUps)
```

`isThinking` is true between UserMessage and AssistantMessage (or until error). Surfaces consume it to disable composers and show progress.

The turn lifecycle is shared with the backend's `orchestrator/state.py` machinery (turn open → turn close, persisted in copilot_state schema). The frontend mirrors but does not own turn identity.

## Error handling

WebSocket errors:
- `connectionStatus: 'disconnected'` triggers automatic reconnect.
- `connectionStatus: 'error'` (fatal) requires user action; surfaces render a banner.

Per-turn errors land as `ErrorMessage` envelopes; surfaces render them inline in the chat. The frontend never silently swallows backend errors (FP7's analog at the realtime layer).

## Testing

The reconciler is exercised by `CopilotContext.spec.tsx`. Key scenarios:
- Append a user message → assistant message ordering preserved.
- Receive a duplicate event → buffer unchanged (idempotence).
- Receive an out-of-order partial → reconciler waits / sorts correctly.
- Reconnect mid-turn → server replays; reconciler dedupes; buffer is identical.

New realtime envelopes require new test cases in this file.

## Open questions

1. **Per-session vs cross-session buffers.** Today buffer is per-browser-tab. Should it survive a page reload via local-storage hydration? Today: no — the server replays `recent_turns` on connect; local-storage hydration would add a state-conflict risk.
2. **Multi-tab synchronisation.** Today two tabs don't see each other's messages until the server replays. Should we broadcast within the browser via `BroadcastChannel`? Today: no — single-tab is the assumed usage; multi-tab is out of scope for V1.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial realtime-session doc. Singleton invariant, reconciler contract, closed message-kind family, turn lifecycle. |
