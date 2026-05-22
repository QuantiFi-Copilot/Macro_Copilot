# Artifact

> The contract every artifact in the Macro Copilot platform must satisfy — what makes something a valid artifact at all, what makes a particular artifact well-formed, and the principles that govern when (and how) a new artifact type may be added to the closed family. **Asset-class-blind by design** — artifact types are structural wrappers; the data inside may be finance-domain data (yields, prices, trades), but the *type itself* is shape-only and runs equally on rates, FX, equities, or any indexed data.

**Version:** v1.1
**Last reviewed:** 2026-05-17
**Status:** load-bearing component contract. Changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Operationalises principles:** P3 (consistency by contract), P4 (determinism — every artifact is content-addressed by `lineage.head_hash`), P5 (honest disclosure — structural metadata is surfaced on the artifact itself), **P8 (closed-family discipline — this contract is the platform's primary closed family)**, P9 (finance-blind — artifact types are structural, not asset-class-specific), P10 (single source of truth — the `ARTIFACT_TYPE_NAMES` tuple is the only valid set).
**See also:** [`runbook.md`](runbook.md) — the procedure for adding a new artifact type to the closed family.

---

## What this folder is

The contract every artifact must satisfy. Artifacts are the **typed wire format** between layers of the substrate: primitives produce them (via the bridge), operators consume and emit them, the workflow executor passes them between nodes, and the artifact store persists them. They are the *only* legal data shape inside the operator and workflow layers — naked `pd.DataFrame`, `pd.Series`, and `np.ndarray` objects do not cross those boundaries.

The artifact layer is **structurally different** from the primitive and operator layers:

- **Primitives and operators are *folders***. Each one lives in its own four-file (primitive) or four-file (operator) folder and is one of many instances of the same contract.
- **Artifacts are *types in a closed family***. Each artifact is one entry in the `ARTIFACT_TYPE_NAMES` tuple (`shared/workflow/registry.py`) plus a corresponding Pydantic class (`shared/artifacts/types.py` or `shared/artifacts/trades.py`). The whole family is the architecture; adding a new entry is a P8-gated closed-family extension, not a routine addition.

This document is organised as **principles** (same as primitive and operator), grouped in four bins: definitional (ART1–ART3, *is this actually an artifact?*) → admission (ART4–ART6, *should this artifact type exist at all?*) → well-formedness (ART7–ART11, *what makes a particular artifact instance valid?*) → operational (ART12–ART16, *the build conventions every artifact follows*).

A note on relationship to the primitive and operator contracts:

| Principle | Primitive (PR) | Operator (OPR) | Artifact (ART) |
|---|---|---|---|
| Concept ownership | PR1 (finance concept; instrument-parameterised) | OPR1 (structural method family) | ART1 (one structural shape; type-parameterised by payload) |
| Closed-family / shared residence | PR8 (Convention enum) | OPR3 (shared/operators/ always) | ART2 (closed family in `ARTIFACT_TYPE_NAMES`); ART3 (no domain residence — types are global) |
| Parsimony / admission | PR4 (composability + LLM clarity) | OPR4 (composability + promotion rule) | ART4 (closed-family extension is ADR-gated); ART5 (new *shape* not new *use case*); ART6 (substrate-wide impact must land together) |
| Methodology offloading | PR7 (config.yaml) | OPR7 (config.yaml + design-lock allowance) | **N/A** — artifacts have no methodology; they are pure schemas |
| Determinism / replay | PR4 / PR15 (parity fixture) | OPR14 (pure function) | ART10 (content-addressed via `lineage.head_hash`) |
| Provenance | PR10 (reachability) | OPR10 (lineage extension) | ART9 (mandatory lineage chain on every instance) |
| Honest refusal | PR11 (raise or envelope) | OPR13 (raise only) | ART11 (validators raise on malformed input at construction) |
| Test pattern | PR16 (compute + wiring + SQL parity) | OPR16 (unit + integration; bootstrap-exception on integration) | ART13 (round-trip + validator + lineage propagation; no compute → no SQL parity) |
| Finance-blind | implicit (primitives carry domain) | OPR6 (asset-class-blind, refined) | ART3 (asset-class-blind types; payload may be finance data, the type itself is not) |

Each principle below cites its primitive / operator analog where one exists and names where artifact-specific behaviour diverges.

## What an artifact *is* — the universal contract

Every artifact is a **frozen Pydantic class** in `shared/artifacts/types.py` or `shared/artifacts/trades.py`, carrying three load-bearing parts:

```python
class <ArtifactType>(BaseModel):
    model_config = ConfigDict(
        frozen=True,                  # immutable; new artifact = new object
        extra="forbid",               # unknown fields rejected at construction
        arbitrary_types_allowed=True, # for pd.Series / pd.DataFrame payloads
    )

    payload: <typed_payload>          # pd.Series, pd.DataFrame, np.ndarray, ...
    <structural_metadata_fields>      # units, frequency, missingness_policy,
                                      # index type, etc.; varies by type
    lineage: Lineage                  # content-addressed chain of steps
                                      # back to the L1 read

    @model_validator(mode="after")
    def _validate_<shape>(self) -> "<ArtifactType>":
        # Structural invariants enforced at construction:
        # index must be DatetimeIndex, dtype must be numeric, mask
        # must agree with event_dates count, etc.
        ...
        return self
```

The closed family today is exactly six types, declared in `ARTIFACT_TYPE_NAMES` in `shared/workflow/registry.py`:

| Type | Payload | What it represents |
|---|---|---|
| **`Series`** | `pd.Series` (DatetimeIndex, numeric dtype) | A single indexed numeric series — a yield, a price, a rate, anything that maps `date → number`. |
| **`SeriesSet`** | keyed dict of `Series` (aligned to a common index) | An aligned collection of `Series`, keyed by `series_key`. Output of alignment operators. |
| **`EventSet`** | `pd.Series[bool]` mask + event-date list + per-event metadata | Discrete events firing at specific timestamps — output of thresholding / event-extraction operators. |
| **`Panel`** | `pd.DataFrame` (DatetimeIndex, per-column units) | A wide tabular artifact (rows = dates, columns = named series with their own units). Used for multi-series outputs and regression coefficient tables. |
| **`WindowedPanel`** | `np.ndarray` shape `[n_events, window_length]` + offsets | N event windows over a target series — `[events × event-relative day offset]`. Output of `event_windows`. |
| **`TradeSet`** | ordered list of `Trade` records (each with leg_specs, entry/exit dates) + `methodology_policy` tag + optional `source_event_key` | A finite, well-formed set of trades fired by a strategy — *pure data* description, no P&L. The `methodology_policy` tag records the trade-construction methodology variant (e.g., `fixed_horizon_v1`); `source_event_key` optionally back-references the upstream `EventSet.source_series_key`. |

Two earlier design-note types — `ScalarMetric` and `RankedResult` — appear in early architecture docs but are **not in the current closed family**. `ScalarMetric` is explicitly deferred per `shared/artifacts/types.py:23`; `RankedResult` is not yet registered. Operators that need single-value or ranked outputs use a single-row `Series` or `Panel` until those two types are admitted via the closed-family-extension procedure.

Two structural-metadata families are themselves closed enums used by multiple artifact types:

- **`TimeSeriesUnits`** (re-exported from `shared/schemas/time_series.py`) — the unit taxonomy. Current values: `PERCENT`, `BPS`, `Z_SCORE`, `RATIO`, `PCT_RANK`, `FACTOR_LEVEL`, `COUNT`. Per [`units.py`](../../../shared/artifacts/units.py): *"if a future operator needs a unit the closed enum does not cover, extend `TimeSeriesUnits` centrally — do NOT introduce a second enum here."*
- **`MissingnessPolicy`** (`shared/artifacts/missingness.py`) — discriminated union: `CleanSingleSeriesV1`, `RawNoCleaning`, `AlignSeriesFFillV1`. Operators check policy compatibility before composing artifacts.

Both taxonomies are closed-family-gated the same way artifact types are.

## What an artifact is *not*

Five boundary statements that prevent common misclassifications:

- **Not a computation.** Artifacts don't transform data; they describe its shape. Computations live in primitives (L2) and operators (L3); artifacts are the wire format between them. An artifact class has no `__init__` logic beyond Pydantic validation; no method that computes a derived value (`compute_zscore()` on `Series` would be wrong — that's an operator).
- **Not a domain model.** An artifact doesn't know whether its payload is yields, FX rates, or equity prices. It knows the *structural shape* of the payload (a `pd.Series` with `DatetimeIndex`, numeric dtype, `TimeSeriesUnits.BPS`). The domain interpretation lives in primitive metadata (the `Convention.source` tags, the `Convention.rationale`) and in workflow templates (the desk-concept they compose). Artifacts are domain-blind.
- **Not a database row.** Artifacts are *in-memory* typed wrappers. They are persisted to the artifact store with their lineage chain, but the persistence layer is `state/artifact_store.py`, not the artifact class itself. The artifact class does not have ORM behaviour, foreign keys, or query methods.
- **Not a configuration object.** Artifacts have no `defaults:`, no `conventions:`, no YAML — they are pure schemas. The closest analogue to "configuration" is the structural metadata fields (`units`, `frequency`, `missingness_policy`), which are part of the artifact's identity, not a tunable knob.
- **Not optional.** Every artifact carries a `Lineage` field — this is mandatory, never `None`, never a default-empty chain. An artifact without lineage is a malformed artifact and the validator rejects it at construction.

## How to use this document

For your first read: scan the **Quick index** below, then read every principle's **Rule** line. The **Why**, **Verify**, and **Anti-patterns** sections are reference material for when a question or a PR turns on a specific principle.

For ongoing work: do not re-read this file from top to bottom. Look up the specific principle by ID when it comes up. Cite by ID (`ART3`, `ART10`) in commit messages, PR comments, and code review — same discipline as the P-numbers from [`../../00_thesis/01_non_negotiables.md`](../../00_thesis/01_non_negotiables.md), the PR-numbers from [`../primitive/README.md`](../primitive/README.md), and the OPR-numbers from [`../operator/README.md`](../operator/README.md). The four namespaces are deliberately separate: P-numbers are platform-wide; PR / OPR / ART are component-specific.

## Quick index — the ART-numbers

| ID | Group | Principle | One-line rule |
|---|---|---|---|
| **ART1** | I | Structural shape ownership | An artifact owns one *structural shape* — a typed wrapper around a payload of a specific kind (single Series, keyed set, event mask, panel, windowed panel, trade set). |
| **ART2** | I | Closed-family membership | The valid set of artifact types is exactly `ARTIFACT_TYPE_NAMES` in `shared/workflow/registry.py`. Adding a new type is an ADR-gated closed-family extension. |
| **ART3** | I | Asset-class-blind types | An artifact type is structural; its payload may carry asset-class-specific data, but the *type itself* runs unchanged on rates, FX, equities, credit, commodities, or any indexed data. |
| **ART4** | II | Closed-family extension is ADR-gated | Adding a new artifact type is the most heavily gated change in the platform. It requires an ADR, simultaneous updates to discriminator unions / registry / bridge / consumer operators / validator / test fixtures, and at least one demonstrated producer-consumer pair landing in the same PR (or explicit prerequisite chain). |
| **ART5** | II | New shape, not new use case | A new artifact type is admitted only when existing types cannot *structurally* carry the data. New use cases for an existing shape — a new kind of `Series` data, a different `MissingnessPolicy`, a new `TimeSeriesUnits` value — do not warrant a new artifact type. |
| **ART6** | II | Substrate-wide-impact commitment | A new artifact type touches at least eight sites (Pydantic class, closed-family enum, discriminator union, executor type-map, artifact-store codec, bridge or producer-operator, consumer-operator, tests). All eight must land in the same PR or an explicit prerequisite chain. Half-landed extensions are auto-reject. |
| **ART7** | III | Frozen, immutable, `extra="forbid"` | Every artifact's `model_config` is `frozen=True, extra="forbid"`, with `arbitrary_types_allowed=True` for pandas / numpy payloads. New artifacts are constructed; existing artifacts are never mutated. |
| **ART8** | III | Mandatory structural metadata | Every artifact carries the structural-metadata fields its type requires — `units` / `frequency` / `missingness_policy` for `Series`-shaped types; `event_dates` / `per_event_metadata` for event types; etc. Missing or `None` metadata where the type requires it is a malformed artifact. |
| **ART9** | III | Mandatory lineage chain | Every artifact carries a `Lineage` field with at least one step (typically a `PrimitiveStep` for primitive outputs or an `OperatorStep` for operator outputs). No artifact ever has empty / null lineage. |
| **ART10** | III | Content-addressed identity | The artifact's identity is `lineage.head_hash`. Two artifacts with the same content (same payload + same metadata + same lineage chain) produce the same hash; the artifact store uses this for dedup and replay. |
| **ART11** | III | Validators raise at construction | Structural invariants (DatetimeIndex required, numeric dtype required, event-count agreement, panel-column-units agreement) are enforced by Pydantic `@model_validator(mode="after")` blocks. Construction of a malformed artifact raises `ValueError` immediately. |
| **ART12** | IV | Closed enums for structural metadata | `TimeSeriesUnits` and `MissingnessPolicy` are themselves closed families. New artifact types reuse these taxonomies; introducing a parallel taxonomy is forbidden. |
| **ART13** | IV | Test pattern | Every artifact ships with round-trip Pydantic JSON tests, validator tests (every malformed-input case raises), lineage-propagation tests (constructing an artifact from upstream lineage produces the expected chain), and immutability tests (`frozen=True` blocks reassignment). **No SQL parity tests** — artifacts have no compute path. |
| **ART14** | IV | Bridge / adapter required for primitive-produced types | Every artifact type that can be produced by a primitive has a registered adapter in `shared/artifacts/adapters/` lifting the primitive's wire-shaped output into the typed artifact. There is no other legal path from primitives to artifacts. |
| **ART15** | IV | No naked pandas escape | Inside operator-layer and workflow-layer code, the artifact is the only legal data carrier. Raw `pd.DataFrame`, `pd.Series`, `np.ndarray` may live *inside* an artifact's `payload` field, but never as a top-level value passed between operators or workflow nodes. |
| **ART16** | IV | Closed-family-extension procedure | Adding a new artifact type follows the documented six-step procedure (ADR → class → discriminator → registry → bridge/producer → consumer → tests). The procedure is the runbook in [`runbook.md`](runbook.md). |

---

## Group I — Definitional: is this actually an artifact?

### ART1 — Structural shape ownership

**Rule.** An artifact owns one *structural shape* — the type-level description of how its payload is organised (a single Series, a keyed collection of Series, an event mask + dates, a wide panel, a windowed panel of events, a trade set). The shape is what other artifact types *do not* express: each type captures a structurally distinct organisation of data.

**Why.** The closed-family discipline (ART2) is what lets the substrate be type-safe end-to-end. Each artifact type is a unique structural shape, and operators dispatch on these shapes. If two artifact types had the same shape, the type algebra would be ambiguous and operators couldn't reliably accept one or the other.

**Verify.**
- The artifact's shape is structurally distinct from every other type in the closed family. *"How is `Series` different from `Panel`?"* — Series is one column over a DatetimeIndex; Panel is multiple columns over a DatetimeIndex. *"How is `Panel` different from `WindowedPanel`?"* — Panel is `[date × series]`; WindowedPanel is `[event × offset]`. Each pair has a one-sentence structural distinction.
- The Pydantic class name names the shape (`Series`, `EventSet`, `WindowedPanel`), not the use case (`YieldPanel`, `RegimeEvents`).
- The artifact does not embed asset-class or domain assumptions; the shape would make sense for any indexed numeric data.

**Anti-patterns.**
- An artifact named `YieldPanel` (asset-class-specific name; the shape is a `Panel`, with rates yields in the payload).
- Two artifact types with the same shape (e.g., `Series` and `YieldSeries` — duplicate; one is a `Series` with `units=PERCENT` in the payload).
- An artifact whose distinction from another is *use-case* not *shape* (`RegimeEvents` vs `BreakoutEvents` — both are `EventSet` shapes).

**Exceptions.** None.

**Relates to.** Analog of [PR1](../primitive/README.md#pr1--concept-ownership-not-instrument-ownership) (primitives own finance concepts) and [OPR1](../operator/README.md#opr1--structural-method-ownership) (operators own structural method families); artifacts own structural shapes.

### ART2 — Closed-family membership

**Rule.** The valid set of artifact types is exactly the tuple `ARTIFACT_TYPE_NAMES` in `shared/workflow/registry.py`, plus the corresponding Pydantic classes registered in the discriminator union. Today the family is:

```python
ARTIFACT_TYPE_NAMES = (
    "Series",
    "SeriesSet",
    "EventSet",
    "Panel",
    "WindowedPanel",
    "TradeSet",
)
```

Code that introduces a new artifact-shaped Python class outside this enum is in violation. The validator (and `artifact_type_name()` in the registry) raises on unknown artifact types deliberately, so a slipped-in custom class surfaces at execution time rather than propagating silently.

**Why.** [P8](../../00_thesis/01_non_negotiables.md) (closed-family discipline) at the artifact layer. The substrate's type algebra is bounded by this set — every operator declares its inputs and outputs in terms of these names, the executor dispatches on them, the validator checks composition against them. Letting the set grow casually breaks every downstream consumer; growing it deliberately (ART4 + ART6) is the only sustainable path.

**Verify.**
- A `grep` for `class .*\(BaseModel\)` inside `shared/artifacts/` returns exactly the closed-family classes plus their structural-metadata helpers (`MissingnessPolicy` variants, `LineageStep` variants, `LegSpec`). No artifact-shaped class lives outside this set.
- Every operator's `OperatorSpec.input_slots` and `output_type` values are members of `ARTIFACT_TYPE_NAMES` (or `"List[<member>]"`).
- The discriminator union in `state/schemas.py` (and the type-map in `shared/workflow/registry.py::artifact_type_name`) exactly mirror `ARTIFACT_TYPE_NAMES`.

**Anti-patterns.**
- A primitive that returns a custom Python class shaped like an artifact but not registered in the closed family.
- An operator that uses a `BaseModel` subclass outside `shared/artifacts/types.py` or `shared/artifacts/trades.py` as if it were an artifact.
- "Just for this one workflow, we need a `RegressionResult` artifact" — no. Either use an existing type (`Panel` for regression coefficient tables) or file an ADR to extend the closed family.

**Exceptions.** None at the level of artifact wrappers. Helper types (`LegSpec`, `MissingnessPolicy` variants, `LineageStep` variants) are part of the artifact contract but not artifact types themselves — they live inside artifacts or inside lineage chains.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md) (closed-family discipline) at the artifact layer; [PR8 / OPR8](../primitive/README.md#pr8--single-central-methodology-surface-the-central-knob) bounded-surface principles for compute layers.

### ART3 — Asset-class-blind types

**Rule.** An artifact *type* is structural — it knows the shape of its payload but not the asset class. The data *inside* an artifact instance may be asset-class-specific (a `Series` of UST yields, a `TradeSet` of FX trades), but the type's fields and validators do not branch on asset class.

This is the operationalisation of [OPR6](../operator/README.md#opr6--asset-class--domain-blind-contract) at the type level: operators are asset-class-blind because the types they consume and emit are asset-class-blind.

**Why.** Cross-asset portability. The same `Series` type carries rates yields today, FX rates tomorrow, equity prices later — the type does not change. The same `TradeSet` carries rates trades, FX trades, equity trades. The type's structural validators (`DatetimeIndex`, numeric dtype, mask agreement, etc.) hold uniformly across asset classes. If the type knew the asset class, the substrate's cross-asset claim ([P9](../../00_thesis/01_non_negotiables.md)) would collapse at the type level.

Generic trading concepts (Trade, LegSpec, holding period) are allowed in artifact types when the *structural shape* of the concept is asset-class-blind — a Trade in rates is structurally the same shape as a Trade in FX (entry date, exit date, leg specs, weights). What the leg's `instrument_key` resolves to is data, not type.

**Verify.**
- No artifact class branches on asset-class identity in its validators or fields.
- No artifact class name contains an asset-class concept (`YieldSeries`, `FXPair`, `EquityPanel` are wrong; `Series`, `Panel` are right).
- Cross-asset tests: at least one round-trip test per artifact type uses non-rates synthetic data (random walks, temperature data, equity prices) and the validator accepts it.

**Anti-patterns.**
- Adding `asset_class: Literal["rates", "fx", ...]` as a field on an artifact type.
- Validators that check `if units == BPS: ...` — that's branching on a rates-shaped unit, which is data-level reasoning leaking into the type.
- An artifact type whose name embeds an asset class.

**Exceptions.** None. Artifact types are structural; asset-class-aware code lives in primitives and workflow templates.

**Relates to.** [P9](../../00_thesis/01_non_negotiables.md), [OPR6](../operator/README.md#opr6--asset-class--domain-blind-contract).

---

## Group II — Admission: should this artifact type exist at all?

### ART4 — Closed-family extension is ADR-gated

**Rule.** Adding a new artifact type is the most heavily gated change in the platform's component system. It requires:

1. **An ADR** in [`../../05_decisions/`](../../05_decisions/) describing the new shape, why no existing artifact type structurally carries it, and which producer / consumer code is affected.
2. **Simultaneous updates** to every site listed in ART6 (closed-family enum, Pydantic class, discriminator union, executor type-map, validator, bridge or producer-operator, consumer-operator), all in the same PR (or an explicit prerequisite chain with the producer / consumer landing in follow-up PRs).
3. **At least one demonstrated producer-consumer pair** — the extension is admitted only when there is a primitive (or operator) that emits the new type *and* an operator (or workflow template) that consumes it. Adding an artifact type with no consumer is forbidden; it accumulates dead substrate.

**Why.** [P8](../../00_thesis/01_non_negotiables.md) is most acute at the artifact layer because every downstream consumer (every operator, every workflow, the executor, the validator, the artifact store) reads from `ARTIFACT_TYPE_NAMES`. A casual addition breaks every site. The ADR + simultaneous-landing rule is what keeps the closed-family discipline intact under change.

The discipline here is *much tighter* than the primitive's PR4 parsimony rule or the operator's OPR4 promotion rule. A new primitive or operator may legitimately ship for a single workflow; a new artifact type may not.

**Verify.**
- The ADR exists in `05_decisions/` and names the new shape, the affected sites, and the rationale.
- The PR (or prerequisite-chained PRs) updates every site in ART6. Half-landed extensions are auto-reject.
- The PR contains a working producer-consumer pair (a primitive or operator that emits the type, plus an operator or workflow template that consumes it), not just the type declaration.

**Anti-patterns.**
- A PR adding an artifact class to `shared/artifacts/types.py` without updating `ARTIFACT_TYPE_NAMES`.
- A PR adding an artifact type with no consumer ("we'll wire up the operator later").
- A PR adding an artifact type with no ADR — even when the type "obviously" belongs (the bar is the ADR, not the obvious-ness).
- A "small" artifact extension that touches only the class file and the registry, leaving the discriminator union and bridge stale.

**Exceptions.** None.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md) (closed-family discipline) at its most acute. The primitive [PR4](../primitive/README.md#pr4--parsimony-the-composability-check--llm-tool-selection-clarity) and operator [OPR4](../operator/README.md#opr4--parsimony--the-promotion-rule) parsimony rules are much lighter-weight; new artifact admission is qualitatively stricter.

### ART5 — New shape, not new use case

**Rule.** A new artifact type is admitted only when existing types **cannot structurally carry** the data. New use cases for an existing shape — a new kind of `Series` data, a different `MissingnessPolicy` variant, a new `TimeSeriesUnits` value, a different value pattern on an existing type's fields — do not warrant a new artifact type. They warrant either extending an existing closed-enum metadata family (`TimeSeriesUnits`, `MissingnessPolicy`) or just emitting an existing type with the new value.

**Why.** Closed-family discipline plus structural distinctness (ART1). Every new artifact type compounds: every operator now has another shape to handle, every test fixture needs another case, every consumer needs another branch. The cost is paid forever; admit only when no existing type carries the shape.

The most common mis-classification: thinking that a *new use case* (a new kind of data being computed) requires a *new artifact*. It almost never does. A new use case usually just emits an existing type with different field values.

**Verify.**
- The PR description names at least one existing artifact type and explains explicitly why its shape cannot carry the new data. *"`Panel` carries `[date × series]`; the new data is `[event × offset]` — that's `WindowedPanel`'s shape, not `Panel`'s"* — that's a real structural argument. *"`Panel` rounds floats differently than I'd like"* — that's not.
- The argument cites the structural fields and validators of the existing type, not the use case.
- If the answer is *"we just need different metadata values on `Series`"*, the right path is to extend `TimeSeriesUnits` or `MissingnessPolicy`, not to add an artifact type.

**Anti-patterns.**
- "We need a `YieldSeries` because we're computing yields" — no, that's `Series` with `units=PERCENT`.
- "We need a `RegressionResult` artifact" — almost always `Panel` with rows = date and columns = `(beta, alpha, r_squared)` with appropriate per-column units.
- "We need a `ScalarMetric` artifact for the summary statistic" — that's a single-row `Series` (or `Panel`) until the deferred `ScalarMetric` is admitted via the full procedure.
- "We need a `RankedResult` artifact for cross-sectional rankings" — that's `SeriesSet` with the rank values as the payload, until `RankedResult` is admitted.

**Exceptions.** None.

**Relates to.** ART4 (admission gate), [PR5 / OPR5](../primitive/README.md#pr5--concept-novelty) concept-novelty tests for primitive and operator layers.

### ART6 — Substrate-wide-impact commitment

**Rule.** Adding a new artifact type touches at least eight sites. All eight must land in the same PR, or in a documented prerequisite chain where the type can be introduced incrementally without breaking downstream code:

1. **The Pydantic class** in `shared/artifacts/types.py` (or a sibling file like `shared/artifacts/trades.py` for compound shapes with helper classes).
2. **The `ARTIFACT_TYPE_NAMES` tuple** in `shared/workflow/registry.py` (the closed-family enum).
3. **The discriminator union `ArtifactTypeLiteral`** in `state/schemas.py` (so the artifact-metadata row's `artifact_type` discriminator carries the new value).
4. **The `artifact_type_name()` type-map** in `shared/workflow/registry.py` (so the executor can label runtime artifacts).
5. **The artifact-store codec** in `state/artifact_store.py` — every new type must be registered in `_ARTIFACT_CLASSES`, dispatched in `_artifact_to_stored` / `_stored_to_artifact`, and given a per-type codec pair (`_<type>_to_stored` + `_<type>_from_stored`) that handles the pandas / numpy payload's JSON-friendly encoding. Without this, `put_artifact` raises `TypeError` and the type cannot be persisted, retrieved, or replayed.
6. **The bridge or producer-operator** in `shared/artifacts/adapters/` (if the type can be produced by a primitive) OR an `OperatorSpec` with this type as `output_type` (if the type is operator-produced).
7. **At least one consumer-operator's `OperatorSpec`** with this type in `input_slots`, OR a workflow template that consumes it as a terminal artifact.
8. **Tests** — artifact-store round-trip, validator tests, lineage propagation, immutability (ART13), plus an integration test using the new type end-to-end through a real producer-consumer pair.

A PR that lands one or two of the eight without the others is auto-reject. The substrate stays consistent because the eight sites are kept in lock-step.

**Why.** The closed-family discipline only works if the family stays internally consistent. A new type in `ARTIFACT_TYPE_NAMES` without a matching Pydantic class crashes at runtime; a class without registry entry passes Python type checks but the executor refuses to label it; a discriminator entry without the artifact-store codec means `put_artifact` raises on first persistence attempt. The "all eight sites" rule is what enforces consistency at admission time.

**Verify.**
- The PR diff shows changes at every site listed above.
- A `grep -rn "<new_type_name>"` finds it in every required site.
- The CI suite passes — which it won't if any of the eight sites is stale.

**Anti-patterns.**
- A "preparation" PR that adds only the class and the enum, with the rest "coming in follow-up" — auto-reject unless the follow-up is an explicit, named prerequisite chain in the ADR.
- An artifact type that exists in the discriminator union but isn't in `ARTIFACT_TYPE_NAMES`, or vice versa.
- A new type with tests but no producer-consumer pair.

**Exceptions.** Helper / metadata types (`LegSpec`, `MissingnessPolicy` variants, `LineageStep` variants) follow a lighter-weight procedure — they live inside artifact types and don't appear in `ARTIFACT_TYPE_NAMES`. Their addition is still ADR-gated (they're closed families too — see ART12) but the eight-site rule doesn't apply.

**Relates to.** ART4 (admission gate). The "all sites land together" rule is what makes ART4's "ADR-gated extension" actually safe.

---

## Group III — Well-formedness: what makes a particular artifact instance valid?

### ART7 — Frozen, immutable, `extra="forbid"`

**Rule.** Every artifact class declares:

```python
model_config = ConfigDict(
    frozen=True,                  # field reassignment blocked
    extra="forbid",               # unknown fields rejected at construction
    arbitrary_types_allowed=True, # for pd.Series / pd.DataFrame / np.ndarray payloads
)
```

The `frozen=True` is non-negotiable. Artifacts are constructed; they are never mutated. Operators emit *new* artifacts; they do not modify their inputs.

The `extra="forbid"` is non-negotiable. An unknown field at construction is a malformed call and should fail loudly — it usually means a caller is passing through stale fields from a different artifact shape.

The `arbitrary_types_allowed=True` is required because Pydantic v2 does not natively validate `pd.Series` / `pd.DataFrame` / `np.ndarray`; the validation lives in the `@model_validator` blocks (ART11) instead.

**Why.** [P4](../../00_thesis/01_non_negotiables.md) (determinism + replayability) at the artifact layer. A mutable artifact would have an unstable identity — the same `head_hash` could refer to different content depending on when the artifact was inspected. With `frozen=True`, an artifact's identity is fixed at construction and replay produces the same content forever.

The payload (pandas / numpy) is still technically mutable in Python — `frozen=True` only blocks reassignment of the wrapper's fields, not deep-copy mutation of the wrapped object. The operator-layer *convention* is "treat artifacts as immutable, copy on transformation" — a convention enforced by code review, not by Python.

**Verify.**
- Every artifact class has `model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)`.
- A test asserts `artifact.field = new_value` raises `ValidationError` (or `TypeError` depending on Pydantic version).
- A test asserts construction with an unknown field raises `ValidationError`.

**Anti-patterns.**
- An artifact class without `frozen=True`.
- An artifact class with `extra="allow"` or no `extra` setting (which defaults to `"ignore"`, also wrong here).
- An artifact "method" that mutates `self.payload` (e.g., `def normalize_in_place(self): self.payload[:] = ...` — wrong; either return a new artifact or move the operation to an operator).

**Exceptions.** None.

**Relates to.** [P4](../../00_thesis/01_non_negotiables.md) (determinism); analog of [OPR14](../operator/README.md#opr14--pure-function-no-io) (pure functions) and [PR4](../primitive/README.md#pr4--parsimony-the-composability-check--llm-tool-selection-clarity) (determinism at the primitive level).

### ART8 — Mandatory structural metadata

**Rule.** Every artifact carries the structural-metadata fields its type requires. The required fields per type are:

| Type | Required structural metadata |
|---|---|
| `Series` | `series_key`, `units`, `missingness_policy`, `lineage`, optional `frequency` |
| `SeriesSet` | `units_by_key`, `missingness_by_key`, `lineage`, plus per-key upstream lineages |
| `EventSet` | `mask`, `event_dates`, `per_event_metadata`, `source_series_key`, optional `frequency`, `lineage` |
| `Panel` | `units_by_column`, `missingness_policy`, `lineage` |
| `WindowedPanel` | `offsets`, `event_dates`, `per_event_metadata`, `target_series_key`, `units`, `lineage` |
| `TradeSet` | ordered list of `Trade` records, required `methodology_policy` tag, optional `source_event_key`, `lineage` |

These are not optional fields with defaults — they are part of the artifact's identity, populated at construction by whoever builds the artifact (the bridge for primitive outputs; the operator for derived artifacts).

**Why.** Structural metadata is what makes the operator layer's compatibility checks possible (OPR11). Without `units`, the `series_arithmetic` operator can't refuse a basis-points-plus-percent addition. Without `missingness_policy`, `align_series` can't refuse to silently mix a `CleanSingleSeriesV1` series with a `RawNoCleaning` series. Without `frequency`, daily and weekly series mix invisibly. The metadata is what makes typed artifacts safer than naked pandas.

**Verify.**
- Every artifact class's field declarations include the metadata above.
- Every adapter / producer constructs artifacts with the metadata populated — not `units=None` or empty defaults.
- A test constructs each artifact type with synthetic data and asserts the metadata fields are present and well-typed.

**Anti-patterns.**
- An artifact constructed with `units=None` where the type requires units.
- An artifact whose `frequency` is left unset when the data source has a known frequency — leave it `None` only when the source genuinely has no frequency, document why.
- A bridge / adapter that constructs an artifact by guessing metadata values from the payload instead of taking them as explicit inputs.

**Exceptions.** None.

**Relates to.** [OPR11](../operator/README.md#opr11--structural-metadata-enforcement-and-honest-refusal) (operators enforce metadata agreement); [P5](../../00_thesis/01_non_negotiables.md) (honest disclosure — metadata is what makes methodology visible at the artifact boundary).

### ART9 — Mandatory lineage chain

**Rule.** Every artifact carries a non-empty `Lineage` field. The chain's exact shape depends on the producer:

- **Primitive output via the canonical bridge** (`tool_output_to_artifact_series` / `tool_output_to_artifact_panel`): the chain is a single `PrimitiveStep` — the primitive *is* the head. (See `shared/artifacts/adapters/from_time_series.py`.)
- **Raw DataFrame adapter** (`raw_dataframe_to_artifact_series`, used for fixture / synthetic data): the chain is `FetchStep` (the L1 read, or the synthetic-source descriptor) followed by an `AdapterStep` recording the lift parameters. (See `shared/artifacts/adapters/from_raw_dataframe.py`.)
- **Operator output**: the producer extends the primary input's chain via `Lineage.append(OperatorStep.build(...))` (OPR10), so the head is the new `OperatorStep`.

The minimum chain length is 1. An empty lineage is forbidden; the model raises at construction.

**Why.** [P4](../../00_thesis/01_non_negotiables.md) (replayability). Lineage is what makes the artifact reproducible: given the lineage chain and the original L1 data, the artifact can be recomputed deterministically. Lineage is also what makes [P5](../../00_thesis/01_non_negotiables.md) (honest disclosure) possible at the workflow level: a user reading the terminal artifact can walk the lineage to see every transformation that touched the data.

**Verify.**
- Every artifact instance has `len(artifact.lineage.steps) >= 1`.
- Every artifact constructed by the canonical `tool_output_to_artifact_*` bridge has a single `PrimitiveStep`; every artifact built via `raw_dataframe_to_artifact_series` has `FetchStep` + `AdapterStep`.
- Every artifact constructed by an operator has an additional `OperatorStep` appended to its primary input's lineage (per [OPR10](../operator/README.md#opr10--lineage-extension-via-operatorstepbuild--lineageappend)).
- A test constructs an artifact with an empty lineage list and asserts the validator raises.

**Anti-patterns.**
- Constructing an artifact with `lineage=None` or `lineage=Lineage(steps=[])` — the validator rejects both.
- An operator that drops lineage when producing its output (lineage length stays the same as input instead of growing by 1).
- A bridge / adapter that produces an artifact whose lineage does not match its declared shape (e.g., `tool_output_to_artifact_*` emitting a multi-step chain instead of the single `PrimitiveStep`, or `raw_dataframe_to_artifact_series` omitting the `FetchStep`).

**Exceptions.** None.

**Relates to.** [P4](../../00_thesis/01_non_negotiables.md), [P5](../../00_thesis/01_non_negotiables.md); originates at [PR10](../primitive/README.md#pr10--provenance-reachability); extended at [OPR10](../operator/README.md#opr10--lineage-extension-via-operatorstepbuild--lineageappend).

### ART10 — Content-addressed identity

**Rule.** An artifact's identity is `lineage.head_hash` — the SHA-256-style hash of the last step in its lineage chain. The artifact store dedups on `head_hash` (`put_artifact` uses it directly per `state/artifact_store.py`); workflow replay uses `head_hash` to detect whether a recomputed artifact matches its original.

The hash recipe (see `shared/artifacts/lineage.py`) is **over the step's `kind`, `name`, `version`, `params`, and `input_hashes`** — *not* over the payload bytes and *not* over the artifact's structural-metadata fields directly. Two artifacts with the same lineage chain (same `head_hash`) are the *same* artifact from the substrate's perspective. The structural-metadata fields on the artifact are *not* part of the hash recipe — which means:

**Producers are responsible for folding every content-defining choice into the lineage step's params.** If an adapter or operator makes a methodology-affecting choice (units selection, missingness-policy choice, alignment policy, window size, ddof, etc.), that choice must appear in the step's `params` dict so the hash captures it. A producer that records a choice only on the artifact metadata (e.g., setting `units=PERCENT` on the `Series` instance but not in the `PrimitiveStep.params`) creates an identity hazard: two artifacts produced from different choices can collide on `head_hash`. The discipline is "if it affects the output content, it goes in lineage params; the artifact-side metadata is a structural view of what lineage already captured."

Hash-not-of-bytes is deliberate: floating-point representations of the same logical value can differ across machines or NumPy versions; using a hash over the *recipe* (lineage chain) bypasses that.

**Why.** [P4](../../00_thesis/01_non_negotiables.md). Content-addressed identity is what makes replay possible: the artifact store can confirm by hash that a re-execution produced the same artifact, even six months later, without comparing payload bytes (which might vary in trivial floating-point representation).

Hash-not-of-bytes is deliberate: floating-point representations of the same logical value can differ across machines or NumPy versions; using a hash over the *recipe* (lineage chain) bypasses that.

**Verify.**
- `Lineage.head_hash` mirrors `Lineage.steps[-1].hash` (cheap equality without walking the chain — codified in `Lineage.from_steps`).
- Constructing the same artifact twice (same lineage chain) produces the same `head_hash`.
- A test asserts: build artifact A with lineage chain L; serialise A; deserialise to A'; A.head_hash == A'.head_hash.

**Anti-patterns.**
- Computing identity from payload bytes (would fail across float-format differences).
- Constructing an artifact and then *mutating* it — breaks `head_hash` invariance.
- An artifact whose `head_hash` doesn't match the hash of its last lineage step (constructed bypassing `Lineage.from_steps` / `Lineage.append`).
- A producer that records a methodology choice on the artifact's structural metadata but **not** in the lineage step's `params` — two artifacts produced from different choices can then collide on `head_hash`, breaking content-addressed identity.

**Exceptions.** None.

**Relates to.** [P4](../../00_thesis/01_non_negotiables.md); [OPR10](../operator/README.md#opr10--lineage-extension-via-operatorstepbuild--lineageappend) (operators extend the chain so the new head_hash is the operator's step hash).

### ART11 — Validators raise at construction

**Rule.** Structural invariants of every artifact type are enforced by `@model_validator(mode="after")` blocks that run at construction. Malformed input — index that isn't a `DatetimeIndex`, non-numeric dtype, mask length disagreeing with event-dates length, panel columns disagreeing with `units_by_column` keys — raises `ValueError` (or `ValidationError`) immediately.

Validators are mandatory; default Pydantic field validation is necessary but not sufficient. The invariants enforced today, by type:

| Type | Validator invariants |
|---|---|
| `Series` | DatetimeIndex required; no duplicate index entries; sorted ascending; numeric dtype |
| `SeriesSet` | Every member is a valid `Series`; common_index keys match member series_keys |
| `EventSet` | Mask has DatetimeIndex; mask dtype is bool; `len(event_dates) == len(per_event_metadata)`; `mask.sum() == len(event_dates)` |
| `Panel` | DatetimeIndex required; `units_by_column.keys()` matches `payload.columns` |
| `WindowedPanel` | Payload is 2D; `payload.shape[0] == len(event_dates) == len(per_event_metadata)`; `payload.shape[1] == len(offsets)` |
| `TradeSet` | Each `Trade` is a valid `Trade`; entry/exit dates well-formed; weights well-formed |

**Why.** [P6](../../00_thesis/01_non_negotiables.md) (no silent failure) at the artifact construction layer. A malformed artifact must fail loudly at the *moment of construction*, not propagate downstream and surface as a confusing error inside an operator three layers later. The validator is the first line of defence and it must be loud.

**Verify.**
- Every artifact class has at least one `@model_validator(mode="after")` block enforcing its structural invariants.
- Every invariant has a corresponding negative test: construct an artifact with the invariant violated, assert the validator raises with a specific message.
- The error message names the artifact type and the specific invariant ("`Series` payload index must be sorted ascending") — generic error messages are insufficient.

**Anti-patterns.**
- An artifact class with only Pydantic field-type checks, no `@model_validator`.
- A validator that returns silently when it should raise (a bool return type, or a print statement instead of `raise ValueError`).
- A validator whose error message is generic ("invalid input") — name the specific invariant.

**Exceptions.** None.

**Relates to.** [P6](../../00_thesis/01_non_negotiables.md) (no silent failure); [OPR11](../operator/README.md#opr11--structural-metadata-enforcement-and-honest-refusal) (operators enforce metadata downstream — but the *artifact* enforces its own shape at construction).

---

## Group IV — Operational

### ART12 — Closed enums for structural metadata

**Rule.** `TimeSeriesUnits` and `MissingnessPolicy` are themselves closed families, governed by P8. New artifact types reuse these taxonomies; introducing a parallel taxonomy is forbidden. Per [`shared/artifacts/units.py`](../../../shared/artifacts/units.py): *"If a future operator needs a unit the closed enum does not cover (e.g., `unitless` distinct from `ratio`), extend `TimeSeriesUnits` centrally — do NOT introduce a second enum here."*

The current `MissingnessPolicy` discriminated union is:

| Variant | What it means |
|---|---|
| `CleanSingleSeriesV1` | The policy applied by `shared.analytics.levels.clean_single_series` (sort + dedup + ffill_limit). Used when a primitive produces a series via the canonical clean step. |
| `RawNoCleaning` | Explicit marker that no cleaning was applied. Operators that take ≥2 inputs MUST explicitly accept this policy (or refuse it). |
| `AlignSeriesFFillV1` | Wraps an upstream policy when `align_series` materially changed the payload by ffilling alignment-introduced gaps. Without this wrapper the metadata would still claim the upstream policy even though the operator imputed cells — metadata-dishonest. |

Adding a new `MissingnessPolicy` variant or a new `TimeSeriesUnits` value is itself an ADR-gated closed-family extension; the discipline is identical to ART4 for artifact types.

**Why.** Structural-metadata coherence. If `Series` had `units: TimeSeriesUnits` while `Panel` had `units_by_column: Dict[str, SomeOtherUnitsEnum]`, operators couldn't reason about unit compatibility across artifact boundaries. The single taxonomy ensures that an operator consuming a `Series` and a `Panel` can compare units uniformly.

**Verify.**
- The artifact types use `TimeSeriesUnits` and `MissingnessPolicy` consistently — no per-type parallel taxonomies.
- A new artifact type's metadata fields reuse these enums; if it needs a unit / policy that doesn't exist, the new variant is added to the existing enum, not invented locally.

**Anti-patterns.**
- Defining `class PanelUnits(Enum)` alongside `TimeSeriesUnits` for a Panel-specific need.
- An artifact field typed as `units: Literal[...]` with an inline enum instead of referencing `TimeSeriesUnits`.
- A new `MissingnessPolicy` variant added without an ADR.

**Exceptions.** None.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md) (closed families everywhere); [P10](../../00_thesis/01_non_negotiables.md) (single source of truth — the metadata taxonomies are themselves single sources).

### ART13 — Test pattern

**Rule.** Every artifact type ships with four test layers:

1. **Artifact-store round-trip tests** — construct an artifact, `put_artifact(...)` it through the real artifact store, `get_artifact(...)` it back, assert content identity. The artifact store routes through `_artifact_to_stored` / `_stored_to_artifact` plus the per-type codec; a raw `model_dump_json()` on the artifact class is **not** a substitute (pandas / numpy payloads are not JSON-serializable by Pydantic alone — the typed codec is where the pandas-to-JSON shape lives). The round-trip test is what catches codec regressions, discriminator drift, and `_NAME_TO_CLASS` staleness.
2. **Validator tests** — for every `@model_validator` invariant, one negative test that constructs a malformed artifact and asserts the validator raises with the expected message; one positive test that constructs a valid artifact and confirms it passes.
3. **Lineage-propagation tests** — construct an artifact with an upstream lineage chain, confirm the chain is preserved; confirm `head_hash` matches `steps[-1].hash`.
4. **Immutability tests** — assert `artifact.field = new_value` raises (Pydantic `frozen=True` enforcement); assert construction with an unknown field raises (`extra="forbid"`).

**No SQL parity tests.** Artifacts have no compute path — there's nothing to parity-check against an SQL baseline. Pure data structures have a different test shape.

**Why.** Each layer catches a different class of bug. The artifact-store round-trip catches serialization regressions across the full persistence path (codec → bytes → blob/inline → deserialize → validate), which is the persistence contract that ships to production. Validator tests catch construction-path bugs (the artifact built from bad input would otherwise propagate downstream). Lineage tests catch chain-corruption bugs. Immutability tests catch frozen-config regressions.

**Verify.**
- For each artifact type, a test file `tests/test_artifacts_<type>.py` (or similar) contains the four layers.
- The validator-test layer covers *every* invariant declared in the model_validator block — not just the happy path.

**Anti-patterns.**
- An artifact type with only happy-path tests (no validator negative cases).
- A test that mocks the artifact's construction instead of going through the real Pydantic validator.
- Missing artifact-store round-trip — codec drift or `_NAME_TO_CLASS` staleness will break persistence silently when the test isn't there.
- A test that calls `artifact.model_dump_json()` as a stand-in for the round-trip. That bypasses the per-type codec where the pandas / numpy payload is actually serialized; the test passes for synthetic in-memory shapes and fails in production.

**Exceptions.** None. New artifact types ship with all four layers from day one.

**Relates to.** [PR16 / OPR16](../primitive/README.md#pr16--test-triplet) (component test patterns); the artifact test pattern is structurally different because there's no compute path, but the discipline (every shape of bug has a corresponding test layer) is the same.

### ART14 — Bridge / adapter required for primitive-produced types

**Rule.** Every artifact type that can be produced by a primitive has a registered adapter in `shared/artifacts/adapters/` that lifts the primitive's wire-shaped output (a JSON-friendly dict containing a canonical `TimeSeries` payload, etc.) into the typed artifact. The adapter is the *only* legal path from primitive output to artifact.

The current adapter set:

| Adapter | Lifts |
|---|---|
| `raw_dataframe_to_artifact_series` | A `pd.DataFrame` (typically from a fetch + clean step in a test or workflow) → `Series` |
| `time_series_to_artifact_series` | A canonical `TimeSeries` (low-level, caller supplies a pre-built `PrimitiveStep`) → `Series` |
| `tool_output_to_artifact_series` | A primitive's output dict + tool identity → `Series`, with `PrimitiveStep` constructed automatically and `CleanSingleSeriesV1` derived from tool config. The dominant Series bridge callsite. |
| `tool_output_to_artifact_panel` | A Panel-emitting primitive's output dict + tool identity → `Panel`, with `PrimitiveStep` constructed automatically. Used by multi-series primitives (`build_sovereign_yield_panel_tool`, `compute_financing_rate_tool`); the executor dispatches Panel-typed primitive outputs to this bridge. |
| `artifact_series_to_time_series` | The reverse: `Series` → wire-format `TimeSeries` for serialisation to the LLM / frontend / persistence. |

A new artifact type that primitives can produce requires a new adapter following the same pattern. The adapter is part of ART4's eight-site landing.

**Why.** [P9](../../00_thesis/01_non_negotiables.md) at the bridge level. The bridge is what isolates "primitive-side wire format" from "operator-side typed artifact" — without it, primitives would need to construct artifacts directly (and would need to know about lineage chains, structural metadata, every artifact type's fields) and operators would need to handle primitive-shaped dicts (and would need to know about JSON vs Pydantic, NaN-vs-None, etc.). The bridge is the only place that knowledge lives.

**Verify.**
- For every artifact type primitives can produce, an adapter exists in `shared/artifacts/adapters/`.
- The adapter constructs the artifact with a complete `Lineage` chain (starting with `PrimitiveStep`) and complete structural metadata.
- No primitive's `compute.py` constructs an artifact directly — it goes through the adapter at the call site (typically inside the MCP server wrapper or the workflow executor).

**Anti-patterns.**
- A primitive whose `compute.py` returns an artifact instance directly. Should return a dict; the wrapper / executor calls the adapter.
- A new artifact type without a registered adapter, leaving primitives unable to produce it.
- An ad-hoc adapter in operator code that converts primitive output to an artifact, bypassing `shared/artifacts/adapters/`.

**Exceptions.** Operator-produced artifact types do not need an adapter — operators construct artifacts directly (per OPR10). The bridge is specifically the primitive → artifact lift.

**Relates to.** The detailed bridge architecture lives at `01_architecture/06_bridge.md` (forthcoming). [PR9](../primitive/README.md#pr9--composition-inheritance-strict) (composition primitives have related but distinct rules).

### ART15 — No naked pandas escape

**Rule.** Inside the operator layer (`shared/operators/`) and the workflow layer (`shared/workflow/`, `<agent>/workflows/`), the artifact is the only legal data carrier between functions. `pd.DataFrame`, `pd.Series`, `np.ndarray` may live *inside* an artifact's `payload` field, and operators may extract them temporarily to do numerical work, but they never appear as:

- Operator function inputs or outputs (operators consume and emit artifacts).
- Workflow node outputs (the executor records artifact types from `OperatorSpec.output_type`).
- Workflow edge payloads (the executor passes artifacts on edges).
- Workflow template node-output declarations.

The escape valve is *internal* — inside an operator's body, extracting `series.payload` to do `.diff()`, `.rolling()`, `.merge()` is fine. The operator's return value is a fresh artifact built from the result.

**Why.** Type-algebra integrity. The substrate's static guarantee — that an operator declaring `output_type="Series"` actually returns a `Series`, that a workflow edge carrying `"EventSet"` actually carries an `EventSet` — depends on every boundary being typed. A single naked-pandas escape at a boundary blows the guarantee and the validator can no longer reason about composition.

**Verify.**
- Operator function signatures consume and return artifact types (no `pd.DataFrame` parameters or returns at the public function level).
- A `grep` for `def .*-> pd\.` inside `shared/operators/` returns zero matches (operators never return pandas at the public API).
- Workflow templates declare every edge's artifact type; the validator confirms each operator's output type matches the downstream operator's input slot type.

**Anti-patterns.**
- An operator whose return type is `pd.DataFrame`.
- A "convenience" operator that takes `pd.Series` as input "to make testing easier" — the test should construct a real `Series` artifact.
- A workflow template that passes raw pandas between nodes via the executor.

**Exceptions.** None at the API boundaries. Internal payload extraction inside operator bodies is fine and expected.

**Relates to.** ART9 (typed artifact I/O over closed family — the operator's analog rule); the type-algebra integrity is the joint property of ART2 + ART9 + ART15.

### ART16 — Closed-family-extension procedure

**Rule.** Adding a new artifact type follows the procedure documented in [`runbook.md`](runbook.md). The procedure has seven steps:

1. **ADR.** File an ADR in `05_decisions/` documenting the new shape, the use case, the structural argument that no existing type carries the data (ART5), the eight affected sites (ART6), and the producer-consumer pair landing alongside.
2. **Pydantic class.** Add the class to `shared/artifacts/types.py` (or a sibling file for compound shapes). Match the universal contract: `frozen=True`, `extra="forbid"`, mandatory `lineage` field, structural metadata fields, `@model_validator(mode="after")` for invariants.
3. **Closed-family enum + discriminator + type-map.** Add to `ARTIFACT_TYPE_NAMES` in `shared/workflow/registry.py`; update the discriminator union (`ArtifactTypeLiteral`) in `state/schemas.py`; update the type-map in `shared/workflow/registry.py::artifact_type_name`.
4. **Artifact-store codec.** Register in `_ARTIFACT_CLASSES`, extend `_artifact_to_stored` / `_stored_to_artifact`, add per-type encode/decode helpers in `state/artifact_store.py`. Without this the type cannot be persisted.
5. **Producer wiring.** Either an adapter in `shared/artifacts/adapters/` (if primitive-produced) or an `OperatorSpec` with this type as `output_type` (if operator-produced).
6. **Consumer wiring.** At least one operator's `OperatorSpec.input_slots` references the new type, or a workflow template that consumes it as a terminal artifact.
7. **Tests.** All four layers from ART13 (artifact-store round-trip, validator tests, lineage propagation, immutability), plus an end-to-end integration test through a real producer-consumer pair.

All seven steps land together (same PR, or an explicit prerequisite chain documented in the ADR). The procedure exists as a runbook because the closed-family-extension review is itself a multi-reviewer audit and needs a checklist.

**Why.** ART4 + ART6 together make extension safe; the runbook makes the procedure followable. Without a documented procedure, every new artifact type would require re-discovering which eight sites need updating, which makes the gate harder to enforce.

**Verify.**
- The procedure is documented in [`runbook.md`](runbook.md).
- Every artifact-type-extension PR cites the runbook's procedure in the description.
- The PR review checklist (in `runbook.md`) is run against the diff before merging.

**Anti-patterns.**
- Skipping the runbook because "this extension is small" — small extensions break the closed family the same way large ones do.
- Filing an ADR without naming the eight sites or providing the producer-consumer pair.
- Following the runbook but landing in pieces without an explicit prerequisite chain documented.

**Exceptions.** None.

**Relates to.** ART4 (admission gate); [P8](../../00_thesis/01_non_negotiables.md) (closed-family discipline).

---

## The current closed family

For reference, the six artifact types in the closed family today, with their canonical use case and the producer / consumer pattern:

| Type | Canonical use case | Primary producers | Primary consumers |
|---|---|---|---|
| `Series` | A single indexed numeric series | Almost every primitive that returns a time series; `align_series.get_series(key)`; `series_arithmetic`; `apply_mask`; `conditional_aggregate`; `summarize_series`; many others | Almost every operator that takes a Series input |
| `SeriesSet` | An aligned keyed collection of Series | `align_series` (consumes a `List[Series]` slot — the substrate's edge-aggregator binds N `Series` edges into the list — and produces a `SeriesSet`); `rolling_regression` | `select_from_series_set` |
| `EventSet` | Events firing at specific timestamps | `threshold_events` | `event_windows`; `apply_mask`; `construct_trades` |
| `Panel` | Wide tabular `[date × series]` | Multi-series primitives (`sovereign_yield_panel`, `financing_rate`); `evaluate_trades`; `summarize_trades` | `evaluate_trades` (`price_panel` + optional `financing_rate_panel` slots); `summarize_trades` (`pnl_panel` slot); workflow-template terminal artifacts |
| `WindowedPanel` | `[event × event-relative offset]` matrix | `event_windows` | `conditional_aggregate` (consumes `WindowedPanel` via its `panel` slot) |
| `TradeSet` | A finite set of trades | `construct_trades` | `evaluate_trades` |

Two design-note types are *not* in the current family but appear in earlier docs:

- **`ScalarMetric`** — explicitly deferred per `shared/artifacts/types.py:23`. Use single-row `Series` until admitted.
- **`RankedResult`** — not yet registered. Use `SeriesSet` with rank values in payloads until admitted.

## Non-standard artifacts (coming soon)

Some artifacts may legitimately not satisfy ART7–ART11 — e.g., wrappers around opaque third-party model objects whose internals are not Pydantic-validatable, or research-only artifact shapes that don't need full closed-family discipline. A non-standard artifact category, its admission tests, and its allowed boundaries are a planned extension. **Until that section opens, every artifact that ships is held to ART7–ART11.**

## Anti-patterns (catalogue-wide)

Auto-reject in review:

- **An artifact-shaped class outside `shared/artifacts/types.py` / `shared/artifacts/trades.py`.** ART2 violation. Use an existing type or file an ADR.
- **An artifact class without `frozen=True`.** ART7 violation.
- **An artifact class with `extra="ignore"` / no `extra` setting.** ART7 violation.
- **An artifact class without a `@model_validator(mode="after")` block.** ART11 violation (almost certainly missing invariants).
- **An artifact instance without a `Lineage` field, or with an empty chain.** ART9 violation.
- **An artifact field that depends on asset class** (`asset_class`, `instrument_type`). ART3 violation.
- **A parallel structural-metadata enum** (defining `class FXUnits` next to `TimeSeriesUnits`). ART12 violation.
- **An operator function whose return type is `pd.DataFrame` or `pd.Series`.** ART15 violation.
- **A primitive that constructs an artifact directly inside `compute.py`.** ART14 violation; use the bridge.
- **A new artifact type PR that lands without all eight sites updated.** ART6 violation.
- **A new artifact type PR without an ADR.** ART4 violation.
- **A new artifact type added "for one workflow."** Either use an existing type, or land a real producer-consumer pair (ART4 + ART5).
- **A test for an artifact type that covers only the happy path.** ART13 violation — every validator invariant must have a negative test.

## Open questions and known gaps

1. **`ScalarMetric` admission.** Tracked since Phase 1A; operators that need single-value outputs currently use single-row `Series`. When the use case becomes load-bearing enough to justify the closed-family extension, the ART4 procedure applies.
2. **`RankedResult` admission.** Same status — not yet registered; cross-sectional ranking operators use `SeriesSet` until admission.
3. **`PositionPath` admission.** Mentioned in `trades.py` as a "Phase 1 V2 concern" — date-indexed position state over a trade's holding window. Use case will arrive when finer-grained trade analytics ship.
4. **Non-standard artifact category.** Placeholder; the contract for non-standard artifacts (third-party model objects, research-only shapes) is unwritten.
5. **`AlignSeriesFFillV1` is the only "wrapping" missingness policy.** Future operators that materially change missingness (e.g., a bfill operator, a resample operator) will need parallel wrapping policies — the procedure for adding them is the same as ART12's enum extension.
6. **The `Lineage` chain's `head_hash` is a SHA-style hash; cross-version stability** of the recipe is tested via `tests/state/test_hash_stability.py`. Any change to the hash recipe (e.g., adding a new field to `OperatorStep`) needs to keep that test green; if it can't, the recipe change is a closed-family-style decision in its own right (the artifact-store's identity contract is `head_hash`).

## Changing an artifact principle

Same discipline as the primitive and operator principles:

1. Open an ADR in [`../../05_decisions/`](../../05_decisions/) describing the proposed change.
2. Land the ADR and the contract change in the same PR.
3. Bump the version of this file.

Artifact-principle changes are higher-stakes than primitive or operator changes because they affect every downstream consumer simultaneously. Expect more reviewers, longer review cycles.

## Citation cheat sheet

| Use | Pattern |
|---|---|
| In a commit message | `feat(artifacts): add Series.frequency field per ART8` |
| In a PR review comment | `This violates ART15 — the operator returns a pd.DataFrame at the public API.` |
| In a code comment (rare) | `# ART9: lineage starts with PrimitiveStep here; downstream operators extend.` |
| In a runbook step | `Step 4 — verify ART6 (all eight sites updated together).` |
| In an ADR | `This decision extends the closed family by admitting ScalarMetric per ART4.` |

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.1 | 2026-05-17 | Pre-canonical corrections after a factual-review pass against the codebase: (a) ART6 promoted the artifact store to a first-class admission site — seven sites → **eight sites** (Pydantic class, `ARTIFACT_TYPE_NAMES`, `ArtifactTypeLiteral`, `artifact_type_name()` type-map, **artifact-store codec in `state/artifact_store.py`**, producer wiring, consumer wiring, tests). (b) ART9 lineage start/end reframed to reflect the real producer shapes — single `PrimitiveStep` for canonical bridge outputs, `FetchStep + AdapterStep` for `raw_dataframe_to_artifact_series`, `OperatorStep` appended for operator outputs. (c) ART10 identity wording corrected — `head_hash` is purely over the lineage step's `kind/name/version/params/input_hashes`; artifact-side structural metadata is NOT in the hash recipe, which means producers must fold every content-defining choice into the lineage params (new anti-pattern added). (d) ART13 test pattern reframed as **artifact-store round-trip** through real `put_artifact` / `get_artifact` — raw `model_dump_json()` is **not** a substitute because pandas / numpy payloads are not Pydantic-serializable; the per-type codec is where the payload encoding lives. (e) ART14 adapter table — added `tool_output_to_artifact_panel` (the real Panel bridge in `from_time_series.py`). (f) ART16 — extension procedure expanded from six to seven steps (artifact-store codec added as Step 4). (g) Structural-metadata table — `TradeSet` row corrected to include required `methodology_policy` and optional `source_event_key`; same correction in the universal-contract table. (h) Closed-family producer/consumer table — `SeriesSet` row corrected (List[Series] is bound from N edges by the validator's edge-aggregator, not from SeriesSet members); `Panel` row corrected (real consumers are `evaluate_trades` / `summarize_trades` slots, not `conditional_aggregate`); `WindowedPanel` row clarified (`conditional_aggregate` consumes via its `panel` slot). (i) `TimeSeriesUnits` enum list corrected — `DIMENSIONLESS` removed (not in the enum); real values are `PERCENT, BPS, Z_SCORE, RATIO, PCT_RANK, FACTOR_LEVEL, COUNT`. (j) Broken codebase paths fixed — `../../shared/...` → `../../../shared/...` (the right depth from `docs_revamped/02_components/artifact/`). | (pending) |
| v1 | 2026-05-17 | Initial artifact contract. Sixteen principles (ART1–ART16) organised in four groups: definitional (ART1–ART3 — structural shape ownership, closed-family membership, asset-class-blind types), admission (ART4–ART6 — ADR-gated extension, new shape not new use case, substrate-wide-impact commitment), well-formedness (ART7–ART11 — frozen / `extra=forbid`, mandatory structural metadata, mandatory lineage, content-addressed identity, validators raise at construction), operational (ART12–ART16 — closed enums for metadata, test pattern, bridge required for primitive-produced types, no naked pandas escape, closed-family-extension procedure). Closed family of six (Series, SeriesSet, EventSet, Panel, WindowedPanel, TradeSet) plus deferred-types note. Superseded by v1.1 the same day after a factual-review pass. | — |
