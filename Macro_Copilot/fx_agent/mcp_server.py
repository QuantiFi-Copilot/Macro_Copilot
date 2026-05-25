from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from fx_agent.forwards.tools.fx_carry import (  # noqa: E402
    CONFIG_PATH as FX_CARRY_CONFIG_PATH,
    FXCarryInput,
    get_fx_carry,
)
from fx_agent.spot.tools.scanner import run_fx_scanner  # noqa: E402
from fx_agent.spot.tools.schemas import FXScannerInput  # noqa: E402
from fx_agent.spot.tools.spot_levels import (  # noqa: E402
    CONFIG_PATH as FX_SPOT_LEVEL_CONFIG_PATH,
    FXSpotLevelInput,
    get_fx_spot_level,
)
from shared.config import load_tool_config  # noqa: E402


logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fx_agent.mcp_server")

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        logger.info("Initializing database engine...")
        _engine = get_db_engine()
        logger.info("Database engine ready.")
    return _engine


mcp = FastMCP(
    name="fx-agent",
    instructions=(
        "You are the FX Agent for a macro desk. You have access to "
        "deterministic tools over stored FX spot and forward observations. "
        "Use tools for market data and calculations; do not invent levels."
    ),
)


@mcp.tool()
def get_fx_spot_level_tool(
    pair: str,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Get a single FX spot pair snapshot and rolling context."""
    try:
        params = FXSpotLevelInput(
            pair=pair,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_SPOT_LEVEL_CONFIG_PATH)
        result = get_fx_spot_level(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX spot level failed for %s", pair)
        return json.dumps({"error": f"FX spot level failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def scan_fx_spot_tool(
    market_scope: Optional[str] = None,
    top_n: int = 10,
    field_name: str = "PX_LAST",
) -> str:
    """Scan FX spot pairs for stretched z-score / momentum signals."""
    try:
        params = FXScannerInput(
            market_scope=market_scope,
            top_n=top_n,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        result = run_fx_scanner(_get_engine(), params)
    except Exception as exc:
        logger.exception("FX spot scanner failed")
        return json.dumps({"error": f"FX spot scanner failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def calculate_fx_carry_tool(tenor: str = "1M") -> str:
    """Rank FX pairs by same-tenor forward-points carry."""
    try:
        params = FXCarryInput(tenor=tenor)
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_CARRY_CONFIG_PATH)
        result = get_fx_carry(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX carry failed for tenor=%s", tenor)
        return json.dumps({"error": f"FX carry failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


if __name__ == "__main__":
    logger.info("Starting FX Agent MCP server (stdio transport)...")
    mcp.run(transport="stdio")
