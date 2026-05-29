# Done Definition — Frontend Factory

A frontend module is **done** only when ALL of the following are true.

**Version:** v1
**Status:** load-bearing exit criteria.

---

## 1. The backend is in place (pre-flight passed)

The pre-flight backend audit (`PRE_FLIGHT_BACKEND_AUDIT.md`) reported all four checks green BEFORE the builder was dispatched:

- Backend tool folder with 4-file shape
- MCP wrapper registered
- DB curated row populated OR migration file present
- `mockups/Compact.png` + `mockups/Extended.png` committed

## 2. The module folder has all required files

Per `STANDARD_MODULE_RULES.md §1`:

- `module.ts` — pure-spec assembly per FM7
- `THESIS.md` — five-question template per FM10
- `surfaces/BuildExtended.tsx` — full canvas per rendering_density.md §2.1
- `surfaces/BuildCompact.tsx` — grid card per rendering_density.md §2.2
- `surfaces/<tool_slug>Shared.ts` — cross-surface helper
- `surfaces/monitor/<Widget>.tsx` — Monitor tile (if monitor_surface claimed)
- `__tests__/module.spec.ts` — round-trip + dual-view contract
- `mockups/Compact.png` + `mockups/Extended.png` — already present (input)

## 3. The module spec satisfies FM1–FM12 (rendering_density override included)

- **FM1** — folder name === backend `tool.name` exactly
- **FM3** — tiers include `custom_build_surface` (overriding FM4 parsimony per rendering_density.md §1.2)
- **FM7** — `module.ts` is a pure value export, no side effects
- **FM8** — every claimed tier has a populated `surfaces.<key>` AND a file at the canonical path
- **FM9** — `typedView: null` (standalone-bridge pattern; methodology_exposure.md §5)
- **FM10** — THESIS exists and answers all five questions
- **FM11** — round-trip test passes
- **FM12** — alphabetical loader entry in `src/modules/index.ts`

## 4. The dual-view contract is satisfied

Per `rendering_density.md §1`:

- BOTH `surfaces.buildExtended` AND `surfaces.buildCompact` populated in `module.ts.surfaces`
- BOTH `surfaces/BuildExtended.tsx` AND `surfaces/BuildCompact.tsx` exist on disk
- THESIS Q1 enumerates BOTH views explicitly
- THESIS Q2 describes the per-view read concretely
- THESIS Q3 justifies the compact view's curated metrics
- The dual-view contract check in `__tests__/module.spec.ts` passes
- The global `loaderPresence.test.ts` enforces the contract for this module

## 5. The standalone bridge contract is satisfied

Per `methodology_exposure.md §5`:

- `MODULE.typedView: null` (or omitted)
- Typed-detail endpoint mounted at `/api/v1/rates/detail/<tool_kind>` (per-tool path)
- `fetchDetail<X>` service helper in `src/services/ratesApi.ts`
- Frontend type mirror in `src/types/rates.ts`
- BOTH extended + compact views consume the SAME typed-detail endpoint

## 6. The mockup conformance check passed

- The rendered extended view visually matches `mockups/Extended.png`
- The rendered compact view visually matches `mockups/Compact.png`
- The builder's BUILDER REPORT confirms conformance (no undocumented deviation)
- The reviewer's APPROVED verdict implicitly approves the conformance

## 7. The methodology disclosure threads from the backend

- `current_metrics.methodology_label` is surfaced on the extended view's methodology card
- The disclosure string does NOT appear as a TSX literal anywhere in `surfaces/`
- `grep` the rendered surface files for the manifest one_liner returns no hits

## 8. The shared cross-surface helper prevents drift

- `surfaces/<tool_slug>Shared.ts` exists
- Data hook, option enumerations, KPI builders, caveats live in this file
- Extended view imports from it (no duplicated logic)
- Compact view imports from it (no duplicated logic)
- Monitor widget imports from it (no duplicated logic)

## 9. The reviewer approved

Per `SINGLE_REVIEW_ROUND_POLICY.md`:

- Dispatch 2 (reviewer) emitted `APPROVED` (single-shot) OR
- Dispatch 2 (reviewer) emitted `CHANGES REQUIRED` AND Dispatch 3 (fix pass) ran AND the pre-commit gate passed

If the latter, `last_reviewer_heading` is `APPROVED-AFTER-FIX` (honest disclosure that the fix bypassed re-review).

## 10. The pre-commit gate passed

Per `TESTING_POLICY.md`:

```bash
cd UI/macro-copilot-dashboard-polished
npm run test:modules    # exit 0
npm run test:build      # exit 0
npm run typecheck       # exit 0 (no NEW errors vs baseline)
```

All three commands MUST have exited 0 BEFORE the commit.

## 11. The commit lives on `frontend_automation`

Per the branch rule, the commit SHA is on the `frontend_automation` branch (and only that branch). Never on `main` / `revamp` / `primitive_automation`.

Commit message follows the template in `ORCHESTRATOR_PROMPT.md` §"Commit-message template".

## 12. The catalog + runtime state are updated truthfully

After commit:

- `frontend_tool_catalog.yaml` entry: `status: done`, `commit_sha: <sha>`, `committed_at: <iso>`, `last_review_heading: APPROVED` (or `APPROVED-AFTER-FIX`)
- `frontend_runtime_state.yaml`: `last_successful_tool`, `last_successful_commit`, `done++`, `remaining--`, `current_tool: null` (until next iteration), `current_step: idle`

## 13. The factory is ready for the next tool

- The working tree is clean (the only changes are the just-committed files)
- No leftover diff from a blocked tool sits in the working tree
- The next eligible tool (per selection order: in_progress → changes_required → waiting_quota → todo) is identified or the catalog is exhausted

---

A tool that fails ANY of (1)–(13) is NOT done. The orchestrator marks it `blocked` or `human_required` per the operational rules; it does NOT silently commit a partial state.
