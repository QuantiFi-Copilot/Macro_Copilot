# Frontend Quality Metrics

> What "a complete frontend module" looks like; how we measure registry drift; what the failing-test signals mean. Measurable contract for the frontend's health.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** measurable contract. Thresholds bump via PR; metric definitions are stable.
**Operationalises principles:** [FP6](../00_thesis/03_frontend_thesis.md), [FP11](../00_thesis/03_frontend_thesis.md), [FM11](../02_components/frontend_module/README.md), P3 (consistency by contract), P4 (determinism).
**See also:** [`../03_standards/frontend_test_patterns.md`](../03_standards/frontend_test_patterns.md), [`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md).

---

## Module completeness

A module is "complete" when:

1. Folder name == backend `tool_name` (FM1).
2. `module.ts` exports `MODULE: PrimitiveModuleSpec`.
3. `THESIS.md` exists with all five questions answered (FM10).
4. For each tier in `MODULE.tiers`, the corresponding `surfaces/<Name>.tsx` exists (FM3, FM8).
5. `__tests__/module.spec.ts` exists and passes the round-trip check (FM11).
6. The module is registered in `src/modules/index.ts` (FM12).

**Module completeness ratio (MCR).** Across all modules: percentage that satisfy all six conditions.

| Target | Value |
|---|---|
| Steady-state | 100% |
| Stage 2+ | ≥ 100% on migrated modules; the unmigrated set lives outside `src/modules/` and is excluded from MCR |

MCR is mechanical: the per-module round-trip test asserts all six conditions, so MCR is "test-pass rate across modules".

## Parity drift

Drift between backend `_PRIMITIVE_SPECS ∪ WORKFLOW_INCOMPATIBLE_TOOLS` and frontend `ALL_PRIMITIVE_MODULES` is measured by `tools/check_module_parity.py`.

| Metric | Definition | Target |
|---|---|---|
| **Backend-only tools** | Tools the backend ships with no frontend module | 0 (after Stage N) |
| **Frontend-only modules** | Modules with no matching backend tool (excluding `deferred` reservations) | 0 always |
| **Tier-claim drift** | Modules whose runtime-status tier disagrees with the backend's registration (e.g. a module claims `generic_runnable` but backend has it in `WORKFLOW_INCOMPATIBLE_TOOLS`) | 0 always |

These metrics are CI-failing. Non-zero parity drift blocks merge.

## Test signals

| Test failure | What it means | Where to look |
|---|---|---|
| `routingCoverage.test.ts: KNOWN_BACKEND_TOOLS expected N, got M` | Registry count drifted (a module was added/removed without snapshot update) | Update the snapshot in the same PR as the module change |
| `assertStandardModuleInvariants: folder name doesn't match toolName` | FM1 violation | Rename the folder OR update `module.ts.toolName` |
| `assertStandardModuleInvariants: missing surface file for claimed tier` | FM3 violation | Create the file OR drop the tier |
| `assertStandardModuleInvariants: THESIS surfaces don't match tiers` | FM10 staleness | Update THESIS Question 1 |
| `check_module_parity: backend has X, frontend doesn't` | Backend PR added a primitive without a coupled frontend PR | Add the module in this PR OR the same coordinated PR |
| `no_hand_authored_registries: literal Set found in toolNames.ts` | A hand-authored registry entry was added (Stage N violation) | Move the tool's registration into a module |

A failed test in any category is a build error. CI does not retry or auto-suppress.

## Coverage targets

Per [`../03_standards/frontend_test_patterns.md`](../03_standards/frontend_test_patterns.md):

| Category | Lines | Branches |
|---|---|---|
| Per-module specs | 100% | 100% |
| Shared shells | 80% | 70% |
| Page shells | 70% | 60% |
| Hooks | 90% | 80% |
| Services | 80% (excluding error branches) | 70% |

These are reported by `vitest --coverage`. A drop below target fails CI.

## Surface coverage by module

For each module, surface coverage is the fraction of its claimed tiers that have a working surface file (test-passing). Targets:

| Module class | Surface coverage |
|---|---|
| `generic_runnable` (default) | N/A — no surfaces claimed |
| With capability tiers | 100% |

The round-trip test enforces this directly (FM3 invariant 4 + 5).

## Honest disclosure rate

A "dishonest" routing event is when a shipped backend primitive lands on the orange decode-error card instead of an honest tier-disclosure card.

| Metric | Definition | Target |
|---|---|---|
| **Dishonest-routing rate** | Fraction of tool clicks (from Library, Ask handoff) that hit the decode-error card | 0% for shipped primitives; truly-unknown tools may legitimately decode-error |

This is measured by manual smoke-test review during PR review (no automated tracking). The structural guarantee is that every shipped backend primitive has a matching module with `tiers` declared, so the decode-error card is unreachable except for genuinely unknown tools.

## Render-shell domain blindness

For every shared render shell:

| Metric | Definition | Target |
|---|---|---|
| **Tool-name references in shared code** | `grep -rE "(calculate|get|scan|build|classify)_[a-z0-9_]+_tool" src/components/{shared,ui}/` | 0 matches |
| **Tool-name references in hooks/services/types/lib** | Same grep against these directories | 0 matches (in lib, the registries reference tool names BUT via derived sets, not literal arrays — a stricter check is needed: `grep -E "\\['(calculate|get|scan)_[a-z0-9_]+_tool'" src/lib/` should be 0 after Stage N) |

These are enforced by the `boundaries/no-tool-name-in-shared` ESLint rule (forthcoming).

## Local-storage layout integrity

| Metric | Definition | Target |
|---|---|---|
| **Layout-version mismatch on read** | Stale local-storage layouts referencing deleted widgets are silently filtered (defensive) | 0 user-visible errors |
| **Layout-version bump frequency** | `LAYOUT_VERSION` bumps | Only when layout-state schema is structurally incompatible |

## Build-time integrity

| Metric | Target |
|---|---|
| TypeScript strict pass | 100% |
| No `any` in new code (existing legacy `any` allowed) | enforce via lint |
| Bundle size | informational; investigated if regressions exceed 5% per PR |

## Open questions

1. **Performance metrics.** Should we track first-paint, time-to-interactive, per-surface render time? Today: no — we're not in a performance-bound regime yet. Revisit when complaints surface.
2. **Accessibility metrics.** WCAG conformance is informally honoured (semantic HTML, keyboard nav). Should we enforce via axe-core? Today: aspirational. Revisit when external accessibility audit is on the roadmap.
3. **Per-module THESIS quality.** The round-trip test asserts THESIS exists; it doesn't grade the prose. Manual review catches shallow THESIS in PR. A linter that flags one-line answers is conceivable but feels heavy-handed; defer.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial quality-metrics doc. Module completeness, parity drift, test signals, coverage targets, render-shell domain-blindness checks. |
