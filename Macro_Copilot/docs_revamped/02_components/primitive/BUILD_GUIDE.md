# Primitive Build Guide

> **Single front-door manual for adding a new primitive end-to-end.** Walks one contributor (human or AI agent) through the 8-stage assembly line from "is this concept a primitive?" to "Stage-8 closeout sign-off," with copy-paste skeletons at each stage and pointers to the deep doctrine where decisions need to be cited.

**Version:** v1
**Last reviewed:** 2026-05-29
**Status:** load-bearing. Required reading at the **start** of any new-primitive PR — before [`README.md`](README.md) (doctrine, PR1–PR16) and [`runbook.md`](runbook.md) (backend procedure, Steps 1–11), both of which this guide subsumes into a single end-to-end walk.
**Audience:** any contributor (human or AI agent) shipping a new primitive.
**AC class:** Adding a new primitive is **Load-bearing** per [`../../00_thesis/02_ai_agent_development_contract.md`](../../00_thesis/02_ai_agent_development_contract.md). Run the full self-check at every stage gate; do not skip.

---

## Why this guide exists

The primitive contract is genuinely standardised — but the standardisation is spread across **14+ load-bearing documents**:

- Backend doctrine ([`README.md`](README.md) — PR1–PR16)
- Backend procedure ([`runbook.md`](runbook.md) — Steps 1–11, backend-only)
- Convention exposure decisions + standalone bridge ([`../../03_standards/methodology_exposure.md`](../../03_standards/methodology_exposure.md))
- Dual Build-view contract ([`../../03_standards/rendering_density.md`](../../03_standards/rendering_density.md))
- 7-axis "done" definition ([`../../03_standards/tool_lifecycle.md`](../../03_standards/tool_lifecycle.md))
- 8-stage assembly line + per-tool tracker template ([`../../03_standards/lifecycle_checklist_template.md`](../../03_standards/lifecycle_checklist_template.md))
- Frontend module contract ([`../frontend_module/README.md`](../frontend_module/README.md) — FM1–FM12)
- Frontend module procedure ([`../frontend_module/runbook.md`](../frontend_module/runbook.md))
- Frontend THESIS template ([`../frontend_module/thesis_template.md`](../frontend_module/thesis_template.md))
- Frontend tier vocabulary ([`../frontend_module/tiers.md`](../frontend_module/tiers.md))
- Backend test patterns ([`../../03_standards/test_patterns.md`](../../03_standards/test_patterns.md))
- Frontend test patterns ([`../../03_standards/frontend_test_patterns.md`](../../03_standards/frontend_test_patterns.md))
- Typed-boundary Pydantic discipline ([`../../03_standards/typed_boundary_discipline.md`](../../03_standards/typed_boundary_discipline.md))
- DB metadata table ADR ([`../../05_decisions/0015-tool-metadata-db-table.md`](../../05_decisions/0015-tool-metadata-db-table.md))

Without a navigation hub a contributor lands on `primitive/README.md` and reads about the four-file backend shape — missing the `exposure:` blocks, the dual-view, the lifecycle checklist, the DB curated migration, the detail bridge, the monitor widget, the mockups, the `methodology_label` threading, the MCP integer-sentinel pattern, the per-tool README, and the `_conventions_from_config` resolver. That is roughly **60–70% of what the standardised pilot tool ships**.

This guide is the single entry point that walks every contributor through every stage in order, links out to the doctrine when a decision needs citing, and ends with a copy-paste checklist that mirrors the per-tool [`LIFECYCLE_CHECKLIST.md`](../../03_standards/lifecycle_checklist_template.md) tracker.

**Canonical reference primitives** — read alongside this guide:

- [`rates_agent/inflation_indexed_bonds/tools/real_yield_level/`](../../../rates_agent/inflation_indexed_bonds/tools/real_yield_level/) — Phase-1 pilot, first tool brought to parity.
- [`rates_agent/inflation_indexed_bonds/tools/breakeven_inflation_simple/`](../../../rates_agent/inflation_indexed_bonds/tools/breakeven_inflation_simple/) — Phase-1 pilot, second tool. **This is the closest thing to a finished worked example**; use it as the parity target for every artifact named in this guide.

---

## How this guide is organised

The guide is **the 8-stage assembly line** from [`../../03_standards/lifecycle_checklist_template.md §4`](../../03_standards/lifecycle_checklist_template.md). One section per stage. Each stage carries:

- **What you create at this stage** — the exact files / fields the pilot tool ships.
- **Decision protocol** — which deeper doc records the cite-by-ID rule.
- **Copy-paste skeleton** — minimum starting shape; do not invent variations.
- **Stage gate** — what must be ☑ before promoting to the next stage.

The stages map to the 7 lifecycle axes per [`../../03_standards/lifecycle_checklist_template.md §7.1`](../../03_standards/lifecycle_checklist_template.md).

| Stage | Concern | Owner doc |
|---|---|---|
| **0** | Pre-flight: decisions BEFORE any code | [`README.md`](README.md) PR1–PR6 + [`runbook.md`](runbook.md) Pre-flight |
| **1** | Backend specification (config + schemas + compute + MCP wrapper + manifest + standalone-bridge contract) | [`README.md`](README.md) PR7–PR16 + [`../../03_standards/methodology_exposure.md`](../../03_standards/methodology_exposure.md) |
| **2** | Backend tests (compute + wiring + parity + sql_validation) | [`../../03_standards/test_patterns.md`](../../03_standards/test_patterns.md) + PR15, PR16 |
| **3** | DB metadata migration | [`../../05_decisions/0015-tool-metadata-db-table.md`](../../05_decisions/0015-tool-metadata-db-table.md) + [`../../03_standards/tool_lifecycle.md §2 axes 1/5/7`](../../03_standards/tool_lifecycle.md) |
| **4** | Frontend module spec | [`../frontend_module/README.md`](../frontend_module/README.md) FM1–FM12 + [`../../03_standards/rendering_density.md`](../../03_standards/rendering_density.md) §5 |
| **5** | Frontend bridge endpoint | [`../../03_standards/methodology_exposure.md §5`](../../03_standards/methodology_exposure.md) |
| **6** | Frontend surfaces (BuildExtended + BuildCompact + Monitor + Mockups) | [`../../03_standards/rendering_density.md`](../../03_standards/rendering_density.md) §§2,3,6 |
| **7** | Mirrors + contracts (surface_contract, per-tool README, graphify) | [`../../03_standards/tool_lifecycle.md §4`](../../03_standards/tool_lifecycle.md) + [`../surface_contract.md`](../surface_contract.md) §§4, 10, 11 |
| **8** | Closeout sign-off (source-material verification + final review) | [`../../03_standards/lifecycle_checklist_template.md §5`](../../03_standards/lifecycle_checklist_template.md) |

---

## Stage 0 — Pre-flight: seven decisions BEFORE writing any code

A new-primitive PR is hard to undo (audit history, parity fixtures, downstream registrations all key on the primitive's name). Make these seven decisions and document them in the PR description before any code lands. **If any is uncertain, stop and ask the human (AC8).**

This stage is the same as [`runbook.md`](runbook.md) §"Pre-flight check"; reproduced here so the assembly line reads top-to-bottom.

| # | Decision | Citation |
|---|---|---|
| 1 | **Concept novelty** — name the desk concept in one sentence; confirm no existing primitive's `tool.name + one_liner` covers this use case under a different input. | [PR5](README.md) |
| 2 | **Parsimony** — write the composition that would produce the same output (`primitive_A → operator_X → primitive_B → ...`); defend the new primitive against ≥1 of the five criteria (accuracy / efficiency / interpretability / provenance / LLM tool-selection clarity). | [PR4](README.md) |
| 3 | **Metadata sufficiency** — list every metadata field the real desk definition requires; confirm each lives in `instrument_master` / `market_data_daily` already, or in a playbook landing this PR. **No proxies.** | [PR6](README.md) |
| 4 | **Domain residence** — name the desk role that asks for this primitive; the folder lives under that sub-agent. Cross-domain data fetching goes through `shared/analytics/*` helpers, never `from <other_agent>`. | [PR3](README.md) |
| 5 | **Concept cohesion** — read your one-sentence concept aloud; if it uses "or" or has an internal mode switch returning different output shapes, **split before drafting**. | [PR2](README.md) |
| 6 | **Central methodology surface** — name the single methodological choice (or cohesive multi-knob — see [PR8](README.md)) that defines what the primitive IS. Everything else goes in YAML. | [PR8](README.md) |
| 7 | **Standardness self-check** — walk through PR7–PR11 once; if any is "no", surface to the human rather than ship a non-standard primitive under a standard label. | [README.md](README.md) Group III |

**Output of Stage 0:** the PR description's "Pre-flight" block, citing by PR-number per [AC2](../../00_thesis/02_ai_agent_development_contract.md). Reviewer reads this first; if missing or hand-waved, PR bounces before any code review.

**Stage gate:** all seven decisions written + cited in the PR description.

---

## Stage 1 — Backend specification

You are now writing code. Stage 1 has **four sub-stages (1A → 1D)** mirroring the per-tool [`LIFECYCLE_CHECKLIST.md`](../../03_standards/lifecycle_checklist_template.md) Stage 1 row-for-row.

### 1.0 Folder placement + naming

Place the tool folder under the agent + sub-agent that owns the concept (Stage-0 decision 4):

```
<agent>/<sub_agent>/tools/<tool_slug>/
├── __init__.py
├── config.yaml
├── schemas.py
├── compute.py
├── README.md
└── LIFECYCLE_CHECKLIST.md
```

Naming conventions (per [`runbook.md`](runbook.md) Step 2):

- **`<tool_slug>`** is the *concept slug* in `snake_case` — e.g. `breakeven_inflation_simple`, `real_yield_level`, `curve_spread`. **Not** the MCP `tool.name`.
- The MCP `tool.name` typically follows `<verb>_<tool_slug>_tool` where the verb is `calculate_` / `get_` / `build_` / `compute_` / `classify_`. Pick the verb that reads naturally.
- The Python compute function matches the verb pattern without `_tool` suffix.

> The pilot tool's folder is `breakeven_inflation_simple/` (slug); the MCP `tool.name` is `calculate_breakeven_inflation_simple_tool` (verb + slug + `_tool`); the Python function is `calculate_breakeven_inflation_simple()` (verb + slug).

### 1A. `config.yaml` — conventions with `exposure:` blocks

The `config.yaml` is the **single source of truth for methodology** (PR7). Three top-level blocks:

```yaml
tool:
  name: <verb>_<tool_slug>_tool      # the MCP-facing name
  domain: <sub_agent>                # e.g. inflation_indexed_bonds
  description: |
    <one paragraph describing what this computes>

conventions:
  <convention_name>:
    value: <scalar>
    source: <registered tag from methodology_disclosure.md §2>
    rationale: <one sentence>
    valid_range: [lo, hi]            # for numerics
    exposure:                         # REQUIRED — methodology_exposure.md §3
      expose: true | false
      rationale: <full sentence — cites Criterion A and/or B per §2.3>
      decided_at: "YYYY-MM-DD"
      decided_in_pr: "#<N>"
      # ↓ REQUIRED only when expose: true
      input_field: <Pydantic field name>
      pydantic_type: <type expression>
      default_source: yaml | input_explicit
      promoted_from_yaml_in_pr: "#<N>" | "n/a"
  <next_convention>:
    ...

methodology:
  what_it_does: |
    <one paragraph — drives the methodology card AND the wire-honesty
     methodology_label on the output>
  assumptions:
    - <each assumption>
  citations: []
  planned_extensions:
    - <if applicable: what's pinned in V1 and the path to configurability>
```

**For each convention, run the exposure decision protocol per [`../../03_standards/methodology_exposure.md §2`](../../03_standards/methodology_exposure.md):**

1. Criterion A — does it materially change the output a desk user reads? (252 → 60 z-window → tactical vs structural framing = ✅)
2. Criterion B — does it sit inside the analytical / statistical relationship the tool computes? (display precision = ❌, ffill = borderline)
3. Both TRUE → `expose: true` admissible.
4. Borderline → default `expose: false` with planned-extension note.

The [PR8](README.md) cap (single central methodology surface, or cohesive multi-knob) bounds how many `expose: true` decisions are admissible on one tool.

Cite [`../../03_standards/methodology_exposure.md §3.3`](../../03_standards/methodology_exposure.md) for the three example shapes (exposed / locked / deferred).

> The pilot tool exposes 4 conventions (`z_score_window_days`, `z_score_min_periods`, `z_score_ddof`, `default_field_name` via `field_name`); the remaining 9 stay YAML-locked. Each `exposure:` block names the Criterion A/B verdict + the desk use case justifying override.

### 1B. `schemas.py` — Pydantic Input/Output + None-sentinel pattern

The Pydantic Input carries the instrument selectors + the exposed-convention overrides as `Optional[T]` with `None` default (the sentinel that resolves against YAML at `compute()` time). The Output mirrors the wire payload.

```python
"""Pydantic schemas for the <tool_slug> tool.

Validation layering
-------------------
- ``field_name`` defaults to None — the sentinel that resolves against
  YAML's ``default_field_name`` convention.  Mirrors the pattern in
  yield_levels / curve_spread / real_yield_level.
- The three rolling-z-score conventions (z_score_window_days /
  z_score_min_periods / z_score_ddof) follow the same None-sentinel
  pattern; resolution happens in compute._conventions_from_config.
- Cross-field invariants (e.g. "short_tenor != long_tenor") live in
  code as @model_validator(mode="after"), NOT in YAML.
"""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


class <Tool>Input(BaseModel):
    """Parameters for computing <one-line concept>."""

    # Instrument selectors (Stage-0 decision 6 — central knob)
    <instrument_field>: str = Field(..., description="...")
    tenor: str = Field(..., min_length=1, description="...")
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "Calendar days of *displayed* history.  Does NOT control "
            "rolling z-score window or trailing range window — those "
            "are independent conventions in config.yaml."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  When None, falls through to "
            "``default_field_name`` from config.yaml.  LLM/HTTP wrappers "
            "MUST translate their wire-level sentinel (empty string for "
            "MCP, missing param for FastAPI) to None before constructing."
        ),
    )

    # Exposed methodology surface — None-sentinel + YAML fallthrough
    z_score_window_days: Optional[int] = Field(
        default=None, ge=60, le=1260,
        description="..."
    )
    z_score_min_periods: Optional[int] = Field(
        default=None, ge=20, le=252,
        description="..."
    )
    z_score_ddof: Optional[int] = Field(
        default=None, ge=0, le=1,
        description="..."
    )

    @model_validator(mode="after")
    def _structural_invariant(self) -> "<Tool>Input":
        # Cross-field invariants here.  See PR7 — math invariants stay
        # in code as Pydantic validators, NOT in YAML.
        ...
        return self


class <Tool>CurrentMetrics(BaseModel):
    """Snapshot metrics.  Carries the ``methodology_label`` field
    threaded from YAML at runtime — NOT a hardcoded Python literal —
    so a YAML edit to the disclosure flows through to the wire."""
    as_of_date: str
    # ... per-tool snapshot fields ...
    methodology_label: str = Field(..., description="...")


class <Tool>TimeSeriesRow(BaseModel):
    """Single row in the bespoke wire-frozen time series."""
    date: str
    # ... per-tool fields ...


class <Tool>Output(BaseModel):
    """Top-level response.

    ``time_series`` is the bespoke shape; ``time_series_*`` are the
    canonical ``shared.schemas.TimeSeries`` payloads the workflow /
    operator layer consumes.  Both are computed from the same display
    DataFrame so they cannot drift."""
    current_metrics: <Tool>CurrentMetrics
    time_series: List[<Tool>TimeSeriesRow]
    time_series_<name>: TimeSeries
    # ... add canonical TimeSeries variants per tool ...


__all__ = [
    "<Tool>Input",
    "<Tool>CurrentMetrics",
    "<Tool>TimeSeriesRow",
    "<Tool>Output",
]
```

**Schema invariants** ([`../../03_standards/typed_boundary_discipline.md`](../../03_standards/typed_boundary_discipline.md)):

- All Pydantic models use `BaseModel` (frozen is default per the standard).
- Inputs use `extra="forbid"`-equivalent strictness (Pydantic v2 default behaviour).
- Per [PR14](README.md), any output field whose name embeds a methodologically-load-bearing parameter (e.g. `high_252d_bps`, `percentile_252d`) is wire-frozen; the corresponding YAML convention is guarded with `NotImplementedError` in `compute.py` (next stage).

### 1C. `compute.py` — `_conventions_from_config` + canonical signature

The canonical `compute()` signature is **fixed**:

```python
"""compute.py — <one-line concept>.

<Module docstring explaining the math, fetch shape, no-proxy guard if
applicable, wire-freeze rationale for any *_252d field, and the
test seam pattern.>
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from <agent>.<sub_agent>.tools.<tool_slug>.schemas import (
    <Tool>Input,
    <Tool>Output,
    <Tool>CurrentMetrics,
    <Tool>TimeSeriesRow,
)
from shared.analytics.levels import period_changes, trailing_high_low_percentile
from shared.analytics.spreads import (
    compute_spread_bps,
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)
from shared.analytics.rates_fetch import fetch_single_tenor
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Public — external callers (mcp_server, REST routes, tests) build a
# ToolConfig from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# Trailing-range window is wire-frozen at 252 in V1 (PR14).
_FROZEN_TRAILING_WINDOW: int = 252


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(
    config: ToolConfig,
    params: Optional[<Tool>Input] = None,
) -> dict:
    """Pull methodology kwargs from ToolConfig, applying caller overrides.

    Override semantics (None-sentinel + YAML fallthrough)
    -----------------------------------------------------
    For each Pydantic Input field whose YAML convention has
    ``exposure.expose: true``:
      - Input field is None (schema default) → use YAML value.
      - Input field carries explicit value → override YAML for this call.

    Remaining YAML-locked conventions are read straight from config.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in "
            "methodology.planned_extensions but is not yet implemented. "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the "
            "output field names embed that number on the wire."
        )

    def _override(convention_name: str) -> Any:
        """Caller's Input value wins when non-None; else YAML."""
        if params is not None:
            input_value = getattr(params, convention_name, None)
            if input_value is not None:
                return input_value
        return config.convention_value(convention_name)

    return {
        # Exposed methodology surface — Input overrides accepted
        "z_window":      _override("z_score_window_days"),
        "z_min_periods": _override("z_score_min_periods"),
        "z_ddof":        _override("z_score_ddof"),
        # YAML-locked conventions — read straight from config
        "z_round_decimals":   config.convention_value("z_score_round_decimals"),
        "buffer_multiplier":  config.convention_value("z_score_buffer_multiplier"),
        "ffill_limit":        config.convention_value("ffill_limit_days"),
        "period_offsets": {
            "daily":   config.convention_value("daily_change_offset_rows"),
            "weekly":  config.convention_value("weekly_change_offset_rows"),
            "monthly": config.convention_value("monthly_change_offset_rows"),
        },
        "trailing_window":      trailing,
        "bps_round_decimals":   config.convention_value("bps_round_decimals"),
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
    }


# ============================================================================
# PUBLIC API — canonical signature
# ============================================================================

def calculate_<tool_slug>(
    engine: Engine,
    params: <Tool>Input,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate <one-line concept>.

    Returns
    -------
    dict
        Serialised ``<Tool>Output``, or ``{"error": "..."}`` on
        recoverable failure (PR11 controlled-envelope pattern).
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    conv = _conventions_from_config(config, params)
    # ... pull conv values ...
    default_field_name = config.convention_value("default_field_name")

    # Resolve field_name: caller wins; None falls through.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # Wire-honesty disclosure threaded from YAML — NOT hardcoded.
    methodology_label = config.methodology.what_it_does.strip()

    # ... fetch + compute + output assembly ...

    output = <Tool>Output(
        current_metrics=...,
        time_series=...,
        # ... including methodology_label sourced from YAML ...
    )
    return output.model_dump()
```

**`compute.py` invariants** (cite by ID):

- Every numeric / string constant is either a mathematical truth (`100` for pct→bps) or read via `config.convention_value(...)`. No module-level `_Z_WINDOW = 252`. **[PR7]**
- The `_conventions_from_config(config, params)` resolver is the single place that maps None-sentinel Input overrides to YAML defaults. **[methodology_exposure.md §4.1]**
- `methodology_label` is sourced from `config.methodology.what_it_does` at runtime, **not** hardcoded as a Python string. **[PR10]**
- Any output field name embedding a load-bearing parameter (e.g. `high_252d_bps`) is wire-frozen; `compute()` raises `NotImplementedError` if the YAML convention is changed without renaming the field. **[PR14]**
- Recoverable failures return `{"error": "..."}` (controlled envelope per [PR11](README.md)); unrecoverable bugs raise. Never silently fall back.

### 1D. `__init__.py` — public re-exports

```python
from .compute import CONFIG_PATH, calculate_<tool_slug>
from .schemas import (
    <Tool>Input,
    <Tool>CurrentMetrics,
    <Tool>TimeSeriesRow,
    <Tool>Output,
)

__all__ = [
    "CONFIG_PATH",
    "calculate_<tool_slug>",
    "<Tool>Input",
    "<Tool>CurrentMetrics",
    "<Tool>TimeSeriesRow",
    "<Tool>Output",
]
```

Re-exports make `from <agent>.<sub_agent>.tools.<tool_slug> import ...` work without reaching into submodules. **[`runbook.md`](runbook.md) Step 6.]**

### 1E. MCP wrapper — integer-sentinel pattern for exposed overrides

The MCP wrapper lives in `<agent>/<sub_agent>/mcp_server.py`. It converts the LLM-facing wire (which prefers concrete primitives over `Optional[T]`) into a Pydantic Input the compute function expects.

**Pattern (mirror the pilot at [`rates_agent/inflation_indexed_bonds/mcp_server.py`](../../../rates_agent/inflation_indexed_bonds/mcp_server.py)):**

For each `expose: true` convention, expose an integer/string sentinel kwarg with a sentinel default the wrapper translates to `None`:

| Convention pydantic type | MCP wrapper sentinel | Wrapper translation |
|---|---|---|
| `Optional[int]` (window-style) | `kwarg_x: int = 0` | `x = None if kwarg_x == 0 else kwarg_x` |
| `Optional[int]` (ddof-style, 0/1 valid) | `kwarg_x: int = -1` | `x = None if kwarg_x == -1 else kwarg_x` |
| `Optional[str]` (field_name) | `kwarg_x: str = ""` | `x = None if not kwarg_x else kwarg_x` |

The convention is: **the sentinel must be a value OUTSIDE the Pydantic field's `valid_range`** so a real user value never collides with the sentinel. (For `z_score_ddof` whose valid range is `{0, 1}`, the sentinel is `-1`. For `z_score_window_days` whose valid range is `[60, 1260]`, the sentinel is `0`.)

```python
@mcp.tool()
def calculate_<tool_slug>_tool(
    engine: Engine,
    <instrument_field>: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",                   # "" sentinel → None
    z_score_window_days: int = 0,           # 0 sentinel → None
    z_score_min_periods: int = 0,           # 0 sentinel → None
    z_score_ddof: int = -1,                 # -1 sentinel → None
) -> Dict[str, Any]:
    """<docstring; explicitly document each override + sentinel>"""
    params = <Tool>Input(
        <instrument_field>=<instrument_field>,
        tenor=tenor,
        lookback_days=lookback_days,
        field_name=field_name or None,
        z_score_window_days=None if z_score_window_days == 0 else z_score_window_days,
        z_score_min_periods=None if z_score_min_periods == 0 else z_score_min_periods,
        z_score_ddof=None if z_score_ddof == -1 else z_score_ddof,
    )
    config = load_tool_config(CONFIG_PATH)
    return calculate_<tool_slug>(engine=engine, params=params, config=config)
```

The wrapper:

- Calls `load_tool_config(CONFIG_PATH)` explicitly and passes `config=` to `calculate_<tool_slug>` (PR7 — no implicit auto-load).
- Converts internal exceptions to the MCP error envelope (`{"error": "..."}`) at this transport-boundary per [P6](../../00_thesis/01_non_negotiables.md). The compute function returns the envelope directly for recoverable errors; unrecoverable bugs raise.
- The docstring explicitly documents each override + its sentinel so the LLM router sees the override semantics.

### 1F. Manifest entry — PM-facing `one_liner` + derived `pm_overridable`

Add a manifest block in `manifesto/03_tool_manifest/<agent>/<NN>_<sub_agent>_manifest.yml`:

```yaml
- name: <verb>_<tool_slug>_tool
  domain: <sub_agent>
  sub_agent: <sub_agent>
  bucket: 1A | 1B | 2 | Asp
  category: <e.g. cross_market_rv>
  status: built
  implementation:
    folder: rates_agent/<sub_agent>/tools/<tool_slug>/
    config: rates_agent/<sub_agent>/tools/<tool_slug>/config.yaml
    schemas: rates_agent/<sub_agent>/tools/<tool_slug>/schemas.py
    compute: rates_agent/<sub_agent>/tools/<tool_slug>/compute.py
    mcp_wrapper: rates_agent/<sub_agent>/mcp_server.py
  one_liner: |
    <PM-FACING PROSE — one to two sentences answering "what does this
     tell me as a desk user?"  No "analogue of X", no method-name
     references, no implementation talk.  Technical detail belongs in
     the per-tool README + DB tool_metadata.desk_narrative.>
  bucket_rationale: |
    <one paragraph naming the bucket and why>
  pm_overridable:
    - <convention_name>  # one row per `expose: true` convention from §1A
    - <convention_name>
  related_tools:
    - <other_tool>
  workflows: []
  references: |
    <mirrors DB tool_metadata.theoretical_reference after Stage 3>
  built_date: "YYYY-MM-DD"
  validation_status: "Stages 1–7 implemented; Stage 3 DB apply + source-material verification pending"
```

**Add the new tool slug to every sibling tool's `available_tools` list** in the same manifest file — the LLM router uses this to know which tools coexist in the same agent's MCP server.

**Stage 1C `one_liner` discipline** ([`../../03_standards/lifecycle_checklist_template.md §10`](../../03_standards/lifecycle_checklist_template.md)): the `one_liner` is the text the Library card surfaces to users. PM-facing prose only; no implementation language. Reviewer rejects PRs whose `one_liner` reads like a commit message.

### 1G. Standalone bridge contract (claim now; deliver in Stage 5)

Decide at Stage 1 — record in the per-tool `LIFECYCLE_CHECKLIST.md` Stage 1D row — whether the tool ships under the **standalone bridge** pattern (default for all new tools):

- The tool does NOT reuse the shared `MODULE.typedView` closed family.
- The tool ships its OWN typed-detail endpoint at `/api/v1/rates/detail/<tool_kind>` (delivered in Stage 5).

Cite [`../../03_standards/methodology_exposure.md §5`](../../03_standards/methodology_exposure.md) for the five-element bridge.

### 1H. Create the per-tool `LIFECYCLE_CHECKLIST.md` NOW (not later)

Copy [`../../03_standards/lifecycle_checklist_template.md §10`](../../03_standards/lifecycle_checklist_template.md) verbatim into `<tool_folder>/LIFECYCLE_CHECKLIST.md`. Fill in:

- Tool name, domain, started date, current stage.
- Stage 1A's per-convention decision table (every convention from `config.yaml` gets a row).
- Stage 1B–1H rows for the work you just completed.

Do NOT rename or reorder stages; **standardisation depends on identical structure across tools** ([`../../03_standards/lifecycle_checklist_template.md §8`](../../03_standards/lifecycle_checklist_template.md)).

The checklist is the **persistent state** across PRs if Stages 1 → 7 split into multiple PRs. Append to its version log on each gate close.

### Stage 1 gate

All sub-rows in the per-tool `LIFECYCLE_CHECKLIST.md` Stage 1 block are ☑ (or ⊘ with one-line reason):

- ☑ 1A — every `config.yaml` convention carries an `exposure:` block (Criterion A/B-justified, dated).
- ☑ 1B — Pydantic Input + MCP wrapper mirror the `expose: true` set; wrapper docstring covers each override.
- ☑ 1C — manifest `pm_overridable` derived from `expose: true` set; `one_liner` is PM-facing prose; sibling tools' `available_tools` updated.
- ☑ 1D — standalone bridge claimed (typed-detail endpoint planned at `/api/v1/rates/detail/<tool_kind>`); `MODULE.typedView` will be `null` in Stage 4.

---

## Stage 2 — Backend tests

Five files (the **test triplet** + parity + sql_validation) under `tests/`:

```
tests/test_<tool_slug>_compute.py
tests/test_<tool_slug>_wiring.py
tests/test_<tool_slug>_parity.py
tests/test_<tool_slug>_sql_validation.py
tests/fixtures/<tool_slug>_v1/
    captured_raw_rows.json
    expected_output.json
    capture_provenance.json
```

Per [`../../03_standards/test_patterns.md`](../../03_standards/test_patterns.md), [PR15](README.md), [PR16](README.md).

### 2A. `test_<tool_slug>_compute.py` — synthetic math + override coverage

Required test classes (one per concern; match the pilot at [`tests/test_breakeven_inflation_simple_compute.py`](../../../tests/test_breakeven_inflation_simple_compute.py)):

| Class | Asserts |
|---|---|
| `TestBundledConfig` | `load_tool_config(CONFIG_PATH)` returns a valid `ToolConfig`; all declared conventions present with non-empty `source` + `rationale`. |
| `TestComputeHappyPath` | Synthetic input → expected synthetic output. |
| `TestConventionOverrides` | Bump each YAML convention via a custom `ToolConfig` and assert output changes deterministically (per [methodology_exposure.md §6](../../03_standards/methodology_exposure.md)). |
| `TestInputOverrides` | For each `expose: true` convention, pass an explicit value via Pydantic Input and assert it wins over the YAML default (the runtime-override path). |
| `TestExposureBlockContract` | Parse `config.yaml`; assert every `expose: true` convention has a corresponding `Optional[...]` field on the Input AND vice versa. This is the lint-style guard that catches drift. |
| `TestNoProxyGuard` *(if applicable)* | Any structural identity invariant (e.g. instrument_type discriminator) refuses cleanly when violated. |
| `TestSameCountryGuard` *(if applicable)* | Cross-domain / cross-country invariants enforced in code. |
| `TestBoundaryRoundingParity` | Output's bps / z-score / yield fields respect the YAML rounding conventions. |
| `TestMethodologyLabelThreading` | `methodology_label` on the output equals `config.methodology.what_it_does.strip()` (proves the YAML-thread, not a hardcoded literal). |
| `TestImportPathStability` | `from <agent>.<sub_agent>.tools.<tool_slug> import calculate_<tool_slug>, <Tool>Input, ...` works (PR1G re-exports stable). |
| `TestCanonicalTimeSeries` | The bespoke `time_series` and canonical `time_series_*` payloads match 1-to-1 on the same `display_df` (cannot drift). |
| `TestTrailingWindowGuard` *(if applicable)* | `compute()` raises `NotImplementedError` when wire-frozen `trailing_range_window_days` is set to anything other than the frozen value (PR14). |

### 2B. `test_<tool_slug>_wiring.py` — MCP-boundary patterns

Assertions at the MCP wrapper boundary:

- `field_name=None` resolves to the YAML default.
- `field_name=""` is treated identically (empty-string sentinel; the LLM wire's None proxy).
- `field_name="EXPLICIT"` is honored.
- Each integer-sentinel `expose: true` kwarg maps `0` (or `-1` / `""`) to `None` correctly.
- Cross-field Pydantic validators fire before `compute()` runs (the validation error happens at construction time, not in the fetch).
- Controlled error envelopes preserve `{"error": "..."}` shape across the wrapper.

### 2C. `test_<tool_slug>_parity.py` — golden-fixture replay

Per [PR15](README.md):

- Loads `tests/fixtures/<tool_slug>_v1/captured_raw_rows.json` and patches `fetch_single_tenor` to return them.
- Verifies the raw-rows SHA256 matches the captured one (tamper detection).
- Calls `compute()` with the same Input + `ToolConfig` used at capture time.
- Asserts byte-equal output against `expected_output.json` within `1e-9` absolute tolerance.

Any subsequent change to a `conventions:` DEFAULT value requires this fixture to be regenerated **in the same PR** with a one-paragraph rationale.

### 2D. `test_<tool_slug>_sql_validation.py` — read-only DB parity

Per [PR16](README.md):

- Connects to the dev DB via the container stack.
- Uses ONLY `SELECT` queries (no `INSERT`/`UPDATE`/`DELETE`/`ALTER`).
- Independently reproduces the core math against real data — not a smoke test.
- Asserts the Python primitive's output matches the SQL baseline within a tolerance declared at the top of the file.

This file is excluded from default pytest collection (standalone runner pattern).

### 2E. `tests/fixtures/<tool_slug>_v1/` — capture-time provenance

Run the parity-capture script (see [`../../03_standards/test_patterns.md`](../../03_standards/test_patterns.md)) to capture:

- `captured_raw_rows.json` — the raw rows that flowed into `compute()` for known inputs.
- `expected_output.json` — the full expected output as JSON.
- `capture_provenance.json` — `{captured_at, database_name, as_of_date, raw_rows_count, raw_rows_sha256}`.

### Stage 2 gate

- ☑ `pytest tests/test_<tool_slug>_compute.py tests/test_<tool_slug>_wiring.py tests/test_<tool_slug>_parity.py -v` — all green.
- ☑ `python tests/test_<tool_slug>_sql_validation.py` (standalone runner) — green against the dev DB.
- ☑ `python -m shared.config.lint` — exits clean (PR13).
- ☑ Parity fixtures NOT regenerated if no DEFAULT methodology value changed.

---

## Stage 3 — DB metadata migration

Stage 3 populates the **three human-curated TEXT fields** on `macro_data.tool_metadata` per [ADR 0015](../../05_decisions/0015-tool-metadata-db-table.md):

- `theoretical_reference` — institutional textbook / paper / standards (e.g. Tuckman 4e Ch. 22).
- `known_limitations` — what this tool can NOT do; documented dependencies; wire-freezes.
- `desk_narrative` — 3-paragraph user-facing macro framing (the source of the per-tool README's "For desk users" section).

The fourth curated field (`source_material_verified`) stays NULL until Stage 8.

### 3A. The migration file

Path: `database/migrations/YYYY-MM-DD_phase1_<verb>_<tool_slug>_curated.sql`

Pattern (mirror the pilot at [`database/migrations/2026-05-28_phase1_calculate_breakeven_inflation_simple_curated.sql`](../../../database/migrations/2026-05-28_phase1_calculate_breakeven_inflation_simple_curated.sql)):

```sql
-- ============================================================================
-- Migration: populate macro_data.tool_metadata HUMAN-CURATED fields for
--            ``<verb>_<tool_slug>_tool``.
--
-- See:
--   - docs_revamped/03_standards/tool_lifecycle.md §2 axes 1/5/7
--   - docs_revamped/03_standards/lifecycle_checklist_template.md §5
--   - docs_revamped/05_decisions/0015-tool-metadata-db-table.md
--
-- APPLY (manually; PR does NOT auto-apply):
--   docker exec -i macro-tsdb psql -v ON_ERROR_STOP=1 -U quantuser -d macrodata \
--       < database/migrations/YYYY-MM-DD_phase1_<verb>_<tool_slug>_curated.sql
-- ============================================================================

-- UPSERT: idempotent.  Three human-curated TEXT fields only.
UPDATE macro_data.tool_metadata
SET
    theoretical_reference = $tref$<full text>$tref$,
    known_limitations     = $klim$<full text>$klim$,
    desk_narrative        = $narr$<full text>$narr$,
    updated_at            = NOW()
WHERE tool_name = '<verb>_<tool_slug>_tool';

-- Defensive INSERT fallback (Phase-0 seed not applied → row absent).
INSERT INTO macro_data.tool_metadata (
    tool_name, domain, category, output_field_units,
    theoretical_reference, known_limitations, desk_narrative
)
SELECT
    '<verb>_<tool_slug>_tool',
    '<sub_agent>',
    '<category>',
    '<output_field_units JSONB>'::jsonb,
    $tref$<full text>$tref$,
    $klim$<full text>$klim$,
    $narr$<full text>$narr$
WHERE NOT EXISTS (
    SELECT 1 FROM macro_data.tool_metadata
    WHERE tool_name = '<verb>_<tool_slug>_tool'
);

-- Stage 8 closeout note (NOT applied here):
--   UPDATE macro_data.tool_metadata
--   SET source_material_verified = jsonb_build_object(
--           'verifier', '<name>', 'date', '<YYYY-MM-DD>',
--           'source',   '<full citation>'
--       ),
--       updated_at = NOW()
--   WHERE tool_name = '<verb>_<tool_slug>_tool';
```

**Why dollar-quoted strings (`$tref$...$tref$`):** the curated text contains punctuation, quotes, and Unicode; dollar-quoting avoids escape ambiguity.

**`output_field_units` JSONB** — mirrors the Pydantic Output's canonical-shape fields:

```json
{"time_series_breakeven": "bps", "time_series_zscore": "z_score"}
```

The unit strings come from the closed `TimeSeriesUnits` enum (`bps`, `pct`, `z_score`, `percentile`, etc.).

### 3B. Apply policy

**The PR does NOT auto-apply migrations.** The live DB is off-limits for automated application; apply manually when authorized:

```sh
docker exec -i macro-tsdb psql -v ON_ERROR_STOP=1 -U quantuser -d macrodata \
    < database/migrations/YYYY-MM-DD_phase1_<verb>_<tool_slug>_curated.sql
```

Until applied, the per-tool `LIFECYCLE_CHECKLIST.md` Stage 3 row stays `⏸ DB apply pending`.

### Stage 3 gate

- ☑ Migration file checked in (idempotent UPDATE + INSERT fallback).
- ☑ `theoretical_reference` + `known_limitations` + `desk_narrative` curated.
- ☑ `output_field_units` JSONB matches the Pydantic Output canonical shape.
- ⏸ Live DB apply (manual; tracked).
- ⏸ `source_material_verified` — Stage 8.

---

## Stage 4 — Frontend module spec

The frontend module folder lives at `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<tool_slug>_tool/` (folder name === MCP `tool.name` exactly, per [FM1](../frontend_module/README.md)).

Stage 4 ships these files (Stages 4 + 6 together produce the full folder; Stage 4 covers `module.ts` + `THESIS.md` + the loader entry):

```
src/modules/primitives/<verb>_<tool_slug>_tool/
├── THESIS.md                      # 4D
├── module.ts                      # 4A–4C
├── surfaces/                      # populated in Stage 6
├── __tests__/
│   └── module.spec.ts             # 4E
└── mockups/                       # populated in Stage 6
```

### 4A. Folder name + loader entry

Folder name === MCP `tool.name` exactly: `<verb>_<tool_slug>_tool` ([FM1](../frontend_module/README.md)).

Add the loader entry in `src/modules/index.ts` in alphabetical position ([FM12](../frontend_module/README.md)):

```ts
import { MODULE as <verb>_<tool_slug>_tool } from './primitives/<verb>_<tool_slug>_tool';
// ... in alphabetical order ...

export const ALL_PRIMITIVE_MODULES: ReadonlyArray<PrimitiveModuleSpec> = [
  // ... insert in alphabetical position ...
  <verb>_<tool_slug>_tool,
];
```

### 4B. `module.ts` — pure-spec assembly

The module spec is a pure value (no side effects per [FM7](../frontend_module/README.md)). Mirror the pilot at [`UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/module.ts`](../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/module.ts):

```ts
import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { <Widget> } from './surfaces/monitor/<Widget>';
import { <SHARED_OPTIONS> } from './surfaces/<tool_slug>Shared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: '<verb>_<tool_slug>_tool',

  // FM3 — tier claims:
  //   * generic_runnable      — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface  — dual-view Build (rendering_density.md §1)
  //   * monitor_surface       — if eligible per surface_contract.md §3.4
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata (mirror backend ToolCard)
  displayName: '<Title Case>',
  category: '<category>',
  oneLineSummary:
    '<PM-facing one-liner; mirrors manifest one_liner>',

  // FM9 — STANDALONE pattern (methodology_exposure.md §5)
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  // ``build`` is kept === buildExtended for the legacy
  // VirtualPrimitiveCanvas dispatcher.  This alias is a transitional
  // compat trick documented in the pilot tools; do NOT remove it
  // until the dispatcher is migrated.
  surfaces: {
    build:         BuildExtended,
    buildExtended: BuildExtended,
    buildCompact:  BuildCompact,
  },

  // FM5c — Monitor catalog widget (if monitor_surface claimed)
  monitorWidgets: [
    {
      id:           '<widget_id>',
      label:        '<short label>',
      description:  '<one-line widget description>',
      category:     'data',
      defaultSize:  'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        { kind: 'select', name: '<param>', label: '...', defaultValue: '...', options: <SHARED_OPTIONS> },
        // ...
      ],
      component: <Widget>,
    },
  ],

  // FM5 — defaultParams mirror Stage 1A exposure decisions
  defaultParams: {
    <instrument_field>: '<default>',
    tenor: '<default>',
    lookback_days: '365',
    field_name: '<default_from_YAML>',
  },
};
```

**The `surfaces.build = BuildExtended` alias** — the legacy `VirtualPrimitiveCanvas` dispatcher still keys off `surfaces.build`. Keeping it === `buildExtended` makes the dispatcher work without per-tool code changes. [`../../03_standards/rendering_density.md §5.2`](../../03_standards/rendering_density.md) states "new modules MUST NOT populate `surfaces.build`"; the pilot tools document the alias as a transitional compat trick until the dispatcher is migrated. Until then, follow the pilot — set `build === buildExtended` and document it in the same one-line comment.

### 4C. Surface tier rules

| Tier | When to claim | Required artifact |
|---|---|---|
| `generic_runnable` | Backend ships in `_PRIMITIVE_SPECS`. **Default for every standardised primitive.** | None per-module (auto-derived). |
| `custom_build_surface` | The module ships its own Build surface (always true under the dual-view mandate). | Both `surfaces/BuildExtended.tsx` AND `surfaces/BuildCompact.tsx` per [`../../03_standards/rendering_density.md`](../../03_standards/rendering_density.md). |
| `monitor_surface` | The desk reads this primitive at a glance every day per [`../surface_contract.md §3.4`](../surface_contract.md). | `surfaces/monitor/<Widget>.tsx` + `MODULE.monitorWidgets[]` entry. |
| `ask_surface` | The generic `AssistantResearchCard` cannot frame this primitive's result usefully. | `surfaces/AskCard.tsx`. |
| `custom_preview_widget` | The persisted-artifact preview needs per-tool layout. | `surfaces/PreviewWidget.tsx`. |

**Tier-set parsimony** ([FM4](../frontend_module/README.md)) applies to everything except the dual-view Build mandate, which is a structural obligation per [`../../03_standards/rendering_density.md §1.2`](../../03_standards/rendering_density.md).

### 4D. `THESIS.md` — the five-question template

Copy [`../frontend_module/thesis_template.md`](../frontend_module/thesis_template.md) verbatim and answer all five questions:

1. **What surfaces does this module ship?** — one bullet per claimed tier.
2. **What does the user read off each surface?** — one paragraph per surface naming the specific decisions a PM makes.
3. **Why these surfaces and not others?** — explicit comparison to the generic alternative for each claimed surface.
4. **What would change the design?** — concrete shifts in user need or backend output that would force a re-write.
5. **Which backend doctrine does this module operationalise?** — cite by ID (P-numbers, PR-numbers, FM-numbers, ADRs).

Examples + anti-patterns live in [`../frontend_module/thesis_template.md`](../frontend_module/thesis_template.md).

**The dual-view contract requires THESIS Q1 / Q2 / Q3 to address BOTH the extended and compact view explicitly** per [`../../03_standards/rendering_density.md §11`](../../03_standards/rendering_density.md). Pilot reference: [`UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/THESIS.md`](../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/THESIS.md).

### 4E. `__tests__/module.spec.ts` — round-trip + dual-view contract

Per [FM11](../frontend_module/README.md) and [`../../03_standards/rendering_density.md §11`](../../03_standards/rendering_density.md). Mirror the pilot at [`UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/__tests__/module.spec.ts`](../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/__tests__/module.spec.ts):

```ts
import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = '<verb>_<tool_slug>_tool';

check('module satisfies the standard invariants', async () => {
  await assertStandardModuleInvariants(MODULE, {
    folderName: FOLDER,
    moduleFolderPath: `${cwd()}/src/modules/primitives/${FOLDER}`,
  });
});

// rendering_density.md §11 — dual-view contract
check('claims custom_build_surface tier', () => { ... });
check('surfaces.buildExtended is populated', () => { ... });
check('surfaces.buildCompact is populated', () => { ... });
check('typedView is null (standalone-module pattern)', () => { ... });
check('mockups folder exists alongside the module', async () => { ... });
```

The shared helper `assertStandardModuleInvariants` covers [FM11](../frontend_module/README.md) invariants 1–8. The dual-view + mockups checks are inline per the pilot pattern.

### Stage 4 gate

- ☑ `module.ts` declares the tier set + `surfaces.{build, buildExtended, buildCompact}` + `monitorWidgets[]` (if `monitor_surface` claimed) + `typedView: null`.
- ☑ `THESIS.md` answers all five questions; Q1 enumerates the dual-view; Q2 describes EACH surface; Q3 justifies the compact view's curated metrics.
- ☑ Loader entry in `src/modules/index.ts` (alphabetical position).
- ☑ `__tests__/module.spec.ts` runs `assertStandardModuleInvariants` + asserts the dual-view contract.
- ☑ `npm run test:modules` clean.

---

## Stage 5 — Frontend bridge endpoint

The standalone-bridge contract per [`../../03_standards/methodology_exposure.md §5`](../../03_standards/methodology_exposure.md) requires the frontend module to consume a **per-tool typed-detail HTTP endpoint** — NOT the generic `/api/v1/tools/{name}/run` route. Both compact and extended Build views consume the SAME endpoint; the compact view just renders less.

### 5A. The typed-detail route

Path: `api/routes/rates/detail.py` (or a new file under `api/routes/rates/detail/` if the route file grows). Mount at `/api/v1/rates/detail/<tool_kind>` where `<tool_kind>` is a short slug (e.g. `real_yield`, `breakeven`, `zcis`) — **NOT** the full `tool.name`.

```python
@router.get("/detail/<tool_kind>", response_model=<Tool>Output)
def <tool_kind>_detail(
    <instrument_field>: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str | None = None,
    z_score_window_days: int | None = None,
    z_score_min_periods: int | None = None,
    z_score_ddof: int | None = None,
    engine: Engine = Depends(get_engine),
) -> dict:
    """Standalone typed-detail endpoint for <tool_slug>."""
    params = <Tool>Input(
        <instrument_field>=<instrument_field>,
        tenor=tenor,
        lookback_days=lookback_days,
        field_name=field_name,
        z_score_window_days=z_score_window_days,
        z_score_min_periods=z_score_min_periods,
        z_score_ddof=z_score_ddof,
    )
    config = load_tool_config(CONFIG_PATH)
    return calculate_<tool_slug>(engine=engine, params=params, config=config)
```

The route's signature mirrors the Pydantic Input field-for-field. The `response_model=<Tool>Output` annotation enforces the wire contract.

### 5B. Frontend service helper + type mirror

Add the service helper in `src/services/ratesApi.ts`:

```ts
export type <ToolKind>DetailParams = {
  <instrument_field>: string;
  tenor: string;
  lookback_days?: number;
  field_name?: string;
  z_score_window_days?: number;
  z_score_min_periods?: number;
  z_score_ddof?: number;
};

export async function fetchDetail<ToolKind>(
  params: <ToolKind>DetailParams,
): Promise<<Tool>Output> {
  const qs = new URLSearchParams({ ... });
  const res = await fetch(`/api/v1/rates/detail/<tool_kind>?${qs}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

Add the TypeScript type mirror in `src/types/rates.ts` matching the Pydantic Output (one TS type per Pydantic class — `<Tool>CurrentMetrics`, `<Tool>TimeSeriesRow`, `<Tool>Output`).

### Stage 5 gate

- ☑ Route mounted at `/api/v1/rates/detail/<tool_kind>` with `response_model=<Tool>Output`.
- ☑ Route signature mirrors the Pydantic Input (incl. all `expose: true` overrides).
- ☑ Service helper `fetchDetail<ToolKind>` added to `src/services/ratesApi.ts`.
- ☑ TypeScript type mirror added to `src/types/rates.ts`.
- ⊘ Integration test in `tests/api/routes/rates/detail/test_<tool_kind>.py` — deferred when `fastapi` is not installed in the working env (record as ⊘ with reason).

---

## Stage 6 — Frontend surfaces

The dual Build views + Monitor widget + shared helper + mockups. Together with Stage 4 these complete the module folder.

```
src/modules/primitives/<verb>_<tool_slug>_tool/surfaces/
├── BuildExtended.tsx                          # 6A
├── BuildCompact.tsx                           # 6A
├── <tool_slug>Shared.ts                       # 6B
└── monitor/
    └── <Widget>.tsx                           # 6C
```

Plus mockups (mandatory per the pilot):

```
src/modules/primitives/<verb>_<tool_slug>_tool/mockups/
├── Compact.png
└── Extended.png
```

### 6A. `BuildExtended.tsx` + `BuildCompact.tsx` — the dual-view mandate

Both files REQUIRED per [`../../03_standards/rendering_density.md §1`](../../03_standards/rendering_density.md). The frontend dispatcher mounts:

- `buildExtended` for single-tool queries (full canvas).
- `buildCompact` as a node body inside multi-tool DAG visualizations.

**`BuildExtended.tsx` contract** ([`../../03_standards/rendering_density.md §2.1`](../../03_standards/rendering_density.md)):

- Full canvas with controls strip (every Pydantic Input field with `exposure: true`) + output canvas + methodology disclosure + provenance footer.
- Calls `fetchDetail<ToolKind>` from `ratesApi.ts`.
- Size envelope: full BuildShell content area (~1024×720+).
- Receives `BuildExtendedProps`: `{ toolName, params, decoded, askHandoff? }`.

**`BuildCompact.tsx` contract** ([`../../03_standards/rendering_density.md §2.2`](../../03_standards/rendering_density.md)):

- Grid card with tool identity chip + headline 3 KPIs + sparkline + methodology disclosure (compact form) + expand affordance + tone cues.
- Does NOT carry the controls strip — editing happens via the expand path.
- Does NOT mount its own modal — calls the `onExpand` prop; shared infrastructure handles modal mounting.
- Size envelope: target ~400×280px at `size='small'`, up to ~600×420px at `size='medium'`.
- Receives `BuildCompactProps`: `{ toolName, params, size, onExpand, callMeta? }`.

Both views fetch from the SAME typed-detail endpoint; the compact view just renders less of the payload.

The detailed required-content list is in [`../../03_standards/rendering_density.md §§2, 6`](../../03_standards/rendering_density.md); the pilot's design rationale lives in [`THESIS.md` Q2 / Q3](../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/THESIS.md).

### 6B. `<tool_slug>Shared.ts` — cross-surface helper

Co-locate cross-surface constants / hooks here so BuildExtended + BuildCompact + Monitor cannot drift. Typical contents:

- Param-option enumerations (e.g. `BREAKEVEN_PAIR_OPTIONS`) consumed by all three surfaces.
- The shared data hook (e.g. `useBreakevenData(params)`) that calls `fetchDetail<ToolKind>` and returns shape-normalised data.
- KPI / descriptor builders that produce a single source of derived display strings.
- Caveat strings + tone-cue lookups.

This shared file is the **single source of truth** for cross-surface rendering — if you find yourself copying a literal between BuildExtended and BuildCompact, move it here.

### 6C. `monitor/<Widget>.tsx` — Monitor tile (if `monitor_surface` claimed)

Monitor widgets are inherently compact per [`../../03_standards/rendering_density.md §8`](../../03_standards/rendering_density.md). The widget:

- Receives `MonitorWidgetProps` (from `src/modules/types.ts`).
- Calls `fetchDetail<ToolKind>` with the parameterised inputs from `MODULE.monitorWidgets[].paramFields`.
- Renders a desk-glanceable tile (typically a current value + period change + percentile / z-score band).

### 6D. `mockups/Compact.png` + `mockups/Extended.png` — the mockup-first workflow

Commit visual reference mockups alongside the module per the pilot pattern. Both files are required (`assertStandardModuleInvariants` checks for them). They serve as:

- The design source-of-truth that THESIS Q2 / Q3 reference.
- Visual-fidelity regression check for future maintainers.

The mockups are created BEFORE implementation in the pilot workflow.

### 6E. Cross-module shared registries (edit, not create)

| Shared registry | When to edit |
|---|---|
| `UI/.../src/components/shared/build/lib/countryCaveats.ts` | If the tool's UI surfaces country-specific caveats (UK RPI→CPIH transition, Canadian RRB legacy, etc.). |
| `UI/.../src/lib/monitorParamOptions.ts` | If the tool's Monitor widget exposes a new param option type. |
| `UI/.../src/modules/__test-utils.ts` | Only if introducing a new surface key beyond the closed family. **For dual-view modules, no edit needed — the existing map covers `buildExtended` / `buildCompact`.** |
| `UI/.../src/lib/__tests__/loaderPresence.test.ts` | No edit needed — the test iterates the registry and auto-validates new modules. |

### Stage 6 gate

- ☑ `surfaces/BuildExtended.tsx` (full canvas) populated.
- ☑ `surfaces/BuildCompact.tsx` (grid card) populated; calls `onExpand` not own modal; no controls strip.
- ☑ `surfaces/<tool_slug>Shared.ts` co-locates cross-surface constants/hooks.
- ☑ `surfaces/monitor/<Widget>.tsx` populated (if `monitor_surface` claimed).
- ☑ `mockups/Compact.png` + `mockups/Extended.png` committed.
- ☑ `npm run test:modules` clean (round-trip + dual-view contract + mockups check).
- ☑ `npm run test:build` clean.
- ☑ `npm run typecheck` — no new errors.
- ⏸ Manual smoke (Library "Open in Build" → extended; multi-tool DAG → compact + expand-to-modal; Monitor add-widget) — tracked.

---

## Stage 7 — Mirrors + contracts

The visible state of the tool now needs to be **mirrored** into the cross-tool registries + the per-tool README + the knowledge graph.

### 7A. `02_components/surface_contract.md` updates

Three rows touch every primitive:

- **§4** — Build / Monitor status row for this tool (mark current axis statuses).
- **§10** — the cross-tool lifecycle roll-up (mirror Stage 1–7 ☑ state; Stage 8 axis stays `in-progress` until source-material verification closes).
- **§11** — append a changelog entry for the PR.

Drift between the per-tool `LIFECYCLE_CHECKLIST.md` and the §10 roll-up is a **PR-gate violation** ([`../../03_standards/lifecycle_checklist_template.md §7`](../../03_standards/lifecycle_checklist_template.md)).

### 7B. Per-tool `README.md` — the human-readable consolidation

Per [`../../03_standards/tool_lifecycle.md §4`](../../03_standards/tool_lifecycle.md). The per-tool README is **NOT** the source of truth; it mirrors the DB + config + schemas.

Pattern (mirror the pilot at [`rates_agent/inflation_indexed_bonds/tools/breakeven_inflation_simple/README.md`](../../../rates_agent/inflation_indexed_bonds/tools/breakeven_inflation_simple/README.md)):

```markdown
# <verb>_<tool_slug>_tool

> <one-line summary; same as manifest one_liner>

**Phase-1 Stage-<X> tool** — link to the parity target and the standards consumed:
[methodology_exposure.md], [rendering_density.md], [lifecycle_checklist_template.md].

## For desk users
### What this tool tells you
<plain-English; what it tells you, how to read the output>

### When to use it
<concrete macro questions; cross-tool pairings>

### How to read the output
<units; sign conventions; what extreme values indicate>

### Known limitations
<QUOTED VERBATIM from DB tool_metadata.known_limitations, with a
 source-link to the migration file.>

## For developers
### Theoretical reference
<QUOTED VERBATIM from DB tool_metadata.theoretical_reference.>
**Source-material verification status:** ⏸ pending (Stage 8) | ☑ verified by <name> on <date>.

### Methodology
<link to config.yaml; per-convention exposure: status table>

### Input contract
<Pydantic field summary table>

### Output contract
<Pydantic field summary + DB output_field_units mirror>

### Testing
<links to the test triplet + parity fixture + SQL validation>

### Frontend surfaces
<links to module folder, typed-detail endpoint, service helper, type
 mirror, surface_contract.md §10 row>

## Mockup-first design workflow
<note that mockups/Compact.png + Extended.png live alongside the module>

## Known data-quality caveats
<any frontend defensive filters; pointer to TD register>
```

### 7C. Manifest `validation_status` update

Set the manifest `validation_status` to:

```yaml
validation_status: "Stages 1–7 implemented; Stage 3 DB apply + source-material verification pending"
```

Flip to a verified-state string when Stage 8 closes.

### 7D. `graphify update .`

Per [`../../../CLAUDE.md`](../../../CLAUDE.md), run `graphify update .` after the code changes land to refresh the knowledge-graph. AST-only, no API cost.

### Stage 7 gate

- ☑ `surface_contract.md §4` row updated.
- ☑ `surface_contract.md §10` row mirrors current per-tool checklist state.
- ☑ `surface_contract.md §11` changelog appended.
- ☑ Per-tool `README.md` created per [`../../03_standards/tool_lifecycle.md §4`](../../03_standards/tool_lifecycle.md).
- ☑ Manifest `validation_status` updated.
- ☑ `graphify update .` run.

---

## Stage 8 — Closeout sign-off

Stage 8 is the only stage that can stay open across multiple PRs — it waits on **human source-material verification** of the Stage-3 curated `theoretical_reference`.

A tool MAY ship to production with Stage 8 `⏸ pending` per [`../../03_standards/lifecycle_checklist_template.md §5`](../../03_standards/lifecycle_checklist_template.md). Stages 1–7 close the user-functional contract; Stage 8 closes the institutional-defensibility contract.

### 8A. Source-material verification

A human verifier (typically the PM or domain expert) reads the curated `theoretical_reference` against the actual source:

```markdown
- ⏸ → ☑ **Source-material verification**
  - Verifier: <name>
  - Date: <YYYY-MM-DD>
  - Source: <full citation>
  - Notes: <any caveats or follow-ups>
```

### 8B. Apply the DB UPDATE

```sql
UPDATE macro_data.tool_metadata
SET source_material_verified = jsonb_build_object(
        'verifier', '<name>',
        'date',     '<YYYY-MM-DD>',
        'source',   '<full citation>'
    ),
    updated_at = NOW()
WHERE tool_name = '<verb>_<tool_slug>_tool';
```

### 8C. Propagate the verified state

- Flip the per-tool `LIFECYCLE_CHECKLIST.md` Stage 8 `⏸` → `☑`.
- Update the manifest `validation_status` to the verified-state string.
- Update the per-tool `README.md` "Theoretical reference" section with the sign-off note.
- Flip `surface_contract.md §10` axis-3 status from `in-progress` → `shipped`.

### Stage 8 gate

- ☑ `source_material_verified` JSONB populated in DB.
- ☑ Per-tool `LIFECYCLE_CHECKLIST.md` Stage 8 ☑.
- ☑ Manifest `validation_status` verified-state.
- ☑ Per-tool README sign-off note added.
- ☑ `surface_contract.md §10` axis-3 `shipped`.

---

## Final checklist

Mirrors the 8-stage gates. A reviewer signs off when each item is met. Cite by ID per [AC2](../../00_thesis/02_ai_agent_development_contract.md).

### Stage 0 — Pre-flight (PR description)
- [ ] PR1 — concept ownership; tool.name is concept-named.
- [ ] PR2 — one cohesive concept.
- [ ] PR3 — domain residence by conventional ownership.
- [ ] PR4 — parsimony argument written; ≥1 of five criteria defensible.
- [ ] PR5 — genuinely new desk concept.
- [ ] PR6 — every required metadata field present; **no proxies**.
- [ ] PR8 — central methodology surface named.

### Stage 1 — Backend specification
- [ ] PR7 — every methodology default in `config.yaml`; no module-level constants in `compute.py`.
- [ ] [methodology_exposure §3](../../03_standards/methodology_exposure.md) — every convention has an `exposure:` block (Criterion A/B-justified, dated, PR-referenced).
- [ ] PR8 — `<Tool>Input` carries instrument selectors + a small cohesive central methodology surface; nothing else.
- [ ] PR10 — `methodology_label` threaded from YAML, not hardcoded.
- [ ] PR11 — unbuilt methods raise `NotImplementedError`; controlled error envelope for recoverable failures.
- [ ] PR12 — every `Convention.source` is a registered tag.
- [ ] PR13 — `python -m shared.config.lint` exits clean.
- [ ] PR14 — methodology-encoded field names guarded by `NotImplementedError`.
- [ ] MCP wrapper uses the integer-sentinel pattern for every `expose: true` kwarg.
- [ ] Manifest `pm_overridable` derived from `expose: true` set.
- [ ] Manifest `one_liner` is PM-facing prose.
- [ ] Sibling tools' `available_tools` updated.
- [ ] Per-tool `LIFECYCLE_CHECKLIST.md` created with Stage 1A decision table filled.

### Stage 2 — Backend tests
- [ ] PR15 — `tests/fixtures/<tool_slug>_v1/` captured + `test_<tool_slug>_parity.py` green.
- [ ] PR16 — `test_<tool_slug>_compute.py` + `test_<tool_slug>_wiring.py` + `test_<tool_slug>_sql_validation.py` all green.
- [ ] `TestInputOverrides` + `TestExposureBlockContract` present and cover every `expose: true` convention.
- [ ] `TestMethodologyLabelThreading` proves YAML-source, not hardcoded literal.

### Stage 3 — DB metadata
- [ ] Migration `database/migrations/YYYY-MM-DD_phase1_<verb>_<tool_slug>_curated.sql` checked in (idempotent UPDATE + INSERT fallback).
- [ ] `theoretical_reference` + `known_limitations` + `desk_narrative` curated.
- [ ] `output_field_units` JSONB matches the canonical Output shape.
- [ ] DB apply tracked (manual; ⏸ until applied).

### Stage 4 — Frontend module spec
- [ ] FM1 — folder name === MCP `tool.name`.
- [ ] FM3 — tier set declared; `custom_build_surface` claimed.
- [ ] FM7 — `module.ts` is a pure value (no side effects).
- [ ] FM8 — `surfaces.{build, buildExtended, buildCompact}` populated; `surfaces.build === buildExtended` alias documented inline.
- [ ] FM9 — `typedView: null` per the standalone-bridge contract.
- [ ] FM10 — `THESIS.md` answers all five questions; Q1/Q2/Q3 cover BOTH views.
- [ ] FM11 — `__tests__/module.spec.ts` runs `assertStandardModuleInvariants` + asserts the dual-view + mockups.
- [ ] FM12 — loader entry in `src/modules/index.ts` (alphabetical position).
- [ ] `MODULE.defaultParams` mirror Stage 1A exposure decisions.

### Stage 5 — Frontend bridge endpoint
- [ ] [methodology_exposure §5](../../03_standards/methodology_exposure.md) — typed-detail route mounted at `/api/v1/rates/detail/<tool_kind>`.
- [ ] Route signature mirrors Pydantic Input (incl. all `expose: true` overrides).
- [ ] Service helper `fetchDetail<ToolKind>` + frontend type `<Tool>Output` added.

### Stage 6 — Frontend surfaces
- [ ] [rendering_density §1](../../03_standards/rendering_density.md) — both `surfaces/BuildExtended.tsx` AND `surfaces/BuildCompact.tsx` shipped.
- [ ] Compact view: no controls strip; calls `onExpand` not own modal; methodology disclosure compact form; tone cues; size-aware.
- [ ] `surfaces/<tool_slug>Shared.ts` co-locates cross-surface constants/hooks.
- [ ] `surfaces/monitor/<Widget>.tsx` populated (if `monitor_surface` claimed) + `MODULE.monitorWidgets[]` declared.
- [ ] `mockups/Compact.png` + `mockups/Extended.png` committed.
- [ ] `npm run test:modules` + `npm run test:build` + `npm run typecheck` clean.

### Stage 7 — Mirrors + contracts
- [ ] `02_components/surface_contract.md §4` + `§10` + `§11` updated.
- [ ] Per-tool `README.md` created per [`../../03_standards/tool_lifecycle.md §4`](../../03_standards/tool_lifecycle.md).
- [ ] Manifest `validation_status` updated.
- [ ] `graphify update .` run.

### Stage 8 — Closeout sign-off
- [ ] Source-material verification by named human.
- [ ] DB `source_material_verified` JSONB populated.
- [ ] Per-tool `LIFECYCLE_CHECKLIST.md` Stage 8 ☑.
- [ ] Manifest `validation_status` flipped to verified-state.
- [ ] `surface_contract.md §10` axis-3 → `shipped`.

### Commit + PR hygiene
- [ ] **AC2** — every rule citation is by ID (`PR8`, `FM10`, `methodology_exposure.md §5`), never paraphrased.
- [ ] **AC6** — commit trailer: `Operationalises: P3, P5; PR1, PR4, PR7, PR8, PR16; FM1, FM3, FM7, FM8, FM10, FM11; AC2, AC5, AC6.`
- [ ] **AC5** — full self-check run; nothing skipped silently.

---

## Reading map (the docs you needed at each stage)

| Stage | Required reading |
|---|---|
| 0 | [`README.md`](README.md) PR1–PR6 + [`runbook.md`](runbook.md) Pre-flight + [`../../00_thesis/02_ai_agent_development_contract.md`](../../00_thesis/02_ai_agent_development_contract.md) AC1–AC8 |
| 1 | [`README.md`](README.md) PR7–PR16 + [`../../03_standards/methodology_exposure.md`](../../03_standards/methodology_exposure.md) + [`../../03_standards/methodology_disclosure.md`](../../03_standards/methodology_disclosure.md) + [`../../03_standards/typed_boundary_discipline.md`](../../03_standards/typed_boundary_discipline.md) + [`../../03_standards/error_handling.md`](../../03_standards/error_handling.md) + [`../../03_standards/lifecycle_checklist_template.md`](../../03_standards/lifecycle_checklist_template.md) (copy §10 into the tool folder) |
| 2 | [`../../03_standards/test_patterns.md`](../../03_standards/test_patterns.md) + [`README.md`](README.md) PR15–PR16 |
| 3 | [`../../03_standards/tool_lifecycle.md`](../../03_standards/tool_lifecycle.md) §2 axes 1/5/7 + [`../../05_decisions/0015-tool-metadata-db-table.md`](../../05_decisions/0015-tool-metadata-db-table.md) |
| 4 | [`../frontend_module/README.md`](../frontend_module/README.md) FM1–FM12 + [`../frontend_module/runbook.md`](../frontend_module/runbook.md) + [`../frontend_module/thesis_template.md`](../frontend_module/thesis_template.md) + [`../frontend_module/tiers.md`](../frontend_module/tiers.md) + [`../../03_standards/rendering_density.md`](../../03_standards/rendering_density.md) §5 |
| 5 | [`../../03_standards/methodology_exposure.md`](../../03_standards/methodology_exposure.md) §5 |
| 6 | [`../../03_standards/rendering_density.md`](../../03_standards/rendering_density.md) §§2, 3, 6 |
| 7 | [`../../03_standards/tool_lifecycle.md`](../../03_standards/tool_lifecycle.md) §4 + [`../surface_contract.md`](../surface_contract.md) §§4, 10, 11 |
| 8 | [`../../03_standards/lifecycle_checklist_template.md`](../../03_standards/lifecycle_checklist_template.md) §5 |

---

## What this guide does NOT cover

Out of scope by design:

- **Operators** ([L3 in the architecture](../../01_architecture/00_internal_architecture.md)) — different contract; a parallel `operator/BUILD_GUIDE.md` will land when operator standardisation begins.
- **Workflow templates** ([L5](../../01_architecture/00_internal_architecture.md)) — different contract; a parallel `workflow_template/BUILD_GUIDE.md` will land when workflow standardisation begins.
- **Orchestration** (the LLM router + supervisor + domain-agent tree) — actively evolving; not yet under a standardised contract.
- **Playbooks** ([`../playbook/runbook.md`](../playbook/runbook.md)) — add-tickers / extend-universe work is a playbook edit, NOT a primitive PR. Per [PR5](README.md) a new universe member is not a new primitive.

---

## Open questions

1. **The `surfaces.build = BuildExtended` legacy alias.** [`../../03_standards/rendering_density.md §5.2`](../../03_standards/rendering_density.md) states new modules MUST NOT populate `surfaces.build`. The pilot tools document the alias as a transitional compat trick until the `VirtualPrimitiveCanvas` dispatcher is migrated. Resolution: either update the dispatcher to key off `buildExtended` (and remove the alias from all modules), or update the rendering-density standard to acknowledge the alias as an interim shape. Tracked.

2. **Phase-1 carve-outs for missing test environments.** Multiple Stage gates note `⊘` items when `fastapi` / `mcp` packages aren't installed in the working env (parity with the pilot). These ⊘ carve-outs are acceptable per the per-tool checklist standard but should converge as the env matures.

3. **Mockup-first vs implementation-first.** The pilot ships mockups committed alongside the module folder. Should mockups be a Stage-0 deliverable instead of Stage-6? Currently captured under "mockup-first design workflow" in the pilot's README; not promoted to a contract row. Revisit when N ≥ 5 primitives ship under this guide.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-29 | Initial BUILD_GUIDE. Single front-door manual covering all 8 lifecycle stages with copy-paste skeletons + cross-references to the 14+ load-bearing docs the guide subsumes. Mirrors the actual pilot artifacts shipped under `calculate_breakeven_inflation_simple_tool` + `get_real_yield_level_tool` (Phase-1 pilot pair). |
