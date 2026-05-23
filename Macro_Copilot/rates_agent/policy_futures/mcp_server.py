"""
mcp_server.py — MCP Server for the Policy Futures (STIR strip) Sub-Agent
========================================================================

Exposes the policy-futures domain tools to the orchestrator via stdio
MCP. Each tool has flat scalar parameters; Pydantic validation happens
inside. Lazy engine singleton. Logging to stderr.

V1 SCAFFOLDING — no tools registered yet. The OpenClaw
primitive-automation factory registers tools here as it builds each
catalogued primitive (see ``Macro_Copilot/automation/primitive_automation/
primitive_catalog.yaml`` and the ``REPO_REFERENCE_MAP.md`` for the
mirror pattern from ``rates_agent/ois/mcp_server.py``).

Tool surface (planned, per the primitive catalog)
-------------------------------------------------
1. ``get_futures_price_level_tool``           — front-contract price +
   implied rate (= 100 − price) + Δ + 252d z.
2. ``build_futures_strip_snapshot_tool``      — all 8 strip positions
   side-by-side (price + implied rate + Δ + z + OI).
3. ``get_volume_open_interest_snapshot_tool`` — daily volume / OI /
   Δ-OI / OI z-score across the strip.
4. ``calculate_futures_calendar_spread_tool`` — same-curve implied-rate
   spread between two strip positions (e.g. SFR2 − SFR1).
5. ``calculate_futures_butterfly_simple_tool`` — three-point implied-rate
   butterfly, fixed 50-50 weighting.
6. ``calculate_futures_cross_market_spread_tool`` — matched-strip
   cross-CB implied-rate differential, with the benchmark-family
   mismatch caveat on the methodology card.
7. ``calculate_futures_pack_average_simple_tool`` — average implied
   rate across whites (positions 1-4) / reds (5-8). Ships with a
   ``PR11`` ``NotImplementedError`` refusal for
   ``curve_family = EUR_SHORT_RATE_FUT`` until ``policy_futures.yml``
   annotates ``delivery_month_type: quarterly | serial`` (Euribor strip
   mixes serial and quarterly contracts).
8. ``scan_policy_futures_extremes_tool`` — morning STIR sweep across
   the universe by absolute z-score.

Each tool description tells the LLM:
  (a) what the tool does
  (b) which user questions it should handle
  (c) which policy-futures curve_family values are valid
  (d) explicit "do NOT use for X" fences so the LLM doesn't route
      sovereign / OIS / bond-futures questions here

Conventions for this domain
---------------------------
- Quoted in PRICE; the desk-recognised read is the IMPLIED RATE
  (= 100 − price).
- Strip-position-keyed (``SFR1`` = front; ``SFR2..SFR8`` = quarterly
  forwards). NOT tenor-keyed — uses the strip-aware fetch helper in
  ``shared/analytics/rates_fetch.py``.
- SOFR / SONIA reference a compounded RFR (3-month look-back);
  Euribor (``EUR_SHORT_RATE_FUT``) references unsecured 3M term-Euribor
  — a structurally different rate object. Cross-CB spreads ALWAYS
  ship with the benchmark-family-mismatch disclosure on the methodology
  card (P5).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from rates_agent.policy_futures.tools.futures_price_level import (  # noqa: E402
    CONFIG_PATH as FUTURES_PRICE_LEVEL_CONFIG_PATH,
    FuturesPriceLevelInput,
    calculate_futures_price_level,
)
from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (  # noqa: E402
    CONFIG_PATH as VOLUME_OPEN_INTEREST_SNAPSHOT_CONFIG_PATH,
    VolumeOpenInterestSnapshotInput,
    calculate_volume_open_interest_snapshot,
)
from shared.config import load_tool_config  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.policy_futures.mcp_server")

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
    name="policy-futures-agent",
    instructions=(
        "You are the Policy Futures (STIR strip) Sub-Agent for a macro "
        "hedge-fund desk. You have access to tools that perform "
        "deterministic calculations over a TimescaleDB database of "
        "daily SOFR / Euribor / SONIA short-term-interest-rate futures "
        "price + volume + open-interest quotes. The strip is "
        "strip-position-keyed (SFR1 = front; SFR2..SFR8 = quarterly "
        "forwards) and quoted in PRICE; the desk-recognised read is "
        "the IMPLIED RATE = 100 − price. Use these tools to answer "
        "questions about the STIR strip's implied-rate levels, calendar "
        "spreads, simple butterflies, pack averages (whites / reds), "
        "cross-CB STIR spreads, volume / open-interest, and morning "
        "extreme scans. Never attempt the math yourself — always call "
        "a tool and relay its output. Do NOT route cash sovereign "
        "bonds, OIS swaps, or bond futures here — those belong to the "
        "sovereign_bonds, ois, and bond_futures agents respectively. "
        "When relaying STIR output, ALWAYS use the phrase 'implied "
        "rate' (NOT 'yield' and NOT 'par rate'); on cross-CB spreads, "
        "ALWAYS preserve the benchmark-family-mismatch disclosure "
        "(SOFR / SONIA = compounded RFR; Euribor = unsecured 3M term-"
        "Euribor — structurally different rate objects). The "
        "futures_pack_average_simple tool refuses EUR_SHORT_RATE_FUT "
        "with a controlled error envelope until the playbook annotates "
        "delivery_month_type (Euribor strip mixes serial and quarterly "
        "contracts); SOFR + SONIA pack averages build cleanly."
    ),
)


# ===========================================================================
# TOOL REGISTRATIONS
# ===========================================================================
# Tools are registered here as the OpenClaw primitive-automation factory
# builds each catalogued primitive. Mirror the per-tool wrapper pattern
# from ``rates_agent/bond_futures/mcp_server.py``:
#
#   1. Import CONFIG_PATH + Input schema + compute function from
#      ``rates_agent.policy_futures.tools.<tool_name>``.
#   2. Add a ``@mcp.tool()`` wrapper that validates input via the
#      Pydantic schema, loads the bundled config via
#      ``load_tool_config(CONFIG_PATH)``, calls the compute function
#      with ``config=`` passed explicitly, and returns the JSON
#      response with LLM-non-friendly fields (full ``time_series``,
#      ``panel`` payloads, underscore-prefixed internals) stripped.
#   3. Add a corresponding entry to
#      ``rates_agent/policy_futures/tools/schemas/__init__.py`` so the
#      schemas hub stays a stable import surface.


# ===========================================================================
# TOOL 1: get_futures_price_level (policy_futures strip-position-keyed)
# ===========================================================================
@mcp.tool()
def get_futures_price_level_tool(
    curve_family: str,
    strip_position: int,
    lookback_days: int = 365,
    as_of_date: str = "",
    field_name: str = "",
) -> str:
    """Get the current policy-futures strip-position raw price + desk-
    recognised IMPLIED RATE (PERCENT), plus 1-day raw-price and
    implied-rate changes, rolling 252d z-score of the implied rate,
    trailing 252d high / low / mid range on BOTH the implied-rate AND
    the raw-price axis, and percentile rank of the current implied
    rate. The snapshot carries the as_of-bounded SCD2 per-strip
    disclosure (underlying_contract_code, security_name, expiry_date,
    contract_size, tick_size, tick_value, inverse_priced flag,
    short_rate_regime label, quote_units) so downstream consumers
    cannot misread the raw quote as a rate.

    Use this tool when the user asks about:
    - Front STIR price / implied rate (e.g. "Where's SFR1?",
                                        "Implied rate on SFR2?")
    - STIR strip moves                (e.g. "How much did the front
                                        SOFR contract move?")
    - STIR strip range / extremes    (e.g. "Is ER3 at a 1-year low
                                        in implied rate?")

    Do NOT use this tool for:
    - Bond futures (TY1 / RX1 / JB1 / ...) → use the bond_futures
      agent's get_futures_price_level_tool.
    - OIS rates (the underlying short-rate curve) → use the ois
      agent's calculate_ois_rate_level_tool.
    - Cash sovereign yields → use the sovereign_bonds agent's
      get_yield_levels_tool.
    - Pack averages / calendar spreads / butterflies on the strip —
      those are separate primitives in this domain (the
      policy_futures automation builds them on later catalog entries).

    ALWAYS preserve the methodology_disclosure field when relaying the
    snapshot to the user — P5 (honest disclosure) requires the
    rolling-generic-strip-read + regime label + inverse-pricing rule
    caveats to be carried forward.

    Parameters
    ----------
    curve_family : str
        Policy-futures curve family. V1 universe: 'SOFR_FUT' (US Fed
        SOFR strip, RFR regime), 'EUR_SHORT_RATE_FUT' (ECB Euribor
        strip, IBOR regime), 'SONIA_FUT' (BOE SONIA strip, RFR
        regime).
    strip_position : int
        1-based strip position. 1 = front contract; 2..8 = quarterly
        forwards down the strip in the V1 universe (whites = 1-4,
        reds = 5-8).
    lookback_days : int, optional
        Calendar days of history for the observation_count window
        (default 365). Does NOT control the rolling z-score window
        (config-locked at 252) or the trailing range window (locked
        at 252).
    as_of_date : str, optional
        ISO-format date (YYYY-MM-DD) anchoring the snapshot to a
        specific trading day. Empty (default ``""``) = anchor at the
        universe's last observed ``trade_date`` for the requested
        strip (post-fetch data-max anchor). An as_of_date BEYOND the
        universe's last observed ``trade_date`` returns the
        documented controlled-error envelope; an as_of_date WITHIN
        the universe range produces a DETERMINISTIC snapshot (same
        as_of + same DB state ⇒ same numbers).
    field_name : str, optional
        Bloomberg field mnemonic. Leave as the default empty string
        ``""`` to use the bundled ``default_price_field`` convention
        from futures_price_level/config.yaml (currently 'PX_LAST').
        Pass an explicit field name to override per call. Mirrors the
        empty-string sentinel pattern used by sovereign
        get_yield_levels_tool / bond_futures get_futures_price_level_tool
        so the YAML default actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's ``default_price_field``.
    # Without this, the LLM omitting field_name would always hit a
    # hardcoded default regardless of what the YAML says — same
    # shadowing pattern fixed for sovereign curve_move_classifier in
    # commit b2605ee.
    field_name_arg = field_name if field_name else None

    # Parse the ISO-format as_of_date sentinel. Empty string ⇒ None
    # (compute resolves to the most-recent universe trade_date for
    # the strip). Malformed value raises a ValidationError envelope
    # below — LLM sees a clean error rather than a stacktrace.
    as_of_date_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_date_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            logger.warning(
                "[get_futures_price_level_tool] as_of_date parse "
                "failed: %s", exc,
            )
            return json.dumps(
                {
                    "error": (
                        f"Invalid as_of_date {as_of_date!r}: must be "
                        f"ISO YYYY-MM-DD (e.g. '2026-04-30'). Detail: "
                        f"{exc}"
                    )
                },
                default=str,
            )
    else:
        as_of_date_arg = None

    try:
        params = FuturesPriceLevelInput(
            curve_family=curve_family,
            strip_position=strip_position,
            lookback_days=lookback_days,
            as_of_date=as_of_date_arg,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_futures_price_level_tool] input validation failed: %s",
            exc,
        )
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception(
            "[get_futures_price_level_tool] failed to connect to "
            "TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). load_tool_config caches by path, so this
    # is a free lookup after the first call within the MCP subprocess's
    # lifetime. Mirrors bond_futures get_futures_price_level_tool
    # exactly.
    try:
        fpl_config = load_tool_config(FUTURES_PRICE_LEVEL_CONFIG_PATH)
        result = calculate_futures_price_level(
            engine=engine, params=params, config=fpl_config,
        )
    except Exception as exc:
        logger.exception(
            "[get_futures_price_level_tool] unhandled error for %s "
            "strip_position=%d",
            params.curve_family, params.strip_position,
        )
        return json.dumps(
            {"error": f"get_futures_price_level_tool failed for "
             f"{params.curve_family} strip_position="
             f"{params.strip_position}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_futures_price_level_tool] tool call complete: %s "
        "strip_position=%d → %s",
        params.curve_family, params.strip_position, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series before returning to the LLM (frontend REST
    # path returns the full payload). The LLM doesn't need every
    # historical row to answer "where's SFR1?" — but it MUST see
    # ``methodology_disclosure`` so the P5 caveat is propagated.
    llm_response: dict = {
        k: v for k, v in result.items() if k != "time_series"
    }
    ts_rows = len(result.get("time_series", []) or [])
    if ts_rows:
        logger.info(
            "[get_futures_price_level_tool] withheld %d time_series "
            "rows from LLM context.",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 2: get_volume_open_interest_snapshot
#         (policy_futures strip-position-keyed)
# ===========================================================================
@mcp.tool()
def get_volume_open_interest_snapshot_tool(
    curve_family: str,
    strip_position: int,
    lookback_days: int = 365,
    as_of_date: str = "",
) -> str:
    """Get the current policy-futures strip-position daily volume +
    end-of-day open interest, plus 1-day ΔOI, rolling 252-day OI
    z-score, trailing 252-day OI range (high / low / percentile), and
    rolling 22-day volume mean / max. The snapshot carries the as_of-
    bounded SCD2 per-strip disclosure (underlying_contract_code,
    security_name, expiry_date, contract_size) so consumers can
    convert contract counts to notional.

    Use this tool when the user asks about:
    - Policy-futures STIR strip volume     (e.g. "Where's SFR1 volume?",
                                            "ER2 volume vs trend?")
    - Policy-futures STIR strip open interest (e.g. "Is SFR1 OI
                                            elevated?",
                                            "SFI strip positioning?")
    - 1-day open-interest moves            (e.g. "ΔOI on SFR1
                                            yesterday?")
    - OI extremes on the STIR strip        (e.g. "Is ER3 OI at a
                                            1-year high?")

    Do NOT use this tool for:
    - Bond futures (TY1 / RX1 / JB1 / ...) → use the bond_futures
      agent's get_futures_volume_oi_tool.
    - Cash sovereign positioning → cash sovereigns do not have a
      desk-recognised open-interest object; this tool is futures-
      specific.
    - Implied-rate level / range / z-score on the strip — that is the
      sibling policy_futures get_futures_price_level_tool (price +
      implied-rate axis). This tool is positioning + flow only.
    - Front-back OI migration as a positioning signal: that is a
      cross-strip concept and is NOT a single-strip primitive
      (future work per ADR 0011 V1 scope).

    ALWAYS preserve the methodology_disclosure field when relaying the
    snapshot to the user — P5 (honest disclosure) requires both the
    rolling-generic-strip OI caveat AND the explicit OI z-score
    lookback window to be carried forward (per the catalog's
    standardness guardrail on this primitive).

    Parameters
    ----------
    curve_family : str
        Policy-futures curve family. V1 universe: 'SOFR_FUT' (US Fed
        SOFR strip), 'EUR_SHORT_RATE_FUT' (ECB Euribor strip),
        'SONIA_FUT' (BOE SONIA strip).
    strip_position : int
        1-based strip position. 1 = front contract; 2..8 = quarterly
        forwards down the strip in the V1 universe (whites = 1-4,
        reds = 5-8).
    lookback_days : int, optional
        Calendar days of history for the observation_count window
        (default 365). Does NOT control the OI z-score window
        (config-locked at 252), the OI trailing range window (locked
        at 252), or the volume short-context window (locked at 22).
    as_of_date : str, optional
        ISO-format date (YYYY-MM-DD) anchoring the snapshot to a
        specific trading day. Empty (default ``""``) = anchor at the
        universe's last observed ``trade_date`` for the requested
        strip (post-fetch data-max anchor). An as_of_date BEYOND the
        universe's last observed ``trade_date`` returns the
        documented controlled-error envelope; an as_of_date WITHIN
        the universe range produces a DETERMINISTIC snapshot (same
        as_of + same DB state ⇒ same numbers).
    """
    # Parse the ISO-format as_of_date sentinel. Empty string ⇒ None
    # (compute resolves to the most-recent universe trade_date for
    # the strip). Malformed value raises a ValidationError envelope
    # below — LLM sees a clean error rather than a stacktrace.
    as_of_date_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_date_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            logger.warning(
                "[get_volume_open_interest_snapshot_tool] as_of_date "
                "parse failed: %s", exc,
            )
            return json.dumps(
                {
                    "error": (
                        f"Invalid as_of_date {as_of_date!r}: must be "
                        f"ISO YYYY-MM-DD (e.g. '2026-04-30'). Detail: "
                        f"{exc}"
                    )
                },
                default=str,
            )
    else:
        as_of_date_arg = None

    try:
        params = VolumeOpenInterestSnapshotInput(
            curve_family=curve_family,
            strip_position=strip_position,
            lookback_days=lookback_days,
            as_of_date=as_of_date_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_volume_open_interest_snapshot_tool] input "
            "validation failed: %s",
            exc,
        )
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception(
            "[get_volume_open_interest_snapshot_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). load_tool_config caches by path, so this
    # is a free lookup after the first call within the MCP subprocess's
    # lifetime. Mirrors get_futures_price_level_tool exactly.
    try:
        vois_config = load_tool_config(
            VOLUME_OPEN_INTEREST_SNAPSHOT_CONFIG_PATH,
        )
        result = calculate_volume_open_interest_snapshot(
            engine=engine, params=params, config=vois_config,
        )
    except Exception as exc:
        logger.exception(
            "[get_volume_open_interest_snapshot_tool] unhandled error "
            "for %s strip_position=%d",
            params.curve_family, params.strip_position,
        )
        return json.dumps(
            {"error": f"get_volume_open_interest_snapshot_tool failed "
             f"for {params.curve_family} strip_position="
             f"{params.strip_position}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_volume_open_interest_snapshot_tool] tool call complete: "
        "%s strip_position=%d → %s",
        params.curve_family, params.strip_position, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series before returning to the LLM (frontend REST
    # path returns the full payload). The LLM doesn't need every
    # historical row to answer "where's SFR1 OI?" — but it MUST see
    # ``methodology_disclosure`` so the P5 caveat + OI-z-score-window
    # disclosure is propagated.
    llm_response: dict = {
        k: v for k, v in result.items() if k != "time_series"
    }
    ts_rows = len(result.get("time_series", []) or [])
    if ts_rows:
        logger.info(
            "[get_volume_open_interest_snapshot_tool] withheld %d "
            "time_series rows from LLM context.",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info(
        "Starting Policy Futures Sub-Agent MCP server (stdio transport)..."
    )
    mcp.run(transport="stdio")
