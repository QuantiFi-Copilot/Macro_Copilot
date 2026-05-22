# Design Principles

These are the non-negotiable design principles for the primitive
automation.

They are drawn from:

- the repo's authoritative documentation tree at `docs_revamped/`
- the migrated reference primitives (`rates_agent/sovereign_bonds/`,
  `rates_agent/ois/`)
- the testing/wiring patterns already present in `tests/`
- the explicit project rules established during this build process

The builder MUST follow them.
The reviewer MUST review against them.
The orchestrator MUST treat violations as real failures.

## 0. Authoritative sources

This file is a *condensed operational restatement* of rules that are
fully documented elsewhere. When this file and the source disagree, the
source wins — open a PR to fix this file.

- **P1–P12 — repo-wide non-negotiables.** See
  `docs_revamped/00_thesis/01_non_negotiables.md`. Cite by ID
  (`P5`, `P12`) in commit messages, PR comments, and reviewer
  output.
- **PR1–PR16 — primitive contract.** See
  `docs_revamped/02_components/primitive/README.md` and
  `docs_revamped/02_components/primitive/runbook.md`. Cite by ID
  (`PR7`, `PR11`, `PR16`).
- **WT1–WT16 — workflow-template contract** (applies when the
  primitive feeds into a workflow). See
  `docs_revamped/02_components/workflow_template/README.md`.
- **Lateral standards.** Source-tag registry, file/folder layout,
  naming, typed-boundary discipline, error handling, hash determinism,
  closed-family discipline, test patterns, code-review checklist — see
  `docs_revamped/03_standards/`.
- **ADRs (architecture decisions).** `docs_revamped/05_decisions/`. New
  closed-family values require a new ADR (P8).
- **The map.** `automation/primitive_automation/REPO_REFERENCE_MAP.md`
  is the index into the above. Read what it points to.

The rest of this file is the operational restatement.

## 1. Build only genuinely new primitive concepts  *(PR1, PR4)*

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

## 2. Standard tools must be honest  *(P5, PR7, PR12)*

A tool counts as standard only if:

- the finance concept is desk-recognizable  *(PR4)*
- the methodology is explicit and disclosed via a registered source
  tag from `docs_revamped/03_standards/methodology_disclosure.md`
- the dependency chain is explicit
- the output carries enough provenance to reconstruct what was done
  *(P5)*
- no load-bearing choice is hidden  *(P6)*

Two categories exist in the repo:

- `desk_invariant_primitive`
- `quant_standard_analytic`

Most primitives in this automation pass should be
`desk_invariant_primitive`.

Do NOT default to `desk_invariant_primitive` lazily. Declare the
category honestly.

## 3. No half-assed primitives  *(P2, P12, PR5)*

If required metadata is missing, do NOT build the primitive.

That means:

- no proxy
- no guessed substitute
- no "temporary V1 approximation"
- no opinionated stand-in
- no silently degraded version of the real concept

If the real primitive requires data the repo does not yet ingest or
cannot yet trust, the primitive must be deferred. P12 — the Bloomberg
Accuracy Boundary — applies: if you cannot answer the three-question
framework (is this Bloomberg-grade? if not, can it be ingested? if
not, refuse) with a clean "build," defer.

## 4. Inputs vs conventions are separate layers  *(PR8, PR9)*

In V1 deterministic mode there are only two layers:

- Inputs: user/LLM-controlled, per query
- Conventions: YAML-locked, system-controlled

The LLM must not alter conventions in V1.

Do NOT expose methodology knobs as LLM inputs unless they are truly the
central user-facing choice that defines what the tool is.

Ancillary methodology belongs in `config.yaml`, not in the input schema.

## 5. YAML owns conventions; code owns invariants  *(PR9, PR10)*

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

## 6. The four-file tool pattern is mandatory  *(PR3)*

Every deterministic tool follows exactly:

`<agent>/<domain>/tools/<tool_name>/`

with:

- `__init__.py`
- `config.yaml`
- `schemas.py`
- `compute.py`

Do not invent alternate layouts. See
`docs_revamped/03_standards/file_and_folder_layout.md` for the
authoritative path rules across the platform.

## 7. Canonical TimeSeries outputs matter  *(PR13, WT-binding rules)*

If the primitive should compose with workflows/operators, it must emit
canonical `shared.schemas.time_series.TimeSeries` fields with honest
units and stable field names.

This is not optional if the tool is intended to feed the workflow
system.

## 8. Call-site config visibility matters  *(PR14)*

Production callers must load the bundled config explicitly and pass
`config=` explicitly.

The auto-load fallback in `compute()` exists mainly as a test seam, not
as the production integration style.

The MCP server and other callers must make the config dependency
visible.

## 9. Wiring is part of the primitive  *(PR9, PR16)*

A primitive is not "done" when the four tool files exist.

The owning domain surfaces must also be updated consistently:

- MCP wrapper in `<agent>/<domain>/mcp_server.py`
- schema re-exports in `<agent>/<domain>/tools/schemas/__init__.py`
- workflow primitive registration in
  `rates_agent/workflows/__init__.py` if the primitive should compose
- the three-test triplet (compute / wiring / sql_validation)
- config lint compatibility

## 10. Review is adversarial, not ceremonial  *(P5, P6)*

The reviewer (Codex primary; Claude on quota-fallback) is not there to
paraphrase the diff. The reviewer is there to find:

- architecture violations
- hidden assumptions
- missing tests
- broken wiring
- non-standard concepts
- dishonesty about data or methodology
- false claims of completeness

If a finding is valid, the builder must fix it.
If it is not valid, the automation must not pretend otherwise.

## 11. One primitive at a time

This automation must never batch primitives in one run.

Reasons:

- quality degrades
- diffs become harder to review
- architectural drift becomes harder to spot
- it becomes unclear which finding belongs to which primitive

The loop is strictly serial. Catalog iteration advances one entry at a
time; each entry passes the full build → review → done gate before the
next entry begins.

## 12. The branch is part of the safety model

This automation may only operate on:

`primitive_automation`

That restriction is intentional. The automation must not wander
through the repo's branch graph. Every wrapper script
(`run_*_builder.sh`, `run_*_reviewer.sh`) calls `check_branch.sh` first;
do not bypass it.

## 13. Testing must be grounded in the real DB  *(PR16, P2, P6)*

Offline tests are necessary but not sufficient.

Every primitive must be tested extremely thoroughly against:

- deterministic offline tests (the `*_compute.py` and `*_wiring.py`
  tests)
- AND read-only DB-backed SQL validation in the repo's container/dev
  environment (the `*_sql_validation.py` test)

If the DB-backed layer cannot be run, the primitive is not fully
validated.

See `automation/primitive_automation/TESTING_AND_DB_VALIDATION_POLICY.md`
for the operational rules and `docs_revamped/03_standards/test_patterns.md`
for the canonical triplet pattern.

## 14. The automation may not mutate the DB  *(P10)*

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

## 15. Pre-flight load_audit gate  *(P6, P12, PR6)*

Before dispatching the builder for ANY primitive (even one previously
marked `todo`), the orchestrator MUST verify that the primitive's
required playbook has a `SUCCESS` row in `macro_data.load_audit`. See
`automation/primitive_automation/PRE_FLIGHT_LOAD_AUDIT.md`.

A primitive whose substrate playbook has not been ingested cannot be
honestly validated against the DB; the SQL-validation layer would
either error or, worse, return empty rows that look like a passing
test. The pre-flight gate makes "data present" an explicit, loud
precondition rather than an implicit assumption.

On a miss: mark the primitive `blocked`, surface the reason to the
human via `primitive_runtime_state.yaml`, and continue to the next
eligible primitive. Do NOT attempt to seed the DB to "satisfy" the
check (that would violate §14).

## 16. Reviewer-engine fallback is a worker swap, not a bar relaxation

When the primary reviewer (Codex) exhausts its quota,
`parse_reviewer_log.py` flags `reviewer_quota_exhausted: true` and the
orchestrator switches to the fallback reviewer (Claude;
`run_claude_reviewer.sh`). The fallback reviewer is fed the SAME
`REVIEWER_PROMPT.md` — the review standard does not change.

The orchestrator records the swap in
`primitive_runtime_state.yaml` as `reviewer_mode: claude_fallback`
plus a `reviewer_mode_history` entry (P5 honest disclosure). Lower
model-independence in fallback mode is a known trade-off, accepted
in preference to halting the factory.

## 17. Catalog-exhaustion shutdown

When every entry in `primitive_catalog.yaml` is in a terminal status
(`done` or `blocked`), the orchestrator sets `orchestrator_status:
catalog_exhausted` and stops scheduling new wakes. Cron continues to
fire the orchestrator entry-point script, but the entry-point
short-circuits as a no-op until a human resets the catalog or
introduces a new entry. See
`automation/primitive_automation/BACKGROUND_EXECUTION_POLICY.md` §8.

This protects the user's token budget — there is no point waking an
orchestrator that has nothing to do.
