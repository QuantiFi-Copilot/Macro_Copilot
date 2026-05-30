# Operator

> The contract every operator in the Macro Copilot platform must satisfy — what makes something an operator at all, what makes it *standard*, and the design principles that govern when to build one (and when to compose existing ones instead). **Finance-blind by design, absolutely** — the operator layer carries no instrument knowledge, no asset-class enums, no domain references, and no finance math. Operators are the pure structural and statistical transformations that domain primitives feed into and that workflow DAGs compose.

**Version:** v2.0
**Last reviewed:** 2026-05-30
**Status:** load-bearing component contract. Changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Operationalises principles:** P1 (future-proofed), P3 (consistency by contract), P4 (determinism), P6 (no silent failure), P8 (closed-family discipline — operators consume and emit only closed-family artifact types), **P9 (finance-blind operator boundary — this contract is the operational expression of P9 at the operator layer, now enforced without exception)**, P10 (single source of truth).
**See also:** [`runbook.md`](runbook.md) — the procedure for adding a new operator. [`BUILD_GUIDE.md`](BUILD_GUIDE.md) — the end-to-end build manual (forthcoming; written alongside the first v2 reference operator). [`../artifact/README.md`](../artifact/README.md) — the **co-equal** artifact contract (ART1–ART16); an operator cannot be standard unless the artifact types it consumes and emits are standard, so the two contracts are hardened in lock-step.

> **v2.0 is a foundational reset, not an incremental edit.** v1.x was written by *describing the operators that already existed* — it codified the shape of a catalogue built to serve two specific workflow recipes (event studies and a paused backtest). v2.0 re-derives the contract from first principles and from five architectural decisions (recorded in [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md)), so that operators become the rock-solid, set-in-stone ingredient the DAG-composition layer is built on. Where a v1.x rule encoded an accident of the old catalogue, v2.0 supersedes it; the change is called out in each principle.

---

## What this folder is

The contract every operator must satisfy. Operators are the **L3 finance-blind structural / statistical transformation layer**: they consume typed artifacts produced by primitives or by other operators, apply a single structural or statistical method (alignment, masking, windowing, aggregation, arithmetic, a pairwise statistical relationship, a single-series transform, a cross-sectional reduction, a unit conversion), and emit a typed artifact downstream. They are the *only* place in the platform where pure, finance-blind compute over typed artifacts lives.

This document is organised as **principles, not archetypes** — same as the [primitive contract](../primitive/README.md). The shapes you see in code are *emergent* from the principles. The 16 numbered principles (**OPR1–OPR16**) are the operator-specific analog of the primitive's PR-numbers, grouped in the same four bins: definitional (OPR1–OPR3, *is this actually an operator?*) → admission (OPR4–OPR6, *should this be built?*) → standardness (OPR7–OPR11, *what does "standard operator" mean operationally?*) → operational (OPR12–OPR16, *the build conventions every standard operator follows and the gate that enforces them*).

Four load-bearing ways operators differ from primitives (unchanged from v1, restated because they anchor everything below):

1. Operators are **finance-blind** (the single most important rule, now absolute — see OPR6).
2. Operators are **pure functions over typed artifacts** — no `engine`, no DB, no I/O (OPR14).
3. Operators **extend** lineage rather than **originate** it — they append an `OperatorStep` to an existing chain (OPR10).
4. Operators consume and emit only the **closed-family artifact types** (OPR9), whose contract is owned by [`../artifact/README.md`](../artifact/README.md).

## What an operator *is* — the universal contract

Every operator is a folder under `shared/operators/<operator_name>/` containing exactly four files:

```
shared/operators/<operator_name>/
  __init__.py     # public-API re-exports: CONFIG_PATH, <operator>, <Operator>Params, <Operator>Error  (all four, always)
  config.yaml     # operator meta (name, method_family, version) + defaults + methodology
  schemas.py      # Pydantic <Operator>Params
  operator.py     # def <operator_name>(<artifact inputs>, params=None, config=None) -> <Output Artifact>
```

The canonical signature — **no exceptions, enforced by the registry meta-test (OPR16)**:

```python
def <operator_name>(
    <artifact_input_1>: <ArtifactType>,
    <artifact_input_2>: Optional[<ArtifactType>] = None,   # only for optional slots
    ...,
    params: Optional[<Operator>Params] = None,             # ALWAYS Optional, ALWAYS defaults to None
    config: Optional[OperatorConfig] = None,
) -> <OutputArtifactType>:
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)   # name AND version (OPR12)
    if params is None:
        params = <Operator>Params(                          # self-resolve every YAML-backed default (OPR8)
            <variant>=config.default_value("<variant>"),
            ...,
        )
    _validate_structural_metadata(<inputs>, params=params)  # units/frequency/missingness (OPR11)
    payload = _apply_<operator_name>(<inputs>, params)      # pure transform, no I/O (OPR14)
    step = OperatorStep.build(
        name=_OPERATOR_NAME, version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(params.model_dump()),   # NaN/Inf -> None (OPR10)
        input_hashes=(primary.lineage.head_hash,),
        auxiliary_lineages=tuple(aux.lineage for aux in <non_primary_inputs>),  # uniform N-ary (OPR10)
    )
    return <OutputArtifactType>(payload=payload, lineage=primary.lineage.append(step), ...)
```

**`params` is ALWAYS `Optional[...] = None`** (OPR8). An operator must be callable with no params and resolve every default from its `config.yaml`. This is the most common authoring and execution path; v1 had four operators that broke it.

**No `engine`.** Operators do not touch the database. They transform what primitives already produced.

**Inputs and outputs are typed artifacts** from the **closed family** (OPR9): `Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`, and `ScalarMetric`. (`TradeSet` was removed from the operator-composable family in v2.0; see *Removed from the operator layer* below.)

**Errors raise one family** (OPR13): `<Operator>Error`, a subclass of `ValueError`. Operators never return `{"error": ...}` envelopes and never leak raw pandas / numpy / pydantic / lineage exceptions; envelope conversion happens once at the transport boundary.

## What an operator is *not*

- **Not finance-aware — ever.** No instrument, curve_family, tenor, asset class, P&L, Sharpe, day-count, or financing math. A candidate that needs any of these is a **primitive**, not an operator. (v2.0 removed the v1 trade-operator carve-out — see OPR6.)
- **Not a data reader.** No `engine`, SQL, network, or file I/O (except the bundled `config.yaml` at import).
- **Not a workflow.** One structural/statistical transformation. A sequence is a workflow template (L4/L5).
- **Not a UI / explanation step.** Operators emit typed artifacts, never prose or formatted output.
- **Not lineage-originating.** Operators append an `OperatorStep`; primitives originate via `PrimitiveStep`.
- **Not a unit converter in disguise.** Operators refuse cross-unit operations; the *only* place a unit transition happens is the dedicated `convert_units` operator (OPR11).

## How to use this document

First read: scan the **Quick index**, then every principle's **Rule** line. Cite by ID (`OPR6`, `OPR9`) in commits, PR comments, and review. The four namespaces are separate: P (platform-wide), PR (primitive), OPR (operator), ART (artifact).

## Quick index — the OPR-numbers

| ID | Group | Principle | One-line rule |
|---|---|---|---|
| **OPR1** | I | Structural-method ownership | An operator owns one finance-blind structural/statistical method family. It never owns a finance concept. |
| **OPR2** | I | One operator, one method family | One cohesive method family with its consequential variants as parameters; unrelated families split. |
| **OPR3** | I | Shared-folder residence | Lives at `shared/operators/<name>/`. Never under an agent. |
| **OPR4** | II | Toolbox admission (parsimony, no recipe gate) | Admit an operator when it adds a distinct, reusable, finance-blind method the composition layer needs and that existing operators do not compose cleanly. **The v1 ≥3-archetype promotion gate is removed.** |
| **OPR5** | II | Method novelty | A genuinely new structural/statistical method or variant, not a one-off or a rename. |
| **OPR6** | II | Finance-blind contract (absolute) | No asset-class concept and no finance math, anywhere. A finance-aware candidate is relocated to a primitive — no carve-out. |
| **OPR7** | III | Defaults offloading | Every user-choosable default in `config.yaml`; structural invariants in code; one bounded design-locked-constant allowance. |
| **OPR8** | III | Uniform, bounded parameter surface | `params: Optional[<T>Params]=None` for every operator; every consequential variant exposed; every default config-resolved when `None`. |
| **OPR9** | III | Typed I/O via structured SlotDescriptor | Inputs/outputs are closed-family artifacts declared by a structured `SlotDescriptor` (type, list, optional, scalar, unit-family, sub-kind). No naked pandas/numpy/dict. |
| **OPR10** | III | Lineage extension (uniform, finite, deterministic) | One `OperatorStep` via `.build`; all non-primary inputs in `auxiliary_lineages`; NaN/Inf params sanitised to `None`. |
| **OPR11** | III | Structural-metadata algebra (units/frequency/missingness) | Refuse cross-unit (convert via `convert_units` only); frequency is load-bearing; uniform `require_matching_*` on every multi-artifact operator; honest combined lenient policy. |
| **OPR12** | IV | Config identity + source taxonomy | A shared helper asserts config `name` **and** `version` match the module; `source` is a closed taxonomy; `version` is semver. |
| **OPR13** | IV | One error family, owns every failure surface | Every `<Operator>Error` subclasses `ValueError`; `OperatorConfigError` subclasses `ValueError`; `NotImplementedError` is the sole sanctioned non-ValueError; no raw library exceptions leak. |
| **OPR14** | IV | Purity + determinism | Pure function; idempotent `head_hash` on rerun; `±Inf` forbidden in payloads; meaningless params normalised before hashing; version-bump rule. |
| **OPR15** | IV | Arity declared in the registry | Single / list / optional / scalar slots declared via `SlotDescriptor`; signature mirrors it; no internal fan-out; no by-name executor special-casing. |
| **OPR16** | IV | Test pattern + the registry-consistency gate | A parametrized meta-test over `OPERATOR_REGISTRY` is the gate; plus unit (happy/variant/mismatch/lineage/determinism/non-rates) and workflow-integration tests. |

---

## Group I — Definitional: is this actually an operator?

### OPR1 — Structural-method ownership

**Rule.** An operator owns one *finance-blind structural or statistical method family*. The families are enumerated in `OperatorMethodFamily` (`shared/config/operator_config.py`) and cover the structural transforms *and* the statistical relationships a research DAG needs (see *Method-family taxonomy* below). An operator never owns a finance concept.

**Why.** Reusability across asset classes (P9). `correlation` runs on yields, FX, equities, or temperatures unchanged because correlation is a statistical operation, not a finance one. The moment an operator carries a finance concept, the cross-asset claim collapses and the operator is a hidden primitive.

**Verify.**
- The operator's `config.yaml` `operator.method_family` is one of the `OperatorMethodFamily` values.
- Name, signature, config, and docstrings describe a structural/statistical method, never an instrument, asset class, curve_family, tenor, yield, spread, rate, P&L, or Sharpe.
- A reviewer can state the operator in one structural sentence: *"`correlation` computes the (optionally rolling) Pearson correlation between two index-aligned Series."*

**Anti-patterns.**
- `calculate_swap_spread`, `find_curve_inversions` — finance. Relocate to a primitive.
- `evaluate_trades`, `summarize_trades` — finance math (P&L, Sharpe). **Removed in v2.0** (OPR6).
- `correlate_btp_bund` — instrument-named. The operator is `correlation`, parameterised by the two Series it is handed.

**Exceptions.** None.

**Relates to.** [P9](../../00_thesis/01_non_negotiables.md); operator analog of [PR1](../primitive/README.md).

### OPR2 — One operator, one method family

**Rule.** One cohesive method family, with its consequential variants exposed as parameters (`correlation` exposes `method ∈ {pearson, spearman}` and an optional `window`; both are variants of *correlation*). Unrelated families split into separate operators.

**Why.** Composition-layer clarity and reviewer sanity. An operator that does two things is two operators in a trench coat; a DAG author (and the validator) cannot reason about which mode is active.

**Verify.**
- `methodology.what_it_does` describes one family in one paragraph, no "or".
- No `mode` parameter switches between *different families*.
- The output artifact type is constant across all parameter values.

**Anti-patterns.**
- `transform_series` that aligns *or* thresholds *or* correlates by a flag. Three operators.
- An operator whose output artifact type changes with a param.

**Exceptions.** None.

**Relates to.** Operator analog of [PR2](../primitive/README.md).

### OPR3 — Shared-folder residence

**Rule.** Every operator lives at `shared/operators/<operator_name>/`. Never under an agent, never under `shared/analytics/`, never under a domain subdirectory.

**Why.** Structural enforcement of OPR6 + [P11](../../00_thesis/01_non_negotiables.md): an operator under an agent's folder would be either domain-coupled (P9 violation) or misplaced (P11 violation).

**Verify.**
- The folder path begins with `shared/operators/`.
- `grep` for `from rates_agent` / `from fx_agent` / any agent package inside any operator file returns zero matches.
- `shared/analytics/` imports, if any, are finance-blind helpers only (`stats.py` yes; `rates_fetch.py` no).

**Anti-patterns.** An operator under `rates_agent/operators/`; an operator importing an agent's tools; a "shared but rates-only" operator (no such thing).

**Exceptions.** None.

**Relates to.** Operator analog of [PR3](../primitive/README.md); [P9](../../00_thesis/01_non_negotiables.md), [P11](../../00_thesis/01_non_negotiables.md).

---

## Group II — Admission: should this be built?

### OPR4 — Toolbox admission (parsimony, no recipe gate)

**Rule.** An operator is admitted to `shared/operators/` when **both** hold:

1. **Distinctness / parsimony.** It adds a genuinely distinct finance-blind structural or statistical method that existing operators do not compose *cleanly* (the composability check still applies — do not add an operator whose effect is one trivial existing chain), measured against the five criteria: accuracy, efficiency, interpretability, provenance, **DAG-composition clarity**.
2. **Toolbox membership.** It belongs to the target finance-blind composition toolbox — a pairwise relationship, a single-series transform, a cross-sectional reduction, a structural transform, or a unit/shape conversion — that the LLM-composed DAG layer ("Tier 2") will draw on.

**The v1 promotion rule — "admit only when reused across ≥3 workflow archetypes" — is REMOVED.** That gate was an artifact of the recipe-driven era (Tier 1 templates) and it actively *blocks* building the toolbox the composition layer needs: a `correlation` operator can never reach three template consumers because no template exists to use it until it exists. Operators are the *ingredients* for open composition; gating ingredient creation on pre-existing recipes is backwards.

**Why.** The product goal is open DAG composition over a rich, finance-blind operator catalogue. The constraint that keeps the catalogue sane is no longer "≥3 recipes" but **distinctness + toolbox-membership + a hard finance-blind boundary (OPR6)**. The catalogue should still be small enough to hold in one's head (target ≈ 25–30 operators; see *Method-family taxonomy*), but it must be *complete enough* that common research DAG nodes (correlation, covariance, cointegration, rolling z-score, cross-sectional rank, …) exist.

**Verify.** The PR description answers three questions:
1. **Composability check.** What existing-operator composition was considered, and why is it materially worse on ≥1 of the five criteria (or impossible)?
2. **Toolbox-membership.** Which target category does it fill, and what DAG node does it enable that is impossible today?
3. **Non-overlap.** Run `name` + `methodology.what_it_does` past every existing operator; if an existing one's variant set could cover it, *extend that operator* instead.

**Anti-patterns.**
- Re-introducing a "≥3 archetypes" or "used by only one workflow → reject" argument. That gate is gone in v2.0.
- Building `correlation_pearson`, `correlation_spearman` as two operators (one operator, `method` variant).
- A one-off transform that exists for a single template's internal convenience — that stays workflow-local code, not because of a recipe count but because it is not a distinct reusable method.
- Speculative breadth with no target-category justification.

**Exceptions.** None. (The v1 "bootstrap exception" is obsolete — without the ≥3 gate there is nothing to bootstrap around.)

**Relates to.** Operator analog of [PR4](../primitive/README.md), with the promotion gate removed; [P8](../../00_thesis/01_non_negotiables.md) (the artifact closed family is still closed — admitting an operator never admits an artifact type).

### OPR5 — Method novelty

**Rule.** A new operator is a genuinely new structural/statistical method or a new variant inside an existing family — not a new finance use case for an existing method, and not a rename.

**Why.** The common mis-classification is reaching for a new operator when the right answer is a new parameter value on an existing one (`correlation` gains `method="spearman"`, not a new operator).

**Verify.**
- The PR names the method family (existing, or new with a one-paragraph justification).
- A reviewer can name an existing operator whose `method_family + variants` could *not* cover the use case under any parameter value.

**Anti-patterns.** `feat: add align_series for FX` (already finance-blind); an operator whose `what_it_does` is "same as `<x>` but for `<y>`"; a diff that is only a different default value.

**Exceptions.** None.

**Relates to.** Operator analog of [PR5](../primitive/README.md).

### OPR6 — Finance-blind contract (absolute)

**Rule.** Operators contain **no asset-class concept and no finance math, anywhere** — not in the signature, the code, the config, the imports, the docstrings, or the output. The decision test: *"Would this operator's code change if I swapped rates inputs for FX or equity inputs?"* If yes, it is a primitive, not an operator. **There is no carve-out.** A candidate that performs P&L accounting, Sharpe annualisation, day-count math, financing logic, or any other asset-class-specific computation is relocated to the primitive layer — it does not live in `shared/operators/` under any label.

**v2.0 supersedes the v1 "asset-class-blind, refined" framing.** v1 blessed `evaluate_trades` / `summarize_trades` as "sanctioned finance-aware trade operators." That was a description of an accident, not a principle. v2.0 restores the absolute boundary: finance-blind means finance-blind. (Founder decision #1; [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md).)

**Why.** P9 is the precondition for cross-asset portability *and* for a composition layer the validator can trust. A "mostly finance-blind" layer is not finance-blind; one finance-aware operator forces every future asset class to special-case it, and a DAG validator cannot reason uniformly about a catalogue with exceptions. The boundary is binary on purpose.

**Verify.**
- The signature contains no asset-class parameter (`curve_family`, `tenor`, `currency_pair`, `sector`, `issuer`) and no finance-math parameter (`financing`, `day_count`, `notional` used for P&L).
- The code does not branch on asset class and performs no finance math.
- `grep` for agent packages inside the operator returns zero matches.
- A non-rates test (Z_SCORE / random-walk / temperature input) passes and produces structurally-correct output (OPR16 requires this for every operator).
- Generic *structural* concepts that are asset-class-agnostic (a window length, a lag, a join policy, a correlation method) are fine; finance *semantics* are not.

**Anti-patterns.**
- Any P&L / Sharpe / financing / day-count math in an operator. → primitive.
- `<Operator>Params` with `curve_family` / `tenor` / an asset-class enum.
- A docstring describing the operator in finance terms when structural terms suffice.
- Re-admitting a finance-aware operator "because the backtest template needs it" — the backtest capability is a primitive set (or a template), not an operator.

**Exceptions.** None.

**Removed in v2.0.** `evaluate_trades` and `summarize_trades` (finance math) are removed from the operator layer. `construct_trades` and the `TradeSet` artifact are removed alongside them: `construct_trades` is structurally blind but exists only to feed the removed consumers, and `TradeSet` is produced/consumed only by the trade trio — so the three operators + `TradeSet` move together into the future finance-aware **backtest primitive set** (relocation decided in [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md); the backtest primitives land later as a separate workstream). Net operator count drops from 12 to **9**; the operator-composable artifact family drops `TradeSet`.

**Relates to.** [P9](../../00_thesis/01_non_negotiables.md); the primitive layer ([PR1](../primitive/README.md)) is where the relocated finance math belongs.

---

## Group III — Standardness: what does "standard operator" mean operationally?

### OPR7 — Defaults offloading

**Rule.** Every *user-choosable* methodology default (join policy, fill policy, threshold rule, aggregator, correlation method, window default, ddof) lives in `config.yaml`'s `defaults:` block with `{value, source, rationale, valid_values?}`. Mathematical and structural invariants stay in code (Pydantic `@model_validator`). Hidden methodology constants in `operator.py` are forbidden, with one bounded exception: **design-locked constants** (a contract-shape value the caller must *not* vary, e.g. a fixed summary anchor) are allowed in code iff (1) documented as design-locked with rationale, (2) stamped into lineage when output-affecting, (3) migration-scoped in the docstring, (4) genuinely structural, not a methodology choice in disguise.

**Why.** Two reasons, the same two that drive the primitive's [PR7](../primitive/README.md). **(1) Configurability / de-opinionation** — the `config.yaml` is where every method choice and default is *declared and offloaded* so the operator ships a sensible default *without being opinionated*, and the caller can override it to switch behaviour seamlessly. This is the operator analog of how a primitive offloads its methodology to YAML: we never bake one "right" method into the code as the only option. **(2) Disclosure** — a reviewer must be able to enumerate every methodology choice from the YAML alone. Design locks are the one principled exception, documented, not hidden.

**Verify.** Every numeric constant in `operator.py` is a mathematical truth, a documented design lock, or `config.default_value(...)`. The `defaults:` block lists every user-choosable choice with a non-empty `source` and `rationale`. (Numeric ranges live on the `<Operator>Params` field as `Field(ge=, le=)`, re-checked on the config path per OPR8 — `OperatorDefault` is `extra="forbid"` and has no `valid_range`.)

**Anti-patterns.** `_FFILL_LIMIT_DAYS = 5` undocumented at module level; magic numbers in the body; a "design lock" the caller would reasonably want to vary.

**Exceptions.** Design-locked constants per the four conditions.

**Relates to.** Operator analog of [PR7](../primitive/README.md); [P5](../../00_thesis/01_non_negotiables.md), [P10](../../00_thesis/01_non_negotiables.md).

### OPR8 — Uniform, bounded parameter surface

**Rule.** **Every operator declares `params: Optional[<Operator>Params] = None`** and, when `params is None`, self-resolves every YAML-backed default from `config.yaml` via `config.default_value(...)`. `<Operator>Params` exposes every consequential method variant as a typed field (`Literal[...]` for enums, constrained numerics for ranges); nothing material is hidden in code. Each parameter is **either** a Pydantic schema-default **or** YAML-authoritative (`None`-in-schema + config-resolved) — never both, never silently divergent. Bounded-range params are re-validated to the full `[min,max]` on *both* the params path and the config path via a shared resolve-and-revalidate helper.

**Methods are baked in, switchable, and refuse cleanly — the same de-opinionated philosophy as primitives.** Where an operator family supports multiple methods (`correlation.method ∈ {pearson, spearman}`, `conditional_aggregate.aggregator ∈ {mean, median, max, …}`, `threshold_events.rule ∈ {one_sided, two_sided}`), **every supported method is implemented in the operator's Python** and selected by a typed param; `config.yaml` declares the valid set and the default. The caller switches methods seamlessly by setting the param, or omits it for the YAML default — the operator is **never opinionated** about which method is *the* method. A method **declared in the valid set but not yet built must refuse cleanly** (`raise NotImplementedError(...)` pointing at `methodology.planned_extensions`) and **never silently fall back** to another method. This is the operator analog of the primitive's [PR11](../primitive/README.md) honest-refusal rule.

**v2.0 supersedes v1's non-uniform surface.** v1 had four operators (`construct_trades`, `rolling_regression`, `select_from_series_set`, `threshold_events`) declaring `params` *required with no default*; the executor omits the `params` kwarg on an empty node, so those four `TypeError`-ed on the most ordinary template path. That is a live ABI break, fixed by this rule.

**Why.** Operators must be independently callable with zero arguments and resolve from config — that is the contract the executor and every test depend on. A bounded, uniform input surface is also what lets the composition layer (and its eventual `OperatorCard`) describe an operator mechanically.

**Verify.**
- `inspect.signature(callable).parameters['params'].default is None` for **all** operators (asserted by the OPR16 meta-test).
- Calling the operator with only its artifact inputs (no `params`, no `config`) succeeds and matches the config defaults.
- No `<Operator>Params` field is both a schema-default and a config key contesting the same value.
- A bounded int (e.g. a window) is rejected out-of-range whether supplied via params or resolved from config.

**Anti-patterns.**
- `params: <T>Params` (required, no default) — the v1 ABI break.
- A param that is config-resolvable on one path and a hardcoded Pydantic default on the other (v1 `conditional_aggregate.min_n`).
- A range enforced on the params path but not the config path (v1 `holding_window_days`).

**Exceptions.** None.

**Relates to.** Operator analog of [PR8](../primitive/README.md), reframed: primitives expose one *central knob* for the LLM; operators expose the *full variant set* for the DAG author, but with the same "nothing hidden, uniform signature" discipline.

### OPR9 — Typed I/O via structured `SlotDescriptor`

**Rule.** Every input slot and the output are declared in the registry by a structured **`SlotDescriptor`**, not a bare class-name string. A `SlotDescriptor` carries: `element_type` (a closed-family artifact type), `is_list`, `optional`, `scalar_ok` (+ `scalar_value_type`), `expected_unit_family` (or `any`), and a `sub_kind` where the artifact type alone is ambiguous (e.g. `Panel.sub_kind`; a `SeriesSet` `key_schema ∈ {fixed:[...], derived_from_input}`). The function signature mirrors the descriptors. No naked `pd.DataFrame` / `pd.Series` / `np.ndarray` / `dict` / prose crosses the public boundary; pandas/numpy may be used *inside* the body only.

**v2.0 supersedes v1's bare class-name slots.** v1 declared slots as strings (`"Series"`, `"List[Series]"`), so edge-compatibility was class-name-only — the validator had zero visibility into units, frequency, missingness, `SeriesSet` keys, or `Panel` sub-shape, and many pairs that *looked* composable broke at runtime. The structured descriptor is what makes the type algebra airtight enough to compose on.

**Why.** Typed artifacts make operators composable; the *structured* descriptor makes them *verifiably* composable before execution — the missing prerequisite for Tier-2 DAG validation.

**Verify.**
- Every `input_slots` entry and the `output_type` is a `SlotDescriptor` over a closed-family type.
- `inspect.signature(callable)` params == slot keys ∪ `{params, config}` ∪ declared positionals (OPR16 meta-test).
- The validator checks the descriptor's expectations (unit family, sub-kind, list/optional/scalar) — not just the class name.

**Anti-patterns.** A `pd.DataFrame` input; a `dict` output; a bare-string slot; a new artifact-shaped class invented locally (that is an ART4 closed-family decision).

**Exceptions.** None.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md); OPR15 (the descriptor *is* the arity declaration); [ART2/ART9](../artifact/README.md) (the closed family it draws on).

### OPR10 — Lineage extension (uniform, finite, deterministic)

**Rule.** Every operator appends **exactly one** `OperatorStep` via `OperatorStep.build(name, version, params, input_hashes, auxiliary_lineages)` and returns the output built on `primary_input.lineage.append(step)`. **All non-primary artifact inputs' chains go into `auxiliary_lineages`** — uniformly, including N-ary/list operators (no stashing provenance only on a payload field). `params` is the operator's resolved params `model_dump()`, passed through `sanitize_params_for_lineage` so that any non-finite computed float (NaN/Inf) becomes `None` (which is JSON-canonical) before hashing.

**v2.0 supersedes v1's divergent mechanics.** v1 let `align_series` (the only N-ary operator) carry per-key provenance on a payload field instead of `auxiliary_lineages`, hand-built `step_params` per operator, and had no NaN-sanitisation — so `summarize_series` *crashed* on its default path (NaN into params → the lineage layer's NaN-rejection raised a bare `ValueError`). The sanitiser + uniform mechanism fix this.

**Why.** [P4](../../00_thesis/01_non_negotiables.md). A single provenance walker must recover every input chain uniformly; the hash must be deterministic and never crash on legal compute output.

**Verify.**
- Output lineage length == input length + 1; head step `name`/`version` match the operator (OPR16 + an executor post-call assertion).
- Head step `auxiliary_lineages` count == (number of artifact input slots − 1).
- No raw computed float reaches `step_params` without passing the sanitiser.
- Rerun on identical inputs yields an identical `head_hash` (OPR14).

**Anti-patterns.** Not appending (length unchanged); replacing the chain (losing upstream); calling `OperatorStep(...)` directly instead of `.build`; an N-ary operator skipping `auxiliary_lineages`; a NaN/Inf in `step_params`.

**Exceptions.** None.

**Relates to.** [P4](../../00_thesis/01_non_negotiables.md); [ART9/ART10](../artifact/README.md) (the lineage-integrity guards live at the artifact layer and back this rule).

### OPR11 — Structural-metadata algebra (units, frequency, missingness)

**Rule.** Operators enforce a single, uniform algebra over the three structural-metadata axes:

- **Units — refuse, do not coerce.** An operator never silently converts units (no in-operator `PERCENT→BPS ×100`). Cross-unit operations raise `<Operator>Error`. The **only** place a unit transition happens is the dedicated **`convert_units`** operator, which a DAG must explicitly include. Every operator declares its output-unit propagation rule on the registry (passthrough / per-key passthrough / `COUNT` / `Z_SCORE` / `RATIO` / "from input") so the validator can track units across a whole chain. The "refuse cross-unit" rule applies to **unit-coherent** operations (arithmetic — BPS + PERCENT is meaningless); **unit-invariant** operators (a dimensionless statistic like `correlation`, a rank, a z-score) impose **no** same-unit requirement — they accept cross-unit inputs, record both inputs' units in lineage for provenance, and declare a fixed output unit (`RATIO` / `Z_SCORE` / …). *(Founder decision #4.)*
- **Frequency — load-bearing.** Frequency is derived deterministically when data enters the typed-artifact world (the adapter bridge — see [ART8](../artifact/README.md)), not left `None`. Every operator that consumes **two or more** artifacts exposes `require_matching_frequency: bool = True` (strict) with identical naming/semantics, and an `'irregular'` value disambiguates event-derived series from `None`-unknown. *(Founder decision #3.)*
- **Missingness — uniform + honest.** Every multi-artifact operator exposes `require_matching_missingness: bool = True`. Under an explicit opt-out, the operator emits a **combined/honest** policy reflecting *all* inputs (never silently "keep left's"), records the relaxation in `step.params`, and preserves **per-key** policy for every `SeriesSet` it produces. Any imputation an operator performs is recorded in `step.params` *and* reflected in the output's `missingness_policy` (never mislabelled `RawNoCleaning`).

**v2.0 supersedes v1's contradictory per-axis behaviour** (only one operator refused cross-unit; two silently ×100'd; frequency checks were no-ops because the tag was always `None`; lenient missingness dropped one input's policy; an operator did `fillna(0)` while labelling output raw).

**Why.** This algebra is the operator layer's safety net and the reason typed artifacts beat naked pandas. The composition layer *will* try to combine a monthly CPI series with a daily rates series, or add a BPS series to a PERCENT one — and silent wrong numbers destroy trust ([P2](../../00_thesis/01_non_negotiables.md), [P12](../../00_thesis/01_non_negotiables.md)). Strict-by-default + explicit, lineage-recorded opt-out surfaces every compatibility decision where a reviewer can see it.

**Verify.**
- Every multi-artifact operator exposes `require_matching_frequency` *and* `require_matching_missingness`, both defaulting `True`.
- A cross-unit operation raises `<Operator>Error`; no operator multiplies to change units.
- `convert_units` exists and is the sole unit-transition operator.
- Adapter-derived `frequency` is populated on production artifacts (not `None`); a frequency mismatch raises in strict mode.
- An opt-out emits a combined policy and records the relaxed flag in `step.params`; a `SeriesSet` producer preserves per-key missingness.

**Anti-patterns.** Silent unit conversion; a frequency check that can never fire because the tag is unset; an operator missing one of the two `require_matching_*` flags; lenient mode dropping an input's policy; `fillna` labelled `RawNoCleaning`.

**Exceptions.** Single-input operators expose only the metadata flags relevant to their one input.

**Relates to.** Operator analog of [PR11](../primitive/README.md); [ART8/ART12](../artifact/README.md) (units/missingness closed enums + frequency derivation); [P2](../../00_thesis/01_non_negotiables.md), [P12](../../00_thesis/01_non_negotiables.md).

---

## Group IV — Operational: build conventions + the gate

### OPR12 — Config identity + source taxonomy

**Rule.** Every operator calls a shared `_check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)` that asserts **both** `config.operator.name == _OPERATOR_NAME` **and** `config.operator.version == _OPERATOR_VERSION`. `config.operator.version` is semver-validated. `default.source` is drawn from a **closed taxonomy** (a `Literal`), reconciled to one spelling (the v1 `methodology_judgement_pending_review` vs `team_judgment_pending_review` drift is resolved to the canonical primitive-side spelling).

**v2.0 supersedes v1** where 0/12 operators cross-checked the config version, three trade operators omitted even the name guard, and `source` was a free string that had already drifted.

**Why.** `_OPERATOR_VERSION` is folded into the lineage hash; if the YAML version can diverge silently, replayed identities are wrong. A missing name guard lets an operator silently read a foreign config's defaults.

**Verify.** The OPR16 meta-test asserts `config.operator.name == registry key`, `config.operator.version == module._OPERATOR_VERSION`, and `source ∈` the closed taxonomy, for every operator.

**Anti-patterns.** Name-only guard; no guard; free-string `source`; non-semver version.

**Exceptions.** None.

**Relates to.** Operator analog of [PR12/PR13](../primitive/README.md); [P4](../../00_thesis/01_non_negotiables.md).

### OPR13 — One error family, owns every failure surface

**Rule.** One error taxonomy, set in stone (founder decision #2):

1. Every `<Operator>Error` subclasses `ValueError`.
2. `OperatorConfigError` subclasses `ValueError` (v1 subclassed bare `Exception`).
3. `NotImplementedError` is the **single sanctioned** non-`ValueError` exception, used uniformly for declared-but-unbuilt variants.
4. **An operator owns *all* failure surfaces for its declared inputs.** It must pre-validate or wrap so that no raw `pandas` / `numpy` / `pydantic` / `lineage` exception escapes on any input the descriptor admits. (v1 leaked raw library exceptions from ≥6 operators on reachable inputs.)
5. The workflow executor **preserves** the original typed `<Operator>Error` (via `__cause__` *and* by surfacing the type), rather than flattening everything to a generic `WorkflowExecutionError`.

**Why.** [P6](../../00_thesis/01_non_negotiables.md). The composition layer must catch failures uniformly and report them specifically — *"node 3 (`correlation`) failed: the two series do not overlap in time"*, not *"something went wrong."* Three escaping exception families and a flattening executor make honest, specific refusal impossible.

**Verify.** A parametrized test asserts every `<Operator>Error` and `OperatorConfigError` is a `ValueError` subclass; degenerate-input tests assert the operator raises its *own* typed error (not a raw library error) on every category in *Decided edge-case behavior*; the executor test asserts the typed error survives.

**Anti-patterns.** `return {"error": ...}` inside an operator; a bare `raise ValueError(...)`; a raw pandas/numpy/pydantic exception escaping; `OperatorConfigError(Exception)`; the executor erasing the type.

**Exceptions.** `NotImplementedError` for unbuilt variants only.

**Relates to.** [P6](../../00_thesis/01_non_negotiables.md); operator analog of [PR11](../primitive/README.md) (operators always raise — no envelope path).

### OPR14 — Purity + determinism

**Rule.** Operators are pure: no DB, network, filesystem (except the bundled config at import), wall-clock, unfixed random, or global mutation. Determinism is pinned: (a) `op(x) == op(x)` produces an identical `head_hash` — a **rerun-equality test is mandatory for every operator** (v1 had it for 3/12); (b) **`±Inf` is forbidden in any payload** (it survives the numeric-dtype check but detonates at JSON persistence — caught at construction instead, see [ART11](../artifact/README.md)); (c) meaningless param combinations are normalised before hashing (a `fill_limit` that is ignored under `fill_policy="raw"`, a `period` ignored for a binary op) so identity tracks content; (d) **version-bump rule** — any behavioural change increments both `_OPERATOR_VERSION` and `config.operator.version`, since the hash folds version but not code.

**Why.** [P4](../../00_thesis/01_non_negotiables.md). A 12-node DAG is replayable iff every node is deterministic and its identity tracks its content.

**Verify.** Purity grep (no `engine`, `datetime.now`, `time.time`, unfixed `random`, `open(`); rerun-equality test green for every operator; an `Inf`-in-payload construction raises; a behavioural change without a version bump is caught in review.

**Anti-patterns.** Any I/O or clock/random in the compute path; `Inf` in a payload; identity finer than content; a behaviour change without a version bump.

**Exceptions.** None.

**Relates to.** Operator analog of the primitive compute-purity discipline; [P4](../../00_thesis/01_non_negotiables.md); [ART10/ART11](../artifact/README.md).

### OPR15 — Arity declared in the registry

**Rule.** Every operator's arity is declared in `OPERATOR_REGISTRY` via the `SlotDescriptor` set (OPR9). Slots are explicitly **single**, **list** (`is_list`), **optional** (`optional`), or **scalar** (`scalar_ok` + `scalar_value_type`) — these are *distinct* declarations, not one overloaded flag (v1 overloaded `accepts_scalar_input` to mean both "scalar" and "optional"). The Python signature mirrors the declaration. No internal fan-out (`for x in xs` where `xs` is not a declared list slot). **No operator is special-cased by name in the executor** — any discriminator/positional argument (e.g. an arithmetic `op`) is a declared registry field resolved from params, so the executor stays generic.

**Why.** Type-algebra clarity and a generic executor. A DAG author and the validator read arity off the registry; the executor must never branch on a specific operator name.

**Verify.** The meta-test asserts the signature mirrors the slot descriptors and that optional/scalar are declared distinctly; `grep` for an operator name inside `executor.py` returns zero matches.

**Anti-patterns.** `Union[T, List[T]]` branching inside the body; one flag meaning both optional and scalar; a `if node.operator_name == "..."` block in the executor.

**Exceptions.** None.

**Relates to.** OPR9 (the descriptor); the executor ABI.

### OPR16 — Test pattern + the registry-consistency gate

**Rule.** Two layers, with the meta-test as the cornerstone:

1. **The registry-consistency meta-test (the gate).** One parametrized test over `OPERATOR_REGISTRY` asserting, per operator: (a) `inspect.signature` params == slot keys ∪ `{params, config}` ∪ declared positionals; (b) `params` default is `None` (OPR8); (c) `<Operator>Error` subclasses `ValueError` (OPR13); (d) every slot/output type is in the single canonical closed-family enum (OPR9); (e) `__init__` exports `{CONFIG_PATH, <operator>, <Operator>Params, <Operator>Error}`; (f) `config.operator.name == registry key` and `config.operator.version == module._OPERATOR_VERSION` (OPR12); (g) the callable appends exactly one `OperatorStep` with matching name/version (OPR10). **This single test is the verifier** — it mechanically proves which contract clauses hold and red-flags every drift; "verify the audit findings" *is* "run this test."
2. **Per-operator tests:** unit (happy path; every variant; every structural-metadata mismatch raising `<Operator>Error`; lineage extension N→N+1; idempotent `head_hash` on rerun; a **non-rates** finance-blind case per OPR6); plus at least one **workflow-integration** test through a real template.

No SQL parity (operators have no DB); the workflow-integration test is the operator's parity layer.

**Why.** [P2](../../00_thesis/01_non_negotiables.md)/[P3](../../00_thesis/01_non_negotiables.md). The meta-test is what makes the contract *enforced* rather than aspirational — it is the gate every legacy-migration and new-operator PR must keep green. One file-naming scheme (`test_operator_<op>.py`); the operator/primitive `rolling_regression` name collision is resolved by renaming the primitive.

**Verify.** `pytest` green on the meta-test for all operators; each operator has its per-operator file with the layers above; the meta-test is wired into CI as a required gate.

**Anti-patterns.** A new or migrated operator not covered by the meta-test; per-operator tests on rates-only data; hand-enumerated variants where `parametrize` belongs; a red operator merged.

**Exceptions.** None.

**Relates to.** Operator analog of [PR15/PR16](../primitive/README.md); the meta-test is the operator equivalent of the primitive parity fixture.

---

## Method-family taxonomy (v2.0)

Operators have no methodology-depth buckets (that is a primitive axis). They have a `method_family` enum (`OperatorMethodFamily` in `shared/config/operator_config.py`). v2.0 **removes the trade families** (relocated to primitives, OPR6) and **adds the statistical/transform/conversion families** the composition toolbox needs:

| `method_family` | What the family does | Operators (●=exists, ○=target Step-4 build) |
|---|---|---|
| `alignment` | Combine N indexed artifacts onto a common index | ● `align_series` |
| `arithmetic` | Typed series add/sub/mul/div (unit-checked, refuse cross-unit) | ● `series_arithmetic` |
| `masking` | Apply a boolean/event mask to an indexed artifact | ● `apply_mask`, ● `threshold_events` |
| `windowing` | Extract windows around event timestamps | ● `event_windows` |
| `aggregation` | Reduce a windowed/panel/series artifact to a summary | ● `conditional_aggregate`, ● `summarize_series` |
| `statistical_relationship` | Pairwise relationships between two series | ● `rolling_regression`, ● `correlation`; ○ `covariance`, `cointegration`, `lead_lag` |
| `single_series_transform` | Per-series transforms | ○ `rolling_zscore`, `rolling_stat`, `percentile_rank`, `diff`, `cumulative`, `lag` |
| `cross_sectional` | Reduce/rank across a collection | ● `select_from_series_set`; ○ `cross_sectional_rank`, `cross_sectional_zscore`, `top_n` |
| `unit_conversion` | The **sole** unit-transition site (OPR11) | ○ `convert_units` |

`ranking` (an empty v1 family) is folded into `cross_sectional`. Target catalogue ≈ 25–30 operators — small enough to hold in one's head (OPR2), complete enough to cover the majority of research-DAG nodes.

## Decided edge-case behavior (set in stone)

The composition layer must never hit an *undefined* behaviour. Every operator implements these decisions; the OPR16 meta-test and per-operator degenerate-input tests enforce them. (These resolve the audit's "undecided edge cases", founder-approved defaults.)

| Scenario | Decided behavior |
|---|---|
| Empty input collection / zero events / empty mask result | **Valid sentinel** — emit a well-formed empty artifact of the declared output type. *Not* an error. (Exception: a zero-width window into `conditional_aggregate` **raises**.) |
| `params` omitted on any operator | Resolve every default from `config.yaml` (OPR8). Never `TypeError`. |
| Non-finite computed float (NaN/Inf) reaching `step.params` | Coerce to `None` via `sanitize_params_for_lineage` before `.build` (OPR10). |
| Duplicate / unsorted / non-`DatetimeIndex` on any artifact index | A typed construction-time failure — the shared artifact index validator ([ART11](../artifact/README.md)) raises, so operators never meet a malformed index. |
| `±Inf` produced into a payload (e.g. divide-by-zero) | **Forbidden at construction** ([ART11](../artifact/README.md)); the producing operation raises `<Operator>Error` (e.g. `series_arithmetic` Series/Series divide-by-zero raises, matching the scalar case — no asymmetry). |
| Cross-unit operation | Raise `<Operator>Error`; the caller must insert `convert_units` (OPR11). |
| Frequency / missingness mismatch on a multi-artifact operator | Raise in strict mode (default); explicit opt-out proceeds and records the relaxation + an honest combined policy in lineage (OPR11). |
| Lenient-missingness output | Combined/honest policy reflecting all inputs; per-key for `SeriesSet` (OPR11). |
| Scalar literal targeting a scalar slot | `SlotDescriptor.scalar_value_type` is numeric-only; `bool` is rejected (OPR9/OPR15). |
| `select_from_series_set` key absent from the upstream producer's key-set | Best-effort static check at validate-time for fixed-key producers (declared via `SlotDescriptor.key_schema`); runtime-only for derived-key producers, documented (OPR9). |

## Removed from the operator layer (v2.0)

| Removed | Reason | Destination |
|---|---|---|
| `evaluate_trades` | Finance math (P&L, financing, day-count) — OPR6 | Backtest **primitive** set (per ADR 0016; built later) |
| `summarize_trades` | Finance math (Sharpe annualisation) — OPR6 | Backtest **primitive** set |
| `construct_trades` | Structurally blind but orphaned without its consumers | Moves with the trade set |
| `TradeSet` artifact | Produced/consumed only by the trade trio | Primitive-output type; dropped from the operator-composable closed family ([ART2](../artifact/README.md)) |

## Worked example — `correlation` (the v2.0 reference)

`correlation` is the canonical v2.0 operator and the planned first reference build (Step 2 of the standardization plan).

- **OPR1/OPR6**: pure statistics. Consumes two `Series`; computes Pearson/Spearman correlation; runs identically on yields, FX, equities, temperatures. Zero finance references.
- **OPR2**: one family (`statistical_relationship`); variants `method ∈ {pearson, spearman}` and an optional `window` (full-sample vs rolling) are variants of *correlation*, not different families.
- **OPR8**: `params: Optional[CorrelationParams] = None`; `method` and `min_periods` resolve from `config.yaml` when omitted.
- **OPR9/OPR15**: input slots `left: Series`, `right: Series` (both required, single); output **always `ScalarMetric`** (the full-sample coefficient, in `RATIO` units) — one constant output type per OPR2. A rolling correlation is a *separate* operator emitting a `Series` (also constant), never a `window` flag on this one. Registered with bare-string slots today; the structured `SlotDescriptor` lands with the wholesale registry/executor ABI step (not half-converted here).
- **OPR10**: appends one `OperatorStep`; `right`'s chain goes in `auxiliary_lineages`; the resolved `method`/`window` are in `step.params`.
- **OPR11**: strict-by-default on frequency + missingness; **unit-invariant** — a correlation is dimensionless, so it accepts cross-unit inputs (correlating a BPS series with a PERCENT series is valid and meaningful), records both inputs' units in lineage, and outputs `RATIO`. It never converts units itself.
- **OPR13**: `CorrelationError(ValueError)` on non-overlapping indices, insufficient overlap (`< min_periods`), or a metadata mismatch — never a raw pandas error.
- **OPR14**: pure; rerun yields an identical `head_hash`; an all-constant input (zero variance → undefined correlation) **raises `CorrelationError`** — an undefined coefficient is a typed refusal, never a `NaN`/`Inf` value (which `ScalarMetric` would reject anyway).

It exercises every principle, fills a real toolbox gap (there is *no* correlation today), and — once built — produces the BUILD_GUIDE, the meta-test, and the migration template for the legacy nine.

## Open questions and known gaps

1. **`ScalarMetric` — admitted (decided).** `correlation`, `covariance`, and `cointegration` naturally output scalars; v2.0 admits a first-class `ScalarMetric` as a closed-family type (replacing the single-row-`Series` + sentinel-date hack), landing via the [ART16](../artifact/README.md) extension procedure alongside the `correlation` reference build.
2. **`OperatorCard` + operator→workflow consumer registry.** The machine-readable I/O+units+arity surface for DAG-time validation and impact analysis. Specified once `SlotDescriptor` lands.
3. **The backtest primitive set.** Where `evaluate_trades` / `summarize_trades` / `construct_trades` / `TradeSet` are re-homed. Separate workstream; tracked by its own ADR.
4. **`convert_units` semantics.** The conversion matrix (which `TimeSeriesUnits` pairs are convertible, and how) is pinned when `convert_units` is built.

## Changing an operator principle

1. Open an ADR in [`../../05_decisions/`](../../05_decisions/). 2. Land the ADR + contract change together. 3. Bump the version. New operators compound onto this contract; existing operators are audited against it; the composition layer relies on it. Slow change is the right shape.

## Citation cheat sheet

| Use | Pattern |
|---|---|
| Commit | `feat(correlation): typed SlotDescriptor I/O per OPR9` |
| PR review | `This violates OPR6 — the operator does P&L math; relocate to a primitive.` |
| Code comment | `# OPR10: right's chain goes in auxiliary_lineages` |
| Runbook step | `Step 8 — the OPR16 meta-test must be green.` |
| ADR | `This decision removes OPR4's ≥3-archetype promotion gate.` |

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| **v2.0** | 2026-05-30 | **Foundational reset** encoding five architectural decisions ([ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md)) and the operator-standardization audit. (1) **OPR6 absolute finance-blindness** — removed the v1 trade-operator carve-out; `evaluate_trades`, `summarize_trades`, `construct_trades`, and the `TradeSet` artifact are removed from the operator layer and relocated to a future backtest **primitive** set (12 operators → 9). (2) **OPR4 promotion gate removed** — the v1 "≥3 workflow archetypes" rule is replaced by toolbox-membership admission, because the LLM-DAG composition layer needs a complete finance-blind operator catalogue and the recipe gate blocked building it. (3) **OPR8 uniform signature** — `params: Optional=None` for every operator with full config-default resolution (fixes the v1 four-operator `TypeError` ABI break). (4) **OPR9/OPR15 structured `SlotDescriptor`** replaces bare class-name slots (units/frequency/sub-kind/arity now machine-checkable). (5) **OPR11 metadata algebra** — units refuse-don't-coerce with a sole `convert_units` site; frequency made load-bearing (derived at the adapter); uniform `require_matching_*` + honest combined lenient policy. (6) **OPR13 one error family** — all `<Operator>Error` and `OperatorConfigError` subclass `ValueError`; operators own every failure surface (no raw library leaks); executor preserves the typed error. (7) **OPR10 lineage** — uniform `auxiliary_lineages` for N-ary; `sanitize_params_for_lineage` (fixes the `summarize_series` default-path crash). (8) **OPR12/OPR14** — config name **and** version identity check; mandatory rerun-determinism; `±Inf` forbidden in payloads. (9) **OPR16 registry-consistency meta-test** introduced as the enforcement gate and the mechanical verifier. Added the v2.0 method-family taxonomy (statistical/transform/conversion families; trade families removed), the *Decided edge-case behavior* table, and the `correlation` reference example. Artifacts named a **co-equal component** hardened in lock-step ([ART](../artifact/README.md)). | [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md) |
| v1.1 | 2026-05-17 | Pre-canonical factual corrections against live code (four-file shape; live closed family incl. `TradeSet`; `OperatorMethodFamily` values; OPR6 "asset-class-blind, refined" with the trade carve-out; OPR7 design-locked-constant allowance; OPR10 real lineage API; OPR15 registry arity; OPR4/OPR16 bootstrap exception). Superseded by v2.0. | — |
| v1 | 2026-05-17 | Initial operator contract. | — |
