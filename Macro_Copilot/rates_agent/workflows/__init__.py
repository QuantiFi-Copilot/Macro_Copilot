"""rates_agent.workflows — workflow templates owned by the rates agent.

Per docs/architecture/workflow_architecture.md, workflow templates
live in their owning agent's package (NOT under ``shared/``) so the
substrate stays finance-blind.  Each template's directory contains
its YAML + an ``__init__.py`` that loads + registers the template.

This module also hosts the ``rates_primitive_resolver`` — the
caller-supplied ``PrimitiveResolver`` (per
``shared.workflow.PrimitiveResolver`` protocol) that maps MCP tool
names to ``PrimitiveSpec`` bundles for the rates agent's primitives.
The substrate doesn't import from rates_agent; the substrate
executor accepts ``primitive_resolver=rates_primitive_resolver`` to
dispatch primitive calls.

Adding a new primitive to the resolver
--------------------------------------
1. The primitive must already exist as a per-tool-folder under
   ``rates_agent/<domain>/tools/<tool>/`` and emit canonical
   ``TimeSeries`` payloads.
2. Add a ``PrimitiveSpec`` entry to ``_PRIMITIVE_SPECS`` below.
   Carries the callable, *Input class, *Output class, CONFIG_PATH,
   and ``output_field_units`` (for the substrate's best-effort
   unit-compat checks at validate-time).
3. Templates that consume the new primitive can reference it by
   ``tool_name`` immediately — no template code change required.

Adding a new template
---------------------
1. Create ``rates_agent/workflows/<template_name>/`` with
   ``template.yaml`` + ``__init__.py``.
2. The ``__init__.py`` calls ``load_workflow_template`` to read the
   YAML and ``register_template`` to add it to the substrate's
   process-wide registry.
3. Import the template package wherever the registry needs to be
   populated (e.g. orchestrator startup, eval harnesses).
"""

from __future__ import annotations

from typing import Dict

from rates_agent.ois.tools.cross_market_spread import (
    CONFIG_PATH as OIS_CROSS_MARKET_SPREAD_CONFIG_PATH,
    OISCrossMarketSpreadInput,
    OISCrossMarketSpreadOutput,
    calculate_ois_cross_market_spread,
)
from rates_agent.ois.tools.curve_spread import (
    CONFIG_PATH as OIS_CURVE_SPREAD_CONFIG_PATH,
    OISCurveSpreadInput,
    OISCurveSpreadOutput,
    calculate_ois_curve_spread,
)
from rates_agent.ois.tools.forward_rate import (
    CONFIG_PATH as OIS_FORWARD_RATE_CONFIG_PATH,
    OISForwardRateInput,
    OISForwardRateOutput,
    calculate_ois_forward_rate,
)
from rates_agent.ois.tools.rate_level import (
    CONFIG_PATH as OIS_RATE_LEVEL_CONFIG_PATH,
    OISRateLevelInput,
    OISRateLevelOutput,
    get_ois_rate_level,
)
from rates_agent.ois.tools.swap_spread import (
    CONFIG_PATH as SWAP_SPREAD_CONFIG_PATH,
    SwapSpreadInput,
    SwapSpreadOutput,
    calculate_swap_spread,
)
from rates_agent.sovereign_bonds.tools.curve_spread import (
    CONFIG_PATH as SOV_CURVE_SPREAD_CONFIG_PATH,
    CurveSpreadInput,
    CurveSpreadOutput,
    calculate_curve_spread,
)
from rates_agent.sovereign_bonds.tools.cross_market_spread import (
    CONFIG_PATH as SOV_CROSS_MARKET_SPREAD_CONFIG_PATH,
    CrossMarketSpreadInput,
    CrossMarketSpreadOutput,
    calculate_cross_market_spread,
)
from rates_agent.sovereign_bonds.tools.yield_levels import (
    CONFIG_PATH as YIELD_LEVELS_CONFIG_PATH,
    YieldLevelInput,
    YieldLevelOutput,
    get_yield_levels,
)
from shared.workflow import PrimitiveResolver, PrimitiveSpec


# ============================================================================
# PRIMITIVE REGISTRY
# ============================================================================
#
# Each entry maps an MCP tool_name to its PrimitiveSpec.  Templates
# reference primitives by tool_name; the substrate executor dispatches
# via this resolver.
#
# ``output_field_units`` is the load-bearing declaration for the
# substrate's best-effort validate-time unit-compat checks (Codex
# P2 follow-up shipped in PR #79).  Each entry maps a primitive's
# canonical TimeSeries field name → unit string (matching
# ``TimeSeriesUnits`` enum values).  Sourced from each primitive's
# ``schemas.py`` documentation.

_PRIMITIVE_SPECS: Dict[str, PrimitiveSpec] = {
    # ---- OIS domain ----
    "calculate_ois_curve_spread_tool": PrimitiveSpec(
        tool_name="calculate_ois_curve_spread_tool",
        callable=calculate_ois_curve_spread,
        input_class=OISCurveSpreadInput,
        output_class=OISCurveSpreadOutput,
        config_path=OIS_CURVE_SPREAD_CONFIG_PATH,
        output_field_units={
            "time_series": "bps",
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_ois_cross_market_spread_tool": PrimitiveSpec(
        tool_name="calculate_ois_cross_market_spread_tool",
        callable=calculate_ois_cross_market_spread,
        input_class=OISCrossMarketSpreadInput,
        output_class=OISCrossMarketSpreadOutput,
        config_path=OIS_CROSS_MARKET_SPREAD_CONFIG_PATH,
        output_field_units={
            "time_series": "bps",
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_ois_forward_rate_tool": PrimitiveSpec(
        tool_name="calculate_ois_forward_rate_tool",
        callable=calculate_ois_forward_rate,
        input_class=OISForwardRateInput,
        output_class=OISForwardRateOutput,
        config_path=OIS_FORWARD_RATE_CONFIG_PATH,
        output_field_units={
            "time_series_forward": "percent",
            "time_series_zscore": "z_score",
        },
    ),
    "get_ois_rate_level_tool": PrimitiveSpec(
        tool_name="get_ois_rate_level_tool",
        callable=get_ois_rate_level,
        input_class=OISRateLevelInput,
        output_class=OISRateLevelOutput,
        config_path=OIS_RATE_LEVEL_CONFIG_PATH,
        output_field_units={
            "time_series": "percent",
        },
    ),
    "calculate_swap_spread_tool": PrimitiveSpec(
        tool_name="calculate_swap_spread_tool",
        callable=calculate_swap_spread,
        input_class=SwapSpreadInput,
        output_class=SwapSpreadOutput,
        config_path=SWAP_SPREAD_CONFIG_PATH,
        output_field_units={
            "time_series": "bps",
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    # ---- Sovereign domain ----
    "calculate_curve_spread_tool": PrimitiveSpec(
        tool_name="calculate_curve_spread_tool",
        callable=calculate_curve_spread,
        input_class=CurveSpreadInput,
        output_class=CurveSpreadOutput,
        config_path=SOV_CURVE_SPREAD_CONFIG_PATH,
        output_field_units={
            "time_series": "bps",
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_cross_market_spread_tool": PrimitiveSpec(
        tool_name="calculate_cross_market_spread_tool",
        callable=calculate_cross_market_spread,
        input_class=CrossMarketSpreadInput,
        output_class=CrossMarketSpreadOutput,
        config_path=SOV_CROSS_MARKET_SPREAD_CONFIG_PATH,
        output_field_units={
            "time_series": "bps",
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "get_yield_levels_tool": PrimitiveSpec(
        tool_name="get_yield_levels_tool",
        callable=get_yield_levels,
        input_class=YieldLevelInput,
        output_class=YieldLevelOutput,
        config_path=YIELD_LEVELS_CONFIG_PATH,
        output_field_units={
            "time_series": "percent",
        },
    ),
}


def rates_primitive_resolver(tool_name: str) -> PrimitiveSpec:
    """``PrimitiveResolver`` implementation for the rates agent.

    Pass to ``shared.workflow.execute_workflow`` as
    ``primitive_resolver=rates_primitive_resolver`` to dispatch
    primitive calls in workflows.  Raises ``KeyError`` for unknown
    tool names with the list of known primitives for diagnostic
    clarity (matches the ``PrimitiveResolver`` protocol's contract).
    """
    if tool_name not in _PRIMITIVE_SPECS:
        known = sorted(_PRIMITIVE_SPECS.keys())
        raise KeyError(
            f"rates_primitive_resolver: tool_name={tool_name!r} is not "
            f"registered.  Known: {known}.  Add a PrimitiveSpec entry "
            "to _PRIMITIVE_SPECS in rates_agent/workflows/__init__.py "
            "to register a new primitive."
        )
    return _PRIMITIVE_SPECS[tool_name]


def known_rates_primitives() -> list[str]:
    """Return the sorted list of primitive tool names the rates
    resolver knows.  Used by diagnostic tooling and template
    authors checking what's available."""
    return sorted(_PRIMITIVE_SPECS.keys())


__all__ = [
    "rates_primitive_resolver",
    "known_rates_primitives",
]
