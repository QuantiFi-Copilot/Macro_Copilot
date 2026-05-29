# Design Principles — Frontend Factory

These are the non-negotiable design principles for the frontend automation factory.

They are drawn from:

- the repo's authoritative documentation tree at `docs_revamped/` (especially `02_components/primitive/BUILD_GUIDE.md`, `02_components/frontend_module/README.md` for FM1–FM12, and `03_standards/rendering_density.md` + `03_standards/methodology_exposure.md` §5)
- the Phase-1 dual-view pilot tools (`calculate_breakeven_inflation_simple_tool`, `calculate_real_yield_curve_spread_tool`, `get_real_yield_level_tool`)
- the explicit project rules the user established for this factory (Claude-only; single review round; mockup-first)

The builder MUST follow them. The reviewer MUST review against them. The orchestrator MUST treat violations as real failures.

## 0. Authoritative sources

This file is a *condensed operational restatement* of rules that are fully documented elsewhere. When this file and the source disagree, the source wins — open a PR to fix this file.

- **`docs_revamped/02_components/primitive/BUILD_GUIDE.md`** — the master front-door manual. Stages 4 → 5 → 6 are this factory's scope. Cite by stage (`Stage 4`, `Stage 6 6A`).
- **FM1–FM12** — frontend module contract. See `docs_revamped/02_components/frontend_module/README.md`. Cite by ID (`FM3`, `FM10`).
- **`docs_revamped/03_standards/rendering_density.md`** — dual-view mandate. Cite by section (`§1`, `§2.2`, `§5`).
- **`docs_revamped/03_standards/methodology_exposure.md` §5** — standalone bridge contract. Cite as `methodology_exposure.md §5`.
- **`docs_revamped/03_standards/frontend_test_patterns.md`** — frontend test discipline.
- **P1–P12** — repo-wide non-negotiables (especially P3 consistency, P5 honest disclosure, P10 single source of truth). See `docs_revamped/00_thesis/01_non_negotiables.md`.
- **PR1–PR16** — primitive contract (the BACKEND contract this factory's frontend wraps). See `docs_revamped/02_components/primitive/README.md`.
- **The map.** `automation/frontend_automation/REPO_REFERENCE_MAP.md` indexes the above.

The rest of this file is the operational restatement.

## 1. The dual-view mandate is structural, not optional  *(rendering_density.md §1, FM3, FM4 override)*

Every new frontend module under this factory MUST ship BOTH `surfaces/BuildExtended.tsx` AND `surfaces/BuildCompact.tsx`. Per `rendering_density.md §1.2`, this OVERRIDES FM4 tier-set parsimony — the compact view is a structural obligation, NOT a capability the module may opt into.

The justification: multi-tool prompts are the baseline reality, not an edge case. A PM asking *"compare US 10Y breakeven vs UK 10Y breakeven vs French 10Y breakeven"* triggers 3 tool calls; the frontend renders them as a grid of compact cards. A module that ships only the extended view falls back to a generic artifact-type card in multi-tool DAGs — an inconsistent user experience that breaks the Phase-1 pilot's standardisation.

## 2. The standalone bridge contract  *(methodology_exposure.md §5, FM9)*

`MODULE.typedView` MUST be `null` (or omitted) for every new module. The frontend consumes the per-tool typed-detail endpoint at `/api/v1/rates/detail/<tool_kind>` — NOT the shared `/api/v1/tools/{name}/run`.

Per `methodology_exposure.md §5`, the five-element bridge is:

| # | Element | Path |
|---|---|---|
| 1 | Backend tool folder | `rates_agent/<sub_agent>/tools/<tool_slug>/` (already exists; pre-flight verifies) |
| 2 | MCP wrapper | `rates_agent/<sub_agent>/mcp_server.py` (already exists; pre-flight verifies) |
| 3 | Typed-detail HTTP endpoint | `api/routes/rates/detail.py` route at `/api/v1/rates/detail/<tool_kind>` (builder ships if not already present) |
| 4 | Frontend module folder | `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/` (builder ships) |
| 5 | Frontend surfaces (Build dual-view + Monitor + optional Ask) | `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/` (builder ships) |

Reusing `/api/v1/tools/{name}/run` from a NEW standalone module's typed Build canvas is forbidden.

## 3. The mockups are the design source-of-truth  *(BUILD_GUIDE.md §Stage 6 6D, P5)*

The committed `mockups/Compact.png` + `mockups/Extended.png` are the visual contract.

- The builder reads them as IMAGES (Claude Opus vision) and assembles TSX that visually matches.
- The reviewer scores conformance to the same mockups.
- Neither the builder NOR the reviewer may redesign the mockup. If the mockup contradicts a documented rule (very rare; the mockups are drawn against BUILD_GUIDE), the builder refuses the build and the reviewer marks `STRUCTURAL:` — the human decides.

## 4. `methodology_label` MUST be threaded from the backend  *(PR10, P5)*

The backend Output carries a `methodology_label` field sourced from `config.yaml:methodology.what_it_does`. The extended view's methodology card MUST surface this field from the typed-detail response, NEVER as a hardcoded TSX literal.

The whole point is that a YAML edit to the disclosure flows through to runtime. Hardcoding the string in TSX breaks that contract silently.

## 5. The shared cross-surface helper prevents drift  *(BUILD_GUIDE.md §Stage 6 6B, P10)*

Each module ships a `surfaces/<tool_slug>Shared.ts` file co-locating:
- The data hook (e.g. `use<X>Data`) that calls `fetchDetail<X>` and normalises the payload
- Option enumerations (e.g. `PAIR_OPTIONS`, `TENOR_OPTIONS`)
- KPI / descriptor builders
- Caveat strings + tone-cue lookups

The extended view, the compact view, and the monitor widget ALL import from this shared helper. Duplicating logic across the three surfaces is a P10 violation.

## 6. FM1 identity is mechanical  *(FM1)*

The folder name under `src/modules/primitives/` MUST equal the backend MCP `tool.name` EXACTLY. No friendly aliases (no `pca/` for `calculate_pca_yield_curve_tool/`). The per-module round-trip test asserts this.

## 7. FM7 pure-spec assembly  *(FM7)*

`module.ts` exports a value, no side effects. No `register*()` calls. No global mutations. The central loader at `src/modules/index.ts` performs all registrations as a single explicit phase.

## 8. THESIS discipline is enforced  *(FM10)*

Every module ships `THESIS.md` answering the five questions from `frontend_module/thesis_template.md`. The dual-view mandate makes specific demands:

- Q1 — must enumerate BOTH `buildExtended` and `buildCompact` explicitly (not "the Build surface").
- Q2 — one paragraph per surface naming the specific decisions a PM makes from EACH view (compact: which 3 KPIs and why; extended: which sections in what order).
- Q3 — must justify the compact view's curated headline metrics (why these 3 and not the alternatives).
- Q5 — must cite by ID (FM-numbers, rendering_density.md sections, methodology_exposure.md §5, BUILD_GUIDE.md stages).

A shallow THESIS (generic answers, no concrete metrics named, no ID citations) is a `CHANGES REQUIRED` finding.

## 9. Single review round, single engine  *(SINGLE_REVIEW_ROUND_POLICY.md)*

This factory uses ONE round (Dispatch 1 builder → Dispatch 2 reviewer → optional Dispatch 3 fix → commit). NO re-review.

Both dispatches use Claude (no Codex). Independence is preserved by:
- `claude --print` is naturally fresh-context per invocation
- the reviewer never sees the builder's prompt or output, only the diff + the docs + the mockups
- the reviewer's system prompt (`CLAUDE_REVIEWER_PROMPT.md`) is adversarial-tuned

There is no fallback engine. Quota exhaustion → clean `waiting_quota` stop; next cron wake retries.

The pre-commit gate (`npm run test:modules` + `npm run test:build` + `npm run typecheck`) is the ONLY safety net after Dispatch 3.

## 10. Pre-commit gate is the contract  *(TESTING_POLICY.md)*

Before any commit, the orchestrator MUST run:

```bash
cd UI/macro-copilot-dashboard-polished
npm run test:modules
npm run test:build
npm run typecheck
```

ALL THREE must exit 0. Any failure → DO NOT COMMIT, mark `human_required`.

## 11. The factory may not mutate environments outside the working tree  *(P10)*

The automation may:
- read backend files for context
- read backend DB for the pre-flight tool_metadata check
- read the committed mockup PNGs
- write to the target frontend module folder + the shared cross-references in `src/types/rates.ts` + `src/services/ratesApi.ts` + `src/modules/index.ts`
- write to `api/routes/rates/detail.py` IF the typed-detail endpoint isn't already built
- run `npm run test:*` / `typecheck` (read-only)

The automation MUST NOT:
- ingest data
- backfill data
- modify schema
- run database writes / deletes / updates
- run `npm install`
- modify `package.json` / `package-lock.json`
- modify the backend tool folder
- modify the MCP wrapper
- modify the DB curated migration
- modify the per-tool backend README or LIFECYCLE_CHECKLIST
- modify the mockups (they're the input)

## 12. The branch is part of the safety model

This factory may only operate on: `frontend_automation`. The check_branch.sh wrapper enforces this at every `run_claude_builder.sh` and `run_claude_reviewer.sh` invocation.

## 13. Pre-flight backend audit is read-only  *(PRE_FLIGHT_BACKEND_AUDIT.md)*

Before dispatching the builder, the orchestrator verifies four artifacts exist:
1. Backend tool folder with 4-file shape
2. MCP wrapper registered
3. DB curated row populated OR curated migration file present
4. Both mockup PNGs committed in target frontend module folder

A miss → mark `blocked`, advance. The orchestrator does NOT manufacture missing artifacts.

## 14. Catalog-exhaustion shutdown

When every entry in `frontend_tool_catalog.yaml` is `done` or `blocked`, the orchestrator sets `orchestrator_status: catalog_exhausted` and stops scheduling new wakes. The cron entry-point script reads this flag and short-circuits subsequent wakes as no-ops. Human resumes by adding new catalog entries or clearing the flag.

Token-budget protection. See `BACKGROUND_EXECUTION_POLICY.md` §8.

## 15. Honest disclosure of dispatch round

The runtime state `dispatch_round` field is the audit trail. `last_reviewer_heading` carries `APPROVED-AFTER-FIX` (NOT `APPROVED`) when Dispatch 3 ran — so a human reading the catalog can see at a glance which tools went through a fix pass without re-review.
