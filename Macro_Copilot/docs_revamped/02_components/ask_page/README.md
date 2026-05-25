# Ask Page

> The Ask page shell — `AskPage.tsx` + Composer + ConversationCanvas + per-message renderers. The chat surface. Hosts module Ask cards; owns no primitive-specific logic.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing page-shell contract. Changes to the per-message renderer dispatch require coordinated module-side updates.
**Operationalises principles:** [FP2](../../00_thesis/03_frontend_thesis.md), [FP12](../../00_thesis/03_frontend_thesis.md), [SI1](../frontend_infrastructure/README.md), P3.
**See also:** [`../frontend_module/README.md`](../frontend_module/README.md), [`../frontend_infrastructure/realtime_session.md`](../frontend_infrastructure/realtime_session.md).

---

## What this is

`AskPage` is the page shell for `/ask`. It is a layout + render-loop shell over `CopilotContext`'s message buffer. It does NOT contain primitive-specific logic.

The Ask surface composition:

```
┌─────────────────────────────────────┬──────────────────┐
│                                     │                  │
│  ConversationCanvas                 │  ContextRail     │
│    (renders messages in order)      │  (per-turn       │
│                                     │   context +      │
│  Composer                           │   threads list)  │
│    (input box, slash hints,         │                  │
│     scope chip)                     │                  │
│                                     │                  │
└─────────────────────────────────────┴──────────────────┘
```

ContextRail is a sibling of ConversationCanvas; it surfaces the active turn's routing decisions, working set, threads list. ThreadsRail / DrawerHints are sibling concerns.

## Per-message renderer dispatch

ConversationCanvas iterates `messages[]` from `CopilotContext` and dispatches each `CopilotMessage` to its renderer based on the message kind. The dispatch table:

| Message kind | Renderer | Path |
|---|---|---|
| `UserMessage` | `UserMessage.tsx` | `src/components/ask/messages/` |
| `ThinkingMessage` | `ThinkingState.tsx` | same |
| `RoutingMessage` | `RoutingStrip.tsx` | same |
| `ToolTraceMessage` | `ToolTrace.tsx` | same |
| `WorkflowResultMessage` | `AssistantResearchCard.tsx` (generic) OR module's `AskCard.tsx` (if claimed) | same / module folder |
| `AssistantMessage` (prose) | `ProseAnswer.tsx` | same |
| `ActionRow` (open-in-build CTA) | `ActionRow.tsx` | same |
| `FollowUps` | `FollowUps.tsx` | same |
| `ProvenanceRow` | `ProvenanceRow.tsx` | same |
| `ErrorMessage` | inline error treatment in ConversationCanvas | same |

Per FP12, the dispatch table is in `ConversationCanvas.tsx`; it dispatches on *message kind*, not on tool name. Per-tool specialisation for `WorkflowResultMessage` happens via a module-spec lookup: `MODULE.surfaces.ask` if claimed, else `AssistantResearchCard`.

## How a module contributes to Ask

A module contributes to Ask through one path:

| Module declaration | Ask behaviour |
|---|---|
| `tiers ∋ ask_surface` AND `MODULE.surfaces.ask` set | `ConversationCanvas` renders this module's `AskCard.tsx` for `WorkflowResultMessage`s whose tool matches `MODULE.toolName`, instead of the generic `AssistantResearchCard`. |
| Default (most modules) | Generic `AssistantResearchCard` renders the result. |

A module's `AskCard` receives the same `AskCardProps` shape (per SI4) so the page shell does not need to know module specifics.

## ContextRail

Path: `src/components/ask/ContextRail.tsx`

Renders alongside the conversation, surfacing:
- The current turn's routing decision (which template / which domain).
- The working-set (named artifacts the chat has accumulated).
- The threads list (recent conversations).

ContextRail does NOT branch on tool name. It dispatches on routing decision kind + working-set artifact kind.

## Composer

Path: `src/components/ask/Composer.tsx`

The input box. Owns its local state (draft text, cursor). Submits via `CopilotContext.sendMessage`. Does NOT submit through HTTP — chat always flows through the WebSocket.

The composer carries a scope chip (currently fixed to "rates"; future expansion to FX / credit) and slash hints (a small affordance for canned starter prompts). Neither is primitive-specific.

## What AskPage does NOT do

- **Branch on tool name in the render loop.** A `switch (toolName)` in `ConversationCanvas` is an FP12 violation.
- **Manage WebSocket state directly.** All chat state lives in `CopilotContext` (the singleton from `App.tsx`).
- **Compute analytical values.** Per FP9; chat renders backend values.
- **Persist conversation history.** The server's `recent_turns` is the source of truth; AskPage just renders.

## Open questions

1. **Slash-commands.** The composer has placeholder slash-hint UI. Should slash-commands become first-class (typed actions that bypass the LLM router)? Today: no — the LLM router handles everything; slash-hints are sugar.
2. **Inline workspace previews.** Should ActionRow's "Open in Build" expand into an inline preview before the user clicks through? Today: no — the inline expand would double the chat surface's information density; we keep the chat focused and let Build be the deep-dive.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial Ask page-shell doc. Per-message renderer dispatch table, module contribution path, ContextRail / Composer scope. |
