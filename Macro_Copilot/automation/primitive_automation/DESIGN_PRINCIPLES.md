# Design Principles

These are the non-negotiable design principles for the primitive
automation.

They are drawn from:

- the repo architecture docs
- the migrated reference primitives
- the testing/wiring patterns already present
- the explicit project rules established during this build process

The builder MUST follow them.
The reviewer MUST review against them.
The orchestrator MUST treat violations as real failures.

## 1. Build only genuinely new primitive concepts

Do NOT build a new primitive just because:

- a new instrument entered the universe
- a new `curve_family` was added
- a new country needs support
- a new tenor combination is useful

If an existing generic primitive already owns the concept, then the
right action is universe/support expansion, not a new primitive.

Examples:

- a new sovereign issuer for `yield_levels` is not a new primitive
- a new market for `curve_spread` is not a new primitive
- a new same-concept family for `cross_market_spread` is not a new
  primitive

Build a new primitive only when the finance concept itself is new.

## 2. Standard tools must be honest

A tool counts as standard only if:

- the finance concept is desk-recognizable
- the methodology is explicit
- the dependency chain is explicit
- the output carries enough provenance to reconstruct what was done
- no load-bearing choice is hidden

Two categories exist in the repo:

- `desk_invariant_primitive`
- `quant_standard_analytic`

Most primitives in this automation pass should be
`desk_invariant_primitive`.

Do NOT default to `desk_invariant_primitive` lazily. Declare the
category honestly.

## 3. No half-assed primitives

If required metadata is missing, do NOT build the primitive.

That means:

- no proxy
- no guessed substitute
- no "temporary V1 approximation"
- no opinionated stand-in
- no silently degraded version of the real concept

If the real primitive requires data the repo does not yet ingest or
cannot yet trust, the primitive must be deferred.

## 4. Inputs vs conventions are separate layers

In V1 deterministic mode there are only two layers:

- Inputs: user/LLM-controlled, per query
- Conventions: YAML-locked, system-controlled

The LLM must not alter conventions in V1.

Do NOT expose methodology knobs as LLM inputs unless they are truly the
central user-facing choice that defines what the tool is.

Ancillary methodology belongs in `config.yaml`, not in the input schema.

## 5. YAML owns conventions; code owns invariants

Move all methodology defaults and configuration choices into the tool's
`config.yaml` where appropriate.

Examples:

- rolling windows
- ddof
- default field names
- fill limits
- rounding rules
- threshold defaults

Do NOT hide these as hardcoded module-level constants in `compute.py`.

But mathematical truths and validation invariants do NOT belong in YAML.
They stay in code:

- `short_tenor != long_tenor`
- date ordering
- same-currency rules
- same-domain rules
- other structural constraints

These live in Pydantic validators or code-level guards.

## 6. The four-file tool pattern is mandatory

Every deterministic tool follows exactly:

`rates_agent/<domain>/tools/<tool_name>/`

with:

- `__init__.py`
- `config.yaml`
- `schemas.py`
- `compute.py`

Do not invent alternate layouts.

## 7. Canonical TimeSeries outputs matter

If the primitive should compose with workflows/operators, it must emit
canonical `shared.schemas.time_series.TimeSeries` fields with honest
units and stable field names.

This is not optional if the tool is intended to feed the workflow
system.

## 8. Call-site config visibility matters

Production callers must load the bundled config explicitly and pass
`config=` explicitly.

The auto-load fallback in `compute()` exists mainly as a test seam, not
as the production integration style.

The MCP server and other callers must make the config dependency
visible.

## 9. Wiring is part of the primitive

A primitive is not "done" when the four tool files exist.

The owning domain surfaces must also be updated consistently:

- MCP wrapper
- schema re-exports if used in the domain
- workflow primitive registration if the primitive should compose
- tests
- config lint compatibility

## 10. Review is adversarial, not ceremonial

Codex is not there to paraphrase the diff.
Codex is there to find:

- architecture violations
- hidden assumptions
- missing tests
- broken wiring
- non-standard concepts
- dishonesty about data or methodology

If a finding is valid, Claude must fix it.
If it is not valid, the automation must not pretend otherwise.

## 11. One primitive at a time

This automation must never batch primitives in one run.

Reasons:

- quality degrades
- diffs become harder to review
- architectural drift becomes harder to spot
- it becomes unclear which finding belongs to which primitive

The loop is strictly serial.

## 12. The branch is part of the safety model

This automation may only operate on:

`primitive_automation`

That restriction is intentional.
The automation must not wander through the repo's branch graph.

## 13. Testing must be grounded in the real DB

Offline tests are necessary but not sufficient.

Every primitive must be tested extremely thoroughly against:

- deterministic offline tests
- AND read-only DB-backed SQL validation in the repo's container/dev
  environment

If the DB-backed layer cannot be run, the primitive is not fully
validated.

## 14. The automation may not mutate the DB

The automation may use the DB only for read-only validation.

It may not:

- ingest data
- backfill data
- modify schema
- run writes
- run deletes
- run updates
- run upserts

Testing must be grounded in the DB, but the automation is not a data
pipeline operator.
