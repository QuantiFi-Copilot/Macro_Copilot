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

from rates_agent.ois.tools.calculate_ois_butterfly import (
    CONFIG_PATH as OIS_BUTTERFLY_CONFIG_PATH,
    OISButterflyInput,
    OISButterflyOutput,
    calculate_ois_butterfly,
)
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
from rates_agent.bond_futures.tools.futures_price_level import (
    CONFIG_PATH as FUTURES_PRICE_LEVEL_CONFIG_PATH,
    FuturesPriceLevelInput,
    FuturesPriceLevelOutput,
    calculate_futures_price_level,
)
from rates_agent.bond_futures.tools.futures_volume_oi import (
    CONFIG_PATH as FUTURES_VOLUME_OI_CONFIG_PATH,
    FuturesVolumeOIInput,
    FuturesVolumeOIOutput,
    calculate_futures_volume_oi,
)
from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
    CONFIG_PATH as SCAN_BOND_FUTURES_EXTREMES_CONFIG_PATH,
    ScanBondFuturesExtremesInput,
    ScanBondFuturesExtremesOutput,
    calculate_scan_bond_futures_extremes,
)
from rates_agent.policy_futures.tools.futures_price_level import (
    CONFIG_PATH as POLICY_FUTURES_PRICE_LEVEL_CONFIG_PATH,
    FuturesPriceLevelInput as PolicyFuturesPriceLevelInput,
    FuturesPriceLevelOutput as PolicyFuturesPriceLevelOutput,
    calculate_futures_price_level as calculate_policy_futures_price_level,
)
from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
    CONFIG_PATH as POLICY_FUTURES_VOLUME_OPEN_INTEREST_SNAPSHOT_CONFIG_PATH,
    VolumeOpenInterestSnapshotInput,
    VolumeOpenInterestSnapshotOutput,
    calculate_volume_open_interest_snapshot,
)
from rates_agent.policy_futures.tools.futures_calendar_spread import (
    CONFIG_PATH as POLICY_FUTURES_CALENDAR_SPREAD_CONFIG_PATH,
    FuturesCalendarSpreadInput,
    FuturesCalendarSpreadOutput,
    calculate_futures_calendar_spread,
)
from rates_agent.policy_futures.tools.futures_butterfly_simple import (
    CONFIG_PATH as POLICY_FUTURES_BUTTERFLY_SIMPLE_CONFIG_PATH,
    FuturesButterflySimpleInput,
    FuturesButterflySimpleOutput,
    calculate_futures_butterfly_simple,
)
from rates_agent.policy_futures.tools.futures_cross_market_spread import (
    CONFIG_PATH as POLICY_FUTURES_CROSS_MARKET_SPREAD_CONFIG_PATH,
    FuturesCrossMarketSpreadInput,
    FuturesCrossMarketSpreadOutput,
    calculate_futures_cross_market_spread,
)
from rates_agent.policy_futures.tools.futures_strip_snapshot import (
    CONFIG_PATH as POLICY_FUTURES_STRIP_SNAPSHOT_CONFIG_PATH,
    FuturesStripSnapshotInput,
    FuturesStripSnapshotOutput,
    calculate_futures_strip_snapshot,
)
from rates_agent.policy_futures.tools.futures_pack_average_simple import (
    CONFIG_PATH as POLICY_FUTURES_PACK_AVERAGE_SIMPLE_CONFIG_PATH,
    FuturesPackAverageSimpleInput,
    FuturesPackAverageSimpleOutput,
    calculate_futures_pack_average_simple,
)
from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (
    CONFIG_PATH as POLICY_FUTURES_SCAN_EXTREMES_CONFIG_PATH,
    ScanPolicyFuturesExtremesInput,
    ScanPolicyFuturesExtremesOutput,
    calculate_scan_policy_futures_extremes,
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
from rates_agent.sovereign_bonds.tools.zscore_custom import (
    CONFIG_PATH as ZSCORE_CUSTOM_CONFIG_PATH,
    ZscoreCustomInput,
    ZscoreCustomOutput,
    calculate_zscore_custom,
)

# PR 19 / PR 20 — Panel-emitting primitives + the TIPS-Nominal
# breakeven spread primitive.  Registered here so the workflow
# executor's primitive resolver can dispatch to them; the
# Panel-shaped primitives also declare ``output_artifact_type="Panel"``
# so the executor picks the Panel bridge over the default Series bridge.
from rates_agent.sovereign_bonds.tools.sovereign_yield_panel import (
    CONFIG_PATH as SOV_YIELD_PANEL_CONFIG_PATH,
    SovereignYieldPanelInput,
    SovereignYieldPanelOutput,
    build_sovereign_yield_panel,
)
from rates_agent.sovereign_bonds.tools.breakeven_inflation import (
    CONFIG_PATH as BREAKEVEN_INFLATION_CONFIG_PATH,
    BreakevenInflationInput,
    BreakevenInflationOutput,
    calculate_breakeven_inflation,
)
from rates_agent.ois.tools.financing_rate import (
    CONFIG_PATH as FINANCING_RATE_CONFIG_PATH,
    FinancingRateInput,
    FinancingRateOutput,
    compute_financing_rate,
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
from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
    CONFIG_PATH as BREAKEVEN_BUTTERFLY_CONFIG_PATH,
    BreakevenButterflyInput,
    BreakevenButterflyOutput,
    calculate_breakeven_butterfly,
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
from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly import (
    CONFIG_PATH as REAL_YIELD_BUTTERFLY_CONFIG_PATH,
    RealYieldButterflyInput,
    RealYieldButterflyOutput,
    calculate_real_yield_butterfly,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
    CONFIG_PATH as REAL_YIELD_CURVE_SPREAD_CONFIG_PATH,
    RealYieldCurveSpreadInput,
    RealYieldCurveSpreadOutput,
    calculate_real_yield_curve_spread,
)
from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (
    CONFIG_PATH as CROSS_COUNTRY_REAL_YIELD_SPREAD_SIMPLE_CONFIG_PATH,
    CrossCountryRealYieldSpreadSimpleInput,
    CrossCountryRealYieldSpreadSimpleOutput,
    calculate_cross_country_real_yield_spread_simple,
)
from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (
    CONFIG_PATH as SCAN_INFLATION_LINKERS_EXTREMES_CONFIG_PATH,
    ScanInflationLinkersExtremesInput,
    ScanInflationLinkersExtremesOutput,
    calculate_scan_inflation_linkers_extremes,
)
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
    CONFIG_PATH as INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH,
    InflationSwapRateLevelInput,
    InflationSwapRateLevelOutput,
    calculate_inflation_swap_rate_level,
)
from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread import (
    CONFIG_PATH as INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH,
    InflationSwapCurveSpreadInput,
    InflationSwapCurveSpreadOutput,
    calculate_inflation_swap_curve_spread,
)
from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
    CONFIG_PATH as INFLATION_SWAP_FORWARD_CONFIG_PATH,
    InflationSwapForwardInput,
    InflationSwapForwardOutput,
    calculate_inflation_swap_forward,
)
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
    CONFIG_PATH as CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH,
    CrossMarketInflationSwapSpreadInput,
    CrossMarketInflationSwapSpreadOutput,
    calculate_cross_market_inflation_swap_spread,
)
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
    CONFIG_PATH as SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH,
    SwapBreakevenBasisSimpleInput,
    SwapBreakevenBasisSimpleOutput,
    calculate_swap_breakeven_basis_simple,
)
from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
    CONFIG_PATH as INFLATION_SWAP_BUTTERFLY_CONFIG_PATH,
    InflationSwapButterflyInput,
    InflationSwapButterflyOutput,
    calculate_inflation_swap_butterfly,
)
from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
    CONFIG_PATH as SCAN_INFLATION_SWAPS_EXTREMES_CONFIG_PATH,
    ScanInflationSwapsExtremesInput,
    ScanInflationSwapsExtremesOutput,
    calculate_scan_inflation_swaps_extremes,
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
    "calculate_ois_butterfly_tool": PrimitiveSpec(
        tool_name="calculate_ois_butterfly_tool",
        callable=calculate_ois_butterfly,
        input_class=OISButterflyInput,
        output_class=OISButterflyOutput,
        config_path=OIS_BUTTERFLY_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen butterfly row list — frontend
            # consumes ``butterfly_bps`` per row, so the unit is BPS
            # (mirrors sovereign butterfly / inflation_swap_butterfly /
            # OIS curve_spread BPS-on-the-wire convention for curve-
            # shape views, even though the underlying OIS rates are
            # quoted in PERCENT).
            "time_series": "bps",
            # Canonical TimeSeries: OIS butterfly history in BPS,
            # rolling z-score in Z_SCORE units.  Operator-layer
            # unit-compat checks rely on these declarations.
            "time_series_butterfly": "bps",
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
    # ---- Bond-futures domain (ADR 0011 — V1 monitors only) ----
    # ``output_field_units`` is intentionally empty: this primitive's
    # ``time_series`` is a bespoke list of ``{date, price}`` rows in
    # the contract's native ``quote_units`` (TY1 ``points``, RX1
    # ``% of par value``, ...). The closed-enum ``TimeSeriesUnits``
    # family has no ``PRICE`` member; declaring ``percent`` or ``bps``
    # would silently lie under P8 (closed-family discipline) + P5
    # (honest disclosure). The validator's empty-dict exemption (see
    # shared/workflow/validate.py:368) defers the unit check to the
    # operator's runtime refusal — that is the honest path until a
    # future ADR extends ``TimeSeriesUnits`` with a price member.
    "get_futures_price_level_tool": PrimitiveSpec(
        tool_name="get_futures_price_level_tool",
        callable=calculate_futures_price_level,
        input_class=FuturesPriceLevelInput,
        output_class=FuturesPriceLevelOutput,
        config_path=FUTURES_PRICE_LEVEL_CONFIG_PATH,
        output_field_units={},
    ),
    # ``output_field_units`` is intentionally empty for the same
    # reason as get_futures_price_level_tool: this primitive's
    # ``time_series`` is a bespoke list of ``{date, volume,
    # open_interest}`` rows in CONTRACT COUNTS — not a unit on the
    # closed-enum ``TimeSeriesUnits`` family (no ``CONTRACTS``
    # member; ``COUNT`` is reserved for discrete flag enumerations).
    # Declaring ``percent`` / ``bps`` / ``count`` would silently lie
    # under P8 (closed-family discipline) + P5 (honest disclosure).
    # The validator's empty-dict exemption (see
    # shared/workflow/validate.py:368) defers the unit check to the
    # operator's runtime refusal — that is the honest path until a
    # future ADR extends ``TimeSeriesUnits`` with a contracts member.
    "get_futures_volume_oi_tool": PrimitiveSpec(
        tool_name="get_futures_volume_oi_tool",
        callable=calculate_futures_volume_oi,
        input_class=FuturesVolumeOIInput,
        output_class=FuturesVolumeOIOutput,
        config_path=FUTURES_VOLUME_OI_CONFIG_PATH,
        output_field_units={},
    ),
    # ``output_field_units`` is intentionally empty for the same
    # reason as the two sibling bond_futures monitors: this primitive's
    # ``results`` is a bespoke list of per-row snapshots where each row
    # mixes contract-native price units, contract-count volume + OI,
    # and unit-less z-scores. None of those map onto the closed-enum
    # ``TimeSeriesUnits`` family in V1 (``PRICE`` and ``CONTRACTS``
    # are not members; declaring ``percent`` / ``bps`` / ``count``
    # would silently lie under P8 + P5). The validator's empty-dict
    # exemption (see shared/workflow/validate.py:368) defers the unit
    # check to the operator's runtime refusal — the honest path until
    # a future ADR extends ``TimeSeriesUnits``. Furthermore, the scan
    # is a SNAPSHOT primitive (no canonical TimeSeries on the wire);
    # per-contract history lives on the sibling
    # get_futures_price_level_tool / get_futures_volume_oi_tool.
    "scan_bond_futures_extremes_tool": PrimitiveSpec(
        tool_name="scan_bond_futures_extremes_tool",
        callable=calculate_scan_bond_futures_extremes,
        input_class=ScanBondFuturesExtremesInput,
        output_class=ScanBondFuturesExtremesOutput,
        config_path=SCAN_BOND_FUTURES_EXTREMES_CONFIG_PATH,
        output_field_units={},
    ),

    # ---- Policy-futures domain (ADR 0011 — strip-position-keyed) ----
    # ``output_field_units`` is intentionally empty for the same
    # P8 + P5 reasons the bond_futures monitors use: this primitive's
    # ``time_series`` carries TWO unit spaces per row (raw_price in
    # the contract's quote space ``100 - rate``, AND implied_rate_pct
    # in PERCENT). The closed-enum ``TimeSeriesUnits`` family has no
    # PRICE member; declaring ``percent`` would only cover the
    # implied-rate axis and silently mis-label the raw-price axis.
    # The validator's empty-dict exemption (see
    # shared/workflow/validate.py:368) defers the unit check to the
    # operator's runtime refusal — that is the honest path until a
    # future ADR extends ``TimeSeriesUnits`` with a PRICE member.
    "policy_futures_get_futures_price_level_tool": PrimitiveSpec(
        tool_name="policy_futures_get_futures_price_level_tool",
        callable=calculate_policy_futures_price_level,
        input_class=PolicyFuturesPriceLevelInput,
        output_class=PolicyFuturesPriceLevelOutput,
        config_path=POLICY_FUTURES_PRICE_LEVEL_CONFIG_PATH,
        output_field_units={},
    ),
    # ``output_field_units`` is intentionally empty for the same P8 +
    # P5 reason as bond_futures get_futures_volume_oi_tool: policy-
    # futures volume + OI live in CONTRACT-COUNT space (volume =
    # contracts traded; open_interest = contracts outstanding), which
    # has no honest member of the closed-enum ``TimeSeriesUnits``
    # family in V1. Declaring ``percent`` / ``bps`` / ``count`` would
    # silently lie under P8 (closed-family discipline) + P5 (honest
    # disclosure). The validator's empty-dict exemption (see
    # shared/workflow/validate.py:368) defers the unit check to the
    # operator's runtime refusal — the honest path until a future ADR
    # extends ``TimeSeriesUnits`` with a CONTRACTS member.
    "policy_futures_get_volume_open_interest_snapshot_tool": PrimitiveSpec(
        tool_name="policy_futures_get_volume_open_interest_snapshot_tool",
        callable=calculate_volume_open_interest_snapshot,
        input_class=VolumeOpenInterestSnapshotInput,
        output_class=VolumeOpenInterestSnapshotOutput,
        config_path=POLICY_FUTURES_VOLUME_OPEN_INTEREST_SNAPSHOT_CONFIG_PATH,
        output_field_units={},
    ),
    # ``output_field_units`` is intentionally empty for the same P8 +
    # P5 reason as the sibling policy_futures_get_futures_price_level_tool
    # entry: the calendar-spread's ``time_series`` carries TWO unit
    # spaces per row (raw_price_spread in the contract's price-spread
    # space, AND spread_implied_rate_pct in PERCENT POINTS). The
    # closed-enum ``TimeSeriesUnits`` family has no PRICE member;
    # declaring ``percent`` would only cover the implied-rate-spread
    # axis and silently mis-label the raw-price-spread axis. The
    # validator's empty-dict exemption (see
    # shared/workflow/validate.py:368) defers the unit check to the
    # operator's runtime refusal — that is the honest path until a
    # future ADR extends ``TimeSeriesUnits``.
    "policy_futures_get_futures_calendar_spread_tool": PrimitiveSpec(
        tool_name="policy_futures_get_futures_calendar_spread_tool",
        callable=calculate_futures_calendar_spread,
        input_class=FuturesCalendarSpreadInput,
        output_class=FuturesCalendarSpreadOutput,
        config_path=POLICY_FUTURES_CALENDAR_SPREAD_CONFIG_PATH,
        output_field_units={},
    ),
    # The simple-butterfly primitive's butterfly series IS single-unit
    # (PERCENT POINTS — the body's implied rate minus the wing-rate
    # average). Unlike the siblings ``futures_price_level`` and
    # ``futures_calendar_spread`` (which carry two unit spaces per row),
    # this primitive emits canonical ``TimeSeries`` with
    # ``TimeSeriesUnits.PERCENT`` (butterfly) +
    # ``TimeSeriesUnits.Z_SCORE`` (z-score) — so we declare them here.
    # The bespoke ``time_series`` shares the same PERCENT-POINTS unit
    # on its ``butterfly_value_pct`` field.
    "policy_futures_get_futures_butterfly_simple_tool": PrimitiveSpec(
        tool_name="policy_futures_get_futures_butterfly_simple_tool",
        callable=calculate_futures_butterfly_simple,
        input_class=FuturesButterflySimpleInput,
        output_class=FuturesButterflySimpleOutput,
        config_path=POLICY_FUTURES_BUTTERFLY_SIMPLE_CONFIG_PATH,
        output_field_units={
            "time_series": "percent",
            "time_series_butterfly": "percent",
            "time_series_zscore": "z_score",
        },
    ),
    # The cross-market-spread primitive's spread series IS single-
    # unit (PERCENT POINTS — the per-leg implied rate differential).
    # Unlike the siblings ``futures_price_level`` and
    # ``futures_calendar_spread`` (which carry two unit spaces per
    # row), this primitive emits canonical ``TimeSeries`` with
    # ``TimeSeriesUnits.PERCENT`` (spread) +
    # ``TimeSeriesUnits.Z_SCORE`` (z-score) — so we declare them
    # here. The bespoke ``time_series`` shares the same PERCENT-
    # POINTS unit on its ``spread_value_pct`` field. Mirrors the
    # sibling ``futures_butterfly_simple`` single-axis output_field_units
    # shape (the butterfly tool's series is also single-unit PERCENT
    # POINTS).
    "policy_futures_get_futures_cross_market_spread_tool": PrimitiveSpec(
        tool_name="policy_futures_get_futures_cross_market_spread_tool",
        callable=calculate_futures_cross_market_spread,
        input_class=FuturesCrossMarketSpreadInput,
        output_class=FuturesCrossMarketSpreadOutput,
        config_path=POLICY_FUTURES_CROSS_MARKET_SPREAD_CONFIG_PATH,
        output_field_units={
            "time_series": "percent",
            "time_series_spread": "percent",
            "time_series_zscore": "z_score",
        },
    ),
    # ``output_field_units`` is intentionally empty by design — same
    # exempt-snapshot pattern the sibling scan_bond_futures_extremes_tool
    # / scan_inflation_linkers_extremes_tool / sovereign_yield_panel
    # (with output_artifact_type) use.  This is a SNAPSHOT primitive
    # (no canonical TimeSeries on the wire); per-row context columns
    # mix the contract's native price units (``100 - rate`` for
    # inverse-priced strips), PERCENT (implied_rate_pct), PERCENT
    # POINTS (daily_change_implied_rate_pct), unit-less z-score,
    # CONTRACTS (open_interest), and plain-string reference columns
    # (contract_code / security_name / expiry_date).  Declaring a
    # single unit would silently lie about the mixed-shape row
    # payload under P8 (closed-family discipline) + P5 (honest
    # disclosure); the validator's empty-dict exemption (see
    # shared/workflow/validate.py:368) defers the unit check to the
    # operator's runtime refusal — the honest path until a future ADR
    # extends ``TimeSeriesUnits`` with PRICE + CONTRACTS members.
    # Per-strip-position history with the full range / percentile /
    # canonical TimeSeries lives on the sibling
    # policy_futures_get_futures_price_level_tool /
    # policy_futures_get_volume_open_interest_snapshot_tool primitives;
    # composing those is the honest path for time-series consumers.
    "policy_futures_get_futures_strip_snapshot_tool": PrimitiveSpec(
        tool_name="policy_futures_get_futures_strip_snapshot_tool",
        callable=calculate_futures_strip_snapshot,
        input_class=FuturesStripSnapshotInput,
        output_class=FuturesStripSnapshotOutput,
        config_path=POLICY_FUTURES_STRIP_SNAPSHOT_CONFIG_PATH,
        output_field_units={},
    ),
    # The pack-average primitive's pack-average series IS single-
    # unit (PERCENT — the simple arithmetic mean of four implied
    # rates). Unlike the siblings ``futures_price_level`` and
    # ``futures_calendar_spread`` (which carry two unit spaces per
    # row), this primitive emits canonical ``TimeSeries`` with
    # ``TimeSeriesUnits.PERCENT`` (pack-average) +
    # ``TimeSeriesUnits.Z_SCORE`` (z-score) — so we declare them
    # here. The bespoke ``time_series`` shares the same PERCENT unit
    # on its ``pack_average_implied_rate_pct`` field. Mirrors the
    # sibling ``futures_butterfly_simple`` /
    # ``futures_cross_market_spread`` single-axis output_field_units
    # shape (those primitives' series are also single-unit PERCENT).
    "policy_futures_get_futures_pack_average_simple_tool": PrimitiveSpec(
        tool_name="policy_futures_get_futures_pack_average_simple_tool",
        callable=calculate_futures_pack_average_simple,
        input_class=FuturesPackAverageSimpleInput,
        output_class=FuturesPackAverageSimpleOutput,
        config_path=POLICY_FUTURES_PACK_AVERAGE_SIMPLE_CONFIG_PATH,
        output_field_units={
            "time_series": "percent",
            "time_series_pack_average": "percent",
            "time_series_zscore": "z_score",
        },
    ),
    # ``output_field_units`` is intentionally empty by design —
    # same exempt-snapshot pattern the sibling
    # scan_bond_futures_extremes_tool /
    # scan_inflation_swaps_extremes_tool /
    # policy_futures_get_futures_strip_snapshot_tool use. This is
    # a SNAPSHOT primitive (no canonical TimeSeries on the wire);
    # per-row context columns mix PERCENT (implied_rate_pct), BPS
    # (daily_change_implied_rate_bps), the contract's native price
    # units (``100 - rate`` for inverse-priced strips), CONTRACTS
    # (volume / open_interest / delta_open_interest_1d), unit-less
    # z-score, and plain-string reference columns (contract_code /
    # security_name / expiry_date / quote_units / short_rate_regime).
    # Declaring a single unit would silently lie about the mixed-
    # shape row payload under P8 (closed-family discipline) + P5
    # (honest disclosure); the validator's empty-dict exemption
    # (see shared/workflow/validate.py:368) defers the unit check
    # to the operator's runtime refusal — the honest path until a
    # future ADR extends ``TimeSeriesUnits`` with PRICE +
    # CONTRACTS members. Per-strip history with the full range /
    # percentile / canonical TimeSeries lives on the sibling
    # policy_futures_get_futures_price_level_tool /
    # policy_futures_get_volume_open_interest_snapshot_tool
    # primitives; composing those is the honest path for
    # time-series consumers.
    "get_scan_policy_futures_extremes_tool": PrimitiveSpec(
        tool_name="get_scan_policy_futures_extremes_tool",
        callable=calculate_scan_policy_futures_extremes,
        input_class=ScanPolicyFuturesExtremesInput,
        output_class=ScanPolicyFuturesExtremesOutput,
        config_path=POLICY_FUTURES_SCAN_EXTREMES_CONFIG_PATH,
        output_field_units={},
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

    # ---- PR 19 / PR 20: Panel-emitting + breakeven primitives ----
    #
    # ``build_sovereign_yield_panel_tool`` and
    # ``compute_financing_rate_tool`` BOTH emit Panel artifacts (rows
    # = dates, columns = instrument keys / single rate column).  They
    # declare ``output_artifact_type="Panel"`` so the executor picks
    # ``tool_output_to_artifact_panel`` over the default Series
    # bridge.  ``calculate_breakeven_inflation_tool`` emits canonical
    # TimeSeries fields (matching the existing swap_spread pattern)
    # so its output_artifact_type defaults to "Series".
    "build_sovereign_yield_panel_tool": PrimitiveSpec(
        tool_name="build_sovereign_yield_panel_tool",
        callable=build_sovereign_yield_panel,
        input_class=SovereignYieldPanelInput,
        output_class=SovereignYieldPanelOutput,
        config_path=SOV_YIELD_PANEL_CONFIG_PATH,
        output_field_units={
            # All columns in the panel are yields (PERCENT); the
            # per-column units_by_column dict on the Panel itself
            # carries the authoritative per-leg unit tags.
            "panel": "percent",
        },
        output_artifact_type="Panel",
    ),
    "compute_financing_rate_tool": PrimitiveSpec(
        tool_name="compute_financing_rate_tool",
        callable=compute_financing_rate,
        input_class=FinancingRateInput,
        output_class=FinancingRateOutput,
        config_path=FINANCING_RATE_CONFIG_PATH,
        output_field_units={
            "panel": "percent",
        },
        output_artifact_type="Panel",
    ),
    "calculate_breakeven_inflation_tool": PrimitiveSpec(
        tool_name="calculate_breakeven_inflation_tool",
        callable=calculate_breakeven_inflation,
        input_class=BreakevenInflationInput,
        output_class=BreakevenInflationOutput,
        config_path=BREAKEVEN_INFLATION_CONFIG_PATH,
        output_field_units={
            "time_series_breakeven": "bps",
            "time_series_zscore": "z_score",
        },
        # Defaults to "Series"; explicit for clarity.
        output_artifact_type="Series",
    ),
    # ---- Custom-window z-score signal primitive ----
    # ``calculate_zscore_custom_tool`` emits a TimeSeries with Z_SCORE
    # units (rolling z-score on a single yield series).  Both field
    # names are declared:
    #   - ``time_series`` (legacy V1 name)
    #   - ``time_series_zscore`` (canonical convention used by every
    #     other z-score-emitting primitive in the codebase)
    # The two fields carry identical payloads — see
    # ZscoreCustomOutput's docstring for the naming reconciliation.
    "calculate_zscore_custom_tool": PrimitiveSpec(
        tool_name="calculate_zscore_custom_tool",
        callable=calculate_zscore_custom,
        input_class=ZscoreCustomInput,
        output_class=ZscoreCustomOutput,
        config_path=ZSCORE_CUSTOM_CONFIG_PATH,
        output_field_units={
            "time_series": "z_score",
            "time_series_zscore": "z_score",
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
    "calculate_real_yield_curve_spread_tool": PrimitiveSpec(
        tool_name="calculate_real_yield_curve_spread_tool",
        callable=calculate_real_yield_curve_spread,
        input_class=RealYieldCurveSpreadInput,
        output_class=RealYieldCurveSpreadOutput,
        config_path=REAL_YIELD_CURVE_SPREAD_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen real-yield-curve-spread row list —
            # frontend consumes ``spread_pct`` per row (the spread
            # is in PERCENT, NOT bps, because real yields are in
            # PERCENT and not multiplied by 100); declare PERCENT.
            "time_series": "percent",
            # Canonical TimeSeries: real-yield curve spread history
            # in PERCENT (mirrors real_yield_level's PERCENT
            # convention; distinct from the BPS convention every
            # breakeven / inflation-swap curve spread uses),
            # rolling z-score in Z_SCORE units.  Operator-layer
            # unit-compat checks rely on these declarations.
            "time_series_spread": "percent",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_cross_country_real_yield_spread_simple_tool": PrimitiveSpec(
        tool_name="calculate_cross_country_real_yield_spread_simple_tool",
        callable=calculate_cross_country_real_yield_spread_simple,
        input_class=CrossCountryRealYieldSpreadSimpleInput,
        output_class=CrossCountryRealYieldSpreadSimpleOutput,
        config_path=CROSS_COUNTRY_REAL_YIELD_SPREAD_SIMPLE_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen cross-country real-yield-spread
            # row list — frontend consumes ``spread_pct`` per row
            # (the spread is in PERCENT, NOT bps, because real
            # yields are in PERCENT and not multiplied by 100;
            # mirrors the same-country real_yield_curve_spread
            # PERCENT convention).
            "time_series": "percent",
            # Canonical TimeSeries: cross-country real-yield spread
            # history in PERCENT (same units as the underlying real
            # yields), rolling z-score in Z_SCORE units.  Operator-
            # layer unit-compat checks rely on these declarations.
            "time_series_spread": "percent",
            "time_series_zscore": "z_score",
        },
    ),
    # ``output_field_units`` is intentionally empty by design — same
    # exempt pattern the sibling scan_bond_futures_extremes_tool /
    # futures_price_level_tool / futures_volume_oi_tool use.  This is
    # a SNAPSHOT primitive (no canonical TimeSeries on the wire);
    # per-row context columns mix PERCENT (real_yield_pct), BPS
    # (daily_change_bps / monthly_change_bps), unit-less z-score, and
    # plain-string reference columns (maturity_date / country /
    # vendor_ticker).  Declaring a single unit would silently lie
    # about the mixed-shape row payload under P8 (closed-family
    # discipline) + P5 (honest disclosure); the validator's empty-
    # dict exemption (see shared/workflow/validate.py) defers the
    # unit check to the operator's runtime refusal — the honest path
    # until a future ADR extends ``TimeSeriesUnits``.  Per-bond /
    # cross-tenor history lives on the sibling per-bond
    # real_yield_level / real_yield_curve_spread / etc. primitives;
    # composing those is the honest path for time-series consumers.
    "scan_inflation_linkers_extremes_tool": PrimitiveSpec(
        tool_name="scan_inflation_linkers_extremes_tool",
        callable=calculate_scan_inflation_linkers_extremes,
        input_class=ScanInflationLinkersExtremesInput,
        output_class=ScanInflationLinkersExtremesOutput,
        config_path=SCAN_INFLATION_LINKERS_EXTREMES_CONFIG_PATH,
        output_field_units={},
    ),
    "calculate_real_yield_butterfly_tool": PrimitiveSpec(
        tool_name="calculate_real_yield_butterfly_tool",
        callable=calculate_real_yield_butterfly,
        input_class=RealYieldButterflyInput,
        output_class=RealYieldButterflyOutput,
        config_path=REAL_YIELD_BUTTERFLY_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen real-yield-butterfly row list —
            # frontend consumes ``butterfly_pct`` per row (the
            # butterfly is in PERCENT, NOT bps, because real yields
            # are in PERCENT and not multiplied by 100; mirrors the
            # same-curve real_yield_curve_spread PERCENT convention
            # and is distinct from the sovereign butterfly's BPS
            # convention).
            "time_series": "percent",
            # Canonical TimeSeries: real-yield butterfly history in
            # PERCENT (same units as the underlying real yields),
            # rolling z-score in Z_SCORE units.  Operator-layer
            # unit-compat checks rely on these declarations.
            "time_series_butterfly": "percent",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_breakeven_butterfly_tool": PrimitiveSpec(
        tool_name="calculate_breakeven_butterfly_tool",
        callable=calculate_breakeven_butterfly,
        input_class=BreakevenButterflyInput,
        output_class=BreakevenButterflyOutput,
        config_path=BREAKEVEN_BUTTERFLY_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen breakeven-butterfly row list —
            # frontend consumes ``butterfly_bps`` per row (the
            # butterfly is in BPS, same units as the underlying
            # breakeven series; mirrors the breakeven_curve_spread
            # / breakeven_inflation_simple BPS convention and is
            # distinct from the real_yield_butterfly's PERCENT
            # convention because breakevens are reported in bps).
            "time_series": "bps",
            # Canonical TimeSeries: breakeven butterfly history in
            # BPS (same units as the underlying breakeven series),
            # rolling z-score in Z_SCORE units.  Operator-layer
            # unit-compat checks rely on these declarations.
            "time_series_butterfly": "bps",
            "time_series_zscore": "z_score",
        },
    ),

    # ---- Inflation-swaps domain ----
    "calculate_inflation_swap_rate_level_tool": PrimitiveSpec(
        tool_name="calculate_inflation_swap_rate_level_tool",
        callable=calculate_inflation_swap_rate_level,
        input_class=InflationSwapRateLevelInput,
        output_class=InflationSwapRateLevelOutput,
        config_path=INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH,
        output_field_units={
            # ZCIS rates are quoted in percent — same unit as
            # nominal sovereign yields, OIS rates, and linker real
            # yields, but the underlying series is the par-rate the
            # zero-coupon inflation swap pays for inflation
            # compensation against the headline index.  Wire field
            # name on the snapshot is ``zcis_rate_pct``;
            # series_name carries the ``_zcis_rate`` suffix.
            "time_series": "percent",
        },
    ),
    "calculate_inflation_swap_curve_spread_tool": PrimitiveSpec(
        tool_name="calculate_inflation_swap_curve_spread_tool",
        callable=calculate_inflation_swap_curve_spread,
        input_class=InflationSwapCurveSpreadInput,
        output_class=InflationSwapCurveSpreadOutput,
        config_path=INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen ZCIS curve-spread row list —
            # frontend consumes ``spread_bps`` per row, so the
            # unit is BPS.
            "time_series": "bps",
            # Canonical TimeSeries: ZCIS curve spread history in
            # BPS, rolling z-score in Z_SCORE units.  Operator-
            # layer unit-compat checks rely on these declarations.
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_inflation_swap_forward_tool": PrimitiveSpec(
        tool_name="calculate_inflation_swap_forward_tool",
        callable=calculate_inflation_swap_forward,
        input_class=InflationSwapForwardInput,
        output_class=InflationSwapForwardOutput,
        config_path=INFLATION_SWAP_FORWARD_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen ZCIS forward row list — frontend
            # consumes ``forward_zcis_pct`` (percent) and
            # ``forward_zcis_bps`` (bps) per row.  Declare PERCENT
            # for the bespoke list to match the canonical forward
            # series unit (operator unit-compat checks key off the
            # primary unit per row).
            "time_series": "percent",
            # Canonical TimeSeries: forward ZCIS rate (a level) in
            # PERCENT — mirrors OIS forward_rate; do NOT ship in
            # BPS (that's the spread convention).  Rolling z-score
            # in Z_SCORE units.  Operator-layer unit-compat checks
            # rely on these declarations.
            "time_series_forward": "percent",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_cross_market_inflation_swap_spread_tool": PrimitiveSpec(
        tool_name="calculate_cross_market_inflation_swap_spread_tool",
        callable=calculate_cross_market_inflation_swap_spread,
        input_class=CrossMarketInflationSwapSpreadInput,
        output_class=CrossMarketInflationSwapSpreadOutput,
        config_path=CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen cross-market ZCIS spread row list
            # — frontend consumes ``spread_bps`` per row, so the
            # unit is BPS (mirrors sovereign cross_market_spread /
            # inflation_swap_curve_spread; cross-market spreads
            # are spread objects, not levels).
            "time_series": "bps",
            # Canonical TimeSeries: cross-market ZCIS spread
            # history in BPS, rolling z-score in Z_SCORE units.
            # Operator-layer unit-compat checks rely on these
            # declarations.
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_swap_breakeven_basis_simple_tool": PrimitiveSpec(
        tool_name="calculate_swap_breakeven_basis_simple_tool",
        callable=calculate_swap_breakeven_basis_simple,
        input_class=SwapBreakevenBasisSimpleInput,
        output_class=SwapBreakevenBasisSimpleOutput,
        config_path=SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen swap-breakeven basis row list —
            # frontend consumes ``basis_bps`` per row, so the unit
            # is BPS (the basis is a spread object, NOT a level).
            "time_series": "bps",
            # Canonical TimeSeries: swap-breakeven basis history
            # in BPS, rolling z-score in Z_SCORE units.  Operator-
            # layer unit-compat checks rely on these declarations.
            "time_series_basis": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    "calculate_inflation_swap_butterfly_tool": PrimitiveSpec(
        tool_name="calculate_inflation_swap_butterfly_tool",
        callable=calculate_inflation_swap_butterfly,
        input_class=InflationSwapButterflyInput,
        output_class=InflationSwapButterflyOutput,
        config_path=INFLATION_SWAP_BUTTERFLY_CONFIG_PATH,
        output_field_units={
            # Bespoke wire-frozen ZCIS butterfly row list —
            # frontend consumes ``butterfly_bps`` per row, so the
            # unit is BPS.  Even though the underlying ZCIS rates
            # are quoted in PERCENT, this is a CURVATURE-OF-RATES
            # object and the inflation_swaps domain established
            # the BPS convention for curve-shape views via
            # inflation_swap_curve_spread; mirrors that here.
            # Distinct from the real_yield_butterfly's PERCENT
            # convention (because real yields are level objects,
            # not curve-shape objects in this repo's V1 wire
            # conventions).
            "time_series": "bps",
            # Canonical TimeSeries: ZCIS butterfly history in BPS,
            # rolling z-score in Z_SCORE units.  Operator-layer
            # unit-compat checks rely on these declarations.
            "time_series_butterfly": "bps",
            "time_series_zscore": "z_score",
        },
    ),
    # ``scan_inflation_swaps_extremes_tool`` is a SNAPSHOT primitive
    # (no canonical TimeSeries on the wire); per-row context columns
    # mix PERCENT (zcis_rate_pct), BPS
    # (daily_change_zcis_rate_bps / monthly_change_zcis_rate_bps),
    # unit-less z-score, and plain-string reference columns
    # (maturity_date / underlying_index / vendor_ticker).  Declaring
    # a single unit would silently lie about the mixed-shape row
    # payload under P8 + P5; the validator's empty-dict exemption
    # (see shared/workflow/validate.py) defers the unit check to
    # the operator's runtime refusal — the honest path until a
    # future ADR extends ``TimeSeriesUnits``.  Same exempt pattern
    # the linker scanner uses.  Per-pillar / cross-tenor history
    # lives on the sibling per-pillar inflation_swap_rate_level /
    # inflation_swap_curve_spread / etc. primitives; composing
    # those is the honest path for time-series consumers.
    "scan_inflation_swaps_extremes_tool": PrimitiveSpec(
        tool_name="scan_inflation_swaps_extremes_tool",
        callable=calculate_scan_inflation_swaps_extremes,
        input_class=ScanInflationSwapsExtremesInput,
        output_class=ScanInflationSwapsExtremesOutput,
        config_path=SCAN_INFLATION_SWAPS_EXTREMES_CONFIG_PATH,
        output_field_units={},
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
