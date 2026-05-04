# Bridge Architecture

The primitive→operator bridge.  Established by Phase 1B Work Items
1–4 (PRs #67–#72) using `OIS curve_spread` and `align_series` /
`series_arithmetic` / `threshold_events` as the reference workload.
Every new primitive that emits canonical `TimeSeries` payloads goes
through the bridge to reach operators; every operator that produces
a `Series` goes through the bridge to reach the wire.

## Intent

Two layers exist on either side:

- **Primitives** (`rates_agent/{sovereign_bonds,ois}/tools/<tool>/`) —
  deterministic, single-purpose tools that fetch from the DB,
  apply a config-driven methodology, and emit a snapshot dict +
  one or more canonical
  `shared.schemas.time_series.TimeSeries` payloads.
- **Operators** (`shared/operators/<operator>/`) — typed transforms
  over `shared.artifacts.types.Series` / `EventSet` / `Panel`.
  Each operator demands frozen artifact wrappers carrying
  structural metadata (units, frequency, missingness policy,
  lineage).

These two layers don't speak the same shape.  Primitives return
JSON-friendly dicts (Pydantic dump).  Operators consume frozen
artifact objects.

The bridge is the **only** path between them.  No bypass channel:
operators take frozen `Series` artifacts; primitives emit
JSON-shaped dicts; the adapter is the bridge.  That is what makes
the lineage chain trustworthy — every artifact built from a
primitive output starts with a `PrimitiveStep` (added in PR #67),
and downstream operators extend the chain via `OperatorStep`
entries.

## Layout

The bridge lives under `shared.artifacts.adapters` as a peer of
the existing `raw_dataframe_to_artifact_series` adapter — NOT a
new top-level `shared/bridge/` package.  The package's `__init__`
already reserved the slot.

```
shared/artifacts/
  lineage.py                 # closed-family Step kinds (incl. PrimitiveStep)
  adapters/
    __init__.py              # re-exports the adapter functions
    from_raw_dataframe.py    # fetch DataFrame -> Series
    from_time_series.py      # primitive TimeSeries <-> Series  ← THE BRIDGE
```

Three public functions ship from `from_time_series.py`:

- `time_series_to_artifact_series` — low-level forward conversion.
  Caller passes a `TimeSeries` plus a fully-built `PrimitiveStep`
  plus a typed `MissingnessPolicy`.  Used by tests and advanced
  callers that want fine-grained control.

- `tool_output_to_artifact_series` — high-level forward
  convenience.  Takes the primitive's raw output dict + tool
  identity bits + the already-loaded `ToolConfig`.  Auto-derives
  the `CleanSingleSeriesV1` policy from the config when possible,
  builds the `PrimitiveStep` for the caller, and calls the
  low-level function.  **The 95% callsite.**

- `artifact_series_to_time_series` — reverse conversion.  Takes a
  frozen `Series` and produces a wire-compatible `TimeSeries`.
  `NaN` → `None` at every row; `description` is filled with a
  linear lineage summary by default.

## Contracts

### Lineage chain

Every artifact built by the bridge carries a `Lineage` whose
`steps[0]` is a `PrimitiveStep` recording the primitive's identity:

```python
PrimitiveStep(
    kind="primitive",                       # closed discriminator
    name="calculate_ois_curve_spread_tool", # MCP tool name (NOT yaml tool.name)
    version="1.0.0",
    params=<primitive's *Input.model_dump()>,
    tool_config_hash=<sha256 of conventions block>,
    tool_config_path=<optional bookkeeping; NOT in hash>,
    output_field=<which time_series* field was extracted>,
    as_of_date=<from current_metrics.as_of_date>,
    input_hashes=(),                        # primitives have no upstream artifacts in v1
    hash=<sha256 over all the above except tool_config_path>,
)
```

Identity-bearing fields (folded into the hash via
`PrimitiveStep.build`'s internal merge):

- `name` — MCP tool name.  Plain `str`, not `Literal`, because the
  primitive family is extensible and lineage must not couple to a
  fixed tool registry.
- `params` — the primitive's `*Input.model_dump()`.
- `tool_config_hash` — content-hash of the bundled
  `ToolConfig.conventions` block.  Two calls with the same
  `*Input` but different YAMLs (e.g. someone bumped
  `z_score_window_days`) MUST produce different step hashes —
  that's what this captures.
- `output_field` — which `time_series*` field of the primitive's
  output was extracted (`time_series_spread` vs
  `time_series_zscore`).  Different field → different artifact →
  different hash.
- `as_of_date` — replay determinism.  A re-run tomorrow with the
  same params has a different `as_of_date` (DB has a new latest)
  and the hash MUST reflect that the underlying data is different.

Bookkeeping-only field (NOT in the hash):

- `tool_config_path` — two callers loading the same YAML from
  different paths (test fixture vs prod) MUST produce the same
  hash if the content is identical.  Path is metadata for human
  debugging, not identity.

### Missingness policy

The bridge auto-derives `CleanSingleSeriesV1` from
`tool_config.conventions["ffill_limit_days"].value` and pins the
other two cleaner invariants explicitly:

```python
resolved_policy = CleanSingleSeriesV1(
    ffill_limit=ffill_limit,
    drop_nan=True,        # matches clean_single_series invariant
    dedup_keep="last",    # matches clean_single_series invariant
)
```

The explicit `drop_nan` / `dedup_keep` pins are deliberate: if a
future change to `clean_single_series`'s invariants drifts those
defaults, this site forces a deliberate update rather than silent
inheritance.

The auto-derived policy captures the cleaning REGIME of the
primitive's upstream pipeline, NOT data identity.  Source field /
as_of_date / output_field / curve+tenor are identity bits that
live elsewhere (`Series.units`, `Series.series_key`,
`PrimitiveStep.params`, `PrimitiveStep.as_of_date`,
`PrimitiveStep.output_field`).  **Two artifacts whose primitives
share the same cleaning regime ARE compatible at the missingness
layer by construction** — even if they came from different
primitives, different fields, or different markets.  Operators
discriminate via units / series_key / lineage; they don't need
missingness to also encode data identity.

If a primitive's YAML doesn't declare `ffill_limit_days`, the
bridge raises `ValueError` with a clear pointer to pass an
explicit policy rather than silently defaulting to
`RawNoCleaning`.

### `None` ↔ `NaN` semantic mapping

`TimeSeriesRow.value` is `Optional[float]` — a `None` row is the
wire encoding for "the tool emitted a gap" (typically the rolling-
window warmup period, or a date the per-day forward-rate guard
skipped).  The artifact's payload is a numeric `pd.Series`; there
is no null-mask sidecar in v1.  So:

- **Forward** (wire → artifact): `None` value → `NaN` at the same
  DatetimeIndex position.
- **Reverse** (artifact → wire): `NaN` → `None` at the same
  DatetimeIndex position.

This is *semantic-faithful* (the `MissingnessPolicy` carries the
contract that NaN means "missing", not "zero") but NOT byte-
identical.  A future null-mask sidecar on `Series.payload` could
close the gap if a desk use case ever needs to distinguish
"missing because no data" from "explicit None reported by the
upstream system" — deferred until that need is real.

The bridge does NOT silently drop `None` rows.  Every input row
maps to one output index position; cleaning happens in the
primitive (via `ffill_limit_days`), not here.

### Round-trip discipline

The forward+reverse round trip is *semantic-faithful*:

- `(rows, units, series_name)` round-trip BYTE-identical (modulo
  the documented `None` ↔ `NaN` semantic mapping).
- `description` differs by design — forward consumes the
  primitive's free-form description; reverse generates a lineage
  summary suitable for human-readable provenance.  The original
  description is recoverable from the structured `Series.lineage`
  (`PrimitiveStep.params` + downstream `OperatorStep.params`)
  but is NOT echoed on the wire.

### Lineage summary format

The reverse path populates `TimeSeries.description` with a linear
summary of the structured lineage:

```
"derived: <step0.name>[ → <stepN.name>]*"
```

Steps are emitted in order from oldest to newest.  Only the linear
primary chain is walked; `OperatorStep.auxiliary_lineages` (right-
hand operands of binary operators like `series_arithmetic`) are
NOT included — they remain on the structured artifact via
`series.lineage`.  The wire description is bounded + readable;
full structured provenance is recoverable from the chain when
needed.

Callers that want a different description pass
`description_override` to the reverse function.  Honored verbatim
— the bridge does NOT prepend `"derived: "` or otherwise mutate.
Empty string raises via Pydantic (`description` has
`min_length=1`).

## Reference recipe

The canonical end-to-end recipe — a runnable example new bridge
authors should read first — lives at
[`tests/test_bridge_reference_recipe.py`](../../tests/test_bridge_reference_recipe.py).
Pipeline shape:

```
OIS curve_spread primitive
  ↓ tool_output_to_artifact_series(output_field="time_series_spread")
Series A (BPS)
  ↓ series_arithmetic(A, "diff")           # unary
Series A_diff (BPS, lineage extends)
  ↓ artifact_series_to_time_series
wire TimeSeries
  description = "derived: calculate_ois_curve_spread_tool → series_arithmetic"
```

That recipe is also pinned by
[`tests/test_bridge_pipeline_smoke.py`](../../tests/test_bridge_pipeline_smoke.py)
across nine pipeline shapes, including:

- multi-stage chains with multiple operator extensions,
- cross-primitive composition (rate_level + curve_spread aligning
  cleanly because both share the same cleaning regime),
- replay determinism (same inputs → same lineage hashes; different
  `output_field` → different hashes),
- `NaN` ↔ `None` survival across operator round-trips,
- structural-metadata refusals (frequency / index mismatch) firing
  at the operator boundary,
- binary-operator auxiliary-lineage IDENTITY (right operand's
  chain preserved on artifact, excluded from wire summary).

## When to use which function

| Goal                                                     | Function                                  |
| -------------------------------------------------------- | ----------------------------------------- |
| Lift a primitive's output dict to operate on it          | `tool_output_to_artifact_series`          |
| Demote an operator output back to wire format            | `artifact_series_to_time_series`          |
| Custom test fixture / advanced bridge integration        | `time_series_to_artifact_series`          |
| Lift a raw fetch DataFrame (not a primitive output)      | `raw_dataframe_to_artifact_series` (peer adapter) |

The high-level `tool_output_to_artifact_series` is the 95%
callsite.  Reach for the low-level `time_series_to_artifact_series`
only when you need to construct the `PrimitiveStep` yourself
(e.g. a non-primitive caller that wants to inject custom identity
bits for testing).

## What the bridge is NOT

- **Not a scheduler.**  The bridge doesn't decide when to call
  primitives or operators.  That's the orchestrator's job.
- **Not a cache.**  The bridge converts on demand.  Primitive
  results are cached upstream (`load_tool_config` is path-keyed
  process-wide); operator results are cached downstream
  (operator-level memoization is on the Phase 1B+ roadmap but
  separate).
- **Not a unit converter.**  If a primitive returns BPS and an
  operator wants PERCENT, the bridge does NOT silently divide by
  100.  It hands the `Series` to the operator with `units=BPS`
  attached and lets the operator's input contract refuse the
  call.  Unit drift is a methodology error and must be caught
  loudly, not papered over.
- **Not a replacement for the canonical TimeSeries shape.**  The
  TimeSeries payload IS the wire format primitives emit.  The
  bridge doesn't define a new canonical shape; it just lifts that
  shape into the artifact-typed world.

## Closed-family discipline

The bridge extends only one closed family in the substrate: the
`LineageStep` discriminated union.  Adding `PrimitiveStep` was a
schema extension to `shared/artifacts/lineage.py`; it required
adding a class AND extending the discriminator union.  The hash
recipe (`_compute_step_hash`) was deliberately NOT changed —
extending it would be a breaking change to every persisted lineage
object across the codebase.  The four primitive identity bits ride
inside a derived `hashed_params` dict in `PrimitiveStep.build`,
which keeps the recipe stable.

This is the same closed-family discipline `MissingnessPolicy`
already follows: adding a new policy or step kind requires
editing the closed union AND the imports.  No "open ended"
families.

## Future-proofing — what this design unblocks

Once the bridge is in place, the orchestrator can compose
primitives + operators into multi-step deterministic workflows:

```
"compute the BTP-Bund spread, align it with the SOFR-ESTR 2Y,
 regress, threshold the residuals, pull historical event windows
 around each threshold cross"
```

That's the workflow surface the LLM picks from in V2.  V1 still
calls a single primitive per query; the bridge is what makes V2
possible without each workflow author writing bespoke conversion
glue.

The design also leaves clean expansion paths for:

- **Sidecar `lineage_json` field on `TimeSeries`** — full
  structured lineage on the wire, when desks need lossless
  provenance echo (currently summarised in `description`).
  Requires a `TimeSeries` schema bump; deferred until needed.
- **Null-mask sidecar on `Series.payload`** — distinguish "missing
  because no data" from "explicit None reported upstream"
  (currently both collapse to `NaN`).  Deferred until a desk
  case needs it.
- **`PrimitiveStep` for primitives that consume artifact inputs**
  — `input_hashes: Tuple[LineageHash, ...] = ()` is the slot
  reserved for Phase 2 composite primitives that take an
  artifact (not just DB rows) as input.  The schema is ready; no
  Phase 2 primitives exist yet.

## Tests + lint surface

- **Bridge unit tests:**
  [`tests/test_adapter_from_time_series.py`](../../tests/test_adapter_from_time_series.py)
  — 60 tests covering the adapter contracts (forward/reverse,
  round-trip, NaN ↔ None, lineage-summary format, missingness
  auto-derivation, error paths).
- **Lineage step kind tests:**
  [`tests/test_artifacts.py::TestPrimitiveStep`](../../tests/test_artifacts.py)
  — 21 tests pinning the new step kind (discriminator, hash
  determinism, JSON round-trip, chain composition).
- **Pipeline integration smoke:**
  [`tests/test_bridge_pipeline_smoke.py`](../../tests/test_bridge_pipeline_smoke.py)
  — 15 tests exercising the bridge against real primitives + real
  operators end-to-end.  Pinned cross-primitive composition,
  replay determinism, NaN survival across operators, structural-
  metadata refusals at the operator boundary, and binary-operator
  auxiliary-lineage identity.
- **Reference recipe:**
  [`tests/test_bridge_reference_recipe.py`](../../tests/test_bridge_reference_recipe.py)
  — runnable worked example.  New bridge authors / orchestrator
  authors should read this first.

The cross-config lint (`shared.config.lint`) does not validate
bridge contracts directly; the bridge has no YAML of its own.
Convention drift on the primitive YAMLs that feed the bridge IS
caught by the existing per-tool-config lint.

## Doing things this way matters

The substrate is built so a new primitive needing operator
composability does NOT require bridge changes:

1. The primitive declares one or more canonical `TimeSeries`
   fields on its `*Output` schema.
2. The primitive's YAML declares `ffill_limit_days` (and any
   other shared cleaning conventions).
3. The caller passes the new primitive's `(output_class,
   output_field, tool_name, tool_config, params)` to
   `tool_output_to_artifact_series`.

Done.  No bridge edits, no operator edits, no orchestrator edits.
The closed-family discipline guarantees that any new primitive
that breaks the contract surfaces as a clean `ValueError` at the
bridge boundary — not as silent provenance drift downstream.
