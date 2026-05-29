"""
detail.py — Rates Workspace Detail Endpoints
===============================================

Full-detail endpoints that power the "See more in workspace" flow:
    /detail/yield, /detail/spread, /detail/cross-market,
    /detail/butterfly, /detail/regime

Each returns the complete tool output including time_series for charts.
"""

from __future__ import annotations

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Engine

from api.dependencies import get_engine
from rates_agent.sovereign_bonds.tools.schemas import (
    CurveSpreadInput,
    CurveSpreadOutput,
    CrossMarketSpreadInput,
    CrossMarketSpreadOutput,
    CurveMoveInput,
    CurveMoveOutput,
    ButterflyInput,
    ButterflyOutput,
    YieldLevelInput,
    YieldLevelOutput,
)
from rates_agent.sovereign_bonds.tools.curve_spread import (
    CONFIG_PATH as CURVE_SPREAD_CONFIG_PATH,
    calculate_curve_spread,
)
from rates_agent.sovereign_bonds.tools.curve_move_classifier import (
    CONFIG_PATH as CURVE_MOVE_CONFIG_PATH,
    classify_curve_move_compute,
)
from rates_agent.sovereign_bonds.tools.cross_market_spread import (
    CONFIG_PATH as CROSS_MARKET_CONFIG_PATH,
    calculate_cross_market_spread,
)
from rates_agent.sovereign_bonds.tools.butterfly import (
    CONFIG_PATH as BUTTERFLY_CONFIG_PATH,
    calculate_butterfly,
)
from rates_agent.sovereign_bonds.tools.yield_levels import (
    CONFIG_PATH as YIELD_LEVELS_CONFIG_PATH,
    get_yield_levels,
)
# Phase-1 pilot: standalone-bridge endpoint for the linker
# real_yield_level primitive.  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new
# tool ships its OWN typed-detail endpoint; this is the first one.
from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
    CONFIG_PATH as REAL_YIELD_LEVEL_CONFIG_PATH,
    RealYieldLevelInput,
    RealYieldLevelOutput,
    get_real_yield_level,
)
# Phase-1 pilot Stage B/C: standalone-bridge endpoints for the linker
# breakeven_inflation_simple + real_yield_curve_spread primitives.  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new
# tool ships its OWN typed-detail endpoint consumed by both the
# extended and compact Build views (rendering_density dual-view).
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
    CONFIG_PATH as BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH,
    BreakevenInflationSimpleInput,
    BreakevenInflationSimpleOutput,
    calculate_breakeven_inflation_simple,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
    CONFIG_PATH as REAL_YIELD_CURVE_SPREAD_CONFIG_PATH,
    RealYieldCurveSpreadInput,
    RealYieldCurveSpreadOutput,
    calculate_real_yield_curve_spread,
)
# Standalone-bridge endpoint for the same-country bond-implied breakeven
# butterfly primitive (3-point breakeven curvature).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
    CONFIG_PATH as BREAKEVEN_BUTTERFLY_CONFIG_PATH,
    BreakevenButterflyInput,
    BreakevenButterflyOutput,
    calculate_breakeven_butterfly,
)
from rates_agent.sovereign_bonds.tools.zscore_custom import (
    CONFIG_PATH as ZSCORE_CUSTOM_CONFIG_PATH,
    ZscoreCustomInput,
    ZscoreCustomOutput,
    calculate_zscore_custom,
)
from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
    CONFIG_PATH as BETA_ADJUSTED_SPREAD_CONFIG_PATH,
    BetaAdjustedSpreadInput,
    BetaAdjustedSpreadOutput,
    calculate_beta_adjusted_spread,
)
from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
    CONFIG_PATH as PCA_YIELD_CURVE_CONFIG_PATH,
    PcaYieldCurveInput,
    PcaYieldCurveOutput,
    calculate_pca_yield_curve,
)
from shared.config import load_tool_config

logger = logging.getLogger("api.routes.rates.detail")

router = APIRouter()


# ============================================================================
# HELPERS
# ============================================================================

def _tool_result_or_raise(result: dict, context: str) -> dict:
    """Check a tool result dict for an error key and raise the
    correct HTTP status.

    Status ladder:
      404 — data does not exist for the requested instrument / window.
      503 — database / infrastructure is unavailable.
      422 — caller's input is syntactically valid but semantically
            rejected by a tool-level cross-layer contract (e.g.,
            zscore_custom's z_score_window_days < the YAML's
            z_score_min_periods).  These are USER-INPUT errors, not
            server bugs, so the client gets a 422 rather than a 500.
      500 — unclassified.  Real server bug; should be rare.
    """
    if "error" not in result:
        return result

    error_msg = result["error"]
    lower = error_msg.lower()

    not_found_phrases = [
        "no data found", "missing tenor", "missing curve",
        "no overlapping observations", "no observations within",
        "all values were null", "insufficient data",
        "no instruments found",
    ]
    if any(phrase in lower for phrase in not_found_phrases):
        raise HTTPException(status_code=404, detail=f"{context}: {error_msg}")

    infra_phrases = [
        "database connection failed", "connection refused",
        "timeout", "could not connect", "operational error",
    ]
    if any(phrase in lower for phrase in infra_phrases):
        raise HTTPException(status_code=503, detail=f"{context}: {error_msg}")

    # User-input mismatches against tool-level cross-layer contracts.
    # Today's only producer is zscore_custom's small-window guard
    # ("z_score_window_days=N is smaller than the YAML's
    # z_score_min_periods=M"); future tools that surface similar
    # input-vs-config errors should reuse this phrase shape so the
    # client gets a 422 rather than a 500.
    user_input_phrases = [
        "is smaller than the yaml",
        "is larger than the yaml",
        "conflicts with the yaml",
        "duplicate tenor",
    ]
    if any(phrase in lower for phrase in user_input_phrases):
        raise HTTPException(status_code=422, detail=f"{context}: {error_msg}")

    raise HTTPException(status_code=500, detail=f"{context}: {error_msg}")


# ============================================================================
# WORKSPACE ENDPOINTS
# ============================================================================

@router.get("/detail/yield", response_model=YieldLevelOutput, summary="Yield Level Detail (workspace)")
def yield_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    tenor: str = Query(..., description="e.g. '10Y'"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's "
            "bundled ``default_field_name`` convention from "
            "yield_levels/config.yaml (currently 'YLD_YTM_MID').  "
            "Pass explicitly to override per request."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  A previous version hardcoded
    ``Query(default="YLD_YTM_MID")`` which silently shadowed the YAML
    default — same fix as the curve_move_classifier wrapper-shadowing
    cleanup (commit b2605ee)."""
    try:
        params = YieldLevelInput(
            curve_family=curve_family, tenor=tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass yield_levels' bundled config explicitly so the config
    # dependency is observable at the endpoint.  load_tool_config is
    # process-cached, so this is a free lookup after the first call.
    try:
        yl_config = load_tool_config(YIELD_LEVELS_CONFIG_PATH)
        result = get_yield_levels(
            engine=engine, params=params, config=yl_config,
        )
    except Exception as exc:
        logger.exception("detail/yield: tool failed for %s %s", curve_family, tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Yield level for {curve_family} {tenor}")
    return result


# ============================================================================
# /detail/real_yield  — Phase-1 pilot standalone-bridge endpoint
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new
# primitive ships its OWN typed-detail endpoint (no shared shell reuse).
# This route mirrors ``/detail/yield``'s shape but operates on the linker
# real-yield primitive AND exposes the three Phase-1 methodology overrides
# (z_score_window_days / z_score_min_periods / z_score_ddof) per the
# rendering_density standard so both the extended and compact Build
# surfaces can pass them through to compute().
#
# The same payload feeds BOTH the extended Build view (mounted in
# single-tool queries) and the compact Build view (mounted in multi-
# tool query DAG nodes); per rendering_density.md §10 the compact view
# just renders less of the payload.  No separate "summary" endpoint.
# ============================================================================
@router.get(
    "/detail/real_yield",
    response_model=RealYieldLevelOutput,
    summary="Real Yield Level Detail (linker, standalone bridge)",
)
def real_yield_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    tenor: str = Query(..., description="Tenor point on the linker curve, e.g. '10Y'"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit (None) to use the tool's "
            "bundled ``default_field_name`` convention from "
            "real_yield_level/config.yaml (currently 'YLD_YTM_MID' — "
            "the linker real-yield-to-maturity mnemonic).  Per the "
            "Phase-1 exposure decision in config.yaml:default_field_name.exposure."
        ),
    ),
    z_score_window_days: Optional[int] = Query(
        default=None,
        ge=60,
        le=1260,
        description=(
            "Trading-day window for the rolling z-score.  Omit (None) "
            "to use the YAML default (currently 252).  Pass 60 / 126 "
            "for tactical framing or 504 for structural-regime work.  "
            "Per config.yaml:z_score_window_days.exposure."
        ),
    ),
    z_score_min_periods: Optional[int] = Query(
        default=None,
        ge=20,
        le=252,
        description=(
            "Minimum observations before the rolling z-score is "
            "emitted.  Omit (None) to use the YAML default (60).  "
            "Scale with ``z_score_window_days`` when overriding."
        ),
    ),
    z_score_ddof: Optional[int] = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Standard-deviation degrees of freedom.  Omit (None) to "
            "use the YAML default (1 — sample std).  0 = population std."
        ),
    ),
):
    """Same payload + override semantics as the MCP wrapper; consumed by
    the frontend module's ``surfaces/BuildExtended.tsx`` AND
    ``surfaces/BuildCompact.tsx`` per the rendering-density dual-view
    contract.  None-sentinels on the four exposed Phase-1 conventions
    fall through to the YAML defaults via compute._conventions_from_config.
    """
    try:
        params = RealYieldLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
            z_score_window_days=z_score_window_days,
            z_score_min_periods=z_score_min_periods,
            z_score_ddof=z_score_ddof,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        ryl_config = load_tool_config(REAL_YIELD_LEVEL_CONFIG_PATH)
        result = get_real_yield_level(
            engine=engine, params=params, config=ryl_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/real_yield: tool failed for %s %s", curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result, f"Real yield level for {curve_family} {tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/breakeven  — Stage-B standalone-bridge endpoint
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# breakeven_inflation_simple primitive ships its OWN typed-detail
# endpoint (no shared shell reuse).  Exposes the same four Phase-1
# methodology overrides as real_yield (z_score_window_days /
# z_score_min_periods / z_score_ddof / field_name) so both the
# extended and compact Build surfaces pass them through to compute().
# The same payload feeds BOTH views (rendering_density.md §10).
# ============================================================================
@router.get(
    "/detail/breakeven",
    response_model=BreakevenInflationSimpleOutput,
    summary="Bond-Implied Breakeven Inflation Detail (linker, standalone bridge)",
)
def breakeven_detail(
    engine: Engine = Depends(get_engine),
    nominal_curve_family: str = Query(..., description="Nominal sovereign curve family — UST / UK_GILT / FR_OAT / CANADA_GOVT"),
    linker_curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    tenor: str = Query(..., description="Tenor point on both curves, e.g. '10Y'"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for BOTH legs.  Omit (None) to use "
            "the tool's bundled ``default_field_name`` convention "
            "(currently 'YLD_YTM_MID').  Per "
            "config.yaml:default_field_name.exposure."
        ),
    ),
    z_score_window_days: Optional[int] = Query(
        default=None,
        ge=60,
        le=1260,
        description=(
            "Trading-day window for the rolling z-score of the breakeven "
            "(bps) series.  Omit (None) to use the YAML default "
            "(currently 252).  Pass 60 / 126 for tactical framing or 504 "
            "for structural-regime work.  Per "
            "config.yaml:z_score_window_days.exposure."
        ),
    ),
    z_score_min_periods: Optional[int] = Query(
        default=None,
        ge=20,
        le=252,
        description=(
            "Minimum observations before the rolling z-score is emitted.  "
            "Omit (None) to use the YAML default (60).  Scale with "
            "``z_score_window_days`` when overriding."
        ),
    ),
    z_score_ddof: Optional[int] = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Standard-deviation degrees of freedom.  Omit (None) to use "
            "the YAML default (1 — sample std).  0 = population std."
        ),
    ),
):
    """Same payload + override semantics as the MCP wrapper; consumed by
    the frontend module's ``surfaces/BuildExtended.tsx`` AND
    ``surfaces/BuildCompact.tsx`` per the rendering-density dual-view
    contract.  None-sentinels on the four exposed Phase-1 conventions
    fall through to the YAML defaults via compute._conventions_from_config.
    """
    try:
        params = BreakevenInflationSimpleInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
            z_score_window_days=z_score_window_days,
            z_score_min_periods=z_score_min_periods,
            z_score_ddof=z_score_ddof,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        bei_config = load_tool_config(BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH)
        result = calculate_breakeven_inflation_simple(
            engine=engine, params=params, config=bei_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/breakeven: tool failed for %s vs %s @ %s",
            nominal_curve_family, linker_curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Breakeven inflation for {nominal_curve_family} vs "
        f"{linker_curve_family} {tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/real_yield_curve_spread  — Stage-C standalone-bridge endpoint
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# real_yield_curve_spread primitive ships its OWN typed-detail endpoint.
# The three rolling-z-score overrides apply to the spread's own z-score +
# the fetch-window buffer; field_name flows to both endpoint level calls.
# The same payload feeds BOTH the extended and compact Build views.
# ============================================================================
@router.get(
    "/detail/real_yield_curve_spread",
    response_model=RealYieldCurveSpreadOutput,
    summary="Real-Yield Curve Spread Detail (linker, standalone bridge)",
)
def real_yield_curve_spread_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    short_tenor: str = Query(..., description="Short tenor of the spread, e.g. '5Y' for 5s10s"),
    long_tenor: str = Query(..., description="Long tenor of the spread, e.g. '10Y' for 5s10s — must be strictly longer than short_tenor"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for BOTH endpoint real-yield "
            "series.  Omit (None) to use the tool's bundled "
            "``default_field_name`` convention (currently 'YLD_YTM_MID'). "
            "Per config.yaml:default_field_name.exposure."
        ),
    ),
    z_score_window_days: Optional[int] = Query(
        default=None,
        ge=60,
        le=1260,
        description=(
            "Trading-day window for the rolling z-score of the real-yield "
            "curve spread (percent) series.  Omit (None) to use the YAML "
            "default (currently 252).  Applies to the spread's own "
            "z-score AND the fetch-window buffer; the inner endpoint "
            "level calls use the YAML default.  Per "
            "config.yaml:z_score_window_days.exposure."
        ),
    ),
    z_score_min_periods: Optional[int] = Query(
        default=None,
        ge=20,
        le=252,
        description=(
            "Minimum observations before the rolling z-score is emitted.  "
            "Omit (None) to use the YAML default (60).  Scale with "
            "``z_score_window_days`` when overriding."
        ),
    ),
    z_score_ddof: Optional[int] = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Standard-deviation degrees of freedom.  Omit (None) to use "
            "the YAML default (1 — sample std).  0 = population std."
        ),
    ),
):
    """Same payload + override semantics as the MCP wrapper; consumed by
    the frontend module's ``surfaces/BuildExtended.tsx`` AND
    ``surfaces/BuildCompact.tsx`` per the rendering-density dual-view
    contract.  None-sentinels on the four exposed Phase-1 conventions
    fall through to the YAML defaults via compute._conventions_from_config.
    """
    try:
        params = RealYieldCurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
            z_score_window_days=z_score_window_days,
            z_score_min_periods=z_score_min_periods,
            z_score_ddof=z_score_ddof,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        rycs_config = load_tool_config(REAL_YIELD_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_real_yield_curve_spread(
            engine=engine, params=params, config=rycs_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/real_yield_curve_spread: tool failed for %s %s%s",
            curve_family, short_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Real-yield curve spread for {curve_family} "
        f"{short_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/breakeven-butterfly  — same-country breakeven butterfly bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# breakeven_butterfly primitive ships its OWN typed-detail endpoint.
# The same payload feeds BOTH the extended and compact Build views and
# the Monitor tile (rendering_density.md §10).  Same four Phase-1
# methodology overrides as the spot breakeven primitive (the inner
# composed calls share the same z-score window / min-periods / ddof
# / field_name semantics).
# ============================================================================
@router.get(
    "/detail/breakeven-butterfly",
    response_model=BreakevenButterflyOutput,
    summary="Bond-Implied Breakeven Butterfly Detail (standalone bridge)",
)
def breakeven_butterfly_detail(
    engine: Engine = Depends(get_engine),
    nominal_curve_family: str = Query(..., description="Nominal sovereign curve family — UST / UK_GILT / FR_OAT / CANADA_GOVT"),
    linker_curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    short_tenor: str = Query(..., description="Short wing tenor (e.g. '2Y' for 2s5s10s)"),
    belly_tenor: str = Query(..., description="Belly tenor (e.g. '5Y' for 2s5s10s)"),
    long_tenor: str = Query(..., description="Long wing tenor (e.g. '10Y' for 2s5s10s) — must satisfy short < belly < long"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL SIX underlying series "
            "(nominal + linker at each endpoint tenor).  Omit (None) to "
            "use the tool's bundled ``default_field_name`` convention "
            "(currently 'YLD_YTM_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the
    frontend module's ``surfaces/BuildExtended.tsx``,
    ``surfaces/BuildCompact.tsx``, AND the Monitor widget per the
    rendering-density dual-view + monitor contract.

    The breakeven-butterfly primitive intentionally does NOT expose the
    Phase-1 z-score overrides at its Input layer — its rolling-z-score
    conventions are sourced from the YAML at compute() time only.
    """
    try:
        params = BreakevenButterflyInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        bbf_config = load_tool_config(BREAKEVEN_BUTTERFLY_CONFIG_PATH)
        result = calculate_breakeven_butterfly(
            engine=engine, params=params, config=bbf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/breakeven-butterfly: tool failed for %s vs %s %s/%s/%s",
            nominal_curve_family, linker_curve_family,
            short_tenor, belly_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Breakeven butterfly for {nominal_curve_family} vs "
        f"{linker_curve_family} {short_tenor}/{belly_tenor}/{long_tenor}",
    )
    return result


@router.get("/detail/spread", response_model=CurveSpreadOutput, summary="Curve Spread Detail (workspace)")
def spread_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    short_tenor: str = Query(default="2Y"),
    long_tenor: str = Query(default="10Y"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: str = Query(default="YLD_YTM_MID"),
):
    try:
        params = CurveSpreadInput(
            curve_family=curve_family, short_tenor=short_tenor,
            long_tenor=long_tenor, lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass the curve_spread tool's bundled config explicitly so the
    # config dependency is observable at the endpoint.  load_tool_config
    # is process-cached, so this is a free lookup after the first call.
    try:
        cs_config = load_tool_config(CURVE_SPREAD_CONFIG_PATH)
        result = calculate_curve_spread(
            engine=engine, params=params, config=cs_config,
        )
    except Exception as exc:
        logger.exception("detail/spread: tool failed for %s %s/%s", curve_family, short_tenor, long_tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Spread for {curve_family} {short_tenor}/{long_tenor}")
    return result


@router.get("/detail/cross-market", response_model=CrossMarketSpreadOutput, summary="Cross-Market Spread Detail (workspace)")
def cross_market_detail(
    engine: Engine = Depends(get_engine),
    curve_family_1: str = Query(..., description="e.g. 'IT_BTP'"),
    curve_family_2: str = Query(..., description="e.g. 'DE_BUND'"),
    tenor: str = Query(default="10Y"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's bundled "
            "``default_field_name`` convention from "
            "cross_market_spread/config.yaml (currently 'YLD_YTM_MID').  "
            "Pass explicitly to override per request.  Same wrapper-"
            "shadowing fix applied as the other migrated detail endpoints "
            "(commit b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  A previous version hardcoded
    ``Query(default="YLD_YTM_MID")`` which silently shadowed the YAML
    default — same fix as the other migrated tools."""
    try:
        params = CrossMarketSpreadInput(
            curve_family_1=curve_family_1, curve_family_2=curve_family_2,
            tenor=tenor, lookback_days=lookback_days, field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass cross_market_spread's bundled config explicitly so the
    # config dependency is observable at the endpoint.  load_tool_config
    # is process-cached, so this is a free lookup after the first call.
    try:
        cm_config = load_tool_config(CROSS_MARKET_CONFIG_PATH)
        result = calculate_cross_market_spread(
            engine=engine, params=params, config=cm_config,
        )
    except Exception as exc:
        logger.exception("detail/cross-market: tool failed for %s-%s %s",
                         curve_family_1, curve_family_2, tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Cross-market {curve_family_1}-{curve_family_2} {tenor}")
    return result


@router.get("/detail/butterfly", response_model=ButterflyOutput, summary="Butterfly Detail (workspace)")
def butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    short_tenor: str = Query(default="2Y"),
    belly_tenor: str = Query(default="5Y"),
    long_tenor: str = Query(default="10Y"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's bundled "
            "``default_field_name`` convention from butterfly/config.yaml "
            "(currently 'YLD_YTM_MID').  Pass explicitly to override per "
            "request.  Same wrapper-shadowing fix applied as the "
            "yield_levels and curve_move_classifier endpoints (commit "
            "b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  A previous version hardcoded
    ``Query(default="YLD_YTM_MID")`` which silently shadowed the YAML
    default — same fix as yield_levels and curve_move_classifier."""
    try:
        params = ButterflyInput(
            curve_family=curve_family, short_tenor=short_tenor,
            belly_tenor=belly_tenor, long_tenor=long_tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass butterfly's bundled config explicitly so the config
    # dependency is observable at the endpoint.  load_tool_config is
    # process-cached, so this is a free lookup after the first call.
    try:
        bf_config = load_tool_config(BUTTERFLY_CONFIG_PATH)
        result = calculate_butterfly(
            engine=engine, params=params, config=bf_config,
        )
    except Exception as exc:
        logger.exception("detail/butterfly: tool failed for %s %s/%s/%s",
                         curve_family, short_tenor, belly_tenor, long_tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Butterfly for {curve_family} {short_tenor}/{belly_tenor}/{long_tenor}")
    return result


@router.get("/detail/regime", summary="Curve Regime Detail (workspace)")
def regime_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    front_tenor: str = Query(default="2Y"),
    back_tenor: str = Query(default="10Y"),
    lookback_period: str = Query(default="1d", description="'1d', '5d', '22d', or '63d'"),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's "
            "bundled ``default_field_name`` convention from "
            "config.yaml (currently 'YLD_YTM_MID').  Pass explicitly "
            "to override per request."
        ),
    ),
):
    """User-facing endpoint name retains "regime" because that's how
    PMs and the frontend's existing typescript types reference this
    surface (``RegimeView`` / ``RegimeOutput``).  Internally we now
    call the renamed ``classify_curve_move_compute`` and translate
    the new ``classification`` / ``description`` field names back to
    the legacy ``regime_tag`` / ``regime_description`` wire format
    so the frontend doesn't need to change.

    ``field_name`` defaults to None at the query layer (FastAPI maps
    a missing query param to None) so the tool's compute() can
    resolve it against the YAML's ``default_field_name`` convention.
    A previous version hardcoded ``Query(default="YLD_YTM_MID")``,
    which silently shadowed the YAML default — fixed alongside the
    matching MCP-wrapper fix.

    The rename rationale (single-observation classifier, not a
    persistence-state regime detector) is documented in
    docs/architecture/tool_architecture.md."""
    try:
        params = CurveMoveInput(
            curve_family=curve_family, front_tenor=front_tenor,
            back_tenor=back_tenor, lookback_period=lookback_period,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        cm_config = load_tool_config(CURVE_MOVE_CONFIG_PATH)
        result = classify_curve_move_compute(
            engine=engine, params=params, config=cm_config,
        )
    except Exception as exc:
        logger.exception("detail/regime: tool failed for %s %s/%s %s",
                         curve_family, front_tenor, back_tenor, lookback_period)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Regime for {curve_family} {front_tenor}/{back_tenor} ({lookback_period})")

    # Translate to the legacy wire format the frontend expects:
    # ``classification`` → ``regime_tag``, ``description`` → ``regime_description``.
    metrics = result.get("current_metrics", {})
    translated_metrics = {**metrics}
    if "classification" in translated_metrics:
        translated_metrics["regime_tag"] = translated_metrics.pop("classification")
    if "description" in translated_metrics:
        translated_metrics["regime_description"] = translated_metrics.pop("description")

    return {"current_metrics": translated_metrics}


@router.get(
    "/detail/zscore-custom",
    response_model=ZscoreCustomOutput,
    summary="Custom-window rolling z-score (workspace)",
)
def zscore_custom_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    tenor: str = Query(..., description="e.g. '10Y'"),
    z_score_window_days: int = Query(
        ...,
        ge=20,
        le=1260,
        description=(
            "Rolling window length in trading days for the z-score.  "
            "This is the tool's central methodological choice — set per "
            "request.  Typical desk values: 60 (tactical), 126 "
            "(quarterly), 252 (annual), 504 (two-year)."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's bundled "
            "``default_field_name`` convention from "
            "zscore_custom/config.yaml (currently 'YLD_YTM_MID').  Pass "
            "explicitly to override per request.  Same wrapper-shadowing "
            "fix applied as the yield_levels and curve_move_classifier "
            "endpoints (commit b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  All other methodology knobs (`min_periods`, `ddof`,
    `buffer_multiplier`, `ffill_limit_days`, rounding) are YAML-locked
    and not exposed at the route — see A13 in
    docs/architecture/tool_architecture.md."""
    try:
        params = ZscoreCustomInput(
            curve_family=curve_family,
            tenor=tenor,
            z_score_window_days=z_score_window_days,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    # Pass zscore_custom's bundled config explicitly so the dependency
    # is observable at the endpoint.  load_tool_config is process-cached,
    # so this is a free lookup after the first call.
    try:
        zc_config = load_tool_config(ZSCORE_CUSTOM_CONFIG_PATH)
        result = calculate_zscore_custom(
            engine=engine, params=params, config=zc_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/zscore-custom: tool failed for %s %s window=%d",
            curve_family, tenor, z_score_window_days,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Custom z-score for {curve_family} {tenor} (window={z_score_window_days}d)",
    )
    return result


@router.get(
    "/detail/beta-adjusted-spread",
    response_model=BetaAdjustedSpreadOutput,
    summary="Beta-Adjusted Spread Detail (workspace)",
)
def beta_adjusted_spread_detail(
    engine: Engine = Depends(get_engine),
    target_curve_family: str = Query(..., description="e.g. 'IT_BTP'"),
    target_tenor: str = Query(..., description="e.g. '10Y'"),
    regressor_curve_family: str = Query(..., description="e.g. 'DE_BUND'"),
    regressor_tenor: str = Query(..., description="e.g. '10Y'"),
    regression_window_days: int = Query(
        ...,
        ge=10,
        le=2520,
        description=(
            "Trailing-window length in trading-day rows for each "
            "rolling fit.  Central methodological choice — set per "
            "request.  Typical desk values: 60 (tactical), 252 "
            "(annual), 504 (two-year)."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic — applies to BOTH legs.  Omit to "
            "use the tool's bundled ``default_field_name`` from "
            "beta_adjusted_spread/config.yaml (currently 'YLD_YTM_MID').  "
            "Same wrapper-shadowing fix applied as the zscore_custom "
            "and yield_levels endpoints (commit b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  All other methodology knobs (`min_periods`, `solver`,
    `condition`-threshold, residual-z-score window, rounding) are
    YAML-locked and not exposed at the route — see A13 in
    docs/architecture/tool_architecture.md.

    The small-window guard (regression_window_days <
    regression_min_periods) returns a controlled error envelope whose
    phrase shape maps to HTTP 422 via the helper's user_input_phrases
    list — same client-error class as zscore_custom's small-window
    guard."""
    try:
        params = BetaAdjustedSpreadInput(
            target_curve_family=target_curve_family,
            target_tenor=target_tenor,
            regressor_curve_family=regressor_curve_family,
            regressor_tenor=regressor_tenor,
            regression_window_days=regression_window_days,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        bas_config = load_tool_config(BETA_ADJUSTED_SPREAD_CONFIG_PATH)
        result = calculate_beta_adjusted_spread(
            engine=engine, params=params, config=bas_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/beta-adjusted-spread: tool failed for %s_%s on "
            "%s_%s window=%d",
            target_curve_family, target_tenor,
            regressor_curve_family, regressor_tenor,
            regression_window_days,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Beta-adjusted {target_curve_family}-{regressor_curve_family} "
        f"{target_tenor}/{regressor_tenor} (window={regression_window_days}d)",
    )
    return result


@router.get(
    "/detail/pca-yield-curve",
    response_model=PcaYieldCurveOutput,
    summary="PCA on Yield Curve (workspace)",
)
def pca_yield_curve_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Sovereign curve, e.g. 'UST'"),
    tenors: Optional[List[str]] = Query(
        default=None,
        description=(
            "Subset of tenor labels.  Repeat the param: "
            "``?tenors=1Y&tenors=2Y&tenors=10Y``.  Omit to use all "
            "playbook-configured tenors of the curve_family.  When "
            "supplied explicitly, the fit uses exactly those tenors "
            "or returns an error."
        ),
    ),
    lookback_days: int = Query(default=1825, ge=400, le=7300),
    n_components: int = Query(default=3, ge=1, le=8),
    change_frequency: Literal["daily", "weekly"] = Query(default="daily"),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit to use the tool's "
            "bundled ``default_field_name`` from "
            "pca_yield_curve/config.yaml (currently 'YLD_YTM_MID').  "
            "Same wrapper-shadowing fix applied as the rest of the "
            "rates roster (commit b2605ee)."
        ),
    ),
):
    """``field_name`` defaults to None at the query layer so the tool's
    compute() can resolve it against the YAML's ``default_field_name``
    convention.  All methodology knobs (``min_observations_for_pca``,
    ``sign_anchor``, ``degenerate_variance_share_threshold``,
    ``ffill_limit_days``, all rounding decimals) are YAML-locked
    and not exposed at the route — see A13 in
    docs/architecture/tool_architecture.md.

    The ``lookback_days`` lower bound is a conservative calendar-day
    floor, not a 1:1 mirror of ``min_observations_for_pca`` — the
    actual fit still depends on how many non-NaN trading-day changes
    remain after differencing.

    The cross-layer min_observations guard returns a controlled error
    envelope whose phrase shape maps to HTTP 422 via the helper's
    user_input_phrases list."""
    try:
        params = PcaYieldCurveInput(
            curve_family=curve_family,
            tenors=tenors,
            lookback_days=lookback_days,
            n_components=n_components,
            change_frequency=change_frequency,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        pca_config = load_tool_config(PCA_YIELD_CURVE_CONFIG_PATH)
        result = calculate_pca_yield_curve(
            engine=engine, params=params, config=pca_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/pca-yield-curve: tool failed for %s n_components=%d",
            curve_family, n_components,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"PCA for {curve_family} (n_components={n_components}, "
        f"{change_frequency}, lookback={lookback_days}d)",
    )
    return result
