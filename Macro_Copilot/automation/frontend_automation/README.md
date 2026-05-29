# Frontend Automation

This directory is the repo-local control surface for the **second** OpenClaw automation vertical — building **frontend modules** for backend primitive tools that already exist on `revamp` (or its descendants) but lack a real frontend.

- process the frontend tool catalog one tool at a time
- on branch `frontend_automation` only
- using two fresh-context Claude dispatches per tool (builder + reviewer)
- following the standardisation we built in `docs_revamped/02_components/primitive/BUILD_GUIDE.md` exactly
- consuming PNG mockups committed alongside the tool's frontend module folder as the visual source-of-truth
- **single round only** — no re-review after fix; see [`SINGLE_REVIEW_ROUND_POLICY.md`](SINGLE_REVIEW_ROUND_POLICY.md)

This package is intentionally repo-local so the automation reads the same source of truth the human Phase-1 pilot used:

- `docs_revamped/02_components/primitive/BUILD_GUIDE.md` — the 8-stage end-to-end manual; this factory operationalises **Stages 4 → 5 → 6** (Frontend module spec → Frontend bridge endpoint → Frontend surfaces)
- `docs_revamped/02_components/frontend_module/README.md` — FM1–FM12
- `docs_revamped/03_standards/rendering_density.md` — dual Build-view mandate
- `docs_revamped/03_standards/methodology_exposure.md` §5 — standalone bridge contract
- `docs_revamped/03_standards/frontend_test_patterns.md`
- the Phase-1 pilot module `calculate_breakeven_inflation_simple_tool/` — the parity target

## Scope of this automation

This automation is ONLY for building frontend modules for backend primitive tools that already pass [`BUILD_GUIDE.md`](../../docs_revamped/02_components/primitive/BUILD_GUIDE.md) Stages 1–3 (backend folder + tests + DB curated metadata).

It does NOT own:

- backend primitive work (handled by `primitive_automation`)
- operators
- workflow templates
- legacy module migrations (modules that already ship a real `surfaces/BuildSurface.tsx`)
- orchestrator logic outside the frontend loop

It is intentionally limited to:

- one tool at a time
- one branch only: `frontend_automation`
- one build-and-review pass per tool (NO multi-round bounce loop — see [`SINGLE_REVIEW_ROUND_POLICY.md`](SINGLE_REVIEW_ROUND_POLICY.md))

## Tool catalog

The frontend tool catalog lives in:

- `frontend_tool_catalog.yaml`

It contains only:

- the first VM automation batch — 10 backend tools without frontends
- entries whose backend already exists on this branch and whose DB curated metadata row has been written
- entries whose `mockups/Compact.png` + `mockups/Extended.png` files have been committed in the tool's frontend module folder

It intentionally excludes:

- tools whose backend is blocked / pending source-material verification beyond what we accept
- tools requiring complex bespoke chart shapes (PCA loadings, rolling-regression diagnostics) — those need per-tool design choices the catalog does not yet encode
- tools that already have a legitimate frontend (the Phase-1 pilots)

## Files in this package

- `DESIGN_PRINCIPLES.md` — Core rules the builder and reviewer must obey. Operational restatement of FM1–FM12 + rendering_density + methodology_exposure §5 + BUILD_GUIDE Stages 4-6.
- `STANDARD_MODULE_RULES.md` — Exact standard-module definition + folder shape + dual-view contract enforced at build time.
- `FRONTEND_BUILD_RULES.md` — Step-by-step build rules for each frontend module (the 8-artifact slot list from BUILD_GUIDE.md §Stage 4–6).
- `TESTING_POLICY.md` — Mandatory pre-commit gate: `npm run test:modules` + `npm run test:build` + `npm run typecheck` + manual smoke checklist (all dispatch paths render).
- `PRE_FLIGHT_BACKEND_AUDIT.md` — Mandatory pre-flight gate. Before any builder dispatch, the orchestrator verifies the backend tool folder exists, the MCP wrapper is registered, the `tool_metadata` curated row is populated (or the migration file exists), and the mockup PNGs are committed. Read-only check; no fix-up. On a miss, the tool is `blocked` and the loop advances.
- `NO_GO_RULES.md` — Hard stop conditions. If any of these fire, the tool must be deferred, not approximated.
- `DONE_DEFINITION.md` — What "finished" means for a frontend module in this repo.
- `REPO_REFERENCE_MAP.md` — Exact files the automation must consult before building/reviewing.
- `SINGLE_REVIEW_ROUND_POLICY.md` — **NEW.** Codifies the explicit user constraint: build → review → optional one-shot fix → commit. No re-review. Cap on dispatches per tool.
- `ORCHESTRATOR_PROMPT.md` — Prompt for the OpenClaw frontend-orchestrator.
- `CLAUDE_BUILDER_PROMPT.md` — Prompt for the Claude Code builder.
- `CLAUDE_REVIEWER_PROMPT.md` — Prompt for the Claude reviewer (fresh context per dispatch).
- `frontend_tool_catalog.yaml` — The ordered tool backlog for this automation slice.
- `frontend_runtime_state.yaml` — Durable runtime/state surface for quota state, current tool, current step, counts, dispatch round, and Discord-readable progress reporting.
- `run_claude_builder.sh` — Canonical VM wrapper for Claude builder runs with explicit model, effort, branch check, and log capture.
- `run_claude_reviewer.sh` — Canonical VM wrapper for Claude reviewer runs (fresh context per dispatch; same model/effort discipline as the builder).
- `parse_reviewer_log.py` — Helper that extracts the reviewer heading, flags environment failures, AND flags `reviewer_quota_exhausted` from the reviewer log. (In the single-Claude factory the quota flag triggers a clean `waiting_quota` stop, not an engine swap.)
- `QUOTA_AND_RESUME_POLICY.md` — Limit checks, thresholds, stop conditions, and resume rules.
- `BACKGROUND_EXECUTION_POLICY.md` — Background run model: recurring wakeups, catalog exhaustion loop, and restart semantics.
- `DISCORD_STATUS_POLICY.md` — What status the orchestrator must write so Discord can answer useful progress questions.
- `OPENCLAW_CRON_RUNBOOK.md` — Recommended background scheduler shape for the frontend factory.
- `DISCORD_STATUS_QUERIES.md` — Sample progress questions the Discord-facing `main` agent should be able to answer from repo state.
- `check_branch.sh` — Hard preflight that refuses to run unless the repo is checked out on `frontend_automation`.
- `pre-commit-branch-guard.sh` — Local hook template that blocks commits on any branch other than `frontend_automation`.
- `pre-push-branch-guard.sh` — Local hook template that blocks pushes from any branch other than `frontend_automation`.

## Intended loop

1. Run `check_branch.sh`.
2. Read the next eligible tool catalog entry from `frontend_tool_catalog.yaml`.
3. Read:
   - `DESIGN_PRINCIPLES.md`
   - `STANDARD_MODULE_RULES.md`
   - `FRONTEND_BUILD_RULES.md`
   - `TESTING_POLICY.md`
   - `PRE_FLIGHT_BACKEND_AUDIT.md`
   - `NO_GO_RULES.md`
   - `DONE_DEFINITION.md`
   - `REPO_REFERENCE_MAP.md`
   - `SINGLE_REVIEW_ROUND_POLICY.md`
   - `QUOTA_AND_RESUME_POLICY.md`
   - `BACKGROUND_EXECUTION_POLICY.md`
   - `DISCORD_STATUS_POLICY.md`
4. Check Claude limits and normalize them into the required JSON contract before starting new work.
5. **Run the pre-flight backend audit** for the selected tool (backend folder + MCP wrapper + `tool_metadata` row + mockup PNGs all present). On a miss, block that tool, record `last_stop_reason: pre_flight_backend_audit_missing`, and advance to the next eligible entry.
6. The orchestrator writes the builder prompt to a temp file and runs `run_claude_builder.sh` for the current tool. The builder produces all 8 frontend artifact slots per [`BUILD_GUIDE.md`](../../docs_revamped/02_components/primitive/BUILD_GUIDE.md) Stages 4 → 5 → 6.
7. The orchestrator writes the reviewer prompt to a temp file and runs `run_claude_reviewer.sh`.
8. Parse the resulting log with `parse_reviewer_log.py`. If it reports `reviewer_quota_exhausted: true` and no valid heading, stop cleanly as `waiting_quota` (NO engine swap — there is no fallback engine in the single-Claude factory).
9. **Apply the single-round adjudication rule** per [`SINGLE_REVIEW_ROUND_POLICY.md`](SINGLE_REVIEW_ROUND_POLICY.md):
   - `APPROVED` → run the pre-commit gate, commit, advance to next tool.
   - `CHANGES REQUIRED` with concrete actionable findings → dispatch the builder ONCE more (the fix pass). After the fix, run the pre-commit gate. If gates pass, commit. **No re-review.** If gates fail OR findings were structural / vague → mark `human_required`.
   - `DEFER / DO NOT BUILD` → mark blocked.
10. When a tool is approved (or approved-after-fix), the orchestrator immediately advances to the next eligible tool in the same run.
11. The run stops only when:
    - the catalog is exhausted (→ catalog-exhaustion shutdown per `BACKGROUND_EXECUTION_POLICY.md` §8)
    - a tool is honestly blocked
    - quota is too low to continue safely
    - Claude quota is exhausted
    - a hard platform/runtime error occurs
    - or human input is genuinely required

If a tool is blocked after scaffold has already been generated:

- the blocker must be recorded truthfully
- the diff must not be left in-tree and allowed to contaminate the next tool
- default behavior is to discard blocked scaffold and continue
- only an explicit human preservation instruction may keep blocked work in the tree

Approval requires:

- both `surfaces/BuildExtended.tsx` AND `surfaces/BuildCompact.tsx` populated per [`rendering_density.md`](../../docs_revamped/03_standards/rendering_density.md) §1
- pre-commit gate green: `npm run test:modules` + `npm run test:build` + `npm run typecheck`
- the tool's `mockups/Compact.png` + `mockups/Extended.png` referenced from the THESIS

Background operation is intentionally:

- wake up
- process as much of the catalog as possible
- stop cleanly on quota / blocker / completion
- wake again later and resume from repo state

It is not intended to depend on one immortal in-memory chat session.

## Branch rule

This automation may only:

- read
- write
- test
- commit

while the current branch is exactly:

`frontend_automation`

It may NOT:

- switch branches
- create side branches
- merge into any other branch
- rebase onto any other branch
- cherry-pick from any other branch
- push changes intended for any other branch

This is a deliberate simplification for V1.

## Optional local hook hardening

If you want an additional local guardrail beyond the prompts and `check_branch.sh`, use:

- `pre-commit-branch-guard.sh`
- `pre-push-branch-guard.sh`

as templates for `.git/hooks/pre-commit` and `.git/hooks/pre-push`.

They are not auto-installed by this package, but they are provided as strict local guardrail templates.

## Why this exists

The repo just proved one strong manual pattern through the Phase-1 dual-view pilot (`calculate_breakeven_inflation_simple_tool` + `calculate_real_yield_curve_spread_tool` + `get_real_yield_level_tool`):

- single front-door doc (`BUILD_GUIDE.md`)
- mockup-first design workflow (PNGs committed alongside the module)
- dual-view rendering-density contract (extended + compact, both required)
- standalone-bridge contract (per-tool typed-detail endpoint, no shared `typedView`)
- shared cross-surface helper (`<tool_slug>Shared.ts`) so the three surfaces cannot drift
- per-module round-trip test asserting the dual-view contract

This package exists to preserve that exact discipline while making the loop automatable across the remaining backend tools that lack frontends.

The two automation-only behavioural choices on top of the Phase-1 manual workflow are:

1. **Two fresh-context Claudes** — primary builder + adversarial reviewer, both via `claude --print` (inherently fresh per invocation, no conversation state bleeds). The independence trade-off vs the primitive factory's Codex+Claude split is documented in [`DESIGN_PRINCIPLES.md`](DESIGN_PRINCIPLES.md) §10.
2. **Single review round** — build → review → (optional one-shot fix) → commit. NO re-review of the fix. Trade-off + mitigations documented in [`SINGLE_REVIEW_ROUND_POLICY.md`](SINGLE_REVIEW_ROUND_POLICY.md).
