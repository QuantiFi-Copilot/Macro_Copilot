# Operator Architecture

The canonical definition and admission standard for the **central
operator layer** that sits above finance-aware primitives and below
workflow templates / orchestration.

This document is the operator-layer analogue of
`docs/architecture/tool_architecture.md`:

- primitives own finance concepts and lock methodology via config
- central operators own structural transformations over typed artifacts
- workflow templates compose primitives + operators into desk-shaped
  analyses
- UI / explanation layers render the resulting artifacts for users

The point of this split is to stop "operator" from becoming a
wastebasket category for anything that does not cleanly fit elsewhere.

## What a central operator is

A **central operator** is a reusable transformation that is:

- **blind to finance meaning**
- **informed by structural semantics**
- **provenance-preserving**
- **typed on both input and output**

It does **not** know:

- instrument
- asset class
- curve family
- tenor
- sovereign vs OIS vs FX

It **does** know:

- index semantics
- units
- frequency
- missingness policy
- mask / event semantics
- lineage / provenance

In other words: a central operator is blind to the *financial meaning*
of its inputs, but not to their *structural meaning*.

## Canonical definition

> A central operator is a finance-domain-blind, provenance-preserving
> transformation over typed analytical artifacts and their structural
> metadata, with explicit method variants and typed composable or
> terminal outputs.

## What operators own

Operators own **structural method families**, not finance concepts.
Examples:

- alignment
- masking
- windowing
- aggregation
- ranking
- arithmetic
- mapping / fan-out control

Examples of operator candidates:

- `align_series`
- `threshold_events`
- `event_windows`
- `conditional_aggregate`
- `cross_sectional_rank`
- `series_arithmetic`
- `map`

Examples of things that are **not** operators:

- `calculate_swap_spread` (a real cross-domain primitive shipped in
  PR #76 — knows the sovereign-yield vs OIS-rate finance concept)
- `find_curve_inversions`
- `regress_breakeven_on_oil`
- `event_study_pipeline`
- `format_event_study_report`

Those belong respectively in:

- finance-aware primitives
- workflow templates
- UI / explanation layer

## Artifact contract

Artifacts flowing through the operator layer are **not** naked pandas /
NumPy objects. They are typed wrappers carrying both payload and
structural metadata.

Illustrative examples:

- `Series`
- `SeriesSet`
- `EventSet`
- `Panel`
- `WindowedPanel`
- `ScalarMetric`
- `RankedResult`

The operator layer reads metadata such as:

- units
- index type
- frequency
- missingness policy
- event / mask semantics
- lineage

This metadata is load-bearing. Without it, operators either become
unsafe (`series_arithmetic` across incompatible units) or silently
domain-dependent (`align_series` making hidden calendar assumptions).

## Required criteria

### 1. Finance-blind contract

The operator's input / output contract must be expressed in typed
artifacts and structural metadata, not finance terms.

If the operator signature contains concepts like:

- `curve_family`
- `tenor`
- `bond`
- `swap`
- `OIS`
- `Treasury`

then it is not a central operator. It is a primitive or a template
component.

### 2. Structural method family with exposed variants

An operator is allowed to be a method, but only within a **structural**
family.

Examples:

- `conditional_aggregate`
- `threshold_events`
- `event_windows`

Consequential variants inside that family must be explicit and
inspectable:

- aggregator
- threshold rule
- window shape
- alignment policy
- ranking direction

No hidden finance-specific defaults. No "obvious" hardcoded choices
that the caller cannot inspect or override.

### 3. Typed output

The output must be either:

- a **typed composable artifact**, or
- a **typed terminal artifact**

Composable artifacts are valid inputs to downstream operators.
Terminal artifacts are valid end-state results for templates / UI
without reinterpretation.

No:

- prose
- ad hoc dicts
- outputs that require LLM parsing to become reusable

### 4. Provenance-preserving

The operator must carry forward upstream lineage and add its own:

- operator name
- parameters
- version / policy choices

This applies even to terminal outputs such as scalars or ranked tables.

## Promotion rule

A tool can be an **operator in principle** before it is promoted into
the shared **central operator layer**.

A candidate is promoted into the shared layer when:

1. it is reused across **3 or more distinct workflow archetypes**, or
2. it is demonstrably **load-bearing to the operator algebra**, meaning
   no composition of existing operators reproduces its effect cleanly

The second clause is reviewed against the existing operator set. It is
not self-declared by the author.

This split matters:

- the **definition** says what an operator *is*
- the **promotion rule** says what earns a stable shared slot

Without that split, the bootstrap process becomes circular: you cannot
build templates without operators, but you also cannot define operators
without templates.

## Fan-out rule

Base operators should be **arity-stable**.

Fan-out over a list of inputs belongs in a separate `map` meta-operator
unless the input artifact is itself a first-class collection type such
as `SeriesSet`.

Examples:

- `map(zscore_custom, list_of_series)` is fan-out
- `cross_sectional_rank(SeriesSet)` is still a normal operator

This keeps the type algebra cleaner and avoids embedding ad hoc list
behaviour into every operator.

## Pass / fail stress list

| Candidate | Result | Why |
|---|---|---|
| `align_series` | Pass | Finance-blind alignment over typed series with explicit alignment policy |
| `threshold_events` | Pass | Finance-blind masking / event extraction with explicit threshold rule |
| `event_windows` | Pass | Finance-blind window extraction over an indexed target artifact |
| `conditional_aggregate` | Pass | Finance-blind aggregation family with explicit aggregator choice |
| `cross_sectional_rank` | Pass | Finance-blind ranking over a `SeriesSet` |
| `series_arithmetic` | Pass | Finance-blind arithmetic over compatible series metadata |
| `calculate_swap_spread` | Fail | Knows finance concepts (sovereign yield vs OIS rate); correctly shipped as a cross-domain primitive in PR #76, not as an operator |
| `find_curve_inversions` | Fail | Domain-specific curve semantics |
| `event_study_pipeline` | Fail | Entire workflow template, not a structural operator |
| `regress_breakeven_on_oil` | Fail | Domain-specific workflow logic in name and behavior |
| `format_event_study_report` | Fail | UI / explanation layer, not typed analytical transformation |
| `conditional_mean` as a standalone tool | Fail | Overly narrow variant of a broader structural family |

## Admission checklist

Before admitting a new operator into the shared central layer, answer:

1. Could this run unchanged on rates, FX, equities, or temperature time
   series?
2. What structural method family does it own?
3. What consequential variants are caller-controlled?
4. What typed artifact does it take in?
5. What typed artifact does it emit?
6. Does it preserve lineage?
7. Is it reused across multiple workflow archetypes, or is it
   algebraically foundational in a reviewable way?

If those answers are fuzzy, the candidate is not ready for admission.

## Relationship to primitives and templates

The operator layer is intentionally narrower than the primitive layer.

- A **primitive** owns a finance concept and may be standard if its
  methodology and dependency chain are explicit, reproducible, and
  provenance-carrying.
- A **central operator** refuses finance concepts and owns only a
  structural transformation over typed artifacts.
- A **workflow template** owns an analysis pattern such as event study,
  cross-sectional screen, attribution decomposition, or
  regime-conditioned relationship.

That separation is what keeps the architecture future-proof:

- primitives do not become pseudo-operators
- operators do not become workflow bundles
- templates do not become hidden domain logic
