# Frontend Thesis

> The frontend's analog of [`00_what_we_build.md`](00_what_we_build.md) + [`01_non_negotiables.md`](01_non_negotiables.md). What the frontend IS, what it is NOT, and the numbered principles every frontend contribution must satisfy. **Agent-agnostic by design**: the principles hold whether the agent is rates today or FX, credit, equities, commodities, options tomorrow.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing. Changes require an ADR in [`../05_decisions/`](../05_decisions/) and a coordinated bump to any dependent file in [`../02_components/frontend_module/README.md`](../02_components/frontend_module/README.md).
**Operationalises principles:** P1 (built right), P3 (consistency by contract), P5 (honest disclosure), P9 (finance-blind layer boundary — the frontend's shared infrastructure layer mirrors the backend operator layer), P10 (single source of truth), P11 (domain isolation — at the frontend, isolation is per-module not per-domain).
**See also:** [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md) (the architectural decision), [`../02_components/frontend_module/README.md`](../02_components/frontend_module/README.md) (the module contract).

---

## What the frontend is

The frontend is the **user-facing surface** of the Macro Copilot platform. It turns the backend's structured analytical universe (primitives, operators, workflows, artifacts) into a small number of interactive rooms a PM uses every day:

- **Monitor** — bento-grid wall of pre-aggregated widgets. Glance, no interaction.
- **Library** — searchable catalogue of every primitive the backend ships, plus the workflow templates that compose them.
- **Ask** — conversational chat surface. The LLM router emits typed actions; the frontend renders them.
- **Build** — primary work surface. 3-column layout (workspaces sidebar | canvas | copilot rail). The canvas hosts whichever module's surface the URL demands.
- **Briefcase** — saved-workspaces and notes (placeholder today).

Each room is a **page shell** — a thin routing + layout layer. Its job is to host a module's surfaces, not to compute analysis.

## What the frontend is NOT

- **Not a recompute layer.** Every number on screen came from the backend's compute layer. The frontend renders; it does not recompute. The Bloomberg Accuracy Boundary (P12) binds the frontend in the same shape it binds the backend: where the backend ingests rather than recomputes, the frontend renders the ingested value verbatim. No client-side z-score recomputation. No client-side curve interpolation. No client-side anything that should be a primitive.
- **Not a vendor-specific layer.** The frontend reads from `/api/v1/...` REST + a WebSocket session. The data substrate behind the API is not the frontend's concern. Bloomberg today; second-source-of-record tomorrow; the frontend does not change.
- **Not a closed-source surface.** Every methodology choice the user sees is *reachable* through the UI: methodology cards, lineage chains, parameters panels, replay views. The frontend's job is to make backend doctrine *visible*, not to hide it.
- **Not a page-organised codebase, at target state.** [ADR 0014](../05_decisions/0014-frontend-module-architecture.md) sets the target architecture: code organised by **module** (one folder per backend unit), not by page. The page folders that remain (`build/`, `library/`, `monitor/`, `ask/`, `layout/`) host modules' surfaces; they do not own primitive-specific logic. **Status today:** the codebase is still page-organised; the migration to module-folders runs through Stage 2–N per [`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md). Every claim in this document about "modules" and "module folders" describes the target state Stage N reaches; the principles (FP1–FP13) bind every NEW primitive's UI work from Stage 2 onward and gradually bind existing surfaces as they migrate. Where a principle's enforcement mechanism (a test, a CI script, an ESLint rule) does not yet exist, the principle is marked "**Target-state, Stage X+**" in the Verify section.

## The mental model: primitives × surfaces

The frontend organises around a matrix:

```
                       MONITOR    BUILD    ASK    LIBRARY    DAG-node
calculate_curve_spread   ●          ●       ●        ●          ●
calculate_pca_*          ·          ●       ●        ●          ●
calculate_cpi_surprise   ●          ●       ●        ●          ●
get_otr_history          ·          ●       ·        ●          ●
event_study (workflow)   ·          ●       ●        ●          ●
...                      ...
```

- **Rows are modules** — one backend primitive or workflow template per row.
- **Columns are surfaces** — Monitor widgets, Build canvases, Ask cards, Library tiles, DAG-node renderers.
- **Cells are claims** — a `●` means the module ships JSX for that surface. A `·` means the module either does not surface there or uses the generic fall-through (Library tile from manifest, DAG node from shared renderer).

**The codebase is organised by row, not by column.** Each module's folder contains every `●` it claims. The cohesion lives in one place. This is the architectural decision recorded in ADR 0014.

## How to read this document

For your first read: scan the **Quick index** below, then read every principle's **Rule** line. That alone gives you the whole spec. The **Why**, **Verify**, and **Anti-patterns** sections are reference material for when a question or a PR turns on a specific principle.

For ongoing work: do not re-read this file from top to bottom. Look up the specific principle by ID when it comes up. Cite by ID (`FP3`, `FP7`) in commit messages, PR comments, and code review, exactly the way the backend cites the P-numbers from [`01_non_negotiables.md`](01_non_negotiables.md). The two namespaces are deliberately separate: **P-numbers are platform-wide; FP-numbers are frontend-specific.** A frontend PR may cite both.

## Quick index — the FP-numbers

| ID | Group | Principle | One-line rule |
|---|---|---|---|
| **FP1** | I | Module identity | Every frontend module owns the end-to-end UI of one named backend unit; the folder name equals the backend's canonical identifier. |
| **FP2** | I | Module-first cohesion | All code that surfaces one backend unit lives in that unit's module folder. Page shells host modules' surfaces; they do not own primitive-specific logic. |
| **FP3** | I | Surface-tier capability declaration | A module declares which surface tiers it claims via a closed family; the module ships exactly the files those tiers require. |
| **FP4** | II | Derived registries | Central registries (`KNOWN_BACKEND_TOOLS`, `RUNNABLE_PRIMITIVE_TOOLS`, etc.) are derived from `ALL_PRIMITIVE_MODULES`. No hand-authored registry entries. |
| **FP5** | II | Pure-spec assembly | Modules export pure spec values. The loader collects them at one barrel; no side-effect registration. |
| **FP6** | II | Backend-parity binding | Every backend primitive that needs a frontend representation has a matching module; every module references a real backend `tool_name` (or a documented `paused`/`deferred` reservation). |
| **FP7** | III | Honest tier disclosure | A module that is `workflow_incompatible` / `paused` / `deferred` surfaces an honest card; the orange decode-error card is reserved for genuinely-unknown tool names. |
| **FP8** | III | Methodology surfacing | Every methodology choice the backend declares is reachable from the UI without leaving the surface: methodology cards, lineage chains, parameter panels. |
| **FP9** | III | No client-side compute | The frontend renders; it does not recompute. Floats from the backend pass through unchanged; computed-on-client values (sparkline scaling, percent formatting, sort orders) are display affordances only and must never replace a backend number. |
| **FP10** | IV | THESIS discipline | Every module ships a `THESIS.md` answering the five required questions (what surfaces, why, what user reads, what would change design, which backend doctrine). |
| **FP11** | IV | Round-trip test | Every module ships `__tests__/module.spec.ts` proving its registry inclusion + tier claims + surface files are consistent. CI fails if any module's spec drifts from its file shape. |
| **FP12** | IV | Page-shell minimality | A page-shell file may import shared infrastructure and module-spec lookups; it may NOT import a specific module's surface file directly. The router-shaped lookup is the only way a shell touches a module. |
| **FP13** | IV | Finance-blind shared layer | Shared render shells, the DAG renderer, the bento grid, the WebSocket session, hooks, services, types, and UI atoms are domain-blind (the frontend's analog of P9). They MUST NOT branch on backend tool names. Branching is the module's job. |

---

## Group I — Definitional: what makes a frontend module

### FP1 — Module identity

**Rule.** Every frontend module owns the end-to-end UI representation of exactly one named backend unit. The unit is either a primitive (a `tool_name` in `_PRIMITIVE_SPECS` ∪ `WORKFLOW_INCOMPATIBLE_TOOLS`) or a workflow template (a `template_id` in `WORKFLOW_ARCHETYPES`). The module's folder name equals the backend's canonical identifier exactly (`src/modules/primitives/calculate_cpi_surprise_tool/`, not `src/modules/primitives/cpi_surprise/`).

**Why.** Mental-model parity with the backend. A `grep calculate_cpi_surprise_tool` from either side finds the same identifier. New engineers reading either side learn the same vocabulary. The folder name *is* the contract — when a backend PR adds `calculate_real_yield_level_tool`, the frontend PR that follows adds `src/modules/primitives/calculate_real_yield_level_tool/`, no name translation, no decoding.

**Verify.**
- `ls src/modules/primitives/` lists folder names that exactly match the union of backend `_PRIMITIVE_SPECS` keys, `WORKFLOW_INCOMPATIBLE_TOOLS` keys, and `UNSUPPORTED_KNOWN_TOOLS` reservations (after Stage N).
- A module folder named `foo` whose `module.ts` declares `toolName: 'bar'` is a build error (the per-module round-trip test catches this).
- A backend `tool_name` with no matching module folder is a CI failure (the cross-side parity test catches this).

**Anti-patterns.**
- Short-name folders: `src/modules/primitives/pca/` instead of `src/modules/primitives/calculate_pca_yield_curve_tool/`.
- A module folder for a unit the backend does not ship (no entry in `_PRIMITIVE_SPECS`, `WORKFLOW_INCOMPATIBLE_TOOLS`, or `UNSUPPORTED_KNOWN_TOOLS`).
- A module folder whose `module.ts.toolName` does not match the folder name.

**Exceptions.** None.

**Relates to.** P10 (single source of truth — the backend tool_name is THE name), P11 (domain isolation — a module owns its slice end-to-end).

### FP2 — Module-first cohesion

**Rule.** Every piece of code that surfaces one specific backend unit lives in that unit's module folder. Monitor widget, Build canvas, Ask card, persisted-artifact preview, bespoke wire types, tests — all in one place. Page shells (`build/`, `library/`, `monitor/`, `ask/`, `layout/`) host modules' surfaces via a router-shaped lookup; they do not own primitive-specific logic.

**Why.** Cohesion over coupling. The set of changes required when a primitive's output shape changes is *every surface of that primitive*. Those surfaces must change in lockstep. If they live in one folder, the developer sees the full impact at a glance and the PR diff is contained. If they're scattered across six page-folders, the developer forgets one and the registry drift compounds.

**Verify.**
- `grep -r 'calculate_X_tool' src/components/{build,library,monitor,ask}/` returns ZERO matches inside specific tool names. (Generic registry reads — `RUNNABLE_PRIMITIVE_TOOLS`, `getModelMetadata(toolName)` — are fine; specific tool-name string literals are the violation.)
- Page shells contain layout + state machine + shared-infrastructure calls only.
- A new primitive's UI work touches exactly one module folder + (rarely) the central loader at `src/modules/index.ts`.

**Anti-patterns.**
- `src/components/build/widgets/PcaPreviewWidget.tsx` (the legacy location) instead of `src/modules/primitives/calculate_pca_yield_curve_tool/surfaces/PreviewWidget.tsx`.
- A `switch (toolName)` block inside `BuildShell.tsx` that branches per primitive.
- Per-primitive conditional logic in `LibraryPage.tsx`.
- An Ask message renderer that hardcodes a per-tool result-card layout inline.

**Exceptions.** None for new code. Existing code that violates FP2 is migrated per the roadmap; no new code may add to the violation.

**Relates to.** FP1 (identity drives cohesion), FP12 (page-shell minimality is the other side of this coin), the backend's PR3 (the same finance-blind isolation principle at a different layer).

### FP3 — Surface-tier capability declaration

**Rule.** A module declares which surface tiers it claims via the closed-family `tiers: SurfaceTier[]` field in its `MODULE` spec. The closed family is exactly (Stage 4e: 5 runtime-status + 4 capability = 9):

```
generic_runnable | workflow_incompatible | manifest_typed_view | paused | deferred  // runtime-status (pick exactly one)
custom_build_surface | custom_preview_widget | monitor_surface | ask_surface       // capability (combine freely)
```

Every module MUST claim exactly one *runtime-status tier* (`generic_runnable`, `workflow_incompatible`, `manifest_typed_view`, `paused`, or `deferred`). Capability tiers (`custom_build_surface`, `custom_preview_widget`, `monitor_surface`, `ask_surface`) may be combined freely. A module claiming a capability tier MUST ship the corresponding `surfaces/<Name>.tsx` file (or, for `monitor_surface`, EITHER `surfaces/MonitorWidget.tsx` OR a non-empty `MODULE.monitorWidgets[]` per the Stage 4d multi-variant shape); conversely, a module that does NOT claim a capability tier MUST NOT have that surface file.

**Why.** Predictable contracts. A reader of the module's `module.ts` knows immediately what UI surfaces exist for this primitive. A reader of `src/modules/primitives/<name>/surfaces/` knows the tier set without reading code. The tier set being a *closed family* (per backend P8) means extensions are ADR-recorded — drift is impossible. The mutually-exclusive runtime-status rule prevents nonsense states like "runnable AND paused."

**Verify.**
- A module's `module.ts.tiers` is a subset of the closed family.
- Exactly one of `{generic_runnable, workflow_incompatible, paused, deferred}` is present.
- For each capability tier in `tiers`, the corresponding `surfaces/<Name>.tsx` exists.
- For each `surfaces/<Name>.tsx` present, the corresponding capability tier is in `tiers`.
- The per-module round-trip test asserts all four points.

**Anti-patterns.**
- `tiers: ['generic_runnable', 'workflow_incompatible']` — mutually exclusive.
- `tiers: ['custom_build_surface']` with no `BuildSurface.tsx`.
- `surfaces/MonitorWidget.tsx` present in the folder but `tiers` does not include `monitor_surface`.
- A new tier added to the union without an ADR amending the closed family.

**Exceptions.** None.

**Relates to.** P8 (closed-family discipline), FP4 (the registries derive from tier claims), [`../02_components/frontend_module/tiers.md`](../02_components/frontend_module/tiers.md) (full per-tier semantics).

---

## Group II — Architectural: how modules wire into the system

### FP4 — Derived registries

**Rule.** The frontend's central registries — `KNOWN_BACKEND_TOOLS`, `RUNNABLE_PRIMITIVE_TOOLS`, `WORKFLOW_INCOMPATIBLE_TOOLS`, `UNSUPPORTED_KNOWN_TOOLS`, `KNOWN_WORKFLOWS`, `PAUSED_WORKFLOWS`, the Monitor widget catalogue, the node-renderer registry, the dashboard registry — are derived from `ALL_PRIMITIVE_MODULES` and `ALL_WORKFLOW_MODULES`. NO hand-authored registry entries.

**Why.** Single source of truth. When the registries are hand-authored, the path from "I shipped a primitive" to "the UI knows about it" is six manual edits across five folders. When they're derived, the path is "add a module folder and one import line." Drift becomes structurally impossible: a module exists ⟹ its tier claims drive its registry membership; a primitive without a module is invisible (and CI catches it via the parity check). This is the operational expression of P10 at the frontend.

**Verify.**
- `src/lib/toolNames.ts` contains no static `Set<string>` literals enumerating tool names. Every set is `new Set(ALL_PRIMITIVE_MODULES.filter(...).map(m => m.toolName))`.
- `src/lib/modelRegistry.ts` builds its `MODELS` array from `ALL_PRIMITIVE_MODULES`, not from a hand-authored list.
- The Monitor widget catalogue (`src/components/monitor/registry.ts`) reads modules' `monitor_surface` claims, not a static dict.
- The node-renderer registry's per-tool entries register from each module's `custom_preview_widget` claim at module-spec collection time, not from individual side-effect imports.
- A grep for `KNOWN_BACKEND_TOOLS: ReadonlySet<string> = new Set<string>([` matches a single derived call; matches with literal string arrays are violations.

**Anti-patterns.**
- Adding a tool name to `RUNNABLE_PRIMITIVE_TOOLS` directly without adding the module.
- A registry entry that says "TODO: migrate to module."
- A central registry hand-extended in a PR that also adds a module — the module is the source; the registry must not duplicate it.

**Exceptions.** During Stage 1 of the migration (per [`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md)) the registries are temporarily hand-authored to close the immediate coverage gap. From Stage 2 onward, every new primitive lands as a module; the hand-authored entries shrink monotonically and are deleted in Stage N.

**Relates to.** P10, FP5 (derivation requires pure-spec assembly), [`../02_components/frontend_registries/README.md`](../02_components/frontend_registries/README.md).

### FP5 — Pure-spec assembly

**Rule.** Modules export pure spec *values*, not side effects. Each module's `module.ts` ends with `export const MODULE: PrimitiveModuleSpec = { ... };`. The single loader at `src/modules/index.ts` imports every module's spec and exposes one `ALL_PRIMITIVE_MODULES: readonly PrimitiveModuleSpec[]` array. Side-effect imports — `import './register'` that mutates a global — are forbidden.

**Why.** Determinism, testability, tree-shaking. (a) Import-order independence: a module's behaviour cannot depend on which file imported it first. (b) Test isolation: a test can `import { MODULE } from '...'` and inspect the spec without triggering any global mutation. (c) Tree-shaking: bundlers can safely drop modules the page does not reach. (d) Debuggability: a developer reading `toolNames.ts` follows a pure data-flow path back to each module's spec; there is no "where did this registry entry come from?" question. The backend's MCP server pattern (each tool's `register` function called explicitly at server-init time, from one place) is the analog.

**Verify.**
- `grep -r "import './" src/modules/` matches only the central loader at `src/modules/index.ts`.
- Module `__tests__/module.spec.ts` imports the module's spec directly and asserts its content without any setup hooks.
- The central loader is a flat list of `import { MODULE as foo } from './primitives/foo';` lines, one per module.
- No module file calls `register*()` mutating functions at module-eval time.

**Anti-patterns.**
- A `register.ts` file that imports the global registry and pushes onto it.
- A module that imports another module's `MODULE` to derive state — modules are independent; their assembly happens only at the loader.
- A test that depends on import order to see the registry in a particular state.

**Exceptions.** Per-tool renderer registration against the node-renderer registry (an existing pattern in `build/widgets/`) is allowed to continue as a side-effect import during the migration, but the registration call MUST be wrapped in a function that the central loader invokes once it has the full module-spec list. By Stage N every renderer-registry entry derives from the module-spec list, not from per-file side effects.

**Relates to.** FP4 (derivation depends on pure values), P10, the backend pattern of explicit registration in `mcp_server.py`.

### FP6 — Backend-parity binding

**Rule.** Every backend primitive whose `tool_name` is in `_PRIMITIVE_SPECS` ∪ `WORKFLOW_INCOMPATIBLE_TOOLS` ∪ `UNSUPPORTED_KNOWN_TOOLS` has a corresponding frontend module under `src/modules/primitives/<tool_name>/`. Conversely, every frontend primitive module's `module.ts.toolName` is in that union OR explicitly declared `tier: 'deferred'` with a comment naming the backend artefact it reserves (e.g. an archetype-enum slot like `attribution_decomposition`).

A coupled CI check (`tools/check_module_parity.py` or analog) asserts the set equality + the reservation exception.

**Why.** This is the structural guarantee that prevents the current 18-primitive gap from recurring. When a backend PR adds a primitive, CI fails on the frontend side until a matching module ships. When a frontend PR adds a module for a tool the backend doesn't recognise, CI fails too. The "did anyone update the frontend?" failure mode is replaced by a build error.

**Verify.**
- `tools/check_module_parity.py` exits clean. The script reads backend `_PRIMITIVE_SPECS` keys + `WORKFLOW_INCOMPATIBLE_TOOLS` keys, reads frontend `ALL_PRIMITIVE_MODULES` tool names, and asserts equality (modulo `deferred` reservations on the frontend side).
- A backend PR adding a primitive without the matching frontend module fails CI.
- A frontend module referencing a non-existent backend tool name fails CI.

**Anti-patterns.**
- A backend primitive shipped without the frontend module. (Stage 0 documents this as the failure mode the current 18-tool gap demonstrates.)
- A frontend module for an aspirational backend feature without a `deferred` tier + a documented reservation.

**Exceptions.** Workflow templates follow the same rule against `WORKFLOW_ARCHETYPES` ∪ active templates. Operators are exempt — they do not get modules.

**Relates to.** P3, FP1, [`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md).

---

## Group III — Behavioural: what modules must surface to the user

### FP7 — Honest tier disclosure

**Rule.** A module whose runtime-status tier is `workflow_incompatible`, `paused`, or `deferred` surfaces an explicit, honest card explaining the state. The user MUST see what the primitive is, why it is not running through the normal path, and what alternative they have today. The orange "Could not decode workspace context" card is reserved for genuinely unknown tool names (string typos, malformed contexts) — not for shipped backend primitives.

**Why.** Trust. A PM seeing a shipped, documented primitive land on an orange error card concludes the platform is broken. A PM seeing the same primitive land on a card that says *"This primitive is workflow-incompatible by output shape (per-meeting WIRP snapshots, not a single Series). Run it via Ask, or open the typed detail at /detail/wirp"* concludes the platform is honest. Honest disclosure is P5 in the user's pixels.

**Verify.**
- For every module with `tier: 'workflow_incompatible'`: the Build surface renders the workflow-incompatible card with the per-tool reason (sourced from `WORKFLOW_INCOMPATIBLE_TOOLS[toolName]` on the backend, mirrored into the module's `unsupportedReason` field).
- For every module with `tier: 'paused'`: the Build surface renders the paused card with the per-tool reason.
- For every module with `tier: 'deferred'`: opening the module's stub URL renders a "reserved name" card with a link to the reservation rationale.
- Truly unknown tool names (NOT in `KNOWN_BACKEND_TOOLS`) continue to render the orange decode-error card. The decode-error card is reserved for THIS case and only this case.

**Anti-patterns.**
- A workflow-incompatible primitive falling through to the decode-error card.
- A paused primitive surfacing as a "missing data" generic error rather than the paused-card with the unblock path.
- A deferred primitive that 404s rather than rendering its reservation card.

**Exceptions.** None.

**Relates to.** P5, P6, FP3 (the tier IS the disclosure mechanism), [`../02_components/frontend_module/tiers.md`](../02_components/frontend_module/tiers.md).

### FP8 — Methodology surfacing

**Rule.** Every methodology choice the backend declares is reachable from the UI without leaving the surface. The methodology card (in Build's right-rail or below the canvas), the lineage chain (in the DAG strip), and the parameters panel (in the Build / model surfaces) together carry every `conventions:` value, every `methodology.what_it_does` line, every `methodology_note` from the output, and every workspace-override the user has made.

**Why.** P5 (honest disclosure) at the pixel level. The backend's primitive contract makes methodology *reachable*; the frontend's job is to make it *seen*. A user who has to leave the page to find out which z-score window was used is one who'll stop trusting the surface.

**Verify.**
- Every Build surface includes a `MethodologyPanel` (or analog) reading from the tool's `ToolCard.methodology` + the run's `methodology_note` (where applicable).
- Every workspace's `?lineage` view shows every step's tool name, methodology hash, and convention values.
- Per-tool `interpretationCards` from the module's spec render below the canvas where declared.
- A user reading any Build surface can reconstruct the methodology card without API calls beyond what's already on the page.

**Anti-patterns.**
- A Build surface that omits the methodology panel.
- A primitive that ships methodology only in `config.yaml` with no UI affordance to read it.
- A composition primitive that hides upstream methodology behind a click instead of surfacing it inline (this is the analog of backend PR10).

**Exceptions.** None.

**Relates to.** P5, backend PR10 (provenance reachability — this is the UI counterpart).

### FP9 — No client-side compute

**Rule.** The frontend renders backend numbers; it does not recompute them. Floats from the backend pass through unchanged. Display affordances — sparkline scaling, percent formatting, decimal precision, sort order, colour-by-threshold — are visual only and MUST NOT replace a backend number with a client-computed one.

**Why.** Bloomberg Accuracy Boundary (P12) at the frontend. If the backend computes a z-score and the frontend recomputes it (even with the same formula), the two can disagree under floating-point edge cases, and the UI shows a number the lineage chain does not justify. Replay (P4) breaks. The same answer in May and November breaks. The frontend's job is to be a faithful renderer.

**Verify.**
- No `Math.*` calls inside surface code that produce a numeric value the user reads. (Display computations like `Math.max` for sparkline scaling are fine; they don't replace a value, they shape pixels.)
- No client-side z-score, percentile, ratio, or aggregation. If you need a derived metric, the backend ships it as a field on the output.
- A test that injects a stub backend response with a specific numeric value asserts that exact value renders pixel-for-pixel.
- Sparkline scaling, bar widths, threshold-based colouring, sort orders are derived from backend values; the backend value itself is what the user reads.

**Anti-patterns.**
- A widget that computes `(latest - mean) / std` on the client because "the backend doesn't ship z-score for this field."
- A KPI strip that displays `Math.round(value * 100) / 100` differently from what the backend's `rounding_decimals` convention says.
- A "percentage change" computation in the UI rather than a backend field.

**Exceptions.** Pasted-input primitives where the user-supplied data is itself the input (e.g. pasted PCA loadings) compute their math on the backend even when the input came from the client. The frontend MAY render lightweight derivations of *user-typed text* during form editing (e.g. "Δ between two dates" preview as the user picks dates) — those are not analytical values, they're editor affordances.

**Relates to.** P2, P4, P12.

---

## Group IV — Operational: the conventions every frontend contribution follows

### FP10 — THESIS discipline

**Rule.** Every module ships a `THESIS.md` in its root folder. The file answers exactly five required questions in the order specified by [`../02_components/frontend_module/thesis_template.md`](../02_components/frontend_module/thesis_template.md):

1. **What surfaces does this module ship?** Enumerate every tier claimed.
2. **What does the user read off each surface?** One paragraph per surface naming the specific decisions a PM makes from it.
3. **Why these surfaces and not others?** Explain the alternatives considered and rejected (could this be a generic surface? Why a bespoke widget?).
4. **What would change the design?** List the specific shifts in user need or backend output that would force a new tier claim or surface rewrite.
5. **Which backend doctrine does this module operationalise?** Cite the relevant P-numbers, PR-numbers, ADRs.

**Why.** Designed artefacts. The backend has methodology decisions captured in `config.yaml` and `methodology.what_it_does`; the frontend has historically had nothing equivalent. THESIS is the analog. It forces the design intent into a citable document, lets future engineers reason about why a module looks the way it does, and creates an audit trail for UX decisions that today live only in chat threads and PR descriptions.

**Verify.**
- `find src/modules -name THESIS.md | wc -l` equals the number of module folders.
- Every THESIS answers all five questions in order.
- The per-module round-trip test asserts THESIS.md exists.

**Anti-patterns.**
- A module without a THESIS.
- A THESIS that's a generic README rather than answering the five questions.
- A THESIS copy-pasted from another module without per-tool customisation.
- A THESIS that drifts from the module's actual tier claims (THESIS says "Monitor surface" but the module does not claim `monitor_surface`).

**Exceptions.** `deferred` modules ship a THESIS that's a one-paragraph reservation note + the five-question template marked "N/A — deferred reservation."

**Relates to.** P1, P5, FP3, [`../02_components/frontend_module/thesis_template.md`](../02_components/frontend_module/thesis_template.md).

### FP11 — Round-trip test

**Rule.** Every module ships `__tests__/module.spec.ts` proving the following invariants:

1. The module's `MODULE.toolName` equals the folder name.
2. The module's `MODULE.tiers` is a subset of the closed family and obeys the mutually-exclusive rule (exactly one runtime-status tier).
3. Every capability tier in `MODULE.tiers` has a matching `surfaces/<Name>.tsx` file (import-resolvability test).
4. Every `surfaces/*.tsx` file present has its corresponding capability tier in `MODULE.tiers`.
5. The module's `MODULE.toolName` appears in the central loader at `src/modules/index.ts` (the loader-presence test).
6. The module's THESIS.md exists.

**Why.** Mechanical enforcement of FP1, FP3, FP10. Without the round-trip, a developer can ship a module whose spec and files disagree, and the failure surfaces at production runtime ("the Monitor widget is in the registry but the file doesn't exist"). The round-trip catches it at CI time.

**Verify.**
- `npm run test:modules` exits clean.
- Every module folder has `__tests__/module.spec.ts`.
- A test missing for a module fails the broader parity check.

**Anti-patterns.**
- A module without the round-trip test.
- A round-trip test that asserts a subset of the six invariants.
- Disabling the test "temporarily" to ship a half-wired module.

**Exceptions.** None.

**Relates to.** FP1, FP3, FP10, [`../03_standards/frontend_test_patterns.md`](../03_standards/frontend_test_patterns.md).

### FP12 — Page-shell minimality

**Rule.** A page-shell file (`BuildShell.tsx`, `LibraryPage.tsx`, `MonitorPage.tsx`, `AskPage.tsx`, `AppShell.tsx`, `Sidebar.tsx`, `ChatDrawer.tsx`, `TopNav.tsx`, and their immediate sub-helpers) may import:
- Shared infrastructure (render shells, hooks, services, types, UI atoms, the DAG renderer, the realtime context).
- Module-spec lookups (`getPrimitiveModule(toolName)`, `getWorkflowModule(templateId)`).
- The central registries (derived per FP4).

A page-shell file may NOT import a specific module's surface file directly (`import { MonitorWidget } from '@/modules/primitives/calculate_curve_spread_tool/surfaces/MonitorWidget'`). The router-shaped lookup is the only way a shell touches a module.

**Why.** Cohesion (FP2) at the import level. Once a shell imports a specific module, every change to that module's surface API requires a shell edit. With the router-shaped lookup, modules can change their surface internals without touching shells; shells stay generic.

**Verify.**
- `grep -r "from '@/modules/primitives/" src/components/` returns ZERO matches outside `src/components/shared/` and outside test files.
- Lint rule: a `from '@/modules/...'` import inside a page-shell file is a build error.
- A page-shell file with a `switch (toolName)` block branching to specific imports is the failure mode this rule prevents.

**Anti-patterns.**
- `BuildShell` directly importing `PcaBuildSurface` to render PCA's canvas.
- `MonitorPage` importing `CpiSurpriseMonitorWidget` from the module folder.
- A page-shell helper file (`buildLayout.ts`) that hardcodes per-module mapping rather than reading from module specs.

**Exceptions.** Test files exercising a specific module's surface in isolation may import directly. Storybook stories may import directly. Production code may not.

**Relates to.** FP2, FP4.

### FP13 — Finance-blind shared layer

**Rule.** Shared render shells (AutoRenderer, RichModelWidget, GenericPrimitiveBuilder, typed views like SpreadView / ButterflyView / YieldView / ScannerView / RegimeView), the DAG renderer (parseWorkflowLineage, deriveDagNodes, DagStrip), the bento grid renderer (WidgetGrid, WidgetCard, WidgetRenderer), the WebSocket session (CopilotContext), hooks (useRatesData, useWorkspaceDetail, useLibraryManifest, useCopilot), services (ratesApi, workflowsApi, libraryApi, workspaceApi), types (workflows, artifacts, rates, library), and UI atoms (Sparkline, Card, Modal) MUST NOT branch on specific backend tool names. Their behaviour is parameterised by *shape* (artifact type, generic schema, slot kind) — not by *which primitive*.

**Why.** This is the frontend analog of backend P9 (finance-blind operator layer). When shared infrastructure branches on a specific tool name, the boundary leaks: the shared layer becomes a co-author of that primitive's UI, the cohesion FP2 demands collapses, and changing the primitive requires touching shared code. The shared layer must shape *pixels*, not *domain math*.

**Verify.**
- `grep -r "calculate_.*_tool\|get_.*_tool\|build_.*_tool" src/components/{ui,shared}/` returns ZERO matches.
- The DAG renderer dispatches on `DagNodeKind` + `DagWireType`, not on tool name.
- AutoRenderer dispatches on `ArtifactType`, not on tool name.
- The GenericPrimitiveBuilder dispatches on `ToolCard.input_fields`, not on tool name.

**Anti-patterns.**
- An `if (toolName === 'calculate_pca_yield_curve_tool') { ... }` branch inside AutoRenderer.
- A specialised render path in DagStrip for a specific primitive.
- A hook that returns different shapes for different tool names.

**Exceptions.** None for the strict shared layer. Per-tool specialisation belongs in the module's `surfaces/`.

**Relates to.** P9, FP2.

---

## How this thesis changes

The contents of this file are thesis-level commitments. They change slowly, in coordinated PRs, with explicit decision records. The procedure mirrors backend's [`00_what_we_build.md`](00_what_we_build.md#how-this-thesis-changes):

1. **Open an ADR** in [`../05_decisions/`](../05_decisions/) describing the proposed change and its consequences for principles, the module contract, the page-shell contracts, the registry derivation rules, and the roadmap.
2. **Land the ADR and the thesis change in the same PR** — never separately. The ADR records the *why*; the thesis records the *what*. They must agree.
3. **Coordinate updates** to any module contract ([`../02_components/frontend_module/README.md`](../02_components/frontend_module/README.md)) or standard ([`../03_standards/frontend_*.md`](../03_standards/)) that depends on the thesis-level claim.
4. **Bump the version** of this file (v1 → v2) and update `Last reviewed`. The version log at the bottom of this file records every change.

Thesis stability is a feature, not a bug. Slow change is what makes the principles trustworthy.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1 | 2026-05-25 | Initial frontend thesis. 13 numbered principles (FP1–FP13) covering module identity, architectural wiring, behavioural disclosure, and operational discipline. | [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md) |
