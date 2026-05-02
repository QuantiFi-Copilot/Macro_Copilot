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
from typing import Optional

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
