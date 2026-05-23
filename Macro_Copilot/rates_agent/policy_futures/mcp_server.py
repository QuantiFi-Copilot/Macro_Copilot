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
from rates_agent.policy_futures.tools.futures_butterfly_simple import (  # noqa: E402
    CONFIG_PATH as FUTURES_BUTTERFLY_SIMPLE_CONFIG_PATH,
    FuturesButterflySimpleInput,
    calculate_futures_butterfly_simple,
)
from rates_agent.policy_futures.tools.futures_calendar_spread import (  # noqa: E402
    CONFIG_PATH as FUTURES_CALENDAR_SPREAD_CONFIG_PATH,
    FuturesCalendarSpreadInput,
    calculate_futures_calendar_spread,
)
from rates_agent.policy_futures.tools.futures_cross_market_spread import (  # noqa: E402
    CONFIG_PATH as FUTURES_CROSS_MARKET_SPREAD_CONFIG_PATH,
    FuturesCrossMarketSpreadInput,
    calculate_futures_cross_market_spread,
)
from rates_agent.policy_futures.tools.futures_pack_average_simple import (  # noqa: E402
    CONFIG_PATH as FUTURES_PACK_AVERAGE_SIMPLE_CONFIG_PATH,
    FuturesPackAverageSimpleInput,
    calculate_futures_pack_average_simple,
)
from rates_agent.policy_futures.tools.futures_price_level import (  # noqa: E402
    CONFIG_PATH as FUTURES_PRICE_LEVEL_CONFIG_PATH,
    FuturesPriceLevelInput,
    calculate_futures_price_level,
)
from rates_agent.policy_futures.tools.futures_strip_snapshot import (  # noqa: E402
    CONFIG_PATH as FUTURES_STRIP_SNAPSHOT_CONFIG_PATH,
    FuturesStripSnapshotInput,
    calculate_futures_strip_snapshot,
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
# TOOL 3: get_futures_calendar_spread
#         (policy_futures strip-position-keyed)
# ===========================================================================
@mcp.tool()
def get_futures_calendar_spread_tool(
    curve_family: str,
    strip_position_short: int,
    strip_position_long: int,
    lookback_days: int = 365,
    as_of_date: str = "",
    field_name: str = "",
) -> str:
    """Get the current policy-futures same-curve calendar spread on
    the implied-rate axis (PERCENT POINTS) between two strip-position
    slots, plus the parallel raw-price spread, 1-day deltas on each
    axis, rolling 252-day z-score of the implied-rate-spread series,
    trailing 252-day high / low / mid range on the implied-rate-spread
    axis, and percentile rank of the current spread. The snapshot
    carries the as_of-bounded SCD2 per-leg disclosure
    (contract_code_short / contract_code_long /
    underlying_contract_code_short / underlying_contract_code_long /
    security_name_short / security_name_long / expiry_date_short /
    expiry_date_long, inverse_priced flag, short_rate_regime label).
    Sign convention: short_leg minus long_leg (fronter minus backer).

    Use this tool when the user asks about:
    - STIR calendar / strip spreads     (e.g. "Where's SFR1-SFR2?",
                                         "ER1-ER4 spread?",
                                         "front-back SOFR strip
                                         slope?")
    - Calendar-spread extremes         (e.g. "Is the SFR1-SFR2
                                         spread at a 1-year high in
                                         implied rate?",
                                         "Z-score on the ER1-ER2
                                         calendar?")
    - Calendar-spread day-on-day moves (e.g. "How much did SFR2-SFR3
                                         move yesterday?")

    Do NOT use this tool for:
    - Bond futures calendar spreads (TY1-TY2 / RX1-RX2 / ...) →
      bond_futures domain, separate primitive.
    - Cross-CB STIR spreads (SOFR vs SONIA / SOFR vs Euribor) → that
      is the sibling ``futures_cross_market_spread`` primitive
      (separate; matched-strip cross-family differentials with the
      benchmark-family-mismatch disclosure).
    - Three-point STIR butterflies (e.g. SFR1-2*SFR2+SFR3) → that is
      the sibling ``futures_butterfly_simple`` primitive (separate;
      same-curve curvature).
    - Single-leg outright price / implied rate / range / z-score on
      one strip slot — that is the sibling
      ``get_futures_price_level_tool`` (price + implied-rate axis on
      ONE strip position).
    - Meeting-by-meeting policy-path decomposition (FOMC / ECB / BOE
      per-meeting implied-step view) → NOT a primitive in this build.
      This tool is the calendar-spread (strip-slope) read on rolling-
      generic strip-slot series, NOT the per-meeting decomposition.

    ALWAYS preserve the methodology_disclosure field when relaying the
    snapshot to the user — P5 (honest disclosure) requires the sign
    convention, the regime label, the inverse-pricing rule, the
    z-score lookback window, and the rolling-generic-strip-spread
    scope-limit caveats to be carried forward.

    Parameters
    ----------
    curve_family : str
        Policy-futures curve family. V1 universe: 'SOFR_FUT' (US Fed
        SOFR strip, RFR regime), 'EUR_SHORT_RATE_FUT' (ECB Euribor
        strip, IBOR regime), 'SONIA_FUT' (BOE SONIA strip, RFR
        regime).
    strip_position_short : int
        1-based strip position of the SHORT (fronter) leg. 1 = front
        contract; 2..8 = quarterly forwards down the strip in the V1
        universe. Must be strictly less than ``strip_position_long``.
    strip_position_long : int
        1-based strip position of the LONG (backer) leg. Must be
        strictly greater than ``strip_position_short``.
    lookback_days : int, optional
        Calendar days of history for the observation_count window
        (default 365). Does NOT control the rolling z-score window
        (config-locked at 252) or the trailing range window (locked
        at 252).
    as_of_date : str, optional
        ISO-format date (YYYY-MM-DD) anchoring the snapshot to a
        specific trading day. Empty (default ``""``) = anchor at the
        universe's last observed ``trade_date`` where BOTH legs are
        observed (post-fetch data-max anchor). An as_of_date BEYOND
        the universe's last observed ``trade_date`` on EITHER leg
        returns the documented controlled-error envelope; an
        as_of_date WITHIN the universe range produces a DETERMINISTIC
        snapshot (same as_of + same DB state ⇒ same numbers).
    field_name : str, optional
        Bloomberg field mnemonic. Leave as the default empty string
        ``""`` to use the bundled ``default_price_field`` convention
        from futures_calendar_spread/config.yaml (currently
        'PX_LAST'). Pass an explicit field name to override per call.
        Mirrors the empty-string sentinel pattern used by sovereign
        get_yield_levels_tool / policy_futures
        get_futures_price_level_tool so the YAML default actually
        flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's ``default_price_field``.
    field_name_arg = field_name if field_name else None

    # Parse the ISO-format as_of_date sentinel. Empty string ⇒ None
    # (compute resolves to the most-recent universe trade_date for
    # the legs). Malformed value raises a ValidationError envelope
    # below — LLM sees a clean error rather than a stacktrace.
    as_of_date_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_date_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            logger.warning(
                "[get_futures_calendar_spread_tool] as_of_date parse "
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
        params = FuturesCalendarSpreadInput(
            curve_family=curve_family,
            strip_position_short=strip_position_short,
            strip_position_long=strip_position_long,
            lookback_days=lookback_days,
            as_of_date=as_of_date_arg,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_futures_calendar_spread_tool] input validation "
            "failed: %s",
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
            "[get_futures_calendar_spread_tool] failed to connect to "
            "TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). load_tool_config caches by path, so this
    # is a free lookup after the first call within the MCP subprocess's
    # lifetime. Mirrors get_futures_price_level_tool /
    # get_volume_open_interest_snapshot_tool exactly.
    try:
        fcs_config = load_tool_config(FUTURES_CALENDAR_SPREAD_CONFIG_PATH)
        result = calculate_futures_calendar_spread(
            engine=engine, params=params, config=fcs_config,
        )
    except Exception as exc:
        logger.exception(
            "[get_futures_calendar_spread_tool] unhandled error for "
            "%s (%d, %d)",
            params.curve_family,
            params.strip_position_short, params.strip_position_long,
        )
        return json.dumps(
            {"error": f"get_futures_calendar_spread_tool failed for "
             f"{params.curve_family} "
             f"({params.strip_position_short},"
             f"{params.strip_position_long}): {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_futures_calendar_spread_tool] tool call complete: %s "
        "(%d, %d) → %s",
        params.curve_family,
        params.strip_position_short, params.strip_position_long, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series before returning to the LLM (frontend REST
    # path returns the full payload). The LLM doesn't need every
    # historical row to answer "where's SFR1-SFR2?" — but it MUST see
    # ``methodology_disclosure`` so the P5 caveats (sign convention,
    # regime label, scope-limits) are propagated.
    llm_response: dict = {
        k: v for k, v in result.items() if k != "time_series"
    }
    ts_rows = len(result.get("time_series", []) or [])
    if ts_rows:
        logger.info(
            "[get_futures_calendar_spread_tool] withheld %d "
            "time_series rows from LLM context.",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 4: get_futures_butterfly_simple
#         (policy_futures strip-position-keyed)
# ===========================================================================
@mcp.tool()
def get_futures_butterfly_simple_tool(
    curve_family: str,
    strip_position_wing_short: int,
    strip_position_body: int,
    strip_position_wing_long: int,
    lookback_days: int = 365,
    as_of_date: str = "",
    field_name: str = "",
) -> str:
    """Get the current policy-futures same-curve simple butterfly on
    the implied-rate axis (PERCENT POINTS) for a three-leg
    (wing_short, body, wing_long) strip-position triple, using the
    FIXED 50-50 simple-butterfly weighting
    (``body − 0.5 * (wing_short + wing_long)``). Plus the three
    per-leg current implied rates, 1-day raw-subtraction change on
    the butterfly axis, rolling 252-day z-score of the butterfly
    series, trailing 252-day high / low / mid range, and percentile
    rank of the current butterfly. The snapshot carries the
    as_of-bounded SCD2 per-leg disclosure (contract_code_* /
    underlying_contract_code_* / security_name_* / expiry_date_*,
    inverse_priced flag, short_rate_regime label). Sign convention:
    body rate minus wing-rate average; positive = belly cheap.

    Use this tool when the user asks about:
    - STIR / policy-futures butterflies   (e.g. "Where's
                                           SFR1-SFR2-SFR3?",
                                           "ER1-ER2-ER4 butterfly?")
    - Butterfly extremes                  (e.g. "Is the
                                           SFR1-SFR2-SFR3 butterfly
                                           at a 1-year high?",
                                           "Z-score on the
                                           SFR1-SFR4-SFR8 butterfly?")
    - Butterfly day-on-day moves          (e.g. "How much did the
                                           ER1-ER2-ER4 butterfly move
                                           yesterday?")

    Do NOT use this tool for:
    - Bond futures butterflies → bond_futures domain, separate
      primitive.
    - Cross-CB STIR butterflies (mixing SOFR / SONIA / Euribor) →
      not a V1 primitive.
    - DV01-neutral / regression-fitted butterflies → those weighting
      variants ship as separate primitives in a future build (this
      tool refuses them via the methodology card).
    - Meeting-by-meeting policy-path butterflies (FOMC / ECB / BOE
      per-meeting implied-step view) → NOT a primitive in this
      build. This tool is the simple butterfly on rolling-generic
      strip-slot series.
    - Calendar spreads on the strip (2-leg) → the sibling
      ``get_futures_calendar_spread_tool``.
    - Single-leg outright price / implied rate → the sibling
      ``get_futures_price_level_tool``.

    ALWAYS preserve the methodology_disclosure field when relaying
    the snapshot to the user — P5 (honest disclosure) requires the
    sign convention, the fixed 50-50 weighting, the regime label,
    the inverse-pricing rule, the z-score lookback window, AND the
    DV01-neutral / meeting-path scope-limit refusals to be carried
    forward.

    Parameters
    ----------
    curve_family : str
        Policy-futures curve family. V1 universe: 'SOFR_FUT' (US Fed
        SOFR strip, RFR regime), 'EUR_SHORT_RATE_FUT' (ECB Euribor
        strip, IBOR regime), 'SONIA_FUT' (BOE SONIA strip, RFR
        regime).
    strip_position_wing_short : int
        1-based strip position of the SHORT wing (fronter wing).
        Must be strictly less than ``strip_position_body``.
    strip_position_body : int
        1-based strip position of the BODY (belly). Must be strictly
        greater than ``strip_position_wing_short`` AND strictly less
        than ``strip_position_wing_long``.
    strip_position_wing_long : int
        1-based strip position of the LONG wing (backer wing). Must
        be strictly greater than ``strip_position_body``.
    lookback_days : int, optional
        Calendar days of history for the observation_count window
        (default 365). Does NOT control the rolling z-score window
        (config-locked at 252) or the trailing range window (locked
        at 252).
    as_of_date : str, optional
        ISO-format date (YYYY-MM-DD) anchoring the snapshot. Empty
        (default ``""``) = anchor at the universe's last observed
        ``trade_date`` where ALL THREE legs are observed. An
        as_of_date BEYOND the universe's last observed ``trade_date``
        on ANY leg returns the documented controlled-error envelope.
    field_name : str, optional
        Bloomberg field mnemonic. Leave as the default empty string
        ``""`` to use the bundled ``default_price_field`` convention
        from futures_butterfly_simple/config.yaml (currently
        'PX_LAST'). Pass an explicit field name to override per call.
    """
    field_name_arg = field_name if field_name else None

    as_of_date_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_date_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            logger.warning(
                "[get_futures_butterfly_simple_tool] as_of_date parse "
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
        params = FuturesButterflySimpleInput(
            curve_family=curve_family,
            strip_position_wing_short=strip_position_wing_short,
            strip_position_body=strip_position_body,
            strip_position_wing_long=strip_position_wing_long,
            lookback_days=lookback_days,
            as_of_date=as_of_date_arg,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_futures_butterfly_simple_tool] input validation "
            "failed: %s",
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
            "[get_futures_butterfly_simple_tool] failed to connect to "
            "TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). Mirrors the sibling
    # get_futures_calendar_spread_tool exactly.
    try:
        fbs_config = load_tool_config(FUTURES_BUTTERFLY_SIMPLE_CONFIG_PATH)
        result = calculate_futures_butterfly_simple(
            engine=engine, params=params, config=fbs_config,
        )
    except Exception as exc:
        logger.exception(
            "[get_futures_butterfly_simple_tool] unhandled error for "
            "%s (%d, %d, %d)",
            params.curve_family,
            params.strip_position_wing_short,
            params.strip_position_body,
            params.strip_position_wing_long,
        )
        return json.dumps(
            {"error": f"get_futures_butterfly_simple_tool failed for "
             f"{params.curve_family} "
             f"({params.strip_position_wing_short},"
             f"{params.strip_position_body},"
             f"{params.strip_position_wing_long}): {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_futures_butterfly_simple_tool] tool call complete: %s "
        "(%d, %d, %d) → %s",
        params.curve_family,
        params.strip_position_wing_short,
        params.strip_position_body,
        params.strip_position_wing_long, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series + canonical series before returning to the
    # LLM (frontend REST path returns the full payload). The LLM
    # doesn't need every historical row — but it MUST see
    # ``methodology_disclosure`` so the P5 caveats are propagated.
    llm_response: dict = {
        k: v for k, v in result.items()
        if k not in ("time_series", "time_series_butterfly", "time_series_zscore")
    }
    ts_rows = len(result.get("time_series", []) or [])
    if ts_rows:
        logger.info(
            "[get_futures_butterfly_simple_tool] withheld %d "
            "time_series rows from LLM context.",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 5: get_futures_cross_market_spread
#         (policy_futures matched-strip cross-market differential)
# ===========================================================================
@mcp.tool()
def get_futures_cross_market_spread_tool(
    curve_family_a: str,
    curve_family_b: str,
    strip_position: int,
    lookback_days: int = 365,
    as_of_date: str = "",
    field_name: str = "",
) -> str:
    """Get the current policy-futures matched-strip cross-market
    implied-rate differential on the implied-rate axis (PERCENT
    POINTS) for a single strip position across TWO different curve
    families (e.g. SOFR_FUT vs SONIA_FUT strip 1 → SFR1 − SFI1, or
    SOFR_FUT vs EUR_SHORT_RATE_FUT strip 4 → SFR4 − ER4). Sign
    convention (wire-frozen): rate_A − rate_B where A is the
    requested ``curve_family_a``; swapping the inputs flips the sign
    by construction.

    Plus the two per-leg current implied rates, 1-day raw-subtraction
    change on the spread axis, rolling 252-day z-score of the spread
    series, trailing 252-day high / low / mid range, and percentile
    rank of the current spread. The snapshot carries the as_of-
    bounded SCD2 per-leg disclosure (contract_code_a /
    contract_code_b / underlying_contract_code_* / security_name_* /
    expiry_date_* / inverse_priced_* / short_rate_regime_*) with
    BOTH per-leg regime labels surfaced even for mixed RFR/IBOR
    pairs (catalog guardrail — no pack-average collapse).

    Output is a RAW cross-market implied-rate differential — NOT
    basis-adjusted (cross-currency basis NOT netted) and NOT beta-
    adjusted (regression residual NOT computed). Basis-adjusted and
    beta-adjusted variants are planned-extension territory per PR11
    and ship as separate primitives.

    Use this tool when the user asks about:
    - Cross-CB STIR spreads             (e.g. "Where's SFR1-SFI1?",
                                          "SFR4-ER4 spread?",
                                          "SOFR vs Euribor matched
                                          strip 2?")
    - Cross-CB STIR spread extremes    (e.g. "Is the SFR1-SFI1
                                          spread at a 1-year high?",
                                          "Z-score on the SFR2-ER2
                                          cross-CB spread?")
    - Cross-CB STIR day-on-day moves   (e.g. "How much did the
                                          SOFR-Euribor matched-strip
                                          spread move yesterday?")

    Do NOT use this tool for:
    - Same-curve calendar spreads (e.g. SFR1-SFR2) → that is the
      sibling ``get_futures_calendar_spread_tool``.
    - Three-leg butterflies on one curve → that is the sibling
      ``get_futures_butterfly_simple_tool``.
    - Single-leg outright price / implied rate → the sibling
      ``get_futures_price_level_tool``.
    - Bond-futures cross-market spreads (TY1 vs RX1 / TY1 vs JB1 /
      ...) → bond_futures domain, separate primitive.
    - Cash-sovereign / OIS cross-market spreads → sovereign_bonds /
      ois agents have their own cross_market_spread primitives.
    - Basis-adjusted / beta-adjusted cross-market spreads → those
      weighting variants ship as separate primitives in a future
      build (this tool refuses them via the methodology card).
    - Meeting-by-meeting policy-path cross-CB decomposition (FOMC
      vs ECB / FOMC vs BOE per-meeting implied-step view) → NOT a
      primitive in this build. This tool is the cross-market spread
      on rolling-generic strip-slot series.

    ALWAYS preserve the methodology_disclosure field when relaying
    the snapshot to the user — P5 (honest disclosure) requires the
    wire-frozen A − B sign convention with specific labels, the
    per-leg short-rate regime labels (with explicit mixed-regime
    labelling), the inverse-pricing rule per leg, the z-score
    lookback window, the RAW-differential label, AND the explicit
    refusal of pack-average collapse to be carried forward.

    Parameters
    ----------
    curve_family_a : str
        First (numerator / 'A') policy-futures curve family. V1
        universe: 'SOFR_FUT' (US Fed SOFR strip, RFR regime),
        'EUR_SHORT_RATE_FUT' (ECB Euribor strip, IBOR regime),
        'SONIA_FUT' (BOE SONIA strip, RFR regime).
    curve_family_b : str
        Second (denominator / 'B') policy-futures curve family. Must
        differ from ``curve_family_a``. Same closed enum.
    strip_position : int
        1-based strip position on BOTH legs (matched-strip read).
        1 = front contract on each market; 2..8 = quarterly forwards
        down each strip in the V1 universe (whites = 1-4,
        reds = 5-8).
    lookback_days : int, optional
        Calendar days of history for the observation_count window
        (default 365). Does NOT control the rolling z-score window
        (config-locked at 252) or the trailing range window (locked
        at 252).
    as_of_date : str, optional
        ISO-format date (YYYY-MM-DD) anchoring the snapshot. Empty
        (default ``""``) = anchor at the universe's last observed
        ``trade_date`` where BOTH legs are observed. An as_of_date
        BEYOND the universe's last observed ``trade_date`` on EITHER
        leg returns the documented controlled-error envelope; an
        as_of_date WITHIN the universe range produces a DETERMINISTIC
        snapshot (same as_of + same DB state ⇒ same numbers).
    field_name : str, optional
        Bloomberg field mnemonic. Leave as the default empty string
        ``""`` to use the bundled ``default_price_field`` convention
        from futures_cross_market_spread/config.yaml (currently
        'PX_LAST'). Pass an explicit field name to override per call.
    """
    field_name_arg = field_name if field_name else None

    as_of_date_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_date_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            logger.warning(
                "[get_futures_cross_market_spread_tool] as_of_date "
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
        params = FuturesCrossMarketSpreadInput(
            curve_family_a=curve_family_a,
            curve_family_b=curve_family_b,
            strip_position=strip_position,
            lookback_days=lookback_days,
            as_of_date=as_of_date_arg,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_futures_cross_market_spread_tool] input validation "
            "failed: %s",
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
            "[get_futures_cross_market_spread_tool] failed to connect "
            "to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). Mirrors the sibling
    # get_futures_calendar_spread_tool / get_futures_butterfly_simple_tool
    # exactly.
    try:
        fxms_config = load_tool_config(
            FUTURES_CROSS_MARKET_SPREAD_CONFIG_PATH,
        )
        result = calculate_futures_cross_market_spread(
            engine=engine, params=params, config=fxms_config,
        )
    except Exception as exc:
        logger.exception(
            "[get_futures_cross_market_spread_tool] unhandled error "
            "for %s vs %s strip_position=%d",
            params.curve_family_a, params.curve_family_b,
            params.strip_position,
        )
        return json.dumps(
            {"error": f"get_futures_cross_market_spread_tool failed "
             f"for {params.curve_family_a} vs "
             f"{params.curve_family_b} strip_position="
             f"{params.strip_position}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_futures_cross_market_spread_tool] tool call complete: "
        "%s vs %s strip_position=%d → %s",
        params.curve_family_a, params.curve_family_b,
        params.strip_position, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series + canonical series before returning to the
    # LLM (frontend REST path returns the full payload). The LLM
    # doesn't need every historical row — but it MUST see
    # ``methodology_disclosure`` so the P5 caveats are propagated.
    llm_response: dict = {
        k: v for k, v in result.items()
        if k not in ("time_series", "time_series_spread", "time_series_zscore")
    }
    ts_rows = len(result.get("time_series", []) or [])
    if ts_rows:
        logger.info(
            "[get_futures_cross_market_spread_tool] withheld %d "
            "time_series rows from LLM context.",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 6: get_futures_strip_snapshot
#         (policy_futures whole-strip snapshot across all 8 positions)
# ===========================================================================
@mcp.tool()
def get_futures_strip_snapshot_tool(
    curve_family: str,
    as_of_date: str = "",
    last_price_field_name: str = "",
    open_interest_field_name: str = "",
) -> str:
    """Get the current policy-futures whole-strip snapshot for ONE
    curve_family — one row per configured strip position (V1 default:
    positions 1..8). Each row carries the per-leg raw_price (in the
    contract's quote space), desk-recognised IMPLIED RATE in PERCENT,
    1-day raw-subtraction change on the implied-rate axis, rolling
    252-day z-score of the per-leg implied-rate level, open_interest
    in CONTRACTS, the as_of-bounded SCD2 disclosure block
    (underlying_contract_code, security_name, expiry_date,
    contract_size), AND a per-row methodology card disclosing the
    short-rate regime (RFR / IBOR) + implied-rate conversion rule.
    The snapshot also carries an output-level methodology_disclosure
    naming the curve_family, regime label, z-score lookback, and
    rolling-generic-strip scope-limit caveat.

    Use this tool when the user asks about:
    - STIR strip shape / slope        (e.g. "Where's the SOFR strip?",
                                        "Is the SOFR strip steep?",
                                        "What does the Euribor strip
                                        look like?")
    - All-strip positioning summary   (e.g. "Show me SFR1..SFR8 with
                                        OI", "Whole SONIA strip
                                        snapshot")
    - Strip-wide screen + comparison  (e.g. "Where are the SOFR strip
                                        z-scores?", "Which Euribor
                                        position has the biggest
                                        1-day move?")

    Do NOT use this tool for:
    - A SINGLE strip slot's price + implied rate + 252d range +
      percentile — that is the sibling ``get_futures_price_level_tool``
      (single-strip-position read with the full range + percentile
      output).
    - A 2-leg same-curve calendar spread (e.g. SFR1-SFR2) → use the
      sibling ``get_futures_calendar_spread_tool``.
    - A 3-leg simple butterfly → use the sibling
      ``get_futures_butterfly_simple_tool``.
    - A cross-CB matched-strip spread (e.g. SOFR vs Euribor on one
      strip position) → use the sibling
      ``get_futures_cross_market_spread_tool``.
    - Per-strip volume + OI history with z-scores → use the sibling
      ``get_volume_open_interest_snapshot_tool``.
    - Bond futures (TY1 / RX1 / JB1 / ...) → bond_futures domain has
      its own snapshot scanner; this tool is policy-futures (STIR)
      only.
    - Cash sovereign yield curves → use the sovereign_bonds agent's
      yield_levels / curve_spread tools.

    ALWAYS preserve the methodology_disclosure field when relaying
    the snapshot to the user — P5 (honest disclosure) requires the
    rolling-generic-strip-snapshot scope-limit + regime label +
    inverse-pricing rule caveats to be carried forward. Each row
    also carries its OWN ``row_methodology_card``; do NOT strip the
    per-row card when relaying a single row from the snapshot.

    Parameters
    ----------
    curve_family : str
        Policy-futures curve family. V1 universe: 'SOFR_FUT' (US Fed
        SOFR strip, RFR regime), 'EUR_SHORT_RATE_FUT' (ECB Euribor
        strip, IBOR regime), 'SONIA_FUT' (BOE SONIA strip, RFR
        regime). The closed Literal in the input schema rejects
        anything else with a clean validation error.
    as_of_date : str, optional
        ISO-format date (YYYY-MM-DD) anchoring the snapshot to a
        specific trading day. Empty (default ``""``) = anchor at the
        universe's last observed ``trade_date`` where ALL configured
        strip positions have a value after cleaning + intersection
        (post-fetch data-max anchor). An as_of_date BEYOND the
        universe's last observed ``trade_date`` on ANY configured
        strip position returns the documented controlled-error
        envelope; an as_of_date WITHIN the universe range produces a
        DETERMINISTIC snapshot (same as_of + same DB state ⇒ same
        numbers).
    last_price_field_name : str, optional
        Bloomberg field mnemonic for the per-leg price series. Leave
        as the default empty string ``""`` to use the bundled
        ``default_price_field`` convention from
        futures_strip_snapshot/config.yaml (currently 'PX_LAST').
        Mirrors the empty-string sentinel pattern used by the
        sibling tools so the YAML default actually flows through.
    open_interest_field_name : str, optional
        Bloomberg field mnemonic for the per-leg open-interest
        series. Leave as the default empty string ``""`` to use the
        bundled ``default_open_interest_field`` convention from
        futures_strip_snapshot/config.yaml (currently 'OPEN_INT').
    """
    # Translate the empty-string sentinels into None so the schema +
    # compute layers resolve against the YAML's defaults. Without
    # this, the LLM omitting field_name would always hit a hardcoded
    # default regardless of what the YAML says — same shadowing
    # pattern fixed for sovereign curve_move_classifier in commit
    # b2605ee.
    last_price_field_arg = (
        last_price_field_name if last_price_field_name else None
    )
    open_interest_field_arg = (
        open_interest_field_name if open_interest_field_name else None
    )

    # Parse the ISO-format as_of_date sentinel. Empty string ⇒ None
    # (compute resolves to the most-recent universe trade_date across
    # all configured strip positions). Malformed value raises a
    # ValidationError envelope below — LLM sees a clean error.
    as_of_date_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_date_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            logger.warning(
                "[get_futures_strip_snapshot_tool] as_of_date parse "
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
        params = FuturesStripSnapshotInput(
            curve_family=curve_family,
            as_of_date=as_of_date_arg,
            last_price_field_name=last_price_field_arg,
            open_interest_field_name=open_interest_field_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_futures_strip_snapshot_tool] input validation "
            "failed: %s",
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
            "[get_futures_strip_snapshot_tool] failed to connect to "
            "TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). load_tool_config caches by path, so this
    # is a free lookup after the first call within the MCP subprocess's
    # lifetime. Mirrors the sibling tools exactly.
    try:
        fss_config = load_tool_config(FUTURES_STRIP_SNAPSHOT_CONFIG_PATH)
        result = calculate_futures_strip_snapshot(
            engine=engine, params=params, config=fss_config,
        )
    except Exception as exc:
        logger.exception(
            "[get_futures_strip_snapshot_tool] unhandled error for "
            "%s",
            params.curve_family,
        )
        return json.dumps(
            {"error": f"get_futures_strip_snapshot_tool failed for "
             f"{params.curve_family}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_futures_strip_snapshot_tool] tool call complete: %s "
        "→ %s",
        params.curve_family, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # The snapshot list is small (1 row per strip position; V1 = 8
    # rows max) so we DO surface it to the LLM verbatim. The LLM
    # needs the per-row payload to answer "where is the SOFR strip?"
    # style questions, AND it MUST see ``methodology_disclosure`` so
    # the P5 caveats are propagated. No history series to withhold
    # here — the snapshot is by design a single-anchor read.
    return json.dumps(result, default=str)


# ===========================================================================
# TOOL 7: get_futures_pack_average_simple
#         (policy_futures whites / reds pack-average implied rate)
# ===========================================================================
@mcp.tool()
def get_futures_pack_average_simple_tool(
    curve_family: str,
    pack: str,
    lookback_days: int = 365,
    as_of_date: str = "",
    field_name: str = "",
) -> str:
    """Get the current policy-futures whites or reds pack-average
    implied rate (simple arithmetic mean across the four pack-member
    strip slots) on ONE curve_family. Plus the four per-leg current
    implied rates, 1-day raw-subtraction change on the pack-average
    axis, rolling 252-day z-score of the pack-average series,
    trailing 252-day high / low / mid range, and percentile rank of
    the current pack average. The snapshot carries the as_of-bounded
    SCD2 per-leg disclosure (contract_codes / underlying_contract_codes
    / security_names / expiry_dates per pack member, inverse_priced
    flag, short_rate_regime label). Weighting: simple arithmetic
    mean (each pack member 1/4 = 0.25).

    Use this tool when the user asks about:
    - STIR pack averages              (e.g. "Where are the SOFR
                                       whites?", "SONIA reds
                                       average?", "Front-year SOFR
                                       implied rate average?")
    - Pack-average extremes           (e.g. "Are the SOFR whites at
                                       a 1-year high?", "Z-score on
                                       the SONIA reds pack?")
    - Pack-average day-on-day moves   (e.g. "How much did the SOFR
                                       whites move yesterday?")

    Do NOT use this tool for:
    - Bond futures pack averages (TY / RX / JB strips) → bond_futures
      domain, separate primitive.
    - Cross-CB pack-average spreads (SOFR whites vs SONIA whites) →
      NOT a V1 primitive.
    - Three-leg STIR butterflies → use the sibling
      ``get_futures_butterfly_simple_tool``.
    - Two-leg same-curve calendar spreads → use the sibling
      ``get_futures_calendar_spread_tool``.
    - Single-leg outright price / implied rate → use the sibling
      ``get_futures_price_level_tool``.
    - Whole-strip snapshot (1..8 side-by-side) → use the sibling
      ``get_futures_strip_snapshot_tool``.
    - Duration-weighted / DV01-weighted pack averages → those
      weighting variants ship as separate primitives in a future
      build (this tool refuses them via the methodology card).
    - Meeting-by-meeting policy-path pack averages (FOMC / ECB / BOE
      per-meeting implied-step view) → NOT a primitive in this
      build.
    - EUR_SHORT_RATE_FUT (Euribor) pack averages — this tool REFUSES
      EUR_SHORT_RATE_FUT with a controlled error envelope until the
      playbook annotates per-row ``delivery_month_type`` metadata
      (the Euribor strip mixes serial and quarterly contracts at the
      front; ADR 0011 V1 scope). SOFR + SONIA pack averages build
      cleanly.

    ALWAYS preserve the methodology_disclosure field when relaying
    the snapshot to the user — P5 (honest disclosure) requires the
    arithmetic-mean weighting, the regime label, the inverse-pricing
    rule, the z-score lookback window, AND the explicit refusal of
    duration-weighted / meeting-path / CTD-of-OIS variants to be
    carried forward.

    Parameters
    ----------
    curve_family : str
        Policy-futures curve family. V1 universe: 'SOFR_FUT' (US Fed
        SOFR strip, RFR regime), 'EUR_SHORT_RATE_FUT' (ECB Euribor
        strip — REFUSED at compute time per ADR 0011 V1 scope),
        'SONIA_FUT' (BOE SONIA strip, RFR regime).
    pack : str
        Which pack to average: 'whites' (strip positions 1-4, front
        year) or 'reds' (strip positions 5-8, second year). The
        strip-position ranges are YAML-locked STIR conventions and
        are NOT user-overridable.
    lookback_days : int, optional
        Calendar days of history for the observation_count window
        (default 365). Does NOT control the rolling z-score window
        (config-locked at 252) or the trailing range window (locked
        at 252).
    as_of_date : str, optional
        ISO-format date (YYYY-MM-DD) anchoring the snapshot to a
        specific trading day. Empty (default ``""``) = anchor at the
        universe's last observed ``trade_date`` where ALL FOUR pack
        legs are observed (post-fetch data-max anchor). An
        as_of_date BEYOND the universe's last observed
        ``trade_date`` on ANY pack leg returns the documented
        controlled-error envelope; an as_of_date WITHIN the universe
        range produces a DETERMINISTIC snapshot (same as_of + same
        DB state ⇒ same numbers).
    field_name : str, optional
        Bloomberg field mnemonic. Leave as the default empty string
        ``""`` to use the bundled ``default_price_field`` convention
        from futures_pack_average_simple/config.yaml (currently
        'PX_LAST'). Pass an explicit field name to override per
        call. Mirrors the empty-string sentinel pattern used by the
        sibling tools so the YAML default actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_price_field``.
    field_name_arg = field_name if field_name else None

    # Parse the ISO-format as_of_date sentinel. Empty string ⇒ None
    # (compute resolves to the most-recent universe trade_date
    # across all four pack legs). Malformed value raises a clean
    # ValidationError envelope below.
    as_of_date_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_date_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            logger.warning(
                "[get_futures_pack_average_simple_tool] as_of_date "
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
        params = FuturesPackAverageSimpleInput(
            curve_family=curve_family,
            pack=pack,
            lookback_days=lookback_days,
            as_of_date=as_of_date_arg,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_futures_pack_average_simple_tool] input validation "
            "failed: %s",
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
            "[get_futures_pack_average_simple_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). Mirrors the sibling
    # get_futures_butterfly_simple_tool exactly. Catches the
    # ADR-0011 V1 EUR_SHORT_RATE_FUT NotImplementedError refusal
    # separately so the LLM sees a clean {"error": "..."} envelope
    # naming the missing delivery_month_type metadata rather than a
    # stack trace.
    try:
        fpas_config = load_tool_config(
            FUTURES_PACK_AVERAGE_SIMPLE_CONFIG_PATH,
        )
        result = calculate_futures_pack_average_simple(
            engine=engine, params=params, config=fpas_config,
        )
    except NotImplementedError as exc:
        logger.warning(
            "[get_futures_pack_average_simple_tool] ADR 0011 V1 "
            "refusal for %s pack=%s: %s",
            params.curve_family, params.pack, exc,
        )
        return json.dumps(
            {"error": str(exc)},
            default=str,
        )
    except Exception as exc:
        logger.exception(
            "[get_futures_pack_average_simple_tool] unhandled error "
            "for %s pack=%s",
            params.curve_family, params.pack,
        )
        return json.dumps(
            {"error": f"get_futures_pack_average_simple_tool failed "
             f"for {params.curve_family} pack={params.pack}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_futures_pack_average_simple_tool] tool call complete: "
        "%s pack=%s → %s",
        params.curve_family, params.pack, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series + canonical series before returning to the
    # LLM (frontend REST path returns the full payload). The LLM
    # doesn't need every historical row — but it MUST see
    # ``methodology_disclosure`` so the P5 caveats are propagated.
    llm_response: dict = {
        k: v for k, v in result.items()
        if k not in (
            "time_series",
            "time_series_pack_average",
            "time_series_zscore",
        )
    }
    ts_rows = len(result.get("time_series", []) or [])
    if ts_rows:
        logger.info(
            "[get_futures_pack_average_simple_tool] withheld %d "
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
