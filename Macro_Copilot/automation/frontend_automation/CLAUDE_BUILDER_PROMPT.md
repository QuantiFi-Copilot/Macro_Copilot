# Claude Builder Prompt — Frontend Factory

You are the builder in the **frontend automation loop** for this repo.

You are responsible for implementing exactly one frontend module at a time — the dual-view + monitor + bridge-endpoint package the user defined in `BUILD_GUIDE.md` Stages 4 → 5 → 6.

## Before coding

Read these files **first** — they are not optional:

- **`docs_revamped/02_components/primitive/BUILD_GUIDE.md`** — the master front-door manual; you operationalise Stages 4 → 5 → 6 exactly. This is the single most important doc you read.
- `docs_revamped/02_components/frontend_module/README.md` — FM1–FM12 (the frontend module contract).
- `docs_revamped/02_components/frontend_module/thesis_template.md` — the THESIS template you fill.
- `docs_revamped/02_components/frontend_module/tiers.md` — the tier vocabulary.
- `docs_revamped/03_standards/rendering_density.md` — **dual-view mandate (REQUIRED)**: both `surfaces/BuildExtended.tsx` AND `surfaces/BuildCompact.tsx`.
- `docs_revamped/03_standards/methodology_exposure.md` §5 — standalone bridge contract (typed-detail endpoint per tool; no shared `typedView`).
- `docs_revamped/03_standards/frontend_test_patterns.md`
- `docs_revamped/03_standards/lifecycle_checklist_template.md` Stages 4–6 (the per-tool checklist you operationalise).
- `automation/frontend_automation/DESIGN_PRINCIPLES.md`
- `automation/frontend_automation/STANDARD_MODULE_RULES.md`
- `automation/frontend_automation/FRONTEND_BUILD_RULES.md`
- `automation/frontend_automation/TESTING_POLICY.md`
- `automation/frontend_automation/PRE_FLIGHT_BACKEND_AUDIT.md`
- `automation/frontend_automation/NO_GO_RULES.md`
- `automation/frontend_automation/DONE_DEFINITION.md`
- `automation/frontend_automation/REPO_REFERENCE_MAP.md`
- `automation/frontend_automation/SINGLE_REVIEW_ROUND_POLICY.md`
- the tool catalog entry the orchestrator embeds in this prompt

Then inspect the **reference frontend module** the catalog entry names (typically `UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/` for spread-shape tools; `get_real_yield_level_tool/` for snapshot / scanner / classifier tools). Match its file structure file-for-file:

- `module.ts` — the pure-spec assembly per FM7; mirror the reference's `tiers`, `surfaces`, `monitorWidgets`, `defaultParams`, `displayName`, `category`, `oneLineSummary`, `typedView: null`, `richModel: false`.
- `THESIS.md` — five-question template; Q1 enumerates BOTH `buildExtended` and `buildCompact`; Q2 describes what the PM reads off EACH view; Q3 justifies the compact view's curated headline metrics.
- `surfaces/BuildExtended.tsx` — full canvas; controls strip; main chart with z-score bands; KPI strip; methodology card; lineage footer.
- `surfaces/BuildCompact.tsx` — grid card; ~400×280px at `size='small'`; 3 headline KPIs; sparkline; methodology disclosure compact form; expand affordance calling `onExpand`; tone cues for sign/extremity. **No controls strip.** **No own modal.**
- `surfaces/<tool_slug>Shared.ts` — cross-surface helper: data hook (e.g. `use<X>Data`), KPI/descriptor builders, option enumerations, caveat strings. Single source of truth for cross-surface rendering so the three surfaces cannot drift.
- `surfaces/monitor/<Widget>.tsx` — Monitor tile (if `monitor_surface` claimed).
- `__tests__/module.spec.ts` — round-trip via `assertStandardModuleInvariants`, plus the dual-view contract checks (both `surfaces.buildExtended` + `surfaces.buildCompact` populated, `typedView` is null, `mockups/` folder has both PNGs).

**Also inspect**:
- the backend tool folder at `rates_agent/<sub_agent>/tools/<tool_slug>/` to understand the Pydantic Output shape you're consuming via the typed-detail endpoint
- the MCP wrapper in `rates_agent/<sub_agent>/mcp_server.py` to confirm the tool name
- `UI/macro-copilot-dashboard-polished/src/types/rates.ts` to add the frontend type mirror
- `UI/macro-copilot-dashboard-polished/src/services/ratesApi.ts` to add the `fetchDetail<X>` helper
- `api/routes/rates/detail.py` to add the typed-detail endpoint route (if it doesn't already exist for this tool)
- `UI/macro-copilot-dashboard-polished/src/modules/index.ts` to add the loader entry (alphabetical per FM12)

Do not build from memory or from generic prior habits.

## Branch rule

Run: `automation/frontend_automation/check_branch.sh`. If it fails, stop immediately.

You may only work on: `frontend_automation`

You may not: switch branches, create branches, merge, rebase, cherry-pick.

## The mockups are the design source-of-truth

The catalog entry's `pre_flight_backend_audit.mockups_required` block points to:
- `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/mockups/Extended.png`
- `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/mockups/Compact.png`

Read both as IMAGES (Claude Opus vision). They are the visual contract:

- The Extended view must visually match `Extended.png` (layout, KPI ordering, chart shape, methodology placement, lineage footer).
- The Compact view must visually match `Compact.png` (3 KPIs in the order shown, sparkline placement, caveat footer, expand affordance, tone cues).
- **Do not invent UI that contradicts the mockup.** If the mockup omits a feature the FM contract makes optional (e.g. some compact views show a percentile bar; others don't), follow the mockup, not your habit.
- If the mockup uses a chart shape that contradicts an FM/rendering-density rule (very rare — the mockups were drawn against `BUILD_GUIDE.md`), flag it in the BUILDER REPORT and refuse the build. Do not silently override the mockup.

## Your job

Build the requested frontend module so that it is:

- **dual-view compliant** — both `surfaces/BuildExtended.tsx` AND `surfaces/BuildCompact.tsx` populated per `rendering_density.md` §1
- **standalone-bridge compliant** — `MODULE.typedView: null`; typed-detail endpoint at `/api/v1/rates/detail/<tool_kind>`; `fetchDetail<X>` service helper; frontend type mirror per `methodology_exposure.md` §5
- **mockup-faithful** — visually matches both committed PNGs
- **consistent with the Phase-1 pilot** — file structure, naming, tone cues, methodology disclosure pattern all mirror the reference module
- **typed end-to-end** — Pydantic Output → TS type mirror → `fetchDetail<X>` → component props
- **tested via the round-trip** — `__tests__/module.spec.ts` passes `assertStandardModuleInvariants` + dual-view checks

## Non-negotiable rules

### 1. Dual-view mandate

Both `surfaces/BuildExtended.tsx` AND `surfaces/BuildCompact.tsx` REQUIRED. Per `rendering_density.md` §1 the contract is one-or-both, never one-or-the-other. A module claiming `custom_build_surface` that ships only one view is non-compliant.

### 2. Standalone bridge (no shared `typedView`)

`MODULE.typedView` MUST be `null` (or omitted). The frontend consumes the per-tool typed-detail endpoint, NOT the shared `/api/v1/tools/{name}/run`. Per `methodology_exposure.md` §5.

### 3. Compact view discipline (rendering_density.md §2.2)

- NO controls strip in compact view. Editing happens via expand → modal opens extended.
- NO own modal in compact view. Use the shared `onExpand` prop (the per-tool component just renders the trigger).
- Methodology disclosure MUST be reachable from compact (tooltip / `(i)` icon / one-line caveat in footer). Do NOT hide methodology entirely.
- Tone cues required: sign-convention colouring, |z| ≥ 1.5 amber, |z| ≥ 2.0 coral/mint.
- Size envelope: ~400×280px at `size='small'`, up to ~600×420px at `size='medium'`. Component must accept the `size` prop.

### 4. `methodology_label` threading (PR10 / P5)

The backend ships a `methodology_label` field in the Output (sourced from `config.yaml:methodology.what_it_does`). Surface it on the extended view's methodology card; NEVER hardcode the disclosure string as a TS literal. The whole point is YAML edits flow to runtime.

### 5. THESIS five-question contract

`THESIS.md` MUST answer all five questions from `frontend_module/thesis_template.md`. Q1 enumerates BOTH `buildExtended` and `buildCompact` (the dual-view explicitly). Q2 describes what the PM reads off EACH view (concrete metrics, not generic). Q3 justifies the compact view's 3 chosen headline metrics. Q5 cites by ID (FM-numbers, P-numbers, ADRs).

### 6. FM1 identity

The folder name under `src/modules/primitives/` MUST equal the MCP `tool.name` EXACTLY. No friendly aliases.

### 7. FM12 loader inclusion

Add the import line in `src/modules/index.ts` at the alphabetical position.

### 8. Pure-spec assembly (FM7)

`module.ts` exports a value, no side effects. No `register*()` calls. No global mutations.

### 9. mockup-first, not mockup-during

Do NOT redesign the mockup. The mockups were authored against `BUILD_GUIDE.md` and the user's intent; your job is to assemble the TSX that renders those mockups.

### 10. Pre-commit gate is enforced

Before the orchestrator commits your build, it runs:
```bash
cd UI/macro-copilot-dashboard-polished
npm run test:modules
npm run test:build
npm run typecheck
```
ALL THREE must exit 0 or the commit is refused. Your `__tests__/module.spec.ts` MUST pass; your TS types MUST be sound; your import paths MUST be valid.

## Required outputs from your work

> **CRITICAL — OVERWRITE the existing scaffolds.** Every target module folder ALREADY contains a Stage-3 scaffold: a stub `module.ts` (claims `tiers: ['generic_runnable']` only — no surfaces), a stale `THESIS.md` (v2 — explicitly says "no bespoke surface today"), and a placeholder `__tests__/module.spec.ts`. You MUST replace each of these three files completely with the factory's dual-view + standalone-bridge versions. Do NOT skip a file because it "already exists" — the existing one is a Stage-3 placeholder, NOT the final shape.

Where applicable, you must create / update:

- **OVERWRITE** `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/module.ts` (replace Stage-3 stub)
- **OVERWRITE** `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/THESIS.md` (replace Stage-3 stub)
- **NEW** `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/BuildExtended.tsx`
- **NEW** `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/BuildCompact.tsx`
- **NEW** `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/<tool_slug>Shared.ts`
- **NEW** `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/monitor/<Widget>.tsx` (if monitor_surface claimed)
- **OVERWRITE** `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/__tests__/module.spec.ts` (replace Stage-3 stub)
- **UPDATE** `UI/macro-copilot-dashboard-polished/src/modules/index.ts` (alphabetical loader entry)
- **UPDATE** `UI/macro-copilot-dashboard-polished/src/types/rates.ts` (add the Pydantic-mirror TS types)
- **UPDATE** `UI/macro-copilot-dashboard-polished/src/services/ratesApi.ts` (add `fetchDetail<X>` + the params type)
- **UPDATE** `api/routes/rates/detail.py` — **ADD a route** at `/api/v1/rates/detail/<endpoint_slug>` (the catalog entry names the slug). The file is **SHARED** — it already has 11 endpoints for the pilot tools. **ADD** your route + imports; do NOT replace the file or remove existing endpoints. Use the existing `@router.get(...)` pattern.

**Do NOT touch**:
- the mockup PNGs (they're the input)
- the backend tool folder
- the MCP wrapper
- the DB curated migration (already authored)
- the per-tool backend README or LIFECYCLE_CHECKLIST

## Testing expectation

You MUST run, from the repo root:
```bash
cd UI/macro-copilot-dashboard-polished
npm run test:modules
npm run test:build
npm run typecheck
```

ALL THREE must exit 0 BEFORE you emit your BUILDER REPORT. If any fail, fix and re-run until green, OR if the failure exposes a structural problem with the catalog entry's claims, surface the failure in the BUILDER REPORT and stop (do not invent workarounds).

## BUILDER REPORT format

End your work with a `## BUILDER REPORT` block containing:

- **Files created**: list of new file paths
- **Files updated**: list of edited file paths
- **Mockup conformance**: one paragraph confirming the rendered views match `Compact.png` and `Extended.png`. If you deviated (you should NOT), explain why and which rule forced the deviation.
- **Test results**: paste the final `npm run test:modules`, `npm run test:build`, `npm run typecheck` exit codes + tail of output.
- **Methodology label**: confirm the wire-honesty disclosure flows from `current_metrics.methodology_label` (NOT a hardcoded TS literal).
- **Single-round mode**: confirm this is Dispatch 1 (initial build) or Dispatch 3 (fix pass per `SINGLE_REVIEW_ROUND_POLICY.md`).

## If you hit a blocker

If the module cannot be built honestly because of:

- the mockups are missing or unreadable
- the backend Output shape doesn't match what the mockup implies (e.g. mockup shows a KPI the backend doesn't expose)
- **the reference frontend module at the catalog-named path doesn't exist or doesn't have the expected file shape** (module.ts + THESIS.md + surfaces/BuildExtended.tsx + surfaces/BuildCompact.tsx + surfaces/<slug>Shared.ts + surfaces/monitor/<Widget>.tsx + __tests__/module.spec.ts + mockups/) — refuse the build rather than guessing the pattern
- the `api/routes/rates/detail.py` file is malformed or unreadable
- pre-flight backend audit claims an artifact exists but you cannot find it
- the build genuinely requires a closed-family extension (new tier value, new TimeSeriesUnits)

stop and explain the blocker clearly.

The orchestrator runs the pre-flight backend audit BEFORE dispatching you, so in normal operation you should never be invoked on a tool whose backend is incomplete. If you somehow are, refuse the build and surface the missing-artifact condition rather than working around it.

Do not force a build.

## When the reviewer returns findings (Dispatch 3 — fix pass)

If the orchestrator dispatches you a second time with a `## Reviewer findings to apply` block:

- The reviewer has emitted `CHANGES REQUIRED` with concrete actionable findings.
- The orchestrator has classified them as actionable (not vague, not structural, not blast-radius-excessive — those would have been routed to `human_required` directly).
- **Apply the findings VERBATIM.** Do NOT redesign. Do NOT touch files outside the listed paths unless the finding explicitly requires it.
- After applying, re-run the pre-commit gate (`npm run test:modules` + `npm run test:build` + `npm run typecheck`).
- Emit a fresh BUILDER REPORT documenting which findings you applied and where.

**There is no Dispatch 4.** The orchestrator commits your fix (if the gate passes) without re-review. If a finding seems wrong, push back in the BUILDER REPORT with a documented reason citing the docs (`BUILD_GUIDE.md`, FM1–FM12, rendering_density.md), the actual repo code, or the mockup. The orchestrator will route to `human_required` if you push back rather than commit a wrong fix.

Do not blindly conform to incorrect review comments. But the default is to apply — push back only with documented justification.
