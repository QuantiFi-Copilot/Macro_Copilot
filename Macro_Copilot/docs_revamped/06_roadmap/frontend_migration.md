# Frontend Migration Roadmap

> The plan for migrating the existing surface-organised frontend codebase to the module-organised architecture defined in [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md). Refactor and new-feature work are deliberately separated — refactor PRs do not add new behaviour; new-feature PRs do not refactor existing surfaces.

**Version:** v2
**Last reviewed:** 2026-05-25
**Status:** roadmap. Each stage updates this doc with progress; stages bump version (v2.1, v2.2, …) as they land.
**Operationalises principles:** [FP1–FP13](../00_thesis/03_frontend_thesis.md), [FM1–FM12](../02_components/frontend_module/README.md).
**See also:** [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md), [`../02_components/frontend_module/runbook.md`](../02_components/frontend_module/runbook.md).

---

## The separation principle

The migration handles two fundamentally different kinds of work:

1. **Refactor work.** Moving existing, functional UI code from page-folder locations (`src/components/build/widgets/PcaPreviewWidget.tsx`, etc.) into module-folder locations (`src/modules/primitives/calculate_pca_yield_curve_tool/surfaces/PreviewWidget.tsx`). No new behaviour. Same code, new home, derived registries.

2. **New-feature work.** Building UI for the 18 backend primitives that ship today with no frontend surface (event signals, cash bonds, inflation primitives). New JSX, new THESIS, new tier claims.

**These two kinds of work do not mix in the same PR.** Stages 4a/4b/4c are pure refactor; Stages 5+ are pure new-feature. The reason: mixing them produces PRs where the diff is impossible to review cleanly (is this line a refactor or a behaviour change?), and the legacy-cleanup logic that refactor PRs ship would interfere with the registration logic new-feature PRs ship.

This separation is the canonical reason the migration is staged the way it is below.

## Stages at a glance

| Stage | What lands | Kind | Status | PR count | Effort |
|---|---|---|---|---|---|
| **0** | The constitution (22-document docs_revamped/ omnibus) | Docs | **In flight (PR #211)** | 1 | Done |
| **1** | Static-registry catch-up + test repair (close the 20-tool gap; fix 3 failing tests) | Hold-the-line patch | Not started | 1 | Small (~2h) |
| **2** | Build the `src/modules/` infrastructure (loader, types, test helpers, ESLint boundary rule) | Infrastructure | Not started | 1 | Small-medium (~half-day) |
| **3** | Scaffold module folders for ALL primitives (existing + 18 missing); skeleton `module.ts` per folder; existing-primitive specs point at legacy surface locations | Scaffolding | Not started | 1 | Medium (mechanical, ~1 day) |
| **4a** | Refactor sovereign + OIS primitives' surfaces INTO their module folders | Pure refactor | Not started | 1 | Medium (~1 day) |
| **4b** | Refactor rich-model primitives' surfaces (PCA, regression, attribution, half-life, beta-adjusted spread) | Pure refactor | Not started | 1 | Medium (~1 day) |
| **4c** | Refactor futures primitives' surfaces (bond_futures + policy_futures) | Pure refactor | Not started | 1 | Medium (~1 day) |
| **5** | Pilot new primitive: `calculate_cpi_surprise_tool` (full surfaces — Monitor + Ask + generic Build) | New-feature | Not started | 1 | Medium |
| **6+** | Remaining 17 new-feature primitives (one or two per PR) | New-feature | Not started | ~10 | Small-medium each |
| **N** | Cleanup: move typed primitive views to `shared/render/typed/`; flip the strict "no hand-authored registries" CI gate | Cleanup | Not started | 1 | Small |

**Net total: ~17 PRs over ~3 months at one PR every 4–5 days.** Stage 1 (hold-the-line) deliverable in days.

## Stage 0 — Documentation (this PR)

**Goal:** Land the 22-document frontend constitution. No code changes.

**Files added.** The 22-document constitution spanning `00_thesis/`, `01_architecture/`, `02_components/`, `03_standards/`, `04_quality_and_evals/`, `05_decisions/`, and this `06_roadmap/` folder.

**Files modified.** None outside docs_revamped/.

**Code changes.** None.

**Acceptance.**
- All 22 documents land in one PR.
- Every cross-reference resolves.
- The ADR ([`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md)) is the spine; thesis, architecture, module contract, infrastructure, registries, page shells, standards, quality, this roadmap all derive from it.

---

## Stage 1 — Static-registry catch-up (the hold-the-line patch)

**Why this exists.** The full module migration takes ~3 months. We will not leave users hitting the orange "Could not decode workspace context" card on 20 shipped backend primitives for that long. Stage 1 is a 2-hour patch that surfaces every backend-shipped primitive honestly through the EXISTING page-folder UI code. No module folders yet.

**Files modified (target — concrete file list is verified at PR time).**
- `src/lib/toolNames.ts` — add 18 entries to `KNOWN_BACKEND_TOOLS` + `RUNNABLE_PRIMITIVE_TOOLS`. Add `get_otr_history_tool` and `calculate_wirp_meeting_pricing_tool` to a new `WORKFLOW_INCOMPATIBLE_TOOLS` set + `UNSUPPORTED_KNOWN_REASONS` entries.
- `src/components/build/primitive/contextDecoder.ts` — add `kind: 'workflow_incompatible'` decoded variant + routing path that surfaces the honest paused/typed-detail card instead of the decode-error card.
- 3 failing test files — update snapshot counts (`routingCoverage.test.ts`, `genericBuilder.test.ts`, `buildHandoffContract.test.ts`).
- `src/types/library.ts` — extend `SUB_AGENT_LABELS` with `inflation_indexed_bonds`, `inflation_swaps`, `bond_futures`, `policy_futures`. Audit `CATEGORY_LABELS` against the live manifest categories.
- `src/components/build/model/controls/CurveAndTenor.ts` — broaden curve-family option sets to include inflation linker, ZCIS, bond_futures, policy_futures families (exposes backend PR5 broadening).
- `src/components/build/primitive/paramSpecs.ts` — same broadening.

**The 20 tools surfaced by Stage 1:**

*Backend-runnable via `_PRIMITIVE_SPECS` (18):*

1. `calculate_otr_ofr_spread_tool`
2. `get_real_yield_level_tool`
3. `calculate_breakeven_inflation_simple_tool`
4. `calculate_forward_breakeven_simple_tool`
5. `calculate_breakeven_curve_spread_tool`
6. `calculate_cross_country_breakeven_spread_simple_tool`
7. `calculate_real_yield_curve_spread_tool`
8. `calculate_cross_country_real_yield_spread_simple_tool`
9. `calculate_real_yield_butterfly_tool`
10. `calculate_breakeven_butterfly_tool`
11. `calculate_inflation_swap_rate_level_tool`
12. `calculate_inflation_swap_curve_spread_tool`
13. `calculate_inflation_swap_forward_tool`
14. `calculate_cross_market_inflation_swap_spread_tool`
15. `calculate_swap_breakeven_basis_simple_tool`
16. `calculate_inflation_swap_butterfly_tool`
17. `calculate_cpi_surprise_tool`
18. `calculate_nfp_surprise_tool`

*Workflow-incompatible (2):*

19. `get_otr_history_tool` — SCD2 transition log; surfaces as honest paused card via `workflow_incompatible` routing.
20. `calculate_wirp_meeting_pricing_tool` — per-meeting WIRP snapshots; same.

`classify_curve_move_tool` is also workflow-incompatible but already has a typed-view route today (`regime`); no Stage 1 change.

**Acceptance.**
- All 20 tools surface honestly when opened from Library (generic builder for the 18 runnable; unsupported card for the 2 workflow-incompatible). Zero decode-error card hits for shipped primitives.
- All 3 previously-failing tests pass.
- `SUB_AGENT_LABELS` covers every sub-agent emitted by `/api/v1/library/manifest` (no raw snake_case in the InstrumentStrip).
- `CurveAndTenor` dropdown contains inflation, ZCIS, bond_futures, policy_futures families (PR5 visible from the UI).

**What this stage does NOT do.** Create `src/modules/`. Move any existing code. Introduce module-folder infrastructure. That's Stage 2.

**Effort.** Small (~2h coding + manual smoke test).

---

## Stage 2 — Build the `src/modules/` infrastructure

**Why this exists.** Before any module folder can exist, the supporting infrastructure has to land: the loader barrel, the type system, the test helpers, the ESLint boundary rule that prevents page-shells from importing module surfaces. Stage 2 lands that skeleton WITHOUT migrating any primitive yet. Pure new-infrastructure PR; everything still works exactly as it did pre-Stage-2.

**Files added.**
- `src/modules/index.ts` — central loader barrel (initially empty arrays; populated in Stage 3).
- `src/modules/types.ts` — `PrimitiveModuleSpec`, `WorkflowModuleSpec`, `SurfaceTier` closed-family literal, `BuildSurfaceProps`, `MonitorWidgetProps`, `AskCardProps`, `UnsupportedKnownReason`, helper types.
- `src/modules/__test-utils.ts` — `assertStandardModuleInvariants(MODULE, opts)` helper covering FM11 invariants 1–8.
- `src/lib/__tests__/loader_presence.spec.ts` — asserts every module folder under `src/modules/{primitives,workflows}/` is imported in `src/modules/index.ts` (invariant 9). Initially asserts the empty case.
- `tools/check_module_parity.py` — cross-side parity script. Reads backend `_PRIMITIVE_SPECS` + `WORKFLOW_INCOMPATIBLE_TOOLS` keys; reads frontend `ALL_PRIMITIVE_MODULES` tool names; asserts set equality (modulo `deferred` reservations).
- `Makefile` target `check-module-parity`.
- ESLint custom rule `boundaries/no-module-from-shells` — blocks `from '@/modules/...'` imports inside `src/components/{build,library,monitor,ask,layout}/**`. (FP12 enforcement.)
- `package.json` scripts — `test:modules`, `test:registries`, optional `test` aggregate. New test commands wired into CI.

**Acceptance.**
- `npm run test:modules` exits clean (no modules to test yet; meta-tests pass).
- `npm run test:registries` exits clean (registries are still hand-authored; loader_presence asserts the empty case).
- `make check-module-parity` exits clean (no modules yet, so the parity rule is trivially satisfied via the temporary whitelist for the Stage 1 hand-authored set).
- ESLint rule active; page-shell files do not yet import from `@/modules/`.
- No user-visible change.

**What this stage does NOT do.** Create any module folder. Migrate any code. Change registry behaviour. The hand-authored Stage 1 entries remain in `toolNames.ts` exactly as they were.

**Effort.** Small-medium (~half-day).

---

## Stage 3 — Scaffold module folders for ALL primitives (your step "a")

**Why this exists.** Before refactor work can begin, every primitive that will eventually have a module needs its folder to exist. Stage 3 creates ~50 module-folder skeletons in one go — every existing primitive AND every backend-shipped primitive missing from the UI today.

**Crucially:** the `module.ts` for existing primitives **points its `surfaces.*` references at the LEGACY page-folder file paths** (e.g. PCA's `surfaces.preview` initially imports from `@/components/build/widgets/PcaPreviewWidget`). The code hasn't moved; only the spec exists. The central registries can now BEGIN to derive from `ALL_PRIMITIVE_MODULES` — but the hand-authored Stage 1 entries remain for primitives whose code is still in the legacy location.

**Files added per module folder (~50 folders):**
- `THESIS.md` — skeleton answering the five required questions; for existing primitives, documents the as-built surface set; for new-feature primitives, marked "Stage 5+ — not yet built; surfaces are aspirational".
- `module.ts` — declares `toolName`, `tiers` (matching the as-built shape for existing primitives; minimal `generic_runnable` for new-feature ones), `displayName`, `category`, `oneLineSummary`. For existing primitives, `surfaces` references the legacy file paths. For new-feature primitives, `surfaces` is empty.
- `__tests__/module.spec.ts` — standard round-trip via `assertStandardModuleInvariants`.

**Files modified.**
- `src/modules/index.ts` — populated with all ~50 imports + `ALL_PRIMITIVE_MODULES` array. Alphabetically sorted by tool name.
- `src/lib/toolNames.ts` — start deriving the registries from `ALL_PRIMITIVE_MODULES`, with the Stage 1 hand-authored entries retained for primitives whose code is still in legacy locations. The derivation produces UNION SETS; duplicates are explicitly de-duped.
- `src/lib/modelRegistry.ts` — same hybrid pattern.

**Acceptance.**
- All ~50 module folders exist with the required-three-files shape (THESIS, module.ts, module.spec.ts).
- `npm run test:modules` exits clean — every module passes its round-trip.
- `make check-module-parity` exits clean — backend tool set equals frontend module set (modulo the Stage 1 transitional entries, tracked via the parity-check whitelist).
- No user-visible change. Existing UI continues to work via the legacy file paths.

**What this stage does NOT do.** Move any existing surface code. Add any new surface JSX. Remove the Stage 1 hand-authored registry entries.

**Effort.** Medium (~1 day, mechanical).

---

## Stage 4a — Refactor sovereign + OIS primitives into module folders

**Why this exists.** Refactor work, in 3 PRs grouped by sub-agent. Stage 4a covers the largest grouping: all sovereign_bonds primitives (curve_spread, butterfly, yield_levels, scanner, classify_curve_move, cross_market_spread, build_sovereign_yield_panel, zscore_custom, breakeven_inflation) plus all OIS primitives (curve_spread, cross_market, forward_rate, rate_level, swap_spread, financing_rate, ois_butterfly). ~17 primitives.

**Per primitive, the refactor work is identical:**
1. Move each existing surface file from its legacy location (e.g. `src/components/build/widgets/...`, `src/components/monitor/widgets/...`, typed view files in `src/components/build/primitive/...`) into `src/modules/primitives/<tool_name>/surfaces/<Name>.tsx`. Rename to the canonical surface-file name per FM8.
2. Update the module.ts `surfaces.*` references from the legacy path to the new in-folder path.
3. Remove the primitive's hand-authored registry entries (Stage 1 leftovers) — the module spec now drives inclusion.
4. Delete the legacy file location.
5. Update imports in page-shells that previously imported the legacy file — page-shells should now consume via the central registry derivation (`getPrimitiveModule(toolName).surfaces.preview`), not via direct import.

**Files modified (representative — exact list per the PR).**
- 17 module folders' `surfaces/` populated.
- ~17 legacy files removed from `src/components/build/widgets/`, `src/components/monitor/widgets/`, `src/components/build/primitive/`.
- `src/lib/toolNames.ts` — Stage 1 hand-authored entries for these 17 primitives DELETED.
- `src/lib/modelRegistry.ts` — same.
- Various page-shell files — imports updated to consume via central derivation.

**Acceptance.**
- Every sovereign + OIS primitive renders identically to pre-Stage-4a state. Pixel-equivalence verified by manual smoke test.
- `npm run test:build` passes (Surface-contract tests in their new locations still pass).
- `make check-module-parity` passes.
- ESLint boundary check passes (no page-shell imports any module surface directly).
- No hand-authored Stage 1 entries remain for any of the 17 migrated primitives.

**What this stage does NOT do.** Add new behaviour. Change visible UI. Migrate rich-model or futures primitives (those are 4b and 4c).

**Effort.** Medium (~1 day).

---

## Stage 4b — Refactor rich-model primitives into module folders

**Why this exists.** Same shape as 4a, but for the 5 rich-model primitives that share the `BuilderCanvas` + `OutputCanvas` + per-tool preview-widget pattern: `calculate_pca_yield_curve_tool`, `calculate_rolling_regression_tool`, `calculate_yield_change_attribution_pca_tool`, `calculate_half_life_tool`, `calculate_beta_adjusted_spread_tool`. Bundling them in one PR keeps the rich-model migration coherent — they all need the same `BuilderCanvas` + `RichModelWidget` integration patterns.

**Per primitive:**
1. Move `surfaces/BuildSurface.tsx` (typically a 5-line wrapper around `<BuilderCanvas toolName={...} />`).
2. Move `surfaces/PreviewWidget.tsx` (typically a 3-line wrapper around `<RichModelWidget toolName={...} />`).
3. Update module.ts to reference in-folder paths.
4. Delete legacy files in `src/components/build/widgets/{Pca,RollingRegression,Attribution,HalfLife,BetaAdjustedSpread}PreviewWidget.tsx`.
5. Delete hand-authored entries in `modelRegistry.ts` — modules now drive `MODELS` derivation.

**Acceptance.**
- Every rich-model primitive renders identically (Build canvas + persisted-artifact preview cards).
- `BuilderCanvas` continues to work — it now reads from `getPrimitiveModule(toolName).richModel` instead of `hasModelMetadata(toolName)`.
- `modelRegistry.ts`'s `MODELS` array is derived from `ALL_PRIMITIVE_MODULES` for all 5 migrated primitives.

**Effort.** Medium (~1 day).

---

## Stage 4c — Refactor futures primitives into module folders

**Why this exists.** Same shape as 4a/4b, for the 12 futures primitives: 3 bond_futures monitors (`futures_price_level`, `futures_volume_oi`, `scan_bond_futures_extremes`) + 8 policy_futures primitives (`futures_price_level`, `futures_strip_snapshot`, `volume_open_interest_snapshot`, `futures_calendar_spread`, `futures_butterfly_simple`, `futures_cross_market_spread`, `futures_pack_average_simple`, `scan_policy_futures_extremes`) + 1 policy futures panel + 1 ois_butterfly. Bundled in one PR because most of these primitives are NEW additions from PR #206 with no bespoke per-tool surfaces; they all go through the generic builder.

**Per primitive:**
1. Most ship via `generic_runnable` only — no surfaces to move, just delete the Stage 1 hand-authored registry entries.
2. The few with typed views or preview widgets (rare for this set) follow the 4a/4b pattern.
3. Update module.ts to claim `generic_runnable` (or appropriate combinations).
4. Delete the primitive's hand-authored registry entries.

**Acceptance.**
- All 12 futures primitives surfaced via their module specs (generic builder).
- No hand-authored entries for any sovereign / OIS / rich-model / futures primitive remain in `toolNames.ts` or `modelRegistry.ts`.
- The "no hand-authored registries" rule (FP4) can be tightened — only the 18 net-new primitives in Stage 5+ remain temporarily hand-authored, and the whitelist shrinks accordingly.

**Effort.** Medium (~1 day).

---

## Stage 4 self-check (after 4a + 4b + 4c)

After Stages 4a, 4b, 4c:
- Every existing UI primitive lives in its module folder.
- Page shells contain ZERO per-primitive code.
- Central registries are FULLY derived for migrated primitives.
- The Stage 1 hand-authored entries for the 18 net-new primitives remain — these are the targets of Stages 5+.

The "refactor work" half of the migration is complete. From Stage 5 onward, all work is new-feature.

---

## Stage 5 — Pilot new-feature primitive: `calculate_cpi_surprise_tool` (your step "c", first one)

**Why this exists.** First true new-feature PR. CPI surprise is the canonical event-signal primitive. Its module already exists as a Stage 3 skeleton; Stage 5 fills in the actual surfaces:
- `surfaces/MonitorWidget.tsx` — bento card showing the latest CPI release inline with the rolling surprise / z-score band.
- `surfaces/AskCard.tsx` — chat-result card with release-date / actual / consensus / surprise / z-score inline.
- Generic Build surface (no custom build surface needed — generic builder + AutoRenderer is sufficient).

The THESIS that was a Stage 3 skeleton is now filled in fully — the five questions answered for real.

This pilot validates that the module architecture works end-to-end for genuinely new UI work, not just relocated UI.

**Files added.**
- `src/modules/primitives/calculate_cpi_surprise_tool/surfaces/MonitorWidget.tsx`
- `src/modules/primitives/calculate_cpi_surprise_tool/surfaces/AskCard.tsx`
- `src/modules/primitives/calculate_cpi_surprise_tool/THESIS.md` — fully populated.

**Files modified.**
- `src/modules/primitives/calculate_cpi_surprise_tool/module.ts` — add `'monitor_surface'` + `'ask_surface'` to `tiers`; populate `surfaces.monitor` and `surfaces.ask`; populate `monitorMeta` (defaultSize, allowedSizes, etc.).
- `src/lib/toolNames.ts` — REMOVE `calculate_cpi_surprise_tool` from the Stage 1 hand-authored entries; the module is now driving its registration entirely.
- `src/components/monitor/registry.ts` — start consuming `MODULE.monitorMeta` from modules-that-claim-`monitor_surface`.
- `src/components/ask/messages/ConversationCanvas.tsx` — start consuming `MODULE.surfaces.ask` from modules-that-claim-`ask_surface`; CPI surprise routes to its bespoke card, everything else falls through to `AssistantResearchCard`.

**Acceptance.**
- Opening CPI Surprise from Library → generic builder works (it claims `generic_runnable`).
- The Monitor catalog modal shows the CPI Surprise widget; user can add it and the live data renders.
- Asking "what's the latest CPI surprise for the US" in chat → the bespoke `AskCard.tsx` renders, NOT the generic `AssistantResearchCard`.
- Parity check passes.
- THESIS.md fully populated and reviewer-signed-off.

**Effort.** Medium.

---

## Stages 6+ — Remaining new-feature primitives (one or two per PR)

**Why this exists.** The other 17 backend-shipped primitives that need new UI work. Each gets a small PR (1–2 primitives per PR depending on size) following the Stage 5 pattern: fill in the surfaces, populate THESIS, remove the Stage 1 hand-authored registry entry.

**Suggested grouping (one row = one PR):**

| PR | Primitives | Notes |
|---|---|---|
| 6 | `calculate_nfp_surprise_tool` + `calculate_otr_ofr_spread_tool` | Two small primitives; NFP mirrors CPI, OTR/OFR is a basic cash-bond spread |
| 7 | `get_real_yield_level_tool` | Linker analog of yield snapshot; may add a Monitor widget |
| 8 | `calculate_breakeven_inflation_simple_tool` + `calculate_forward_breakeven_simple_tool` | Linker breakeven primitives |
| 9 | `calculate_breakeven_curve_spread_tool` + `calculate_breakeven_butterfly_tool` | Linker breakeven structures |
| 10 | `calculate_real_yield_curve_spread_tool` + `calculate_real_yield_butterfly_tool` | Linker real-yield structures |
| 11 | `calculate_cross_country_breakeven_spread_simple_tool` + `calculate_cross_country_real_yield_spread_simple_tool` | Cross-country linker |
| 12 | `calculate_inflation_swap_rate_level_tool` + `calculate_inflation_swap_curve_spread_tool` | ZCIS primitives |
| 13 | `calculate_inflation_swap_forward_tool` + `calculate_inflation_swap_butterfly_tool` | ZCIS primitives |
| 14 | `calculate_cross_market_inflation_swap_spread_tool` + `calculate_swap_breakeven_basis_simple_tool` | ZCIS primitives |
| 15 | `get_otr_history_tool` | Workflow-incompatible; needs a bespoke typed canvas calling a typed-detail endpoint |
| 16 | `calculate_wirp_meeting_pricing_tool` | Workflow-incompatible; same as above |

These groupings are recommendations. Reordering is fine if a higher-priority primitive surfaces.

After Stage 16, every backend-shipped primitive has its real UI representation. The 18-tool gap is closed end-to-end.

**Effort per PR.** Small-medium.

---

## Stage N — Cleanup

**Why this exists.** Architecture polish after the substantive work is done.

**Files modified.**
- Move the 7 typed primitive views from `src/components/build/primitive/{Spread,CrossMarket,Butterfly,Yield,Scanner,Regime,Forward}PrimitiveView.tsx` into `src/components/shared/render/typed/`. Update imports.
- Move `src/components/build/widgets/index.ts` shared widget shells (AutoRenderer, RichModelWidget) into `src/components/shared/render/`. Update imports.
- Delete the parity-check whitelist (now empty).
- Flip the strict "no hand-authored registry entries" CI gate from advisory to blocking.

**Acceptance.**
- File layout matches [`../03_standards/frontend_file_layout.md`](../03_standards/frontend_file_layout.md) exactly.
- No legacy file locations remain.
- The strict registries-derivation gate blocks any hand-authored registry entry in CI.

**Effort.** Small.

---

## Per-PR acceptance gate (applies to every Stage 3+ PR)

Before merging, the PR description confirms:

1. ☐ Which stage this PR belongs to (3 / 4a / 4b / 4c / 5 / 6+ / N).
2. ☐ The kind of work (refactor vs new-feature vs cleanup).
3. ☐ For refactor PRs: NO new behaviour added. Diff is move-and-rename only.
4. ☐ For new-feature PRs: THESIS populated for every new primitive; every claimed tier has a working surface.
5. ☐ `npm run test:modules`, `npm run test:registries`, `npm run test:build`, `make check-module-parity` — all green.
6. ☐ Manual smoke test screenshots of every surface touched.
7. ☐ Hand-authored registry entries for migrated primitives REMOVED.
8. ☐ Legacy file locations DELETED.
9. ☐ No regression in any other module's tests.

## Migration mistakes to avoid

- **Mixing refactor and new-feature work in one PR.** The hard rule. Refactor PRs (Stages 4a/4b/4c) MUST NOT add new tier claims or new surface JSX. New-feature PRs (Stages 5+) MUST NOT touch primitives outside their explicit scope.
- **Migrating a primitive without removing its legacy entries.** The hand-authored registry entry must come out in the same PR that adds the module's full surfaces. Otherwise the registry has duplicate entries and the next PR finds inconsistent state.
- **Migrating a primitive that the backend hasn't shipped.** Verify backend's `_PRIMITIVE_SPECS` / `WORKFLOW_INCOMPATIBLE_TOOLS` before populating the module's surfaces. A module for a non-existent backend tool fails the parity check.
- **Skipping the THESIS.** "I'll write it later" is the failure mode FM10 is designed to prevent. THESIS is REQUIRED at module-scaffold time (Stage 3) and FULLY populated when surfaces land (Stage 5+).
- **Adding a Monitor widget for a primitive that didn't have one, in a refactor PR.** That's net-new behaviour; it belongs in a Stage 5+ new-feature PR for that primitive, not in the refactor.
- **Stage 4 ordering.** 4a/4b/4c are independent in principle but share the `src/modules/` infrastructure. Land them in order to keep PR diffs small and reviewable; don't parallelise.

## Stage 0 self-audit

This roadmap is part of Stage 0. Self-check:

- ☐ Every doc in the omnibus PR cross-links correctly.
- ☐ No doc references a forthcoming doc that wasn't written.
- ☐ Every principle (FP, FM, SI) is defined exactly once and cited where used.
- ☐ Every closed family (SurfaceTier, DagNodeKind, etc.) is enumerated identically across all docs.
- ☐ Every backend doctrine cited (P-numbers, PR-numbers, ADRs) exists and is correctly numbered.

The cross-link audit is the final task before opening the PR.

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-25 | Restructured staging to cleanly separate refactor work (Stage 4a/4b/4c — sub-agent grouped) from new-feature work (Stage 5+ — pilot then one-or-two-per-PR). Inserted Stage 2 (build `src/modules/` infrastructure) and Stage 3 (scaffold ALL module folders with skeleton specs pointing at legacy file paths) so refactor PRs have somewhere to move code INTO. Total PR count reduced from ~50 to ~17 over ~3 months. |
| v1 | 2026-05-25 | Initial migration roadmap. Stages 0–N defined; per-stage scope, acceptance gates, mistakes to avoid. |
