# Typed Boundary Discipline

> Every typed boundary in the platform uses frozen Pydantic with `extra="forbid"`. No loose dicts, tuples, or `**kwargs` carry contracted data between layers. Pydantic is today's mechanism; the rule is *typed boundaries*, not *Pydantic*.

**Version:** v1
**Last reviewed:** 2026-05-18

## 1. Universal Rule

Every contracted boundary — primitive `*Input`/`*Output`, operator `*Params`, artifact wrapper, lineage step, template `*Declaration`, config schema, API request/response, persistence row — is a `pydantic.BaseModel` with:

```python
model_config = ConfigDict(
    frozen=True,                  # immutable; new state = new object
    extra="forbid",               # unknown fields rejected at construction
    arbitrary_types_allowed=True, # only when payload includes pandas / numpy
                                  # (omit otherwise; default is False)
)
```

Cross-field invariants are enforced by `@model_validator(mode="after")` blocks that raise `ValueError` with a specific named message. Field-level validation goes in the field type or `Field(..., constraint=...)`.

## 2. Why This Exists

- **Construction-time rejection of malformed input.** `extra="forbid"` catches typo'd field names; `@model_validator` catches structural invariants. Without these, a malformed call propagates as a confusing error three layers deeper.
- **Immutable contracts are content-addressable.** `frozen=True` is what lets the lineage layer hash a step's params reliably (per [`hash_determinism.md`](hash_determinism.md)). Mutable models would change identity under the substrate's hand.
- **No loose dict contracts** means a reader of an operator's signature knows exactly what artifact types flow in and out. A `Dict[str, Any]` argument hides intent and disables IDE/type-checker help.
- **The substrate's static guarantee** — that an operator declaring `input_slots={"series_list": "List[Series]"}` actually receives a list of `Series` artifacts — depends on every boundary being typed.

## 3. Applies To

Every component's typed boundaries:

- Primitive `<Tool>Input` / `<Tool>Output` schemas in `<agent>/<domain>/tools/<tool>/schemas.py`
- Operator `<Operator>Params` schemas in `shared/operators/<operator>/schemas.py`
- Operator + primitive `config.yaml`-loaded objects (`OperatorConfig`, `ToolConfig`)
- Artifact wrapper classes in `shared/artifacts/types.py`, `shared/artifacts/trades.py`
- Lineage step classes (`PrimitiveStep`, `OperatorStep`, `AdapterStep`, `FetchStep`) in `shared/artifacts/lineage.py`
- Workflow `WorkflowTemplate`, `SlotDeclaration`, `*NodeTemplate`, `WorkflowEdge`, etc.
- DB row schemas in `state/schemas.py`
- API request / response models in `api/routes/*/`

The rule applies even when the payload is a `pd.Series` / `pd.DataFrame` / `np.ndarray` — set `arbitrary_types_allowed=True` and write structural validation in `@model_validator(mode="after")` (Pydantic does not validate pandas / numpy by itself).

## 4. Component Manifestations

| Component | Principle | Highlight |
|---|---|---|
| Primitive | [PR8](../02_components/primitive/README.md) | `*Input` and `*Output` are frozen + extra-forbid; cohesive surface |
| Operator | [OPR8](../02_components/operator/README.md) | `*Params` is frozen + extra-forbid; consequential variants typed as `Literal[...]` |
| Artifact | [ART7](../02_components/artifact/README.md) | Frozen + extra-forbid + arbitrary_types_allowed; `@model_validator` for shape invariants |
| Lineage step | implicit in [ART10](../02_components/artifact/README.md) | Frozen + extra-forbid; `_compute_step_hash` recipe relies on immutability |
| Workflow template | [WT9](../02_components/workflow_template/README.md) | `SlotDeclaration.type` Literal of 6 closed values; topology nodes frozen |

## 5. Anti-Patterns

- **`extra="allow"`** or no `extra` setting (default is `"ignore"`, which silently drops typos). Wrong everywhere.
- **`frozen=False`** because "we want to mutate the artifact's units after construction". Build a new artifact; do not mutate.
- **`arbitrary_types_allowed=True`** on a model that has no pandas / numpy payload. Adds attack surface for no reason — omit.
- **A loose dict at a boundary.** `def operator(inputs: Dict[str, Any]) -> Dict[str, Any]` is a typed-boundary violation. Use the operator's `*Params` model and a closed-family artifact union (per [ART15](../02_components/artifact/README.md)).
- **Field-level validation duplicated in caller code.** If the model's validator catches it, the caller doesn't need a second copy.
- **A `@model_validator(mode="before")`** to coerce input shapes silently. Coercion at the boundary is a kind of silent fallback (see [`error_handling.md`](error_handling.md)). Reject; do not coerce.
- **`Optional[...]` with a `None` default for a field that should be mandatory.** If the model accepts `None`, downstream code branches on it; the field is not mandatory in practice. Either make it `Optional` and document the meaning of `None`, or make it required.
- **A new typed model that wraps an existing model "to add a field".** Either add the field to the original (with an ADR if it's a closed family) or compose via a structural relation; do not shadow.

## 6. Reviewer Checks

- [ ] Every new `BaseModel` has `model_config = ConfigDict(frozen=True, extra="forbid", ...)`.
- [ ] `arbitrary_types_allowed=True` appears only when the model carries pandas / numpy.
- [ ] Cross-field invariants live in `@model_validator(mode="after")`, not in caller code.
- [ ] Validator error messages name the specific invariant (per [`error_handling.md`](error_handling.md)).
- [ ] No public function returns a `Dict[str, Any]` instead of a typed model (primitive `compute.py` returning a dict is the documented exception — the bridge lifts it to a typed artifact).
- [ ] No `Optional` fields with `None` defaults where the field must be set in practice.

## 7. Links

- [P3 (consistency by contract)](../00_thesis/01_non_negotiables.md), [P4 (determinism)](../00_thesis/01_non_negotiables.md), [P6 (no silent failure)](../00_thesis/01_non_negotiables.md)
- [`error_handling.md`](error_handling.md) — what validators raise
- [`hash_determinism.md`](hash_determinism.md) — why `frozen=True` is what makes lineage hashable
- Component manifestations: [PR8](../02_components/primitive/README.md), [OPR8](../02_components/operator/README.md), [ART7](../02_components/artifact/README.md), [ART11](../02_components/artifact/README.md), [WT9](../02_components/workflow_template/README.md)
