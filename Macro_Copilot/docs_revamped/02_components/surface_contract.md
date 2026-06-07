# Surface Contract

> The authoritative per-tool, per-surface status registry for the Macro_Copilot frontend.  As of v5 (Phase 0 of the `revamp` branch) the contract also tracks lifecycle-axis status per the [tool lifecycle standard](../03_standards/tool_lifecycle.md).

**Version:** v5 (Phase 0 of `revamp` — lifecycle axes added)
**Last reviewed:** 2026-05-26
**Status:** draft — Phase 0 in progress
**Branch discipline:** as of v5, rows live on the `revamp` branch.  `build` stays at v4 until `revamp` merges back.
**See also:** [`../03_standards/tool_lifecycle.md`](../03_standards/tool_lifecycle.md) (lifecycle standard); [`../05_decisions/0015-tool-metadata-db-table.md`](../05_decisions/0015-tool-metadata-db-table.md) (DB metadata architecture).

---

## 0. Purpose

Every backend artifact in this system — a **primitive**, an **operator**, or a **workflow** — can be exposed on up to four frontend surfaces: **Library**, **Ask**, **Build**, **Monitor**. This document is the single source of truth for:

1. Which surface each backend artifact **should** have (eligibility).
2. Which surface each backend artifact **currently has** (status).
3. The **classifications** that pin down what "having a surface" actually means for that artifact + surface combination.
4. **(v5)** Which lifecycle axes (per [`../03_standards/tool_lifecycle.md`](../03_standards/tool_lifecycle.md) §2) each tool has shipped — see §10.

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
A single backend tool with a typed input + typed output.  Today the backend ships **58 total primitives** the frontend must consider, broken down as: **52** in `rates_agent/workflows/__init__.py:_PRIMITIVE_SPECS` (with executable PrimitiveSpec) + **3** in `WORKFLOW_INCOMPATIBLE_TOOLS` (real backend tools whose output shape can't lift to a Series/Panel artifact) + **3** manifest-only / typed-detail tools (have typed-detail endpoints but no PrimitiveSpec entry — e.g. `calculate_butterfly_tool`, `scan_extremes_tool`).

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
- **Single-primitive** — `?context=<encoded>` with one decoded tool → `VirtualPrimitiveCanvas` mounts the module's **extended Build view** (`MODULE.surfaces.buildExtended` per the [rendering-density standard](../03_standards/rendering_density.md); legacy modules: `MODULE.surfaces.build` / generic builder fallback).
- **Multi-tool query (ad-hoc DAG)** — `?context=` with ≥2 decoded tools → DAG visualization with each primitive rendered as a node body via the module's **compact Build view** (`MODULE.surfaces.buildCompact` per the rendering-density standard).  Each node carries an expand affordance that opens that primitive's extended view in a modal/drawer overlay with a breadcrumb back to the DAG.  Legacy modules without `buildCompact` fall back to the artifact-type generic card.
- **Workflow template execution** — `/workspace/:slug` (persisted) → `BuildCompleted` with 4 tabs (DAG / Results / Parameters / Notes).  Workflow templates (`event_study`, `regime_conditioned_relationship`, `backtest`) are a SEPARATE backend concern from ad-hoc multi-tool queries; their dashboard rendering will be governed by a future workflow-contract analogous to the primitive contract.  **Provisional rule** until that contract lands: workflow-template DAG nodes also mount each primitive's compact view, never the extended view inline.

**Dual-view dispatch (rendering-density standard).** Per [`../03_standards/rendering_density.md`](../03_standards/rendering_density.md), every new primitive module ships TWO Build-side views — `buildExtended` (full canvas) and `buildCompact` (grid card) — and the Build canvas dispatches between them based on the single-tool vs multi-tool query context above.  Both views are MANDATORY for new modules; legacy modules are explicitly carved out and migrated opportunistically.

**Multi-tool queries vs workflow templates — two different concepts:**

| Concept | What it is | Where the DAG comes from |
|---|---|---|
| **Multi-tool query (ad-hoc DAG)** | The LLM stitches N primitive calls together AT QUERY TIME to answer an open-ended multi-step prompt (e.g. *"compare US 2s10s vs Bund 2s10s vs BTP 2s10s"*).  No backend template defines the DAG — it's emergent in the supervisor turn. | The supervisor turn's tool-call sequence; rendered by ad-hoc DAG infrastructure. |
| **Workflow template execution** | A backend-declared DAG recipe at `rates_agent/workflows/<template_id>/template.yaml`.  The structure is pre-defined; users fill in slot values. | The template file; rendered by the specialised dashboard for that archetype. |

Both render each primitive node via `buildCompact`; the difference is in the surrounding chrome (ad-hoc DAG infrastructure vs archetype-specific dashboard) and the lifecycle (ephemeral chat-turn vs persisted workspace).

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
| `calculate_ois_curve_spread_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (OisCurveSpread widget) |
| `calculate_ois_cross_market_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_ois_forward_rate_tool` | single-series-percent | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (OisForwardRate widget) |
| `get_ois_rate_level_tool` | single-series-percent | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (OisRateLevel widget) |
| `calculate_ois_butterfly_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (OisButterfly widget) |
| `calculate_swap_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_wirp_meeting_pricing_tool` | workflow-incompatible | registered | routed-generic (paused) | n/a (workflow_incompatible) | n/a | not-required | n/a |

### 4.3 Inflation-indexed bonds / linkers (10)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `get_real_yield_level_tool` | single-series-percent | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (RealYieldLevel widget) |
| `calculate_breakeven_inflation_simple_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (BreakevenInflation widget) |
| `calculate_forward_breakeven_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_breakeven_curve_spread_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (BreakevenCurveSpread widget) |
| `calculate_cross_country_breakeven_spread_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_real_yield_curve_spread_tool` | single-series-percent | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (RealYieldCurveSpread widget) |
| `calculate_cross_country_real_yield_spread_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_real_yield_butterfly_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (RealYieldButterfly widget) |
| `calculate_breakeven_butterfly_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `build_linker_panel_tool` | panel | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |

### 4.4 Inflation swaps (8)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `calculate_inflation_swap_rate_level_tool` | single-series-percent | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (InflationSwapRateLevel widget) |
| `calculate_inflation_swap_curve_spread_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | required | deferred |
| `calculate_inflation_swap_forward_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_cross_market_inflation_swap_spread_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (CrossMarketZcis widget) |
| `calculate_swap_breakeven_basis_simple_tool` | single-series-bps | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `calculate_inflation_swap_butterfly_tool` | single-series-bps | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (InflationSwapButterfly widget) |
| `build_zcis_panel_tool` | panel | registered | routed-generic | generic-builder | scaffolded | not-required | n/a |
| `scan_inflation_swaps_extremes_tool` | scanner-rankedlist | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (ZcisScanner widget) |

### 4.5 Policy futures / STIR (8)

| tool_name | output_kind | library | ask | build_archetype | build_status | monitor_elig | monitor_status |
|---|---|---|---|---|---|---|---|
| `policy_futures_get_futures_price_level_tool` | single-series-percent | registered | routed-generic | typed-result-renderer | dual-view-built | required | built (PolicyFuturesPriceLevel widget) |
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
5. **(v5) Lifecycle gates** — per [`../03_standards/tool_lifecycle.md`](../03_standards/tool_lifecycle.md) §7.  Tool-touching PRs additionally update the affected tool's lifecycle row in §10; static metadata edits go through the DB (`macro_data.tool_metadata`) with YAML mirrors updated in the same PR; methodology (config.yaml) changes require a parity-fixture regeneration + rationale + per-tool README update.

---

## 10. Lifecycle status (per tool — Phase 0+)

> Tracks the 7 lifecycle axes per tool per [`../03_standards/tool_lifecycle.md`](../03_standards/tool_lifecycle.md) §2.
>
> **This §10 is the roll-up summary view.**  The per-tool **granular truth** — every check-markable item across all 8 lifecycle stages — lives in `rates_agent/<domain>/tools/<tool>/LIFECYCLE_CHECKLIST.md` (or the operator / workflow analog), per [`../03_standards/lifecycle_checklist_template.md`](../03_standards/lifecycle_checklist_template.md).  The 4-value axis enum here (`shipped` / `in-progress` / `not-started` / `n/a`) maps to per-tool checklist stage-gate state per that standard's §7.  **Drift between this §10 row and the per-tool checklist is a PR-gate violation.**

### 10.1 Lifecycle axis status enum

For each tool × each of the 7 lifecycle axes, the status is one of:

| Status | Meaning |
|---|---|
| **`not-started`** | Axis not yet shipped for this tool. |
| **`in-progress`** | Work landed but not complete (e.g. pytest exists but SQL ground-truth doesn't). |
| **`shipped`** | Axis is complete per the lifecycle standard's definition. |
| **`n/a`** | Axis is not applicable to this tool (e.g. some tools have no Monitor widget by eligibility rule). |

### 10.2 Policy

- **Pilot tools (Phase 1)** — shown explicitly in §11.3 below.  All 7 axes get tracked.
- **All other 56 primitives** — defaulted to `not-started` for axes 1, 5, 7 (theoretical reference, known limitations, desk narrative — the human-curated DB fields); inherit existing status for axes 2, 3, 4, 6 (methodology, testing, contracts, frontend — most have partial state today).
- Per Phase 3 policy: a tool's lifecycle row gets fully filled only when the tool is touched for any reason.  No mass back-fill pass.

### 10.3 Pilot tools (Phase 1)

Two primitives selected per [`../03_standards/tool_lifecycle.md`](../03_standards/tool_lifecycle.md) §6.  Lifecycle rows for these will be populated in Phase 1; Phase 0 records the pilot selection only.

| tool_name | ax1 theoretical_ref | ax2 methodology (config) | ax3 testing (pytest + SQL + source) | ax4 input/output contracts | ax5 known_limitations | ax6 frontend surfaces | ax7 desk_narrative |
|---|---|---|---|---|---|---|---|
| `get_real_yield_level_tool` | shipped (DB tool_metadata.theoretical_reference filled; Tuckman 4e Ch. 22 + per-country primary issuers) | shipped (config.yaml + exposure: blocks for all 13 conventions per methodology_exposure.md) | in-progress (pytest + parity + SQL all green; source-material sign-off pending in Stage 8) | shipped (Pydantic Input + Metrics + Output + Phase-1 methodology-override fields) | shipped (DB tool_metadata.known_limitations filled) | shipped (BuildExtended + BuildCompact per rendering_density.md dual-view contract; Monitor RealYieldLevel widget; standalone typed-detail bridge endpoint) | shipped (DB tool_metadata.desk_narrative filled; manifest one_liner rewritten PM-facing) |
| `calculate_breakeven_inflation_simple_tool` | in-progress (curated in 2026-05-28 migration; live-DB apply + Stage-8 sign-off pending) | shipped (config.yaml + exposure: blocks for all 13 conventions per methodology_exposure.md) | in-progress (pytest incl. TestInputOverrides + TestExposureBlockContract + parity + SQL green; source-material sign-off pending Stage 8) | shipped (Pydantic Input + 3 Phase-1 override fields + Output) | in-progress (curated in migration; live-DB apply pending) | shipped (BuildExtended + BuildCompact per rendering_density.md dual-view; Monitor BreakevenInflation widget; standalone typed-detail bridge `/detail/breakeven`) | in-progress (desk_narrative curated in migration; live-DB apply pending; manifest one_liner rewritten PM-facing) |
| `calculate_real_yield_curve_spread_tool` | in-progress (curated in 2026-05-28 migration; live-DB apply + Stage-8 sign-off pending) | shipped (config.yaml + exposure: blocks for all 15 conventions per methodology_exposure.md) | in-progress (pytest incl. TestInputOverrides + TestExposureBlockContract + parity + SQL green; source-material sign-off pending Stage 8) | shipped (Pydantic Input + 3 Phase-1 override fields + Output) | in-progress (curated in migration; live-DB apply pending) | shipped (BuildExtended + BuildCompact per rendering_density.md dual-view; Monitor RealYieldCurveSpread widget; signed 5-zone regime slider; standalone typed-detail bridge `/detail/real_yield_curve_spread`) | in-progress (desk_narrative curated in migration; live-DB apply pending; manifest one_liner rewritten PM-facing) |

### 10.4 Workflow pilot — deferred

Workflows (`event_study`, `regime_conditioned_relationship`, `backtest`) are **out of scope for the primitive pilot**.  Workflow lifecycle standardisation happens after the primitive standard proves out — see [`../03_standards/tool_lifecycle.md`](../03_standards/tool_lifecycle.md) §5 (workflow caveats).

---

## 11. Change log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-26 | Initial drafting. Populated all 58 primitive rows + 3 workflow rows. Identified 3 containment violations (CPI / NFP / OTR-OFR stub claims). Defined classifications + PR gates. |
| v2 | 2026-05-26 | Stage C resolution.  Retracted the 3 containment violations from v1: CPI / NFP modules dropped `monitor_surface` + `ask_surface` tier claims (stub widget + regression Ask card); OTR-OFR module dropped `monitor_surface` tier claim (stub widget).  All 3 modules now `generic_runnable` only.  OTR-OFR row updated: Monitor eligibility corrected from `not-required` to `required (deferred)` — it IS desk-glanceable but needs the typed-detail endpoint first. |
| v3 | 2026-05-26 | Decoder + event_study audit.  §8 item 2 (decoder coverage) marked RESOLVED — audit found the `isRunnablePrimitive` fallback already covers every module by construction; added a coverage assertion in `routingCoverage.test.ts` as a permanent CI guard.  §8 item 4 (event_study frontend) split — frontend chain verified clean; "garbage numbers" attributed to backend payload (out of session scope).  §5 `event_study` row notes updated with audit findings.  No production code changes; test + doc only. |
| v4 | 2026-05-26 | Operator-surfacing + fork-pipeline audit.  §8 item 5 (operator intermediate-output surfacing) marked RESOLVED — pre-flight read found the existing "Show all artifacts" toggle in `ResultsView.SpecialisedShell` already renders every non-terminal node through the per-artifact widget registry; PR renamed the toggle to "Show intermediate stages (N)" + tightened descriptions across `ResultsView` + `EventStudyDashboard` + `RegimeRelationshipDashboard` + `BacktestDashboard` for vocabulary alignment with §6.  §8 item 3 (workspace fork loop) marked RESOLVED — end-to-end traced; pipeline fully wired through `WorkspaceOverridesProvider.apply` → `forkWorkspace` API → `navigate` to the new slug; already covered by existing tests (`buildE2E.test.ts`, `regressionLock.test.ts §C`).  §6 operator visibility policy expanded with the shipped intermediate-step surfacing details.  `dashboardContract.test.ts` updated to lock the new toggle copy.  100% backwards compatible — same toggle, same state, same per-artifact rendering; only the user-facing copy + count changed. |
| v5 | 2026-05-26 | **Phase 0 of `revamp` branch.**  Contract extended to track tool lifecycle per the new [`../03_standards/tool_lifecycle.md`](../03_standards/tool_lifecycle.md) standard.  New §10 "Lifecycle status" tracks 7 axes per tool (theoretical_reference, methodology, three-way testing, input/output contracts, known_limitations, frontend surfaces, desk_narrative).  Existing §10 (changelog) renumbered to §11.  §0 + §9 updated to reference the lifecycle doc + the new lifecycle PR gate (#5).  Pilot trio scoped to TWO primitives (`get_real_yield_level_tool` + `calculate_breakeven_inflation_simple_tool`); workflows deferred until primitive standard proves out.  Companion ADR [`../05_decisions/0015-tool-metadata-db-table.md`](../05_decisions/0015-tool-metadata-db-table.md) lands the DB-backed metadata table (`macro_data.tool_metadata`) as the source of truth for units + theoretical reference + known_limitations + desk_narrative.  No code changes outside docs + database/ folders. |
| v6 | 2026-05-28 | **Rails for Phase-1 pilot.**  §10 prefaced with a paragraph clarifying that the section is the roll-up summary view; the **per-tool granular truth** lives in `rates_agent/<domain>/tools/<tool>/LIFECYCLE_CHECKLIST.md` (or operator / workflow analog) per two new sibling standards in `03_standards/`: [`lifecycle_checklist_template.md`](../03_standards/lifecycle_checklist_template.md) (the 8-stage assembly-line template, `☐/☑/⏸/⊘` status, `pending` treatment for source-material verification, per-tool ↔ §10-row mapping) and [`methodology_exposure.md`](../03_standards/methodology_exposure.md) (the per-convention `exposure:` YAML-block decision protocol consumed by Stage 1A + the standalone-module bridge contract — every new module ships its own typed-detail endpoint at `/api/v1/rates/detail/<tool_kind>` and full `BuildSurface.tsx`; the `MODULE.typedView` closed family is frozen for new modules).  Per-tool `LIFECYCLE_CHECKLIST.md` shipped for the first pilot tool `get_real_yield_level_tool`.  No row-level edits to §4 / §5 / §10.3.  No code changes outside `docs_revamped/` and the per-tool checklist file. |
| v7 | 2026-05-28 | **Dual-view rendering-density contract.**  §3.3 (Build states) rewritten: distinguishes single-tool query (mounts extended view) from multi-tool query / ad-hoc DAG (mounts compact view per node + expand-to-extended modal) from workflow-template execution (deferred to future workflow contract, provisional same-rule).  New sibling standard [`../03_standards/rendering_density.md`](../03_standards/rendering_density.md) mandates every new primitive ship BOTH `surfaces.buildExtended` AND `surfaces.buildCompact` — no opt-in, no tier-parsimony exception.  Legacy modules carved out and migrated opportunistically.  Multi-tool queries (LLM-stitched ad-hoc DAGs) explicitly disambiguated from workflow templates (`event_study` / `regime_conditioned_relationship` / `backtest`) — different backend concerns sharing the word "multi-tool".  No row-level edits to §4 / §5 / §10.3.  No code changes outside `docs_revamped/` and the per-tool checklist updates. |
| v8 | 2026-05-28 | **Phase-1 pilot `get_real_yield_level_tool` Stage A closeout.**  §4.3 row updated: `build_archetype: typed-result-renderer`, `build_status: dual-view-built` (new value semantically equivalent to `typed-renderer-built` but signals both `buildExtended` + `buildCompact` ship per rendering_density §1), `monitor_status: built (RealYieldLevel widget)`.  §10.3 row reflects Stages 1-3 (Backend PR) + Stages 4-6 (Frontend PR) + Stage 7 (this row update + per-tool README + manifest mirror) closed; Stage 8 (`source_material_verified`) stays `in-progress`.  Frontend shipped: 18-file shared-shell library at `UI/.../src/components/shared/build/` (finance-blind, reusable); per-tool wrappers `surfaces/{BuildExtended,BuildCompact,monitor/RealYieldLevelWidget}.tsx`; focused-mode infrastructure (collapses sidebar + chat rail when extended view mounts; edge toggles to re-open).  Defensive frontend sanity filter at `±6%` real-yield bounds catches Bloomberg generic-roll artifacts (TD #31).  Per-tool README at `rates_agent/inflation_indexed_bonds/tools/real_yield_level/README.md`.  Multi-tool DAG container (shared infra for hosting compact cards) is the next deliverable in Stages D-E of the closeout plan — out of scope for this v8 row. |
| v20 | 2026-06-07 | **Frontend factory batch-2 tool 12 / 18 — `calculate_ois_forward_rate_tool` dual-view shipped (APPROVED).**  §4.2 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_status: deferred → built (OisForwardRate widget)` (monitor_elig was already `required`).  Forward-shape OIS implied forward overnight rate spanning two pillars (e.g. SOFR 1Y1Y / 2Y1Y / 5Y5Y) — risk-neutral implied policy-path nowcast.  Standalone typed-detail endpoint at `/api/v1/rates/detail/ois-forward-rate`; `OisForwardRateOutput` mirror type (snake_case preserved; mirrors backend `OISForwardRateCurrentMetrics` field-for-field including `forward_rate_pct`, `daily_change_bps`, `current_z_score`, `start_spot_rate_pct`, `end_spot_rate_pct`, plus `time_series` / `time_series_forward` / `time_series_zscore`).  Methodology label sourced via per-family `FAMILY_REGISTRY` in `oisForwardRateShared.ts` with explicit `TODO(PR10)` marker — same OIS sub-domain pattern as siblings (`OISForwardRateCurrentMetrics` does NOT carry `methodology_label`, mirroring OIS rate_level / curve_spread / butterfly).  Documented mockup deviation: Extended 9-cell KPI strip's "5D CHANGE" and "1M CHANGE" cells substituted with START SPOT / END SPOT because backend lacks `weekly_change_bps` / `monthly_change_bps` (config.yaml `methodology.planned_extensions` — deferred to follow-up PR so the wire shape stays stable in this migration).  Deviation documented in THESIS §"Known mockup/backend deviation", `oisForwardRateShared.ts:359-371`, and `BuildExtended.tsx:24-28`.  Reviewer (fresh-context Claude) emitted `APPROVED` with no blocking findings.  Pre-commit gate green: `npm run test:modules` ✓ (`calculate_ois_forward_rate_tool: 10 passed, 0 failed`; all module + registry tests passed) `npm run test:build` ✓ (all build-folder tests passed) `npm run typecheck` ✓.  `graphify update` skipped — not on PATH on this VM (recorded per `ORCHESTRATOR_PROMPT` Mirrors §2 fallback). |
| v19 | 2026-06-06 | **Frontend factory batch-2 tool 11 / 18 — `calculate_inflation_swap_rate_level_tool` dual-view shipped (APPROVED).**  §4.4 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_status: deferred → built (InflationSwapRateLevel widget)` (monitor_elig was already `required`).  Snapshot-shape ZCIS rate level (level + 1D change + 252d range/percentile + rolling z-score); mirrors the just-shipped `get_ois_rate_level_tool` snapshot pattern and the Phase-1 `get_real_yield_level_tool` pilot.  Standalone typed-detail endpoint at `/api/v1/rates/detail/inflation-swap-rate-level`; `InflationSwapRateLevelOutput` + `InflationSwapRateLevelMetrics` mirror types (snake_case preserved, exact field-by-field match to backend Pydantic schemas).  Methodology label sourced from the wire (`current_metrics.methodology_label`, threaded from backend `config.yaml:methodology.what_it_does`) — NOT a TSX literal.  Per-currency reference-index disclosure (HICPxT EUR / CPI-U NSA USD / RPI GBP / CPI Canada) cited per the catalog guardrail.  No tool-name alias bridge needed — frontend folder name matches the MCP function + workflow registry name (`calculate_inflation_swap_rate_level_tool`).  Reviewer (fresh-context Claude) emitted `APPROVED` with no blocking findings.  Pre-commit gate green: `npm run test:modules` ✓ (all module + registry tests passed) `npm run test:build` ✓ (all build-folder tests passed) `npm run typecheck` ✓.  `graphify update` skipped — not on PATH on this VM (recorded per `ORCHESTRATOR_PROMPT` Mirrors §2 fallback). |
| v18 | 2026-06-06 | **Frontend factory batch-2 tool 10 / 18 — `get_ois_rate_level_tool` dual-view shipped (APPROVED).**  §4.2 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_status: deferred → built (OisRateLevel widget)` (monitor_elig was already `required`).  First snapshot-shape tool of the batch-2 catalog; mirrors `get_real_yield_level_tool`'s Phase-1 snapshot pilot (level + change + z-score + range strip + sparkline) for the OIS curve family.  Standalone typed-detail endpoint at `/api/v1/rates/detail/ois-rate-level`; `OisRateLevelOutput` mirror type (snake_case preserved, `current_rate_pct` field-by-field with backend `OISRateLevelOutput` Pydantic schema).  Folder-vs-MCP alias bridged via `KNOWN_TOOL_ALIASES` in `src/lib/toolNames.ts` (already present pre-build per the v18 alias note); frontend folder is `get_ois_rate_level_tool`, MCP function + workflow registry use `calculate_ois_rate_level_tool`.  Per-family caveat registry (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS) hand-rolled in `oisRateLevelShared.ts` (sibling OIS sub-domain pattern: backend `OISRateLevelMetrics` does NOT carry `methodology_label`, mirroring the OIS curve_spread / butterfly siblings).  THESIS Q4 documents the one-line swap to wire-sourced once OIS PR10 ships.  Reviewer (fresh-context Claude) emitted `APPROVED` with no blocking findings.  Pre-commit gate green: `npm run test:modules` ✓ (`get_ois_rate_level_tool: 9 passed, 0 failed`; all module + registry tests passed) `npm run test:build` ✓ (all build-folder tests passed) `npm run typecheck` ✓.  `graphify update` skipped — not on PATH on this VM (recorded per `ORCHESTRATOR_PROMPT` Mirrors §2 fallback). |
| v17 | 2026-05-30 | **Frontend factory tool 9 / 9 — `policy_futures_get_futures_price_level_tool` dual-view shipped (APPROVED).**  §4.5 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_status: deferred → built (PolicyFuturesPriceLevel widget)` (monitor_elig was already `required`).  Final tool of the 9-tool initial batch; catalog now fully shipped (9 done / 0 blocked).  Resumed after prior structural-finding block (`reviewer_findings_structural_no_fix_attempt` from round-1: shell-density vs. mockup-density mismatch — Compact.png demanded a 2nd identity line + 6 secondary KPIs the shared `BuildCompactShell` does not expose).  Human applied `option_c_shell_density_acceptance` (the same precedent set by `calculate_breakeven_butterfly_tool` at commit `fdac7d2`): keep shell-standard density (single identity line + 3 KPIs) and document the deviation in THESIS `## Mockup conformance` rather than cross-tool refactor the shell.  Catalog design guardrails were updated to encode (a) the Option-(c) shell-density acceptance and (b) the identity-row hazard (do NOT prepend `· ` to `secondary` — the `IdentityBlock` already inserts the glyph; pass bare labels).  Snapshot shape: STIR contract price level + daily change + 252-day z-score + range strip; quote convention `100 - implied rate` (an UP price ⇒ DOWN implied rate / dovish) called out in methodology disclosure.  Standalone typed-detail endpoint at `/api/v1/rates/detail/policy-futures-price`; `PolicyFuturesPriceLevelOutput` mirror type (snake_case preserved, exact field-by-field match to backend `FuturesPriceLevelOutput` Pydantic schema).  Methodology honesty wire-sourced via `data.methodology_disclosure` (no TSX literal).  Both Build views consume the shared `usePolicyFuturesPrice` hook; Monitor widget uses its own local fetcher matching the linker analog pattern.  Reviewer (fresh-context Claude) emitted `APPROVED` confirming all three round-1 findings (#1 + #2 double-`·` bugs + #3 STRUCTURAL density) addressed; no blocking findings on the round-2 builder diff.  Pre-commit gate green: `npm run test:modules` ✓ (`policy_futures_get_futures_price_level_tool: 8 passed, 0 failed`; all 58 modules + registry pass) `npm run test:build` ✓ (all build-folder tests passed) `npm run typecheck` ✓.  `graphify update` skipped — not on PATH on this VM (recorded per `ORCHESTRATOR_PROMPT` Mirrors §2 fallback). |
| v16 | 2026-05-30 | **Frontend factory tool 2 / 9 — `calculate_breakeven_curve_spread_tool` dual-view shipped (APPROVED).**  §4.3 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_status: deferred → built (BreakevenCurveSpread widget)` (monitor_elig was already `required`).  Resumed after the human re-authored `mockups/{Compact,Extended}.png` to depict the correct 2-leg breakeven curve spread (intake commit `0b1eb86`); prior block (`mockup_content_mismatch`: mockups were byte-identical to tool 1's 3-leg butterfly mockups) cleared.  Same-curve breakeven tenor spread (US 2s10s BE, EUR 2s10s BE, GBP 2s10s BE, etc.); mirrors `calculate_real_yield_curve_spread_tool`'s design (same shape, different concept — inflation compensation, not real yield).  Standalone typed-detail endpoint at `/api/v1/rates/detail/breakeven-curve-spread`; `BreakevenCurveSpreadOutput` mirror type (snake_case preserved, exact field-by-field match to backend `BreakevenCurveSpreadCurrentMetrics` + `BreakevenCurveSpreadOutput` Pydantic schemas — `current_spread_bps`, `short_breakeven_bps`/`long_breakeven_bps`, `short_years`/`long_years`, `spread_label`, `methodology_label`, both `time_series_spread`/`time_series_zscore`).  Methodology honesty wire-sourced via `current_metrics.methodology_label` (no TSX literal).  Mockup deviation (justified, documented in THESIS): slope/regime captions use `Upward Sloping`/`Inverted`/`Flat` instead of the mockup's `Bull Flattening`/`Bear Steepening` because bull/bear/flattening/steepening describe directional MOVES not LEVELS — labelling a snapshot upward-sloping spread "Bull Flattening" would be finance-incorrect.  Reviewer (fresh-context Claude) emitted `APPROVED` with no blocking findings (one explicitly-justified deviation acknowledged).  Pre-commit gate green: `npm run test:modules` ✓ (`calculate_breakeven_curve_spread_tool` covered; all module + registry tests passed) `npm run test:build` ✓ (all build-folder tests passed) `npm run typecheck` ✓.  `graphify update` skipped — not on PATH on this VM (recorded per `ORCHESTRATOR_PROMPT` Mirrors §2 fallback). |
| v15 | 2026-05-29 | **Frontend factory tool 8 / 9 — `calculate_ois_curve_spread_tool` dual-view shipped (APPROVED).**  §4.2 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_status: deferred → built (OisCurveSpread widget)` (monitor_elig was already `required`).  Second OIS-family tool under the dual-view + standalone-bridge contracts (tool 7 `calculate_ois_butterfly_tool` was the first).  Same-curve 2-leg OIS tenor spread (USD-SOFR 2s10s, EUR-ESTR 2s10s, etc.); mirrors `calculate_real_yield_curve_spread_tool`'s design (same shape, different curve family).  Standalone typed-detail endpoint at `/api/v1/rates/detail/ois-curve-spread`; `OisCurveSpreadOutput` mirror type (snake_case preserved, exact field-by-field match to backend `OISCurveSpreadCurrentMetrics` + `OISCurveSpreadOutput` Pydantic schemas).  Methodology honesty carve-out (sub-domain consistent with tool 7): `OISCurveSpreadCurrentMetrics` does NOT carry a `methodology_label` field, so the disclosure is sourced from `OIS_CURVE_SPREAD_COMPACT_CAVEAT` in `oisCurveSpreadShared.ts` with a `TODO(PR10)` marker — flip to wire-sourced will be a one-line edit once the OIS sub-domain catches up.  Mockup deviations (bull/bear flattening regime substituted with steeper/flatter cues; top-right card #3 substituted with identity + caveat; decomposition row folded into headline strip) forced by wire constraints (bull/bear requires rate-direction history not in Output) or shell-level layout choices, following the just-approved OIS butterfly precedent.  Reviewer (fresh-context Claude) emitted `APPROVED` with no blocking findings.  Pre-commit gate green: test:modules ✓ (`calculate_ois_curve_spread_tool: 6 passed, 0 failed`; all module + registry tests passed) test:build ✓ (all build-folder tests passed) typecheck ✓.  `graphify update` skipped — not on PATH on this VM (recorded per `ORCHESTRATOR_PROMPT` Mirrors §2 fallback). |
| v14 | 2026-05-29 | **Frontend factory tool 7 / 9 — `calculate_ois_butterfly_tool` dual-view shipped (APPROVED).**  §4.2 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_elig: not-required → required`, `monitor_status: n/a → built (OisButterfly widget)`.  First OIS-family tool brought under the dual-view + standalone-bridge contracts (sibling pattern to the three inflation_swaps + linker butterflies shipped earlier in this wake — same 3-point same-curve fly, different curve family).  Standalone typed-detail endpoint at `/api/v1/rates/detail/ois-butterfly`; `OisButterflyOutput` mirror type (snake_case preserved; exact field-by-field match to backend `OISButterflyCurrentMetrics` + `OISButterflyOutput` Pydantic schemas).  Methodology honesty carve-out: `OISButterflyCurrentMetrics` does NOT carry a `methodology_label` field (distinct from linker/ZCIS sibling Outputs), so the disclosure is sourced from `OIS_BUTTERFLY_COMPACT_CAVEAT` in `oisButterflyShared.ts` with a `TODO(PR10)` marker — flip to wire-sourced will be a one-line edit once the OIS sub-domain catches up.  Family registry honestly uses the playbook's actual short-form curve_family identifiers (USD_SOFR / EUR_ESTR / GBP_SONIA / JPY_OIS / AUD_OIS / CAD_OIS) while labelling the overnight index (SOFR / ESTR / SONIA / TONA / AONIA / CORRA) on the surface — matches the backend schema's "honest disclosure" comment.  Reviewer (fresh-context Claude) emitted `APPROVED` with no blocking findings (one minor non-blocking THESIS prose self-inconsistency: "Mockup conformance" mentions 2 cards while Q2 enumerates all 3 — implementation matches mockup, prose only).  Pre-commit gate green: test:modules ✓ (6 passing per-module checks) test:build ✓ typecheck ✓.  Builder produced 1-byte stdout log (transient claude --print buffering — same pattern as tool 5 reviewer dispatch 2 first attempt) but all 8 artifact slots populated on-disk; no retry needed since the diff was complete. |
| v13 | 2026-05-29 | **Frontend factory tool 6 / 9 — `scan_inflation_swaps_extremes_tool` dual-view shipped (APPROVED).**  §4.4 row updated: `output_kind: scanner-result → scanner-rankedlist`, `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_status: deferred → built (ZcisScanner widget)`.  First scanner-shape tool brought under the dual-view + standalone-bridge contracts (compact view is a top-N ranked table, NOT a sparkline — distinct from the four prior single-series-bps tools).  Standalone typed-detail endpoint at `/api/v1/rates/detail/zcis-scanner`; `ScanInflationSwapsExtremesOutput` + `ScanInflationSwapsExtremesResultRow` mirror types (snake_case preserved, `signal` Literal, `methodology_disclosure` required at row + response level).  Wire's `methodology_disclosure` threaded into Extended `MethodologyCard` "Disclosure" row + Compact footer `title=` tooltip + Monitor widget caveat tooltip (P5 honesty preserved on the load-bearing field).  Mockup deviation (`TIMEFRAME` / `RANK BY` / per-row `PCTILE` absent) documented in THESIS Q3/Q4 as schema-locked / future-scenario — builder correctly aligned to wire over aspirational mockup.  Reviewer (fresh-context Claude) emitted `APPROVED` with one non-blocking observation (unused `instrumentLabel` import — code-hygiene smell, not a typecheck failure since `noUnusedLocals` is off).  Pre-commit gate green: test:modules ✓ (7 passing per-module checks) test:build ✓ typecheck ✓. |
| v12 | 2026-05-29 | **Frontend factory tool 5 / 9 — `calculate_inflation_swap_butterfly_tool` dual-view shipped (APPROVED-AFTER-FIX).**  §4.4 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_elig: not-required → required`, `monitor_status: n/a → built (InflationSwapButterfly widget)`.  Same-curve 3-point ZCIS butterfly (USD 2-5-10, EUR 2-5-10, GBP 2-5-10, etc.).  Standalone typed-detail endpoint at `/api/v1/rates/detail/zcis-butterfly`; `InflationSwapButterflyOutput` mirror type.  Reviewer (fresh-context Claude) emitted `CHANGES REQUIRED` with 2 concrete + actionable + bounded findings against `surfaces/BuildExtended.tsx`: (a) identity primary used raw `curveFamily` instead of space-separated market label (deviation from mockup); (b) inlined `bucketForPercentile` logic bypassed the existing shared helper.  Both fixed in Dispatch 3 (single-file fix; touched only `surfaces/BuildExtended.tsx`).  Pre-commit gate green after fix: test:modules ✓ test:build ✓ typecheck ✓.  First initial-reviewer-output failure of the wake (1-byte log on first dispatch — transient; succeeded on retry without any prompt change), confirming Case F-style transient failures may simply be retried before escalating to `waiting_quota`. |
| v11 | 2026-05-29 | **Frontend factory tool 4 / 9 — `calculate_cross_market_inflation_swap_spread_tool` dual-view shipped.**  §4.4 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_elig: not-required → required`, `monitor_status: n/a → built (CrossMarketZcis widget)`.  First inflation_swaps tool brought under the dual-view + standalone-bridge contracts.  Standalone typed-detail endpoint at `/api/v1/rates/detail/cross-market-zcis`; `CrossMarketInflationSwapSpreadOutput` mirror type (snake_case, includes `index_family_caveat` + `methodology_label`).  Catalog design guardrail satisfied: the "CPI-U vs HICPxT vs RPI not fungible" caveat is sourced from `current_metrics.index_family_caveat` (wire-honest, NOT a TSX literal) and surfaced on the compact view's caveat footer, the Monitor widget's bottom row, and the extended view's top-right Index-Families card.  Reviewer (fresh-context Claude) emitted `APPROVED` with no findings.  Pre-commit gate green: test:modules ✓ test:build ✓ typecheck ✓. |
| v10 | 2026-05-29 | **Frontend factory tool 3 / 9 — `calculate_real_yield_butterfly_tool` dual-view shipped.**  §4.3 row updated: `build_archetype: generic-builder → typed-result-renderer`, `build_status: scaffolded → dual-view-built`, `monitor_elig: not-required → required`, `monitor_status: n/a → built (RealYieldButterfly widget)`.  Shipped per `BUILD_GUIDE.md` Stages 4-6 + `rendering_density.md §1` dual-view contract + `methodology_exposure.md §5` standalone-bridge contract.  Files: `module.ts` (tiers=[generic_runnable, custom_build_surface, monitor_surface], typedView=null), `THESIS.md` (5-question contract incl. Mockup-conformance subsection documenting the "TIPS"→"linkers" caveat generalization), `surfaces/{BuildExtended,BuildCompact,realYieldButterflyShared,monitor/RealYieldButterflyWidget}.tsx`, `__tests__/module.spec.ts` (round-trip + dual-view + standalone-bridge + mockup-folder checks), standalone typed-detail endpoint at `/api/v1/rates/detail/real-yield-butterfly`, `fetchDetailRealYieldButterfly` service helper, `RealYieldButterflyOutput` mirror type.  Reviewer (independent Claude, fresh context) emitted `APPROVED` with no findings.  Pre-commit gate green: `npm run test:modules` ✓, `npm run test:build` ✓, `npm run typecheck` ✓.  No row added to §10.3 (pilot-pinned).  Tool 2 of the factory's batch (`calculate_breakeven_curve_spread_tool`) is `blocked` on misfiled mockup PNGs (byte-identical to tool 1's mockups — see `automation/frontend_automation/frontend_tool_catalog.yaml` block_reason for full evidence); requires human re-author of the two PNGs before it can ship.  Tool 1 (`calculate_breakeven_butterfly_tool`, commit fdac7d2) shipped without a §11 entry — its §4.3 row remains stale (scaffolded/not-required) pending a separate update wake. |
| v9 | 2026-05-28 | **Phase-1 pilots Stage B + C — `calculate_breakeven_inflation_simple_tool` + `calculate_real_yield_curve_spread_tool` brought to full parity with `get_real_yield_level_tool`.**  §4.3 rows updated: both → `build_archetype: typed-result-renderer`, `build_status: dual-view-built`, `monitor_status: built` (BreakevenInflation + RealYieldCurveSpread widgets); curve_spread `output_kind` corrected `single-series-bps → single-series-percent` (the spread is in PERCENT; changes in bps); curve_spread `monitor_elig` flipped `not-required → required`.  §10.3 gained a `calculate_real_yield_curve_spread_tool` row and the breakeven row was fully populated.  Backend (Stage 1-3): `exposure:` blocks added to every convention (13 breakeven / 15 curve_spread); 3 rolling-z-score Input overrides + `field_name` exposed (mirror of real_yield_level); `_conventions_from_config(config, params)` override resolver; MCP wrappers extended with integer-sentinel z-kwargs; manifest `one_liner` rewritten PM-facing + `pm_overridable` derived; `TestInputOverrides` + `TestExposureBlockContract` added (170 compute tests green; parity + lint green); curated-metadata migrations written (`database/migrations/2026-05-28_phase1_*_curated.sql`) — NOT applied to the live DB (off-limits this PR), so DB-backed axes (1/5/7) are `in-progress` pending apply.  Frontend (Stage 4-6): per-tool surfaces (`BuildExtended` / `BuildCompact` / `monitor/*Widget` / `*Shared.ts`) + module.ts (dual-view + monitor) + THESIS (Q1-Q5) + dual-view spec; standalone typed-detail endpoints `/detail/breakeven` + `/detail/real_yield_curve_spread` + service helpers + frontend types.  Shared infra fix: `__test-utils.ts` + `loaderPresence.test.ts` surface→tier maps + the FM8 file-existence check now recognise `buildExtended` / `buildCompact` (was a latent gap that also affected `get_real_yield_level_tool`).  `shared_mockups/README.md` documents the multi-tool DAG mockup + the open Stage-D design questions.  Stage 8 (`source_material_verified`) stays `in-progress` for both. |
