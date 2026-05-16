# Repo Reference Map

These are the files the primitive automation must consult before
building or reviewing a primitive.

## Core architecture docs

- `docs/architecture/tool_architecture.md`
  Canonical primitive layout and configuration contract.
- `docs/architecture/methodology_sources.md`
  Registered YAML source tags.
- `docs/technical_debt.md`
  Explicit build blockers and known architecture constraints.
- `automation/primitive_automation/TESTING_AND_DB_VALIDATION_POLICY.md`
  Mandatory read-only DB-backed validation rules for this automation.
- `automation/primitive_automation/QUOTA_AND_RESUME_POLICY.md`
  Required quota contract, thresholds, and resume semantics.
- `automation/primitive_automation/BACKGROUND_EXECUTION_POLICY.md`
  Required background scheduler / catalog-loop behavior.
- `automation/primitive_automation/DISCORD_STATUS_POLICY.md`
  Required durable state for Discord-readable progress reporting.
- `automation/primitive_automation/OPENCLAW_CRON_RUNBOOK.md`
  Recommended recurring scheduler shape.

## Config validation + lint

- `shared/config/tool_config.py`
  Exact schema and validation behavior for `config.yaml`.
- `shared/config/lint.py`
  Cross-tool convention drift check.

## Reference primitive implementations

Start with these first:

- `rates_agent/sovereign_bonds/tools/yield_levels/`
- `rates_agent/sovereign_bonds/tools/curve_spread/`
- `rates_agent/sovereign_bonds/tools/cross_market_spread/`
- `rates_agent/sovereign_bonds/tools/butterfly/`
- `rates_agent/ois/tools/swap_spread/`

When the category matches, also inspect:

- `rates_agent/sovereign_bonds/tools/rolling_regression/`
- `rates_agent/sovereign_bonds/tools/pca_yield_curve/`
- `rates_agent/sovereign_bonds/tools/yield_change_attribution_pca/`

## Reference wiring surfaces

- `rates_agent/sovereign_bonds/mcp_server.py`
- `rates_agent/ois/mcp_server.py`
- `rates_agent/sovereign_bonds/tools/schemas/__init__.py`

## Workflow primitive registration

- `rates_agent/workflows/__init__.py`

Consult this if the primitive should compose into workflow templates or
other operator/workflow surfaces.

## Reference tests

Use these as the primary test pattern:

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

## DB/container surfaces

These are the environment surfaces to consult before defining the DB-
backed validation path:

- `docker-compose.yml`
- `Dockerfile`
- `database/database.py`
- `database/schema.sql`

## Additional guidance from the current repo

- Existing primitives often include canonical `TimeSeries` fields and
  snapshot parity expectations.
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
  - honest placeholder guards where present
- SQL validation runners are standalone scripts with the current repo's
  own pattern and must be treated as first-class validation, not as
  optional extras

## Important reminder

The automation must inspect the live repo state before building.

These reference files are the starting point, not a replacement for
reading the actual current implementation relevant to the primitive at
hand.
