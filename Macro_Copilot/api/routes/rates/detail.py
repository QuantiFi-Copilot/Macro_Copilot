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
from datetime import date
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
# Standalone-bridge endpoint for the same-country bond-implied breakeven
# curve spread primitive (2-point tenor spread on a single nominal/linker
# pair — the inflation-compensation term-structure object).  Per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Same-country invariant inherited transitively from the spot breakeven
# primitive's ``_enforce_same_country_invariant`` guard.
from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread import (
    CONFIG_PATH as BREAKEVEN_CURVE_SPREAD_CONFIG_PATH,
    BreakevenCurveSpreadInput,
    BreakevenCurveSpreadOutput,
    calculate_breakeven_curve_spread,
)
# Standalone-bridge endpoint for the same-country linker real-yield
# butterfly primitive (3-point curvature on a SINGLE linker curve — no
# nominal pair).  Per ``docs_revamped/03_standards/methodology_exposure.md
# §5`` every new tool ships its OWN typed-detail endpoint consumed by
# both the extended and compact Build views (rendering_density dual-view)
# + the Monitor tile.
from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly import (
    CONFIG_PATH as REAL_YIELD_BUTTERFLY_CONFIG_PATH,
    RealYieldButterflyInput,
    RealYieldButterflyOutput,
    calculate_real_yield_butterfly,
)
# Standalone-bridge endpoint for the same-tenor cross-market ZCIS spread
# primitive (e.g. USD_ZCIS 5Y minus EUR_ZCIS 5Y).  First inflation_swaps tool
# under the standalone-bridge contract — per
# ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Surfaces the load-bearing index-family caveat (USD_ZCIS / EUR_ZCIS /
# GBP_ZCIS reference different indices — NOT a clean expected-inflation
# divergence) via the wire's per-leg metadata + ``index_family_caveat``.
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
    CONFIG_PATH as CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH,
    CrossMarketInflationSwapSpreadInput,
    CrossMarketInflationSwapSpreadOutput,
    calculate_cross_market_inflation_swap_spread,
)
# Standalone-bridge endpoint for the same-curve zero-coupon inflation swap
# (ZCIS) butterfly primitive (3-point curvature on ONE ZCIS curve family).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Surfaces the load-bearing index-family caveat (CPI-U / HICPxT / RPI are
# distinct inflation measures) via the same-curve invariant — all three legs
# share inflation_index_family / index_lag / interpolation / underlying_index.
from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
    CONFIG_PATH as INFLATION_SWAP_BUTTERFLY_CONFIG_PATH,
    InflationSwapButterflyInput,
    InflationSwapButterflyOutput,
    calculate_inflation_swap_butterfly,
)
# Standalone-bridge endpoint for the universe-wide ZCIS rate-extremes scanner.
# First SCANNER-shape primitive under the dual-view contract — the wire
# returns a ranked LIST of (curve_family, tenor) extremes rather than a single
# time series, so this endpoint feeds the per-tool BuildCompact (top-N table)
# and BuildExtended (universe scan + ranked detail) per
# ``docs_revamped/03_standards/rendering_density.md §10`` + the standalone-
# bridge contract (``methodology_exposure.md §5``).
from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
    CONFIG_PATH as SCAN_INFLATION_SWAPS_EXTREMES_CONFIG_PATH,
    ScanInflationSwapsExtremesInput,
    ScanInflationSwapsExtremesOutput,
    calculate_scan_inflation_swaps_extremes,
)
# Standalone-bridge endpoint for the same-curve OIS butterfly primitive (3-point
# curvature on ONE OIS par-swap curve family — e.g. USD_SOFR_OIS 2s5s10s).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Single-curve, raw OIS par-rate-space curvature — POSITIVE = belly cheap,
# NEGATIVE = belly rich.  Risk-neutral policy-pricing caveat surfaces on the
# methodology card (OIS prices the expected policy path, not realised outcomes).
from rates_agent.ois.tools.calculate_ois_butterfly import (
    CONFIG_PATH as OIS_BUTTERFLY_CONFIG_PATH,
    OISButterflyInput,
    OISButterflyOutput,
    calculate_ois_butterfly,
)
# Standalone-bridge endpoint for the same-curve OIS curve-spread primitive
# (2-point tenor spread on ONE OIS par-swap curve family — e.g. USD_SOFR_OIS
# 2s10s).  Same standalone-bridge contract as the OIS butterfly bridge: own
# typed-detail endpoint consumed by both the extended and compact Build views
# (rendering_density dual-view) + the Monitor tile.  Single-curve, raw OIS
# par-rate-space spread (long − short, in BPS).  Risk-neutral policy-pricing
# caveat surfaces on the methodology card (OIS prices the expected policy
# path, not realised outcomes).  Rolling-z-score conventions are YAML-locked
# on this primitive — only ``lookback_days`` + ``field_name`` are exposed.
from rates_agent.ois.tools.curve_spread import (
    CONFIG_PATH as OIS_CURVE_SPREAD_CONFIG_PATH,
    OISCurveSpreadInput,
    OISCurveSpreadOutput,
    calculate_ois_curve_spread,
)
# Standalone-bridge endpoint for the single-tenor OIS par-swap-rate-level
# primitive (e.g. USD_SOFR_OIS 2Y, EUR_ESTR_OIS 10Y, GBP_SONIA_OIS 5Y).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Risk-neutral implied policy path caveat (SOFR / EFFR / SONIA / ESTR
# family is the desk-canonical OIS implied policy expectation) surfaces on
# the methodology card.  Rolling-z-score conventions are YAML-locked on
# this primitive — only ``lookback_days`` + ``field_name`` are exposed at
# the input layer (mirrors the OIS curve_spread / butterfly siblings).
from rates_agent.ois.tools.rate_level import (
    CONFIG_PATH as OIS_RATE_LEVEL_CONFIG_PATH,
    OISRateLevelInput,
    OISRateLevelOutput,
    get_ois_rate_level,
)
# Standalone-bridge endpoint for the implied OIS forward-rate primitive
# (e.g. SOFR 1Y1Y, 5Y5Y ESTR, 2Y1Y SONIA, ad-hoc date-window forwards).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Risk-neutral implied policy path caveat (forwards on OIS curves price the
# expected policy path, not realised central-bank decisions) surfaces on
# the methodology card.  Two equivalent input modes — tenor-pair OR
# date-pair — supply exactly ONE; the schema layer rejects partial /
# both modes at construction time.  Rolling-z-score conventions are
# YAML-locked on this primitive — only ``lookback_days`` + ``field_name``
# are exposed at the input layer (mirrors the OIS rate_level /
# curve_spread / butterfly siblings).
from rates_agent.ois.tools.forward_rate import (
    CONFIG_PATH as OIS_FORWARD_RATE_CONFIG_PATH,
    OISForwardRateInput,
    OISForwardRateOutput,
    calculate_ois_forward_rate,
)
# Standalone-bridge endpoint for the single-pillar zero-coupon inflation swap
# (ZCIS) rate-level primitive (e.g. USD_ZCIS 5Y, EUR_ZCIS 10Y, GBP_ZCIS 2Y).
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` every new tool
# ships its OWN typed-detail endpoint consumed by both the extended and
# compact Build views (rendering_density dual-view) + the Monitor tile.
# Surfaces the load-bearing reference metadata (``inflation_index_family``,
# ``index_lag``, ``interpolation``, ``underlying_index``) so the desk can
# interpret the level honestly — USD_ZCIS / EUR_ZCIS / GBP_ZCIS reference
# distinct inflation indices (CPI-U / HICPxT / RPI) with different lags +
# interpolation conventions.  Rolling-z-score conventions are YAML-locked
# on this primitive — only ``lookback_days`` + ``field_name`` are exposed.
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
    CONFIG_PATH as INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH,
    InflationSwapRateLevelInput,
    InflationSwapRateLevelOutput,
    calculate_inflation_swap_rate_level,
)
# Standalone-bridge endpoint for the policy_futures strip-position price level
# primitive (SFR1 / SFR2 / ER1 / SFI1 / ... — STIR strip slots on
# SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT).  Keyed by
# ``(curve_family, strip_position)`` per ADR 0013.  Same standalone-bridge
# contract as the other rates primitives: own typed-detail endpoint consumed
# by both the extended and compact Build views (rendering_density dual-view)
# + the Monitor tile.  Methodology disclosure flows verbatim from
# compute()'s ``methodology_disclosure`` string (NOT a hardcoded TS literal).
from rates_agent.policy_futures.tools.futures_price_level import (
    CONFIG_PATH as POLICY_FUTURES_PRICE_LEVEL_CONFIG_PATH,
    FuturesPriceLevelInput,
    FuturesPriceLevelOutput,
    calculate_futures_price_level,
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


# ----------------------------------------------------------------------------
# /detail/breakeven-curve-spread  — same-country breakeven curve spread bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# breakeven_curve_spread primitive ships its OWN typed-detail endpoint.
# Same-country 2-point tenor spread on a single nominal/linker pair (e.g.
# UST/USD_TIPS 2s10s breakeven, UK_GILT/GBP_LINKER 5s30s breakeven).  The
# same payload feeds BOTH the extended and compact Build views and the
# Monitor tile (rendering_density.md §10).  The rolling-z-score conventions
# are YAML-locked on this primitive — no input-layer overrides for window /
# min-periods / ddof (mirrors the sibling breakeven-butterfly bridge).
# ============================================================================
@router.get(
    "/detail/breakeven-curve-spread",
    response_model=BreakevenCurveSpreadOutput,
    summary="Bond-Implied Breakeven Curve Spread Detail (standalone bridge)",
)
def breakeven_curve_spread_detail(
    engine: Engine = Depends(get_engine),
    nominal_curve_family: str = Query(..., description="Nominal sovereign curve family — UST / UK_GILT / FR_OAT / CANADA_GOVT"),
    linker_curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    short_tenor: str = Query(..., description="Short tenor of the spread (e.g. '2Y' for 2s10s)"),
    long_tenor: str = Query(..., description="Long tenor of the spread (e.g. '10Y' for 2s10s) — must be strictly longer than short_tenor"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL FOUR underlying series "
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

    The breakeven-curve-spread primitive intentionally does NOT expose
    the Phase-1 z-score overrides at its Input layer — its rolling-z-
    score conventions are sourced from the YAML at compute() time only
    (mirrors the sibling breakeven-butterfly primitive).
    """
    try:
        params = BreakevenCurveSpreadInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        bcs_config = load_tool_config(BREAKEVEN_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_breakeven_curve_spread(
            engine=engine, params=params, config=bcs_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/breakeven-curve-spread: tool failed for %s vs %s %s/%s",
            nominal_curve_family, linker_curve_family,
            short_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Breakeven curve spread for {nominal_curve_family} vs "
        f"{linker_curve_family} {short_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/real-yield-butterfly  — same-country linker real-yield butterfly bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# real_yield_butterfly primitive ships its OWN typed-detail endpoint.
# Single-curve primitive — one linker ``curve_family`` + three strictly-
# ordered tenors; no nominal counterparty (distinct from breakeven-butterfly).
# The same payload feeds BOTH the extended and compact Build views and the
# Monitor tile (rendering_density.md §10).  The rolling-z-score conventions
# are YAML-locked on this primitive — no input-layer overrides for window /
# min-periods / ddof.
# ============================================================================
@router.get(
    "/detail/real-yield-butterfly",
    response_model=RealYieldButterflyOutput,
    summary="Linker Real-Yield Butterfly Detail (standalone bridge)",
)
def real_yield_butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Linker curve family — USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB"),
    short_tenor: str = Query(..., description="Short wing tenor (e.g. '5Y' for 5s10s30s)"),
    belly_tenor: str = Query(..., description="Belly tenor (e.g. '10Y' for 5s10s30s)"),
    long_tenor: str = Query(..., description="Long wing tenor (e.g. '30Y' for 5s10s30s) — must satisfy short < belly < long"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL THREE endpoint real-yield "
            "series.  Omit (None) to use the tool's bundled "
            "``default_field_name`` convention (currently 'YLD_YTM_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the
    frontend module's ``surfaces/BuildExtended.tsx``,
    ``surfaces/BuildCompact.tsx``, AND the Monitor widget per the
    rendering-density dual-view + monitor contract.

    The real-yield-butterfly primitive intentionally does NOT expose the
    Phase-1 z-score overrides at its Input layer — its rolling-z-score
    conventions are sourced from the YAML at compute() time only.  This
    mirrors the sibling breakeven-butterfly bridge.
    """
    try:
        params = RealYieldButterflyInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        ryb_config = load_tool_config(REAL_YIELD_BUTTERFLY_CONFIG_PATH)
        result = calculate_real_yield_butterfly(
            engine=engine, params=params, config=ryb_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/real-yield-butterfly: tool failed for %s %s/%s/%s",
            curve_family, short_tenor, belly_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Real-yield butterfly for {curve_family} "
        f"{short_tenor}/{belly_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/cross-market-zcis  — same-tenor cross-market ZCIS spread bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# cross_market_inflation_swap_spread primitive ships its OWN typed-detail
# endpoint.  Two-curve, single-tenor primitive — two ZCIS curve families
# (e.g. USD_ZCIS, EUR_ZCIS, GBP_ZCIS) at a shared pillar (e.g. 5Y).
# Schema layer rejects ``leg_a_curve_family == leg_b_curve_family`` (same-
# curve, two-tenor spreads belong to ``inflation_swap_curve_spread``).
# The same payload feeds BOTH the extended and compact Build views and the
# Monitor tile (rendering_density.md §10).  Rolling-z-score conventions
# are YAML-locked on this primitive — no input-layer overrides for window /
# min-periods / ddof; only ``lookback_days`` + ``field_name`` are exposed.
# ============================================================================
@router.get(
    "/detail/cross-market-zcis",
    response_model=CrossMarketInflationSwapSpreadOutput,
    summary="Cross-Market ZCIS Spread Detail (standalone bridge)",
)
def cross_market_zcis_detail(
    engine: Engine = Depends(get_engine),
    leg_a_curve_family: str = Query(..., description="Left (numerator) ZCIS curve family — USD_ZCIS / EUR_ZCIS / GBP_ZCIS"),
    leg_b_curve_family: str = Query(..., description="Right (denominator) ZCIS curve family.  Must differ from leg_a_curve_family"),
    tenor: str = Query(..., description="Single tenor pillar shared by both legs (e.g. '5Y', '10Y')"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic threaded into BOTH endpoint ZCIS "
            "level series.  Omit (None) to use the tool's bundled "
            "``default_zcis_rate_field`` convention (currently 'PX_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The cross-market ZCIS spread primitive intentionally does NOT expose
    the z-score conventions at its Input layer — its rolling-z-score
    conventions are sourced from the YAML at compute() time only.  This
    mirrors the sibling breakeven-butterfly / real-yield-butterfly bridges.
    """
    try:
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family=leg_a_curve_family,
            leg_b_curve_family=leg_b_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        cmzcis_config = load_tool_config(CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH)
        result = calculate_cross_market_inflation_swap_spread(
            engine=engine, params=params, config=cmzcis_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/cross-market-zcis: tool failed for %s - %s %s",
            leg_a_curve_family, leg_b_curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Cross-market ZCIS spread for {leg_a_curve_family} - "
        f"{leg_b_curve_family} {tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/zcis-butterfly  — same-curve ZCIS butterfly bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# inflation_swap_butterfly primitive ships its OWN typed-detail endpoint.
# Single-curve, three-tenor primitive — one ZCIS ``curve_family`` (e.g.
# USD_ZCIS, EUR_ZCIS, GBP_ZCIS) plus three strictly-ordered tenors; cross-
# curve butterflies are forbidden by the schema layer.  The same payload
# feeds BOTH the extended and compact Build views and the Monitor tile
# (rendering_density.md §10).  Rolling-z-score conventions are YAML-locked
# on this primitive (no input-layer overrides — mirrors the breakeven-
# butterfly / real-yield-butterfly siblings).
# ============================================================================
@router.get(
    "/detail/zcis-butterfly",
    response_model=InflationSwapButterflyOutput,
    summary="Same-Curve ZCIS Butterfly Detail (standalone bridge)",
)
def zcis_butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="Inflation-swap curve family — USD_ZCIS / EUR_ZCIS / GBP_ZCIS"),
    short_tenor: str = Query(..., description="Short wing tenor (e.g. '2Y' for 2s5s10s, '5Y' for 5s10s30s)"),
    belly_tenor: str = Query(..., description="Belly tenor (e.g. '5Y' for 2s5s10s, '10Y' for 5s10s30s)"),
    long_tenor: str = Query(..., description="Long wing tenor (e.g. '10Y' for 2s5s10s, '30Y' for 5s10s30s) — must satisfy short < belly < long"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL THREE endpoint ZCIS rate "
            "series.  Omit (None) to use the tool's bundled "
            "``default_zcis_rate_field`` convention (currently 'PX_MID')."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The inflation_swap_butterfly primitive intentionally does NOT expose the
    z-score conventions at its Input layer — its rolling-z-score
    conventions are sourced from the YAML at compute() time only.  Mirrors
    the sibling breakeven-butterfly / real-yield-butterfly bridges.
    """
    try:
        params = InflationSwapButterflyInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        zcisfly_config = load_tool_config(INFLATION_SWAP_BUTTERFLY_CONFIG_PATH)
        result = calculate_inflation_swap_butterfly(
            engine=engine, params=params, config=zcisfly_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/zcis-butterfly: tool failed for %s %s/%s/%s",
            curve_family, short_tenor, belly_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"ZCIS butterfly for {curve_family} "
        f"{short_tenor}/{belly_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/ois-butterfly  — same-curve OIS butterfly bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# calculate_ois_butterfly primitive ships its OWN typed-detail endpoint.
# Single-curve, three-tenor primitive — one OIS ``curve_family`` (closed enum
# sourced from rates_agent/playbooks/ois.yml: USD_SOFR_OIS / EUR_ESTR_OIS /
# GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS) plus three distinct tenors;
# the schema layer rejects duplicate tenors at construction time.  The same
# payload feeds BOTH the extended and compact Build views and the Monitor
# tile (rendering_density.md §10).  Rolling-z-score conventions are YAML-
# locked on this primitive (mirrors the sibling sovereign / linker / ZCIS
# butterflies — no input-layer overrides for window / min-periods / ddof);
# only ``lookback_days`` + ``field_name`` are exposed at the API layer.
# ============================================================================
@router.get(
    "/detail/ois-butterfly",
    response_model=OISButterflyOutput,
    summary="Same-Curve OIS Butterfly Detail (standalone bridge)",
)
def ois_butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "OIS curve family — closed enum sourced from "
            "rates_agent/playbooks/ois.yml: USD_SOFR_OIS / EUR_ESTR_OIS / "
            "GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS."
        ),
    ),
    short_tenor: str = Query(..., description="Short wing tenor (e.g. '2Y' for 2s5s10s)"),
    belly_tenor: str = Query(..., description="Belly tenor (e.g. '5Y' for 2s5s10s)"),
    long_tenor: str = Query(..., description="Long wing tenor (e.g. '10Y' for 2s5s10s) — must satisfy short < belly < long"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for ALL THREE endpoint OIS par-swap "
            "rate series.  Omit (None) to use the tool's bundled "
            "``default_swap_rate_field`` convention (currently 'PX_LAST' — "
            "the OIS Bloomberg mid-rate field, NOT the sovereign "
            "'YLD_YTM_MID' yield-to-maturity field)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The OIS butterfly primitive intentionally does NOT expose the z-score
    conventions at its Input layer — its rolling-z-score conventions are
    sourced from the YAML at compute() time only.  Mirrors the sibling
    sovereign / linker / ZCIS butterfly bridges.
    """
    try:
        params = OISButterflyInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        oisfly_config = load_tool_config(OIS_BUTTERFLY_CONFIG_PATH)
        result = calculate_ois_butterfly(
            engine=engine, params=params, config=oisfly_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/ois-butterfly: tool failed for %s %s/%s/%s",
            curve_family, short_tenor, belly_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"OIS butterfly for {curve_family} "
        f"{short_tenor}/{belly_tenor}/{long_tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/ois-curve-spread  — same-curve OIS tenor spread bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# calculate_ois_curve_spread primitive ships its OWN typed-detail endpoint.
# Single-curve, two-tenor primitive — one OIS ``curve_family`` (USD_SOFR_OIS
# / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS) plus a
# (short_tenor, long_tenor) pair; the schema layer rejects identical tenors
# at construction time.  The same payload feeds BOTH the extended and compact
# Build views and the Monitor tile (rendering_density.md §10).  Rolling-
# z-score conventions are YAML-locked on this primitive (mirrors the sibling
# OIS butterfly bridge — no input-layer overrides for window / min-periods /
# ddof); only ``lookback_days`` + ``field_name`` are exposed at the API layer.
# ============================================================================
@router.get(
    "/detail/ois-curve-spread",
    response_model=OISCurveSpreadOutput,
    summary="Same-Curve OIS Tenor Spread Detail (standalone bridge)",
)
def ois_curve_spread_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "OIS curve family identifier.  Examples: 'USD_SOFR_OIS', "
            "'EUR_ESTR_OIS', 'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', "
            "'CAD_OIS'."
        ),
    ),
    short_tenor: str = Query(
        ...,
        description=(
            "Short leg of the spread.  OIS curves have a dense short-end "
            "grid: '1W', '1M', '2M', '3M', '6M', '9M', '1Y', '2Y', '3Y'."
        ),
    ),
    long_tenor: str = Query(
        ...,
        description=(
            "Long leg of the spread.  Examples: '2Y', '5Y', '10Y', '20Y', "
            "'30Y'.  Must differ from short_tenor."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic for both endpoint OIS par-swap rate "
            "series.  Omit (None) to use the tool's bundled "
            "``default_swap_rate_field`` convention (currently 'PX_LAST' — "
            "the OIS Bloomberg mid-rate field, NOT the sovereign "
            "'YLD_YTM_MID' yield-to-maturity field)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The OIS curve-spread primitive intentionally does NOT expose the z-score
    conventions at its Input layer — its rolling-z-score conventions are
    sourced from the YAML at compute() time only.  Mirrors the sibling OIS
    butterfly bridge.
    """
    try:
        params = OISCurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        cs_config = load_tool_config(OIS_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_ois_curve_spread(
            engine=engine, params=params, config=cs_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/ois-curve-spread: tool failed for %s %s/%s",
            curve_family, short_tenor, long_tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"OIS curve spread for {curve_family} {short_tenor}/{long_tenor}",
    )
    return result


# ============================================================================
# /detail/ois-rate-level  — single-tenor OIS par-swap-rate snapshot bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the OIS
# rate-level primitive ships its OWN typed-detail endpoint.  Same payload
# feeds BOTH the extended Build view (mounted for single-tool queries) and
# the compact Build view (mounted as a node body inside multi-tool DAGs) +
# the Monitor tile per rendering_density.md §10.  Rolling-z-score conventions
# are YAML-locked on this primitive (no input-layer overrides — mirrors the
# sibling OIS curve_spread / butterfly bridges); only ``lookback_days`` +
# ``field_name`` are exposed at the API layer.  Risk-neutral implied policy
# path caveat is the desk-canonical methodology disclosure (SOFR / EFFR /
# SONIA / ESTR / TONA / AONIA / CORRA family is the OIS implied policy
# expectation).
# ============================================================================
@router.get(
    "/detail/ois-rate-level",
    response_model=OISRateLevelOutput,
    summary="OIS Rate Level Detail (standalone bridge)",
)
def ois_rate_level_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "OIS curve family identifier.  Examples: 'USD_SOFR_OIS', "
            "'EUR_ESTR_OIS', 'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', "
            "'CAD_OIS'."
        ),
    ),
    tenor: str = Query(
        ...,
        description=(
            "Tenor point on the OIS curve.  OIS curves have a dense "
            "short-end grid: '1W', '1M', '2M', '3M', '6M', '9M', '1Y', "
            "'2Y', '3Y', '5Y', '10Y', '20Y', '30Y'."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit (None) to use the tool's "
            "bundled ``default_swap_rate_field`` convention from "
            "rate_level/config.yaml (currently 'PX_LAST' — the OIS "
            "Bloomberg mid-rate field, NOT the sovereign 'YLD_YTM_MID' "
            "yield-to-maturity field)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The OIS rate-level primitive intentionally does NOT expose the z-score
    conventions at its Input layer — its rolling-z-score conventions are
    sourced from the YAML at compute() time only.  Mirrors the sibling OIS
    curve_spread / butterfly bridges.
    """
    try:
        params = OISRateLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        rl_config = load_tool_config(OIS_RATE_LEVEL_CONFIG_PATH)
        result = get_ois_rate_level(
            engine=engine, params=params, config=rl_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/ois-rate-level: tool failed for %s %s",
            curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"OIS rate level for {curve_family} {tenor}",
    )
    return result


# ============================================================================
# /detail/ois-forward-rate  — implied OIS forward-rate snapshot bridge
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the OIS
# forward-rate primitive ships its OWN typed-detail endpoint.  Same payload
# feeds BOTH the extended Build view (mounted for single-tool queries) and
# the compact Build view (mounted as a node body inside multi-tool DAGs) +
# the Monitor tile per rendering_density.md §10.  Two equivalent input
# modes — tenor-pair (start_tenor + end_tenor) OR date-pair (start_date +
# end_date); supply exactly ONE.  Rolling-z-score conventions are
# YAML-locked on this primitive (no input-layer overrides — mirrors the
# OIS rate_level / curve_spread / butterfly siblings); only
# ``lookback_days`` + ``field_name`` are exposed at the API layer.  Sign
# convention surfaced on the methodology card: forward_rate_pct is the
# absolute implied forward rate; daily_change_bps POSITIVE = the forward
# repriced HIGHER (hawkish implied-policy-path stretch).  Risk-neutral
# implied policy path caveat (OIS forwards price the EXPECTED policy
# path, not realised central-bank decisions) is the desk-canonical
# methodology disclosure.
# ============================================================================
@router.get(
    "/detail/ois-forward-rate",
    response_model=OISForwardRateOutput,
    summary="OIS Forward Rate Detail (standalone bridge)",
)
def ois_forward_rate_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "OIS curve family identifier.  Examples: 'USD_SOFR_OIS', "
            "'EUR_ESTR_OIS', 'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', "
            "'CAD_OIS'."
        ),
    ),
    start_tenor: Optional[str] = Query(
        default=None,
        description=(
            "Start tenor of the forward window.  For '1Y1Y' use '1Y'; "
            "for '5Y5Y' use '5Y'; for '2Y1Y' use '2Y'.  Must be present "
            "on the curve.  Mutually exclusive with start_date."
        ),
    ),
    end_tenor: Optional[str] = Query(
        default=None,
        description=(
            "End tenor of the forward window.  For '1Y1Y' use '2Y' "
            "(start=1Y + forward=1Y); for '5Y5Y' use '10Y'; for '2Y1Y' "
            "use '3Y'.  Must be present on the curve.  Mutually "
            "exclusive with end_date."
        ),
    ),
    start_date: Optional[str] = Query(
        default=None,
        description=(
            "Start date of the forward window (YYYY-MM-DD).  Mutually "
            "exclusive with start_tenor.  Must be on or after the "
            "curve's as-of date."
        ),
    ),
    end_date: Optional[str] = Query(
        default=None,
        description=(
            "End date of the forward window (YYYY-MM-DD).  Must be "
            "strictly after start_date.  Mutually exclusive with "
            "end_tenor."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit (None) to use the tool's "
            "bundled ``default_swap_rate_field`` convention from "
            "forward_rate/config.yaml (currently 'PX_LAST' — the OIS "
            "Bloomberg mid-rate field, NOT the sovereign 'YLD_YTM_MID' "
            "yield-to-maturity field)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    Supply exactly ONE of (start_tenor + end_tenor) or (start_date +
    end_date) — the schema layer rejects partial / both modes at
    construction time.  The OIS forward-rate primitive intentionally
    does NOT expose the z-score conventions at its Input layer — its
    rolling-z-score conventions are sourced from the YAML at compute()
    time only.  Mirrors the sibling OIS rate_level / curve_spread /
    butterfly bridges.
    """
    try:
        params = OISForwardRateInput(
            curve_family=curve_family,
            start_tenor=start_tenor,
            end_tenor=end_tenor,
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        fr_config = load_tool_config(OIS_FORWARD_RATE_CONFIG_PATH)
        result = calculate_ois_forward_rate(
            engine=engine, params=params, config=fr_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/ois-forward-rate: tool failed for %s tenor=(%s,%s) date=(%s,%s)",
            curve_family, start_tenor, end_tenor, start_date, end_date,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    window_label = (
        f"{start_tenor}/{end_tenor}" if start_tenor and end_tenor
        else f"{start_date} to {end_date}"
    )
    _tool_result_or_raise(
        result,
        f"OIS forward rate for {curve_family} {window_label}",
    )
    return result


# ============================================================================
# /detail/inflation-swap-rate-level — single-pillar ZCIS rate snapshot bridge
# ----------------------------------------------------------------------------
# First level-shape inflation_swaps primitive under the standalone-bridge
# contract.  Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# ZCIS rate-level primitive ships its OWN typed-detail endpoint.  Same payload
# feeds BOTH the extended Build view (mounted for single-tool queries) and
# the compact Build view (mounted as a node body inside multi-tool DAGs) +
# the Monitor tile per rendering_density.md §10.  Rolling-z-score conventions
# are YAML-locked on this primitive (no input-layer overrides — mirrors the
# OIS rate_level sibling); only ``lookback_days`` + ``field_name`` are exposed
# at the API layer.  Surfaces the LOAD-BEARING reference metadata
# (``inflation_index_family`` / ``index_lag`` / ``interpolation`` /
# ``underlying_index``) on the wire so the desk can interpret the level
# honestly — USD_ZCIS references CPI-U with 3M lag + Daily interpolation,
# EUR_ZCIS references HICPxT with 3M lag + Monthly interpolation, GBP_ZCIS
# references RPI with 2M lag + Monthly interpolation.  ``methodology_label``
# is threaded from config.yaml's ``methodology.what_it_does`` (NOT hardcoded).
# ============================================================================
@router.get(
    "/detail/inflation-swap-rate-level",
    response_model=InflationSwapRateLevelOutput,
    summary="ZCIS Rate Level Detail (standalone bridge)",
)
def inflation_swap_rate_level_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Inflation-swap curve family identifier.  Examples: "
            "'USD_ZCIS' (US CPI-U), 'EUR_ZCIS' (Eurozone HICPxT), "
            "'GBP_ZCIS' (UK RPI).  See rates_agent/playbooks/"
            "inflation_swaps.yml for the ingested universe."
        ),
    ),
    tenor: str = Query(
        ...,
        description=(
            "Tenor point on the ZCIS curve.  Current ingested grid is "
            "'1Y', '2Y', '3Y', '5Y', '10Y', '20Y', '30Y' on each of "
            "USD_ZCIS / EUR_ZCIS / GBP_ZCIS."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic.  Omit (None) to use the tool's "
            "bundled ``default_zcis_rate_field`` convention from "
            "inflation_swap_rate_level/config.yaml (currently 'PX_MID' "
            "— the canonical mid quoted ZCIS rate Bloomberg publishes "
            "for inflation swaps)."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx``, ``surfaces/BuildCompact.tsx``,
    AND the Monitor widget per the rendering-density dual-view + monitor
    contract.

    The ZCIS rate-level primitive intentionally does NOT expose the z-score
    conventions at its Input layer — they're sourced from the YAML at
    compute() time only.  Mirrors the OIS rate_level + sibling level tools.
    """
    try:
        params = InflationSwapRateLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        isrl_config = load_tool_config(INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH)
        result = calculate_inflation_swap_rate_level(
            engine=engine, params=params, config=isrl_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/inflation-swap-rate-level: tool failed for %s %s",
            curve_family, tenor,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"ZCIS rate level for {curve_family} {tenor}",
    )
    return result


# ----------------------------------------------------------------------------
# /detail/zcis-scanner  — universe-wide ZCIS rate-extremes scanner bridge
# ----------------------------------------------------------------------------
# First SCANNER-shape primitive under the standalone-bridge contract.  Wire
# shape is a ranked LIST (top-N rows by |z| of the 252d-rolling ZCIS rate
# level z-score) rather than a single time series — the BuildCompact view
# renders this as a top-N table (NOT a sparkline) and the BuildExtended view
# renders the same payload as a universe scan + full ranked detail.  The
# rolling-z-score conventions are YAML-locked on this primitive (no input-
# layer overrides — mirrors the sibling linker / bond_futures scanners);
# ``curve_families`` / ``top_n`` / ``min_abs_z_score`` / ``as_of_date``
# remain exposed.
# ============================================================================
@router.get(
    "/detail/zcis-scanner",
    response_model=ScanInflationSwapsExtremesOutput,
    summary="ZCIS Universe Extremes Scan (standalone bridge)",
)
def zcis_scanner_detail(
    engine: Engine = Depends(get_engine),
    curve_families: Optional[str] = Query(
        default=None,
        description=(
            "Comma-separated list of ZCIS curve families to scan.  Omit "
            "(None) for the full universe (USD_ZCIS / EUR_ZCIS / "
            "GBP_ZCIS).  Pass a CSV to narrow (e.g. 'USD_ZCIS,EUR_ZCIS')."
            "  Non-ZCIS families are refused at schema-validation time."
        ),
    ),
    top_n: Optional[int] = Query(
        default=None,
        ge=1,
        le=50,
        description=(
            "Number of extreme stems to return.  Omit (None) to fall "
            "through to the YAML default (currently 5)."
        ),
    ),
    min_abs_z_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold for inclusion.  Omit "
            "(None) to fall through to the YAML default (currently 1.5)."
        ),
    ),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the scan.  Omit "
            "(None) to anchor to the most-recent shared trading day in "
            "the DB across the universe."
        ),
    ),
):
    """Same payload semantics as the MCP wrapper; consumed by the frontend
    module's ``surfaces/BuildExtended.tsx`` (universe scan + ranked detail)
    AND ``surfaces/BuildCompact.tsx`` (top-N table) per the rendering-
    density dual-view contract + the Monitor widget per the standalone-
    bridge contract.

    The rolling-z-score conventions are YAML-locked on this primitive —
    only scope / threshold / anchor inputs are exposed at the API layer.
    """
    parsed_families: Optional[List[str]] = None
    if curve_families and curve_families.strip():
        parsed_families = [
            cf.strip() for cf in curve_families.split(",") if cf.strip()
        ]

    as_of_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid as_of_date {as_of_date!r}: must be ISO "
                    f"YYYY-MM-DD (e.g. '2026-04-08'). Detail: {exc}"
                ),
            )
    else:
        as_of_arg = None

    try:
        params = ScanInflationSwapsExtremesInput(
            curve_families=parsed_families,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
            as_of_date=as_of_arg,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        scan_config = load_tool_config(
            SCAN_INFLATION_SWAPS_EXTREMES_CONFIG_PATH,
        )
        result = calculate_scan_inflation_swaps_extremes(
            engine=engine, params=params, config=scan_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/zcis-scanner: tool failed for curve_families=%s",
            parsed_families,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"ZCIS universe scan ({', '.join(parsed_families) if parsed_families else 'full universe'})",
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


# ----------------------------------------------------------------------------
# /detail/policy-futures-price  — policy_futures strip-position price level
# ----------------------------------------------------------------------------
# Per ``docs_revamped/03_standards/methodology_exposure.md §5`` the
# ``policy_futures_get_futures_price_level_tool`` primitive ships its OWN
# typed-detail endpoint.  Keyed by ``(curve_family, strip_position)`` per
# ADR 0013 (strip-position-keyed monitors).  Conventions are YAML-locked
# in V1; only the structural keys plus ``lookback_days`` / ``as_of_date``
# / ``field_name`` are exposed (mirrors the MCP wrapper's input surface).
@router.get(
    "/detail/policy-futures-price",
    response_model=FuturesPriceLevelOutput,
    summary="Policy Futures Strip-Position Price Level Detail (standalone bridge)",
)
def policy_futures_price_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(
        ...,
        description=(
            "Policy-futures curve family — 'SOFR_FUT' (US RFR), "
            "'EUR_SHORT_RATE_FUT' (Euribor IBOR), 'SONIA_FUT' (UK RFR)."
        ),
    ),
    strip_position: int = Query(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position. 1 = front contract; whites = 1-4, "
            "reds = 5-8 in the V1 universe."
        ),
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    as_of_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO-format date (YYYY-MM-DD) anchoring the snapshot.  Omit "
            "to anchor at the universe's last observed trade_date for "
            "the requested strip (post-fetch data-max anchor).  A date "
            "BEYOND the universe's last observed trade_date returns the "
            "documented controlled-error envelope rather than silently "
            "re-labelling an unbounded read."
        ),
    ),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg observation field mnemonic.  Omit (None) to use "
            "the tool's bundled ``default_price_field`` convention from "
            "futures_price_level/config.yaml (currently 'PX_LAST').  "
            "Per config.yaml:default_price_field."
        ),
    ),
):
    """Same payload + sentinel semantics as the MCP wrapper.  Consumed by
    ``surfaces/BuildExtended.tsx`` AND ``surfaces/BuildCompact.tsx`` per
    the rendering-density dual-view contract, plus the Monitor tile.
    None-sentinels on ``field_name`` / ``as_of_date`` fall through to the
    YAML default / data-max anchor via compute() — same shadowing fix
    pattern as sovereign get_yield_levels / linker real_yield_level.
    """
    parsed_as_of: Optional[date] = None
    if as_of_date and as_of_date.strip():
        try:
            parsed_as_of = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid as_of_date {as_of_date!r}: {exc}",
            )

    try:
        params = FuturesPriceLevelInput(
            curve_family=curve_family,
            strip_position=strip_position,
            lookback_days=lookback_days,
            as_of_date=parsed_as_of,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        pf_config = load_tool_config(POLICY_FUTURES_PRICE_LEVEL_CONFIG_PATH)
        result = calculate_futures_price_level(
            engine=engine, params=params, config=pf_config,
        )
    except Exception as exc:
        logger.exception(
            "detail/policy-futures-price: tool failed for %s strip=%d",
            curve_family, strip_position,
        )
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(
        result,
        f"Policy futures price level for {curve_family} strip={strip_position}",
    )
    return result
