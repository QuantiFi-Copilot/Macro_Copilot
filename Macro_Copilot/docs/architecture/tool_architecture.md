# Tool Architecture

The canonical layout for a deterministic-mode tool in `rates_agent/`.
Established by the tool-config refactor pilot (commits 0–6) using
`sovereign_bonds.curve_spread` as the reference implementation; every
new tool, and every legacy tool that gets migrated, follows the same
shape.

## Folder layout

Each tool is its own Python package:

```
rates_agent/<domain>/tools/<tool_name>/
  __init__.py     # public-API re-exports (the function + schema classes + CONFIG_PATH)
  config.yaml     # conventions (system constants) + methodology metadata
  schemas.py      # Pydantic input / output models
  compute.py      # deterministic math, config-driven
```

Reference example: `rates_agent/sovereign_bonds/tools/curve_spread/`.

The four-file shape is the same regardless of bucket:

- **Bucket 1A** simple tools (curve spread, yield level): few conventions,
  short methodology, small `compute.py`.
- **Bucket 1B** assumption-laden tools (forward rates, regime
  classification): more conventions, richer methodology with
  citations, larger `compute.py`.
- **Bucket 2** model-dependent tools (PCA, HMM): conventions include
  model-spec choices (number of components, centering, scaling),
  methodology marked `model_dependent: true` once that field is
  added to the schema.

The differences between buckets are configuration content, not
structural. Don't fragment the layout.

## What goes where

### `config.yaml`

Three top-level blocks:

```yaml
tool:
  name: <mcp_tool_name>          # e.g. calculate_curve_spread_tool
  domain: <sovereign_bonds | ois | ...>
  description: <one-line human description>

conventions:
  <key>:
    value: <scalar>              # bool / int / float / str
    source: <documented tag>     # see docs/architecture/methodology_sources.md
    rationale: <one sentence>
    valid_range: [lo, hi]        # optional, numeric values only

methodology:
  what_it_does: <one-paragraph description, drives future UI card>
  assumptions:                   # optional list
    - "<short string>"
  citations: []                  # optional list
```

Loaded by `shared.config.load_tool_config(path)` and validated against
`shared.config.tool_config.ToolConfig`. The validator enforces:

- `Convention.value` must be scalar (no dicts/lists/None).
- `valid_range` (if present) applies to numeric values only and the
  value must lie within `[lo, hi]`.
- `source` and `rationale` are non-empty strings.
- All required fields present, no unknown top-level keys
  (`extra="forbid"`).
- Models are frozen — accidental mutation through the cache is
  blocked.

### `schemas.py`

Pydantic models for the tool's INPUTS and OUTPUTS:

- `<Tool>Input` — what the LLM extracts from the user per query.
  Fields here are the parameters the LLM legitimately controls
  (curve_family, tenors, lookback_days, field_name, etc.).
- `<Tool>CurrentMetrics` — the snapshot block returned in the
  output's `current_metrics`.
- `<Tool>TimeSeriesRow` — one row of the optional `time_series`.
- `<Tool>Output` — top-level response (current_metrics + optional
  time_series).

**Validators stay in code, not in YAML.** Invariants like "short_tenor
!= long_tenor" or "end_date > start_date" encode mathematical truth
and are not configurable. They live in the Pydantic schema as
`@model_validator(mode="after")`.

### `compute.py`

The deterministic math. Public function signature:

```python
def calculate_<tool>(
    engine: Engine,
    params: <Tool>Input,
    config: ToolConfig | None = None,
) -> dict:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    z_window = config.convention_value("z_score_window_days")
    ffill_limit = config.convention_value("ffill_limit_days")
    # ... etc, every convention pulled explicitly ...

    raw_df = fetch_<...>(engine=engine, ..., field_name=params.field_name)
    # ... math, using primitives from shared/analytics/* with the
    #     conventions passed as kwargs ...
```

Patterns:

- The function takes `engine`, `params`, and an optional `config`.
- When `config=None`, loads the bundled `config.yaml` (process-cached
  by path).
- Every convention is read via `config.convention_value(...)` and
  passed as an explicit kwarg to the underlying primitive.
- No module-level constants for methodology decisions.
- `CONFIG_PATH` is exposed as a public module-level symbol so
  external callers (REST routes, MCP server, tests) can construct
  their own `ToolConfig` from the same source.
- Test seams (`fetch_<...>`, `date`, etc.) live inside this module's
  namespace. Tests patch them at `<package>.compute.<name>`, NOT at
  the package init.

### `__init__.py`

Re-exports the public API for backward-compatible imports:

```python
from .compute import CONFIG_PATH, calculate_<tool>
from .schemas import (<Tool>Input, <Tool>Output, ...)

__all__ = ["CONFIG_PATH", "calculate_<tool>", ...]
```

External callers do
`from rates_agent.<domain>.tools.<tool_name> import calculate_<tool>`
without reaching into the submodules.

## Parameter resolution

In V1 deterministic mode there are TWO layers:

1. **Inputs** — `<Tool>Input` Pydantic. The LLM extracts these from
   the user's prompt; per-query.
2. **Conventions** — `config.yaml`. System constants. Read-only at
   the LLM layer; mutable only by editing the YAML and redeploying.

A future advanced mode will add a third layer (UI controls or
per-user presets), inserted *between* inputs and conventions in the
resolution order. The seam is already in place — `compute()` accepts
a custom `config: ToolConfig`, so an advanced-mode resolver can build
that ToolConfig from defaults + user preset + request override and
hand it in.

The LLM cannot touch conventions in V1. The system prompts forbid it
explicitly, and no MCP tool wrapper exposes a convention as an
LLM-overridable parameter. See the "you NEVER alter the methodology"
rule in both `SOVEREIGN_BONDS_SYSTEM_PROMPT` and `OIS_SYSTEM_PROMPT`.

## How callers consume the tool

REST endpoints, the MCP server, tests, and the parity capture harness
all follow the same pattern:

```python
from rates_agent.<domain>.tools.<tool_name> import (
    CONFIG_PATH as <TOOL>_CONFIG_PATH,
    calculate_<tool>,
)
from shared.config import load_tool_config

# Per request / invocation:
try:
    cfg = load_tool_config(<TOOL>_CONFIG_PATH)
except Exception as exc:
    # 503 / error envelope, caller's controlled-failure path
    ...

result = calculate_<tool>(engine=engine, params=params, config=cfg)
```

Always:
- Wrap `load_tool_config` in the same `try/except` shape the caller
  uses for the tool itself, so a bad YAML surfaces as a clean 503
  (REST) or `{"error": "..."}` envelope (MCP) rather than an
  unhandled 500.
- Pass `config=` explicitly. The auto-load fallback exists for tests
  but production callers should make the config dependency visible
  at the callsite.
- Re-use the loaded `ToolConfig` across an inner loop (e.g. the
  `/curve-shapes` endpoint loops over multiple curves) instead of
  re-loading per iteration.

## Snapshot parity test

Every tool ships with a parity fixture that locks the *real production
output* against known inputs. See
`tests/fixtures/curve_spread_v1/README.md` for the canonical setup:

- Capture script connects to the live DB, calls the tool, records the
  raw rows, the frozen-today date, and the expected output, plus a
  `capture` provenance block (captured_at, database_name, as_of_date,
  raw_rows_count, raw_rows_sha256).
- Test replays captured rows through a mocked fetcher, verifies the
  raw-rows hash matches (tamper detection), runs the tool with
  `date.today()` patched to the recorded value, and asserts byte-equal
  output (with a 1e-9 absolute tolerance to absorb harmless float
  noise).
- Regenerate fixtures only after a deliberate methodology change.
  The regenerated diff should be inspected in the same PR as the
  methodology change, with rationale recorded.

The capture script reads from the same `config.yaml` the tool uses
(via `CONFIG_PATH`), so a bump to `z_score_window_days` or
`z_score_buffer_multiplier` in YAML propagates to fixture regeneration
automatically.

## CI lint

`shared.config.lint.check_yaml_consistency` walks every
`rates_agent/*/tools/*/config.yaml` and flags conventions whose
**value** disagrees across tools. Run it via:

```bash
python -m shared.config.lint
```

What it catches: a future `ois.curve_spread` and
`sovereign_bonds.curve_spread` should both use `z_score_window_days:
252`. If one drifts to 504 silently, the lint fails CI loudly.

What it does NOT flag: different `source` / `rationale` text across
tools — two tools can legitimately cite different sources for the
same numeric default.

## Source-tag policy

`Convention.source` is a non-empty string. The set of legitimate
values is documented in `docs/architecture/methodology_sources.md`. New
source tags require a one-line entry there explaining what the tag
means. At time of writing, the source field is enforced as a
*non-empty string only* — strict enum enforcement is a planned
tightening once the source set has stabilised.

## Central-knob discipline (A13)

Each tool exposes ONLY its central methodological choice as a user-
facing input — the choice that defines what the tool IS.  All
ancillary methodology stays in YAML and is NOT user-overridable in V1.

This is the practical enforcement of the "deterministic mode" boundary:
the LLM gets a small, well-typed input surface; the methodology stays
locked in code + YAML.

| Tool | Central knob (user input) | Ancillary (YAML, not user-overridable) |
|---|---|---|
| `curve_spread` | `curve_family`, `short_tenor`, `long_tenor`, `lookback_days` | z-score window, ddof, ffill, default field, rounding |
| `yield_levels` | `curve_family`, `tenor`, `lookback_days` | z-score window, ddof, ffill, default field, rounding |
| `butterfly` | `curve_family`, `short/belly/long_tenor`, `lookback_days` | z-score window, ddof, ffill, weights (50-50 locked), rounding |
| `cross_market_spread` | `curve_family_1/2`, `tenor`, `lookback_days` | z-score window, ddof, ffill, rounding |
| `curve_move_classifier` | `curve_family`, `front/back_tenor`, `lookback_period` | classifier thresholds, ffill, rounding |
| `zscore_custom` | `curve_family`, `tenor`, **`z_score_window_days`** | z-score `min_periods`, `ddof`, `buffer_multiplier`, ffill, rounding |
| `rolling_regression` | `target_spec`, `regressor_specs`, **`regression_window_days`** | `regression_min_periods`, `add_constant`, solver, ffill, rounding |

Structural choices (formulas, sign conventions, anchoring, solver) are
locked in code or guarded with the `NotImplementedError`
honest-placeholder pattern documented under
`MethodologyMeta.planned_extensions`.  See the existing
`trailing_range_window_days = 252` guard in any of the migrated tools
for the canonical example.

## Tool category — honesty mechanism

`ToolMeta.category` (added by the v6 sprint) is a `Literal`-enforced
field on every tool's `config.yaml`.  Two values:

### `desk_invariant_primitive`

A trader on any major rates desk would recognise the tool's name and
know what its inputs and outputs are without a methodology preface.
Configuration is calibration, not interpretation.

Examples:
- `curve_spread` ("2s10s")
- `yield_levels` ("where's UST 10Y")
- `butterfly` ("2s5s10s fly")
- `cross_market_spread` ("BTP-Bund 10Y")
- `curve_move_classifier` ("today's move was a bull-steepener")
- `zscore_custom` ("60-day z-score of UST 10Y")
- `beta_adjusted_spread` ("beta-adjusted RV of BTP vs Bund")
- `half_life` ("OU half-life of the residual")

### `quant_standard_analytic`

Textbook quant primitive whose interpretation is universal but whose
configuration must be specified before use.  A trader recognises the
concept; a methodology preface is needed before consuming the output.

Examples:
- `yield_change_decomposition_simple` (caller-supplied groupings)
- `rolling_regression` (window, regressor selection are central choices)
- `pca_yield_curve` (lookback, change frequency, n_components)
- `yield_change_attribution_pca` (depends on PCA loadings)

The default for `category` is `desk_invariant_primitive` so the
existing five migrated tools keep their identity without explicit YAML
edits.  Any new tool must declare its category explicitly when it does
not fit the default.

## Doing things this way matters

The substrate is built so you can add a new tool — Bucket 1A, 1B, or
2 — by:

1. Creating the four-file folder under
   `rates_agent/<domain>/tools/<tool_name>/`.
2. Writing the YAML, schema, and compute function.
3. Wiring three or four external callers (MCP server, REST routes,
   tests) to import from the new path.

No orchestrator changes. No frontend changes. No `shared/analytics/`
changes (assuming the math primitives already exist; if not, add them
as parameter-driven kwargs the same way commit 2 of the pilot did).
The CI lint enforces convention-value consistency across the
roster automatically.
