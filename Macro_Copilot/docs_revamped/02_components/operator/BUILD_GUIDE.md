# Operator BUILD_GUIDE

> The single front-door manual for building a new operator end-to-end. The contract ([`README.md`](README.md), OPR1–OPR16) is the *why*; the runbook ([`runbook.md`](runbook.md)) is the *procedure*; this guide is the **worked example** — it walks the real, shipped `correlation` operator (the v2.0 reference) file-by-file so a new operator is a copy-paste-and-adapt away. Build one perfect example, then multiply.

**Version:** v1.0
**Last reviewed:** 2026-05-30
**Status:** load-bearing build manual. Tracks the contract; changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Reference build:** [`correlation`](../../../shared/operators/correlation/) — admitted via [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md); gated by the OPR16 meta-test.

---

## Orientation

An operator is a finance-blind, pure function over typed artifacts, living in a **four-file folder** at `shared/operators/<name>/`:

```
shared/operators/correlation/
  __init__.py     # 4 exports: CONFIG_PATH, correlation, CorrelationParams, CorrelationError
  config.yaml     # operator meta + defaults (the de-opinionation layer) + methodology
  schemas.py      # CorrelationParams (every consequential variant, typed)
  operator.py     # def correlation(left, right, *, params=None, config=None) -> ScalarMetric
```

The build is six stages. **The meta-test is the gate at every stage** — when it is green for your operator, the operator conforms.

| Stage | Produces | Worked example |
|---|---|---|
| 0 Admission | the PR-description argument (7 pre-flight questions) | "correlation is a distinct finance-blind statistical method the toolbox lacks" |
| 1 Backend | the four files | `shared/operators/correlation/*` |
| 2 Artifact | any new closed-family type the operator emits (ART16) | `ScalarMetric` (admitted across 8 sites) |
| 3 Register | the `OPERATOR_REGISTRY` entry + (re)exports | `"correlation": OperatorSpec(...)` |
| 4 Tests | per-operator tests + add to the meta-test's `_V2_CONFORMANT` | `tests/test_operator_correlation.py` |
| 5 Verify + close | green meta-test, finance-blind/determinism sign-off, `graphify update` | `pytest` + the PR checklist |

---

## Stage 0 — Admission (no code)

Answer the seven pre-flight questions from [`runbook.md`](runbook.md#pre-flight--seven-decisions-before-any-code) in the PR description, citing by OPR-number. For `correlation`:

1. **Finance-blind (OPR6):** "the correlation between two index-aligned numeric series" — makes sense on temperature data → operator, not primitive. ✓
2. **Method family (OPR1):** `statistical_relationship`. ✓
3. **Toolbox admission (OPR4):** no existing operator computes correlation; `rolling_regression` is a different statistic. No ≥3-archetype gate (removed in v2.0). ✓
4. **Novelty (OPR5):** genuinely new statistical method. ✓
5. **Typed I/O (OPR9):** two `Series` → one `ScalarMetric` (a new closed-family type — triggers Stage 2). ✓
6. **Parameter surface (OPR7/8):** `method ∈ {pearson, spearman, kendall}` (kendall declared-but-unbuilt → honest refusal); `min_periods`. ✓
7. **Metadata algebra (OPR11):** strict frequency + missingness; **unit-invariant** (a correlation is dimensionless — see Stage 1). ✓

## Stage 1 — The four files

Clone `shared/operators/correlation/` and adapt. The non-negotiable shapes:

**`config.yaml`** — the configurability / de-opinionation layer (OPR7). Every method default lives here with `{value, source, rationale, valid_values?}`; the `methodology.planned_extensions` block names declared-but-unbuilt variants (the honest-refusal targets). `operator.version` is semver and **must equal** `operator.py::_OPERATOR_VERSION` (OPR12).

**`schemas.py`** — `<Operator>Params(BaseModel)` with `frozen=True, extra="forbid"`; every consequential variant is a typed field (`Literal[...]` for enums, `Field(ge=…)` for bounded numerics). Schema defaults **mirror** the YAML (a Stage-4 test pins they don't diverge — OPR8).

**`operator.py`** — the canonical signature and body order (copy this skeleton):

```python
def correlation(left, right, *, params=None, config=None) -> ScalarMetric:
    # 1. config + identity (name AND version) — OPR12
    if config is None: config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)
    # 2. resolve params from config when omitted — OPR8
    if params is None: params = CorrelationParams(method=config.default_value("method"), ...)
    # 3. honest refusal for a declared-but-unbuilt variant — OPR8
    if params.method not in _IMPLEMENTED_METHODS: raise NotImplementedError(...)
    # 4. typed-input + structural-metadata checks — OPR9 / OPR11
    #    (raise <Operator>Error; refuse cross-unit ONLY where units must cohere)
    # 5. the pure transform — no I/O; never let +-Inf into the payload — OPR14
    # 6. lineage: ONE OperatorStep via .build; aux chains; sanitised params — OPR10
    step = OperatorStep.build(name=_OPERATOR_NAME, version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(left.lineage.head_hash, right.lineage.head_hash),
        auxiliary_lineages=(right.lineage,))
    return ScalarMetric(..., lineage=left.lineage.append(step))
```

Always `params: Optional[...] = None` (OPR8). `<Operator>Error(ValueError)` (OPR13). Module constants `_OPERATOR_NAME`, `_OPERATOR_VERSION`, `_CONFIG_PATH`.

**`__init__.py`** — the four OPR16 exports: `CONFIG_PATH`, the operator, the `*Params`, the `*Error`.

**A note on the metadata algebra (OPR11), using correlation as the lesson.** OPR11 says operators *refuse* cross-unit operations — but that rule applies to operations where units must **cohere** (arithmetic: BPS + PERCENT is meaningless). `correlation` is **unit-invariant**: a correlation coefficient is dimensionless and scale-invariant, so correlating a BPS series with a PERCENT series is valid. So `correlation` imposes **no** same-unit requirement, records both inputs' units in lineage for provenance, and declares its output unit as `RATIO`. The general lesson: each operator declares its **output-unit propagation rule**; "refuse cross-unit" is the rule for unit-coherent ops, "unit-invariant + declared output unit" is the rule for statistics like correlation/rank.

## Stage 2 — Artifact (only if you emit a new closed-family type)

If your operator emits a type not yet in the closed family, admit it via the ART16 procedure — **all eight sites in one PR** (this is what `ScalarMetric` did, [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md)):

1. the Pydantic class — `shared/artifacts/types.py` (`ScalarMetric`: frozen, `extra="forbid"`, finite-value validator, mandatory `lineage`).
2. `ARTIFACT_TYPE_NAMES` — `shared/workflow/registry.py`.
3. `artifact_type_name()` type-map — same file.
4. `ArtifactTypeLiteral` discriminator — `state/schemas.py`.
5. the artifact-store codec — `state/artifact_store.py` (`_ARTIFACT_CLASSES`, the `Artifact` union, the `_artifact_to_stored`/`_stored_to_artifact` dispatch, and a `_<type>_to_stored`/`_<type>_from_stored` pair).
6. `TerminalArtifact` union — `shared/workflow/result.py`.
7. the package export — `shared/artifacts/__init__.py`.
8. tests — round-trip + validator (Stage 4).

Most operators reuse an existing type and **skip this stage entirely.**

## Stage 3 — Register

Add the `OperatorSpec` to `OPERATOR_REGISTRY` (`shared/workflow/registry.py`):

```python
"correlation": OperatorSpec(
    operator_name="correlation", callable=correlation,
    params_class=CorrelationParams,
    input_slots={"left": "Series", "right": "Series"},
    output_type="ScalarMetric",
),
```

> **Slot declaration today vs. the SlotDescriptor ABI.** The contract (OPR9/OPR15) calls for a structured `SlotDescriptor`; the live registry still uses bare-string slot types. Building `correlation` against the current bare-string ABI keeps it runnable through the existing executor **and consistent with the other operators** — converting one operator to `SlotDescriptor` while the rest use strings would make the registry inconsistent. The `SlotDescriptor` upgrade is its own wholesale ABI step ([ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md) consequence step 4) that converts every operator at once. Until then, declare bare-string slots exactly like the reference.

## Stage 4 — Tests

Two files (copy `tests/test_operator_correlation.py` as the template):

- **`tests/test_operator_<name>.py`** — happy path; every implemented variant; the honest-refusal case (unbuilt variant raises `NotImplementedError`); every structural-metadata mismatch raises `<Operator>Error`; the schema-default-mirrors-YAML assertion (OPR8); lineage extends N→N+1 with one step + `auxiliary_lineages`; rerun determinism (stable `head_hash`); a **non-rates** finance-blind case (OPR6); and any artifact-validator negative test.
- **the gate** — add your operator to `_V2_CONFORMANT` in `tests/test_operator_registry_consistency.py`. That single edit subjects it to the full OPR16 conformance check (signature == slots ∪ {params, config}; `params` default None; `<Op>Error ⊂ ValueError`; types in the closed family; the four `__init__` exports; config name+version identity).

## Stage 5 — Verify + close

```bash
python -m pytest tests/test_operator_<name>.py tests/test_operator_registry_consistency.py -q
graphify update .   # refresh the knowledge graph after code lands
```

The PR checklist in [`runbook.md`](runbook.md#pr-review-checklist) signs off OPR1–OPR16. **The meta-test being green is the verification** — conformance is proven mechanically, not by hand-review.

---

## What you do NOT build for an operator

- **No frontend module.** Operators have no per-operator UI — they render as DAG nodes via shared infrastructure, and their output artifact renders via the per-artifact-type renderer ([`../frontend_module/README.md`](../frontend_module/README.md): "operators do not get modules"). The only frontend work an operator can trigger is a renderer for a *new artifact type* (Stage 2), which is artifact-level, not operator-level.
- **No DB / curated-narrative migration.** Operators are finance-blind; there is no desk narrative to curate (that is a primitive concern).
- **No SQL parity test.** Operators have no DB path; the workflow-integration test is the parity layer.

## Worked-example index

| Contract clause | Where `correlation` demonstrates it |
|---|---|
| OPR1/OPR6 finance-blind statistical method | `operator.py` (zero finance vocabulary; non-rates test passes) |
| OPR2 constant output type | always `ScalarMetric` (rolling correlation is a *separate* future operator) |
| OPR7/OPR8 config-offload + honest refusal | `config.yaml` defaults + `kendall` → `NotImplementedError` |
| OPR9 typed I/O | two `Series` → one `ScalarMetric` |
| OPR10 lineage | `OperatorStep.build` + `auxiliary_lineages=(right.lineage,)` + `sanitize_params_for_lineage` |
| OPR11 metadata algebra | strict frequency/missingness; unit-invariant, output `RATIO` |
| OPR12 config identity | `_check_config_identity(config, name, version)` |
| OPR13 one error family | `CorrelationError(ValueError)`; degenerate inputs raise it, never a raw library error |
| OPR14 determinism | rerun yields identical `head_hash`; zero-variance → typed refusal, never `NaN` |
| OPR16 the gate | `tests/test_operator_registry_consistency.py` |

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.0 | 2026-05-30 | Initial operator BUILD_GUIDE — the worked-example front-door, grounded in the shipped `correlation` reference operator (the v2.0 template) and the `ScalarMetric` artifact admission. Six build stages; the OPR16 meta-test as the gate. | [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md) |
