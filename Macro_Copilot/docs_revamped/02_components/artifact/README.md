# Artifact

> The contract every artifact in the Macro Copilot platform must satisfy — what makes something a valid artifact at all, what makes a particular artifact well-formed, and the principles that govern when (and how) a new artifact type may be added to the closed family. **Asset-class-blind by design** — artifact types are structural wrappers; the data inside may be finance-domain data (yields, prices), but the *type itself* is shape-only and runs equally on rates, FX, equities, or any indexed data.

**Version:** v2.0
**Last reviewed:** 2026-05-30
**Status:** load-bearing component contract. Changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Operationalises principles:** P3 (consistency by contract), P4 (determinism — every artifact is content-addressed by `lineage.head_hash`), P5 (honest disclosure — structural metadata is surfaced on the artifact itself), **P8 (closed-family discipline — this contract is the platform's primary closed family)**, P9 (finance-blind — artifact types are structural, not asset-class-specific), P10 (single source of truth — one canonical closed-family enum, every other site derived).
**See also:** [`runbook.md`](runbook.md) — the procedure for adding a new artifact type. [`../operator/README.md`](../operator/README.md) — the **co-equal** operator contract (OPR1–OPR16). Operators are the machines; artifacts are the standardised parts that flow between them. An operator cannot be standard unless the artifact types it consumes and emits are standard, so the two contracts are **hardened in lock-step**.

> **v2.0 is a foundational reset, paired with the operator v2.0 reset.** The operator audit found that the single largest cluster of operator defects was *not* in the operators — it was here: artifact types that do not enforce their own shape (only `Series` validated its index; `EventSet`/`Panel` accepted duplicate/unsorted indices), a lineage layer with **zero integrity guards** (a forgeable `head_hash`, disconnected chains, and `NaN`-in-params all accepted), and a closed family enumerated in three-plus hand-maintained authorities that had already drifted. v2.0 makes "valid artifact" mean the **same strict thing for every type**, adds lineage integrity guards, single-sources the closed family, removes `TradeSet` (its operators are relocated to primitives), and recommends admitting a first-class `ScalarMetric`. Each change is called out in the relevant principle and the Version log (see [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md)).

---

## What this folder is

The contract every artifact must satisfy. Artifacts are the **typed wire format** between layers: primitives produce them (via the bridge), operators consume and emit them, the workflow executor passes them between nodes, the artifact store persists them. They are the *only* legal data shape inside the operator and workflow layers — naked `pd.DataFrame` / `pd.Series` / `np.ndarray` do not cross those boundaries.

The artifact layer is **structurally different** from the primitive and operator layers:

- **Primitives and operators are *folders*** — many instances of one four-file contract.
- **Artifacts are *types in a closed family*** — each is one entry in the canonical closed-family enum plus a Pydantic class. The whole family is the architecture; adding an entry is a P8-gated extension, not a routine addition.

Organised as **principles** in four bins: definitional (ART1–ART3, *is this actually an artifact?*) → admission (ART4–ART6, *should this type exist?*) → well-formedness (ART7–ART11, *what makes an instance valid?*) → operational (ART12–ART16, *the build conventions every artifact follows*).

## What an artifact *is* — the universal contract

Every artifact is a **frozen Pydantic class** in `shared/artifacts/types.py`, carrying three load-bearing parts:

```python
class <ArtifactType>(BaseModel):
    model_config = ConfigDict(
        frozen=True,                  # immutable; new artifact = new object
        extra="forbid",               # unknown fields rejected at construction
        arbitrary_types_allowed=True, # for pd.Series / pd.DataFrame / np.ndarray payloads
    )

    payload: <typed_payload>          # pd.Series, pd.DataFrame, np.ndarray, or scalar
    <structural_metadata_fields>      # units, frequency, missingness_policy, ... (varies by type)
    lineage: Lineage                  # content-addressed chain back to the L1 read

    @model_validator(mode="after")
    def _validate_<shape>(self) -> "<ArtifactType>":
        _validate_datetime_index(self.payload.index, "<ArtifactType>")   # SHARED, every indexed type (ART11)
        # + type-specific invariants (numeric/finite dtype, mask==dates, panel-column-units, ...)
        return self
```

The closed family (v2.0) is declared **once** in a canonical enum (`shared/workflow/registry.py::ARTIFACT_TYPE_NAMES`), from which every other site is derived (ART2):

| Type | Payload | What it represents |
|---|---|---|
| **`Series`** | `pd.Series` (DatetimeIndex, numeric, finite-or-NaN) | A single indexed numeric series — yield, price, rate, anything `date → number`. |
| **`SeriesSet`** | keyed dict of `Series` (aligned to a common index) | An aligned keyed collection. Output of alignment / regression. |
| **`EventSet`** | `pd.Series[bool]` mask + ordered event-dates + per-event metadata | Discrete events firing at timestamps. Output of thresholding. |
| **`Panel`** | `pd.DataFrame` (DatetimeIndex, per-column units, `sub_kind`) | Wide tabular `[date × column]`. |
| **`WindowedPanel`** | `np.ndarray [n_events, window_length]` + offsets | N event windows over a target series. |
| **`ScalarMetric`** | a single finite number + `units` | A single statistic (a full-sample correlation, a cointegration test stat). Replaces the single-row-`Series` + sentinel-date hack. |

**Removed in v2.0:** **`TradeSet`** — its only producer/consumers (`construct_trades`, `evaluate_trades`, `summarize_trades`) are finance-aware and were relocated to the primitive layer per [OPR6](../operator/README.md). `TradeSet` therefore leaves the operator-composable closed family; if a future backtest *primitive* emits it, it becomes a primitive-output type and is re-admitted via the ART4 procedure at that time.

Two structural-metadata families are themselves closed enums reused by every type (ART12):
- **`TimeSeriesUnits`** — `PERCENT, BPS, Z_SCORE, RATIO, PCT_RANK, FACTOR_LEVEL, COUNT` (extend centrally, never fork).
- **`MissingnessPolicy`** — discriminated union: `CleanSingleSeriesV1`, `RawNoCleaning`, `AlignSeriesFFillV1`.

## What an artifact is *not*

- **Not a computation.** Artifacts describe shape; they don't transform data. No `compute_zscore()` method on `Series` (that's an operator).
- **Not a domain model.** An artifact doesn't know whether its payload is yields or FX — it knows the structural shape. Domain interpretation lives in primitives and templates.
- **Not a database row.** In-memory typed wrappers; persistence is `state/artifact_store.py`, not the class.
- **Not a configuration object.** Pure schemas; no `defaults:`, no YAML.
- **Not optional.** Every artifact carries a non-empty `Lineage`. An artifact without lineage is malformed and the validator rejects it.

## Quick index — the ART-numbers

| ID | Group | Principle | One-line rule |
|---|---|---|---|
| **ART1** | I | Structural shape ownership | One structural shape per type; the type name names the shape, never a use case. |
| **ART2** | I | Closed-family membership (single source) | The valid set is one canonical enum; every other site (discriminator, type-map, terminal, store codec) is **derived** from it. |
| **ART3** | I | Asset-class-blind types | The type is structural; its payload may be finance data, but the type's fields/validators never branch on asset class. |
| **ART4** | II | Closed-family extension is ADR-gated | Adding/removing a type is the most heavily gated change in the platform. |
| **ART5** | II | New shape, not new use case | Admit a type only when no existing type can *structurally* carry the data. |
| **ART6** | II | Substrate-wide-impact commitment | A type change touches every derived site; all land together (lock-step test). |
| **ART7** | III | Frozen, immutable, `extra="forbid"` | `frozen=True, extra="forbid", arbitrary_types_allowed=True`; construct, never mutate. |
| **ART8** | III | Mandatory structural metadata (incl. derived frequency) | Every type carries its required metadata; `frequency` is **derived at the adapter**, not left `None`. |
| **ART9** | III | Mandatory lineage chain + integrity guards | Non-empty `Lineage`; `head_hash == steps[-1].hash`; chain connectivity; `.build()` is the only correct path. |
| **ART10** | III | Content-addressed identity | Identity is `lineage.head_hash` over the recipe; producers fold every content-defining choice into `step.params` (sanitised, finite). |
| **ART11** | III | Validators raise at construction (uniform, strict) | One **shared** index/dtype/finiteness validator across **all** types; `±Inf` forbidden; EventSet semantic invariants enforced. |
| **ART12** | IV | Closed enums + typed escape hatches | `TimeSeriesUnits` / `MissingnessPolicy` closed; `per_event_metadata` gets a typed per-producer key contract. |
| **ART13** | IV | Test pattern | Round-trip + validator + lineage-propagation + immutability, plus the cross-type uniform-validator test. |
| **ART14** | IV | Bridge / adapter for primitive-produced types | The adapter is the only primitive→artifact path; it derives + populates frequency/units/missingness. |
| **ART15** | IV | No naked pandas escape | Artifacts are the only legal carrier between operator/workflow functions. |
| **ART16** | IV | Closed-family-extension procedure | The documented runbook procedure; all derived sites land together. |

---

## Group I — Definitional

### ART1 — Structural shape ownership

**Rule.** An artifact owns one *structural shape* — the type-level description of how its payload is organised (a single Series, a keyed collection, an event mask + dates, a wide panel, a windowed panel, a scalar). Each type captures a structurally distinct organisation that no other type expresses.

**Why.** Closed-family type-safety (ART2). Operators dispatch on shapes; if two types had the same shape the algebra would be ambiguous.

**Verify.** Each type is structurally distinct in one sentence (*"Series is one column over a DatetimeIndex; Panel is many; WindowedPanel is `[event × offset]`; ScalarMetric is a single number"*). The class name names the shape (`Series`, not `YieldSeries`). No asset-class assumptions.

**Anti-patterns.** `YieldPanel` (use `Panel` with yields in the payload); `RegimeEvents` vs `BreakoutEvents` (both are `EventSet`).

**Exceptions.** None.

**Relates to.** Analog of [PR1](../primitive/README.md) / [OPR1](../operator/README.md).

### ART2 — Closed-family membership (single source of truth)

**Rule.** The valid set of artifact types is declared **once**, in the canonical enum `ARTIFACT_TYPE_NAMES` (`shared/workflow/registry.py`). **Every other authority is derived from it, not hand-maintained in parallel**: the discriminator union (`state/schemas.py::ArtifactTypeLiteral`), the executor type-map (`artifact_type_name()`), `WorkflowResult.TerminalArtifact`, and the artifact-store codec map (`_ARTIFACT_CLASSES`). A lock-step test asserts all derived sites equal the canonical enum.

The family today (v2.0):

```python
ARTIFACT_TYPE_NAMES = ("Series", "SeriesSet", "EventSet", "Panel", "WindowedPanel", "ScalarMetric")
# TradeSet removed (relocated to the primitive layer with its operators — OPR6).
# ScalarMetric admitted in v2.0 (ART4/ART5); lands via the ART16 procedure with the correlation reference build.
```

**v2.0 supersedes v1**, where the family was enumerated in 3+ hand-maintained places that had **already drifted** — `TerminalArtifact` omitted `TradeSet` though the registry produced it, so a workflow ending on a `TradeSet` failed validation. Single-sourcing makes that class of drift structurally impossible.

**Why.** [P8](../../00_thesis/01_non_negotiables.md) + [P10](../../00_thesis/01_non_negotiables.md). Every downstream consumer reads the family; if the authorities disagree, the substrate breaks in confusing, deferred ways. One source + derived sites + a lock-step test is the only sustainable shape.

**Verify.**
- A single canonical enum exists; the discriminator, type-map, `TerminalArtifact`, and store-codec map are derived from it (or a lock-step test asserts equality).
- `grep` for `class .*\(BaseModel\)` in `shared/artifacts/` returns exactly the closed-family classes + helper types.
- Every operator `SlotDescriptor` type is a member of the enum.

**Anti-patterns.** A type in one authority but not another; a new artifact-shaped class outside `shared/artifacts/`; "just for this workflow, a `RegressionResult` type" (use `Panel`/`SeriesSet`, or file an ART4 ADR).

**Exceptions.** Helper types (`MissingnessPolicy` variants, `LineageStep` variants) are part of the contract but not artifact types and don't appear in the enum.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md), [P10](../../00_thesis/01_non_negotiables.md).

### ART3 — Asset-class-blind types

**Rule.** An artifact *type* is structural — it knows the shape of its payload, not the asset class. The data *inside* an instance may be asset-class-specific (a `Series` of UST yields); the type's fields and validators never branch on asset class. This is the type-level operationalisation of [OPR6](../operator/README.md): operators are finance-blind because the types they consume/emit are.

**Why.** Cross-asset portability. The same `Series` carries yields today, FX tomorrow; the structural validators hold uniformly. If the type knew the asset class, P9 collapses at the type level.

**Verify.** No artifact branches on asset-class identity; no name embeds an asset class (`YieldSeries`, `EquityPanel` wrong); a non-rates round-trip test passes for every type.

**Anti-patterns.** An `asset_class` field on a type; `if units == BPS:` branching in a validator (data-level reasoning leaking into the type); an asset-class-named type.

**Exceptions.** None.

**Relates to.** [P9](../../00_thesis/01_non_negotiables.md), [OPR6](../operator/README.md).

---

## Group II — Admission

### ART4 — Closed-family extension is ADR-gated

**Rule.** Adding **or removing** an artifact type is the most heavily gated change in the platform's component system. It requires: (1) an ADR naming the shape, the use case, the structural argument (ART5), and the derived sites (ART6); (2) simultaneous updates to every derived site, all in one PR (or an explicit prerequisite chain); (3) a demonstrated producer-consumer pair. The discipline is *much* tighter than the primitive PR4 or operator OPR4 admission — a primitive or operator may ship for a single use; an artifact type may not.

**v2.0 note.** The two v2.0 family changes are themselves ART4 decisions, recorded here pending their ADRs: **remove `TradeSet`** (its operators relocated — [OPR6](../operator/README.md)) and **add `ScalarMetric`** (recommended — see ART5 + Open questions).

**Why.** [P8](../../00_thesis/01_non_negotiables.md) is most acute here: every operator, the validator, the executor, and the store read the family. A casual change breaks every site.

**Verify.** The ADR exists and names the derived sites + rationale; the PR updates every site (lock-step test green); a working producer-consumer pair lands with it.

**Anti-patterns.** A class added without updating the enum; a type with no consumer ("wire it later"); any extension without an ADR.

**Exceptions.** None.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md); ART6.

### ART5 — New shape, not new use case

**Rule.** Admit a new type only when existing types **cannot structurally carry** the data. A new *use case* for an existing shape (a new kind of `Series` data, a new `TimeSeriesUnits` value) does not warrant a new type — extend the metadata enum or emit an existing type with new values.

**The `ScalarMetric` case (why it qualifies).** A full-sample correlation, a covariance, a cointegration test statistic, a Sharpe-free summary number — these are genuinely a *single number*, not a time series. v1 forced them into a single-row `Series` stamped at a fake `SUMMARY_SENTINEL_DATE` so two summaries could compose. That hack is load-bearing and ugly: the "date" is a lie, and the toolbox of statistical operators (correlation/covariance/cointegration — the next build) will *predominantly* output scalars. A first-class `ScalarMetric` (a finite number + `units` + `lineage`) is the structurally-honest shape. **This is admitted in v2.0** (decided); it lands via the ART16 procedure alongside the `correlation` reference build.

**Why.** Every type compounds (every operator gains a shape to handle); admit only when no existing type carries the shape *honestly*. The sentinel-date `Series` fails "honestly" — the index is fictional.

**Verify.** The PR names an existing type and explains why its shape cannot carry the data; the argument cites structural fields, not the use case.

**Anti-patterns.** `YieldSeries` (that's `Series` + `units`); `RegressionResult` (that's `Panel`); a new type because "rounding differs."

**Exceptions.** None.

**Relates to.** ART4; analog of [PR5](../primitive/README.md) / [OPR5](../operator/README.md).

### ART6 — Substrate-wide-impact commitment

**Rule.** A type change touches every derived site; all land together (or an explicit prerequisite chain). With ART2's single-source enum, the sites are *derived*, so the commitment is: update the canonical enum **and** confirm (via the lock-step test) that the discriminator, type-map, `TerminalArtifact`, store codec, producer wiring, consumer wiring, and tests all follow. A PR that lands one site without the others is auto-reject.

**Why.** The closed family only works if it stays internally consistent. The lock-step test is what enforces consistency at admission time — it would have caught the v1 `TradeSet`-missing-from-`TerminalArtifact` drift.

**Verify.** The PR diff touches the canonical enum; the lock-step test passes; `grep -rn "<type>"` finds it at every required site; CI is green.

**Anti-patterns.** A "preparation" PR adding only the class + enum; a discriminator entry without a store codec.

**Exceptions.** Helper/metadata types follow the lighter ART12 procedure.

**Relates to.** ART4; the lock-step test is what makes ART4 safe.

---

## Group III — Well-formedness

### ART7 — Frozen, immutable, `extra="forbid"`

**Rule.** Every artifact class declares `model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)`. `frozen` and `extra="forbid"` are non-negotiable: artifacts are constructed, never mutated; unknown fields fail loudly.

**Why.** [P4](../../00_thesis/01_non_negotiables.md). A mutable artifact has an unstable identity — the same `head_hash` could refer to different content. The convention "treat the wrapped pandas object as immutable; copy on transformation" is enforced by review (Python can't freeze the payload deeply).

**Verify.** Every class has the three-flag `model_config`; a test asserts field reassignment raises and an unknown field raises.

**Anti-patterns.** A class without `frozen=True`; `extra="ignore"`/unset; a method that mutates `self.payload`.

**Exceptions.** None.

**Relates to.** [P4](../../00_thesis/01_non_negotiables.md); analog of [OPR14](../operator/README.md).

### ART8 — Mandatory structural metadata (including derived frequency)

**Rule.** Every artifact carries the structural-metadata fields its type requires, populated at construction (never `None` where the type requires a value):

| Type | Required structural metadata |
|---|---|
| `Series` | `series_key`, `units`, `missingness_policy`, `frequency`, `lineage` |
| `SeriesSet` | `units_by_key`, `missingness_by_key`, per-key upstream lineages, `frequency`, `lineage` |
| `EventSet` | `mask`, ordered `event_dates`, `per_event_metadata`, `source_series_key`, `frequency`, `lineage` |
| `Panel` | `units_by_column`, `missingness_policy`, `sub_kind`, `lineage` |
| `WindowedPanel` | `offsets`, `event_dates`, `per_event_metadata`, `target_series_key`, `units`, `lineage` |
| `ScalarMetric` | `value` (finite), `units`, `lineage` |

**`frequency` is load-bearing in v2.0 (founder decision #3).** It is **derived deterministically at the bridge** (`pd.infer_freq` or the primitive's known cadence) and populated on every production artifact — not left `None`. An `'irregular'` value disambiguates event-derived series from `None`-unknown. This is what makes the operator-layer frequency checks ([OPR11](../operator/README.md)) real instead of no-ops.

**Why.** Structural metadata is what makes the operator compatibility checks possible. Without `units`, `series_arithmetic` can't refuse a BPS+PERCENT add; without a *populated* `frequency`, daily and monthly series mix invisibly.

**Verify.** Every class declares the fields above; every adapter/producer populates them (not `None`, not guessed from the payload); a production artifact has a non-`None` `frequency`; a test constructs each type and asserts metadata presence.

**Anti-patterns.** `units=None` where required; `frequency` left unset when the source has a known cadence; a bridge guessing metadata from the payload instead of taking it explicitly.

**Exceptions.** `frequency=None` is legal only for a genuinely aperiodic source, documented; prefer `'irregular'` for event-derived series.

**Relates to.** [OPR11](../operator/README.md); [P5](../../00_thesis/01_non_negotiables.md); ART14 (the adapter derives it).

### ART9 — Mandatory lineage chain + integrity guards

**Rule.** Every artifact carries a non-empty `Lineage`, and the `Lineage` model itself enforces integrity (v2.0 adds the guards):

1. `head_hash == steps[-1].hash` (the cheap-equality invariant).
2. **Chain connectivity** — the primary chain's `input_hashes` include the prior head, so a disconnected/forged chain is rejected at construction.
3. **`.build()` is the only correct path** — direct `OperatorStep(...)`/`Lineage(...)` construction that bypasses hash recomputation is rejected; producers must use `OperatorStep.build` / `Lineage.append`.
4. **Params are finite** — `step.params` is passed through `sanitize_params_for_lineage` (NaN/Inf → `None`) before hashing; the lineage layer never receives a non-finite float.

**v2.0 supersedes v1**, where `Lineage` had **no guards at all** (verified: a forgeable `head_hash`, a disconnected append-chain, and an `OperatorStep` with `NaN` params + an arbitrary hash were all accepted) — and the NaN-rejection-without-sanitisation turned every computed-float-in-params into a latent crash (`summarize_series` was red on its default path).

**Why.** [P4](../../00_thesis/01_non_negotiables.md). Lineage is the replay substrate and the disclosure substrate; if it can be forged or silently broken, the audit story is decorative.

**Verify.** Every instance has `len(lineage.steps) >= 1`; a forged `head_hash`, a disconnected chain, and a NaN-param step each raise at construction; the canonical bridge produces a single `PrimitiveStep`, an operator appends one `OperatorStep`.

**Anti-patterns.** `lineage=None` / empty chain; an operator that drops lineage; a producer recording a methodology choice on metadata but **not** in `step.params` (two different choices then collide on `head_hash`).

**Exceptions.** None.

**Relates to.** [P4](../../00_thesis/01_non_negotiables.md), [P5](../../00_thesis/01_non_negotiables.md); [OPR10](../operator/README.md) (operators extend the chain).

### ART10 — Content-addressed identity

**Rule.** Identity is `lineage.head_hash` — a SHA-256 over the head step's `kind/name/version/params/input_hashes`, **not** over payload bytes and **not** over the artifact's structural metadata. The artifact store dedups and replay-checks on it. **Producers must fold every content-defining choice into `step.params`** (units selection, alignment policy, window size, ddof, …) so the hash captures it; a choice recorded only on artifact metadata is an identity hazard (two artifacts from different choices collide).

**Why.** [P4](../../00_thesis/01_non_negotiables.md). Hash-of-recipe (not bytes) survives float-representation differences across machines/versions; recipe completeness is what makes it correct.

**Verify.** `head_hash == steps[-1].hash`; constructing the same artifact twice yields the same hash; a serialise→deserialise round-trip preserves the hash; a methodology choice not in `step.params` is flagged in review.

**Anti-patterns.** Identity from payload bytes; mutating after construction; a choice on metadata but not in `step.params`.

**Exceptions.** None.

**Relates to.** [P4](../../00_thesis/01_non_negotiables.md); [OPR10](../operator/README.md).

### ART11 — Validators raise at construction (uniform, strict)

**Rule.** Structural invariants are enforced by `@model_validator(mode="after")` at construction, and v2.0 makes them **uniform across every type** via shared helpers:

- **One shared index validator.** `_validate_datetime_index(index, label)` — `DatetimeIndex` + monotonic-increasing + no duplicates — is called from **every indexed type**: `Series`, `SeriesSet.common_index` (and members), `EventSet.mask`, `Panel.payload`. `WindowedPanel.offsets` must be unique + sorted. (v1: only `Series` validated its index; `EventSet`/`Panel` accepted duplicate/unsorted indices — the root cause of the operator-layer raw-crash cluster.)
- **Numeric + finite dtype.** Every numeric payload must be a numeric dtype, and **`±Inf` is forbidden** (NaN is allowed as "missing"; `Inf` is not — it survives a dtype check but detonates at JSON persistence). Forbidding it at construction turns a late, non-obvious failure into a loud, immediate one.
- **EventSet semantic invariants.** `event_dates` are unique; `event_dates[i] ↔ per_event_metadata[i]` are co-ordered; `set(True-mask dates) == set(event_dates)` (v1 enforced only a *count* match, so `event_windows` double-counted duplicate dates and misattributed reversed metadata).

Malformed input raises `ValueError`/`ValidationError` immediately, with a message naming the type and the specific invariant.

**Why.** [P6](../../00_thesis/01_non_negotiables.md) at construction. A malformed artifact must fail at the moment of construction, not three operators downstream as a raw pandas error. Making "valid" mean the *same strict thing* for every type is what closes the ≥6-operator raw-leak cluster at the root.

**Verify.** Every indexed type calls the shared index validator; every numeric payload rejects `±Inf`; `EventSet` enforces the three semantic invariants; every invariant has a negative test with a specific message.

**Anti-patterns.** A type with only field-type checks; a type accepting a duplicate/unsorted index; an `Inf` admitted into a payload; a generic "invalid input" message.

**Exceptions.** None.

**Relates to.** [P6](../../00_thesis/01_non_negotiables.md); [OPR11](../operator/README.md) (operators enforce *agreement* downstream; the artifact enforces its own *shape* here).

---

## Group IV — Operational

### ART12 — Closed enums + typed escape hatches

**Rule.** `TimeSeriesUnits` and `MissingnessPolicy` are closed families (P8); reuse them, never fork a parallel enum. v2.0 also closes the two **untyped escape hatches** the audit flagged: `per_event_metadata` (`List[Dict[str,Any]]`) gets a **documented per-producer required-key contract** (each producer declares which keys it populates — e.g. `threshold_events` populates `{triggering_value, threshold, zscore_value?}`), validated at construction. (`LegSpec.units`, the other v1 escape hatch, leaves with `TradeSet`.)

**Why.** Structural-metadata coherence + honest disclosure. An untyped `Dict[str,Any]` is a place where meaning silently varies between producers; a per-producer key contract makes it inspectable.

**Verify.** Artifacts use `TimeSeriesUnits`/`MissingnessPolicy` consistently (no parallel enums); a new unit/policy is added to the existing enum via ADR; each `per_event_metadata` producer's key contract is documented and validated.

**Anti-patterns.** `class PanelUnits(Enum)` alongside `TimeSeriesUnits`; an inline `Literal[...]` instead of the enum; an undocumented `per_event_metadata` shape.

**Exceptions.** None.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md), [P10](../../00_thesis/01_non_negotiables.md).

### ART13 — Test pattern

**Rule.** Every type ships four layers: (1) **artifact-store round-trip** through real `put_artifact`/`get_artifact` (raw `model_dump_json()` is **not** a substitute — pandas/numpy payloads need the per-type codec); (2) **validator tests** — every invariant has a negative test with the expected message + a positive test; (3) **lineage-propagation** — chain preserved, `head_hash == steps[-1].hash`; (4) **immutability** — reassignment and unknown-field both raise. v2.0 adds a **cross-type uniform-validator test** asserting every indexed type rejects a duplicate/unsorted index and an `Inf` payload (the shared-validator guarantee). No SQL parity (artifacts have no compute path).

**Why.** Each layer catches a different bug class; the round-trip catches codec/discriminator drift that ships to production.

**Verify.** Each type has a test file with the four layers; the cross-type test covers every indexed type; the validator layer covers *every* invariant.

**Anti-patterns.** Happy-path-only tests; a `model_dump_json()` stand-in for the round-trip; a missing negative case.

**Exceptions.** None.

**Relates to.** Analog of [PR16](../primitive/README.md) / [OPR16](../operator/README.md).

### ART14 — Bridge / adapter for primitive-produced types

**Rule.** Every artifact type a primitive can produce has a registered adapter in `shared/artifacts/adapters/` that lifts the primitive's wire-shaped output into the typed artifact — the *only* legal primitive→artifact path. The adapter constructs a complete `Lineage` (starting with `PrimitiveStep`) and **populates complete structural metadata, including the derived `frequency`** (ART8). Current adapters: `tool_output_to_artifact_series`, `tool_output_to_artifact_panel`, `raw_dataframe_to_artifact_series`, `time_series_to_artifact_series`, and the reverse `artifact_series_to_time_series`.

**Why.** [P9](../../00_thesis/01_non_negotiables.md) at the bridge. The bridge isolates primitive-side wire format from operator-side typed artifacts; without it, primitives would need to know lineage/metadata internals and operators would need to handle primitive dicts.

**Verify.** Every primitive-produced type has an adapter; the adapter builds a complete chain + metadata (incl. frequency); no primitive `compute.py` constructs an artifact directly.

**Anti-patterns.** A primitive returning an artifact instance; a new type with no adapter; an ad-hoc adapter in operator code.

**Exceptions.** Operator-produced types need no adapter (operators construct directly per OPR10).

**Relates to.** ART8; the bridge architecture (`01_architecture/06_bridge.md`, forthcoming).

### ART15 — No naked pandas escape

**Rule.** Inside the operator layer (`shared/operators/`) and the workflow layer (`shared/workflow/`, `<agent>/workflows/`), the artifact is the only legal data carrier between functions. `pd.DataFrame`/`pd.Series`/`np.ndarray` may live *inside* a `payload` and be extracted temporarily inside an operator body, but never appear as operator inputs/outputs, workflow node outputs, or edge payloads.

**Why.** Type-algebra integrity. A single naked-pandas escape at a boundary blows the static guarantee the validator relies on.

**Verify.** Operator signatures consume/return artifacts; `grep` for `def .*-> pd\.` inside `shared/operators/` is empty; templates declare every edge's artifact type.

**Anti-patterns.** An operator returning `pd.DataFrame`; a "convenience" operator taking `pd.Series`; raw pandas on a workflow edge.

**Exceptions.** None at API boundaries; internal payload extraction is fine.

**Relates to.** [OPR9](../operator/README.md); type-algebra integrity is the joint property of ART2 + ART9 + ART15.

### ART16 — Closed-family-extension procedure

**Rule.** Adding (or removing) a type follows the [`runbook.md`](runbook.md) procedure: ADR → Pydantic class → canonical enum (+ derived sites via the lock-step test) → artifact-store codec → producer wiring → consumer wiring → tests. All land together (or an explicit prerequisite chain in the ADR).

**Why.** ART4 + ART6 make extension safe; the runbook makes it followable.

**Verify.** The procedure is documented; every extension PR cites it; the PR review checklist runs against the diff.

**Anti-patterns.** Skipping the runbook for a "small" extension; an ADR without the derived sites; landing in pieces without a documented chain.

**Exceptions.** None.

**Relates to.** ART4; [P8](../../00_thesis/01_non_negotiables.md).

---

## The current closed family (v2.0)

| Type | Canonical use case | Primary producers | Primary consumers |
|---|---|---|---|
| `Series` | A single indexed numeric series | almost every series-returning primitive; `align_series.get_series`; `series_arithmetic`; `apply_mask`; `conditional_aggregate`; `summarize_series`; the new single-series transforms | almost every operator with a Series input |
| `SeriesSet` | Aligned keyed collection | `align_series`; `rolling_regression` | `select_from_series_set`; the new cross-sectional operators |
| `EventSet` | Events at timestamps | `threshold_events` | `event_windows`; `apply_mask` |
| `Panel` | Wide `[date × column]` | multi-series primitives | workflow-template terminal artifacts; aggregation operators |
| `WindowedPanel` | `[event × offset]` | `event_windows` | `conditional_aggregate` |
| `ScalarMetric` | A single statistic | the new `correlation`/`covariance`/`cointegration` operators (full-sample); summary operators | workflow-template terminal artifacts; `series_arithmetic` (scalar comparisons) |

**Removed:** `TradeSet` (relocated with the trade operators — [OPR6](../operator/README.md)). `RankedResult` remains unregistered (cross-sectional operators use `SeriesSet`).

## Anti-patterns (catalogue-wide)

- An artifact-shaped class outside `shared/artifacts/`. (ART2)
- A class without `frozen=True` / with `extra="ignore"` / without a `@model_validator`. (ART7/ART11)
- An indexed type that does **not** call the shared index validator, or admits `±Inf`. (ART11)
- An artifact without lineage, or a forgeable/disconnected chain. (ART9)
- A field that depends on asset class; a parallel structural-metadata enum. (ART3/ART12)
- An operator returning `pd.DataFrame`/`pd.Series`; a primitive constructing an artifact in `compute.py`. (ART15/ART14)
- A type change that doesn't update the canonical enum + derived sites together, or lands without an ADR. (ART4/ART6)
- A test covering only the happy path. (ART13)

## Open questions and known gaps

1. **`ScalarMetric` — admitted (decision closed).** The statistical-operator toolbox (correlation/covariance/cointegration) predominantly outputs scalars, and the single-row-`Series` + `SUMMARY_SENTINEL_DATE` workaround is dishonest (a fictional index). `ScalarMetric` is admitted as a first-class type and lands via the ART16 procedure alongside the `correlation` reference build. The legacy single-row-`Series` summaries (`summarize_series`) migrate to `ScalarMetric` during the legacy-migration step.
2. **`TradeSet` re-admission.** When the backtest **primitive** set is built, `TradeSet` (and `LegSpec`, `PositionPath`) are re-admitted via ART16 as primitive-output types — at which point whether they are *operator-composable* is a fresh ART4 question.
3. **`RankedResult`.** Still unregistered; cross-sectional ranking operators use `SeriesSet` until a load-bearing case justifies the extension.
4. **Frequency-derivation specifics.** The exact `infer_freq`-vs-known-cadence policy and the `'irregular'` sentinel semantics are pinned when the adapter change lands (ART8/ART14).
5. **Hash-recipe stability.** Any change to the `OperatorStep`/`PrimitiveStep` hash recipe must keep `tests/state/test_hash_stability.py` green; a recipe change is a closed-family-style decision in its own right.

## Changing an artifact principle

1. Open an ADR in [`../../05_decisions/`](../../05_decisions/). 2. Land the ADR + change together. 3. Bump the version. Artifact-principle changes are higher-stakes than primitive or operator changes — they affect every downstream consumer simultaneously. Expect more reviewers.

## Citation cheat sheet

| Use | Pattern |
|---|---|
| Commit | `feat(artifacts): shared _validate_datetime_index on all indexed types per ART11` |
| PR review | `This violates ART11 — EventSet admits a duplicate event_date.` |
| Code comment | `# ART9: head_hash == steps[-1].hash invariant` |
| Runbook step | `Step 3 — update the canonical enum; the lock-step test derives the rest (ART2/ART6).` |
| ADR | `This decision admits ScalarMetric per ART4/ART5.` |

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| **v2.0** | 2026-05-30 | **Foundational reset**, paired with operator v2.0, encoding the standardization audit + founder decisions. (1) **ART2/ART6 single-source closed family** — one canonical `ARTIFACT_TYPE_NAMES` enum with every derived site (discriminator, type-map, `TerminalArtifact`, store codec) derived + a lock-step test (fixes the v1 drift where `TerminalArtifact` omitted `TradeSet`). (2) **ART11 uniform strict validators** — one shared `_validate_datetime_index` across *all* indexed types; `±Inf` forbidden at construction; EventSet semantic invariants (uniqueness, co-ordering, mask==dates) — closes the operator-layer raw-crash cluster at the root. (3) **ART9 lineage integrity guards** — `head_hash==steps[-1].hash`, chain connectivity, `.build()`-only, and `sanitize_params_for_lineage` (fixes the v1 forgeable/disconnected/NaN-crash gaps). (4) **ART8 frequency made load-bearing** — derived at the adapter, populated on every production artifact, with an `'irregular'` value (founder decision #3). (5) **`TradeSet` removed** from the closed family (its operators relocated to primitives — OPR6); **`ScalarMetric` admitted** (founder decision #5) because the statistical-operator toolbox outputs scalars and the single-row-`Series`+sentinel-date hack is dishonest. (6) **ART12 typed escape hatch** — `per_event_metadata` gains a per-producer required-key contract. Added `Panel.sub_kind`, the v2.0 closed-family table, and the cross-type uniform-validator test (ART13). | [ADR 0016](../../05_decisions/0016-operator-and-artifact-standardization-v2.md) |
| v1.1 | 2026-05-17 | Pre-canonical corrections against the codebase (eight admission sites incl. the artifact-store codec; ART9 lineage start/end shapes; ART10 identity-over-recipe; ART13 store-round-trip; `tool_output_to_artifact_panel`; `TimeSeriesUnits` values; path depths). Superseded by v2.0. | — |
| v1 | 2026-05-17 | Initial artifact contract (ART1–ART16; six-type family incl. `TradeSet`). | — |
