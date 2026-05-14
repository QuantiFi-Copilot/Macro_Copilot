# Build Workspace QA Matrix

**PR8 — end-to-end verification checklist for the Build revamp.**

This document is the canonical reproducible verification surface for
the Build page after PR1 → PR7.  Reviewers walk every row before
sign-off; CI runs the runtime test suite (see "Automated checks"
below) on every PR.

Architecture summary, top to bottom:

```
PR1   Routing coverage             — every known tool reaches a deterministic Build surface
PR2   Schema-driven primitive builders
PR3   Artifact payload client + persisted-artifact loading (no re-runs)
PR4   Payload-backed generic artifact widgets
PR5   Workspace fork via slot overrides
PR6   Branch-aware DAG anatomy view
PR7   Workflow-specific Results dashboards
PR8   This QA matrix + e2e regression coverage + bounded polish
```

The Build shell mounts at `/workspace*` and routes via four URL
shapes, listed alongside their canvas in the table below.


## Automated checks

| Command                                                | Pass criterion                                    |
| ------------------------------------------------------ | ------------------------------------------------- |
| `npm run typecheck` (or `node_modules/.bin/tsc -p tsconfig.app.json --noEmit`) | tsc clean — no errors                              |
| `npm run typecheck:full` / `tsc -p tsconfig.json --noEmit` | full project clean                                 |
| `npm run build` / `vite build`                          | production bundle builds (chunk-size warnings are expected and pre-PR8) |
| `npm run test:build` (PR8 — see `scripts/run_build_tests.mjs`) | every Build-folder test passes (currently 281 assertions across 14 test files after PR1-followup) |

The runtime test suite uses esbuild to bundle each `.test.ts` file
and runs the bundle with Node.  The repo does not ship a JS test
runner (no vitest / jest / playwright) and the brief explicitly
discourages adding one in PR8 ("do not add a heavy new testing
dependency only for this PR unless the repo already supports it").

## Coverage matrix — what's tested where

| Concern                                  | Test file                                                              | Assertions |
| ---------------------------------------- | ---------------------------------------------------------------------- | ---------- |
| PR1 — routing coverage                   | `src/components/build/primitive/__tests__/routingCoverage.test.ts`     | 36         |
| PR2 — schema-driven primitive builders   | `src/components/build/primitive/__tests__/genericBuilder.test.ts`      | 28         |
| PR3 — artifact payload client + types    | `src/components/build/widgets/__tests__/artifactPayload.test.ts`       | 24         |
| PR3 — RichModelWidget no-rerun contract  | `src/components/build/widgets/__tests__/richModelWidgetContract.test.ts` | 8        |
| PR4 — widget format helpers              | `src/components/build/widgets/__tests__/widgetFormat.test.ts`          | 32         |
| PR4 — widget structural contract         | `src/components/build/widgets/__tests__/widgetContract.test.ts`        | 11         |
| PR5 — slot-driven parameter controls     | `src/components/build/parameters/__tests__/slotControls.test.ts`       | 19         |
| PR5 — parameters tab structural contract | `src/components/build/parameters/__tests__/parametersContract.test.ts` | 11         |
| PR6 — DAG model                          | `src/components/build/dag/__tests__/dagModel.test.ts`                  | 14         |
| PR6 — DAG view contract                  | `src/components/build/dag/__tests__/dagContract.test.ts`               | 12         |
| PR7 — dashboard registry + resolver      | `src/components/build/results/__tests__/dashboardRegistry.test.ts`     | 18         |
| PR7 — Results tab structural contract    | `src/components/build/results/__tests__/dashboardContract.test.ts`     | 11         |
| PR8 — e2e Build flow matrix              | `src/components/build/__tests__/buildE2E.test.ts`                      | 27         |
| **PR1-followup — persisted-model adapters** | `src/components/build/widgets/__tests__/persistedModelAdapters.test.ts` | **20**  |

The PR8 e2e file walks every row of the manual matrix below through
the pure-logic layer (decoder → builder kind → dashboard kind →
artifact resolver) so a future regression that breaks a row produces
a CI failure, not a manual-QA escape.

## Manual QA matrix

Each row below is reproducible from a desktop browser (≥ 1280 px
wide) and a 768 px narrow viewport.  Open the network tab — the
"Network signal" column lists the request the row should/shouldn't
fire.  All routes are relative to the Build app root.

### Empty + entry-state flows

| # | Route                                | User action                                  | Expected canvas                                      | Tab / state behaviour                                                          | Network signal                          |
| - | ------------------------------------ | -------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------ | --------------------------------------- |
| 1 | `/workspace`                         | Empty Build shell                            | `BuildEmptyState` with the LLM composer              | No tabs — composer + sidebar only                                              | No artifact / tool fetch                |
| 2 | `/workspace?builder=calculate_pca_yield_curve_tool` | Library "Open in builder" on PCA       | `BuilderCanvas` → `ModelWorkspacePage` (3-col playground) | Right rail shows methodology; user can edit + run                              | `GET /tools/calculate_pca_yield_curve_tool` |
| 3 | `/workspace?workflow=event_study&workflow_status=known` | Workflow handoff without a slug         | `WorkflowStatusCanvas` ("workflow ran, no workspace persisted") | Back-to-Ask CTA visible                                                        | No network calls                        |
| 4 | `/workspace?workflow=backtest&workflow_status=paused` | Workflow handoff for paused archetype  | `WorkflowStatusCanvas` (paused state, amber tone)    | Workflow paused copy; back-to-Ask                                              | No network calls                        |
| 5 | `/workspace?workflow=unknown_id&workflow_status=unknown` | Stale or renamed workflow link        | `WorkflowStatusCanvas` (unknown state, coral tone)   | "Workflow not recognised" copy + back-to-Ask                                   | No network calls                        |

### Library → Build (PR2 + PR1)

| # | Route source                                                                  | Tool                                          | Expected canvas                                      | Acceptance                                                                                                       |
| - | ----------------------------------------------------------------------------- | --------------------------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 6 | Library → "Open in Build" on `calculate_curve_spread_tool`                    | curve spread (typed view)                     | `SpreadPrimitiveView` with editable dropdowns        | curve / short_tenor / long_tenor / lookback / field_name all editable; chart fetches on change                   |
| 7 | Library → "Open in Build" on `get_yield_levels_tool`                          | yield levels (typed view)                     | `YieldPrimitiveView` with editable dropdowns         | curve / tenor / lookback / field_name editable; chart fetches; PR2 widened controls fixed the UST-10Y hardcode    |
| 8 | Library → "Open builder" on `calculate_swap_spread_tool`                      | swap spread (generic builder)                 | `GenericPrimitiveBuilder` (3-col schema-driven form) | sovereign + OIS curve + tenor + lookback editable; Run button posts to `/tools/calculate_swap_spread_tool/run`     |
| 9 | Library → "Open builder" on `calculate_ois_curve_spread_tool`                 | OIS curve spread (generic builder)            | `GenericPrimitiveBuilder`                            | OIS curve + tenor controls + Run                                                                                  |
| 10 | Library → "Open builder" on `calculate_breakeven_inflation_tool`             | breakeven inflation (generic builder)         | `GenericPrimitiveBuilder`                            | nominal + real curves + tenor + Run                                                                               |
| 11 | Library → "Open builder" on `build_sovereign_yield_panel_tool`               | sovereign panel (generic builder)             | `GenericPrimitiveBuilder`                            | legs array editor (JSON fallback) + dates + Run                                                                   |
| 12 | Library → "Open Build (unsupported)" on `scan_ois_extremes_tool`             | OIS extremes (paused, no backend)             | `UnsupportedKnownToolCanvas`                         | "Tool is paused — Ask cannot run it today" honest copy                                                            |

### Ask handoff (PR1 + PR3)

| # | Trigger                                                                       | Expected URL after click                                                          | Canvas                                                | Acceptance                                                                                |
| - | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 13 | Ask single-primitive answer → "Open in Build"                                 | `/workspace?context=<single-tool>`                                                | `VirtualPrimitiveCanvas` (typed or generic)           | Tool name normalised; params pre-filled                                                  |
| 14 | Ask multi-primitive answer → "Open in Build" (different tools)                | `/workspace?context=<multi-tool>`                                                  | `MultiPrimitiveCanvas` (chip strip per tile)          | Typed tiles render charts; generic tiles open builder on click                            |
| 15 | Ask workflow turn that persisted → "Open in Build"                            | `/workspace/<slug>`                                                                | `BuildCompleted` (full tabs)                          | Persisted artifacts load via `/artifacts/{hash}/payload` (PR3); no `runPrimitive` fires    |

### Persisted workflow workspaces (PR5 + PR6 + PR7)

| #  | Workspace                                | DAG tab                                                                                     | Results tab                                                                                                                                | Parameters tab                                                                                                                                                                            |
| -- | ---------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 16 | event_study                              | Branch-aware view: signal+target sources, threshold_events / event_windows / aggregate paths, terminal `compare` highlighted in amber.  Slot labels visible.       | `EventStudyDashboard`: Setup → Events → Event windows → Conditional vs unconditional aggregate → Abnormal response.  Show-all artifacts toggle. | `WorkspaceSlotsPanel` at top lists editable slots: signal/target tool + threshold + post_window + lookback.  Per-stage inspector is read-only. Apply & fork posts `slot_overrides`.        |
| 17 | regime_conditioned_relationship          | Branch-aware: lhs/rhs/regime_signal parallel sources; relationship → beta; regime_diff → high_mask/low_mask → high_betas/low_betas → high_summary/low_summary → compare. | `RegimeRelationshipDashboard`: Inputs → Regime construction → Relationship model → Conditional summaries → Comparison output.  Show-all toggle. | Slots: lhs/rhs/regime_signal tools, regression_window_days, regime_threshold.  Per-stage inspector read-only.                                                                              |
| 18 | backtest (if any direct invocation persisted) | Branch-aware: signal → events → trades; price_panel + financing → evaluate → summarize.  Terminal in amber.                                                          | `BacktestDashboard`: Always-on paused banner; sections render real artifacts where persisted, `MissingArtifactCard` where not.                | Slots: signal_tool_name, signal_params dict, threshold, sovereign_curves, tenors, start_date, end_date, financing_method.  Read-only stage detail.                                          |
| 19 | unknown workflow / legacy (no template_id) | Branch-aware if edges exist; linear fallback with banner if not.  Cycle/orphan banners surface honestly.                                                              | `GenericResultsDashboard` (the pre-PR7 grid)                                                                                                | If template_id null → "This workspace is not forkable" banner; per-stage inspector still works                                                                                            |

### Parameter override / fork (PR5)

| #  | User action                                                                                | Expected outcome                                                                                                                                                          | Failure handling                                                          |
| -- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 20 | event_study workspace → Parameters → change `signal_params.window_days` 126 → 252 → Apply  | PendingOverridesBar enables → POST `/workspace/<slug>/fork` body `{slot_dict_overrides: {signal_params: {window_days: 252}}}` → navigate to new workspace slug.            | Original workspace unchanged; new workspace listed in sidebar with parent link |
| 21 | Apply with no overrides queued                                                              | Apply button disabled; no fork fired                                                                                                                                       | n/a                                                                       |
| 22 | Fork backend returns 422 (bad slot value)                                                   | PendingOverridesBar shows error; **local edits preserved** (per PR5 contract); user can correct + re-apply                                                                  | No silent reset                                                           |
| 23 | Workspace has `template_id === null`                                                        | "Workspace is not forkable" banner; no edit affordance                                                                                                                     | n/a                                                                       |
| 24 | Per-stage editor — try editing a node param                                                 | Input is `disabled`; lock icon visible; banner explains "stage details are read-only — edit slots above"                                                                   | n/a                                                                       |

### Artifact / widget failures (PR3 + PR4)

| #  | Scenario                                                                | Expected widget state                                                                                       |
| -- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 25 | EventSet widget — workspace has `event_dates: []` payload                | Widget shows "0 events flagged" + honest zero-events copy.  Count is NOT `row_count`.                       |
| 26 | Panel widget — one-row summary panel                                     | Each column rendered as a labelled metric cell.  Date NOT coerced to 1970.                                  |
| 27 | SeriesSet widget — multi-member set                                      | Chip strip (or `<select>` ≥ 8 members) lets user switch active member; per-member preview reuses Series primitives |
| 28 | WindowedPanel widget — t-5 … t+5 offsets                                 | Header shows event count + `t-5 … t+5` + offset count.  Preview table uses `t-N` / `t0` / `t+N` column labels |
| 29 | TradeSet widget — empty trades payload (paused backtest)                 | `PausedState` caption; no trade table; no fabricated PnL                                                    |
| 30 | Artifact payload 404 (orphaned hash)                                     | Widget body shows `ArtifactPayloadError` card with Retry button; sibling widgets unaffected                  |
| 31 | Artifact payload 503 (object storage down)                               | Widget body shows "storage offline, retry later" hint; Retry preserves the queue                            |
| 32 | Workspace with two widgets pointing at the same artifact hash            | Network tab shows ONE `GET /artifacts/<hash>/payload`; in-flight de-dup via PR3 cache                       |

### DAG view (PR6)

| #  | Scenario                                                                 | Expected behaviour                                                                                          |
| -- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| 33 | Branching workflow (event_study)                                         | Multiple lanes visible; signal/target on separate ranks; merge at `windows`; terminal `compare` ringed amber |
| 34 | Click any node                                                            | DagInspector opens below canvas with params + inputs + outputs + artifact hash chip                          |
| 35 | "Show edge labels" toggle                                                  | Slot labels render at curve midpoints; toggling hides them                                                  |
| 36 | "Compact" density toggle                                                  | Card width reflows; canvas size recalculates                                                                |
| 37 | "Fit" button                                                              | Canvas scrolls to top-left                                                                                  |
| 38 | Workspace with orphan edges                                               | Amber warning banner above canvas: "ORPHAN EDGES — One or more edges reference unknown nodes…"              |
| 39 | Workspace with missing edges (legacy)                                     | Linear-fallback banner + single-lane strip                                                                   |
| 40 | Workspace with a cycle (defensive)                                        | `CYCLE DETECTED` banner; cycle nodes pinned at right with `cycleFallback: true` styling                     |

### Results dashboards (PR7)

| #  | Scenario                                                              | Expected dashboard                                                                                          |
| -- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 41 | event_study workspace                                                  | `EventStudyDashboard` with 5 sections                                                                       |
| 42 | regime_conditioned_relationship workspace                              | `RegimeRelationshipDashboard` with 5 sections                                                               |
| 43 | backtest workspace                                                     | `BacktestDashboard` with paused banner; sections render real or `MissingArtifactCard`                       |
| 44 | Unsupported workflow                                                   | `GenericResultsDashboard` (the pre-PR7 grid)                                                                |
| 45 | Specialised dashboard + "Show all artifacts" toggle                    | Chevron expands the generic grid below the specialised view                                                  |
| 46 | event_study workspace missing the `windows` node                       | EventWindow section renders `MissingArtifactCard` honestly; other sections still render                      |
| 47 | backtest workspace with NO persisted execution stages                  | Paused banner copy pivots to "execution unavailable — nothing fabricated"; trades/eval/summary all show missing |

## Polish issues fixed in PR8

| # | Issue                                                                                            | Fix                                                                                              |
| - | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| 1 | `nodeRendererRegistry.ts:249` always-true `!==` comparison flagged by esbuild on every build     | Introduced a module-scoped `DEFAULT_FALLBACK_SENTINEL` reference and compare against it          |
| 2 | Persisted PCA / rolling-regression / attribution / half-life / beta-adjusted-spread cards passed `StoredArtifact.payload` to renderers that expected the live `*Output` dict (`current_metrics`, `time_series_*`); rendered empty bodies | PR1-followup: dropped the legacy `Renderer` prop; replaced with a per-tool adapter (`persistedModelAdapters.ts`) that classifies the payload + drives an honest "what's in this saved snapshot" / "what's not" surface |
| 3 | Stale "re-runs the tool / until backend ships payload endpoint" docstrings in `Pca-/RollingRegression-PreviewWidget.tsx` | Removed; preview widgets now document the adapter contract |

## Manual reproduction notes

1. Stand up the backend per top-level `README.md` (FastAPI + Postgres + object storage).
2. Seed event_study + regime workspaces by sending Ask prompts the substrate routes to those templates (`canonical proof Q1` / `canonical proof Q2` in the template YAMLs).
3. Open Build at `/workspace/<slug>` and walk the matrix above.

For headless / Playwright-style verification: the project does not
ship Playwright today.  When/if it does (separate scope), every row
in this matrix has a corresponding selector / assertion already in
the runtime test suite — translation will be 1:1.

## What this matrix doesn't cover

These are deliberately deferred to future PRs and are NOT acceptance
criteria for PR8:

- Workflow editor (drag-drop DAG node creation / deletion).
- Re-enabling paused backtest execution on the LLM-facing MCP surface.
- Real chat-assistant behaviour inside the Build copilot rail.
- Per-archetype heatmap / hero visualisations.
- Mobile viewports.
- Authentication / multi-user.
