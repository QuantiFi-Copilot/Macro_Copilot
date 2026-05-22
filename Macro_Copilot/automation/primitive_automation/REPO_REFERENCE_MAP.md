# Repo Reference Map

These are the files the primitive automation must consult before
building or reviewing a primitive.

The authoritative project documentation lives in `docs_revamped/`. The
older `docs/` tree is being phased out — when an entry here points at a
`docs_revamped/` file, treat that as the source of truth even if a
sibling file in `docs/` says something different. Where this map and a
`docs_revamped/` file disagree, the doc wins; open a PR to fix this
map.

## How to use this map

1. **Before any build or review**, read every file under §"Core
   non-negotiables" and §"Primitive contract" once per session.
2. For the specific primitive at hand, also read the matching
   §"Reference primitives" entry and the matching §"Reference tests"
   entry.
3. Only after that, inspect the live repo's current implementation of
   neighbouring tools — the docs explain the *contract*, but the
   neighbours show the *current shape* of that contract.

Reading from memory or from generic software-engineering habits is a
P5 violation (you are claiming a methodology source you did not
consult).

## Core non-negotiables — repo-wide rules

- `docs_revamped/00_thesis/00_what_we_build.md`
  One-page statement of the platform thesis (5-layer L1→L5 stack,
  agent isolation, typed boundaries). Read once per session.
- `docs_revamped/00_thesis/01_non_negotiables.md`
  P1–P12 with IDs, rules, and verification recipes. **Cite by ID**
  (`P5`, `P12`) in review output — IDs are stable, text is not.
- `docs_revamped/00_thesis/02_ai_agent_development_contract.md`
  How an AI agent is expected to behave when modifying this repo —
  the contract this automation itself must satisfy.
- `docs_revamped/01_architecture/00_internal_architecture.md`
  Internal (in-process) architecture: substrate ↔ primitives ↔
  operators ↔ workflow templates ↔ orchestration.
- `docs_revamped/01_architecture/01_external_architecture.md`
  External (process + protocol) architecture: MCP transport, sub-
  agents, gateway.

## Primitive contract — what a primitive *is*

- `docs_revamped/02_components/primitive/README.md`
  The PR1–PR16 primitive contract. Definitional (PR1–PR3), admission
  (PR4–PR6), standardness (PR7–PR11), operational (PR12–PR16). Cite
  by ID.
- `docs_revamped/02_components/primitive/runbook.md`
  The procedure for adding a new primitive end-to-end. The builder
  follows this; the reviewer scores against it.
- `docs_revamped/02_components/workflow_template/README.md`
  The WT1–WT16 workflow-template contract. Consult when the primitive
  composes into a workflow template (binds a slot, exposes a
  TimeSeries result, registers via `<agent>/workflows/__init__.py`).
- `docs_revamped/02_components/workflow_template/runbook.md`
  The procedure for adding a new workflow template.

## Lateral standards — apply to every component type

- `docs_revamped/03_standards/README.md`
  Index + how lateral standards relate to per-component contracts.
- `docs_revamped/03_standards/file_and_folder_layout.md`
  Canonical paths (substrate vs agent vs shared; the 4-file primitive
  package shape; tests location). P3 + P11.
- `docs_revamped/03_standards/naming_conventions.md`
  Module / file / function / class / convention-key / test-file
  naming. P3.
- `docs_revamped/03_standards/typed_boundary_discipline.md`
  Frozen Pydantic with `extra="forbid"` at every typed boundary; no
  loose dicts across layers. PR8 manifestation.
- `docs_revamped/03_standards/closed_family_discipline.md`
  Domain enum, instrument-type enum, archetype enum, and source-tag
  registry are closed families. New value = ADR. P8 + PR8.
- `docs_revamped/03_standards/methodology_disclosure.md`
  Source-tag registry and the rule that methodology lives in YAML on
  the card. P5 + PR7 + PR12.
- `docs_revamped/03_standards/error_handling.md`
  Per-layer error conventions; no silent fallback; raise vs envelope
  per boundary. P6 + PR11.
- `docs_revamped/03_standards/test_patterns.md`
  Determinism, fixtures, file naming, parity tests where structurally
  possible. PR16's "three tests per primitive" recipe lives here.
- `docs_revamped/03_standards/hash_determinism.md`
  Lineage step hash recipe; cross-deploy stability. P4.
- `docs_revamped/03_standards/code_review_checklist.md`
  The reviewer's per-line checklist; the automation reviewer must
  honour this.

## Architecture decision records (ADRs)

The ADR catalogue. Read the ones relevant to the primitive being
built — extending the platform without consulting prior ADRs is a P8
violation.

- `docs_revamped/05_decisions/0001-instrument-metadata-history.md`
- `docs_revamped/05_decisions/0002-playbook-metadata-history-section.md`
- `docs_revamped/05_decisions/0003-cash-bond-substrate.md`
- `docs_revamped/05_decisions/0004-event-calendar-substrate.md`
- `docs_revamped/05_decisions/0005-cash-bond-playbook-universe.md`
- `docs_revamped/05_decisions/0006-inflation-domain-agents.md`
- `docs_revamped/05_decisions/0007-otr-resolver.md`
- `docs_revamped/05_decisions/0008-event-playbook-contract.md`
- `docs_revamped/05_decisions/0009-wirp-time-series.md`  *(Proposed; do
  not build against WIRP yet)*
- `docs_revamped/05_decisions/0011-futures-domain-agents.md`  *(adds
  POLICY_FUTURES + BOND_FUTURES domains; bond_futures V1 is
  monitors-only)*

## Automation-loop policies (this folder)

These are the operational rules the orchestrator and the builder /
reviewer workers obey. They are stricter than the per-primitive
contract because they govern the *automation* — not the *primitive*.

- `automation/primitive_automation/DESIGN_PRINCIPLES.md`
  Pointer index into docs_revamped/ + the principles this automation
  asserts on top of them.
- `automation/primitive_automation/STANDARD_TOOL_AND_YAML_RULES.md`
  Standard-tool definition + YAML contract enforced at build time.
- `automation/primitive_automation/PRIMITIVE_BUILD_RULES.md`
  What the builder must produce (4-file package, tests, registration).
- `automation/primitive_automation/TESTING_AND_DB_VALIDATION_POLICY.md`
  Mandatory two-layer testing: offline deterministic + read-only DB-
  backed SQL validation.
- `automation/primitive_automation/PRE_FLIGHT_LOAD_AUDIT.md`
  Mandatory pre-flight gate: a `load_audit` SUCCESS row must exist
  for every `required_playbook` named on the catalog entry before
  any builder is dispatched. Read-only check; no fix-up.
- `automation/primitive_automation/NO_GO_RULES.md`
  Hard "do not build" conditions.
- `automation/primitive_automation/DONE_DEFINITION.md`
  Exit criteria — what "done" means for a primitive in this
  automation.
- `automation/primitive_automation/QUOTA_AND_RESUME_POLICY.md`
  Required quota contract, thresholds, resume semantics, and the
  Codex → Claude reviewer fallback.
- `automation/primitive_automation/BACKGROUND_EXECUTION_POLICY.md`
  Required background scheduler / catalog-loop behavior, including
  catalog-exhaustion shutdown.
- `automation/primitive_automation/DISCORD_STATUS_POLICY.md`
  Required durable state for Discord-readable progress reporting,
  including `reviewer_mode` and `orchestrator_status`.
- `automation/primitive_automation/OPENCLAW_CRON_RUNBOOK.md`
  Recommended recurring scheduler shape.

## Config validation + lint

- `shared/config/tool_config.py`
  Exact schema and validation behavior for `config.yaml`.
- `shared/config/lint.py`
  Cross-tool convention drift check.

## Reference primitive implementations

Start with these — they are the canonical shape every new primitive
must match. **Read the actual files, not just the names.**

Level / window-style (single-instrument, single-window stats):

- `rates_agent/sovereign_bonds/tools/yield_levels/`
- `rates_agent/ois/tools/swap_spread/`

Multi-leg spreads / curve geometry:

- `rates_agent/sovereign_bonds/tools/curve_spread/`
- `rates_agent/sovereign_bonds/tools/cross_market_spread/`
- `rates_agent/sovereign_bonds/tools/butterfly/`

Statistical / decomposition primitives (consult when the primitive at
hand is regression / PCA / attribution-shaped):

- `rates_agent/sovereign_bonds/tools/rolling_regression/`
- `rates_agent/sovereign_bonds/tools/pca_yield_curve/`
- `rates_agent/sovereign_bonds/tools/yield_change_attribution_pca/`

## Reference wiring surfaces (per-domain MCP servers)

The MCP server is where each tool is registered. Mirror the
per-tool wrapper pattern from the matching reference server.

- `rates_agent/sovereign_bonds/mcp_server.py`
- `rates_agent/ois/mcp_server.py`
- `rates_agent/sovereign_bonds/tools/schemas/__init__.py`
- `rates_agent/policy_futures/mcp_server.py`  *(scaffold; tools land
  here as the automation builds them)*
- `rates_agent/bond_futures/mcp_server.py`    *(scaffold; V1 monitors
  only)*

## Workflow primitive registration

- `rates_agent/workflows/__init__.py`

Consult this if the primitive should compose into workflow templates
or other operator/workflow surfaces. WT1–WT16 apply.

## Reference tests

The canonical PR16 three-test triplet (compute + wiring + sql_validation).
Use these as the primary test pattern — file naming, fixtures, parity
expectations.

- `tests/test_yield_levels_compute.py`
- `tests/test_yield_levels_wiring.py`
- `tests/test_yield_levels_sql_validation.py`
- `tests/test_curve_spread_compute.py`
- `tests/test_curve_spread_wiring.py`
- `tests/test_curve_spread_sql_validation.py`
- `tests/test_swap_spread_compute.py`
- `tests/test_swap_spread_wiring.py`
- `tests/test_swap_spread_sql_validation.py`
- `tests/conftest.py`
- `tests/sql_validation_common.py`
- `tests/fixtures/curve_spread_v1/README.md`
- `tests/test_curve_spread_parity.py`

## Shared analytics helpers

Primitives must reach the DB through these helpers, not via raw SQL
in the tool's `compute.py`. They are the canonical fetcher surfaces.

- `shared/analytics/rates_fetch.py`
  Single-tenor and cross-tenor fetchers (cash bonds, OIS, swaps);
  strip-aware helpers (`fetch_strip_position`, `fetch_strip_group`,
  `fetch_cross_market_strip`) for STIR / policy-futures primitives.
- `shared/analytics/event_calendar_fetch.py`
  `fetch_economic_releases` (CPI, NFP, retail sales, PMI, claims …)
  and `fetch_central_bank_meetings` (FOMC / ECB / BOE / BOJ / RBA /
  BOC). Backs the three V1 event primitives.

If a primitive needs a fetcher shape that does not exist yet, add it
to the appropriate helper module and write a unit test alongside it
— do not inline new SQL into `compute.py`.

## DB/container surfaces

These are the environment surfaces to consult before defining the DB-
backed validation path:

- `docker-compose.yml`
- `Dockerfile`
- `database/database.py`
- `database/schema.sql`

The `tsdb` container exposes `macro_data.*` (including `load_audit`)
to both the host and the in-container test runner. Use
`database.database.get_db_engine()` for SQLAlchemy access.

## Additional guidance from the current repo

- Existing primitives often include canonical `TimeSeries` fields and
  snapshot parity expectations (PR15).
- Current wiring tests explicitly check:
  - config passed explicitly
  - correct `CONFIG_PATH`
  - empty-string / None sentinel pattern
  - controlled error envelope
- Existing compute tests explicitly check:
  - bundled config exists and loads
  - conventions present
  - default values
  - explicit config vs auto-load parity
  - convention overrides change behavior
  - honest placeholder guards (PR11) where present
- SQL validation runners are standalone scripts with the current
  repo's own pattern and must be treated as first-class validation,
  not as optional extras.

## Important reminder

The automation must inspect the live repo state before building.
These reference files are the starting point, not a replacement for
reading the actual current implementation relevant to the primitive
at hand.
