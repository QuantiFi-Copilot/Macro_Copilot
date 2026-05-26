# Surface Contract

> The authoritative per-tool, per-surface status registry for the Macro_Copilot frontend.

**Version:** v1 (initial drafting)
**Last reviewed:** 2026-05-26
**Status:** draft — pending sign-off
**Branch discipline:** all rows are written against the `build` branch.

---

## 0. Purpose

Every backend artifact in this system — a **primitive**, an **operator**, or a **workflow** — can be exposed on up to four frontend surfaces: **Library**, **Ask**, **Build**, **Monitor**. This document is the single source of truth for:

1. Which surface each backend artifact **should** have (eligibility).
2. Which surface each backend artifact **currently has** (status).
3. The **classifications** that pin down what "having a surface" actually means for that artifact + surface combination.

No surface code may be added, removed, or modified without updating the corresponding row in this document in the same PR. That is the gate.

---

## 1. THE CONTAINMENT PRINCIPLE (load-bearing)

> A tool may be partially shipped — i.e. one surface built, another not — **if and only if** all the partial state lives entirely inside that tool's module folder at `src/modules/primitives/<tool_name>/` (or `src/modules/workflows/<workflow_id>/` for workflows).

**Concretely:**

- ✅ **Allowed:** module A's Monitor widget is real, its Ask card is real, its Build is just the generic fallback. *All* the per-tool code lives inside the module folder. The central registries (`toolNames.ts`, `monitor/registry.ts`, `widgets/index.ts`, `BuildShell.tsx`, `ConversationCanvas.tsx`, `contextDecoder.ts`, `persistedModelAdapters.ts`) carry zero per-tool entries for this module — they only do dispatch.
- ✅ **Allowed:** module B's bespoke Build canvas is partially polished, the result-renderer for module C is not yet pixel-perfect. As long as the files live inside their module folders and the central files have no exceptions for them.
- ❌ **Disallowed:** a "placeholder" `KNOWN_BACKEND_TOOLS` entry for tool X in `src/lib/toolNames.ts`.
- ❌ **Disallowed:** a `case 'tool_x':` branch inside `src/lib/contextDecoder.ts` to special-case routing for X.
- ❌ **Disallowed:** a stub widget file under `src/components/monitor/widgets/` named after a specific primitive.
- ❌ **Disallowed:** a module that claims a tier (e.g. `monitor_surface`) but ships a placeholder widget that does not deliver the claimed capability. **A module's `tiers` array is a contract.** Claiming a tier you don't deliver is "half-built state for the system," which the principle forbids.

**Enforcement:**

- ESLint `boundaries/no-module-from-shells` (already in place, FP12).
- This document. Every PR must update affected rows; a row marked `built` for surface X requires the corresponding file to exist inside the module folder AND the module's `tiers` array to claim the matching tier.

---

## 2. The 3 backend building blocks

### 2.1 Primitive
A single backend tool with a typed input + typed output, registered in `rates_agent/workflows/__init__.py:_PRIMITIVE_SPECS`. Today: 55 primitive specs + 3 workflow-incompatible tools = **58 total primitives the frontend must consider**.

Output kinds (drives Build / Monitor archetype choice):
- `single-series-bps` — time series in basis points (spreads, butterflies)
- `single-series-percent` — time series in percent (yields, rates)
- `single-series-events` — per-event values (CPI surprise, NFP surprise; index is release dates, not trading dates)
- `snapshot-grid` — multi-row table (curves × tenors)
- `scanner-result` — ranked rows
- `regime-state` — single-row classification
- `rich-model` — composite output (PCA loadings + variance + factor scores; regression coefficients; half-life + half-life CI; etc.)
- `panel` — tabular multi-row dataset (e.g. `build_sovereign_yield_panel`)

### 2.2 Operator
Internal computational glue, defined in `shared/operators/`. Today: **12 operators**. By design, operators have NO direct frontend surface. They are not in Library, do not get routed by Ask, do not appear on Build except as nodes in a workflow DAG.

The operators are: `align_series`, `series_arithmetic`, `threshold_events`, `event_windows`, `conditional_aggregate`, `rolling_regression`, `apply_mask`, `select_from_series_set`, `summarize_series`, `construct_trades`, `evaluate_trades`, `summarize_trades`.

Operator outputs only surface when (a) they are the **terminal artifact** of a workflow (rendered in the standard ResultsView), or (b) future enhancement: a workflow's ResultsView opts in to an "intermediate steps" view that surfaces non-terminal operator artifacts.

### 2.3 Workflow
A DAG template chaining primitives + operators, defined in `rates_agent/workflows/<template_id>/template.yaml`. Today: **3 templates** — `event_study`, `regime_conditioned_relationship`, `backtest`.

Workflow surface contract:
- MUST render the DAG in Build via `DagView`.
- MUST render the terminal artifact in Build via `ResultsView` (specialised dashboard if one exists, generic widget grid otherwise).
- MUST render its progress + result inline on Ask via `WorkflowResultCard`.
- MAY surface intermediate operator outputs via the (future) "show intermediate steps" toggle.

---

## 3. The 4 frontend surfaces

### 3.1 Library

**Purpose:** catalogue of every backend-registered tool. The user browses, filters by sub-agent / category, clicks a card → `ToolDetailDrawer` → "Open in Build" deep-link.

**Source of truth:** `/api/v1/library/manifest`. The page is fully auto-derived.

**Status enum (per tool):** `registered` | `hidden`.

This surface has zero per-tool frontend code — it is a generic list+drawer over the manifest. No row in the registry below needs anything beyond `registered` / `hidden`.

### 3.2 Ask

**Purpose:** chat-style answer to a natural-language query. Brief textual answer + a "Show more in Build" handoff button that transfers context to the Build page.

**Render component:**
- Default: `AssistantResearchCard` (7 zones — RoutingStrip, ProseAnswer, ToolTrace, ResultCanvas, DagStrip, ProvenanceRow, ActionRow, FollowUps). Already feature-complete. Renders the terminal artifact chart, the DAG, lineage, follow-ups.
- Per-module override: `MODULE.surfaces.ask` lets a module replace the entire research card with bespoke chrome. A bespoke ask card **MUST add value over the generic** (e.g. a specific framing/header callout above the standard zones); it MUST NOT drop the generic's chart / DAG / provenance / follow-ups.

**Workflow rendering:** `WorkflowResultCard` (renders inline inside the chat bubble for workflow turns).

**Status enum (per tool) — Ask routing stage:**

| Stage | Meaning |
|---|---|
| **1 — `unrouted`** | The Supervisor's system prompt doesn't list this tool's domain as covering relevant queries → "clarify" / "out_of_scope" response. Ask never reaches the tool. |
| **2 — `routed-generic`** | Supervisor routes to the right domain → domain agent calls the tool → `AssistantResearchCard` renders the result. Default for any tool whose domain is already enumerated in the Supervisor prompt. |
| **3 — `routed-bespoke-frontend`** | Stage 2 plus the module ships `MODULE.surfaces.ask` and the card adds real value beyond the generic. |
| **4 — `routed-bespoke-full`** | Stage 3 plus the domain agent prompt has tool-specific framing logic (e.g. NFP-specific "actual vs consensus" structured output). |

A module that claims `MODULE.surfaces.ask` but where the Supervisor doesn't route to it = **stage 1 (unrouted)**. Frontend claim alone is not enough.

### 3.3 Build

**Purpose:** the canonical analysis-anatomy view. The most important page in the product. Three states:

- **Empty** — no slug, no `?context=`, no `?workflow=`, no `?builder=` → polished "What would you like to build?" entry with category tiles + composer.
- **Single-primitive** — `?context=<encoded>` with one decoded tool → `VirtualPrimitiveCanvas` (editable param dropdowns + typed result renderer or module's bespoke build surface).
- **Workflow / multi-primitive** — `?context=` with ≥2 decoded tools → `MultiPrimitiveCanvas`; OR `/workspace/:slug` (persisted) → `BuildCompleted` with 4 tabs (DAG / Results / Parameters / Notes).

**Per-tool Build archetypes:**

| Archetype | Definition | How it ships |
|---|---|---|
| **`generic-builder`** | Default. The page renders a schema-driven form (from backend `ToolCard.input_fields`) + runs the tool + shows the output via `AutoRenderer`. No per-tool frontend code beyond what the manifest provides. | Module claims only `generic_runnable`. No surface claims. |
| **`typed-result-renderer`** | Same controls path as `generic-builder` (or hand-rolled `PrimitiveParamControls` for the 6 Stage-4 typed views), but the **output area** ships a bespoke chart + KPI strip via `MODULE.surfaces.resultRenderer`. | Module claims `manifest_typed_view` OR `custom_build_surface` AND ships `surfaces/ResultRenderer.tsx`. |
| **`custom-canvas`** | The module owns the entire Build page — bespoke controls AND bespoke output (model-builder feel: PCA, regression, half-life). | Module claims `custom_build_surface` AND ships `surfaces/BuildSurface.tsx`. |

**Status enum (per tool):**

| Status | Meaning |
|---|---|
| **`scaffolded`** | Module folder exists. Falls through to `generic-builder`. The default schema-driven form + `AutoRenderer` is what the user sees. |
| **`typed-renderer-built`** | `surfaces.resultRenderer` ships and renders real data via recharts. Editable params via the shared `PrimitiveParamControls` or the generic form. |
| **`custom-canvas-built`** | `surfaces.build` ships and is feature-complete: editable form controls + bespoke output. |
| **`production-validated`** | The above PLUS verified end-to-end against real data, snapshot-tested, no open regressions, parameter changes round-trip via URL. |

### 3.4 Monitor

**Purpose:** desk-glanceable board for state the user tracks throughout the trading day. Pre-aggregated grids + parameterised tiles.

**Eligibility rule (load-bearing):**

> A tool gets a Monitor widget **only** if a desk user would park it on their board and read it through the trading day. Single-fire event reads (CPI / NFP / PPI releases), one-off scenario calculations, and rich-model fits do NOT belong on Monitor. They are Ask / Build affairs.

**Widget type taxonomy:**

| Type | Examples | Data source | Parameterised? |
|---|---|---|---|
| **`snapshot-grid`** | YieldSnapshot, CurveSpreads, CrossMarketSpreads | shared `RatesDataContext` (page-level fetch) | No |
| **`scanner-leaderboard`** | Scanner | shared `RatesDataContext` | No |
| **`time-series-chart`** | YieldLevel, SpreadChart, CrossMarketSpread | per-widget `/api/v1/rates/detail/...` fetch | Yes (curve + tenor + lookback) |
| **`regime-state`** | CurveClassifier | per-widget `/api/v1/rates/detail/regime` | Yes |

**Eligibility enum (per tool):**

| Eligibility | Definition |
|---|---|
| **`required`** | Desk-glanceable; should have a Monitor widget. |
| **`built`** | Eligible AND ships a real, non-stub widget. |
| **`not-required`** | Explicitly does NOT belong on Monitor (event tools, rich-model tools, one-shot calculators). |
| **`deferred`** | Eligible but parked behind an upstream dependency (e.g. typed-detail endpoint not yet shipped). |

**Anti-pattern:** a module that claims `monitor_surface` but ships a placeholder widget violates the containment principle. The claim must match delivery.

---

## 4. The per-primitive registry

> 58 rows. One row per backend primitive. Columns: tool_name | domain | output_kind | library | ask_stage | build_archetype | build_status | monitor_eligibility | monitor_status.

### 4.1 Sovereign bonds (9)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `calculate_curve_spread_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | typed-renderer-built | required | built (2 widgets: CurveSpreads, SpreadChart) |
| `calculate_cross_market_spread_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | typed-renderer-built | required | built (2 widgets: CrossMarketSpreads, CrossMarketSpread) |
| `calculate_butterfly_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | typed-renderer-built | not-required | n/a |
| `get_yield_levels_tool` | single-series-percent | registered | routed-generic | typed-result-renderer | typed-renderer-built | required | built (YieldLevel; YieldSnapshot also reads this domain) |
| `classify_curve_move_tool` | regime-state | registered | routed-generic | typed-result-renderer | typed-renderer-built | required | built (CurveClassifier) |
| `scan_extremes_tool` | scanner-result | registered | routed-generic | typed-result-renderer | typed-renderer-built | required | built (Scanner) |
| `calculate_otr_ofr_spread_tool` | single-series-bps | registered | unrouted | generic-builder | scaffolded | required | deferred (needs typed-detail endpoint for OTR-OFR series) |
| `build_sovereign_yield_panel_tool` | panel | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `get_otr_history_tool` | workflow-incompatible | registered | unrouted | n/a (workflow_incompatible) | n/a | not-required | n/a |

### 4.2 OIS (7)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `calculate_ois_curve_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | required | deferred (typed renderer not yet) |
| `calculate_ois_cross_market_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_ois_forward_rate_tool` | single-series-percent | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `get_ois_rate_level_tool` | single-series-percent | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_ois_butterfly_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_swap_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_wirp_meeting_pricing_tool` | workflow-incompatible | registered | routed-generic (paused) | n/a (workflow_incompatible) | n/a | not-required | n/a |

### 4.3 Inflation-indexed bonds / linkers (10)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `get_real_yield_level_tool` | single-series-percent | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_breakeven_inflation_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_forward_breakeven_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_breakeven_curve_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_cross_country_breakeven_spread_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_real_yield_curve_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_cross_country_real_yield_spread_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_real_yield_butterfly_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_breakeven_butterfly_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `build_linker_panel_tool` | panel | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |

### 4.4 Inflation swaps (8)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `calculate_inflation_swap_rate_level_tool` | single-series-percent | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_inflation_swap_curve_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_inflation_swap_forward_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_cross_market_inflation_swap_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_swap_breakeven_basis_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_inflation_swap_butterfly_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `build_zcis_panel_tool` | panel | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `scan_inflation_swaps_extremes_tool` | scanner-result | registered | routed-generic | generic-builder | scaffolded | required | deferred |

### 4.5 Policy futures / STIR (8)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `policy_futures_get_futures_price_level_tool` | single-series-percent | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `policy_futures_get_volume_open_interest_snapshot_tool` | snapshot-grid | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `policy_futures_get_futures_strip_snapshot_tool` | snapshot-grid | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `policy_futures_get_futures_calendar_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `policy_futures_get_futures_cross_market_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `policy_futures_get_futures_butterfly_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `policy_futures_get_futures_pack_average_simple_tool` | single-series-percent | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `get_scan_policy_futures_extremes_tool` | scanner-result | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `build_policy_futures_strip_panel_tool` | panel | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |

### 4.6 Bond futures (3)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `get_futures_price_level_tool` | single-series-percent | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `get_futures_volume_oi_tool` | snapshot-grid | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `scan_bond_futures_extremes_tool` | scanner-result | registered | routed-generic | generic-builder | scaffolded | required | deferred |

### 4.7 Inflation-linker scanner (1)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `scan_inflation_linkers_extremes_tool` | scanner-result | registered | routed-generic | generic-builder | scaffolded | required | deferred |

### 4.8 OIS scanner — paused (1)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `scan_ois_extremes_tool` | scanner-result | registered | unrouted | n/a (paused) | n/a | required | deferred |

### 4.9 Rich-model / analytical (5)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `calculate_pca_yield_curve_tool` | rich-model | registered | routed-generic | custom-canvas | custom-canvas-built | not-required | n/a |
| `calculate_rolling_regression_tool` | rich-model | registered | routed-generic | custom-canvas | custom-canvas-built | not-required | n/a |
| `calculate_yield_change_attribution_pca_tool` | rich-model | registered | routed-generic | custom-canvas | custom-canvas-built | not-required | n/a |
| `calculate_half_life_tool` | rich-model | registered | routed-generic | custom-canvas | custom-canvas-built | not-required | n/a |
| `calculate_beta_adjusted_spread_tool` | rich-model | registered | routed-generic | custom-canvas | custom-canvas-built | not-required | n/a |

### 4.10 Cross-domain shared (3)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `compute_financing_rate_tool` | single-series-percent | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_breakeven_inflation_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_zscore_custom_tool` | single-series-events | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |

### 4.11 Event-release surprises (2)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `calculate_cpi_surprise_tool` | single-series-events | registered | unrouted | generic-builder | scaffolded | not-required | n/a |
| `calculate_nfp_surprise_tool` | single-series-events | registered | unrouted | generic-builder | scaffolded | not-required | n/a |

**Resolved in PR (post-contract Stage C):** Modules `calculate_cpi_surprise_tool` and `calculate_nfp_surprise_tool` previously claimed `monitor_surface` + `ask_surface` with stub widgets and a bespoke Ask card that was a regression vs the generic.  Both tier claims were retracted; the stub files were deleted; the modules now claim `generic_runnable` only.  Per the containment principle (§1), a module's `tiers` array must match delivery.

---

## 5. Per-workflow registry

| template_id | nodes (count) | status | ask | build_dag | build_results | notes |
|---|---:|---|---|---|---|---|
| `event_study` | ~10 | active | routed-generic via `WorkflowResultCard` | rendered via `DagView` | rendered via `ResultsView → EventStudyDashboard` (specialised) | **frontend chain verified clean (PR audit):** node-ID role bindings match template (`signal`/`target`/`align`/`events`/`windows`/`aggregate`/`unconditional_aggregate`/`compare`); 5 dashboard sections render via `NodeWidgetCard` with `MissingArtifactCard` fallbacks; `WorkflowResultCard` handles event-relative Series via `EventRelativeStrip`.  User-reported "garbage outputs" are backend payload quality — frontend renders honestly whatever the backend persists. |
| `regime_conditioned_relationship` | ~12 | active | routed-generic via `WorkflowResultCard` | rendered via `DagView` | rendered via `ResultsView → RegimeConditionedDashboard` (specialised) | — |
| `backtest` | ~6 | paused (backend gated) | not routed | n/a | `WorkflowStatusCanvas` shows "paused" card | depends on financing + price-panel data that isn't fully shipped backend-side |

---

## 6. Operator visibility policy

Operators have NO frontend surface and ARE NOT listed in this registry by name. They exist only as nodes inside workflow DAGs.

When an operator's output IS the terminal artifact of a workflow, it surfaces via the standard `ResultsView` widget grid (one of `SeriesWidget` / `SeriesSetWidget` / `EventSetWidget` / `PanelWidget` / `WindowedPanelWidget` / `TradeSetWidget`) — dispatched by artifact type. No per-operator code lives in the frontend.

**Intermediate-step surfacing (shipped):** every workflow's specialised `ResultsView` dashboard ships a "Show intermediate stages (N)" toggle.  When the toggle is on, every NON-terminal per-node artifact (primitives AND operator outputs alike — `align_series`, `threshold_events`, `event_windows`, `conditional_aggregate`, `rolling_regression`, `construct_trades`, `evaluate_trades`, `summarize_trades`, `select_from_series_set`, etc.) renders through the same per-artifact widget registry the terminal output uses (`NodeWidgetCard` → `resolveNodeRenderer`).  No per-operator UI code is required — adding a new operator is automatically covered by the registry the moment its artifact type is recognised.

---

## 7. Cross-cutting status — containment principle today

**RESOLVED (PR Stage C — same PR as this row update):**

1. **`calculate_cpi_surprise_tool`** previously claimed `monitor_surface` + `ask_surface` with stub widget + regression Ask card.  Both claims retracted.  Module now `generic_runnable` only.
2. **`calculate_nfp_surprise_tool`** — same.  Both claims retracted.  Module now `generic_runnable` only.
3. **`calculate_otr_ofr_spread_tool`** previously claimed `monitor_surface` with stub widget.  Claim retracted.  Module now `generic_runnable` only.  Monitor remains eligible per the rule; row marked `deferred` until the typed-detail endpoint ships OTR-OFR series.

Containment is restored.  Every remaining tier claim across the 58 primitives matches a real, polished surface.

---

## 8. Critical end-to-end gaps (require multi-PR work, NOT containment violations)

These are correctly contained today (no central-file leakage) but are functionally incomplete:

1. **Ask routing for event-release queries** is at Stage 1 (`unrouted`) for the 5 event-class tools (CPI / NFP / OTR-OFR + others). Resolved by extending the Supervisor system prompt in `orchestrator/prompts.py` to claim these tools under their owning domain. **Out of scope for this session per user direction; queued for the orchestration session.**
2. **~~`contextDecoder` typed-kind coverage~~** — **RESOLVED (PR Decoder Audit, v3).**  Audit traced `decodeOne` against every module in `ALL_PRIMITIVE_MODULES`: by construction, the `isRunnablePrimitive` branch (step 3) routes every module that claims `generic_runnable` to `kind: 'generic_builder'`, and the module-derived runnable set automatically covers all such modules.  Modules with `manifest_typed_view`/`workflow_incompatible`/`paused` route via their own branches.  A new coverage assertion in `src/components/build/primitive/__tests__/routingCoverage.test.ts` iterates `ALL_PRIMITIVE_MODULES` and fails CI if any module decodes to null — pure regression guard.  The orange "Could not decode" card now only fires for malformed JSON or backend tools not yet registered as frontend modules (zero today per parity check).
3. **~~Workspace fork → re-render with new params loop~~** — **RESOLVED (PR Operator-Surfacing + Fork-Pipeline Audit, v4).**  End-to-end loop traced: `WorkspaceOverridesProvider.apply()` calls `forkWorkspace(slug, {slot_overrides, slot_dict_overrides})` → backend returns the new slug → `navigate('/workspace/${new_slug}')` mounts the forked workspace.  Pre-flight validation (`validation.ok`) blocks malformed submissions; legacy non-forkable workspaces (template_id null) surface a static "Forkable workspaces only" affordance instead of throwing.  Pipeline is already covered by `__tests__/buildE2E.test.ts` (`overridesToServerPatch` round-trip) + `__tests__/regressionLock.test.ts §C` (call-site pinning).  No code change required.
4. **`event_study` / `backtest` backend payload quality** — user reported "garbage numbers". **Frontend chain verified clean for `event_study` (PR Decoder Audit, v3):** template node IDs match role bindings, dashboard renders honest fallbacks, `WorkflowResultCard` handles event-relative Series.  Any garbage numbers originate in the backend payload (slot bindings, operator output values) — fix lives outside this session.  `backtest` is paused on the backend; frontend correctly shows `WorkflowStatusCanvas` "paused" card.
5. **~~Operator intermediate-output surfacing~~** — **RESOLVED (PR Operator-Surfacing + Fork-Pipeline Audit, v4).**  Pre-flight read found the "Show all artifacts" toggle in `ResultsView.SpecialisedShell` already mounts `XAllArtifactsFallback` → `GenericResultsDashboard` rendering every non-terminal node via `NodeWidgetCard` → `resolveNodeRenderer` → per-artifact widget.  Operators dispatch identically to primitives (artifact-type-keyed).  PR renamed the toggle from "Show all artifacts" → "Show intermediate stages (N)" + tightened the description to explicitly mention operator outputs (vocabulary aligned with this §6).  Behavior 100% backwards compatible — same toggle, same state, same per-artifact rendering; only the user-facing copy + count changed.
6. **GenericPrimitiveBuilder output polish for the 32 tools with `monitor_elig=deferred` / scaffolded** — the schema-driven form + `AutoRenderer` works, but per-tool typed-result-renderers would raise the polish floor. This is per-module work; rows for those tools will move from `build_status=scaffolded` → `typed-renderer-built` as renderers land.

---

## 9. PR gates

A PR may land iff:

1. **Every surface code change has a corresponding row update.** Adding a Monitor widget for tool X requires updating the row `monitor_status: required → built`. Removing a stub requires updating `monitor_status: stub-claim → not-required` (or `→ deferred` if there's a planned proper widget).
2. **No central-file leakage.** PRs that add `case`/`if`/entries for a specific tool in any file under `src/components/{build,library,monitor,ask,layout}/` or in `src/lib/{toolNames,contextDecoder,modelRegistry,persistedModelAdapters}.ts` must be rejected unless the change is generic (i.e. applies to all tools symmetrically). Per-tool exceptions go inside the module folder.
3. **`tiers` claim matches delivery.** A module's `MODULE.tiers` array is a contract. Adding `monitor_surface` to the array requires shipping a non-stub widget in the same PR. Removing a tier requires removing the surface file.
4. **Tests track the contract.** `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`, which already cross-checks tier claims against `surfaces.*` files. Strengthening the invariant to also reject stub widgets (e.g. via a minimum-line-count or a manifest of "approved-non-stub" files) is a separate hardening item.

---

## 10. Change log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-26 | Initial drafting. Populated all 58 primitive rows + 3 workflow rows. Identified 3 containment violations (CPI / NFP / OTR-OFR stub claims). Defined classifications + PR gates. |
| v2 | 2026-05-26 | Stage C resolution.  Retracted the 3 containment violations from v1: CPI / NFP modules dropped `monitor_surface` + `ask_surface` tier claims (stub widget + regression Ask card); OTR-OFR module dropped `monitor_surface` tier claim (stub widget).  All 3 modules now `generic_runnable` only.  OTR-OFR row updated: Monitor eligibility corrected from `not-required` to `required (deferred)` — it IS desk-glanceable but needs the typed-detail endpoint first. |
| v3 | 2026-05-26 | Decoder + event_study audit.  §8 item 2 (decoder coverage) marked RESOLVED — audit found the `isRunnablePrimitive` fallback already covers every module by construction; added a coverage assertion in `routingCoverage.test.ts` as a permanent CI guard.  §8 item 4 (event_study frontend) split — frontend chain verified clean; "garbage numbers" attributed to backend payload (out of session scope).  §5 `event_study` row notes updated with audit findings.  No production code changes; test + doc only. |
| v4 | 2026-05-26 | Operator-surfacing + fork-pipeline audit.  §8 item 5 (operator intermediate-output surfacing) marked RESOLVED — pre-flight read found the existing "Show all artifacts" toggle in `ResultsView.SpecialisedShell` already renders every non-terminal node through the per-artifact widget registry; PR renamed the toggle to "Show intermediate stages (N)" + tightened descriptions across `ResultsView` + `EventStudyDashboard` + `RegimeRelationshipDashboard` + `BacktestDashboard` for vocabulary alignment with §6.  §8 item 3 (workspace fork loop) marked RESOLVED — end-to-end traced; pipeline fully wired through `WorkspaceOverridesProvider.apply` → `forkWorkspace` API → `navigate` to the new slug; already covered by existing tests (`buildE2E.test.ts`, `regressionLock.test.ts §C`).  §6 operator visibility policy expanded with the shipped intermediate-step surfacing details.  `dashboardContract.test.ts` updated to lock the new toggle copy.  100% backwards compatible — same toggle, same state, same per-artifact rendering; only the user-facing copy + count changed. |
