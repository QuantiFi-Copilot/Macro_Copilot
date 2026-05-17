# Runbook — How to add a new operator

> Step-by-step procedure for adding a new operator. The bar to clear is high: operators are the small, finance-blind structural-transformation catalog every workflow template composes. A new operator is debt amortised across every workflow that touches it, so the admission gate is the tightest in the platform.

**Version:** v1.1
**Last reviewed:** 2026-05-17
**Audience:** any contributor (human or AI agent) introducing a new operator.
**Prerequisite reading:** [`README.md`](README.md) — the operator contract (OPR1–OPR16). Read it once before starting; refer back when a step says *"per principle OPR<N>."*
**Operationalises principles:** P1 (built right, not as a placeholder), P3 (every operator follows the same shape), P9 (finance-blind operator boundary — the *defining* constraint), and operator-specific OPR1–OPR16 throughout.
**AC class:** Adding a new operator is **Load-bearing** per [`../../00_thesis/02_ai_agent_development_contract.md`](../../00_thesis/02_ai_agent_development_contract.md). Run the full self-check; do not skip the gate.

---

## When to use this runbook

- **Introducing a new structural method family** the platform does not yet have (e.g., a new aggregation pattern, a new windowing strategy, a new ranking transform).
- **Promoting a workflow-local helper into a shared operator** once it has accumulated the ≥3-archetype reuse the promotion rule (OPR4) requires.
- **Splitting an over-broad operator** that has accidentally accreted two unrelated method families (OPR2 violation).

## When NOT to use this runbook

- **Adding a new variant inside an existing operator's method family** (e.g., a new `aggregator` value in `conditional_aggregate`, a new `join_policy` value in `align_series`). That is an in-place edit to the operator's YAML + `<Operator>Params` Literal + a code branch + a test — much smaller than a new operator.
- **Building a workflow-local helper** that composes existing operators. That belongs inside the workflow template's package, not in `shared/operators/`. Per OPR4, helpers do not get promoted to shared operators until they pass the promotion rule.
- **Building a finance-aware computation.** That is a primitive, not an operator. See [`../primitive/runbook.md`](../primitive/runbook.md).
- **Building a workflow template.** Multi-step compositions are templates; a single operator is one structural transformation. See [`../workflow_template/runbook.md`](../workflow_template/runbook.md) (forthcoming).
- **Building a UI / explanation helper.** That belongs in the orchestrator / UI layer, not in `shared/operators/`.

The dividing line: a new operator introduces a *new structural method family or variant* the platform's operator algebra does not yet express, *and* the promotion rule (OPR4) holds. An in-place edit, a workflow-local helper, or a non-structural transform does not.

## Pre-flight check — seven decisions BEFORE writing any code

A new operator PR is hard to undo: the operator may end up in multiple workflow templates whose downstream behaviour depends on its contract. Seven decisions to make and document in the PR description before any code is drafted. **If any of these is uncertain, stop and ask the human (AC8).**

### 1. Finance-blind test (OPR6) — could it run on temperature data?

Write the operator's purpose as a single sentence using **only structural terms** (alignment, masking, windowing, aggregation, ranking, arithmetic). Forbidden vocabulary: instrument, asset class, curve_family, tenor, sovereign, bond, swap, OIS, Treasury, currency, yield, spread, rate.

Concrete test: rewrite your one-sentence purpose as if the inputs are temperature time series instead of yield series. Does the operator's job still make sense? If yes, you have a candidate operator. If no (the job is specific to finance), you are looking at a primitive, not an operator.

### 2. Structural method family (OPR1) — which family does this belong to?

Identify the structural method family. Either it is one of the existing families (alignment, masking, thresholding, windowing, aggregation, arithmetic, selection, construction, evaluation) — in which case you are usually adding a *variant* not a *new operator* (see "When NOT to use this runbook") — or it is a genuinely new family.

A genuinely new family needs a one-paragraph justification in the PR description: what structural method does the platform's operator algebra not yet express, and why is this addition necessary?

### 3. Parsimony + promotion (OPR4) — what existing composition was considered?

Write out the composition of existing operators that would produce the same output:

`<input> → operator_A → operator_B → operator_C → <same output>`

Then argue why this composition is **materially worse** against at least one of the five criteria:

- **Accuracy.** The composition accumulates error that a direct operator avoids.
- **Efficiency.** The composition requires N intermediate artifact materialisations; the direct operator does it in one pass.
- **Interpretability.** The composition produces intermediate artifacts whose meaning a workflow-template reviewer cannot follow.
- **Provenance.** The composition produces a lineage chain too noisy to audit.
- **Workflow-template clarity.** The composition forces every template that uses it to repeat the same sub-DAG; a single operator simplifies templates and makes the operator's role auditable.

**Then defend the promotion rule.** Name at least three distinct workflow archetypes that will use this operator (event_study, regime_conditioned_relationship, attribution_decomposition, cross_sectional_screen, backtest, …), OR argue it is algebraically foundational (no composition of existing operators reproduces its effect cleanly). The reviewer evaluates this against the existing operator set; **promotion is not self-declared.**

If the parsimony argument or the promotion argument fails, stop. The right path is either to extend an existing operator with a new variant, or to keep the candidate as workflow-local code until it has accumulated cross-template reuse.

### 4. Concept novelty (OPR5) — is this genuinely new?

Run the proposed operator's `name` and `methodology.what_it_does` past every existing operator's `name` and `what_it_does`. Is there an existing operator whose scope could plausibly cover this? If yes, the right path is usually to *extend the existing operator's variant set* rather than add a new operator. Operators are scarcer than primitives by design.

### 5. Typed I/O (OPR9) — what artifact types in and out?

Name the input artifact type(s) (drawn from the closed family: `Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`, `TradeSet`) and the output artifact type. If the operator's natural I/O does not fit the closed family, one of two things is true:

- A new artifact type is needed — that is an ADR-gated decision under [P8](../../00_thesis/01_non_negotiables.md) and [`../artifact/`](../artifact/) (forthcoming); the operator PR depends on the new artifact type landing first.
- The candidate is not actually an operator (e.g., it consumes / emits dicts or prose, which is a UI or primitive concern).

### 6. Parameter surface (OPR8) — what consequential variants?

List every method variant the operator's family supports that callers should be able to choose between (e.g., `join_policy: ["inner", "outer"]` for alignment, `aggregator: ["mean", "median", "max", "min"]` for aggregation). Each variant becomes a field on `<Operator>Params`, each value becomes a Literal entry, and the default lives in `config.yaml`'s `defaults:` block with a registered `source`.

Hidden hardcoded choices the caller cannot inspect or override are prohibited (OPR8 anti-pattern).

### 7. Structural-metadata enforcement (OPR11) — what compatibility checks?

Identify the structural-metadata fields the operator needs its inputs to agree on: frequency, units, missingness policy, index type, etc. For each, declare whether the default is strict (`require_matching_X=True`) or whether the operator can legitimately tolerate mismatches (rare).

The default is **strict** unless there is a defensible structural argument otherwise. Strict-by-default + explicit opt-in flag + lineage recording is the canonical pattern.

---

## Step 1 — Write the parsimony + promotion argument in the PR description

Before drafting any code, the PR description must explicitly answer the seven pre-flight questions above. This is the reviewer's first read. If the answers are missing or hand-waved, the PR bounces before any code is reviewed. **Per AC2 (cite, never paraphrase) and AC6 (cite operationalised principles in commits), cite by OPR-number throughout.**

The PR description must also explicitly name:

- The closest existing operator and why this one is not a variant of it.
- The composition this operator replaces and why composition is materially worse.
- At least three workflow archetypes that will use this operator, OR a one-paragraph defense of algebraic foundation.

## Step 2 — Place the four-file folder

```
shared/operators/<operator_name>/
  __init__.py
  config.yaml
  schemas.py
  operator.py
```

Naming conventions:

- `<operator_name>` is lowercase snake_case, names the structural transformation (`align_series`, `threshold_events`, `event_windows`, `series_arithmetic`). Operator names never embed finance concepts.
- The folder lives at `shared/operators/<operator_name>/`. **Always.** Never under any agent (OPR3).
- The Python function name in `operator.py` matches the folder name verbatim (e.g., folder `align_series/` has `def align_series(...)`).

## Step 3 — Draft `config.yaml`

Three top-level blocks, in this conventional order:

```yaml
operator:
  name: <operator_name>
  method_family: <one of the canonical OperatorMethodFamily values: alignment |
                  arithmetic | masking | windowing | aggregation | ranking |
                  mapping | trade_construction | trade_evaluation | trade_summary |
                  (new family, justified above, requires Literal-enum extension
                  in shared/config/operator_config.py)>
  version: "1.0.0"
  description: >-
    <one paragraph; asset-class-blind framing (per OPR6) — no asset-class-
    specific concepts like curve_family or tenor; generic trading concepts
    like trade or holding-period are allowed if applicable>

defaults:
  <every methodology default the operator makes>:
    value: <scalar | enum string | null>
    source: <registered tag — see "Source tags" below>
    rationale: <one sentence explaining why this default>
    valid_values: [...]               # ONLY for enum-typed defaults

methodology:
  what_it_does: >-
    <one paragraph; same asset-class-blind framing as operator.description
    but longer; drives the methodology card if the operator is ever
    surfaced in UI>
  planned_extensions:
    - "<each planned variant or capability not yet built>"
```

**Numeric ranges are NOT supported in operator YAML.** `OperatorDefault` is `Pydantic` with `extra="forbid"` and only accepts `value, source, rationale, valid_values` — adding `valid_range` will fail loader validation. Numeric constraints on defaults belong in the corresponding `<Operator>Params` field as a Pydantic `Field(..., ge=..., le=...)` constraint. This differs deliberately from the primitive `config.yaml` which does support `valid_range` — the operator schema is tighter.

**Per OPR7**: every methodology default goes here. *Verify*: read the YAML; can you list every methodology choice the operator makes without reading `operator.py`? If not, methodology is hidden in code (unless it's one of the documented design-locked-constant cases per OPR7 — see that record).

**Per OPR12**: every `source` value names a tag actually in use across the operator catalog. Current tags observed in live `shared/operators/*/config.yaml` files: `operator_v1_default`, `methodology_judgement_pending_review`, `industry_standard_252_business_days`, `industry_standard_sovereign_repo_usd_money_market`, `rolling_regression_primitive_v1`, `derived_from_window`. New tags should be added by editing live configs first and documenting them in a registry; today the lint validates schema shape but does not yet enforce a tag registry. No vague tags (`default`, `standard`, `convention`, `tbd`).

**Per OPR6**: the `description` and `methodology.what_it_does` use **asset-class-blind** vocabulary — generic trading concepts like *trade*, *holding window*, *P&L* are allowed; asset-class-specific concepts (*curve_family*, *tenor*, *sovereign vs OIS*, *FX pair*, *equity sector*) are forbidden. The test: would the operator's code change if the data swapped from rates to FX to equities? If yes (asset-class branching), violation; if no (the operator treats them all structurally identically), it's fine.

## Step 4 — Draft `schemas.py`

```python
from typing import Literal, Optional, List
from pydantic import BaseModel, ConfigDict, Field, model_validator


class <Operator>Params(BaseModel):
    """Parameters for ``<operator_name>``.

    Per the operator-architecture doctrine: consequential method
    variants are exposed here. Hidden hardcoded choices are forbidden.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Every consequential method variant, typed as Literal[...] or
    # constrained numeric
    <variant_1>: Literal["...", "..."] = "<default>"
    <variant_2>: Literal["...", "..."] = "<default>"

    # Structural-metadata compatibility flags (OPR11). Default strict.
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True

    @model_validator(mode="after")
    def _validate_invariants(self) -> "<Operator>Params":
        # Cross-field invariants live in code, not YAML.
        # E.g., "fill_limit is only meaningful when fill_policy='ffill'"
        ...
        return self
```

**Per OPR8**: every consequential variant is a typed `Literal[...]` field with a default. Validators stay in code.

**Per OPR11**: structural-metadata-matching flags default to `True` (strict).

**Per OPR9**: the operator's *function signature* (in `operator.py`) is typed against closed-family artifact types. `<Operator>Params` carries non-artifact parameters; the artifact inputs themselves are positional arguments to the operator function.

## Step 5 — Draft `operator.py`

```python
from pathlib import Path
from typing import Optional, List

from shared.artifacts.lineage import OperatorStep
from shared.artifacts.types import <InputArtifactType>, <OutputArtifactType>
from shared.config.operator_config import OperatorConfig, OperatorConfigError, load_operator_config
from shared.operators.<operator_name>.schemas import <Operator>Params


_OPERATOR_NAME = "<operator_name>"
_OPERATOR_VERSION = "1.0.0"
_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# Design-locked constants (OPR7 allowance): document each one's
# rationale + migration pointer in the docstring above the constant.
# E.g.:
# _SUMMARY_SENTINEL_DATE = pd.Timestamp("1970-01-01")
# """Anchor date for single-row summary outputs. Design-locked so two
# summaries can compose via series_arithmetic.subtract. Migration:
# when series_arithmetic supports caller-supplied anchor alignment."""


class <Operator>Error(ValueError):
    """Raised by ``<operator_name>`` on a recoverable user-facing failure."""


def <operator_name>(
    <artifact_input_1>: <InputArtifactType>,
    # ... additional artifact inputs as needed ...
    params: Optional[<Operator>Params] = None,
    config: Optional[OperatorConfig] = None,
) -> <OutputArtifactType>:
    """<one-sentence structural description, asset-class-blind framing>.

    Parameters
    ----------
    <artifact_input_1>:
        <structural description of the input artifact>
    params:
        Optional <Operator>Params. When omitted, fields are resolved
        explicitly from config.yaml defaults via config.default_value().
    config:
        Optional OperatorConfig. When omitted, bundled config.yaml is
        loaded (process-cached).

    Returns
    -------
    <OutputArtifactType>
        <structural description of the output>

    Raises
    ------
    <Operator>Error
        On structural-metadata mismatch or invalid inputs.
    """
    # ------------------------------------------------------------------
    # 1. Load config + resolve defaults explicitly (field-by-field).
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)

    # Defensive: detect drift between caller-supplied config and this
    # operator's identity.  Live operators do this; copy the pattern.
    if not isinstance(config, OperatorConfig):
        raise OperatorConfigError(
            f"<operator_name>: 'config' must be an OperatorConfig instance; "
            f"got {type(config).__name__}."
        )
    if config.operator.name != _OPERATOR_NAME:
        raise OperatorConfigError(
            f"<operator_name>: config name mismatch — expected "
            f"{_OPERATOR_NAME!r}, got {config.operator.name!r}."
        )

    if params is None:
        # Resolve each field EXPLICITLY by name.  Do not iterate over
        # model_fields; real <Operator>Params often include per-call
        # fields (e.g. output_keys, legs, financing_rate_panel) that
        # are NOT in the YAML defaults block.  Resolve only the fields
        # that have YAML-backed defaults; let Pydantic supply schema
        # defaults for the rest.
        params = <Operator>Params(
            <variant_1>=config.default_value("<variant_1>"),
            <variant_2>=config.default_value("<variant_2>"),
            # ... only the fields that exist in defaults: in config.yaml ...
            # Per-call fields without YAML defaults are omitted here;
            # Pydantic uses their schema defaults.
        )

    # ------------------------------------------------------------------
    # 2. Structural-metadata enforcement (OPR11).  Raise on mismatch.
    # ------------------------------------------------------------------
    _validate_structural_metadata(
        <artifact_input_1>, ..., params=params,
    )

    # ------------------------------------------------------------------
    # 3. The transformation itself — pure function, no I/O.
    # ------------------------------------------------------------------
    payload = _apply_<operator_name>(<artifact_input_1>, ..., params=params)

    # ------------------------------------------------------------------
    # 4. Lineage extension (OPR10).  Append, do not extend.
    #    Runtime-resolved policy decisions go INSIDE params, not as a
    #    separate field on OperatorStep (the model rejects unknown fields).
    # ------------------------------------------------------------------
    primary_input = <artifact_input_1>   # the lineage chain we append to
    new_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=params.model_dump(),       # includes any runtime-resolved decisions
        input_hashes=(primary_input.lineage.head_hash,),
        # For binary / N-ary operators, auxiliary inputs' lineages
        # embed into the step rather than living as a separate top-
        # level chain.  Unary operators leave this empty.
        # auxiliary_lineages=(<secondary_input>.lineage,),
    )
    new_lineage = primary_input.lineage.append(new_step)

    return <OutputArtifactType>(
        payload=payload,
        lineage=new_lineage,
        # ... other typed-artifact metadata ...
    )
```

**Per OPR7**: every constant in this file is either a mathematical truth, a documented design-locked constant (with rationale + migration pointer), or `config.default_value("...")`.

**Per OPR10**: the lineage API is `OperatorStep.build(name=..., version=..., params=..., input_hashes=..., auxiliary_lineages=...)` and `Lineage.append(step)`. Direct `OperatorStep(...)` construction or `lineage.extend(...)` are not the correct calls. Runtime policy decisions go inside `params` (the `OperatorStep` model is `extra="forbid"` — there is no `policy_choices` field).

**Per OPR11**: structural-metadata enforcement happens immediately; mismatches raise `<Operator>Error` with a specific message before any computation runs.

**Per OPR13**: failures raise `<Operator>Error` (a `ValueError` subclass). No `return {"error": "..."}` envelopes.

**Per OPR14**: no `datetime.now()`, no unfixed `random.*`, no DB access, no network, no filesystem (except the bundled-config load at module top).

**Default-resolution pitfall.** Do not write `<Operator>Params(**{k: config.default_value(k) for k in <Operator>Params.model_fields})`. Real operators have per-call fields not in YAML defaults (`output_keys`, `legs`, `financing_rate_panel`, etc.); calling `config.default_value()` on those raises. Resolve YAML-backed fields explicitly by name, and let Pydantic supply schema defaults for the rest.

## Step 6 — Draft `__init__.py`

```python
from pathlib import Path

from shared.operators.<operator_name>.operator import <operator_name>, <Operator>Error
from shared.operators.<operator_name>.schemas import <Operator>Params


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "<operator_name>",
    "<Operator>Error",
    "<Operator>Params",
    "CONFIG_PATH",
]
```

Stable re-exports so workflow templates can `from shared.operators.<operator_name> import <operator_name>` without reaching into submodules.

## Step 7 — Register and integrate

A standalone operator does nothing. Confirm at least the following touchpoints land in the same PR (or are explicitly tracked as prerequisites):

| Touchpoint | What goes in |
|---|---|
| `shared/operators/__init__.py` | Re-export the operator (if the package exposes a flat surface). |
| Operator registry (typically in `shared/workflow/registry.py`) | If the workflow executor uses a registry pattern, register `<operator_name>` so workflow templates can reference it by name. |
| At least one workflow template under `<agent>/workflows/<template>/template.yaml` | The first consumer of the new operator. Without a consumer, OPR4's promotion rule fails — the operator should not yet exist in `shared/`. |
| `tests/conftest.py` | If the test fixture pattern requires it (synthetic artifact builders). |

Missing any of these means the operator may compile but is not reachable from any workflow — the catalog silently loses it.

## Step 8 — Run the test pattern (OPR16)

Two test layers, both required:

### `tests/test_<operator_name>.py` — unit tests

- Happy path: synthetic typed-artifact inputs produce the expected output with bundled defaults.
- Every consequential variant: one test case per `Literal[...]` value, verifying each variant's behaviour.
- Every structural-metadata mismatch case: one test per `require_matching_X=False` flag, both showing strict-default rejection (`<Operator>Error` raised) and explicit-opt-in tolerance (mismatch accepted, choice recorded in lineage).
- Lineage extension (OPR10): assert input's lineage length is N; output's lineage length is N+1; the new step's `operator_name` matches.
- Determinism (OPR14): call the operator twice with identical inputs; assert byte-identical outputs (`output_1.lineage.head_hash == output_2.lineage.head_hash`).
- **Cross-asset robustness (OPR6)**: at least one test case uses non-rates synthetic data (random walks, temperature data, equity prices) and verifies the operator produces structurally-correct output without rates assumptions.

### Workflow-integration test

At least one workflow template under `<agent>/workflows/` references this operator and has an integration test that exercises the operator inside the template:

- The operator is invoked via the workflow executor with realistic upstream and downstream operators.
- The lineage chain extends correctly across the template's nodes.
- The output artifact flows into the next node and is consumed without error.

**No SQL parity test** — operators don't talk to the DB; there is no independent SQL baseline. The workflow-integration test is the operator's parity layer.

## Step 9 — Verify the promotion rule (OPR4)

Before merging, confirm one of the following three paths:

- **≥3-archetype reuse.** List the workflow archetypes by name in the PR description; for each, point to either an existing template that references the operator or an explicitly planned template PR.
- **Algebraic foundation.** Document the foundation in `methodology.what_it_does` and confirm with the reviewer that no composition of existing operators reproduces the operator's effect cleanly.
- **Bootstrap exception (ADR-backed).** New asset-class or new archetype work sometimes legitimately needs a new operator before three consumers exist (chicken-and-egg). For these, file an ADR in `../../05_decisions/` that explicitly records the bootstrap status, names the expected consumer(s), and commits to a phase-boundary review where the operator is promoted (consumer #3 lands) or demoted to workflow-local code. The ADR is mandatory under this path — bootstrap-without-ADR is rejected.

If none of the three holds, the operator should not yet live in `shared/operators/`. Keep it as workflow-local code; promote when reuse arrives.

## Step 10 — Run CI lint and structural checks

```bash
python -m shared.config.lint
```

Operator-side lint behaviour: the live lint compares the same convention name across primitives *and* operators. If your operator's `ffill_limit` default differs from a primitive's `ffill_limit_days` value, the lint flags it. Either align, or use a distinct convention name (per the playbook-style disambiguation rule for cross-config consistency).

## Automation scope — what the build-bot is and is not allowed to do

Operators are **not currently bot-eligible.** The automation bot at `tmp/automation/primitive_automation/` is scoped to primitives (specifically, Bucket 1A standard primitives — Archetype A / B in level / spread shape). Operator construction is outside the bot's templates and is human-implemented.

This is deliberate: operators require finance-blind judgment (OPR6), promotion-rule justification (OPR4), and workflow-integration design that the bot's template scaffolding does not yet cover. The scope-expansion path is documented in [`../primitive/runbook.md`](../primitive/runbook.md)'s automation section — when an operator-build template is added to the bot, this restriction relaxes.

## Common pitfalls

Things reviewers see repeatedly:

- **Finance concepts leaking into the operator.** The most common single failure. `<Operator>Params` has `curve_family` or `tenor`, or the code branches on instrument identity. OPR6 violation; refactor to artifact metadata.
- **An operator that only one workflow uses.** OPR4 promotion-rule violation; keep as workflow-local code until reuse arrives.
- **A new operator when an existing one could be extended.** OPR4 + OPR5 violation; add a variant to the existing operator's `<Operator>Params` Literal.
- **Methodology constants in `operator.py`.** OPR7 violation; move to YAML.
- **Output type is `dict` or `pd.DataFrame`.** OPR9 violation; lift to a typed artifact.
- **Lineage chain not extended.** OPR10 violation; the output's lineage must be input's lineage + this operator's `OperatorStep`.
- **Silent tolerance of structural-metadata mismatches.** OPR11 violation; strict-by-default with explicit opt-in.
- **`return {"error": "..."}` from inside the operator.** OPR13 violation; raise `<Operator>Error` instead.
- **`datetime.now()` or unfixed random anywhere in the operator.** OPR14 violation; pure function only.
- **Internal fan-out (`for x in xs: ...`) where `xs` is not a first-class collection artifact.** OPR15 violation; use a collection artifact (`SeriesSet`) or wait for the `map` meta-operator.
- **Tests only use rates synthetic data.** OPR6 verification gap; add a non-rates synthetic test case (temperature, random walk, equity).
- **Operator placed under `rates_agent/` or any agent.** OPR3 violation; operators live at `shared/operators/`.

## PR review checklist

The reviewer signs off when each item is met. Cite the matching OPR-number; do not paraphrase (AC2).

- [ ] **Pre-flight check** answered explicitly in the PR description (OPR1, OPR4, OPR5, OPR6, OPR8, OPR9, OPR11 each addressed).
- [ ] **OPR1.** The operator owns a single structural method family. Family is named in `config.yaml`'s `operator.method_family` field.
- [ ] **OPR2.** The operator's `methodology.what_it_does` describes one family; no internal mode switch across different families.
- [ ] **OPR3.** The operator lives at `shared/operators/<operator_name>/`. Not under any agent.
- [ ] **OPR4.** Parsimony argument explicit (composition considered, defended against ≥1 criterion). Promotion path documented: ≥3 archetypes, OR algebraic foundation confirmed by reviewer, OR bootstrap exception with ADR.
- [ ] **OPR5.** Genuinely a new structural method or variant, not a one-off transform.
- [ ] **OPR6.** Asset-class-blind: no asset-class-specific vocabulary in signature, code, config, or imports. `grep` for `from rates_agent` etc. is empty. Generic trading concepts (trade, holding window, P&L) are allowed when they're part of the structural method family. At least one test case uses non-rates data.
- [ ] **OPR7.** Every user-choosable methodology default in `config.yaml`. Hidden methodology constants in `operator.py` only if they pass the design-lock test (documented in docstring + stamped into lineage + migration-scoped + genuinely structural, not a methodology choice in disguise). Cross-field invariants in Pydantic.
- [ ] **OPR8.** `<Operator>Params` exposes every consequential variant; no hidden hardcoded choices.
- [ ] **OPR9.** Inputs and outputs are closed-family artifact types. No naked pandas / numpy / dicts.
- [ ] **OPR10.** Output's lineage extends primary input's lineage via `Lineage.append(OperatorStep.build(name=..., version=..., params=..., input_hashes=..., auxiliary_lineages=...))`. Runtime-resolved policy decisions are inside `params` (no separate `policy_choices` field).
- [ ] **OPR11.** Structural-metadata enforcement (frequency, units, missingness) strict by default. Mismatch raises `<Operator>Error` with a specific message. Opt-in flag exists for each strict check; opt-in choice recorded inside `params` so it shows up in the resulting `OperatorStep`.
- [ ] **OPR12.** Every `default.source` references a registered tag.
- [ ] **OPR13.** Operator raises typed `<Operator>Error(ValueError)`. No `return {"error": ...}` anywhere.
- [ ] **OPR14.** Pure function: no `engine`, no DB, no network, no filesystem (except bundled config), no `datetime.now`, no unfixed random.
- [ ] **OPR15.** Arity declared in `OperatorSpec.input_slots` (`"<Type>"` or `"List[<Type>]"`). The operator's Python signature mirrors the registry declaration. No `Union[T, List[T]]` branching inside the operator.
- [ ] **OPR16.** Unit-test file covers happy path + every variant + every mismatch case + lineage extension + determinism + cross-asset robustness. Registry / validator tests confirm registration. Workflow-integration test exists *or* an ADR-backed bootstrap exception names the timeline for the first consumer.
- [ ] **Registry**: operator is reachable from the workflow executor.
- [ ] **AC6.** Commit message ends with `Operationalises: P3, P9; OPR1, OPR4, OPR6, OPR9, OPR10, OPR11; AC1, AC3, AC5, AC6.` (adjust IDs to whichever apply).

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.1 | 2026-05-17 | Pre-canonical corrections aligned with the README v1.1 revisions: (a) "three-file folder" → "four-file folder". (b) Step 3 YAML template — replaced wrong `method_family` enum values with the live `OperatorMethodFamily` Literal (`alignment, arithmetic, masking, windowing, aggregation, ranking, mapping, trade_construction, trade_evaluation, trade_summary`); removed `valid_range` from the defaults shape (`OperatorDefault` has `extra="forbid"` and only accepts `value`/`source`/`rationale`/`valid_values`). (c) Step 5 `operator.py` template — corrected lineage API to `Lineage.append(OperatorStep.build(name=..., version=..., params=..., input_hashes=..., auxiliary_lineages=...))`; corrected params construction to explicit field-by-field resolution; added "default-resolution pitfall" callout. (d) Step 9 promotion-rule verification — added the **bootstrap exception** path with ADR backing for new-asset-class / new-archetype work. (e) PR review checklist — updated OPR4/OPR6/OPR7/OPR10/OPR11/OPR15/OPR16 items to reflect the revised principles (bootstrap path, asset-class-blind framing, design-locked-constant allowance, correct lineage API, registry-declared arity, bootstrap test allowance). | (pending) |
| v1 | 2026-05-17 | Initial runbook for adding a new operator. Replaced by v1.1 the same day after a factual-review pass aligned with the README's corrections. | — |
