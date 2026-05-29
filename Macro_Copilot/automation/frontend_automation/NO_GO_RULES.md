# No-Go Rules — Frontend Factory

If any rule in this file is triggered, the tool must NOT be built in the current run. Mark the tool `blocked` or `human_required` per the orchestrator's rules and advance.

**Version:** v1
**Status:** load-bearing.

---

## 1. Missing mockups

If `mockups/Compact.png` OR `mockups/Extended.png` is missing from the target module folder, do NOT build. The mockups are the design source-of-truth per BUILD_GUIDE.md §Stage 6 6D + `PRE_FLIGHT_BACKEND_AUDIT.md` Check 4.

- Mark `blocked` with `block_reason: mockups_missing: <path>`.
- Continue to next tool.

The orchestrator MUST NOT manufacture mockups.

## 2. Missing backend artifacts

If ANY of the pre-flight backend audit checks fail (`PRE_FLIGHT_BACKEND_AUDIT.md` Checks 1–3):

- Backend tool folder missing or incomplete (4-file shape not satisfied)
- MCP wrapper not registered
- DB curated row not populated AND migration file not present

→ do NOT build. Mark `blocked` with reason `backend_metadata_missing`.

The backend is a precondition. The frontend factory does NOT build backend artifacts.

## 3. Not actually a frontend-buildable tool

If the catalog entry's `backend_tool_name` doesn't exist in any `mcp_server.py`, the tool was added to the catalog by mistake. Mark `blocked` with `block_reason: backend_tool_does_not_exist`.

## 4. Mockup contradicts the FM contract

If the mockup shows UI that contradicts a load-bearing rule (e.g. the compact view's mockup shows a controls strip, which `rendering_density.md §2.2` forbids), refuse the build:

- Builder: emit a BUILDER REPORT documenting the contradiction; do NOT silently override the mockup or the rule.
- Reviewer: emit `STRUCTURAL:` finding; orchestrator routes to `human_required`.

The human resolves: either fix the mockup or change the contract (via ADR).

## 5. Dual-view single-half ship

A new module that ships only `surfaces/BuildExtended.tsx` and skips `surfaces/BuildCompact.tsx` (or vice versa) is auto-reject. Per `rendering_density.md §1` the contract is one-or-both, never one-or-the-other.

This rule is enforced by `__tests__/module.spec.ts`'s dual-view contract check AND by the `loaderPresence.test.ts` global iteration.

## 6. typedView reuse on a new module

A new module that sets `MODULE.typedView` to anything other than `null` is auto-reject. Per `methodology_exposure.md §5`, new modules use the standalone bridge — not the shared `typedView` closed family.

The reviewer flags this; the orchestrator routes to `human_required` (it's a `STRUCTURAL:` finding because fixing it requires changing the module's whole bridge approach).

## 7. Hardcoded methodology disclosure

A new module whose `surfaces/BuildExtended.tsx` contains the methodology disclosure as a TSX string literal (instead of reading `current_metrics.methodology_label` from the typed-detail response) violates PR10 / P5. The reviewer flags this; the orchestrator routes the fix to Dispatch 3 (it's actionable, not structural).

## 8. Branch violation

If the current branch is not exactly: `frontend_automation`, the automation stops immediately.

It may not: switch branches, create a new branch, rebase, merge, cherry-pick.

## 9. Incomplete module folder

Do not call a tool done if it's missing required files:

- `module.ts`
- `THESIS.md`
- `surfaces/BuildExtended.tsx`
- `surfaces/BuildCompact.tsx`
- `surfaces/<tool_slug>Shared.ts`
- `surfaces/monitor/<Widget>.tsx` (if monitor_surface claimed)
- `__tests__/module.spec.ts`
- mockups already present (input)

Per `STANDARD_MODULE_RULES.md §1`.

## 10. Missing cross-references

Do not call a tool done if the cross-references aren't wired:

- `src/modules/index.ts` loader entry (alphabetical, per FM12)
- `src/types/rates.ts` TS type mirror of the Pydantic Output
- `src/services/ratesApi.ts` `fetchDetail<X>` helper + `<X>DetailParams` type
- `api/routes/rates/detail.py` typed-detail route (if not already present)

Per `STANDARD_MODULE_RULES.md §9, §10`.

## 11. Reviewer not satisfied

If the reviewer emitted `CHANGES REQUIRED` with findings that the orchestrator classified as structural / vague / blast-radius-excessive / mockup-contradicting / hallucinated, the orchestrator routes to `human_required` (per `SINGLE_REVIEW_ROUND_POLICY.md §3`) — Dispatch 3 (fix pass) is SKIPPED.

Do NOT silently downgrade the verdict to APPROVED. Do NOT commit on partial reviewer satisfaction.

## 12. Pre-commit gate fails

If `npm run test:modules` / `npm run test:build` / `npm run typecheck` fail (after Dispatch 1 OR after Dispatch 3), do NOT commit. Mark `human_required`.

Per `TESTING_POLICY.md §3` and `SINGLE_REVIEW_ROUND_POLICY.md §5`, the gate is the only safety net after the fix pass — its failure is a hard stop.

## 13. Modifying the backend, mockups, or DB

The frontend factory may NOT:

- Modify the backend tool folder
- Modify the MCP wrapper or any backend Python file
- Modify the DB curated migration SQL
- Apply ingestion / writes / updates to the DB
- Overwrite or "improve" the mockup PNGs
- Modify the backend per-tool README or LIFECYCLE_CHECKLIST
- Modify `package.json` or `package-lock.json`
- Run `npm install`

If any of these become necessary, mark `human_required` with a precise note about what backend / mockup / package change is needed.

## 14. Loop continuation after CHANGES REQUIRED that should be human_required

If the reviewer emits `CHANGES REQUIRED` but the orchestrator (in good faith) classifies all findings as actionable + bounded and dispatches the fix pass, then the fix pass's gate fails, do NOT loop back to a second review round. Per `SINGLE_REVIEW_ROUND_POLICY.md`, mark `human_required` and stop the wake.

## 15. Single tool at a time

This automation must never batch tools in one dispatch. Per `DESIGN_PRINCIPLES.md §11` (carried from the primitive factory): the loop is strictly serial. Catalog iteration advances one entry at a time.

## 16. Modifying the runtime state for tools other than `current_tool`

The orchestrator MAY only mutate the catalog entry + runtime fields for `current_tool`. Mutating other entries (status changes, dispatch_round bumps) is forbidden in the same wake — those changes happen when the orchestrator selects that other tool as `current_tool` in a subsequent iteration.

## 17. Committing without the pre-commit gate

Every commit MUST be preceded by a green pre-commit gate. Committing without the gate (or with `last_gate_result != pass`) is a hard violation of `TESTING_POLICY.md`.

## 18. Reviewer engine swap

This factory has NO fallback reviewer engine (Claude-only). If the parser reports `reviewer_quota_exhausted: true`, mark `waiting_quota` and STOP the wake. Do NOT attempt to swap engines (there's nothing to swap to). Do NOT proceed to a fix dispatch without a verdict.

Per `DESIGN_PRINCIPLES.md §10`.

## 19. Catalog-entry shape violations

If a catalog entry is missing `pre_flight_backend_audit`, `mockups_required`, `reference_frontend_module`, `target_frontend_folder`, or `required_tiers`, mark `human_required`. The orchestrator cannot dispatch without these fields.

## 20. Mockup-image read failure

If the builder cannot read the mockup PNGs as images (file corrupted, wrong format, unreadable), STOP and mark `human_required`. Do NOT fabricate the visual contract.
