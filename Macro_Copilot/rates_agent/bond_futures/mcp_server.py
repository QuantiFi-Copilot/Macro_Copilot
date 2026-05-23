"""
mcp_server.py — MCP Server for the Bond Futures Sub-Agent
=========================================================

Exposes the bond-futures domain tools to the orchestrator via stdio
MCP. Each tool has flat scalar parameters; Pydantic validation happens
inside. Lazy engine singleton. Logging to stderr.

V1 ships MONITORS ONLY (per ADR 0011)
-------------------------------------
The desk-recognised RV stack on bond futures (CTD identification,
gross / net basis, implied repo rate, DV01-weighted inter-commodity
spreads, cross-country DV01 + FX-adjusted spreads) is PHASE-4 work,
gated on D-repo + D-deliverable data ingestion that has not landed
yet. This V1 ships:

1. ``get_futures_price_level_tool``           — front-month bond-futures
   price + Δ + 252d range / z-score.
2. ``get_futures_volume_oi_tool``             — daily volume / OI /
   Δ-OI / OI z-score (front-back OI migration is the positioning
   signal).  *(pending — factory build_order 2)*
3. ``scan_bond_futures_extremes_tool``        — morning bond-futures
   sweep by absolute z-score across the universe.  *(pending —
   factory build_order 3)*

Each tool's methodology card MUST disclose: "this is CTD-of-rolling-
generic price, not a clean tenor-anchored yield; for CTD-implied
yield see the Phase-4 stack" (P5 honest disclosure).

The TY1 / UXY1 (10Y) and US1 / WN1 (30Y) ambiguity is resolved at
fetch time via the ``contract_code`` disambiguator (TD#11 — already
in the playbook). NB the enriched view's ``contract_code`` column
COALESCEs the SCD2 history's per-window underlying contract code
(TYH6 / TYM6 / ...) onto the master stem, so the read-side fetcher
joins ``market_data_daily`` to ``instrument_master`` directly and
filters on the master ``contract_code`` stem — see
``shared.analytics.rates_fetch.fetch_rolling_generic_series``.

Conventions for this domain
---------------------------
- Quoted in PRICE. The price IS the headline read for monitors; do
  NOT back-of-the-envelope a yield from it.
- ``contract_code`` (TY1 / UXY1 / US1 / WN1 / RX1 / JB1 / ...) is the
  canonical instrument identifier; ``curve_family`` (UST_FUT, DE_FUT,
  ...) groups them, and ``contract_code`` disambiguates within a
  ``(curve_family, tenor)`` pair (TD#11).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from rates_agent.bond_futures.tools.futures_price_level import (  # noqa: E402
    CONFIG_PATH as FUTURES_PRICE_LEVEL_CONFIG_PATH,
    FuturesPriceLevelInput,
    calculate_futures_price_level,
)
from shared.config import load_tool_config  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.bond_futures.mcp_server")

_engine = None


def _get_engine():
    """Return (and cache) a SQLAlchemy engine using env-var config."""
    global _engine
    if _engine is None:
        logger.info("Initializing database engine...")
        _engine = get_db_engine()
        logger.info("Database engine ready.")
    return _engine


mcp = FastMCP(
    name="bond-futures-agent",
    instructions=(
        "You are the Bond Futures Sub-Agent for a macro hedge-fund "
        "desk. You have access to tools that perform deterministic "
        "calculations over a TimescaleDB database of daily sovereign-"
        "bond futures price + volume + open-interest quotes (TY1, "
        "UXY1, US1, WN1, RX1, JB1, etc. across UST_FUT / DE_FUT / "
        "UK_FUT / JP_FUT and analogues). V1 SHIPS MONITORS ONLY — "
        "front-month price level, volume / open-interest, and a "
        "morning extreme scan. Use these tools to answer questions "
        "about price levels, Δ-OI / volume / OI z-scores, and "
        "morning sweeps across the bond-futures universe. Never "
        "attempt the math yourself — always call a tool and relay its "
        "output. When relaying price, ALWAYS preserve the methodology "
        "disclosure: 'this is rolling-generic price; the CTD-implied "
        "yield is not yet a primitive in this build' (P5). Do NOT "
        "back-of-the-envelope a yield from the price; the CTD path "
        "requires deliverable-basket + conversion-factor metadata "
        "that is Phase-4 work gated on D-repo + D-deliverable. Do "
        "NOT route policy / STIR futures (SFR / ER / SFI) here — "
        "those belong to the policy_futures agent. Do NOT route cash "
        "sovereign yield questions here — those belong to the "
        "sovereign_bonds agent. If the user asks about CTD-implied "
        "yields, basis, or DV01-weighted RV, respond with "
        "out_of_scope and explain those primitives are pending the "
        "deliverable basket + conversion factor + repo data "
        "ingestion."
    ),
)


# ===========================================================================
# TOOL 1: get_futures_price_level
# ===========================================================================
@mcp.tool()
def get_futures_price_level_tool(
    curve_family: str,
    contract_code: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the current front-month rolling-generic bond-futures price
    level, plus daily / weekly / monthly price-unit changes, 1-year
    z-score, and trailing 252-day range (high / low / percentile).
    The snapshot carries the per-contract ``quote_units`` and
    ``contract_size`` so downstream consumers cannot misread (e.g.) a
    TY1 ``110.45`` as a yield.

    Use this tool when the user asks about:
    - Bond-futures price levels    (e.g. "Where's TY1?", "RX1 right now?")
    - Bond-futures price moves     (e.g. "How much has US1 moved this week?")
    - Bond-futures range extremes  (e.g. "Is JB1 at a 1-year high?")

    Do NOT use this tool for:
    - Policy / STIR futures (SFR / ER / SFI) → use the policy_futures
      agent's get_futures_price_level_tool.
    - Cash sovereign yields (UST 10Y, Bund 10Y) → use the
      sovereign_bonds agent's get_yield_levels_tool.
    - CTD-implied yields, basis, or DV01-weighted RV → those are
      Phase-4 work gated on D-repo + D-deliverable. Respond with
      out_of_scope rather than approximating with this monitor.

    ALWAYS preserve the methodology_disclosure field when relaying the
    snapshot to the user — P5 (honest disclosure) requires the rolling-
    generic-price caveat to be carried forward.

    Parameters
    ----------
    curve_family : str
        Bond-futures curve family. Examples: 'UST_FUT', 'DE_FUT',
        'UK_FUT', 'JP_FUT', 'FR_FUT', 'IT_FUT', 'ES_FUT', 'CA_FUT',
        'AU_FUT'.
    contract_code : str
        Rolling-generic stem from the bond_futures playbook universe —
        the canonical disambiguator per TD#11. Examples: 'TY1', 'UXY1',
        'US1', 'WN1', 'TU1', 'FV1', 'RX1', 'UB1', 'DU1', 'OE1', 'G1',
        'JB1', 'OAT1', 'IK1', 'BTS1', 'KOA1', 'CN1', 'YM1', 'XM1'.
    lookback_days : int, optional
        Calendar days of history for the observation_count window
        (default 365).
    field_name : str, optional
        Bloomberg field mnemonic. Leave as the default empty string
        ""  to use the bundled ``default_price_field`` convention
        from futures_price_level/config.yaml (currently 'PX_LAST').
        Pass an explicit field name to override per call. Mirrors the
        empty-string sentinel pattern used by sovereign
        get_yield_levels_tool / ois calculate_ois_rate_level_tool so
        the YAML default actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's ``default_price_field``.
    # Without this, the LLM omitting field_name would always hit a
    # hardcoded default regardless of what the YAML says — same
    # shadowing pattern fixed for sovereign curve_move_classifier in
    # commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = FuturesPriceLevelInput(
            curve_family=curve_family,
            contract_code=contract_code,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_futures_price_level_tool] input validation failed: %s", exc,
        )
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception(
            "[get_futures_price_level_tool] failed to connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). load_tool_config caches by path, so this
    # is a free lookup after the first call within the MCP subprocess's
    # lifetime. Mirrors sovereign get_yield_levels_tool exactly.
    try:
        fpl_config = load_tool_config(FUTURES_PRICE_LEVEL_CONFIG_PATH)
        result = calculate_futures_price_level(
            engine=engine, params=params, config=fpl_config,
        )
    except Exception as exc:
        logger.exception(
            "[get_futures_price_level_tool] unhandled error for %s %s",
            params.curve_family, params.contract_code,
        )
        return json.dumps(
            {"error": f"get_futures_price_level_tool failed for "
             f"{params.curve_family} {params.contract_code}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_futures_price_level_tool] tool call complete: %s %s → %s",
        params.curve_family, params.contract_code, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series before returning to the LLM (frontend REST
    # path returns the full payload). The LLM doesn't need every
    # historical price to answer "where's TY1?" — but it MUST see
    # ``methodology_disclosure`` so the P5 caveat is propagated.
    llm_response: dict = {
        k: v for k, v in result.items() if k != "time_series"
    }
    ts_rows = len(result.get("time_series", []) or [])
    if ts_rows:
        logger.info(
            "[get_futures_price_level_tool] withheld %d time_series rows from LLM context.",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# REMAINING TOOL REGISTRATIONS
# ===========================================================================
# get_futures_volume_oi_tool + scan_bond_futures_extremes_tool land
# here as the OpenClaw primitive-automation factory builds them
# (per the catalog's ``build_order``).


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info(
        "Starting Bond Futures Sub-Agent MCP server (stdio transport)..."
    )
    mcp.run(transport="stdio")
