# Hash Determinism

> Every produced artifact has a content-addressed identity via its lineage `head_hash`. The recipe over what goes into the hash is **closed and stable**; changing it breaks every persisted artifact's identity.

**Version:** v1
**Last reviewed:** 2026-05-18

## 1. Universal Rule

The lineage `head_hash` is the hash of the last `LineageStep` in the chain. Each step's hash is computed by `_compute_step_hash(kind, name, version, params, input_hashes)` in [`shared/artifacts/lineage.py`](../../shared/artifacts/lineage.py). That recipe is **the** contract.

| In the hash | Not in the hash |
|---|---|
| `kind` (`"primitive"` / `"operator"` / `"adapter"` / `"fetch"`) | `tool_config_path` (file-system path; varies across deploys) |
| `name` (tool / operator name) | `methodology_version_id` (DB auto-increment id; varies across DB restores) |
| `version` (semantic version string) | Wall-clock time of the call |
| `params` (the call's full argument dict) | Anything in `state/` or the artifact-store row |
| `input_hashes` (tuple of upstream step hashes) | The Python object's `id(...)` |
| Per-step constructor-internal fields **only** when folded into `params` by `<Step>.build()` | Anything not listed in the left column |

Two artifacts produce the same `head_hash` iff their full lineage chains are identical under the recipe. The recipe is stable across processes, machines, and (load-bearing for replay) across software versions. Any change to the recipe is an ADR-gated change.

## 2. Producers fold content-defining choices into `params`

A producer (primitive, operator, adapter) makes some choices that affect the output content (window size, regression-intercept flag, fill policy, units selection). Per [ART10](../02_components/artifact/README.md), **every such choice must appear in the step's `params` dict** so the hash captures it. Recording a methodology choice only on the artifact's structural metadata (not in lineage params) creates an *identity hazard*: two artifacts produced from different choices can collide on `head_hash`.

The pattern (from `PrimitiveStep.build` in [`shared/artifacts/lineage.py`](../../shared/artifacts/lineage.py)):

```python
@classmethod
def build(cls, *, name, version, params, tool_config_hash, output_field, as_of_date, ...):
    # Fold content-defining bits into a derived dict that goes into
    # _compute_step_hash. tool_config_path and methodology_version_id
    # are NOT folded — they are bookkeeping-only.
    hashed_params = {
        "input_params": params,
        "tool_config_hash": tool_config_hash,
        "output_field": output_field,
        "as_of_date": as_of_date,
    }
    h = _compute_step_hash(kind="primitive", name=name, version=version,
                           params=hashed_params, input_hashes=input_hashes)
    return cls(..., hash=h, ...)
```

`OperatorStep.build`, `AdapterStep.build`, `FetchStep.build` follow the same pattern: the `build()` classmethod is the only legal construction path because it controls what enters the hash.

## 3. Why This Exists

- **[P4](../00_thesis/01_non_negotiables.md) (determinism + replayability).** A workflow re-executed six months later must produce byte-identical results — verifiable by comparing `head_hash`. Without a stable recipe, the comparison fails for trivial reasons (a new DB auto-increment id, a different deploy path) and the replay claim collapses.
- **Hash-not-of-bytes.** Floating-point representations of the same logical value differ across machines + NumPy versions. Hashing the *recipe* (the lineage chain) bypasses that. Two artifacts that are byte-different but recipe-identical have the same `head_hash`.
- **Bookkeeping fields are excluded** so the hash survives non-substantive variations: a moved file path, a re-keyed methodology row, a cosmetic refactor.

## 4. Component Manifestations

| Component | Step type | Where `build()` lives |
|---|---|---|
| Primitive | `PrimitiveStep` | [`shared/artifacts/lineage.py`](../../shared/artifacts/lineage.py) |
| Operator | `OperatorStep` | [`shared/artifacts/lineage.py`](../../shared/artifacts/lineage.py); appended via `Lineage.append(...)` per [OPR10](../02_components/operator/README.md) |
| Bridge / adapter | `AdapterStep` | [`shared/artifacts/lineage.py`](../../shared/artifacts/lineage.py); used by `raw_dataframe_to_artifact_series` for synthetic / fixture data |
| L1 read | `FetchStep` | [`shared/artifacts/lineage.py`](../../shared/artifacts/lineage.py); origin of every real-data chain |
| Artifact identity | `Lineage.head_hash` derives from the last step's `hash` | [`shared/artifacts/lineage.py`](../../shared/artifacts/lineage.py); the artifact-store uses this directly per [ART10](../02_components/artifact/README.md) |
| Cross-deploy stability test | `tests/state/test_hash_stability.py` | Pinned hashes per step kind; any recipe change must keep this test green |

## 5. Anti-Patterns

- **Constructing a step directly** (`PrimitiveStep(name=..., hash=...)`) instead of via `<Step>.build()`. The hash is then whatever the caller provided; the substrate's recipe is bypassed.
- **`lineage.extend(...)`** when the API is `lineage.append(step)`. `append` returns a new `Lineage` with the step's hash propagated to `head_hash`; `extend` is not a substrate method.
- **A producer recording a methodology choice on the artifact's metadata but not in `params`.** Two different choices then produce the same hash; identity is no longer content-addressed.
- **Folding `methodology_version_id` or `tool_config_path` into the hash recipe.** Both are bookkeeping; both vary across deploys. Including them breaks replay across DB restores or file moves.
- **Hashing the payload bytes** as the artifact's identity. Floats vary across NumPy versions; identity is over the recipe.
- **Mutating an artifact after construction.** Even though `frozen=True` blocks reassignment of wrapper fields, in-place pandas mutation of `payload` breaks the recipe's promise without changing `head_hash`. Treat artifacts as immutable in caller code (per [ART7](../02_components/artifact/README.md)).
- **Changing the `_compute_step_hash` recipe** without an ADR + a migration plan for every persisted artifact. The recipe is itself a closed-family contract.

## 6. Reviewer Checks

- [ ] Every new step type has a `build()` classmethod that funnels through `_compute_step_hash`.
- [ ] The `build()` classmethod folds every content-defining choice into the `params` dict it passes to the hash.
- [ ] Bookkeeping-only fields (paths, registry IDs) are documented as NOT in the hash, both in the docstring and in the recipe call.
- [ ] No code constructs a step directly with an explicit `hash=` argument outside `build()`.
- [ ] `tests/state/test_hash_stability.py` passes — pinned cross-version hashes still match.
- [ ] If the PR changes the recipe (rare), an ADR exists, the test's pinned hashes are regenerated, and the persisted-artifact migration is documented.

## 7. Links

- [P4 (determinism + replayability)](../00_thesis/01_non_negotiables.md)
- [`typed_boundary_discipline.md`](typed_boundary_discipline.md) — `frozen=True` is what makes the hash promise hold
- [`methodology_disclosure.md`](methodology_disclosure.md) — the `params` dict is where per-call methodology lives
- Component manifestations: [ART10](../02_components/artifact/README.md) (content-addressed identity), [OPR10](../02_components/operator/README.md) (lineage extension via `OperatorStep.build` + `Lineage.append`)
