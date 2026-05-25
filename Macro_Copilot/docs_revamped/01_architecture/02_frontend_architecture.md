# Frontend Architecture

> The frontend's analog of [`00_internal_architecture.md`](00_internal_architecture.md). High-level layout, data flow, page-shell composition, module assembly, registry derivation. Reference architecture for any frontend contribution.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing. Changes require an ADR in [`../05_decisions/`](../05_decisions/) and coordinated updates to [`../02_components/frontend_module/README.md`](../02_components/frontend_module/README.md) where the module contract is affected.
**Operationalises principles:** [FP1–FP13](../00_thesis/03_frontend_thesis.md), backend P3 (consistency by contract), P9 (finance-blind layer), P10 (single source of truth), P11 (domain isolation).
**See also:** [`00_internal_architecture.md`](00_internal_architecture.md) (backend), [`../00_thesis/03_frontend_thesis.md`](../00_thesis/03_frontend_thesis.md) (frontend thesis), [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md) (architectural decision).

---

## Where the frontend sits

The platform's five-layer architecture, from [`00_internal_architecture.md`](00_internal_architecture.md):

```
L1  Data substrate         (instrument_master, time_series, event_calendar, ...)
L2  Primitives             (rates_agent/<sub_agent>/tools/<name>/)
L3  Operators              (shared/operators/<name>/)
L4  Workflow substrate     (shared/workflow/)
L5  Workflow templates     (rates_agent/workflows/<template>/)
─────────────────────────  ← API boundary (FastAPI + WebSocket at orchestrator)
L6  Frontend               (UI/macro-copilot-dashboard-polished/)
```

L6 is the frontend. It is a single-page React application (Vite + TypeScript + Tailwind + React Router) that reads from the L5/L4/L3/L2 stack through a thin REST + WebSocket boundary at the orchestrator. **The frontend has no direct access to L1–L4**; every backend interaction goes through `/api/v1/...` or the `/ws/copilot_chat` socket.

## The layered frontend

The L6 frontend has its own internal layering:

```
L6.1  Module specs           (src/modules/{primitives,workflows}/<name>/module.ts)
       — pure-spec values declaring tier claims + display metadata + surface refs
L6.2  Module surfaces        (src/modules/.../<name>/surfaces/*.tsx)
       — per-module JSX for Build / Monitor / Ask / preview
L6.3  Central registries     (src/lib/{toolNames,modelRegistry,...}.ts)
       — derived sets / dicts assembled from L6.1
L6.4  Shared infrastructure  (src/components/{shared,ui}/, src/hooks/, src/services/,
                              src/types/, src/context/)
       — finance-blind toolkit: render shells, DAG renderer, REST clients, WebSocket
L6.5  Page shells            (src/components/{layout,build,library,monitor,ask}/)
       — routing + layout + module-surface hosting
L6.6  Entry point            (src/main.tsx, src/App.tsx)
       — boot, providers, top-level router
```

The layering is enforced by import direction:
- **L6.1 imports L6.2, L6.4** (a module's spec references its surfaces and may import shared types).
- **L6.3 imports L6.1** only (registries derive from module specs).
- **L6.4** imports L6.4 (shared) only — no module imports, no page imports, no registry imports.
- **L6.5 imports L6.3 and L6.4** but NOT L6.2 directly (per FP12; the shell uses module-spec lookups).
- **L6.6 imports L6.5**.

A page shell directly importing a module's surface (`import { PcaBuildSurface } from '@/modules/primitives/.../surfaces/BuildSurface'`) violates FP12 and is a build error.

## The matrix model

The frontend's organisational principle is **modules × surfaces**:

```
Module                                ▼   Build  Monitor  Ask  Library*  DAG-node*
calculate_curve_spread_tool             ●    ·     ●        ●          ●
calculate_cross_market_spread_tool      ●    ·     ●        ●          ●
calculate_butterfly_tool                ●    ·     ●        ●          ●
get_yield_levels_tool                   ●    ●     ●        ●          ●
classify_curve_move_tool [WI]           ●    ●     ●        ●          ●
scan_extremes_tool                      ●    ●     ●        ●          ●
calculate_pca_yield_curve_tool          ●    ·     ●        ●          ●
calculate_rolling_regression_tool       ●    ·     ●        ●          ●
calculate_yield_change_attribution_pca  ●    ·     ●        ●          ●
calculate_half_life_tool                ●    ·     ●        ●          ●
calculate_beta_adjusted_spread_tool     ●    ·     ●        ●          ●
calculate_cpi_surprise_tool             ●    ●     ●        ●          ●
calculate_nfp_surprise_tool             ●    ●     ●        ●          ●
calculate_otr_ofr_spread_tool           ●    ·     ·        ●          ●
get_otr_history_tool [WI]               ●    ·     ·        ●          ●
calculate_wirp_meeting_pricing_tool [WI]●    ·     ·        ●          ●
...
                                      (* = automatic from shared infrastructure;
                                       a module does not need to claim Library
                                       or DAG-node tiers — they fall out from
                                       the manifest + shared renderer.)
```

- Each ● marks a tier the module claims via its `MODULE.tiers` field.
- Each row's tier set drives that module's contents (which files exist in `surfaces/`).
- Each column's set across rows drives a page shell's behaviour (which modules contribute to that surface).

The matrix is enforced by the per-module round-trip test (FP11) and the derived registries (FP4).

## Code map

### Top-level directories

```
UI/macro-copilot-dashboard-polished/
├── public/
├── src/
│   ├── main.tsx                       L6.6 — bootstrap
│   ├── App.tsx                        L6.6 — router + providers
│   │
│   ├── modules/                       L6.1 + L6.2 — the heart of the architecture
│   │   ├── index.ts                   — central loader (hand-maintained barrel)
│   │   ├── types.ts                   — PrimitiveModuleSpec, WorkflowModuleSpec, SurfaceTier
│   │   ├── primitives/
│   │   │   └── <tool_name>/           — one folder per backend primitive
│   │   │       ├── THESIS.md
│   │   │       ├── module.ts
│   │   │       ├── surfaces/
│   │   │       │   ├── BuildSurface.tsx
│   │   │       │   ├── PreviewWidget.tsx
│   │   │       │   ├── MonitorWidget.tsx
│   │   │       │   └── AskCard.tsx
│   │   │       ├── types.ts           — optional
│   │   │       └── __tests__/module.spec.ts
│   │   └── workflows/
│   │       └── <template_id>/         — one folder per backend workflow template
│   │           ├── THESIS.md
│   │           ├── module.ts
│   │           ├── surfaces/
│   │           │   └── ResultsDashboard.tsx
│   │           └── __tests__/module.spec.ts
│   │
│   ├── lib/                           L6.3 — central registries (derived)
│   │   ├── toolNames.ts               KNOWN_BACKEND_TOOLS, RUNNABLE_PRIMITIVE_TOOLS,
│   │   │                              WORKFLOW_INCOMPATIBLE_TOOLS, UNSUPPORTED_KNOWN_TOOLS,
│   │   │                              KNOWN_WORKFLOWS, PAUSED_WORKFLOWS, normalizeToolName
│   │   ├── modelRegistry.ts           MODELS (derived); paramHintFor, inferFieldControl
│   │   └── chart.ts                   — shared chart utilities (finance-blind)
│   │
│   ├── components/                    L6.4 + L6.5
│   │   ├── shared/                    L6.4 — finance-blind render shells
│   │   │   ├── render/                — AutoRenderer, RichModelWidget, typed views
│   │   │   ├── dag/                   — DagStrip, deriveDagNodes, parseWorkflowLineage
│   │   │   └── bento/                 — WidgetGrid, WidgetCard, WidgetRenderer
│   │   ├── ui/                        L6.4 — atoms (Sparkline, Card, Modal, ...)
│   │   ├── layout/                    L6.5 — AppShell, Sidebar, TopNav, ChatDrawer
│   │   ├── build/                     L6.5 — BuildShell + canvas modes (empty/building/completed)
│   │   ├── library/                   L6.5 — LibraryPage + chips/strips/search/grid/drawer
│   │   ├── monitor/                   L6.5 — MonitorPage + layout state + provider
│   │   └── ask/                       L6.5 — AskPage + Composer + ConversationCanvas + messages
│   │
│   ├── context/                       L6.4 — CopilotContext (singleton WebSocket)
│   ├── hooks/                         L6.4 — useRatesData, useWorkspaceDetail, ...
│   ├── services/                      L6.4 — ratesApi, workflowsApi, libraryApi, workspaceApi
│   ├── types/                         L6.4 — wire types mirroring backend Pydantic schemas
│   └── utils/                         L6.4 — cn (className helper)
│
├── package.json
├── tsconfig.json
├── vite.config.ts
└── tailwind.config.ts
```

The strict invariant: **page-shell folders (`build/`, `library/`, `monitor/`, `ask/`) contain NO per-primitive code.** They host modules' surfaces; they do not own them. (FP2, FP12.)

## Data flow — Library

```
Backend                                  Frontend
─────────                                ────────
manifesto/03_tool_manifest/              GET /api/v1/library/manifest
  rates_agent/                              ↓
    01_sovereign_bonds_manifest.yml      ManifestResponse
    02_ois_manifest.yml                     ↓
    03_bond_futures_manifest.yml         useLibraryManifest hook
    04_inflation_indexed_bonds_manifest     ↓
    05_inflation_swaps_manifest.yml      LibraryPage
    06_policy_futures_manifest.yml          ├ AgentStrip       (agent picker)
                                            ├ InstrumentStrip  (sub-agent filter)
                                            ├ CategoryChips    (category filter)
                                            ├ LibrarySearch    (free-text)
                                            ├ ToolCardGrid     (cards from manifest)
                                            └ ToolDetailDrawer (per-tool drawer)
```

LibraryPage is fully manifest-driven. A new manifest entry on the backend appears in the Library automatically. Per FP4, the module spec does NOT control whether a tool appears in the Library — the backend manifest does. Modules contribute to Library only by *existing* (a tool with no module still appears in the Library as a manifest entry).

What modules contribute to Library: when a user clicks "Open in Build" from the drawer, the routing goes through `contextDecoder` which reads the central registries (derived from modules). Whether the click lands on a typed view, a generic builder, a workflow-incompatible card, or a paused card is module-driven.

## Data flow — Ask → Build

```
User types in Ask Composer
         ↓
WebSocket /ws/copilot_chat  ──→  Backend supervisor + child + workflow router
         ↓
WebSocket message buffer (CopilotContext)
         ↓
ConversationCanvas renders messages/ per-message-type:
  ├ UserMessage
  ├ ThinkingState
  ├ RoutingStrip          (which template / which domain)
  ├ DagStrip              (workflow DAG view)
  ├ AssistantResearchCard (per-tool result card — generic)
  ├ ToolTrace             (per-tool call trace)
  ├ ProseAnswer
  ├ ActionRow             ("Open in Build" CTA)
  ├ FollowUps             (suggested next prompts)
  └ ProvenanceRow
         ↓
User clicks "Open in Build"
         ↓
ActionRow.resolveBuildHref builds /workspace?context=<encoded>
         ↓
URL navigation                  ──→  BuildShell
         ↓
BuildShell.SlugFreeShell reads ?context
         ↓
contextDecoder.decodePrimitiveContext(raw)
  ├ normalizeToolName              (resolves manifest aliases)
  ├ if hasModelMetadata(tool)      → kind: 'builder'         (rich-model)
  ├ else if TOOL_TO_VIEW[tool]     → kind: <view>            (typed view)
  ├ else if isRunnablePrimitive(tool) → kind: 'generic_builder'
  ├ else if isWorkflowIncompatible(tool) → kind: 'workflow_incompatible'
  ├ else if isKnownBackendTool(tool) → kind: 'unsupported_known'  (paused)
  └ else                           → null                    (decode-error)
         ↓
BuildShell renders the matching canvas
```

Every decision in `contextDecoder` reads from the central registries. The central registries are derived from `ALL_PRIMITIVE_MODULES`. Therefore every routing decision traces back to module tier claims.

The `kind: 'workflow_incompatible'` branch is new (added per ADR 0014); pre-ADR there was no honest tier for shipped-but-bridge-incompatible primitives.

## Data flow — Build canvas selection

BuildShell at `/workspace` (no slug) chooses among:

```
?builder=<toolName>         → BuilderCanvas (rich-model playground)
?workflow=<templateId>      → WorkflowStatusCanvas (paused/unavailable)
?context=<encoded>          → ContextCanvasRouter
   ├ single-tool result      → VirtualPrimitiveCanvas
   │  ├ typed view              (Spread / CrossMarket / Butterfly / Yield / Regime / Scanner)
   │  ├ generic_builder         (GenericPrimitiveBuilder + AutoRenderer)
   │  ├ workflow_incompatible   (module's BuildSurface OR honest paused card)
   │  ├ unsupported_known       (paused card with per-tool reason)
   │  └ null                    (decode-error orange card)
   └ multi-tool result       → MultiPrimitiveCanvas (N cards side-by-side)
isBuilding (pending prompt)  → BuildBuilding
default                      → BuildEmptyState
```

BuildShell at `/workspace/:slug` always renders `BuildCompleted` against the workspace persisted at `slug`. The workspace replay carries the full workflow lineage; per-node rendering goes through the node-renderer registry (per-artifact-type + per-tool overrides) — which is itself derived from module specs (per FP4).

## Data flow — Monitor

```
RatesDataProvider (lifted to AppShell for sidebar consumption)
         ↓
useRatesData() — calls /api/v1/rates/* aggregated endpoints
         ↓
MonitorPage / RatesAgentPage
         ↓
WidgetGrid
         ↓
each WidgetCard dispatches by widget id  → WidgetRenderer
                                               ↓
                                          Monitor widget (per-module, claimed via monitor_surface tier)
```

The widget catalogue (`WIDGET_TYPES`) is derived from modules' `monitor_surface` tier claims. A module that does not claim `monitor_surface` does not appear in the widget catalogue. A user's persisted layout that references a deleted widget is filtered out on read (defensive).

## The realtime session

`CopilotContext` (`src/context/CopilotContext.tsx`) is a singleton React provider mounted above `AppShell` (in `App.tsx`). It owns:

- One WebSocket connection to `/ws/copilot_chat`.
- A message buffer (`messages: CopilotMessage[]`).
- The `sendMessage(content)` action.
- `isThinking`, `connectionStatus`, and trace-step reconciliation.

Every surface that needs realtime data reads from `useCopilotContext()`. Two surfaces consume it heavily:
- `AskPage` — primary consumer (chat is the WebSocket's home).
- `BuildShell.SlugFreeShell` — secondary consumer (the empty-state composer routes through the same WebSocket so a prompt in Build behaves identically to a prompt in Ask).

The WebSocket is a SINGLETON. Two surfaces mounting the same provider see the same buffer; this is intentional (a prompt sent in Build appears in Ask's history seamlessly).

## Central registries — what's derived from where

After Stage N of the migration ([`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md)):

| Registry | Derived from | Type |
|---|---|---|
| `KNOWN_BACKEND_TOOLS` | `ALL_PRIMITIVE_MODULES.map(m => m.toolName)` | `ReadonlySet<string>` |
| `RUNNABLE_PRIMITIVE_TOOLS` | modules where `tiers ∋ generic_runnable` | `ReadonlySet<string>` |
| `WORKFLOW_INCOMPATIBLE_TOOLS` | modules where `tiers ∋ workflow_incompatible` | `ReadonlySet<string>` |
| `UNSUPPORTED_KNOWN_TOOLS` | modules where `tiers ∋ paused` | `ReadonlySet<string>` |
| `UNSUPPORTED_KNOWN_REASONS` | per-module `unsupportedReason` field | `Record<string, UnsupportedKnownReason>` |
| `KNOWN_TOOL_ALIASES` | per-module `aliases` field (rare; for manifest shorthand vs canonical mismatch) | `Record<string, string>` |
| `KNOWN_WORKFLOWS` | workflow modules where `tiers ∋ active` | `ReadonlySet<string>` |
| `PAUSED_WORKFLOWS` | workflow modules where `tiers ∋ paused` | `ReadonlySet<string>` |
| `MODELS` (modelRegistry) | per-primitive-module `richModel` field | `ModelMetadata[]` |
| `TOOL_TO_VIEW` (contextDecoder) | per-primitive-module `typedView` field | `Record<string, PrimitiveViewKind>` |
| `WIDGET_TYPES` (monitor catalogue) | modules where `tiers ∋ monitor_surface` | `Record<string, WidgetTypeMeta>` |
| Node-renderer registry (per-tool) | modules where `tiers ∋ custom_preview_widget` | runtime registrations |
| Dashboard registry (per-workflow) | workflow modules' `surfaces.results` | runtime registrations |

The derivation is pure: a function from `ALL_PRIMITIVE_MODULES` and `ALL_WORKFLOW_MODULES` to each registry. No mutation. The registries can be regenerated at any time without producing side effects.

See [`../02_components/frontend_registries/README.md`](../02_components/frontend_registries/README.md) for the full derivation contract.

## Routing

```
React Router routes (in src/App.tsx via AppShell):

/                        → MonitorPage
/rates                   → RatesAgentPage
/fx                      → FxAgentPlaceholder
/credit                  → CreditAgentPlaceholder
/macro-equity            → MacroEquityPlaceholder
/policy                  → PolicyEventsPlaceholder
/pm-orchestrator         → PmOrchestratorPlaceholder
/workspace               → BuildShell (no slug — empty / building)
/workspace/:slug         → BuildShell (slug-bound — completed)
/workflows               → WorkflowsCataloguePage (legacy)
/library                 → LibraryPage
/tools                   → redirect to /library (legacy alias)
/ask                     → AskPage
/briefcase               → BriefcasePlaceholder
/events                  → redirect to /policy
*                        → redirect to /
```

AppShell picks one of three layout shapes per route:
- **Full-width** (Ask, Briefcase, Library, Build): `<main>{Routed}</main>` only.
- **Sidebar + main** (Monitor, Rates Agent, agent placeholders): two-column grid with persistent Sidebar.
- **Legacy three-column** (Workflows catalogue): sidebar + main + ChatDrawer.

The TopNav is mounted ONCE at the very top of the shell, not per-layout, so the brand glyph stays anchored across route transitions.

## API boundary

The frontend talks to the backend through exactly these surfaces:

| Surface | Used for |
|---|---|
| `GET /api/v1/library/manifest` | Library catalogue (LibraryPage) |
| `GET /api/v1/tools/{name}` | ToolCard for one tool (Library drawer + GenericPrimitiveBuilder) |
| `POST /api/v1/tools/{name}/run` | Run a runnable primitive (GenericPrimitiveBuilder + BuilderCanvas) |
| `GET /api/v1/rates/yield-snapshot` etc. | Pre-aggregated rates monitor widgets |
| `GET /api/v1/rates/detail/{type}` | Typed primitive detail (Spread / CrossMarket / Butterfly / Yield / Scanner / Regime) |
| `GET /api/v1/workflows/catalogue` | Workflows catalogue (legacy) |
| `POST /api/v1/workflows/run` | Run a workflow template |
| `GET /api/v1/workspace/{slug}` | Workspace detail + replay (BuildCompleted) |
| `GET /api/v1/artifacts/{hash}/payload` | Artifact payload (DAG node fetch) |
| `GET /api/v1/artifacts/{hash}/replay` | Artifact replay (provenance reconstruction) |
| `WS /ws/copilot_chat` | Realtime chat (CopilotContext) |

Every service file in `src/services/` wraps one of these endpoint families. Per FP13, services are domain-blind — they pass strings through. Per FP9, they do not recompute.

## What changes at each layer when a primitive is added

| Backend action | L6.1 change | L6.3 change | L6.4 change | L6.5 change |
|---|---|---|---|---|
| Add primitive to `_PRIMITIVE_SPECS` | New module folder with `tiers: ['generic_runnable', ...]` | No change (derived) | No change | No change |
| Add manifest entry | No change (LibraryPage reads manifest) | No change | No change | No change |
| Add primitive with new output shape | Module ships custom `BuildSurface` if needed | No change | Possibly new wire type | No change |
| Add new typed-detail endpoint | Module declares `typedView: 'newKind'` | TOOL_TO_VIEW gains entry (derived) | New typed view component in `shared/render/` | No change |
| Add bond_futures sub-agent | (No frontend work for sub-agent itself; LibraryPage's InstrumentStrip auto-shows new sub-agent if SUB_AGENT_LABELS knows it) | No change | Possibly new SUB_AGENT_LABELS entry | No change |

The principle: **adding a primitive on the backend should produce a frontend diff localised to one module folder + at most one line in the central loader at `src/modules/index.ts`.** Anything more is a smell.

## Browser-side state

State lives in three places, in increasing scope:

1. **Component state** (`useState`) — ephemeral UI state (form values, hover, expanded/collapsed).
2. **URL state** (`useSearchParams`) — bookmarkable / shareable state (active workspace, context, builder selection, form pre-fills).
3. **Provider state** (`CopilotContext`, `RatesDataProvider`, `WorkspaceOverridesContext`) — cross-surface state (WebSocket buffer, rates fetch, workspace overrides queue).

There is no Redux. There is no MobX. Providers are React Context, hand-tuned.

Workspaces persist on the backend (`POST /api/v1/workspace`); user-side state for a workspace (overrides queue, pinned-run comparisons) lives in `WorkspaceOverridesContext` until it's submitted.

Local-storage persistence is used for:
- Monitor widget layouts (per the `LAYOUT_VERSION` schema in [`monitor/registry.ts`](../../UI/macro-copilot-dashboard-polished/src/components/monitor/registry.ts)).
- Recent workspace navigation (in the Sidebar's Today panel).

Local storage is NOT used for analytical state — every analysis is backend-persisted via workspace.

## Build + tooling

- **Vite** for dev server + production bundle.
- **TypeScript strict** — no implicit any; modules' `module.ts` is fully type-checked.
- **Tailwind** for styles — no CSS files outside the shared `index.css`.
- **Vitest** for unit + contract tests. The per-module round-trip test (FP11) runs under vitest.
- **Playwright** for E2E (used sparingly; reserved for cross-surface flow tests like Ask → Build).
- **ESLint** — at minimum, a custom rule that disallows `from '@/modules/'` imports inside `src/components/{build,library,monitor,ask,layout}/**` (FP12 enforcement).

## How this architecture changes

Architectural-level changes follow the same procedure as the thesis ([`../00_thesis/03_frontend_thesis.md`](../00_thesis/03_frontend_thesis.md#how-this-thesis-changes)): ADR first, then the architecture doc, then any contract update, in one coordinated PR.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1 | 2026-05-25 | Initial frontend architecture. Documents the post-ADR-0014 layered shape (L6.1–L6.6), data flows for Library / Ask→Build / Build canvas / Monitor / realtime, registry derivation table, API boundary, browser-side state model. | [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md) |
