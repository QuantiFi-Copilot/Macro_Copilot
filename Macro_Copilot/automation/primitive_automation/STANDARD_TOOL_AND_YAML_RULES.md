# Standard Tool And YAML Rules

This file is the direct build/review contract for standard primitive
tools in this repo.

It consolidates:

- the repo's standard-tool definition
- the canonical per-tool folder pattern
- the V1 configuration-offloading rules
- the category rules

## A. Definition of a standard tool

A tool is standard when:

- the finance concept is desk-recognizable
- the methodology is explicit
- the dependency chain is visible
- the output carries enough provenance to reconstruct what was done
- no load-bearing choice is hidden or silently drifting

A tool FAILS the standard test if:

- a consequential method choice is hidden
- a proxy stands in for missing required metadata
- two runs can differ because upstream methodology changed silently
- the output does not surface the method/dependency state
- the prompt could materially mean different things without the tool
  making its chosen method explicit

## B. Category rules

Two categories exist:

### `desk_invariant_primitive`

Use when a trader would recognize the tool's name and know what it is
without a methodology preface.

Examples already in the repo:

- `yield_levels`
- `curve_spread`
- `cross_market_spread`
- `butterfly`
- `swap_spread`
- `curve_move_classifier`
- `zscore_custom`
- `beta_adjusted_spread`
- `half_life`

### `quant_standard_analytic`

Use when the concept is still standard, but its interpretation depends
on an explicit methodology preface or central configuration.

Examples already in the repo:

- `rolling_regression`
- `pca_yield_curve`
- `yield_change_attribution_pca`

Do not tag everything as `desk_invariant_primitive` by default.
Pick the category honestly.

## C. Canonical folder pattern

Every deterministic tool must be a package:

```text
rates_agent/<domain>/tools/<tool_name>/
  __init__.py
  config.yaml
  schemas.py
  compute.py
```

This is mandatory across bucket types.

## D. What belongs in `config.yaml`

`config.yaml` has three top-level blocks:

```yaml
tool:
  name:
  domain:
  description:
  category:

conventions:
  <key>:
    value:
    source:
    rationale:
    valid_range:

methodology:
  what_it_does:
  assumptions:
  citations:
  planned_extensions:
```

Use registered methodology source tags from:

`docs/architecture/methodology_sources.md`

Known tags in current use include:

- `industry_standard_1y_window`
- `industry_standard_sample_std`
- `bloomberg_field_convention`
- `derived_from_window`
- `team_judgment_pending_review`
- `trading_day_convention`
- `legacy_default_pre_pilot`

Do not invent vague source tags like:

- `default`
- `standard`
- `convention`
- `bloomberg`

## E. What belongs in `schemas.py`

Expected shape:

- `<Tool>Input`
- `<Tool>CurrentMetrics`
- `<Tool>TimeSeriesRow` where applicable
- `<Tool>Output`

Rules:

- only legitimate per-query inputs belong in `<Tool>Input`
- optional `field_name`-style inputs default to `None`
- cross-field invariants live in validators
- validators stay in code, not YAML
- output models must reflect the canonical wire format honestly

## F. What belongs in `compute.py`

The public signature should look like:

```python
def calculate_<tool>(
    engine,
    params: <Tool>Input,
    config: ToolConfig | None = None,
) -> dict:
    if config is None:
        config = load_tool_config(CONFIG_PATH)
```

Rules:

- expose `CONFIG_PATH`
- load config when `config is None`
- read conventions explicitly with `config.convention_value(...)`
- pass methodology choices explicitly into helper functions
- no hidden module-level methodology constants
- return controlled `{"error": "..."}` envelopes for recoverable
  failures where the surrounding pattern expects that

## G. What belongs in `__init__.py`

Re-export:

- `CONFIG_PATH`
- compute function
- schema classes

This keeps imports stable for the rest of the repo.

## H. Input/convention discipline

In V1 deterministic mode:

- the LLM may set inputs
- the LLM may NOT alter conventions

Only expose a methodology knob as an input if it is the central
user-facing choice that defines the tool.

Everything else belongs in YAML.

## I. No instrument-specific tool names

Do not build tools like:

- `get_italy_10y_yield_tool`
- `get_bund_10y_tool`
- `get_ty1_price_tool`

The primitive must own a concept, not a single instrument instance.

## J. No proxy concepts

Do not ship a weakened or transformed proxy under the name of the real
primitive.

Examples of forbidden behavior:

- using a 1:1 raw proxy where the real desk concept is DV01-weighted
- using guessed metadata when the real concept depends on trusted
  reference data
- using an internal heuristic and presenting it as the canonical market
  object

If the real thing is blocked, defer it.

## K. Workflow composition requirement

If the primitive should be available to the workflow/template layer:

- it must emit canonical `TimeSeries`
- it must be registered in `rates_agent/workflows/__init__.py`
- it must declare correct `output_field_units`

This is part of the primitive contract, not optional polish.
