# Runbook — How to add a new artifact type to the closed family

> Step-by-step procedure for the **closed-family extension** that admits a new artifact type. The bar to clear is the highest in the platform's component system: an artifact type sits on the wire between every primitive, every operator, and every workflow template, and admitting one obligates the substrate to keep that shape working forever. Adding one is rare. Doing it correctly is non-negotiable.

**Version:** v1.1
**Last reviewed:** 2026-05-17
**Audience:** any contributor (human or AI agent) proposing a new artifact type. The audience for this runbook is *deliberately narrow* — most contributors will never trigger it; the right reflex when a new shape feels needed is to first verify against ART5 that an existing type cannot structurally carry the data.
**Prerequisite reading:** [`README.md`](README.md) — the artifact contract (ART1–ART16). Read it once before starting; refer back when a step says *"per principle ART<N>."*
**Operationalises principles:** P1 (built right, not as a placeholder), P3 (every artifact follows the same shape), [P8 (closed-family discipline at its most acute)](../../00_thesis/01_non_negotiables.md), P9 (asset-class-blind types), P10 (single source of truth — `ARTIFACT_TYPE_NAMES` is the only valid set), and artifact-specific ART1–ART16 throughout.
**AC class:** Adding a new artifact type is **Load-bearing** per [`../../00_thesis/02_ai_agent_development_contract.md`](../../00_thesis/02_ai_agent_development_contract.md), the heaviest such change in the component system. Run the full self-check; do not skip the gate; expect multiple reviewers.

---

## When to use this runbook

- **Introducing a structurally new shape** the platform's closed family does not yet express (e.g., a graph-shaped relational artifact; a sparse irregular-grid panel; a typed scalar-metric envelope; a typed ranked-result envelope).
- **Promoting a deferred design-note type** (`ScalarMetric`, `RankedResult`, `PositionPath`) into the closed family once the load-bearing use case has arrived.
- **Splitting an over-broad artifact type** that has accidentally accreted two structurally different shapes (ART1 violation discovered post-hoc).

## When NOT to use this runbook

- **Adding a new value to a closed metadata enum** (`TimeSeriesUnits`, `MissingnessPolicy`). That is an ADR-gated change too (ART12), but it follows a lighter-weight procedure — extend the enum centrally, update the consumers that branch on the new value, ship the tests. The eight-site rule (ART6) does not apply.
- **Adding a new helper inside an artifact type** (`LegSpec`, future `PositionPath`-as-leg-metadata, etc.). Helper / metadata sub-types are part of an artifact's contract but do not appear in `ARTIFACT_TYPE_NAMES`; they follow the lighter helper-type procedure (ADR + class + tests, no discriminator update).
- **Computing a new derived quantity that *fits* an existing shape.** A new kind of `Series` data, a new metric expressed as a single-row `Panel`, a regression-coefficient table expressed as `Panel` — these are use-case additions, not artifact-type additions. ART5 forbids admitting a new type for a new use case.
- **Building a research scratch shape that lives inside one workflow only.** That is workflow-local code; it does not need to be in the closed family. (Until a non-standard-artifact contract is written, such shapes must stay inside the workflow template's package.)

The dividing line: a new artifact type introduces a *new structural shape* the substrate's closed family does not yet express, *and* there is a real producer-consumer pair landing alongside (ART4 + ART6). An enum extension, a helper-type addition, a new use case of an existing shape, or a workflow-local scratch shape does not.

## Pre-flight check — six decisions BEFORE writing any code

A new artifact type PR is essentially impossible to undo: every operator, every workflow template, the executor, the validator, the artifact store all read from `ARTIFACT_TYPE_NAMES`, and a removal later requires migrating every consumer. Six decisions to make and document in the ADR (Step 1) before any code is drafted. **If any of these is uncertain, stop and ask the human (AC8).**

### 1. Structural-distinctness test (ART1, ART5) — what shape, and why can't an existing type carry it?

Write the new type's structural shape in one sentence using only structural terms: index type, payload shape, mandatory metadata fields, structural invariants. Then write down the existing artifact type whose shape is *closest* to the new one and argue **structurally** — not by use case — why that existing type cannot carry the data.

Concrete examples of valid structural arguments:

- *"`Panel` carries `[date × series]`; the new data is `[event × event-relative offset]` — a fundamentally different axis layout that `Panel` cannot express because Panel's row axis is calendar-indexed not event-indexed."* (This is exactly the structural argument that justifies `WindowedPanel`.)
- *"`Series` carries `date → number`; the new data is `node → number` over a graph — no calendar index at all, validators don't apply."*

Concrete examples of **invalid** arguments (use-case, not shape):

- *"We need a `YieldSeries` to make rates-specific code more readable."* No — that is `Series` with `units=PERCENT`; the structural shape is identical.
- *"`Panel` is fine but I want a different field name."* No — same shape; rename a metadata key inside `Panel` if needed, do not duplicate the type.
- *"We need a `RegressionResult` artifact to carry beta + alpha + r²."* No — that is a single-row `Panel` with three columns.

If the answer to *"what does this type's shape express that no existing type can?"* is anything other than a one-sentence structural argument, **stop**. The right path is almost always to use an existing type, extend an existing enum (ART12), or rename a field inside an existing type.

### 2. Asset-class-blindness test (ART3) — does the type embed any domain assumption?

Write the new type's purpose using only structural vocabulary. Forbidden: instrument, asset class, curve_family, tenor, sovereign, bond, swap, OIS, Treasury, currency, yield, spread, rate, FX pair, equity sector.

Concrete test: rewrite your one-sentence purpose as if the payload is temperature data, or as if it is equity prices. Does the type's job still make sense? If yes, candidate is asset-class-blind. If no (the type's job is specific to rates / FX / equities), the asset-class-specific behaviour belongs in primitive metadata or the workflow template, not in the artifact type.

Generic trading concepts (Trade, LegSpec, holding period, P&L) are allowed when their structural shape is identical across asset classes — the existing `TradeSet` is the precedent.

### 3. Producer-consumer pair (ART4, ART6) — what real code uses this?

Name the producer and the consumer that will land alongside the type. The producer is either a primitive (in which case a new adapter in `shared/artifacts/adapters/` lifts its output into the typed artifact) or an operator (in which case the operator's `OperatorSpec` lists this type as `output_type`). The consumer is at least one operator that lists this type in `OperatorSpec.input_slots`, or a workflow template that consumes it as a terminal artifact.

**No producer-consumer pair → no admission.** An artifact type that ships without a real consumer accumulates dead substrate forever — the codebase's hardest debt to pay down, because the type itself is referenced from `ARTIFACT_TYPE_NAMES`, the discriminator union, the validator, and the executor type-map, and removing those references later breaks every consumer that started using it in the interim.

If the producer-consumer pair will land in separate PRs, the ADR must document the explicit prerequisite chain — *"PR #N admits the type; PR #N+1 lands the consumer within X weeks; if PR #N+1 does not land by date Y, PR #N is reverted"*.

### 4. Structural-metadata coverage (ART8, ART12) — what mandatory fields, and do existing enums cover them?

List the structural-metadata fields the new type requires its instances to carry. For each:

- Does an existing closed-enum metadata family cover the value taxonomy (`TimeSeriesUnits`, `MissingnessPolicy`)? If yes, reuse the enum (ART12). If no, document the enum extension as part of this same PR — adding a parallel taxonomy is forbidden.
- Are the fields mandatory (no defaults) — i.e., is every well-formed instance of this type guaranteed to have them populated? If a field has a sensible default, it is probably a payload-side concern, not type-level metadata.

The artifact's metadata is what makes downstream operator compatibility checks (OPR11) possible. Under-specified metadata at admission time makes the type ergonomically permissive but structurally unsafe — every operator becomes responsible for re-deriving what metadata should have carried.

### 5. Validator invariants (ART11) — what must be true at construction?

List every structural invariant the type's `@model_validator(mode="after")` block will enforce. Examples from existing types:

- `Series`: DatetimeIndex; no duplicate index entries; sorted ascending; numeric dtype.
- `EventSet`: Mask has DatetimeIndex; mask dtype is bool; `len(event_dates) == len(per_event_metadata)`; `mask.sum() == len(event_dates)`.
- `WindowedPanel`: Payload is 2D; `payload.shape[0] == len(event_dates) == len(per_event_metadata)`; `payload.shape[1] == len(offsets)`.

For each invariant, write the negative case: *what malformed input is the validator catching, and what is the specific error message?* Generic error messages ("invalid input") fail ART11; each invariant gets a named, specific message.

### 6. Eight-site coverage (ART6) — every site listed?

Enumerate the eight sites this admission must touch (per ART6):

1. The Pydantic class file (`shared/artifacts/types.py` or a sibling like `shared/artifacts/trades.py`).
2. `ARTIFACT_TYPE_NAMES` tuple in [`shared/workflow/registry.py`](../../../shared/workflow/registry.py).
3. `ArtifactTypeLiteral` discriminator in [`state/schemas.py`](../../../state/schemas.py).
4. `artifact_type_name()` type-map in [`shared/workflow/registry.py`](../../../shared/workflow/registry.py).
5. Artifact-store codec in [`state/artifact_store.py`](../../../state/artifact_store.py) — `_ARTIFACT_CLASSES` entry, `_artifact_to_stored` / `_stored_to_artifact` dispatch branches, and a per-type encode/decode helper pair handling the pandas / numpy payload's JSON-friendly encoding.
6. Producer wiring (new adapter in `shared/artifacts/adapters/` for primitive-produced, or `OperatorSpec.output_type` for operator-produced).
7. Consumer wiring (operator `OperatorSpec.input_slots` referencing the new type, or workflow-template terminal-artifact declaration).
8. Tests (the four layers from ART13 plus an end-to-end integration test).

If any of the eight is missing from your PR plan, **stop**. The discipline only works because all eight land together.

---

## Step 1 — File the ADR

Create `../../05_decisions/<NNNN>_admit_<type_name>_artifact.md`. The ADR is mandatory under ART4; no PR drafts code before this file exists and the ADR's framing has had at least preliminary review.

The ADR must answer:

- **Shape** — what is the new type's structural shape (one paragraph, structural vocabulary only).
- **Structural distinctness (ART1, ART5)** — which existing type is closest, and why is it structurally inadequate (one paragraph).
- **Asset-class-blindness (ART3)** — confirm the type embeds no asset-class-specific assumption (one sentence + the temperature-data test).
- **Producer-consumer pair (ART4)** — the primitive / operator that produces this type, and the operator / workflow template that consumes it. Whether both land in the same PR or in a documented prerequisite chain.
- **Affected sites (ART6)** — all eight sites listed by file path; the diff scope per site (added class, added enum value, added codec helper, added adapter, etc.).
- **Mandatory metadata fields (ART8)** — the field list, the closed-enum coverage (or the centrally-extended enum if a new value is needed).
- **Validator invariants (ART11)** — the list of `@model_validator` invariants, each with its negative-case error message.
- **Tests (ART13)** — the test file path, the four-layer coverage plan, the integration-test file path.

The ADR is reviewed alongside the PR; merging the PR merges the ADR.

## Step 2 — Add the Pydantic class

Place the class in `shared/artifacts/types.py` (or a sibling file like `shared/artifacts/trades.py` for compound shapes that need helper classes).

```python
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.lineage import Lineage
from shared.artifacts.missingness import MissingnessPolicy
from shared.artifacts.units import TimeSeriesUnits


class <ArtifactType>(BaseModel):
    """<one-paragraph structural description; asset-class-blind framing>.

    Shape: <one-sentence shape statement>.
    Producers: <where this type is constructed>.
    Consumers: <which operators / templates consume this type>.
    """
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    payload: <typed_payload>
    # Mandatory structural metadata (ART8) — every field required, no
    # sensible defaults:
    <metadata_field_1>: <closed_enum_type>
    <metadata_field_2>: <closed_enum_type>
    # ...

    # Mandatory lineage chain (ART9):
    lineage: Lineage

    @model_validator(mode="after")
    def _validate_<shape>(self) -> "<ArtifactType>":
        # Structural invariants (ART11). Each block raises ValueError
        # with a specific named message — generic errors fail review.
        if <invariant_1_violated>:
            raise ValueError(
                f"<ArtifactType>: <specific invariant statement>"
            )
        if <invariant_2_violated>:
            raise ValueError(
                f"<ArtifactType>: <specific invariant statement>"
            )
        # ...
        return self
```

**Per ART7**: `frozen=True, extra="forbid", arbitrary_types_allowed=True` — no exceptions.

**Per ART8**: structural-metadata fields have no defaults. Every well-formed instance carries them populated at construction.

**Per ART9**: `lineage: Lineage` is mandatory, not `Optional[Lineage]`. The model accepts no instance without a populated chain.

**Per ART11**: every structural invariant has a named `@model_validator(mode="after")` block raising `ValueError` with a specific message.

**Per ART12**: metadata types reference closed enums (`TimeSeriesUnits`, `MissingnessPolicy`) — never inline `Literal` or parallel enum.

## Step 3 — Wire the closed-family enum and discriminator

Three sites, all in the same PR:

### 3a. `ARTIFACT_TYPE_NAMES`

Open [`shared/workflow/registry.py`](../../../shared/workflow/registry.py) and add the new type's name to the tuple:

```python
ARTIFACT_TYPE_NAMES: tuple[str, ...] = (
    "Series",
    "SeriesSet",
    "EventSet",
    "Panel",
    "WindowedPanel",
    "TradeSet",
    "<NewType>",        # ← added per ADR-NNNN
)
```

### 3b. `artifact_type_name()` type-map

In the same file, add the runtime-instance → name mapping:

```python
def artifact_type_name(artifact: Any) -> str:
    type_map = {
        Series: "Series",
        SeriesSet: "SeriesSet",
        EventSet: "EventSet",
        Panel: "Panel",
        WindowedPanel: "WindowedPanel",
        TradeSet: "TradeSet",
        <NewType>: "<NewType>",     # ← added
    }
    ...
```

### 3c. `ArtifactTypeLiteral` discriminator

In [`state/schemas.py`](../../../state/schemas.py), extend the discriminator literal so the artifact store can persist + deserialize the new type:

```python
ArtifactTypeLiteral = Literal[
    "Series",
    "SeriesSet",
    "EventSet",
    "Panel",
    "WindowedPanel",
    "TradeSet",
    "<NewType>",        # ← added
]
```

**Per ART6**: all three of the above land together. A PR that updates `ARTIFACT_TYPE_NAMES` without the type-map (or vice versa) is auto-reject — the validator will accept the name but the executor will refuse to label runtime instances, breaking workflow execution silently.

## Step 4 — Wire the artifact-store codec

The artifact store ([`state/artifact_store.py`](../../../state/artifact_store.py)) is what persists artifacts to Postgres + object storage and reads them back during replay. A new artifact type without codec registration is a runtime hazard: `put_artifact` raises `TypeError` on the first persistence attempt, the workflow node fails, and replays of any workflow that produces this type are impossible.

Four sub-sites, all in the same file:

### 4a. `_ARTIFACT_CLASSES`

```python
_ARTIFACT_CLASSES: Tuple[Tuple[str, type], ...] = (
    ("Series", Series),
    ("SeriesSet", SeriesSet),
    ("EventSet", EventSet),
    ("Panel", Panel),
    ("WindowedPanel", WindowedPanel),
    ("TradeSet", TradeSet),
    ("<NewType>", <NewType>),       # ← added
)
```

### 4b. `_artifact_to_stored` dispatch

```python
def _artifact_to_stored(artifact: Artifact) -> StoredArtifact:
    ...
    elif isinstance(artifact, TradeSet):
        meta, payload = _trade_set_to_stored(artifact)
    elif isinstance(artifact, <NewType>):                  # ← added
        meta, payload = _<new_type>_to_stored(artifact)    # ← added
    ...
```

### 4c. `_stored_to_artifact` dispatch

```python
def _stored_to_artifact(stored: StoredArtifact) -> Artifact:
    ...
    if cls is <NewType>:                                    # ← added
        return _<new_type>_from_stored(stored)              # ← added
    ...
```

### 4d. Per-type codec helpers

```python
def _<new_type>_to_stored(artifact: <NewType>) -> Tuple[Dict, Dict]:
    """Encode the artifact's payload + structural metadata as
    JSON-friendly dicts. Pandas/numpy payloads serialise via
    .to_dict() / .tolist(); date-like indexes via ISO strings."""
    metadata = {
        # All structural-metadata fields the type carries —
        # closed-enum values stringified, no Pydantic models.
    }
    payload = {
        # The payload encoded as JSON-friendly nested dicts.
    }
    return metadata, payload


def _<new_type>_from_stored(stored: StoredArtifact) -> <NewType>:
    """Reverse: lift the stored dict back into a typed artifact.
    Reconstruct pandas indexes / numpy arrays; rehydrate Pydantic
    nested models from their dict form."""
    # ... lift payload back into pd.Series / pd.DataFrame / np.ndarray
    # ... lift metadata back into closed-enum values + Pydantic models
    return <NewType>(
        payload=payload,
        <metadata_fields>=...,
        lineage=Lineage.model_validate(stored.payload["lineage"]),
    )
```

**Per ART6 + ART13**: the artifact-store round-trip test from Step 7a is what catches codec drift; it must exercise the real `put_artifact` / `get_artifact` path (with a `conn` and `object_storage` fixture), not a synthetic `model_dump_json()` shortcut.

## Step 5 — Wire the producer

Two paths, mutually exclusive:

### 5a. Primitive-produced — add a bridge adapter

If the new type can be produced by a primitive (the primitive's `compute.py` returns a dict; the bridge lifts it into the typed artifact), add a new adapter to `shared/artifacts/adapters/`:

```python
# shared/artifacts/adapters/from_<source_shape>.py

from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.types import <NewType>


def <source_shape>_to_artifact_<new_type>(
    <primitive_output>: <wire_shape>,
    *,
    tool_name: str,
    tool_version: str,
    tool_config_hash: str,            # SHA of the primitive's bundled config.yaml
    output_field: str,                # which field of the primitive's output payload this artifact lifts
    as_of_date: str,                  # ISO YYYY-MM-DD; the primitive's snapshot date
    tool_config_path: str | None = None,           # bookkeeping-only (NOT in hash)
    methodology_version_id: int | None = None,     # bookkeeping-only (NOT in hash)
    <metadata_args>: <closed_enum_types>,
) -> <NewType>:
    """Lift a primitive's <wire_shape> output into the typed
    <NewType> artifact, constructing the PrimitiveStep automatically.
    """
    payload = _lift_payload(<primitive_output>)
    step = PrimitiveStep.build(
        name=tool_name,
        version=tool_version,
        params=<primitive_input_params>.model_dump(),  # the primitive's *Input dict
        tool_config_hash=tool_config_hash,
        tool_config_path=tool_config_path,
        methodology_version_id=methodology_version_id,
        output_field=output_field,
        as_of_date=as_of_date,
        input_hashes=(),                                # v1: primitives have no input artifacts
    )
    return <NewType>(
        payload=payload,
        <metadata_args>=<metadata_args>,
        lineage=Lineage.from_steps([step]),
    )
```

**Per ART14**: the adapter constructs `lineage` starting with `PrimitiveStep` — no other path from primitive output to artifact is legal. The adapter populates every required structural-metadata field (ART8).

**Per ART10**: every content-defining choice the primitive made must appear in `params` (or in `tool_config_hash`, for choices that live in the config). `tool_config_path` and `methodology_version_id` are *bookkeeping-only* — they are NOT folded into the step hash, so changing them does not break identity. See [`shared/artifacts/lineage.py`](../../../shared/artifacts/lineage.py) for the canonical `PrimitiveStep.build` signature.

Re-export the adapter from `shared/artifacts/adapters/__init__.py`.

### 5b. Operator-produced — declare in `OperatorSpec.output_type`

If the new type is produced by an operator (not a primitive), no adapter is needed; the operator constructs the artifact directly (per OPR10's `OperatorStep.build(...) + Lineage.append(...)` pattern). The producer-side wiring is the operator's existing `OperatorSpec.output_type` field referencing the new type's name:

```python
OperatorSpec(
    operator_name="<operator_name>",
    callable=<operator_callable>,
    params_class=<OperatorParams>,
    input_slots={...},
    output_type="<NewType>",      # ← the new artifact type
)
```

The operator's `operator.py` follows the canonical operator pattern (see [`../operator/runbook.md`](../operator/runbook.md) Step 5); the lineage is extended via `OperatorStep.build + Lineage.append`; the artifact is constructed with all mandatory structural metadata.

## Step 6 — Wire the consumer

At least one consumer must land in the same PR (or in a documented follow-up per the ADR's prerequisite chain). Two paths:

### 6a. Operator consumer

Add (or update) an operator whose `OperatorSpec.input_slots` references the new type:

```python
OperatorSpec(
    operator_name="<consumer_operator_name>",
    callable=<consumer_callable>,
    params_class=<ConsumerParams>,
    input_slots={
        "<slot_name>": "<NewType>",      # ← consumes the new type
    },
    output_type="<some_existing_type>",
)
```

The consuming operator follows the operator contract (OPR1–OPR16). Its `operator.py` accepts the new type as a typed argument (ART15 — no naked pandas at boundaries), enforces structural-metadata compatibility (OPR11), and extends the lineage chain (OPR10).

### 6b. Workflow-template terminal consumer

Alternatively, a workflow template's terminal node — whose `output_type` is the new artifact — can be the consumer:

```yaml
# <agent>/workflows/<template>/template.yaml
template_id: <template_id>
archetype: <archetype_name>
description: >-
  <one-paragraph template description>

slot_schema:
  - name: <slot_name>
    type: <str|int|float>
    required: <true|false>
    description: >-
      <what this slot binds>

nodes:
  - kind: primitive                    # or "operator"
    node_id: <upstream_node>
    tool_name: {$slot: <slot_name>}    # for primitives; operators use operator_name
    output_field: {$slot: <slot_name>}
    params: {$slot: <slot_name>}

  - kind: operator
    node_id: <terminal_node>
    operator_name: <producer_operator>
    params:
      <param>: <value_or_slot_ref>

edges:
  - source_node_id: <upstream_node>
    target_node_id: <terminal_node>

terminal_node_id: <terminal_node>      # producer_operator's output_type
                                       # IS the workflow's terminal
                                       # artifact (the new <NewType>)
```

The terminal node's operator declares `output_type="<NewType>"` in its `OperatorSpec` (per Step 5b). The template's `terminal_node_id` points at that node, so the workflow's persisted terminal artifact is the new type. A template-terminal consumer is acceptable when the new type is meant to be the user-facing output (rendered in the UI as a card, persisted as a workspace artifact). The template must have at least one integration test that exercises the full chain end-to-end. See real templates such as [`rates_agent/workflows/backtest/template.yaml`](../../../rates_agent/workflows/backtest/template.yaml) for the canonical shape.

## Step 7 — Write the tests

All four ART13 layers, plus one end-to-end integration test. Place in `tests/test_artifacts.py` (or a dedicated `tests/test_artifact_<new_type>.py` if the type warrants its own file).

### 7a. Artifact-store round-trip test

The artifact-store round-trip exercises the real codec path (`_artifact_to_stored` → bytes → `_stored_to_artifact`); raw `model_dump_json()` on the artifact class is **not** a substitute because pandas / numpy payloads are not JSON-serializable by Pydantic alone — the typed codec is where the payload-to-JSON shape lives.

```python
from state.artifact_store import put_artifact, get_artifact

def test_<new_type>_artifact_store_round_trip(conn, object_storage):
    original = <NewType>(payload=..., <metadata>=..., lineage=...)
    artifact_hash = put_artifact(
        original, conn=conn, object_storage=object_storage,
    )
    restored = get_artifact(artifact_hash, conn=conn, object_storage=object_storage)
    assert isinstance(restored, <NewType>)
    assert restored.lineage.head_hash == original.lineage.head_hash
    # Per-type payload equality — for Series, this is pandas equality:
    pd.testing.assert_series_equal(restored.payload, original.payload)
    # For other types, the per-type equality check applies (e.g.
    # assert_frame_equal for Panel/WindowedPanel, list-equality for
    # TradeSet).
```

This test will fail at admission time if Step 4 (artifact-store codec) is missing or stale — that is by design; the codec is part of the eight-site landing (Step 8).

### 7b. Validator tests — one per invariant

For every `@model_validator` invariant from Step 5 of the pre-flight, one negative test and one positive test:

```python
def test_<new_type>_rejects_<invariant_name>():
    with pytest.raises(ValueError, match="<specific message fragment>"):
        <NewType>(payload=<malformed_input>, ...)


def test_<new_type>_accepts_well_formed_<scenario>():
    artifact = <NewType>(payload=<well_formed_input>, ...)
    assert artifact.<metadata_field> == <expected>
```

### 7c. Lineage-propagation test

```python
def test_<new_type>_preserves_upstream_lineage():
    upstream_lineage = Lineage.from_steps([<seed_step>, <intermediate_step>])
    artifact = <NewType>(
        payload=...,
        <metadata>=...,
        lineage=upstream_lineage,
    )
    assert len(artifact.lineage.steps) == 2
    assert artifact.lineage.head_hash == upstream_lineage.head_hash
```

### 7d. Immutability test

```python
def test_<new_type>_is_frozen():
    artifact = <NewType>(payload=..., <metadata>=..., lineage=...)
    with pytest.raises((ValidationError, TypeError)):
        artifact.<metadata_field> = <new_value>


def test_<new_type>_rejects_unknown_field():
    with pytest.raises(ValidationError):
        <NewType>(payload=..., <metadata>=..., lineage=..., bogus_field="x")
```

### 7e. End-to-end integration test

The producer-consumer pair from Step 4 + Step 5 wired through the workflow executor (if the consumer is an operator) or the template executor (if the consumer is a template terminal):

```python
def test_<new_type>_end_to_end_through_real_producer_and_consumer():
    # 1. Produce via the real adapter / operator
    produced = <producer>(<inputs>)
    assert isinstance(produced, <NewType>)
    assert produced.lineage.head_hash is not None

    # 2. Consume via the real downstream operator / template
    consumed_output = <consumer_operator>(produced, ...)
    assert isinstance(consumed_output, <downstream_artifact_type>)
    assert consumed_output.lineage.head_hash != produced.lineage.head_hash
    # The chain extended by one step
    assert len(consumed_output.lineage.steps) == len(produced.lineage.steps) + 1
```

**Per ART13**: there is **no SQL parity test** — artifacts have no compute path; SQL parity is a primitive-layer concept.

**Cross-asset-robustness coverage (ART3)**: at least one of the validator-positive tests uses non-rates synthetic data (random walks, temperature data, equity prices) to confirm the type does not embed asset-class assumptions.

## Step 8 — Confirm the eight-site landing

Before requesting review, run a final check that every site listed in the ADR is updated in the same PR (or the prerequisite chain is explicit and dated):

```bash
# Sanity: the new type name appears in every required site
grep -rn "<NewType>" \
    shared/artifacts/types.py \
    shared/artifacts/trades.py \
    shared/workflow/registry.py \
    state/schemas.py \
    state/artifact_store.py \
    shared/artifacts/adapters/ \
    shared/operators/ \
    tests/
```

If any required site is missing, the PR is not ready. Half-landed extensions are auto-reject (ART6).

## Step 9 — Run CI and structural checks

```bash
pytest tests/test_artifacts.py tests/test_<new_type>.py -v
pytest tests/                       # full suite — confirm nothing downstream regressed
python -m shared.config.lint        # confirm no config-side fallout
```

The lint does not currently enforce the artifact-side eight-site rule mechanically — the gate is the PR review checklist. CI green plus manual checklist review is the substitute.

## Automation scope — what the build-bot is and is not allowed to do

Artifact-type admission is **not bot-eligible**, full stop. The automation bot at `tmp/automation/primitive_automation/` is scoped to primitives (specifically, Bucket 1A standard primitives — Archetype A / B in level / spread shape). Operators and artifacts are explicitly outside the bot's scope.

This is deliberate and unlikely to change. The closed-family-extension judgment requires:

- Structural-distinctness argument (ART1, ART5) — judgment about whether an existing type can carry the shape.
- Asset-class-blindness argument (ART3) — judgment about future cross-asset use.
- Producer-consumer pairing decision (ART4) — coordination across multiple PRs that no template scaffolding can author.
- Substrate-wide impact assessment (ART6) — every downstream consumer is affected; the review is human-multi-party.
- ADR authorship (ART4) — institutional decision-making, not pattern-matching.

If a bot ever proposes an artifact-type addition, the PR is auto-reject regardless of the change's apparent correctness.

## Common pitfalls

Things reviewers see repeatedly when this runbook is followed loosely:

- **"We just need a `YieldSeries` to make the rates code more readable."** ART3 + ART5 violation. The shape is `Series`; readability lives in primitive metadata and variable naming, not in the artifact type.
- **"We need a `RegressionResult` artifact for the regression workflow."** ART5 violation. A regression coefficient table is a `Panel` with one row per date and columns `(beta, alpha, r_squared)` plus per-column units. The shape already exists.
- **"We need a `ScalarMetric` artifact for the summary statistic."** This is a tracked open question (ART open-questions catalogue) — the deferred `ScalarMetric` type is the right home, admitted via this runbook when the load-bearing use case arrives. Until then, use single-row `Series` / `Panel`.
- **PR adds the class and the enum but not the discriminator + type-map.** ART6 violation; auto-reject. The validator will accept the type name but the artifact store cannot deserialize it, breaking persistence silently.
- **PR adds the type but no producer-consumer pair.** ART4 violation; the type accumulates as dead substrate. Either land the pair in the same PR or explicitly chain it in the ADR.
- **Validator has only a "happy path" check and no negative messages.** ART11 violation. Every invariant gets a specific named error message; generic "invalid input" fails review.
- **Validator returns `False` or prints a warning instead of raising.** ART11 violation — silent failure. Always `raise ValueError(...)`.
- **Mandatory metadata fields declared as `Optional[...]` with `None` defaults.** ART8 violation. Mandatory means mandatory; if it can be `None`, it is not mandatory and probably belongs in payload, not metadata.
- **Parallel enum introduced** (`class PanelUnits(Enum)` next to `TimeSeriesUnits`). ART12 violation; extend the existing enum centrally.
- **Adapter constructs the artifact with `lineage=Lineage(steps=[])`** or `lineage=None`. ART9 violation; the artifact store will reject it at persistence time, but the runtime error is far from the construction site.
- **Operator's `operator.py` returns `pd.DataFrame` "until the new artifact type lands"** as an interim step. ART15 violation. Either land the artifact type first and then the operator, or chain them in the ADR's prerequisite plan — never ship a transitional naked-pandas escape.
- **Test suite covers only the happy path.** ART13 violation. Every validator invariant gets a negative test.
- **Adapter lives outside `shared/artifacts/adapters/`** (e.g., inside an agent folder or an operator's package). ART14 violation; all primitive → artifact lifts live in the central adapter folder.
- **The PR description does not cite ART-numbers.** AC2 + AC6 violation (do not paraphrase principles; cite by ID). Reviewers cannot trace the change to the contract.

## PR review checklist

The reviewer signs off when each item is met. Cite the matching ART-number; do not paraphrase (AC2). Artifact-type admission deserves multi-reviewer sign-off — at least two reviewers familiar with the substrate.

### Closed-family discipline (ART2, ART4, ART6)

- [ ] **ART2.** The new type appears in `ARTIFACT_TYPE_NAMES` (`shared/workflow/registry.py`) and the discriminator (`state/schemas.py`'s `ArtifactTypeLiteral`).
- [ ] **ART4.** An ADR exists in `../../05_decisions/` documenting the new type, the structural-distinctness argument, the asset-class-blindness confirmation, and the producer-consumer pair.
- [ ] **ART6.** All eight sites are updated in the same PR (or an explicit, dated prerequisite chain in the ADR): Pydantic class, `ARTIFACT_TYPE_NAMES`, `ArtifactTypeLiteral`, `artifact_type_name()` type-map, **artifact-store codec** (`_ARTIFACT_CLASSES` + `_artifact_to_stored` + `_stored_to_artifact` + per-type helpers in `state/artifact_store.py`), producer wiring (adapter or operator), consumer wiring (operator slot or template terminal), tests.

### Structural shape and identity (ART1, ART3, ART5)

- [ ] **ART1.** The type's structural shape is distinct from every existing type — a one-sentence shape contrast against each existing type is in the ADR.
- [ ] **ART3.** No asset-class-specific field, name, or validator branch. The "temperature-data test" passes.
- [ ] **ART5.** The structural argument names the closest existing type and explains why its shape cannot carry the data. The argument is about shape, not use case.

### Well-formedness (ART7–ART11)

- [ ] **ART7.** `model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)`.
- [ ] **ART8.** Every required structural-metadata field is declared; none has a default making it optional in practice. Closed-enum types from `TimeSeriesUnits` / `MissingnessPolicy` referenced where applicable.
- [ ] **ART9.** `lineage: Lineage` is mandatory; no `Optional`, no default-empty.
- [ ] **ART10.** Artifact identity is `lineage.head_hash`; the test layer confirms two constructions with the same lineage produce the same `head_hash`.
- [ ] **ART11.** `@model_validator(mode="after")` block exists; every structural invariant has a specific named `ValueError` message; every invariant has both a positive and a negative test.

### Operational (ART12–ART16)

- [ ] **ART12.** No parallel enum introduced. New metadata values, if any, extended the existing `TimeSeriesUnits` / `MissingnessPolicy` enums centrally (with a parallel ADR if the extension is non-trivial).
- [ ] **ART13.** Tests cover all four layers: round-trip JSON, validator (every invariant, positive + negative), lineage propagation, immutability. Plus the end-to-end integration test using the real producer + real consumer. At least one validator-positive test uses non-rates synthetic data.
- [ ] **ART14.** If primitive-produced, an adapter exists in `shared/artifacts/adapters/`; no primitive's `compute.py` constructs the artifact directly. If operator-produced, the operator follows OPR10's lineage-extension pattern.
- [ ] **ART15.** No `pd.DataFrame`, `pd.Series`, or `np.ndarray` at any boundary involving the new type (operator signatures, workflow edges, template node-outputs). Internal payload extraction inside operator bodies is allowed.
- [ ] **ART16.** The runbook (this file) was followed; the PR description cites it.

### Universal items

- [ ] **AC2 / AC6.** Commit message ends with `Operationalises: P3, P8, P9; ART1, ART4, ART6, ART7, ART8, ART9, ART11, ART13, ART14; AC1, AC3, AC5, AC6.` (adjust IDs to whichever apply).
- [ ] **AC8.** Any uncertainty about structural-distinctness, asset-class-blindness, or producer-consumer pairing was raised with a human reviewer before code was drafted, not after.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.1 | 2026-05-17 | Pre-canonical corrections aligned with the README v1.1 revisions: (a) artifact-store codec promoted to a first-class admission site — seven sites → eight sites; new Step 4 ("Wire the artifact-store codec") added between discriminator and producer wiring, with sub-steps for `_ARTIFACT_CLASSES`, `_artifact_to_stored`, `_stored_to_artifact`, and the per-type encode/decode helper pair in `state/artifact_store.py`. (b) Test layer 7a reframed as **artifact-store round-trip** through real `put_artifact` / `get_artifact`, replacing the incorrect raw `model_dump_json()` example (pandas / numpy payloads are not JSON-serializable by Pydantic alone — the typed codec is where the payload encoding lives). (c) Step 5 (producer) adapter skeleton — corrected `PrimitiveStep.build` signature to `name=..., version=..., params=..., tool_config_hash=..., output_field=..., as_of_date=..., input_hashes=...` (was incorrectly `tool_name=..., tool_version=...`). (d) Step 5b operator-producer + Step 6a consumer — corrected `OperatorSpec(operator_name=...)` field (was incorrectly `name=...`). (e) Step 6b workflow-template terminal example — replaced placeholder `id/operator/output` structure with the real template shape (`kind`, `node_id`, `operator_name`/`tool_name`, `edges` with `source_node_id`/`target_node_id`, `terminal_node_id`). (f) Step 8 grep — added `state/artifact_store.py` and updated to eight-site language. (g) PR review checklist ART6 item updated to enumerate the eight sites and call out the codec explicitly. | (pending) |
| v1 | 2026-05-17 | Initial runbook for adding a new artifact type to the closed family. Six pre-flight decisions, eight-step procedure (ADR → class → enum+discriminator+type-map → producer → consumer → tests → seven-site landing confirmation → CI). Superseded by v1.1 the same day after a factual-review pass aligned with the README's corrections. | — |
