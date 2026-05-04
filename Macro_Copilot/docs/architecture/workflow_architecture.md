# Workflow Architecture

The canonical definition and admission standard for the **workflow
template layer** that sits above central operators and below the
LLM-facing orchestration / UI layer.

This document is the workflow-layer analogue of
`docs/architecture/tool_architecture.md` and
`docs/architecture/operator_architecture.md`:

- primitives own finance concepts and lock methodology via config
- central operators own structural transformations over typed artifacts
- workflow templates compose primitives + operators into desk-shaped
  analyses
- LLM / UI layers select templates, fill slots, and render results

The point of this split is to stop "workflow" from becoming a place
where finance logic, structural transforms, and orchestration glue
get mixed together — the same wastebasket failure mode that
`operator_architecture.md` was written to prevent for operators.

## What a workflow template is

A **workflow template** is a reusable, named **analysis pattern** that
is:

- **desk-recognizable** by name (a macro PM or quant would know what
  it computes without reading the code)
- **topology-locked** (the DAG shape is fixed; the LLM does not
  rewire it)
- **slot-driven** (only the central analysis knobs — instruments,
  thresholds, windows — are LLM-fillable)
- **typed end-to-end** (all node-to-node handoffs use frozen
  artifacts via the bridge; no untyped dicts or prose)
- **provenance-preserving** (the workflow's terminal artifacts carry
  a lineage chain that walks back through every primitive +
  operator + bridge edge that produced them)
- **instrument-agnostic in shape** (the same template works on rates,
  FX, credit, equities, or any other primitive family that emits
  canonical `TimeSeries` payloads)

It does **not** know:

- how to fetch raw data (primitives do)
- how to align series, threshold events, or aggregate (operators do)
- how to render charts, format tables, or write prose (UI layer does)
- which template a given prompt should bind to (LLM selection layer
  does)

It **does** know:

- which primitives + operators it composes
- the canonical DAG shape that produces its output
- which slots are user-fillable vs template-locked
- what its terminal artifact type is
- what archetype it belongs to

## Canonical definition

> A workflow template is a desk-recognizable, topology-locked,
> slot-driven composition of typed primitives and central operators
> that produces a typed terminal artifact with full lineage
> provenance, and is reusable across instrument families without
> code change.

## What templates own

Templates own **analysis archetypes**, not finance concepts and not
structural transforms.  The V1 archetype set, frozen in this
document, is exactly four:

- `event_study` — conditional aggregation across event windows
  (threshold a signal → extract events → forward windows on a target
  → conditional vs unconditional aggregate)
- `regime_conditioned_relationship` — masked / regime-conditional
  analysis (classify regimes → split sample by mask → run a
  per-subsample analysis → compare across regimes)
- `attribution_decomposition` — explained-vs-residual decomposition
  ("what drove the move" — regress or difference one series against
  another → split into attributable + residual → aggregate)
- `cross_sectional_screen` — fan-out + rank ("which instrument is
  most stretched" — apply the same sub-analysis across a universe →
  rank cross-sectionally → return ordered list)

Examples of things that are **not** workflow templates:

- `calculate_swap_spread` (primitive — owns a finance concept; the
  cross-domain swap-spread primitive shipped in PR #76)
- `align_series` (operator — owns a structural transform)
- `format_event_study_report` (UI / explanation layer)
- `sofr_ust_2y_event_study` (template instance, not a template —
  see "What fails the standard test" below)
- `spread_analysis` (too low-level — a thin wrapper over one
  primitive is not an archetype)
- `alignment_then_arithmetic` (too low-level — a reusable subgraph
  / motif, not a desk-shaped analysis)

The four archetypes above are **closed for V1**.  Adding a fifth
archetype requires a separate PR that explicitly extends this list
and demonstrates the new shape is not a specialization of any
existing archetype.

## Artifact contract

Workflow nodes consume and emit the same typed artifacts the
operator layer uses:

- `Series`
- `SeriesSet`
- `EventSet`
- `Panel`
- `WindowedPanel`
- `ScalarMetric` (terminal — single-number outputs with units +
  lineage)
- `RankedResult` (terminal — ordered universe with per-member
  metric, units, and lineage)

The handoff from primitives → operator layer goes through the
bridge (`shared.artifacts.adapters.from_time_series`); the handoff
from operators → primitives (in composite primitives, deferred)
would go through the same bridge in the reverse direction.
Templates **never** consume or emit naked pandas / NumPy objects.

## Required criteria

### 1. Desk-recognizable archetype

The template's name must describe an analysis pattern a macro PM or
desk quant would recognize without reading the code.

Pass: `event_study`, `attribution_decomposition`,
`regime_conditioned_relationship`, `cross_sectional_screen`.

Fail: `sofr_ust_2y_event_study` (too narrow — it's a template
instance), `daily_curve_spread_chart` (not a recognized analysis
pattern), `compute_then_threshold_then_window` (describes mechanics,
not the analysis it performs).

### 2. Topology-locked DAG

The template owns the DAG shape.  The LLM fills parameter slots; it
does **not** add, remove, reorder, or rewire nodes.

If the LLM needs to choose between two genuinely different DAG
shapes for the same prompt, those are two templates, not one.  If
the LLM needs to invent a topology, it is operating in the
unconstrained orchestration path (research track, NOT the V1
production path).

### 3. Slot-driven inputs

The template exposes ONLY the central analysis knobs as fillable
slots.  Everything else — operator parameters, threshold-rule
choices, alignment policies, aggregator selections — is
template-locked.

Legitimate slot examples:

- `signal_spec`, `target_spec` (which series to use)
- `threshold` (how stretched is "stretched")
- `lookback_days` (how much history)
- `window_pre`, `window_post` (event-study window size)
- `conditioning_spec` (which series defines the regime)
- `regressor_specs` (regression decomposition inputs)
- `universe` (which instruments to fan out across)
- `ranking_metric` (how to rank in cross-sectional screen)

Illegitimate slots (would make the template a thin LLM wrapper
rather than a standard archetype):

- `ddof`, `ffill_limit`, `min_periods` — these are operator-level
  conventions; they belong in operator config, not template slots
- `dag_topology` or `node_list` — the LLM does not invent topology
- `aggregator_function` as free-form — must be one of a closed
  enum if exposed at all

### 4. Typed end-to-end

Every edge between nodes uses a frozen artifact, validated by the
substrate's edge-validator.  No template may bypass the bridge or
introduce raw-pandas handoffs between nodes.

### 5. Provenance-preserving

The template's terminal artifact carries a `Lineage` chain whose
oldest steps are `PrimitiveStep`s (one per primitive node) and
whose newer steps are `OperatorStep`s (one per operator node), in
topological order.  A workflow-level summary node may be added at
the head, but it does not replace the per-node chain.

The reverse-bridge serialization
(`artifact_series_to_time_series`) of any `Series`-typed terminal
artifact must produce a wire `description` that summarizes the
template's name + the chain of nodes that produced the value.

### 6. Instrument-agnostic in shape

The template must be capable of running unchanged against a
**non-rates synthetic primitive** that emits canonical `TimeSeries`
payloads through the bridge.  The same template must work for
rates today and FX / credit / equities / any-future-domain
tomorrow with no template code change — only `signal_spec` /
`target_spec` bindings change.

This is enforced as a **mandatory test per template**: every
template's test suite must include at least one test that runs the
template against a finance-blind synthetic primitive (rather than
a real rates primitive).  See "Admission checklist" point 1.

## Promotion rule

A template can exist as a **prompt-specific composition** before it
is promoted into the **shared workflow template catalogue**.

A candidate is promoted into the shared catalogue when:

1. it owns one of the four V1 archetypes, AND
2. it has at least **two distinct prompts** that bind to it
   correctly in the template-selection gauntlet (Track A — see
   "Evaluation contract" below), AND
3. it passes the instrument-agnostic test (Required criterion 6).

Templates that don't yet meet all three criteria can live as
**template-local prototypes** under
`rates_agent/workflows/<archetype>/<prototype_name>/` while the
gaps are closed.  Same closed-family discipline as primitives →
operators promotion.

The 1-template-per-archetype discipline is intentional for V1: each
archetype gets exactly one shared catalogue entry, with the canonical
slot schema for that archetype.  Multiple variants per archetype are
deferred until a real desk use case demands them.

## Pass / fail stress list

| Candidate | Result | Why |
|---|---|---|
| `event_study` | Pass | Desk-recognizable archetype, topology-locked, slot-driven, instrument-agnostic |
| `regime_conditioned_relationship` | Pass | Desk-recognizable archetype, mask-conditional, generalizable to any regime classifier |
| `attribution_decomposition` | Pass | Desk-recognizable archetype, regress / difference + residual aggregation |
| `cross_sectional_screen` | Pass | Desk-recognizable archetype, fan-out + rank, generalizable across universes |
| `sofr_ust_2y_event_study` | Fail | Template instance (instrument bindings hardcoded), not a template |
| `btp_bund_decomp_last_30d` | Fail | Template instance (instruments + window hardcoded) |
| `gilt_oat_cheap_rich_scan` | Fail | Template instance, not a generalizable archetype |
| `spread_analysis` | Fail | Thin wrapper over one primitive — not a desk archetype |
| `alignment_then_arithmetic` | Fail | Reusable subgraph / motif, not a desk-shaped analysis |
| `zscore_threshold_event` | Fail | Internal mechanics, not a desk archetype name |
| `event_study_pipeline_v2` | Fail (for V1) | Second variant of an existing archetype — defer until a real desk use case demands it |
| `format_event_study_report` | Fail | UI / explanation layer, not a workflow |
| `compute_then_threshold_then_window` | Fail | Names the mechanics, not the analysis |

## Admission checklist

Before admitting a new template into the shared workflow catalogue,
answer:

1. **Could this run unchanged against a synthetic non-rates
   primitive that emits canonical `TimeSeries` payloads?**  If no,
   the template has hidden rates-specific assumptions and is not
   instrument-agnostic.
2. **Which of the four V1 archetypes does this template own?**  If
   the answer is "none of them," the candidate is either too
   narrow (a template instance), too broad (multiple archetypes
   bundled), or proposes a new archetype (separate review required
   per the closed-family rule above).
3. **What are the slots, and is each one a legitimate central
   analysis knob (not an operator-level convention)?**
4. **What is the terminal artifact type?**  Must be one of
   `Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`,
   `ScalarMetric`, `RankedResult`.
5. **Does the lineage chain on the terminal artifact walk back
   through every primitive + operator node in the DAG?**
6. **Does the template require any new operator or artifact?**  If
   yes, does the new operator pass `operator_architecture.md`'s
   admission checklist independently?  Template-local helpers are
   acceptable; shared promotion requires the operator's own bar.
7. **Are there at least two distinct prompts in the
   template-selection gauntlet that bind to this template?**
   (Promotion criterion 2.)

If those answers are fuzzy, the candidate is not ready for
admission.

## Unit-algebra extension policy

Templates are allowed to declare **explicit unit conversions** as
part of their topology — e.g. an `attribution_decomposition`
template that produces a "% explained" `RATIO` from a regression
over `BPS` series may include an explicit `convert_units` step.

Templates **MUST NOT** silently coerce across units, AND operators
**MUST NOT** be extended with implicit cross-unit coercion paths to
accommodate template needs.  The strict unit-algebra at the
operator boundary (per `series_arithmetic`'s refusal of cross-unit
arithmetic) stays strict.

If a template needs a unit conversion that the operator layer does
not yet support, the conversion lives at the template layer as an
explicit step — not as a hidden coercion inside an operator.

## Template-selection contract

The LLM-facing surface for templates has two layers:

### Catalogue + cards

Each template exposes a **template card** derived from its
`template.yaml` — the workflow analog of a primitive's MCP tool
description.  The card includes:

- archetype name
- one-line human-readable description
- the slot schema (typed)
- the terminal artifact type
- an `archetype_signature` declaration: which structural cues in a
  prompt indicate this template

The catalogue (a `list_workflows()` MCP tool, conceptually) returns
the cards.

### Routing

Prompts route to templates via a `route_to_template` LLM step that
mirrors the existing `RouteDecision` pattern in
`orchestrator/contracts.py`.  The output is a structured-JSON
choice from a closed enum:

- one of `{event_study, regime_conditioned_relationship,
  attribution_decomposition, cross_sectional_screen}`, OR
- `no_template_applicable`

`no_template_applicable` routes to the unconstrained orchestration
path (Track B in the eval contract below).  In V1, that path is
NOT shipped to users — it exists only as a research track inside
the eval harness.

The router does NOT pick parameter values.  Once the template is
chosen, a separate per-template MCP tool fills the slot schema via
structured output, then dispatches to the workflow executor.

This is the same separation of concerns the rest of the
architecture uses: routing is one decision, parameter binding is a
separate decision, and execution is deterministic given the
binding.

## Evaluation contract

Two test tracks, with thresholds **declared in advance** (not
back-fitted after the eval runs).

### Track A — templated orchestration (production gate)

Pass/fail evaluation of the LLM-facing template surface.  At least
30 prompts (target 30–50), drawn from desk-realistic queries that
all bind to one of the four templates.

Acceptance thresholds (V1):

- **≥85% template-selection accuracy** — the router picks the
  correct template for the prompt
- **≥80% parameter-binding agreement on matched cases** — when the
  template is correct, the slot values match the ground truth
  (with caller-tolerable equivalences, e.g. `"10Y" == "10y"`)
- **100% workflow-execution success on bound templates** — no
  broken DAGs; if the template + parameters bind cleanly, the
  workflow runs end to end without error

Track A is the production gate.  Failing Track A means the V1
orchestration path is not shippable.

### Track B — unconstrained orchestration (research track only)

Capacity-mapping evaluation of the LLM building DAGs from scratch
on multi-step / multi-instrument / multi-tool prompts.  At least
15 prompts (target 15–25).

Acceptance: NOT pass/fail.  The output is a **capacity report**
covering:

- average DAG depth achieved
- % of generated DAGs that validated against the substrate's
  validator
- % that executed end to end (validated AND ran without error)
- top-3 failure modes with examples
- diagnostic recommendations for V2 scope

Track B is **not a production path**.  It exists to inform V2
scope ("the LLM can sustain 4-step DAGs cleanly but breaks at 6
steps with mixed-unit operands" is the kind of insight that
shapes the next milestone).

## Folder layout

Each template is its own directory:

```
rates_agent/workflows/<template_name>/
  template.yaml                # DAG topology + slot schema + card
  __init__.py                  # public-API re-exports if needed
  tests/                       # template-specific tests
    test_<template>_compute.py # end-to-end with real primitives
    test_<template>_synthetic.py  # MANDATORY instrument-agnostic test
```

The four V1 templates live at:

- `rates_agent/workflows/event_study/`
- `rates_agent/workflows/regime_conditioned_relationship/`
- `rates_agent/workflows/attribution_decomposition/`
- `rates_agent/workflows/cross_sectional_screen/`

The substrate code lives separately at `shared/workflow/`:

```
shared/workflow/
  __init__.py
  types.py                     # Workflow, PrimitiveNode, OperatorNode, edges, slots
  validate.py                  # cycle detection, slot/type/unit compatibility
  executor.py                  # topological execution via graphlib + bridge
  result.py                    # WorkflowResult + workflow-level lineage summary
  template.py                  # WorkflowTemplate, slot schema, binding rules
  template_loader.py           # YAML loading + caching + validation
  template_registry.py         # list templates, retrieve metadata
  template_card.py             # human/LLM-readable template descriptors
```

The substrate is **finance-blind** — it does not import from
`rates_agent/`.  Enforced as a structural test in the substrate
PR.

## What makes a template "standard"

The word **standard** is load-bearing, just as it is for primitives
and operators.  It does **not** mean:

- universally optimal
- universally parameter-free
- uncontested across every desk
- fixed forever

It means the template is made **explicit, controlled, and
reproducible**:

- the analysis pattern is named and recognizable
- the DAG topology is fixed and inspectable
- the slot schema is typed
- the operator + primitive choices are template-locked
- the lineage chain is end-to-end recoverable
- the template is reusable across instrument families

This is why both the four V1 archetypes and any future archetype
that earns its slot can be standard: **standardness is a property
of how the methodology is exposed, not of how narrow the template
is.**

### What this definition allows

A template can still be standard even if:

- it has consequential parameter choices (every template does — the
  slot schema IS the consequential surface)
- it offers multiple supported aggregator / threshold-rule choices
  (as long as the choices are caller-controlled and inspectable)
- it requires a methodology preface before interpretation (the
  template card provides this)

The requirement is not "one configuration forever."  The
requirement is that the topology family, the chosen variant, and
the slot bindings are made explicit and do not drift silently.

### What fails the standard test

A template is **not** standard if any load-bearing choice is
hidden or unrecoverable.  Typical failure modes:

- a consequential structural choice (which operator, which
  aggregator, which window shape) is hardcoded but not documented
- the LLM is allowed to invent topology rather than picking from
  the closed archetype set
- the template silently assumes a specific instrument family or
  curve_family in its operator parameters
- the terminal artifact's lineage chain does not walk back through
  every node
- two runs can differ because the LLM picked different operator
  parameters that the template should have locked

## Relationship to primitives and operators

The workflow layer is the highest layer in the deterministic-mode
substrate.  The three-layer separation discipline:

- A **primitive** owns a finance concept (curve_spread, yield_levels,
  swap_spread) and is standard if its methodology and dependency
  chain are explicit, reproducible, and provenance-carrying.
- A **central operator** refuses finance concepts and owns only a
  structural transformation over typed artifacts.
- A **workflow template** owns a desk-recognizable analysis pattern
  and composes primitives + operators via a topology-locked,
  slot-driven DAG.

That separation is what keeps the architecture future-proof:

- primitives do not become pseudo-templates (a template is not just
  "a primitive with two preset parameters")
- operators do not become workflow bundles (an operator is one
  structural transform, not a multi-step analysis)
- templates do not become hidden domain logic (templates compose
  the lower two layers; they do not reinvent finance math or
  structural transforms)

A new instrument family (FX, credit, equities) is added by:

1. Building primitives for that family under
   `rates_agent/<family>/tools/` (or a sibling agent package).
2. Ensuring those primitives emit canonical `TimeSeries` payloads
   through the bridge.
3. **Reusing the existing four templates unchanged** by passing
   the new family's primitive specs as `signal_spec` /
   `target_spec` bindings.

If step 3 requires changing a template, the template was not
standard.

## Doing things this way matters

The substrate is built so that:

1. The LLM picks one of four templates and fills its slots — the
   production orchestration path is small, well-typed, and
   inspectable.
2. The unconstrained orchestration path exists for capacity
   research, not production.
3. New instrument families inherit all four templates for free,
   provided their primitives emit the canonical `TimeSeries` shape.
4. New archetypes (a future fifth template family) require an
   explicit closed-family extension, the same way a new operator
   step kind requires a closed-family extension to `LineageStep`.

No orchestrator rewrite per new instrument.  No template rewrite
per new instrument.  No operator rewrite per new template.  The
admission checklist + promotion rule + closed-family discipline at
each layer is what makes this work.
