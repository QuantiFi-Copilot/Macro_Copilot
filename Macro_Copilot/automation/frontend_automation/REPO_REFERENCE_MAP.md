# Repo Reference Map — Frontend Factory

These are the files the frontend automation must consult before building or reviewing a frontend module.

The authoritative project documentation lives in `docs_revamped/`. The **single most load-bearing doc** is `docs_revamped/02_components/primitive/BUILD_GUIDE.md` — the front-door manual built during the Phase-1 pilot. This factory operationalises **Stages 4 → 5 → 6** of that guide.

## How to use this map

1. **Before any build or review**, read every file under §"Core non-negotiables" and §"Frontend module contract" once per session.
2. For the specific tool at hand, also read the matching §"Reference frontend modules" entry, the backend tool folder it wraps, and the committed mockup PNGs.
3. Only after that, inspect the live repo's current implementation of neighbouring modules — the docs explain the *contract*, but the neighbours show the *current shape* of that contract.

Reading from memory or generic software-engineering habits is a P5 violation.

## Core non-negotiables — repo-wide rules

- `docs_revamped/00_thesis/00_what_we_build.md` — platform thesis. Read once per session.
- `docs_revamped/00_thesis/01_non_negotiables.md` — P1–P12 with IDs. Cite by ID (`P3`, `P5`, `P10`).
- `docs_revamped/00_thesis/02_ai_agent_development_contract.md` — AC1–AC8. This factory's behavior contract.
- `docs_revamped/00_thesis/03_frontend_thesis.md` — FP1–FP13 (frontend-specific platform thesis).
- `docs_revamped/01_architecture/00_internal_architecture.md` — internal architecture.
- `docs_revamped/01_architecture/01_external_architecture.md` — MCP transport.
- `docs_revamped/01_architecture/02_frontend_architecture.md` — frontend stack overview.

## **The master spec — BUILD_GUIDE.md (load-bearing)**

- **`docs_revamped/02_components/primitive/BUILD_GUIDE.md`** — the master front-door manual for adding a new primitive end-to-end (Stages 1–8). This factory operationalises **Stages 4 → 5 → 6** specifically:
  - **Stage 4** — Frontend module spec (module.ts + THESIS + tests + loader)
  - **Stage 5** — Frontend bridge endpoint (typed-detail route + service helper + type mirror)
  - **Stage 6** — Frontend surfaces (BuildExtended + BuildCompact + Shared helper + Monitor widget + mockups)

  Cite by stage (`Stage 4`, `Stage 6 6A`).

## Frontend module contract — what a frontend module *is*

- `docs_revamped/02_components/frontend_module/README.md` — **FM1–FM12** primitive frontend module contract. Cite by ID (`FM3`, `FM10`).
- `docs_revamped/02_components/frontend_module/runbook.md` — procedure for adding a new module (the backend-only slice of BUILD_GUIDE; BUILD_GUIDE subsumes).
- `docs_revamped/02_components/frontend_module/thesis_template.md` — the THESIS.md template the builder copies.
- `docs_revamped/02_components/frontend_module/tiers.md` — full per-tier semantics for `generic_runnable`, `custom_build_surface`, `custom_preview_widget`, `monitor_surface`, `ask_surface`, `workflow_incompatible`, `paused`, `deferred`, `manifest_typed_view`.

## Lateral standards — apply to every frontend module

- `docs_revamped/03_standards/README.md` — index of the lateral standards.
- **`docs_revamped/03_standards/rendering_density.md`** — the **dual-view mandate** (§1 contract, §2.1 extended view, §2.2 compact view, §3 dispatch rules, §5 module-spec extension, §6 compact content, §8 other surfaces, §11 reviewer checks).
- **`docs_revamped/03_standards/methodology_exposure.md`** — §5 is the **standalone bridge contract** (typed-detail endpoint per tool; no shared `typedView` reuse for new modules).
- `docs_revamped/03_standards/frontend_test_patterns.md` — `assertStandardModuleInvariants` + the round-trip pattern + the dual-view contract enforcement.
- `docs_revamped/03_standards/frontend_file_layout.md` — per-module folder shape (`module.ts` + `THESIS.md` + `surfaces/` + `__tests__/` + `mockups/`).
- `docs_revamped/03_standards/frontend_naming_conventions.md` — surface file names (BuildExtended.tsx + BuildCompact.tsx are MANDATORY; legacy BuildSurface.tsx is forbidden for new modules).
- `docs_revamped/03_standards/lifecycle_checklist_template.md` — the per-tool 8-stage checklist (Stages 4–6 are this factory's scope).
- `docs_revamped/03_standards/tool_lifecycle.md` — the 7-axis definition of "done"; axis 6 is frontend surfaces.
- `docs_revamped/03_standards/methodology_disclosure.md` — methodology disclosure conventions (the `methodology_label` thread the extended view surfaces).
- `docs_revamped/03_standards/code_review_checklist.md` — the universal PR gate. Section 1J + 1K (Dual View + Frontend module + Lifecycle) apply directly.
- `docs_revamped/03_standards/closed_family_discipline.md` — the tier set is a closed family per P8 + FM3.
- `docs_revamped/03_standards/typed_boundary_discipline.md` — frozen Pydantic at every boundary; the TS type mirror in `types/rates.ts` MUST match the Pydantic Output exactly.

## Primitive contract — context for what the frontend wraps

The frontend wraps a backend primitive. Reading the primitive contract is necessary to understand the typed payload the frontend consumes.

- `docs_revamped/02_components/primitive/README.md` — PR1–PR16 (BACKEND contract). Cite when surfacing backend invariants on the frontend.
- `docs_revamped/02_components/primitive/runbook.md` — backend procedure (scope-limited slice; BUILD_GUIDE.md is the full procedure).

## Reference frontend modules (parity targets)

Start with these — they are the canonical shape every new module must match. **Read the actual files, not just the names.**

- `UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/` — **the primary reference**. Phase-1 dual-view pilot. Spread / multi-leg shape. The most complete reference — includes dual-view + Monitor + Shared helper + Mockups + THESIS that answers all five Q's.
- `UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_real_yield_curve_spread_tool/` — Phase-1 dual-view pilot. Spread shape variant.
- `UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/` — Phase-1 dual-view pilot. Single-leg snapshot shape (use as reference for snapshot / scanner / classifier shapes).

For each new tool, the catalog entry's `reference_frontend_module` field names which of these to mirror.

## Reference cross-files (the builder edits these for every tool)

- `UI/macro-copilot-dashboard-polished/src/modules/index.ts` — the loader; add new module entry in alphabetical position.
- `UI/macro-copilot-dashboard-polished/src/modules/__test-utils.ts` — `assertStandardModuleInvariants` lives here; do NOT modify (the existing map covers the dual-view + monitor + ask + preview surface keys).
- `UI/macro-copilot-dashboard-polished/src/lib/__tests__/loaderPresence.test.ts` — the global module-presence + dual-view contract test; iterates all modules; new modules auto-validated.
- `UI/macro-copilot-dashboard-polished/src/types/rates.ts` — TS type mirror of every backend Pydantic Output.
- `UI/macro-copilot-dashboard-polished/src/services/ratesApi.ts` — `fetchDetail<X>` helpers for every typed-detail endpoint.
- `UI/macro-copilot-dashboard-polished/src/modules/types.ts` — `PrimitiveModuleSpec`, `BuildExtendedProps`, `BuildCompactProps`, `MonitorWidgetProps`, etc. shared module-type definitions.
- `UI/macro-copilot-dashboard-polished/src/lib/monitorParamOptions.ts` — shared parameter option enums used by monitor widgets.
- `UI/macro-copilot-dashboard-polished/src/components/shared/build/lib/countryCaveats.ts` — shared country-specific caveats registry.

## Backend artifacts the frontend depends on (pre-flight verifies these)

- `rates_agent/<sub_agent>/tools/<tool_slug>/__init__.py` — backend re-exports
- `rates_agent/<sub_agent>/tools/<tool_slug>/config.yaml` — methodology + exposure: blocks
- `rates_agent/<sub_agent>/tools/<tool_slug>/schemas.py` — Pydantic Input + Output (the typed contract you mirror in TS)
- `rates_agent/<sub_agent>/tools/<tool_slug>/compute.py` — `_conventions_from_config` resolver + `calculate_<tool_slug>`
- `rates_agent/<sub_agent>/mcp_server.py` — MCP wrapper with integer-sentinel pattern for exposed overrides
- `manifesto/03_tool_manifest/rates_agent/<NN>_<sub_agent>_manifest.yml` — PM-facing `one_liner` (mirror into `MODULE.oneLineSummary`)
- `api/routes/rates/detail.py` — the typed-detail endpoint mounting point
- `macro_data.tool_metadata` (DB) — `theoretical_reference`, `known_limitations`, `desk_narrative`, `output_field_units` (the methodology card surfaces these)
- `database/migrations/*_<tool_name>_curated.sql` — the curated migration that populates `tool_metadata`

## ADRs

- `docs_revamped/05_decisions/0014-frontend-module-architecture.md` — the original frontend module architecture decision. Background for FM1–FM12.
- `docs_revamped/05_decisions/0015-tool-metadata-db-table.md` — DB metadata table architecture. Background for what the methodology card surfaces.

## Automation-loop policies (this folder)

These are the operational rules the orchestrator and the builder / reviewer workers obey:

- `automation/frontend_automation/DESIGN_PRINCIPLES.md` — pointer index into docs_revamped/ + this factory's operational principles.
- `automation/frontend_automation/STANDARD_MODULE_RULES.md` — the 8-artifact frontend module shape contract.
- `automation/frontend_automation/FRONTEND_BUILD_RULES.md` — what the builder must produce + the 15-step build procedure.
- `automation/frontend_automation/TESTING_POLICY.md` — the pre-commit gate (npm run test:modules + test:build + typecheck).
- `automation/frontend_automation/PRE_FLIGHT_BACKEND_AUDIT.md` — the 4-check pre-flight gate before builder dispatch.
- `automation/frontend_automation/NO_GO_RULES.md` — hard "do not build" conditions.
- `automation/frontend_automation/DONE_DEFINITION.md` — exit criteria for the tool to be marked done.
- `automation/frontend_automation/SINGLE_REVIEW_ROUND_POLICY.md` — the 3-dispatch cap + the build → review → fix → commit flow.
- `automation/frontend_automation/QUOTA_AND_RESUME_POLICY.md` — quota contract; single-engine (no fallback).
- `automation/frontend_automation/BACKGROUND_EXECUTION_POLICY.md` — recurring wake model + catalog-exhaustion shutdown.
- `automation/frontend_automation/DISCORD_STATUS_POLICY.md` — required durable state for Discord-readable progress.
- `automation/frontend_automation/OPENCLAW_CRON_RUNBOOK.md` — recommended cron job shape.

## Test infra surfaces

- `UI/macro-copilot-dashboard-polished/package.json` — defines `test:modules` + `test:build` + `typecheck` scripts.
- `UI/macro-copilot-dashboard-polished/vitest.config.ts` — Vitest config for `test:modules`.
- `UI/macro-copilot-dashboard-polished/tsconfig.json` — TypeScript config for `typecheck`.

## Important reminder

The automation must inspect the live repo state before building. These reference files are the starting point, not a replacement for reading the actual current implementation relevant to the tool at hand.

The mockup PNGs are READ AS IMAGES (Claude Opus vision) and are the design source-of-truth. Do not skim them; do not approximate from memory; do not redesign them.
