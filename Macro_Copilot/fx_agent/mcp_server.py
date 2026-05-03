"""
mcp_server.py — MCP Server for the FX Agent
===========================================

Exposes deterministic FX tools to the Copilot orchestrator via stdio MCP.
The REST API returns full workspace payloads; the MCP surface returns compact
JSON suitable for LLM context.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from fx_agent.forwards.tools.forward_curve import (  # noqa: E402
    FXForwardCurveInput,
    get_fx_forward_curve,
)
from fx_agent.forwards.tools.fx_carry import FXCarryInput, get_fx_carry  # noqa: E402
from fx_agent.macro.tools.trade_setup import (  # noqa: E402
    FXTradeSetupInput,
    get_fx_trade_setup,
)
from fx_agent.macro.tools.risk_overlay import (  # noqa: E402
    FXMacroRiskOverlayInput,
    get_fx_macro_risk_overlay,
)
from fx_agent.spot.tools.spot_levels import (  # noqa: E402
    FXSpotLevelInput,
    get_fx_spot_level,
)
from fx_agent.spot.tools.scanner import run_fx_scanner  # noqa: E402
from fx_agent.spot.tools.schemas import FXScannerInput  # noqa: E402
from fx_agent.vol.tools.realized_vol import (  # noqa: E402
    FXRealizedVolInput,
    get_fx_realized_vol,
)

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fx_agent.mcp_server")


mcp = FastMCP(
    name="fx-agent",
    instructions=(
        "You are the FX Agent for a macro hedge-fund desk. Use deterministic "
        "tools for FX spot levels, forward-implied carry, and forward curves. "
        "Never do the calculations yourself; call the tool and summarise the "
        "structured output."
    ),
)


def _json_error(message: str) -> str:
    return json.dumps({"error": message}, default=str)


@mcp.tool()
def get_fx_spot_level_tool(
    pair: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get a current FX spot snapshot for one pair.

    Use this when the user asks where a pair trades, how much it moved, where
    it sits versus its trailing range, or whether spot looks stretched.

    Parameters
    ----------
    pair : str
        FX pair, e.g. EURUSD, GBPUSD, USDJPY.
    lookback_days : int
        Calendar days of history for the display window.
    field_name : str
        Bloomberg field. Leave empty to use PX_LAST.
    """
    try:
        params = FXSpotLevelInput(
            pair=pair,
            lookback_days=lookback_days,
            field_name=field_name or "PX_LAST",
        )
    except ValidationError as exc:
        logger.warning("[get_fx_spot_level_tool] validation failed: %s", exc)
        return _json_error(f"Invalid parameters: {exc.errors()}")

    try:
        result = get_fx_spot_level(params=params)
    except Exception as exc:
        logger.exception("[get_fx_spot_level_tool] failed")
        return _json_error(f"FX spot level failed: {exc}")

    return result.model_dump_json()


@mcp.tool()
def get_fx_carry_tool(tenor: str = "1M") -> str:
    """Rank G10 FX pairs by forward-implied carry for one tenor.

    Use this when the user asks about FX carry, high-carry/low-carry pairs,
    forward points, or annualized carry by tenor.

    Parameters
    ----------
    tenor : str
        Forward tenor, e.g. 1W, 1M, 3M, 6M.
    """
    try:
        params = FXCarryInput(tenor=tenor)
    except ValidationError as exc:
        logger.warning("[get_fx_carry_tool] validation failed: %s", exc)
        return _json_error(f"Invalid parameters: {exc.errors()}")

    try:
        result = get_fx_carry(params=params)
    except Exception as exc:
        logger.exception("[get_fx_carry_tool] failed")
        return _json_error(f"FX carry failed: {exc}")

    return result.model_dump_json()


@mcp.tool()
def get_fx_forward_curve_tool(pair: str) -> str:
    """Get the current FX forward curve for one pair across available tenors.

    Use this for questions about forward points term structure, outright
    forwards, and annualized carry across tenors for a single FX pair.

    Parameters
    ----------
    pair : str
        FX pair, e.g. EURUSD, GBPUSD, USDJPY.
    """
    try:
        params = FXForwardCurveInput(pair=pair)
    except ValidationError as exc:
        logger.warning("[get_fx_forward_curve_tool] validation failed: %s", exc)
        return _json_error(f"Invalid parameters: {exc.errors()}")

    try:
        result = get_fx_forward_curve(params=params)
    except Exception as exc:
        logger.exception("[get_fx_forward_curve_tool] failed")
        return _json_error(f"FX forward curve failed: {exc}")

    return result.model_dump_json()


@mcp.tool()
def scan_fx_extremes_tool(
    market_scope: str = "",
    top_n: int = 10,
    field_name: str = "PX_LAST",
) -> str:
    """Scan FX spot pairs for stretched z-scores and momentum signals.

    Use this when the user asks which FX pairs look stretched, which G10
    pairs have the biggest z-scores, or where spot momentum is breaking out.

    Parameters
    ----------
    market_scope : str
        Optional market filter, e.g. G10. Leave empty for all ingested FX spot.
    top_n : int
        Maximum number of pairs to return.
    field_name : str
        Bloomberg field. Defaults to PX_LAST.
    """
    try:
        params = FXScannerInput(
            market_scope=market_scope or None,
            top_n=top_n,
            field_name=field_name,
        )
    except ValidationError as exc:
        logger.warning("[scan_fx_extremes_tool] validation failed: %s", exc)
        return _json_error(f"Invalid parameters: {exc.errors()}")

    try:
        result = run_fx_scanner(params=params)
    except Exception as exc:
        logger.exception("[scan_fx_extremes_tool] failed")
        return _json_error(f"FX scanner failed: {exc}")

    return result.model_dump_json()


@mcp.tool()
def get_fx_realized_vol_tool(
    pair: str,
    window_observations: int = 21,
    lookback_days: int = 365,
    return_type: str = "log_return",
    field_name: str = "PX_LAST",
) -> str:
    """Compute annualized realized volatility for one FX pair.

    Use this when the user asks about realized vol, whether a pair is moving
    more than usual, or wants spot context with a volatility lens.

    Parameters
    ----------
    pair : str
        FX pair, e.g. EURUSD, GBPUSD, USDJPY.
    window_observations : int
        Rolling return window, e.g. 21 for one trading month.
    lookback_days : int
        Calendar days of history to fetch.
    return_type : str
        log_return or simple_return.
    field_name : str
        Bloomberg field. Defaults to PX_LAST.
    """
    try:
        params = FXRealizedVolInput(
            pair=pair,
            window_observations=window_observations,
            lookback_days=lookback_days,
            return_type=return_type,
            field_name=field_name,
        )
    except ValidationError as exc:
        logger.warning("[get_fx_realized_vol_tool] validation failed: %s", exc)
        return _json_error(f"Invalid parameters: {exc.errors()}")

    try:
        result = get_fx_realized_vol(params=params)
    except Exception as exc:
        logger.exception("[get_fx_realized_vol_tool] failed")
        return _json_error(f"FX realized vol failed: {exc}")

    llm_response = {"current_metrics": result.current_metrics.model_dump()}
    return json.dumps(llm_response, default=str)


@mcp.tool()
def get_fx_trade_setup_tool(
    pair: str,
    tenor: str = "1M",
    vol_window_observations: int = 21,
    lookback_days: int = 365,
) -> str:
    """Build a deterministic FX trade setup for one pair.

    Use this when the user asks for a trade idea, setup, bias, directional
    view, or risk/reward summary combining spot, carry, forwards and realized
    volatility.

    Parameters
    ----------
    pair : str
        FX pair, e.g. EURUSD, GBPUSD, USDJPY.
    tenor : str
        Carry tenor, e.g. 1W, 1M, 3M, 6M.
    vol_window_observations : int
        Rolling observation window for realized volatility.
    lookback_days : int
        Calendar days of spot history for spot and vol context.
    """
    try:
        params = FXTradeSetupInput(
            pair=pair,
            tenor=tenor,
            vol_window_observations=vol_window_observations,
            lookback_days=lookback_days,
        )
    except ValidationError as exc:
        logger.warning("[get_fx_trade_setup_tool] validation failed: %s", exc)
        return _json_error(f"Invalid parameters: {exc.errors()}")

    try:
        result = get_fx_trade_setup(params=params)
    except Exception as exc:
        logger.exception("[get_fx_trade_setup_tool] failed")
        return _json_error(f"FX trade setup failed: {exc}")

    return result.model_dump_json()


@mcp.tool()
def get_fx_macro_risk_overlay_tool(
    pair: str,
    lookback_days: int = 365,
    correlation_window_observations: int = 63,
    field_name: str = "PX_LAST",
) -> str:
    """Overlay one FX pair with macro risk proxies.

    Use this when the user asks about risk-on/risk-off context, DXY, VIX,
    MOVE, SPX, gold, oil, macro risk proxies, or whether broad risk signals
    confirm or challenge an FX setup.

    Parameters
    ----------
    pair : str
        FX pair, e.g. EURUSD, GBPUSD, USDJPY.
    lookback_days : int
        Calendar days of history for proxy context.
    correlation_window_observations : int
        Observation window used for return correlation.
    field_name : str
        Bloomberg field. Defaults to PX_LAST.
    """
    try:
        params = FXMacroRiskOverlayInput(
            pair=pair,
            lookback_days=lookback_days,
            correlation_window_observations=correlation_window_observations,
            field_name=field_name,
        )
    except ValidationError as exc:
        logger.warning("[get_fx_macro_risk_overlay_tool] validation failed: %s", exc)
        return _json_error(f"Invalid parameters: {exc.errors()}")

    try:
        result = get_fx_macro_risk_overlay(params=params)
    except Exception as exc:
        logger.exception("[get_fx_macro_risk_overlay_tool] failed")
        return _json_error(f"FX macro risk overlay failed: {exc}")

    return result.model_dump_json()


if __name__ == "__main__":
    logger.info("Starting FX Agent MCP server on stdio")
    mcp.run()
