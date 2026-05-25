# Frontend Migration Roadmap

> The plan for migrating the existing surface-organised frontend codebase to the module-organised architecture defined in [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md). Sequenced as stages; one merge per stage.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** roadmap. Each stage updates this doc with progress; stages bump version (v1.1, v1.2, …) as they land.
**Operationalises principles:** [FP1–FP13](../00_thesis/03_frontend_thesis.md), [FM1–FM12](../02_components/frontend_module/README.md).
**See also:** [`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md), [`../02_components/frontend_module/runbook.md`](../02_components/frontend_module/runbook.md).

---

## Stages at a glance

| Stage | What lands | Status | Effort |
|---|---|---|---|
| **0** | The constitution (this doc + 21 supporting docs across docs_revamped/) | **In flight (current PR)** | 1 omnibus PR |
| **1** | Static-registry catch-up + test repair (close the 18-tool gap + 2 workflow-incompatible; fix 3 failing tests) | Not started | 1 PR, small |
| **2** | First module: `calculate_cpi_surprise_tool` as reference implementation | Not started | 1 PR, medium |
| **3** | Migrate the 17 other backend-runnable primitives (one module per PR) | Not started | 17 PRs, small each |
| **4** | Migrate the 2 workflow-incompatible tools (`get_otr_history_tool`, `calculate_wirp_meeting_pricing_tool`) into modules | Not started | 2 PRs |
| **5** | Migrate existing rich-model primitives (PCA, regression, attribution, half-life, beta-adjusted spread) | Not started | 5 PRs |
| **6** | Migrate remaining backend-shipped primitives (sovereign / OIS / futures / etc., not in #2–#5) | Not started | ~20 PRs |
| **7** | Migrate workflow templates (`event_study`, `regime_conditioned_relationship`) | Not started | 2 PRs |
| **8** | Migrate the 7 typed primitive views into shared infrastructure layout under `src/components/shared/render/typed/` | Not started | 1 PR |
| **9** | Delete hand-authored registry entries; flip `no_hand_authored_registries.spec.ts` to fully enforce | Not started | 1 PR |
| **N** | Architecture cleanup; legacy folders removed | Not started | N PRs |

Total estimated PRs: ~50. Total estimated time: 3–4 months at one stage every 2–3 days.

## Stage 0 — Documentation (this PR)

**Goal:** Land the 22-document frontend constitution.

**Files added.** Per the omnibus PR. The 22-document constitution lands in one PR spanning `00_thesis/`, `01_architecture/`, `02_components/`, `03_standards/`, `04_quality_and_evals/`, `05_decisions/`, and this `06_roadmap/` folder.

**Files modified.** None outside docs_revamped/.

**Code changes.** None.

**Acceptance.**
- All 22 documents land in one PR.
- Every cross-reference resolves.
- The ADR ([`../05_decisions/0014-frontend-module-architecture.md`](../05_decisions/0014-frontend-module-architecture.md)) is the spine; thesis, architecture, module contract, infrastructure, registries, page shells, standards, quality, this roadmap all derive from it.

## Stage 1 — Static-registry catch-up + test repair

**Goal:** Close the immediate coverage gap. The 18 backend-runnable primitives and 2 workflow-incompatible primitives missing from frontend registries get hand-authored entries (temporary; replaced in subsequent stages). Failing tests are repaired.

**Files modified.**
- `src/lib/toolNames.ts` — add 18 entries to `KNOWN_BACKEND_TOOLS` + `RUNNABLE_PRIMITIVE_TOOLS`. Add 2 entries to `KNOWN_BACKEND_TOOLS` + new `WORKFLOW_INCOMPATIBLE_TOOLS` set + `UNSUPPORTED_KNOWN_REASONS`.
- `src/components/build/primitive/contextDecoder.ts` — add `kind: 'workflow_incompatible'` decoded variant + routing path.
- `src/components/build/primitive/__tests__/routingCoverage.test.ts` — update snapshot count.
- `src/components/build/primitive/__tests__/genericBuilder.test.ts` — update snapshot count.
- `src/components/build/primitive/__tests__/buildHandoffContract.test.ts` — update snapshot count.
- `src/types/library.ts` — extend `SUB_AGENT_LABELS` with `inflation_indexed_bonds`, `inflation_swaps`, `bond_futures`, `policy_futures`. Audit `CATEGORY_LABELS` against the live manifest categories.
- `src/components/build/model/controls/CurveAndTenor.ts` — extend `SOVEREIGN_CURVES` / add `INFLATION_CURVES` / add `ZCIS_CURVES` / add `BOND_FUTURES_CONTRACTS` / add `POLICY_FUTURES_CURVES` to expose PR5's curve-family broadening.
- `src/components/build/primitive/paramSpecs.ts` — same broadening.

**Tools missing from registry today (the 18 + 2):**

Backend-runnable (18):
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

Workflow-incompatible (2 new — `classify_curve_move_tool` is already in KNOWN_BACKEND_TOOLS via the typed-view path):
19. `get_otr_history_tool`
20. `calculate_wirp_meeting_pricing_tool`

**Acceptance.**
- All 20 tools surface honestly when opened from Library (generic builder for the 18, unsupported card for the 2).
- All 3 previously-failing tests pass.
- `SUB_AGENT_LABELS` covers every sub-agent emitted by `/api/v1/library/manifest`.
- `CurveAndTenor` dropdown contains inflation, ZCIS, bond_futures, policy_futures families (PR5 visible).

**Effort.** Small (~2h coding + manual smoke test).

## Stage 2 — Reference module (`calculate_cpi_surprise_tool`)

**Goal:** Implement the first module under the new architecture. Establishes the reference implementation.

**Files added.**
- `src/modules/index.ts` (new — central loader barrel).
- `src/modules/types.ts` (new — `PrimitiveModuleSpec`, `WorkflowModuleSpec`, `SurfaceTier`, `BuildSurfaceProps`, etc.).
- `src/modules/__test-utils.ts` (new — `assertStandardModuleInvariants` helper).
- `src/modules/primitives/calculate_cpi_surprise_tool/THESIS.md`
- `src/modules/primitives/calculate_cpi_surprise_tool/module.ts`
- `src/modules/primitives/calculate_cpi_surprise_tool/surfaces/MonitorWidget.tsx`
- `src/modules/primitives/calculate_cpi_surprise_tool/surfaces/AskCard.tsx`
- `src/modules/primitives/calculate_cpi_surprise_tool/__tests__/module.spec.ts`
- `src/lib/__tests__/loader_presence.spec.ts` (new — asserts the loader includes every module).
- `tools/check_module_parity.py` (new — cross-side parity check).
- `Makefile` target `check-module-parity`.

**Files modified.**
- `src/lib/toolNames.ts` — REMOVE `calculate_cpi_surprise_tool` from hand-authored sets; the module spec now drives its inclusion. Keep other hand-authored entries.
- `src/components/build/primitive/contextDecoder.ts` — start reading TOOL_TO_VIEW from a derived source (only PCI-S's tool-name entry derived; rest still hand-authored).
- `src/components/monitor/registry.ts` — start reading WIDGET_TYPES from a derived source (only CPI surprise's widget entry derived; rest still hand-authored).
- `src/components/ask/messages/ConversationCanvas.tsx` — start reading per-tool AskCard from `MODULE.surfaces.ask` (only CPI surprise routes here; rest fall through to AssistantResearchCard).

**Acceptance.**
- The `calculate_cpi_surprise_tool` module passes its round-trip test.
- Opening CPI Surprise from Library → generic builder works (it claims `generic_runnable`).
- The Monitor catalog modal shows the CPI Surprise widget (claimed `monitor_surface`).
- Asking "what's the latest CPI surprise for the US" → Ask renders the module's bespoke `AskCard.tsx` (claimed `ask_surface`).
- Parity check passes (CPI Surprise is in `_PRIMITIVE_SPECS` and in `ALL_PRIMITIVE_MODULES`).

**Effort.** Medium (~1 day of focused work — establishes infrastructure types + helpers + ESLint boundary rule + a primer for future modules).

## Stages 3 — N

Each stage migrates 1+ primitives following the [runbook](../02_components/frontend_module/runbook.md). The migration order is driven by:

1. **Backend importance + frontend gap.** Primitives that ship in `_PRIMITIVE_SPECS` but have no current frontend surface come first (closing the 18-tool gap MODULE-by-MODULE supersedes Stage 1's static fix).
2. **Cohesion grouping.** Migrate primitives within a backend sub-agent together (all inflation_swaps, then all inflation_indexed_bonds, etc.) to share migration learnings.
3. **Rich-model primitives last.** They already have working surfaces; the migration is moving code into modules without behaviour change. Lower priority than primitives that don't surface at all.

Recommended order (one stage = one PR unless noted):

| Order | Module | Notes |
|---|---|---|
| 3.1 | `calculate_nfp_surprise_tool` | Symmetric to CPI surprise; validates the event-signal pattern |
| 3.2 | `calculate_otr_ofr_spread_tool` | Simple cash-bond spread primitive |
| 3.3 | `get_real_yield_level_tool` | Real-yield level (analog of `get_yield_levels_tool`) |
| 3.4 | `calculate_breakeven_inflation_simple_tool` | Linker primitive |
| 3.5 | `calculate_forward_breakeven_simple_tool` | Linker primitive |
| 3.6 | `calculate_breakeven_curve_spread_tool` | Linker primitive |
| 3.7 | `calculate_cross_country_breakeven_spread_simple_tool` | Linker primitive |
| 3.8 | `calculate_real_yield_curve_spread_tool` | Linker primitive |
| 3.9 | `calculate_cross_country_real_yield_spread_simple_tool` | Linker primitive |
| 3.10 | `calculate_real_yield_butterfly_tool` | Linker primitive |
| 3.11 | `calculate_breakeven_butterfly_tool` | Linker primitive |
| 3.12 | `calculate_inflation_swap_rate_level_tool` | ZCIS primitive |
| 3.13 | `calculate_inflation_swap_curve_spread_tool` | ZCIS primitive |
| 3.14 | `calculate_inflation_swap_forward_tool` | ZCIS primitive |
| 3.15 | `calculate_cross_market_inflation_swap_spread_tool` | ZCIS primitive |
| 3.16 | `calculate_swap_breakeven_basis_simple_tool` | ZCIS primitive |
| 3.17 | `calculate_inflation_swap_butterfly_tool` | ZCIS primitive |
| 4.1 | `get_otr_history_tool` | Workflow-incompatible (with custom Build surface) |
| 4.2 | `calculate_wirp_meeting_pricing_tool` | Workflow-incompatible (with custom Build surface) |
| 5.1 | `calculate_pca_yield_curve_tool` | Rich-model |
| 5.2 | `calculate_rolling_regression_tool` | Rich-model |
| 5.3 | `calculate_yield_change_attribution_pca_tool` | Rich-model |
| 5.4 | `calculate_half_life_tool` | Rich-model |
| 5.5 | `calculate_beta_adjusted_spread_tool` | Rich-model |
| 6.x | Remaining sovereign / OIS / futures primitives | One per PR |
| 7.1 | `event_study` workflow | First workflow module |
| 7.2 | `regime_conditioned_relationship` workflow | Second workflow module |
| 8 | Move 7 typed primitive views into shared/render/typed/ | Code-organisation cleanup |
| 9 | Delete hand-authored registry entries; flip `no_hand_authored_registries.spec.ts` to fully enforce | Stage-N gate |
| N | Architecture cleanup (rename / consolidate legacy folders) | Final cleanup |

This list is updated as stages complete. The current order is a recommendation; reordering is fine if a higher-priority primitive (e.g. a new backend primitive shipping mid-migration) needs to jump the queue.

## Per-stage acceptance gate

Before merging any stage 3+ PR:

1. ☐ Module folder + 5 required files + N surfaces per tier claims.
2. ☐ Module's round-trip test passes.
3. ☐ THESIS answers all 5 questions; reviewer signs off.
4. ☐ Parity check passes (`tools/check_module_parity.py`).
5. ☐ Manual smoke test: open from Library, run, verify each claimed surface.
6. ☐ Hand-authored registry entry for this tool REMOVED from `src/lib/toolNames.ts`.
7. ☐ If the module has `custom_preview_widget` / `custom_build_surface` / `ask_surface`, the corresponding legacy file in `src/components/build/widgets/` / etc. is DELETED in the same PR.
8. ☐ No regression in any other module's tests.

## Migration mistakes to avoid

- **Migrating a primitive without removing its legacy entries.** The hand-authored registry entry must come out in the same PR that adds the module. Otherwise the registry has duplicate entries — one from the module, one hand-authored — and the next module that touches the same code finds inconsistent state.
- **Migrating a primitive that the backend hasn't shipped.** Verify backend's `_PRIMITIVE_SPECS` / `WORKFLOW_INCOMPATIBLE_TOOLS` before adding the module. A module for a non-existent backend tool fails the parity check.
- **Adding new behaviour during migration.** A migration PR replicates existing behaviour in the new structure. New features (e.g. adding a Monitor widget for a primitive that didn't have one) go in a SEPARATE follow-up PR.
- **Skipping the THESIS.** "I'll write it later" is the failure mode FM10 is designed to prevent.

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
| v1 | 2026-05-25 | Initial migration roadmap. Stages 0–N defined; per-stage scope, acceptance gates, mistakes to avoid. |
