# Build Page

> The Build page shell — `BuildShell.tsx` + canvas modes (empty / building / completed) + 3-column layout. Hosts modules' Build surfaces; owns no primitive-specific logic.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing page-shell contract. Changes that affect module Prop contracts require an ADR.
**Operationalises principles:** [FP2](../../00_thesis/03_frontend_thesis.md), [FP12](../../00_thesis/03_frontend_thesis.md), [SI4](../frontend_infrastructure/README.md), P3.
**See also:** [`../frontend_module/README.md`](../frontend_module/README.md), [`../frontend_infrastructure/render_shells.md`](../frontend_infrastructure/render_shells.md).

---

## What this is

`BuildShell` is the page shell for `/workspace` and `/workspace/:slug`. It is a routing + layout shell. It does NOT contain primitive-specific logic.

The 3-column grid:

```
┌──────────────┬────────────────────────────────────┬────────────────┐
│              │                                    │                │
│  Workspaces  │         Canvas                     │   Copilot      │
│  Sidebar     │         (empty / building /        │   Rail         │
│              │          completed / canvas)       │                │
│              │                                    │                │
└──────────────┴────────────────────────────────────┴────────────────┘
```

The shape is stable across all canvas modes; only the centre column's content swaps.

## State machine

```
URL = /workspace            (no slug)
       │
       ├─ ?builder=<name>      → BuilderCanvas (rich-model playground)
       ├─ ?workflow=<id>       → WorkflowStatusCanvas (paused / unavailable)
       ├─ ?context=<encoded>   → ContextCanvasRouter
       │                          ├─ decode single → VirtualPrimitiveCanvas
       │                          └─ decode multi  → MultiPrimitiveCanvas
       ├─ pending prompt       → BuildBuilding
       └─ default              → BuildEmptyState (composer)

URL = /workspace/:slug      (slug-bound)
       │
       └─ → BuildCompleted (the persisted workspace)
```

## Page-shell minimality contract (FP12 enforcement)

BuildShell.tsx MAY import:
- Shared infrastructure (render shells, hooks, services, types, atoms, the realtime context, the WorkspaceOverridesProvider).
- Central registries (via the contextDecoder which reads derived sets).
- Module-spec lookups (`getPrimitiveModule(toolName)`).

BuildShell.tsx MAY NOT import:
- A specific module's surface file (`@/modules/primitives/<name>/surfaces/...`).

The contextDecoder + the module-spec lookup are the *only* paths from BuildShell to a module's JSX. The decoder returns a `kind`; the kind is dispatched to a shared shell (typed view / generic builder / rich-model BuilderCanvas / unsupported card) which then internally consults `MODULE.surfaces.<key>` if the module claims a custom capability.

## Canvas modes

### BuildEmptyState

Path: `src/components/build/empty/BuildEmptyState.tsx`

Renders the empty-state composer + sample prompts. Sends user input via `CopilotContext.sendMessage`. Transitions to `BuildBuilding` when `isThinking` and a pending prompt exist.

### BuildBuilding

Path: `src/components/build/building/BuildBuilding.tsx`

Renders progress indicators while the backend turn is in flight. Reads from `CopilotContext.messages` to surface routing decisions / tool traces. Transitions away when a workflow_result with a workspace.slug lands → BuildShell navigates to `/workspace/:slug`.

### BuildCompleted

Path: `src/components/build/completed/BuildCompleted.tsx`

The slug-bound view. Reads workspace detail via `useWorkspaceDetail`. Renders:
- The DAG strip (via shared `DagStrip`).
- The bento-card view of each persisted artifact (via the node-renderer registry; per-tool entries come from modules' `custom_preview_widget` claims).
- The workspace's title, methodology card, parameters tab, notes tab.

### VirtualPrimitiveCanvas

Path: `src/components/build/primitive/VirtualPrimitiveCanvas.tsx`

Renders a single decoded primitive context. Dispatches on the decoder's `kind`:
- `builder` → redirects to `?builder=<toolName>` (BuilderCanvas mounts).
- typed-view kind → mounts the matching shared typed view (SpreadView, etc.).
- `generic_builder` → mounts `GenericPrimitiveBuilder`.
- `workflow_incompatible` / `unsupported_known` → mounts `UnsupportedKnownToolCanvas` with the module's `unsupportedReason`.
- `null` (decode error) → mounts the orange "Could not decode workspace context" card. **Reserved for truly-unknown tools per FP7.**

### MultiPrimitiveCanvas

Path: `src/components/build/primitive/MultiPrimitiveCanvas.tsx`

Renders N decoded primitives side-by-side (R6.3 multi-card grid). Each card dispatches independently per the same rules as VirtualPrimitiveCanvas.

### BuilderCanvas

Path: `src/components/build/model/BuilderCanvas.tsx` (shared infrastructure, not page-shell)

The rich-model playground. BuildShell mounts it when `?builder=<toolName>` is present. See [`../frontend_infrastructure/render_shells.md`](../frontend_infrastructure/render_shells.md#buildercanvas) for the contract.

### WorkflowStatusCanvas

Path: `src/components/build/workflow/WorkflowStatusCanvas.tsx`

Renders an honest paused / unavailable workflow card when Ask hands off a workflow turn with a recognised `template_id` but no persisted workspace slug.

## How a module contributes to Build

A module contributes to Build through these mechanical paths:

| Module declaration | Build behaviour |
|---|---|
| `tiers ∋ generic_runnable` | Opening from Library → `GenericPrimitiveBuilder` (no module surface needed). |
| `tiers ∋ custom_build_surface` AND `MODULE.surfaces.build` set | Opening from Library → mounts the module's `BuildSurface`. |
| `richModel === true` | Opening from Library → redirects to `/workspace?builder=<toolName>` → `BuilderCanvas` mounts. |
| `typedView === '<kind>'` | Opening from Library → `VirtualPrimitiveCanvas` mounts the matching typed view. |
| `tiers ∋ custom_preview_widget` AND `MODULE.surfaces.preview` set | `BuildCompleted`'s per-artifact bento cards use this widget instead of the generic per-type widget. |
| `tiers ∋ workflow_incompatible | paused | deferred` | Opening from Library → `UnsupportedKnownToolCanvas` with the module's `unsupportedReason`. |

The mounting decision is in `contextDecoder.ts`; the priorities are documented there. Per FP12, BuildShell itself does NOT branch — it delegates to the decoder + module-spec lookup.

## What BuildShell does NOT do

- **Branch on tool name.** A `switch (toolName)` in BuildShell is a FP12 violation.
- **Compute analytical values.** Per FP9; BuildShell renders the backend's values.
- **Own per-primitive layout.** Modules own that via their `surfaces/`.
- **Run primitives directly.** Shared shells (GenericPrimitiveBuilder, BuilderCanvas) own running; BuildShell mounts them.

## Open questions

1. **Lazy-loading the workspace replay.** Today `useWorkspaceDetail` fetches detail + replay eagerly. Should replay be lazy until the user opens the DAG strip? Today: eager — replay is small. Revisit when workspaces with many nodes appear.
2. **Per-module URL parameter conventions.** Today URL params are flat-string for the generic builder, structured for rich-model. Should we standardise? Today: no — the asymmetry is intentional (typed-detail endpoints accept only scalar query params).

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial Build page-shell doc. State machine, canvas modes, FP12 enforcement, module-contribution table. |
