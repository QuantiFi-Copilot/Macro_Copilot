# Test Patterns

> Every component ships a test pattern. The pattern's **shape** differs by component (linked out); the **discipline** is universal — determinism, real validators not mocks, parity tests where structurally possible, and tests as first-class citizens of the contract.

**Version:** v1
**Last reviewed:** 2026-05-18

## 1. Universal Rule

| Rule | What it means |
|---|---|
| Every component ships a test pattern. | The pattern is documented in the component's runbook; admission depends on it. |
| Tests live at `tests/test_<thing>.py`. | Flat; not co-located with the component. (See [`naming_conventions.md`](naming_conventions.md).) |
| Tests are deterministic. | Fixed random seeds (`np.random.RandomState(42)`); frozen dates (`_FROZEN_TODAY`, `_FrozenDate.today()` monkeypatches); no `datetime.now()` in test code. |
| Tests exercise the real validators. | No mocking of Pydantic models. Construct real artifacts, call real bind/validate paths. |
| Parity tests where structurally possible. | SQL parity for primitives (PR16 triplet); artifact-store round-trip for artifacts (ART13); instrument-agnostic for templates (WT15). |
| Coverage tests assert the real failure mode. | A test that asserts a specific exception's message catches future code paths that "kind of" raise; a test that asserts only `pytest.raises(Exception)` does not. |
| Synthetic data fixtures are reusable. | Cross-component test helpers live in `tests/conftest.py` or `tests/_workflow_synthetic_fetchers.py`; per-tool fixtures live next to the test file. |
| No test imports another test. | Tests are independent. Shared setup goes in fixtures, not in imports. |

## 2. Why This Exists

- **Tests are part of the contract.** Every component's principle list ends with a test-pattern principle (PR16, OPR16, ART13, WT15) because a contract without tests is not enforceable. The standards-level rule is that this is universal, not optional, not "do as much as you can".
- **Determinism is replayability.** [P4](../00_thesis/01_non_negotiables.md) requires that a workflow re-executed six months later produces byte-identical results. Tests that depend on wall-clock or unseeded randomness break the same way a workflow does.
- **Real validators catch real bugs.** Mocking Pydantic past the construction validator means the test passes for inputs the production substrate would reject. The cost of mocking is paid in production bugs that "should have been caught".
- **Parity tests are the integrity check.** A primitive whose compute layer matches an independent SQL implementation is much harder to silently break than one whose tests only confirm self-consistency.

## 3. Applies To

Every component layer (primitive, operator, artifact, workflow template, playbook), every loader, every bridge, every API surface. Substrate changes (validator, executor, registry) also follow these rules — substrate tests live in `tests/test_workflow_substrate.py` and `tests/test_workflow_template_system.py`.

## 4. Component Manifestations

| Component | Test pattern | Where it lives | Parity layer |
|---|---|---|---|
| Primitive | Three-file triplet ([PR16](../02_components/primitive/README.md)): `test_<tool>_compute.py` + `test_<tool>_wiring.py` + `test_<tool>_sql_validation.py` | `tests/` (flat) | SQL parity test (independent SQL implementation matches compute output) |
| Operator | Unit test + workflow-integration test ([OPR16](../02_components/operator/README.md)) | `tests/test_<operator>.py` | No SQL parity (operators don't talk to DB); integration test is the parity layer |
| Artifact | Four layers ([ART13](../02_components/artifact/README.md)): artifact-store round-trip + validator (positive + negative per invariant) + lineage propagation + immutability | `tests/test_artifacts.py` (or `tests/test_artifact_<type>.py` for large surfaces) | Artifact-store round-trip (real `put_artifact` / `get_artifact` exercises the persistence codec) |
| Workflow template | Five layers ([WT15](../02_components/workflow_template/README.md)): structural validity + slot-binding rejection + real-data E2E + mandatory instrument-agnostic E2E + topology-archetype-fit gate | `tests/test_workflow_<template>.py` | Instrument-agnostic E2E proves the operator substrate's asset-class-blindness |
| Playbook | Per-playbook contract | `tests/` | Source-of-record parity (see [P12](../00_thesis/01_non_negotiables.md)) |

## 5. Anti-Patterns

- **`datetime.now()` in test code.** Use `_FROZEN_TODAY` / `_FrozenDate.today()` monkeypatching. Tests must be deterministic across days.
- **Unseeded `random` / `np.random`.** Use `RandomState(<fixed_seed>)` so failures reproduce.
- **Mocking a Pydantic model to "skip" validation.** Construct the real model; pass real inputs. If validation is in the way, the test target probably has the wrong shape.
- **Tests inside the component folder** (`shared/operators/align_series/test_align_series.py`). Move to `tests/test_align_series.py`.
- **Happy-path-only coverage for a validator.** Every `@model_validator` invariant gets both a positive test (asserts well-formed input passes) and a negative test (asserts malformed input raises with the expected message).
- **`pytest.raises(Exception)`** (or `ValueError` without `match=`). Use the specific class + a message-fragment match so the test breaks if the exception's *meaning* changes.
- **A primitive with no SQL parity test** when SQL parity is structurally possible. Statistical-fit primitives (PCA, regression) are the documented exception; others are catalog debt (per [PR16](../02_components/primitive/README.md)).
- **A workflow template without the instrument-agnostic test.** The WT3 asset-class-blindness claim is then unverified.
- **Tests that depend on test execution order.** Use fixtures; do not rely on module-level mutable state set by an earlier test.
- **A test that imports another test file.** Shared setup goes in `tests/conftest.py` (or a `_` -prefixed helper module like `tests/_workflow_synthetic_fetchers.py`), not in cross-test imports.
- **A mock that replaces the whole substrate** ("`patch.object(execute_workflow, ...)`" instead of patching the DB fetcher). Patch the narrowest boundary; let the real substrate run.

## 6. Reviewer Checks

- [ ] Tests live at `tests/test_<thing>.py`.
- [ ] Determinism: fixed seeds, frozen dates, no wall-clock dependencies.
- [ ] Real validators run (no Pydantic mocking).
- [ ] Each component-specific test pattern (per the runbook) is satisfied — if PR adds a primitive, the triplet exists; if an operator, the unit + integration tests exist; if an artifact, the four layers; if a template, the five layers.
- [ ] Negative tests use specific exception class + `match="<fragment>"`.
- [ ] Parity test exists where structurally possible (and the absence is documented as catalog debt where it isn't).
- [ ] Synthetic fixtures live in `conftest.py` or a `_`-prefixed helper, not inline-duplicated across test files.

## 7. Links

- [P3 (consistency by contract)](../00_thesis/01_non_negotiables.md), [P4 (determinism)](../00_thesis/01_non_negotiables.md), [P12 (everything else is wrong)](../00_thesis/01_non_negotiables.md)
- [`naming_conventions.md`](naming_conventions.md) — test file naming
- Component manifestations: [PR16](../02_components/primitive/README.md), [OPR16](../02_components/operator/README.md), [ART13](../02_components/artifact/README.md), [WT15](../02_components/workflow_template/README.md)
