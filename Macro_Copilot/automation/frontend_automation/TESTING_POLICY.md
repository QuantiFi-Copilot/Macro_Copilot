# Testing Policy — Frontend Factory

This automation does NOT treat "claude said the build worked" as sufficient. Every frontend module's build is verified through a **pre-commit gate** before any commit is allowed.

This is the frontend-factory equivalent of the primitive factory's `TESTING_AND_DB_VALIDATION_POLICY.md`. The shape is similar (mandatory gate, read-only verification, no mutation, no commit without the gate). The contents differ because the verification target is npm tests + TypeScript + the module round-trip — NOT pytest + DB SQL validation.

**Version:** v1
**Status:** load-bearing operational policy.

---

## 1. The pre-commit gate (the only gate)

After Dispatch 1 (initial build) emits `APPROVED` OR after Dispatch 3 (fix pass) completes, the orchestrator MUST run from the repo root:

```bash
cd UI/macro-copilot-dashboard-polished
npm run test:modules
npm run test:build
npm run typecheck
```

**ALL THREE must exit 0.** Any failure → DO NOT COMMIT.

Per `SINGLE_REVIEW_ROUND_POLICY.md` §5, this gate is the ONLY safety net after Dispatch 3 (the fix pass). There is no re-review.

## 2. What each command tests

### `npm run test:modules`

Runs the module round-trip tests across `src/modules/primitives/<every>/__tests__/module.spec.ts`. Each per-module spec calls `assertStandardModuleInvariants(MODULE, {folderName, moduleFolderPath})` from `src/modules/__test-utils.ts`, which checks the FM1–FM12 invariants in one shot:

- FM1 — folder name === toolName
- FM3 — `tiers` is a subset of the closed family + exactly one runtime-status tier
- FM7 — `module.ts` is a pure value export
- FM8 — every claimed capability tier has a corresponding `surfaces/<Name>.tsx` file
- FM10 — `THESIS.md` exists, Q1 enumerates the tier set
- FM11 — the spec calls `assertStandardModuleInvariants` (recursive — this test asserts itself)
- FM12 — the module is imported in `src/modules/index.ts`

Plus the per-module additional checks (the new factory's modules add):

- `tiers` includes `custom_build_surface`
- `surfaces.buildExtended` is populated
- `surfaces.buildCompact` is populated
- `typedView` is null (standalone-module pattern)
- `mockups/` folder exists with `Compact.png` AND `Extended.png`

A failure in any of these → DO NOT COMMIT.

Plus the platform-wide loader-presence test at `src/lib/__tests__/loaderPresence.test.ts` which iterates the global module registry and validates the dual-view contract for every module that claims `custom_build_surface`.

### `npm run test:build`

Runs the Vite production build (or equivalent per `package.json`). This catches:

- broken imports (the new module's loader entry is wrong; a surface file path doesn't resolve)
- broken JSX
- broken type re-exports
- bundler-level errors that don't surface in `npm run typecheck` alone

### `npm run typecheck`

Runs `tsc --noEmit` (or equivalent). This catches:

- new TypeScript errors in the module's surfaces (wrong prop types, missing imports, mismatched generic constraints)
- type drift between the backend Output mirror (in `types/rates.ts`) and the surface component's consumer
- regression in `__test-utils.ts` or `loaderPresence.test.ts` triggered by the new module's spec

The baseline for `npm run typecheck` is "no NEW errors beyond the pre-existing repo baseline". If the repo currently has X errors at HEAD, the gate is "exactly X errors after the build" (NOT zero — the legacy modules carry their own debt).

The orchestrator captures the BEFORE-count by running `npm run typecheck` against HEAD before dispatching the builder, then compares AFTER-count after the builder finishes. If AFTER > BEFORE, the gate fails.

## 3. The gate is the ONLY safety net after Dispatch 3

Per `SINGLE_REVIEW_ROUND_POLICY.md`, this factory does NOT re-review the fix pass. The trust model is:

- Dispatch 1 (builder) produces the initial build
- Dispatch 2 (reviewer) emits APPROVED or actionable CHANGES REQUIRED
- Dispatch 3 (builder, optional fix pass) applies the reviewer findings
- Pre-commit gate runs

If the gate passes → commit. If the gate fails → mark `human_required`.

The gate IS the test of whether Dispatch 3 introduced a regression. There is no Dispatch 4 to re-review.

## 4. Read-only

This automation may use the frontend build / test infrastructure only for read-only verification:

- `npm run test:modules` — runs Vitest in `node` mode against `module.spec.ts`; no file mutation
- `npm run test:build` — outputs to `UI/macro-copilot-dashboard-polished/dist/` which is gitignored; no source mutation
- `npm run typecheck` — `tsc --noEmit`; no emit, no mutation

The automation may NOT:

- run `npm install` (the lockfile is authoritative; install drift is a separate human-authored PR)
- modify `package.json` or `package-lock.json`
- run `npm run lint --fix` (auto-fixing belongs to the builder dispatch, not the gate)
- run any script with side effects on the database or the live API server

If the gate runner discovers a missing npm dependency, that is a HARD STOP (`human_required` with reason `npm_dependency_missing`) — NOT an opportunity to `npm install`.

## 5. What "gate green" means by case

### Case C — APPROVED on first review

- Gate runs after the reviewer emits `APPROVED`.
- Gate green → commit. `last_reviewer_heading: APPROVED`.
- Gate red → `human_required` with reason `pre_commit_gate_failed_after_approve`. DO NOT commit.

### Case D — APPROVED-AFTER-FIX (Dispatch 3 ran)

- Gate runs after Dispatch 3 (the builder fix pass).
- Gate green → commit. `last_reviewer_heading: APPROVED-AFTER-FIX` (honest disclosure that the fix bypassed re-review).
- Gate red → `human_required` with reason `pre_commit_gate_failed_after_fix`. DO NOT commit.

There is NO Case where the gate is skipped.

## 6. What the orchestrator records

After each gate run:

```yaml
last_gate_result: pass | fail
last_gate_run_at: <iso>
last_gate_output: |
  <tail of npm output, ~50 lines>
```

If the gate fails, the FULL gate output goes into `status_notes` with the failure context so the human can diagnose.

## 7. Manual smoke checks (out-of-band, NOT part of the automated gate)

A human SHOULD periodically run these manual smoke checks against a sample of factory-built modules:

- Open the module via Library → "Open in Build" → confirm the EXTENDED view renders cleanly (single-tool dispatch).
- Trigger a multi-tool Ask query containing this module → confirm the COMPACT view renders inside the DAG (multi-tool dispatch).
- Click the compact card's expand affordance → confirm the modal mounts the extended view with breadcrumb.
- Add the module's Monitor widget from the catalog modal → confirm it renders live data.
- Compare the rendered views to the committed mockups (`mockups/Compact.png` + `mockups/Extended.png`) for visual fidelity.

These are NOT part of the automated gate (the automation cannot run a browser). They are tracked in the per-tool `LIFECYCLE_CHECKLIST.md` Stage 6 `Manual smoke` rows.

A periodic out-of-band failure of the manual smoke → a finding that escapes both the reviewer and the gate. The fix is to extend the gate to catch the class of bug. See §8.

## 8. Gate-extension protocol

If a class of bug repeatedly escapes the gate (e.g. a wrong prop type that `npm run typecheck` lets through because of an `any` cast), the gate is extended in a separate PR — NOT inside the factory's commit window.

The extension PR:

1. Adds a NEW test or check that catches the class.
2. Runs the new check against ALL existing factory-built modules to confirm none regress.
3. Updates this policy doc with the new check + rationale.

The factory CANNOT extend its own gate. The human decides what regression classes are worth catching.

## 9. Anti-patterns

- Committing without the gate.
- "Fixing" a gate failure by adding an `// @ts-ignore` or `// eslint-disable-line` (mark `human_required`; the human decides).
- Running `npm install` to satisfy a missing dependency (HARD STOP).
- Treating a `npm run test:modules` failure as a flake (it's NOT — Vitest is deterministic; investigate every failure).
- Treating a `npm run typecheck` failure as "pre-existing baseline" without diffing against the pre-dispatch baseline.
- Manually editing the test files to make them pass (the test is the contract; if the test fails, the code is wrong).
- Skipping `npm run test:build` because "it's slow" — it catches real bundler-level issues that the other two don't.

## 10. Links

- `SINGLE_REVIEW_ROUND_POLICY.md` §5 — the gate IS the only safety net after Dispatch 3
- `ORCHESTRATOR_PROMPT.md` §"Pre-commit gate rule" — the operational embodiment
- `DONE_DEFINITION.md` — a module is done only if the gate passed
- BUILD_GUIDE.md §Stage 6 — the gate runs are listed as part of the Stage 6 gate
- `src/modules/__test-utils.ts` — `assertStandardModuleInvariants` (the heart of `npm run test:modules`)
- `src/lib/__tests__/loaderPresence.test.ts` — the loader-presence + dual-view contract test
