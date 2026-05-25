# Frontend Infrastructure

> The contract for L6.4 — the finance-blind toolkit every module slots into. Render shells, the DAG renderer, the bento grid, the WebSocket session, hooks, services, types, UI atoms. Counterpart to backend's [`../operator/README.md`](../operator/README.md) — both layers are domain-blind by design.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing component contract. Changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Operationalises principles:** [FP9](../../00_thesis/03_frontend_thesis.md) (no client-side compute), [FP13](../../00_thesis/03_frontend_thesis.md) (finance-blind shared layer), [FM7](../frontend_module/README.md) (pure-spec assembly), P3 (consistency by contract), P9 (finance-blind boundary — frontend's analog: the shared layer is finance-blind, modules are finance-aware), P10 (single source of truth — the shared layer's contracts).
**See also:** [`render_shells.md`](render_shells.md), [`dag_renderer.md`](dag_renderer.md), [`realtime_session.md`](realtime_session.md), [`services_and_types.md`](services_and_types.md), [`../frontend_module/README.md`](../frontend_module/README.md).

---

## What this folder is

This folder contains the contract every piece of frontend shared infrastructure must satisfy. Shared infrastructure is the *finance-blind* layer between modules (finance-aware) and the backend API. It owns:

- **Render shells** — generic widget chrome that multiple modules slot into (AutoRenderer, RichModelWidget, GenericPrimitiveBuilder, typed views).
- **DAG renderer** — workflow lineage visualisation, finance-blind.
- **Bento grid** — Monitor surface's layout engine.
- **Realtime session** — singleton WebSocket + message buffer (CopilotContext).
- **Hooks** — cross-surface data hooks.
- **Services** — REST clients to `/api/v1/...`.
- **Types** — wire types mirroring backend schemas.
- **UI atoms** — Sparkline, Card, Modal, primitives the design system reuses.

## What shared infrastructure is NOT

- **Not a module.** Shared infrastructure has no `THESIS.md`, no tier claims, no per-tool branching. It's the toolkit modules use.
- **Not domain-aware.** A render shell that branches on `if (toolName === 'calculate_pca_yield_curve_tool')` is a contract violation (FP13). The branching belongs in the module.
- **Not a page shell.** Page shells (`BuildShell`, `LibraryPage`, etc.) live in `src/components/{build,library,...}/` and are documented in [`../build_page/`](../build_page/), [`../library_page/`](../library_page/), etc. Shared infrastructure is consumed by page shells AND by modules; it sits one layer down.

## Quick index — the SI-numbers

| ID | Principle | One-line rule |
|---|---|---|
| **SI1** | Domain-blind dispatch | Shared infrastructure dispatches on shape (artifact type, schema kind, slot kind), never on tool name. |
| **SI2** | No client-side compute | Shared infrastructure renders backend values verbatim; display affordances do not replace numbers (FP9 analog). |
| **SI3** | One concept, one shared shell | Each render concept (AutoRenderer, RichModelWidget, typed primitive view, generic builder, BuilderCanvas) has exactly one canonical implementation. |
| **SI4** | Stable Prop contracts | Each shared component declares a stable, typed Prop interface. Prop changes are coordinated with module-surface updates (FM8). |
| **SI5** | Service / type boundary | One service file per `/api/v1/...` route family; one type file per wire-shape family; no cross-mixing. |
| **SI6** | Hooks are read-side | Hooks read data, manage local state, surface loading/error. They do not push side effects (e.g., a hook does not `fetch` AND `mutate global state` from one hook call). |
| **SI7** | Atoms have no domain meaning | UI atoms (Sparkline, Card, Modal) accept generic typed props; they do not import from `@/modules/` or `@/lib/toolNames`. |

---

## SI1 — Domain-blind dispatch

**Rule.** Shared infrastructure components dispatch on *shape*, not on *tool name*. The canonical dispatch axes are:

- **Artifact type** (`Series` / `Panel` / `SeriesSet` / `EventSet` / `WindowedPanel` / `TradeSet`) for output renderers.
- **DAG node kind** (`primitive` / `operator` / `terminal_artifact`) for DAG-node renderers.
- **Field-schema type + name pattern** (`curve_family` / `tenor` / `lookback_days` / `date` / `field_name`) for input controls.
- **Slot kind** (`int` / `float` / `str` / `bool` / `dict` / `list`) for workflow-template slot controls.

A `switch (toolName)` branch inside shared infrastructure is a contract violation. Per-tool branching belongs in the module.

**Verify.** `grep -rE "(calculate|get|scan|build|classify|policy_futures_get)_[a-z0-9_]+_tool" src/components/{shared,ui}/ src/hooks/ src/services/ src/types/ src/lib/chart.ts` returns ZERO matches.

**Anti-patterns.**
- AutoRenderer dispatching `if (toolName === 'X') return <XSpecificLayout />`.
- A typed primitive view (SpreadView) that branches per primitive instead of per output shape.
- A hook returning different data shapes per `toolName`.

**Exceptions.** None. Per-tool specialisation lives in the module's `surfaces/`.

## SI2 — No client-side compute

**Rule.** Shared infrastructure renders backend values verbatim. Display affordances (sparkline scaling, percent formatting, sort orders, threshold colouring, decimal rendering) are visual transformations; they do NOT replace a backend number with a client-computed one.

Examples of allowed display affordances:
- `Math.max(...sparklineData)` to set the sparkline's vertical scale.
- Formatting a number to 2 decimals when the backend's `rounding_decimals` is 2.
- Sorting a list of bonds by `yield_mid` descending — the values come from the backend, only the order is client-side.

Examples of forbidden client-side compute:
- Recomputing z-scores client-side.
- Computing percent changes between two backend values.
- Aggregating a backend Series into a custom statistic.

**Verify.** No `Math.*` call in shared infrastructure produces a numeric value the user reads as a primary metric. Aggregations (`Math.max`, `reduce`) used only for visual transforms (scale, colour) are fine.

**Anti-patterns.** See FP9 anti-patterns in [`../../00_thesis/03_frontend_thesis.md`](../../00_thesis/03_frontend_thesis.md#fp9--no-client-side-compute).

**Exceptions.** Editor affordances during form input (e.g. preview of "days between two picked dates") are not analytical values; they're editor sugar.

## SI3 — One concept, one shared shell

**Rule.** Each render concept has exactly one canonical implementation:

| Concept | Canonical shell |
|---|---|
| Per-artifact-type output rendering | `AutoRenderer` (in `src/components/shared/render/`) |
| Rich-model preview card (PCA, regression, attribution, half-life, beta-adjusted) | `RichModelWidget` |
| Generic schema-driven primitive builder | `GenericPrimitiveBuilder` |
| Rich-model builder playground | `BuilderCanvas` + `OutputCanvas` |
| Typed primitive view (curve spread shape) | `SpreadPrimitiveView` |
| Typed primitive view (cross-market spread shape) | `CrossMarketPrimitiveView` |
| Typed primitive view (butterfly shape) | `ButterflyPrimitiveView` |
| Typed primitive view (yield level shape) | `YieldPrimitiveView` |
| Typed primitive view (scanner) | `ScannerPrimitiveView` |
| Typed primitive view (regime classifier) | `RegimePrimitiveView` |
| Typed primitive view (forward — placeholder) | `ForwardPrimitiveView` |
| DAG strip | `DagStrip` |
| Bento grid | `WidgetGrid` + `WidgetCard` + `WidgetRenderer` |

Adding a new shared shell requires:
1. Demonstrating that no existing shell can be parameterised to handle the new case.
2. Documenting the new shell's Prop contract.
3. Adding an entry to this table.

Duplicate or near-duplicate shared shells are a P10 violation (single source of truth).

**Verify.** A grep for shared-shell names finds one implementation per concept.

**Anti-patterns.**
- A "ButterflyPrimitiveView2" alongside "ButterflyPrimitiveView".
- A `GenericPrimitiveBuilderV2` introduced without retiring `GenericPrimitiveBuilder`.

**Exceptions.** During refactoring, two implementations may coexist temporarily, but the PR that introduces the new one MUST land the retirement of the old one within N+1 PRs.

## SI4 — Stable Prop contracts

**Rule.** Each shared component declares a typed Prop interface. The interface is the contract; module surfaces consume it. Changes to the interface are coordinated with module-surface updates per FM8.

Module-facing Prop interfaces live in `src/modules/types.ts`:

```ts
export type BuildSurfaceProps = {
  toolName: string;
  initialParams: Record<string, unknown>;
  workspaceContext?: WorkspaceContext;
  // ... stable shared fields ...
};

export type MonitorWidgetProps = {
  // ... stable shared fields ...
};

export type AskCardProps = {
  toolResult: ToolResult;
  // ... stable shared fields ...
};
```

Module-internal Prop interfaces (used inside `surfaces/<Name>.tsx`) follow the same shape and are declared once in `@/modules/types.ts`, not redeclared per module.

**Verify.** Each module-facing prop type is declared in exactly one place. Per-module surface files import the type, do not redeclare it.

**Anti-patterns.**
- A module surface declaring its own `Props` type that's a near-copy of `BuildSurfaceProps`.
- A module surface accepting custom props the standard interface doesn't carry.

## SI5 — Service / type boundary

**Rule.** Services and types map 1:1 to backend route families:

| Service file | Wraps |
|---|---|
| `src/services/ratesApi.ts` | `/api/v1/rates/...` (aggregated + typed-detail) |
| `src/services/workflowsApi.ts` | `/api/v1/workflows/...` + `/api/v1/tools/...` |
| `src/services/libraryApi.ts` | `/api/v1/library/...` |
| `src/services/workspaceApi.ts` | `/api/v1/workspace/...` + `/api/v1/artifacts/...` |
| `src/services/copilot.ts` | WebSocket `/ws/copilot_chat` (paired with `src/context/CopilotContext.tsx`) |

| Type file | Owns |
|---|---|
| `src/types/rates.ts` | Rates aggregated + typed-detail response shapes |
| `src/types/workflows.ts` | Workflow catalogue, ToolCard, PrimitiveRunResult, run params |
| `src/types/library.ts` | Library manifest response shape + category/sub-agent labels |
| `src/types/artifacts.ts` | Series / Panel / SeriesSet / EventSet / WindowedPanel / TradeSet wire shapes + ArtifactSummary |
| `src/types/copilot.ts` | WebSocket message envelopes (CopilotMessage, etc.) |

A service file MUST NOT call another backend route family. A type file MUST NOT import another type file's bespoke shape (shared scalar types like `IsoDate` are fine; cross-imports of complex shapes are a smell).

**Verify.** `grep -r "@/services/ratesApi" src/services/` returns matches only in `ratesApi.ts` itself. Cross-imports between service files are a contract violation.

**Anti-patterns.**
- A service file that "convenience-wraps" two route families.
- A type file that re-exports another type file's shape under a renamed alias.

**Exceptions.** Shared scalar types (`IsoDate`, `Currency`, `TenorString`, `CurveFamily`) live in `src/types/common.ts` and may be imported by any type file.

## SI6 — Hooks are read-side

**Rule.** Hooks (`useRatesData`, `useWorkspaceDetail`, `useLibraryManifest`, `useCopilot`, etc.) own one read concern. They do NOT bundle reads with mutations or registrations.

The mental model: a hook returns `{ data, isLoading, error, refetch }` (or an analogous shape). It does NOT also expose `mutate(...)` or `register(...)` — those are separate hooks or service calls.

**Verify.** A hook's return type is read-shaped. Mutations live in services + explicit action handlers; not in hooks.

**Anti-patterns.**
- A `useWorkspace` hook that returns both `detail` AND `createWorkspace(...)` — split into `useWorkspaceDetail` (read) + `useCreateWorkspace` (mutation).
- A hook that registers a global side effect on mount (the side effect belongs in a service or in the realtime session).

**Exceptions.** `useCopilot` is allowed to expose `sendMessage` because the WebSocket is fundamentally bidirectional; the read and write paths share state. This is a documented exception.

## SI7 — Atoms have no domain meaning

**Rule.** UI atoms (`Sparkline`, `Card`, `Modal`, `Badge`, `Tooltip`, etc.) accept generic typed props. They MUST NOT:
- Import from `@/modules/`.
- Import from `@/lib/toolNames` or `@/lib/modelRegistry`.
- Branch on backend identifiers.

Atoms are the lowest-level finance-blind layer; they shape pixels.

**Verify.** `grep -r "from '@/modules\|from '@/lib/toolNames\|from '@/lib/modelRegistry'" src/components/ui/` returns ZERO matches.

**Anti-patterns.**
- A `Sparkline` component that styles itself differently for "rates" vs "fx" data — that's a domain branch, even if it's just a colour palette switch. Move the palette decision into the consumer.

## How shared infrastructure changes

Changes to shared infrastructure follow the standard ADR cadence when they affect module-facing Prop contracts or shared-shell behaviour. Internal refactors (changing the implementation of a render shell without changing its Prop interface) do NOT require an ADR.

| Change kind | ADR required? |
|---|---|
| New shared shell (new render concept) | Yes — add to SI3 table + module-facing implications |
| Prop interface change | Yes — coordinated with module surfaces (FM8) |
| Service / type file split or merge | Yes — affects SI5 |
| Hook signature change | Yes if it affects `useRatesData`, `useWorkspaceDetail`, etc. consumed by modules; no for internal helper hooks |
| Atom internal change | No |
| Render-shell internal optimisation | No |

## Open questions

1. **Co-located stories.** Should every shared shell ship a Storybook story? Today: optional; recommended for shells with non-trivial visual states.
2. **Lazy-loading the bento grid.** Today Monitor widgets load eagerly; lazy-loading them per visible viewport would reduce first-paint cost. Open; revisit when Monitor surfaces accumulate >12 widgets per layout on average.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1 | 2026-05-25 | Initial shared-infrastructure contract. 7 numbered principles (SI1–SI7), canonical shared-shell list, service/type boundary table. | [`../../05_decisions/0014-frontend-module-architecture.md`](../../05_decisions/0014-frontend-module-architecture.md) |
