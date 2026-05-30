# Runbook — How to add a new operator

> Step-by-step procedure for adding a new operator. The bar is high: operators are the small, **finance-blind** structural/statistical catalogue the workflow-composition layer is built on. Every operator follows the **same** contract; the gate that proves it does is the registry-consistency meta-test (OPR16).

**Version:** v2.0
**Last reviewed:** 2026-05-30
**Audience:** any contributor (human or AI agent) introducing a new operator.
**Prerequisite reading:** [`README.md`](README.md) — the operator contract (OPR1–OPR16 v2.0). [`../artifact/README.md`](../artifact/README.md) — the co-equal artifact contract (the types you consume/emit). [`../../05_decisions/0016-operator-and-artifact-standardization-v2.md`](../../05_decisions/0016-operator-and-artifact-standardization-v2.md) — why the v2.0 rules are what they are. Read the contract once before starting; refer back when a step says *"per OPR\<N\>."*
**Operationalises principles:** P1, P3, P4, P6, P8, P9 (the defining constraint), P10, and OPR1–OPR16 throughout.

---

## When to use this runbook

- **Introducing a new finance-blind structural/statistical method** the toolbox does not yet have (e.g. `correlation`, `covariance`, `cointegration`, `rolling_zscore`, `cross_sectional_rank`, `convert_units`).
- **Promoting a workflow-local helper** into a shared operator once it is a genuinely distinct, reusable method (no recipe-count gate — see OPR4).
- **Splitting an over-broad operator** that accreted two unrelated method families (OPR2).

## When NOT to use this runbook

- **Adding a new method variant inside an existing operator's family** (a new `aggregator` value, a new `correlation.method`). That is an in-place edit: a new `Literal` value + a YAML default + a code branch + a test. Much smaller than a new operator.
- **Building a finance-aware computation** (P&L, Sharpe, day-count, anything asset-class-specific). That is a **primitive**, not an operator — no exceptions (OPR6). See [`../primitive/runbook.md`](../primitive/runbook.md).
- **Building a workflow template** (a multi-step composition) or a **UI/explanation step**. Those are L4/L5 and the orchestrator/UI layer respectively.

## Pre-flight — seven decisions BEFORE any code

Answer all seven in the PR description. **If any is uncertain, stop and ask.**

### 1. Finance-blind test (OPR6) — would it run on temperature data?
Write the operator's purpose in one sentence using only structural/statistical terms. Forbidden vocabulary: instrument, asset class, curve_family, tenor, sovereign, bond, swap, OIS, Treasury, currency, yield, spread, rate, **P&L, Sharpe, financing, day-count**. Rewrite the sentence as if the inputs are temperature series. Still makes sense → candidate operator. Specific to finance → it is a primitive. **There is no finance carve-out.**

### 2. Method family (OPR1) — which family?
One of the `OperatorMethodFamily` values (`alignment, arithmetic, masking, windowing, aggregation, statistical_relationship, single_series_transform, cross_sectional, unit_conversion`), or a genuinely new family with a one-paragraph justification (a new family is a `Literal`-enum extension + ADR).

### 3. Toolbox admission + parsimony (OPR4) — distinct and needed?
Write the existing-operator composition that would produce the same effect (`<input> → op_A → op_B → <same output>`) and argue why it is materially worse on ≥1 of: accuracy, efficiency, interpretability, provenance, **DAG-composition clarity** — or impossible. Then name the **target toolbox category** it fills and the DAG node it enables that is impossible today. **There is no ≥3-archetype gate** (removed in v2.0); the bar is distinctness + toolbox-membership + the hard finance-blind boundary.

### 4. Method novelty (OPR5) — genuinely new?
Run `name` + `methodology.what_it_does` past every existing operator. If an existing operator's variant set could cover it, **extend that operator** instead.

### 5. Typed I/O (OPR9) — which artifact types, declared how?
Name the input slot(s) and output, each as a `SlotDescriptor` over the closed family (`Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`, `ScalarMetric`) — with `is_list` / `optional` / `scalar_ok` / `expected_unit_family` / `sub_kind` as needed. If the natural I/O is not in the closed family, either it is an ART4 closed-family decision (the operator PR depends on the type landing first) or the candidate is not an operator.

### 6. Parameter surface (OPR7 + OPR8) — methods, defaults, refusal.
List every consequential method variant the caller can pick. **Every supported method is implemented in Python**, selected by a typed param; `config.yaml` declares the valid set and a default; the caller switches by setting the param or omits it for the default — **never opinionated**. A declared-but-unbuilt variant **refuses cleanly** (`NotImplementedError` → `methodology.planned_extensions`), never silently falls back. Convention-style knobs (ddof, min_periods) are YAML-locked defaults, not central knobs.

### 7. Metadata algebra (OPR11) — units / frequency / missingness.
State the output-unit propagation rule (passthrough / `COUNT` / `RATIO` / …); confirm the operator **refuses** cross-unit (no in-operator conversion — that is `convert_units`); for every **multi-artifact** operator declare `require_matching_frequency` and `require_matching_missingness` (both default `True`, strict).

---

## Step 1 — Write the admission argument
Before any code, the PR description answers the seven pre-flight questions, citing by OPR-number. If hand-waved, the PR bounces before code review.

## Step 2 — Place the four-file folder

```
shared/operators/<operator_name>/
  __init__.py
  config.yaml
  schemas.py
  operator.py
```

- `<operator_name>` is lowercase snake_case naming the structural/statistical method (`correlation`, `rolling_zscore`, `convert_units`). **Never** embeds a finance concept.
- Lives at `shared/operators/<operator_name>/`. **Always** (OPR3). Never under an agent.
- The function name in `operator.py` matches the folder verbatim.

## Step 3 — Draft `config.yaml` (the configurability / de-opinionation layer, OPR7)

```yaml
operator:
  name: <operator_name>
  method_family: <one OperatorMethodFamily value>
  version: "1.0.0"                 # semver; MUST equal operator.py _OPERATOR_VERSION (OPR12)
  description: >-
    <one paragraph, finance-blind framing — no asset-class concepts>

defaults:
  <every user-choosable method/variant default>:
    value: <scalar | enum string | null>
    source: <registered closed-taxonomy tag>     # OPR12
    rationale: <one sentence>
    valid_values: [...]                           # for enum-typed defaults

methodology:
  what_it_does: >-
    <one paragraph, finance-blind>
  planned_extensions:
    - "<each declared-but-unbuilt method variant + why>"   # honest-refusal targets (OPR8)
```

The config is where every method choice and default is **declared and offloaded** so the operator is not opinionated and the caller can switch seamlessly (OPR7). Numeric ranges go on the `<Operator>Params` field (`Field(ge=, le=)`), not in YAML — `OperatorDefault` is `extra="forbid"` and has no `valid_range`.

## Step 4 — Draft `schemas.py`

```python
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

class <Operator>Params(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # every consequential method variant, typed (OPR8)
    method: Literal["...", "..."] = "<default>"
    # convention knobs as constrained numerics (range re-checked on both paths)
    min_periods: int = Field(..., ge=1)
    # structural-metadata flags — strict by default (OPR11); only for multi-artifact operators
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True

    @model_validator(mode="after")
    def _invariants(self) -> "<Operator>Params":
        # cross-field invariants live in code, not YAML
        return self
```

Every consequential variant is a typed field; nothing material is hidden in code (OPR8).

## Step 5 — Draft `operator.py` (the canonical signature)

```python
from pathlib import Path
from typing import Optional
from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import <InputType>, <OutputType>
from shared.config.operator_config import (
    OperatorConfig, OperatorConfigError, load_operator_config, _check_config_identity,
)
from shared.operators.<operator_name>.schemas import <Operator>Params

_OPERATOR_NAME = "<operator_name>"
_OPERATOR_VERSION = "1.0.0"
_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

class <Operator>Error(ValueError):          # OPR13 — subclass ValueError, always
    """Raised by <operator_name> on a recoverable user-facing failure."""

def <operator_name>(
    left: <InputType>,
    right: Optional[<InputType>] = None,    # only for optional slots
    params: Optional[<Operator>Params] = None,   # OPR8 — ALWAYS Optional, default None
    config: Optional[OperatorConfig] = None,
) -> <OutputType>:
    # 1. config + identity (OPR12 — name AND version)
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # 2. resolve defaults from YAML when omitted (OPR8)
    if params is None:
        params = <Operator>Params(
            method=config.default_value("method"),
            min_periods=config.default_value("min_periods"),
        )

    # 3. honest refusal for declared-but-unbuilt method variants (OPR8)
    if params.method not in _IMPLEMENTED_METHODS:
        raise NotImplementedError(
            f"method={params.method!r} is declared but not yet built; "
            "see config.yaml methodology.planned_extensions."
        )

    # 4. metadata enforcement (OPR11) — raise <Operator>Error on mismatch; refuse cross-unit
    _validate_structural_metadata(left, right, params=params)

    # 5. the transform — pure, no I/O; forbid +/-Inf into the payload (OPR14 / ART11)
    payload = _apply(left, right, params)

    # 6. lineage (OPR10) — one OperatorStep via .build; aux chains; sanitised params
    step = OperatorStep.build(
        name=_OPERATOR_NAME, version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(params.model_dump()),
        input_hashes=(left.lineage.head_hash,),
        auxiliary_lineages=((right.lineage,) if right is not None else ()),
    )
    return <OutputType>(payload=payload, lineage=left.lineage.append(step), ...)
```

Per OPR7 (no hidden methodology constants), OPR10 (`.build` + `auxiliary_lineages` + sanitise), OPR11 (strict metadata, refuse cross-unit), OPR13 (raise `<Operator>Error`; no `{"error": ...}` envelopes; no raw pandas/numpy leaks), OPR14 (pure; no clock/random; no `±Inf`).

## Step 6 — Draft `__init__.py`

```python
from pathlib import Path
from shared.operators.<operator_name>.operator import <operator_name>, <Operator>Error
from shared.operators.<operator_name>.schemas import <Operator>Params

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
__all__ = ["<operator_name>", "<Operator>Error", "<Operator>Params", "CONFIG_PATH"]   # all four (OPR16)
```

## Step 7 — Register in the registry (OPR9 / OPR15)

Add the operator to `OPERATOR_REGISTRY` in `shared/workflow/registry.py` with a structured `SlotDescriptor` per slot — explicit `element_type`, `is_list`, `optional`, `scalar_ok` (+ `scalar_value_type`), `expected_unit_family`, `sub_kind` — and the `output_type` descriptor. The Python signature mirrors the descriptors. **No by-name special-casing in the executor**; any discriminator argument is a declared registry field resolved from params.

## Step 8 — Tests (OPR16)

1. **The registry-consistency meta-test** (the gate) automatically covers the new operator — it asserts signature == slots ∪ `{params, config}`, `params` default `None`, `<Operator>Error ⊂ ValueError`, types in the canonical enum, the four `__init__` exports, config name+version identity, and single-`OperatorStep` append. The operator must turn it green.
2. **Per-operator `tests/test_operator_<operator_name>.py`:** happy path; every method variant; every structural-metadata mismatch raising `<Operator>Error`; lineage extension N→N+1; idempotent `head_hash` on rerun; **a non-rates finance-blind case** (random walk / temperature / Z_SCORE); declared-but-unbuilt variant raises `NotImplementedError`.
3. **At least one workflow-integration test** through a real template (the operator's parity layer — there is no SQL parity).

## Step 9 — Run the gate
`pytest` the meta-test + the per-operator file; both green. Running the meta-test **is** the verification that the operator conforms — do not hand-attest.

## Step 10 — CI lint
```bash
python -m shared.config.lint
```
Aligns shared convention names/values across primitives and operators; either align or use a distinct name.

## Automation scope
Operators are **not currently bot-eligible** — they require finance-blind judgment (OPR6), toolbox-admission justification (OPR4), and workflow-integration design the primitive bot's templates do not cover. The scope-expansion path opens when an operator-build template (and the OPR16 gate) is added to the automation.

## Common pitfalls (all auto-reject)
- Finance concept or finance math in the operator → it is a primitive (OPR6).
- `params` declared required (no default) → breaks the executor ABI (OPR8).
- A method variant that silently falls back instead of refusing (OPR8).
- A raw pandas/numpy/pydantic/lineage exception escaping; `OperatorConfigError` not a `ValueError`; `return {"error": ...}` (OPR13).
- Output type `dict` / `pd.DataFrame`; a bare class-name slot instead of a `SlotDescriptor` (OPR9).
- Lineage not extended; `auxiliary_lineages` skipped for a non-primary input; NaN/Inf in `step.params` (OPR10).
- Silent unit conversion (use `convert_units`); a `require_matching_*` flag missing on a multi-artifact operator (OPR11).
- `±Inf` in a payload; `datetime.now`/unfixed random in the compute path (OPR14).
- Operator placed under an agent; importing an agent package (OPR3).
- Tests only on rates data; the meta-test not green (OPR16).

## PR review checklist
Cite the OPR-number; do not paraphrase.
- [ ] **Pre-flight** answered (OPR1, OPR4, OPR5, OPR6, OPR8, OPR9, OPR11).
- [ ] **OPR1/OPR2** one finance-blind method family; `method_family` set.
- [ ] **OPR3** at `shared/operators/<name>/`; no agent imports.
- [ ] **OPR4** distinct + toolbox-member; composition considered; **no ≥3-archetype argument used**.
- [ ] **OPR5** genuinely new method/variant.
- [ ] **OPR6** finance-blind, absolute; non-rates test passes.
- [ ] **OPR7** config = configurability layer; every default in YAML; design locks documented.
- [ ] **OPR8** `params: Optional=None`; every variant exposed; methods baked in + switchable; unbuilt refuse cleanly.
- [ ] **OPR9/OPR15** structured `SlotDescriptor` I/O; signature mirrors it.
- [ ] **OPR10** one `OperatorStep` via `.build`; `auxiliary_lineages`; sanitised params.
- [ ] **OPR11** units refuse; frequency + missingness flags strict-by-default; honest combined lenient policy.
- [ ] **OPR12** config name + version identity; closed `source` taxonomy.
- [ ] **OPR13** `<Operator>Error(ValueError)`; no raw leaks; no envelopes.
- [ ] **OPR14** pure; rerun-deterministic; no `±Inf`.
- [ ] **OPR16** meta-test green; per-operator + workflow-integration tests present.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v2.0 | 2026-05-30 | Rewritten to match operator contract v2.0: absolute finance-blindness (no trade operators); toolbox admission (≥3-archetype gate removed); uniform `params: Optional=None` + config-default resolution; methods-baked-in + honest-refusal added to the parameter step; structured `SlotDescriptor` registration; units-refuse / frequency-load-bearing / missingness-uniform metadata step; one `ValueError`-rooted error family; lineage `.build` + `auxiliary_lineages` + `sanitize_params_for_lineage`; config name+version identity; the OPR16 registry-consistency meta-test as the gate (Step 8/9). | [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md) |
| v1.1 | 2026-05-17 | Pre-canonical runbook (four-file shape; v1 promotion gate; trade operators; v1 lineage API). Superseded by v2.0. | — |
| v1 | 2026-05-17 | Initial runbook. | — |
