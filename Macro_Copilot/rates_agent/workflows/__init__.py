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
from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
    CONFIG_PATH as REAL_YIELD_LEVEL_CONFIG_PATH,
    RealYieldLevelInput,
    RealYieldLevelOutput,
    get_real_yield_level,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
    CONFIG_PATH as BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH,
    BreakevenInflationSimpleInput,
    BreakevenInflationSimpleOutput,
    calculate_breakeven_inflation_simple,
)
from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple import (
    CONFIG_PATH as FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH,
    ForwardBreakevenSimpleInput,
    ForwardBreakevenSimpleOutput,
    calculate_forward_breakeven_simple,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread import (
    CONFIG_PATH as BREAKEVEN_CURVE_SPREAD_CONFIG_PATH,
    BreakevenCurveSpreadInput,
    BreakevenCurveSpreadOutput,
    calculate_breakeven_curve_spread,
)
from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
    CONFIG_PATH as CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH,
    CrossCountryBreakevenSpreadSimpleInput,
    CrossCountryBreakevenSpreadSimpleOutput,
    calculate_cross_country_breakeven_spread_simple,
)

# Analytical model primitives — registered so the workspace UI's
# model-playground can surface + run them via the same /tools catalogue
# + /tools/{name}/run REST surface used for the desk primitives.
# Registration is purely additive — no behaviour change to any of
# these primitives.
from rates_agent.sovereign_bonds.tools.rolling_regression import (
    CONFIG_PATH as ROLLING_REGRESSION_CONFIG_PATH,
    RollingRegressionInput,
    RollingRegressionOutput,
    calculate_rolling_regression,
)
from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
    CONFIG_PATH as PCA_YIELD_CURVE_CONFIG_PATH,
    PcaYieldCurveInput,
    PcaYieldCurveOutput,
    calculate_pca_yield_curve,
)
from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca import (
    CONFIG_PATH as YIELD_CHANGE_ATTRIBUTION_PCA_CONFIG_PATH,
    YieldChangeAttributionPcaInput,
    YieldChangeAttributionPcaOutput,
    calculate_yield_change_attribution_pca,
)
from rates_agent.sovereign_bonds.tools.half_life import (
    CONFIG_PATH as HALF_LIFE_CONFIG_PATH,
    HalfLifeInput,
    HalfLifeOutput,
    calculate_half_life,
)
from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
    CONFIG_PATH as BETA_ADJUSTED_SPREAD_CONFIG_PATH,
    BetaAdjustedSpreadInput,
    BetaAdjustedSpreadOutput,
    calculate_beta_adjusted_spread,
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
            # NEW: rolling z-score of the day-over-day CHANGE in the
            # swap spread.  Canonical signal for the event-study
            # proof-Q1 binding ("spread widened by more than Nσ in a
            # single day").  See SwapSpreadOutput.time_series_change_zscore
            # docstring + the swap_spread compute change_zscore builder.
            "time_series_change_zscore": "z_score",
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

    # ---- Inflation-indexed-bonds (linker) domain ----
    "get_real_yield_level_tool": PrimitiveSpec(
        tool_name="get_real_yield_level_tool",
        callable=get_real_yield_level,
        input_class=RealYieldLevelInput,
        output_class=RealYieldLevelOutput,
        config_path=REAL_YIELD_LEVEL_CONFIG_PATH,
        output_field_units={
            # Linker real yields are quoted in percent — same unit as
            # nominal sovereign yields, but the underlying series is
            # the linker real-yield-to-maturity.  Wire field name on
            # the snapshot is ``real_yield_pct``; series_name carries
            # the ``_real_yield`` suffix (set in the per-tool config).
            "time_series": "percent",
        },
    ),
    "calculate_breakeven_inflation_simple_tool": PrimitiveSpec(
        tool_name="calculate_breakeven_inflation_simple_tool",
        callable=calculate_breakeven_inflation_simple,
        input_class=BreakevenInflationSimpleInput,
        output_class=BreakevenInflationSimpleOutput,
        config_path=BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen breakeven row list — frontend
            # consumes ``breakeven_bps`` per row, so the unit is BPS.
            "time_series": "bps",
            # Canonical TimeSeries: breakeven history in BPS, rolling
            # z-score in Z_SCORE units.  Operator-layer unit-compat
            # checks rely on these declarations.
            "time_series_breakeven": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_forward_breakeven_simple_tool": PrimitiveSpec(
        tool_name="calculate_forward_breakeven_simple_tool",
        callable=calculate_forward_breakeven_simple,
        input_class=ForwardBreakevenSimpleInput,
        output_class=ForwardBreakevenSimpleOutput,
        config_path=FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen forward-breakeven row list —
            # frontend consumes ``forward_breakeven_bps`` per row,
            # so the unit is BPS.
            "time_series": "bps",
            # Canonical TimeSeries: forward breakeven history in
            # BPS, rolling z-score in Z_SCORE units.  Operator-layer
            # unit-compat checks rely on these declarations.
            "time_series_forward": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_breakeven_curve_spread_tool": PrimitiveSpec(
        tool_name="calculate_breakeven_curve_spread_tool",
        callable=calculate_breakeven_curve_spread,
        input_class=BreakevenCurveSpreadInput,
        output_class=BreakevenCurveSpreadOutput,
        config_path=BREAKEVEN_CURVE_SPREAD_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen breakeven-curve-spread row list —
            # frontend consumes ``spread_bps`` per row, so the
            # unit is BPS.
            "time_series": "bps",
            # Canonical TimeSeries: breakeven curve spread history
            # in BPS, rolling z-score in Z_SCORE units.  Operator-
            # layer unit-compat checks rely on these declarations.
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_cross_country_breakeven_spread_simple_tool": PrimitiveSpec(
        tool_name="calculate_cross_country_breakeven_spread_simple_tool",
        callable=calculate_cross_country_breakeven_spread_simple,
        input_class=CrossCountryBreakevenSpreadSimpleInput,
        output_class=CrossCountryBreakevenSpreadSimpleOutput,
        config_path=CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen cross-country breakeven-spread row
            # list — frontend consumes ``spread_bps`` per row, so
            # the unit is BPS.
            "time_series": "bps",
            # Canonical TimeSeries: cross-country breakeven spread
            # history in BPS, rolling z-score in Z_SCORE units.
            # Operator-layer unit-compat checks rely on these
            # declarations.
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        },
    ),

    # ---- Analytical models (workspace model-playground surface) ----
    #
    # These are the same per-tool-folder primitives the v6 sprint
    # built; registration here exposes them through the
    # rates_primitive_resolver so the /tools catalogue endpoint and
    # the /tools/{name}/run endpoint surface them uniformly with the
    # desk primitives above.  Output unit declarations follow each
    # primitive's schemas.py field documentation.
    "calculate_rolling_regression_tool": PrimitiveSpec(
        tool_name="calculate_rolling_regression_tool",
        callable=calculate_rolling_regression,
        input_class=RollingRegressionInput,
        output_class=RollingRegressionOutput,
        config_path=ROLLING_REGRESSION_CONFIG_PATH,
        output_field_units={
            # betas / r-squared are unitless ratios; alpha + residual
            # live in yield-percent space.
            "time_series_betas": "ratio",
            "time_series_alpha": "percent",
            "time_series_residual": "percent",
            "time_series_r_squared": "ratio",
            "time_series_condition_flag": "count",
        },
    ),
    "calculate_pca_yield_curve_tool": PrimitiveSpec(
        tool_name="calculate_pca_yield_curve_tool",
        callable=calculate_pca_yield_curve,
        input_class=PcaYieldCurveInput,
        output_class=PcaYieldCurveOutput,
        config_path=PCA_YIELD_CURVE_CONFIG_PATH,
        output_field_units={
            # Factor scores are unitless eigen-coordinates.
            "time_series_factors": "factor_level",
        },
    ),
    "calculate_yield_change_attribution_pca_tool": PrimitiveSpec(
        tool_name="calculate_yield_change_attribution_pca_tool",
        callable=calculate_yield_change_attribution_pca,
        input_class=YieldChangeAttributionPcaInput,
        output_class=YieldChangeAttributionPcaOutput,
        config_path=YIELD_CHANGE_ATTRIBUTION_PCA_CONFIG_PATH,
        # Pure-snapshot primitive (no time_series_* fields).
        output_field_units={},
    ),
    "calculate_half_life_tool": PrimitiveSpec(
        tool_name="calculate_half_life_tool",
        callable=calculate_half_life,
        input_class=HalfLifeInput,
        output_class=HalfLifeOutput,
        config_path=HALF_LIFE_CONFIG_PATH,
        output_field_units={},
    ),
    "calculate_beta_adjusted_spread_tool": PrimitiveSpec(
        tool_name="calculate_beta_adjusted_spread_tool",
        callable=calculate_beta_adjusted_spread,
        input_class=BetaAdjustedSpreadInput,
        output_class=BetaAdjustedSpreadOutput,
        config_path=BETA_ADJUSTED_SPREAD_CONFIG_PATH,
        output_field_units={
            # Beta is unitless; residual is bps; z-score is z_score.
            "time_series_beta": "ratio",
            "time_series_residual": "bps",
            "time_series_residual_z_score": "z_score",
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
