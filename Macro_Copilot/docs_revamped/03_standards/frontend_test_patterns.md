# Frontend Test Patterns

> The standard test patterns every frontend contribution follows. Module round-trips, registry derivation, parity checks, surface contracts. Counterpart to backend's [`test_patterns.md`](test_patterns.md).

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing standard. New test categories require an ADR if they introduce new CI gates.
**Operationalises principles:** [FP11](../00_thesis/03_frontend_thesis.md), [FM11](../02_components/frontend_module/README.md), P3 (consistency by contract), P4 (determinism — tests are the determinism floor).
**See also:** [`test_patterns.md`](test_patterns.md) — backend analog; [`../02_components/frontend_module/README.md`](../02_components/frontend_module/README.md) — what the per-module test asserts.

---

## Test categories

**Status today (post-Stage-4e):** All four categories are live. `npm run test:build` runs `scripts/run_build_tests.mjs`; `npm run test:modules` and `npm run test:registries` run `scripts/run_module_tests.mjs` (Stage 2 shipped both runners; Stage 4e fixed an overwrite bug where every `module.spec.ts` bundled to the same temp filename and only the last spec actually ran). Parity is `python tools/check_module_parity.py` (or `make check-module-parity`). The tooling is plain Node + esbuild — no Vitest dependency; the historical `import { describe, it } from 'vitest'` boilerplate examples below describe the aspirational Vitest target, but live module tests use a tiny custom `check()` runner that the bundler resolves at compile time.

The frontend test suite is organised into four categories:

| Category | What it asserts | Files | CI command | Status |
|---|---|---|---|---|
| **Per-module round-trips** | Module's spec ↔ folder ↔ surfaces ↔ THESIS consistency (FM11) | `src/modules/.../__tests__/module.spec.ts` | `npm run test:modules` | **Live (Stage 2+ infrastructure, Stage 4e runner fix)** |
| **Registry derivation** | Central registries are pure derivations of `ALL_PRIMITIVE_MODULES`; no hand-authored entries for migrated primitives | `src/lib/__tests__/loaderPresence.test.ts` + `pageShellBoundary.test.ts` | `npm run test:registries` (alias for `test:modules`) | **Live** |
| **Surface contracts** | Shared shells render correctly for representative inputs; per-tool surfaces honour their Prop contracts | `src/components/.../__tests__/*.test.ts` and `src/lib/__tests__/*.test.ts` | `npm run test:build` (via `scripts/run_build_tests.mjs`) | **Live** |
| **Parity (cross-side)** | Backend + frontend agree on the primitive / workflow set | `python tools/check_module_parity.py` | `make check-module-parity` | **Live (whitelist shrunk to 20 Stage-5+ targets after Stage 4e)** |

The default `npm test` runs `test:build` + `test:modules`. CI runs them in this order plus `make check-module-parity`.

## Category 1 — Per-module round-trip

Every module ships exactly one `__tests__/module.spec.ts` file. The boilerplate:

```ts
import { describe, it } from 'vitest';
import { assertStandardModuleInvariants } from '@/modules/__test-utils';
import { MODULE } from '../module';

describe('module spec', () => {
  it('satisfies the standard module invariants', () => {
    assertStandardModuleInvariants(MODULE, {
      folderName: '<tool_name>',
    });
  });
});
```

The helper `assertStandardModuleInvariants(MODULE, opts)` asserts FM11 invariants 1–8:

1. `MODULE.toolName === opts.folderName`.
2. `MODULE.tiers` is a non-empty subset of the closed `SurfaceTier` union.
3. Exactly one runtime-status tier in `MODULE.tiers`.
4. For each capability tier in `MODULE.tiers`, `surfaces/<Name>.tsx` is importable.
5. For each `surfaces/<Name>.tsx` present, the corresponding capability tier is in `MODULE.tiers`.
6. `THESIS.md` exists (asserted via filesystem read in a test-environment helper).
7. THESIS Question 1 enumerates exactly the tiers in `MODULE.tiers`.
8. If `tiers ∋ workflow_incompatible | paused | deferred`, `MODULE.unsupportedReason` is non-null with all three fields non-empty.

Invariant 9 (loader presence) is asserted by a single top-level test (see Category 2 below).

### Per-module extra assertions

A module MAY add module-specific assertions beyond the standard invariants:

```ts
it('claims event_signal category', () => {
  expect(MODULE.category).toBe('event_signal');
});

it('runs with US country default', () => {
  expect(MODULE.defaultParams?.country).toBe('US');
});
```

These additions are module-author choices; they catch regressions specific to this module's contract with the backend.

### What this test does NOT do

- Render the surfaces (that's a surface-contract test).
- Hit the network (modules are pure specs).
- Mutate global state.

## Category 2 — Registry derivation

Lives in `src/lib/__tests__/`. Asserts:

| File | Asserts |
|---|---|
| `registry_derivation.spec.ts` | Each central registry's contents equal the pure-function derivation from `ALL_PRIMITIVE_MODULES` |
| `no_hand_authored_registries.spec.ts` | After Stage N, no `new Set<string>([` literal arrays exist in `src/lib/` (whitelist-bound during migration) |
| `loader_presence.spec.ts` | Every module folder under `src/modules/{primitives,workflows}` has its `MODULE` imported in `src/modules/index.ts` and included in the appropriate `ALL_*_MODULES` array |
| `tier_consistency.spec.ts` | Across all modules, mutually-exclusive runtime-status invariant holds; tier-capability ↔ surface-file consistency holds |

These run once per test invocation (they're suite-level, not per-module).

## Category 3 — Surface contracts

Surface-contract tests live next to the components they exercise. Patterns:

### Shared-shell tests

```ts
// src/components/shared/render/__tests__/AutoRenderer.spec.tsx
describe('AutoRenderer', () => {
  it('renders Series artifact', () => {
    const result: PrimitiveRunResult = makeSeriesFixture();
    render(<AutoRenderer result={result} toolCard={mockCard} />);
    expect(screen.getByText(...)).toBeInTheDocument();
  });

  it('falls through to FallbackWidget for unknown artifact type', () => {
    // ...
  });
});
```

Each shared shell is tested for:
- Every dispatch branch (per-artifact-type, per-kind).
- Loading state, error state.
- Empty / null result.
- That it does NOT branch on tool name (a smoke test that the same shell renders correctly for two different toolNames with the same result shape).

### Module-surface tests

A module that ships custom surfaces tests them in `src/modules/.../<name>/__tests__/surfaces/<Name>.spec.tsx`:

```ts
describe('MonitorWidget for calculate_cpi_surprise_tool', () => {
  it('renders the latest release inline with the rolling band', () => {
    const data = makeCpiFixture();
    render(<MonitorWidget data={data} />);
    expect(screen.getByText(/surprise/i)).toBeInTheDocument();
  });

  it('handles empty data', () => { /* ... */ });
});
```

These are at the module's discretion; the round-trip test only asserts the file exists.

### Page-shell tests

Page shells are tested for routing + canvas-mode transitions, not for primitive-specific behaviour:

```ts
describe('BuildShell', () => {
  it('mounts BuilderCanvas when ?builder=<toolName>', () => {
    renderWithRouter(<BuildShell />, { initialEntries: ['/workspace?builder=calculate_pca_yield_curve_tool'] });
    expect(screen.getByText(/PCA/i)).toBeInTheDocument();
  });

  it('mounts WorkflowStatusCanvas when ?workflow=<id>', () => { /* ... */ });

  it('mounts UnsupportedKnownToolCanvas for paused module', () => { /* ... */ });
});
```

Page-shell tests do NOT exercise specific module surfaces deeply; that's the module test's job. Page-shell tests assert the *routing* — which canvas gets mounted for which URL state.

## Category 4 — Parity (cross-side)

A standalone Python script (`tools/check_module_parity.py`) asserts backend + frontend agree on the primitive / workflow set:

```python
# Pseudo-code
backend_tools = read_primitive_specs() | read_workflow_incompatible_tools()
frontend_modules = read_module_loader()
deferred = read_deferred_reservations()

assert backend_tools | deferred == frontend_modules, (
    "Set drift detected:\n"
    f"  backend - frontend = {backend_tools - frontend_modules}\n"
    f"  frontend - backend - deferred = {frontend_modules - backend_tools - deferred}\n"
)
```

The script runs in CI and is invoked via `make check-module-parity`. It is the structural guarantee against the registry-drift failure mode that produced the current 18-tool gap.

## Mocking and fixtures (target-state)

The mocking infrastructure below is **target-state Stage 2+**. Today the existing `npm run test:build` suite uses bespoke fixtures inside individual `__tests__` directories without a shared `src/__test-setup/` layer. The Stage 2 reference module (`calculate_cpi_surprise_tool`) introduces the shared infrastructure.

| Need | How (target-state) |
|---|---|
| Mock backend API | MSW (Mock Service Worker) at the network boundary; configured in `src/__test-setup/msw-handlers.ts` |
| Fixture artifact data | Per-shape factories in `src/__test-setup/fixtures/{series,panel,...}.ts` |
| Mock WebSocket | A test-only `MockCopilotProvider` in `src/__test-setup/MockCopilotProvider.tsx` |
| Render with router | `renderWithRouter` helper in `src/__test-setup/render.tsx` |

Test setup files live in `src/__test-setup/` (target-state). They are imported only from test files; production code MUST NOT import from this directory.

## Determinism

Tests MUST be deterministic. Specifically:

- No `new Date()` without a clock mock.
- No `Math.random()` without a seed.
- No network calls (MSW intercepts everything, target-state once MSW lands).
- No reliance on test execution order.

Flaky tests are CI failures; they don't get retry-merged.

## Snapshot policy

Snapshot tests are allowed but bounded:

- Module round-trip helper uses `expect(MODULE).toMatchSnapshot()` for spec freeze (catches accidental drift in the spec shape).
- Visual snapshots (rendered HTML) are NOT used — they're brittle and rarely informative.
- Registry-count assertions (`expect(RUNNABLE_PRIMITIVE_TOOLS.size).toBe(N)`) are tripwires intentionally updated when modules are added/removed.

When a snapshot needs updating, the PR description names the reason (a new module shipped, a tier was added, etc.). Routine snapshot updates without explanation fail review.

## Coverage targets (target-state)

**Status today:** no coverage gate is enforced. Adding `vitest --coverage` and the targets below is target-state Stage 2+, paired with the introduction of `src/modules/` and the per-module round-trip tests.

Per-category targets (enforced by `vitest --coverage`, target-state):

| Category | Lines | Branches |
|---|---|---|
| Per-module specs | 100% | 100% |
| Shared shells | 80% | 70% |
| Page shells | 70% | 60% |
| Hooks | 90% | 80% |
| Services | 80% (excluding error branches) | 70% |

Coverage drops below target fail CI. The targets are coordinated with [`../04_quality_and_evals/frontend_quality_metrics.md`](../04_quality_and_evals/frontend_quality_metrics.md).

## Test naming

| Test name | Says |
|---|---|
| `'satisfies the standard module invariants'` | The module passes the FM11 round-trip |
| `'renders <result-shape> artifact'` | A shared shell handles that artifact type |
| `'mounts <canvas> when <url-state>'` | A page-shell routing test |
| `'is included in ALL_PRIMITIVE_MODULES'` | A loader-presence test |
| `'derives from modules where tiers ∋ <tier>'` | A registry-derivation test |

Tests describe *behaviour*, not *implementation*. Avoid `'calls fetch with x'` style; prefer `'shows error banner on network failure'`.

## Open questions

1. **Visual regression tests.** Today not used. Should they be added for the shared shells where layout drift is invisible to existing tests? Today: deferred; revisit when layout regressions become a recurring failure.
2. **E2E test scope.** Today Playwright is used sparingly for cross-surface flow tests. Should the scope expand to every Build canvas mode? Today: no — unit + contract tests are sufficient; E2E is reserved for genuinely cross-surface verification.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial frontend test patterns. 4 categories, per-module round-trip helper, parity script, mocking conventions, coverage targets. |
