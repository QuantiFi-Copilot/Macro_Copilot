"""
mcp_server.py — MCP Server for the Bond Futures Sub-Agent
=========================================================

Exposes the bond-futures domain tools to the orchestrator via stdio
MCP. Each tool has flat scalar parameters; Pydantic validation happens
inside. Lazy engine singleton. Logging to stderr.

V1 ships MONITORS ONLY (per ADR 0013)
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
   signal).
3. ``scan_bond_futures_extremes_tool``        — morning bond-futures
   sweep by absolute z-score across the universe (price LEVEL,
   1-day price CHANGE, volume LEVEL, OI LEVEL — four metrics, top-N
   per metric). Replaces the Phase-4 inter-commodity DV01-weighted
   spread stack per ADR 0013 V1 scope.

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
from datetime import date
from pathlib import Path

from typing import Optional

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
from rates_agent.bond_futures.tools.futures_volume_oi import (  # noqa: E402
    CONFIG_PATH as FUTURES_VOLUME_OI_CONFIG_PATH,
    FuturesVolumeOIInput,
    calculate_futures_volume_oi,
)
from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (  # noqa: E402
    CONFIG_PATH as SCAN_BOND_FUTURES_EXTREMES_CONFIG_PATH,
    ScanBondFuturesExtremesInput,
    calculate_scan_bond_futures_extremes,
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
# TOOL 2: get_futures_volume_oi
# ===========================================================================
@mcp.tool()
def get_futures_volume_oi_tool(
    curve_family: str,
    contract_code: str,
    lookback_days: int = 365,
) -> str:
    """Get the current rolling-generic bond-futures daily volume + end-
    of-day open interest, plus 1-day ΔOI, rolling 252-day OI z-score,
    trailing 252-day OI range (high / low / percentile), and rolling
    22-day volume mean / max. The snapshot carries the per-contract
    ``contract_size`` so consumers can convert contract counts to
    notional.

    Use this tool when the user asks about:
    - Bond-futures volume       (e.g. "Where's TY1 volume today?",
                                       "JB1 volume vs trend?")
    - Bond-futures open interest (e.g. "Is RX1 OI elevated?",
                                       "How stretched is US1 positioning?")
    - 1-day open-interest moves  (e.g. "ΔOI on TY1 yesterday?")
    - OI extremes                (e.g. "Is FV1 OI at a 1-year high?")

    Do NOT use this tool for:
    - Policy / STIR futures (SFR / ER / SFI) → use the policy_futures
      agent's volume/OI tool.
    - Cash sovereign positioning → cash sovereigns do not have a
      desk-recognised open-interest object; this tool is futures-
      specific.
    - Front-back OI migration as a positioning signal: that is a
      cross-contract concept (front - next OI delta) and is NOT a
      single-contract primitive. The scan_bond_futures_extremes tool
      surfaces extreme readings across the bond-futures universe;
      cross-contract roll-pressure primitives are Phase-4 work.

    ALWAYS preserve the methodology_disclosure field when relaying the
    snapshot to the user — P5 (honest disclosure) requires both the
    rolling-generic-OI caveat AND the explicit OI z-score lookback
    window to be carried forward (per the catalog's methodology
    guardrail on this primitive).

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
        (default 365). Does NOT control the OI z-score window
        (config-locked at 252), the OI trailing range window (locked
        at 252), or the volume short-context window (locked at 22).
    """
    try:
        params = FuturesVolumeOIInput(
            curve_family=curve_family,
            contract_code=contract_code,
            lookback_days=lookback_days,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_futures_volume_oi_tool] input validation failed: %s", exc,
        )
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception(
            "[get_futures_volume_oi_tool] failed to connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). load_tool_config caches by path, so this
    # is a free lookup after the first call within the MCP subprocess's
    # lifetime. Mirrors sovereign get_yield_levels_tool /
    # get_futures_price_level_tool exactly.
    try:
        fvoi_config = load_tool_config(FUTURES_VOLUME_OI_CONFIG_PATH)
        result = calculate_futures_volume_oi(
            engine=engine, params=params, config=fvoi_config,
        )
    except Exception as exc:
        logger.exception(
            "[get_futures_volume_oi_tool] unhandled error for %s %s",
            params.curve_family, params.contract_code,
        )
        return json.dumps(
            {"error": f"get_futures_volume_oi_tool failed for "
             f"{params.curve_family} {params.contract_code}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_futures_volume_oi_tool] tool call complete: %s %s → %s",
        params.curve_family, params.contract_code, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series before returning to the LLM (frontend REST
    # path returns the full payload). The LLM doesn't need every
    # historical row to answer "where's TY1 OI?" — but it MUST see
    # ``methodology_disclosure`` so the P5 caveat + OI-z-score-window
    # disclosure is propagated.
    llm_response: dict = {
        k: v for k, v in result.items() if k != "time_series"
    }
    ts_rows = len(result.get("time_series", []) or [])
    if ts_rows:
        logger.info(
            "[get_futures_volume_oi_tool] withheld %d time_series rows from LLM context.",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 3: scan_bond_futures_extremes
# ===========================================================================
@mcp.tool()
def scan_bond_futures_extremes_tool(
    curve_families: str = "",
    top_n: int = 0,
    min_abs_z_score: float = -1.0,
    as_of_date: str = "",
) -> str:
    """Scan the bond-futures rolling-generic universe and rank stems by
    absolute 252-day z-score across four metrics — price LEVEL, 1-day
    price CHANGE ("Δ"), daily traded VOLUME, end-of-day OPEN-INTEREST
    LEVEL. Returns the top-N extremes PER METRIC, each tagged with the
    methodology disclosure.

    Use this tool when the user asks about:
    - Morning bond-futures sweeps        (e.g. "Where is the bond-
                                                futures universe
                                                stretched today?")
    - Universe-wide extreme moves         (e.g. "Biggest movers
                                                across UST + Bund
                                                futures today?")
    - Cross-contract volume / OI extremes (e.g. "Where is positioning
                                                most stretched in
                                                bond futures?")

    Do NOT use this tool for:
    - Policy / STIR futures (SFR / ER / SFI) — those route to the
      policy_futures agent's scan_policy_futures_extremes tool.
    - Inter-commodity DV01-weighted spreads (TY vs RX, US vs RX) —
      Phase-4 work gated on D-repo + D-deliverable; respond with
      out_of_scope rather than approximating with this monitor.
    - Tenor-anchored yield calls — bond-futures monitors live in
      price space; the CTD-implied yield path requires deliverable-
      basket + conversion-factor metadata that is Phase-4 work.
    - A per-contract deep-dive on one stem — use
      get_futures_price_level_tool / get_futures_volume_oi_tool for
      one (curve_family, contract_code) at a time.

    ALWAYS preserve the methodology_disclosure field — present on EVERY
    result row AND on the response — when relaying to the user. P5
    (honest disclosure) + the catalog's methodology guardrail require
    the universe-wide-sweep label, the explicit z-score lookback window,
    and the rolling-generic-price / non-DV01-spread caveats to be
    propagated.

    Parameters
    ----------
    curve_families : str, optional
        Comma-separated list of curve families to scan. Empty
        (default ``""``) = scan the full bond-futures universe
        (UST_FUT, DE_FUT, UK_FUT, JP_FUT, FR_FUT, IT_FUT, ES_FUT,
        CA_FUT, AU_FUT). Pass a CSV to narrow (e.g.
        ``"UST_FUT,DE_FUT"`` for a UST + Bund sweep). MCP exposes
        flat scalars, so the wrapper accepts a string and splits it
        before constructing the Pydantic input — mirrors the
        sovereign scan_extremes_tool convention. Policy-futures
        curves (SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT) are
        REFUSED at schema-validation time per the closed-family
        whitelist in config.yaml.
    top_n : int, optional
        Number of extreme stems to return PER METRIC. MCP exposes
        flat scalars, so the sentinel ``0`` (default) means "omit"
        and falls through to the YAML's ``default_top_n``
        convention (currently 5) — keeps the default YAML-locked
        per PR9 / PR10. Pass an integer in [1, 50] to override per
        query; the schema-layer bound rejects out-of-range values.
    min_abs_z_score : float, optional
        Minimum absolute z-score threshold for inclusion in the
        ranking. Applied PER METRIC — a stem may pass on price but
        fail on volume; it appears in the price top-N only. The
        sentinel ``-1.0`` (default) means "omit" and falls through
        to the YAML's ``default_min_abs_z_score`` convention
        (currently 1.5). Pass a non-negative float to override per
        query; the schema-layer ``ge=0.0`` bound stays as a
        structural invariant.
    as_of_date : str, optional
        ISO-format date (YYYY-MM-DD) anchoring the scan to a
        specific trading day. Empty (default ``""``) = anchor to
        the most-recent shared trading day across the fetched
        universe (max trade_date observed after per-stem
        alignment) — same as-of resolution pattern as
        get_futures_price_level_tool / get_futures_volume_oi_tool.
        The fetch window itself is methodology (derived from YAML:
        z_score_window_days × z_score_buffer_multiplier, currently
        252 × 1.5 = 378 calendar days) and is NOT an LLM input.
        Reviewer round-1 mandatory-fix: removed the previous
        ``lookback_days`` input (PR8 / OPR8 input-schema overreach)
        and replaced ``date.today()`` anchoring with this explicit
        anchor for determinism.
    """
    # Parse the comma-separated curve_families CSV into a list (or
    # None when empty). MCP exposes flat scalars, so we accept a
    # string and split it before constructing the Pydantic input —
    # mirrors the sovereign scan_extremes_tool convention. The empty-
    # string sentinel translates to None so the YAML's whitelist
    # full-universe default flows through.
    parsed_families = None
    if curve_families and curve_families.strip():
        parsed_families = [
            cf.strip() for cf in curve_families.split(",") if cf.strip()
        ]

    # Sentinel resolution: the MCP boundary cannot carry None for
    # int/float, so we use 0 / -1.0 sentinels for "omit". The
    # schema's ``ge=1`` / ``ge=0.0`` bounds would have rejected the
    # sentinel values themselves, so we translate to None BEFORE
    # constructing the Pydantic input — preserves the schema's None
    # → YAML-default fall-through, with no concrete defaults leaking
    # into the MCP wrapper (PR9 / PR10 mandatory-fix).
    top_n_arg: Optional[int] = top_n if top_n > 0 else None
    min_abs_z_score_arg: Optional[float] = (
        min_abs_z_score if min_abs_z_score >= 0.0 else None
    )

    # Parse the ISO-format as_of_date sentinel. Empty string ⇒ None
    # (compute resolves to the most-recent shared trading day).
    # Non-empty ⇒ parse with date.fromisoformat; a malformed value
    # raises and is caught by the ValidationError envelope below so
    # the LLM sees a clean error rather than a stacktrace.
    as_of_date_arg: Optional[date]
    if as_of_date and as_of_date.strip():
        try:
            as_of_date_arg = date.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            logger.warning(
                "[scan_bond_futures_extremes_tool] as_of_date parse "
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
        params = ScanBondFuturesExtremesInput(
            curve_families=parsed_families,
            top_n=top_n_arg,
            min_abs_z_score=min_abs_z_score_arg,
            as_of_date=as_of_date_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[scan_bond_futures_extremes_tool] input validation failed: %s", exc,
        )
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception(
            "[scan_bond_futures_extremes_tool] failed to connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable here (PR14). load_tool_config caches by path, so this
    # is a free lookup after the first call within the MCP subprocess's
    # lifetime. Mirrors get_futures_price_level_tool /
    # get_futures_volume_oi_tool exactly.
    try:
        scan_config = load_tool_config(SCAN_BOND_FUTURES_EXTREMES_CONFIG_PATH)
        result = calculate_scan_bond_futures_extremes(
            engine=engine, params=params, config=scan_config,
        )
    except Exception as exc:
        logger.exception(
            "[scan_bond_futures_extremes_tool] unhandled error for "
            "curve_families=%s",
            params.curve_families,
        )
        return json.dumps(
            {"error": f"scan_bond_futures_extremes_tool failed: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    n_rows = len(result.get("results", []) or [])
    # ``top_n`` / ``min_abs_z_score`` / ``as_of_date`` may all be None
    # (YAML-fallback / latest-shared-date sentinels); format with %r so
    # the log line is honest about which knobs the LLM actually set
    # versus which fell through to defaults.
    logger.info(
        "[scan_bond_futures_extremes_tool] tool call complete: "
        "curve_families=%s top_n=%r min_abs_z=%r as_of=%r → %s (%d rows)",
        params.curve_families, params.top_n, params.min_abs_z_score,
        params.as_of_date, status, n_rows,
    )

    return json.dumps(result, default=str)


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info(
        "Starting Bond Futures Sub-Agent MCP server (stdio transport)..."
    )
    mcp.run(transport="stdio")
