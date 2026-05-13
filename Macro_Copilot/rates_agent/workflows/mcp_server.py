"""mcp_server.py — MCP Server for the Rates-Agent Workflow Templates
=====================================================================

Exposes WORKFLOW TEMPLATES (DAG-shaped analyses) to the orchestrator
via stdio MCP — distinct from the per-domain primitive MCP servers
in ``rates_agent/sovereign_bonds/mcp_server.py`` and
``rates_agent/ois/mcp_server.py`` (which expose individual primitives
like ``calculate_curve_spread_tool``).

Why a separate MCP server
-------------------------
- Templates are CROSS-DOMAIN (e.g. ``event_study`` Q1 binds the
  cross-domain swap_spread primitive AND a sovereign yield_levels
  primitive; ``regime_conditioned_relationship`` Q2 binds a sovereign
  yield, an OIS rate, and an OIS curve_spread regime classifier).
  Splitting templates across the per-domain primitive servers would
  force awkward duplication.
- Keeping the template surface separate from the primitive surface
  preserves the architectural distinction "primitive vs workflow"
  declared in ``docs/architecture/workflow_architecture.md``.

Tool surface (V1)
-----------------
- ``list_workflows()``                          → JSON list of TemplateCards
- ``describe_workflow_template(template_id)``   → JSON of one card with
                                                  full slot schema
- ``event_study_workflow(...)``                 → bind + execute the
                                                  event_study template
- ``regime_conditioned_relationship_workflow(...)`` → same for the
                                                  regime archetype

Adding a new template = one new ``@mcp.tool()`` wrapper here, identical
shape to the existing two.  All runtime logic (slot binding,
validation, execution, summarization, error envelopes) lives in
``rates_agent.workflows._runner``; the wrappers in this module are
deliberately thin so the MCP transport surface and the in-process
runtime are exercised by the same code path.

Hand-coded wrappers (NOT generated)
------------------------------------
Each per-template MCP tool is a hand-coded function with a docstring
the LLM sees as the MCP tool description (when to pick the template,
what each slot means, examples of valid bindings).  Same discipline
as the existing primitive MCP wrappers in
``rates_agent/{ois,sovereign_bonds}/mcp_server.py``.

Output shape
------------
Every tool returns a JSON string envelope:
  - on success: ``{"ok": true, "template_id": "...", "terminal_artifact": {...}, "workflow_lineage_summary": "..."}``
  - on failure: ``{"ok": false, "template_id": "...", "error": "<diagnostic>"}``
See ``rates_agent.workflows._runner.summarize_terminal`` for the
terminal-artifact summary shape.

Stdio entry point
-----------------
``python -m rates_agent.workflows.mcp_server`` — same pattern as the
per-domain primitive servers.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Make sure the project root is on sys.path BEFORE any in-repo imports
# below.  Mirrors the bootstrap pattern in the per-domain MCP servers.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from database.database import get_db_engine  # noqa: E402

# Importing each template's package triggers ``register_template`` via
# their ``__init__.py``, populating the substrate's process-wide
# template registry.  Required so ``list_workflows()`` sees them.
#
# NOTE: ``rates_agent.workflows.backtest`` is intentionally NOT imported
# here.  The backtest archetype is paused from the LLM-facing surface
# until the data substrate carries the fields it needs to produce
# economically-meaningful trade P&L (MOD_DUR_MID + CUR_CPN + DAY_CNT_DES
# + PX_DIRTY for DV01 weighting; CPI-U NSA + seasonal factors for TIPS
# carry; OTR history; true O/N OIS; bid/ask).  Without those, the V1
# backtest is a yield-change distribution mislabelled as a P&L
# backtest.  The template, operators (construct_trades / evaluate_trades
# / summarize_trades), and tests remain in the repo and run under the
# existing test gauntlet — they just don't reach the LLM router or the
# MCP catalogue.  Re-enable by uncommenting the import below once the
# data prerequisites land.
#
#   import rates_agent.workflows.backtest  # noqa: F401, E402
import rates_agent.workflows.event_study  # noqa: F401, E402
import rates_agent.workflows.regime_conditioned_relationship  # noqa: F401, E402

from rates_agent.workflows._runner import (  # noqa: E402
    describe_workflow_card,
    list_workflow_cards,
    run_template,
)
from shared.workflow import known_template_ids  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.workflows.mcp_server")


# ---------------------------------------------------------------------------
# Lazy DB engine (mirrors the per-domain MCP servers)
# ---------------------------------------------------------------------------
_engine = None


def _get_engine():
    """Return (and cache) a SQLAlchemy engine using env-var config.
    The workflow templates' primitive resolver dispatches to the same
    rates primitive callables the per-domain MCP servers use, so the
    engine acquisition path is identical.
    """
    global _engine
    if _engine is None:
        logger.info("Initializing database engine...")
        _engine = get_db_engine()
        logger.info("Database engine ready.")
    return _engine


def _engine_or_error_envelope(template_id: str):
    """Return ``(engine, None)`` on success, ``(None, json_envelope)``
    on DB-init failure.  Centralizes the "engine acquisition" failure
    path so each per-template wrapper has the same external contract."""
    try:
        return _get_engine(), None
    except Exception as exc:
        logger.exception("[%s] DB engine init failed", template_id)
        envelope = {
            "ok": False,
            "template_id": template_id,
            "error": f"Database connection failed: {exc}",
        }
        return None, json.dumps(envelope, default=str)


mcp = FastMCP(
    name="workflows-agent",
    instructions=(
        "You are the Rates-Agent Workflow Specialist.  You have access "
        "to a small catalogue of standard workflow templates that "
        "answer DAG-shaped desk questions (event studies, regime-"
        "conditioned relationship analyses, etc.) deterministically "
        "via composed primitives + operators.  Use ``list_workflows`` "
        "to enumerate the catalogue, ``describe_workflow_template`` "
        "to inspect one template's slot schema, then call the matching "
        "per-template tool with concrete slot values.  Each template's "
        "wrapper docstring documents when to pick it and how to bind "
        "its slots.  You NEVER fill slots from intuition — every slot "
        "must come from the user's words OR from primitive metadata "
        "the template's docstring documents."
    ),
)


# ===========================================================================
# CATALOGUE TOOLS
# ===========================================================================


@mcp.tool()
def list_workflows() -> str:
    """List every workflow template registered with the substrate.

    Returns a JSON array of TemplateCards — uniform LLM-readable
    descriptors per ``shared.workflow.template_card``.  Each card
    carries ``template_id``, ``archetype``, ``description``, the full
    typed ``slot_schema`` (slot name + type + required + description +
    optional default), ``terminal_artifact_type``, ``primitives_used``,
    ``operators_used``, ``node_count`` / ``edge_count``, and the
    ``archetype_signature`` cues.

    Use this tool first to enumerate the catalogue, then
    ``describe_workflow_template`` for a deeper view of one template
    before binding its slots.
    """
    return json.dumps(list_workflow_cards(), default=str)


@mcp.tool()
def describe_workflow_template(template_id: str) -> str:
    """Return the full TemplateCard for one registered template.

    Same JSON shape as one entry of ``list_workflows`` but called
    explicitly when you have already chosen a candidate template and
    want the full slot schema in front of you before binding.

    Parameters
    ----------
    template_id : str
        The ``template_id`` of a registered template (e.g.
        ``"event_study"``, ``"regime_conditioned_relationship"``).
        Must match an entry in ``list_workflows`` exactly.
    """
    return json.dumps(describe_workflow_card(template_id), default=str)


# ===========================================================================
# PER-TEMPLATE WORKFLOW TOOLS
# ===========================================================================
#
# One ``@mcp.tool()`` per registered template.  Each wrapper:
#   1. accepts the template's slot schema as flat scalars / dicts,
#   2. assembles them into a ``slot_values`` dict,
#   3. acquires the DB engine (or returns the engine-error envelope),
#   4. delegates to ``_runner.run_template`` for binding + validation
#      + execution + envelope construction,
#   5. serializes the envelope to JSON for the wire.
#
# Adding a new template = add one new ``@mcp.tool()`` here with the
# new template's slot signature.  All runtime logic stays in _runner
# so wrappers are deliberately thin.


@mcp.tool()
def event_study_workflow(
    signal_tool_name: str,
    signal_params: dict,
    signal_output_field: str,
    target_tool_name: str,
    target_params: dict,
    target_output_field: str,
    threshold: float,
    post_window: int = 5,
) -> str:
    """Execute the canonical event_study workflow: threshold a signal
    series, extract events, forward-window a target series at each
    event, aggregate the conditional and unconditional moves in
    parallel, and emit the per-offset abnormal (conditional minus
    unconditional) Series.

    Use this tool when the user asks:
      - "Over the last 5 years, when X exceeds Y, what's the average
        N-day forward move in Z?"
      - "Conditional aggregation around event days vs unconditional
        baseline."
      - "Event study around moves of more than N sigma."
      - "Abnormal forward move relative to the unconditional
        baseline."

    Canonical Q1 binding (single-day swap-spread widening → 10Y UST
    forward move):
      signal_tool_name = "calculate_swap_spread_tool"
      signal_params = {
        "sovereign_curve_family": "UST",
        "ois_curve_family": "USD_SOFR_OIS",
        "tenor": "2Y",
        "lookback_days": 1825
      }
      signal_output_field = "time_series_change_zscore"
      target_tool_name = "get_yield_levels_tool"
      target_params = {"curve_family": "UST", "tenor": "10Y", "lookback_days": 1825}
      target_output_field = "time_series"
      threshold = 1.5
      post_window = 5

    Notes on event-semantic choice
    ------------------------------
    The signal_output_field controls whether the event question is
    "spread WIDENED today" (use ``time_series_change_zscore`` —
    rolling z-score of the day-over-day spread CHANGE) vs "spread IS
    STRETCHED today" (use ``time_series_zscore`` — rolling z-score of
    the LEVEL).  Today only ``calculate_swap_spread_tool`` emits the
    change-zscore field; the other spread primitives ship the
    level-zscore only.

    Parameters
    ----------
    signal_tool_name : str
        MCP tool name of the primitive emitting the event-trigger
        signal series (e.g. ``calculate_swap_spread_tool``,
        ``calculate_curve_spread_tool``,
        ``calculate_cross_market_spread_tool``).  Z_SCORE-typed
        outputs paired with the template-locked ``threshold_basis=
        raw_value`` give the canonical "|z| > N" event extraction.
    signal_params : dict
        Complete *Input dict for the signal primitive.  Caller
        assembles the dict shape; the primitive's *Input Pydantic
        validator catches field-level errors at execution time.
    signal_output_field : str
        Which time_series* field of the signal primitive's output to
        lift (e.g. ``time_series_change_zscore``, ``time_series_zscore``).
    target_tool_name : str
        MCP tool name of the primitive emitting the target series whose
        forward moves get aggregated around each event (e.g.
        ``get_yield_levels_tool``, ``calculate_swap_spread_tool``,
        ``calculate_ois_forward_rate_tool``).
    target_params : dict
        Complete *Input dict for the target primitive.  Lookback
        should typically be ≥ signal lookback so windows around late
        events have data to aggregate.
    target_output_field : str
        Which time_series* field of the target primitive's output to
        lift (e.g. ``time_series`` for yield_levels).
    threshold : float
        Absolute-value event threshold applied to the signal.  For
        Z_SCORE-typed signals, this is the ``|z|`` boundary
        (e.g. 1.5 = "events when |z| > 1.5").
    post_window : int, optional
        Trading-day count for the forward window after each event
        (default 5 = one calendar week).
    """
    template_id = "event_study"
    engine, err = _engine_or_error_envelope(template_id)
    if err is not None:
        return err
    slot_values = {
        "signal_tool_name": signal_tool_name,
        "signal_params": signal_params,
        "signal_output_field": signal_output_field,
        "target_tool_name": target_tool_name,
        "target_params": target_params,
        "target_output_field": target_output_field,
        "threshold": threshold,
        "post_window": post_window,
    }
    envelope = run_template(template_id, slot_values, engine=engine)
    return json.dumps(envelope, default=str)


@mcp.tool()
def regime_conditioned_relationship_workflow(
    lhs_tool_name: str,
    lhs_params: dict,
    lhs_output_field: str,
    rhs_tool_name: str,
    rhs_params: dict,
    rhs_output_field: str,
    regime_signal_tool_name: str,
    regime_signal_params: dict,
    regime_signal_output_field: str,
    regression_window: int,
    high_threshold: float,
    low_threshold: float,
    regression_min_periods: int = 30,
) -> str:
    """Execute the regime_conditioned_relationship workflow: rolling-
    OLS regression of an LHS series's CHANGE on an RHS series's
    CHANGE, classify regimes from a third regime-signal series's
    DAILY MOVE at two independent thresholds (high / low), subsample
    the rolling-β series by each regime mask, summarize the per-
    regime β samples (mean with std dispersion), and emit the per-
    regime β-difference scalar (high − low) at the summarize sentinel
    date.

    Use this tool when the user asks:
      - "Rolling beta of X CHANGE to Y CHANGE in steepening vs
        flattening regimes of the curve over the last N years."
      - "How does the relationship between A and B differ across
        regimes?"
      - "Regime-conditioned regression of one series on another."
      - "Compare beta distributions across high vs low regimes."

    Canonical Q2 binding (UST 10Y change vs 2Y SOFR OIS change,
    conditioned on 2s10s curve steepening / flattening):
      lhs_tool_name = "get_yield_levels_tool"
      lhs_params = {"curve_family": "UST", "tenor": "10Y", "lookback_days": 1500}
      lhs_output_field = "time_series"
      rhs_tool_name = "get_ois_rate_level_tool"
      rhs_params = {"curve_family": "USD_SOFR_OIS", "tenor": "2Y", "lookback_days": 1500}
      rhs_output_field = "time_series"
      regime_signal_tool_name = "calculate_ois_curve_spread_tool"
      regime_signal_params = {"curve_family": "USD_SOFR_OIS",
                              "short_tenor": "2Y", "long_tenor": "10Y",
                              "lookback_days": 1500}
      regime_signal_output_field = "time_series_spread"
      regression_window = 60
      high_threshold = 3.0     # steepening (curve moved UP by ≥3 bps today)
      low_threshold = -3.0     # flattening (curve moved DOWN by ≥3 bps today)

    Steepening vs flattening — MOVE-based regimes
    ---------------------------------------------
    The regime classification is on the regime-signal's DAILY MOVE,
    not its level — the template inserts a ``regime_diff`` step
    (series_arithmetic op=diff, period=1) BEFORE the threshold_events
    branches, so high_threshold / low_threshold are minimum-daily-move
    boundaries.  ``high_threshold >= low_threshold`` is enforced by the
    substrate at bind() time via the template's slot_constraints
    (a violator binding raises ``SlotBindingError`` before any node
    runs).

    Parameters
    ----------
    lhs_tool_name : str
        MCP tool name of the primitive emitting the dependent (target)
        series for the rolling regression (e.g. ``get_yield_levels_tool``).
    lhs_params : dict
        Complete *Input dict for the LHS primitive.
    lhs_output_field : str
        Which time_series* field of the LHS primitive's output to lift
        (e.g. ``time_series`` for yield_levels).
    rhs_tool_name : str
        MCP tool name of the primitive emitting the independent
        (regressor) series.  V1 supports a single regressor.
    rhs_params : dict
        Complete *Input dict for the RHS primitive.
    rhs_output_field : str
        Which time_series* field of the RHS primitive's output to lift.
    regime_signal_tool_name : str
        MCP tool name of the primitive emitting the regime-classifier
        signal (e.g. ``calculate_ois_curve_spread_tool`` for a 2s10s
        curve regime).
    regime_signal_params : dict
        Complete *Input dict for the regime-signal primitive.
    regime_signal_output_field : str
        Which time_series* field of the regime-signal primitive's
        output to lift (e.g. ``time_series_spread`` for a curve_spread
        in BPS).
    regression_window : int
        Trailing-window length, in trading days, for each rolling-OLS
        fit.  Canonical Q2 default 60.  Must be >= regression_min_periods.
    high_threshold : float
        Daily-move threshold strictly ABOVE which a day is classified
        as the "high" regime (e.g. steepening).  Units match the regime
        signal's level units.  Must satisfy ``high_threshold >= low_threshold``.
    low_threshold : float
        Daily-move threshold strictly BELOW which a day is classified
        as the "low" regime (e.g. flattening).
    regression_min_periods : int, optional
        Minimum non-NaN observations within a rolling window required
        for a non-NaN fit.  Default 30.
    """
    template_id = "regime_conditioned_relationship"
    engine, err = _engine_or_error_envelope(template_id)
    if err is not None:
        return err
    slot_values = {
        "lhs_tool_name": lhs_tool_name,
        "lhs_params": lhs_params,
        "lhs_output_field": lhs_output_field,
        "rhs_tool_name": rhs_tool_name,
        "rhs_params": rhs_params,
        "rhs_output_field": rhs_output_field,
        "regime_signal_tool_name": regime_signal_tool_name,
        "regime_signal_params": regime_signal_params,
        "regime_signal_output_field": regime_signal_output_field,
        "regression_window": regression_window,
        "regression_min_periods": regression_min_periods,
        "high_threshold": high_threshold,
        "low_threshold": low_threshold,
    }
    envelope = run_template(template_id, slot_values, engine=engine)
    return json.dumps(envelope, default=str)


# NOTE: ``backtest_workflow`` MCP tool intentionally removed from the
# LLM-facing surface in this PR.  Rationale: the V1 backtest archetype
# computes a yield-change distribution and labels it as trade P&L
# (Sharpe / drawdown / hit rate).  That labelling is not defensible
# without DV01 weighting, TIPS CPI carry, OTR resolution, and true
# O/N OIS financing — none of which the ingested data layer carries
# today.  Surfacing it to the LLM produces numbers a PM cannot trust.
# The template, operators, primitives, and tests remain in the repo
# under their respective test gauntlets; only the LLM-facing MCP tool
# wrapper is hidden.  Re-enable by restoring the import above and the
# tool wrapper below once the data prerequisites land
# (MOD_DUR_MID + CUR_CPN + DAY_CNT_DES + PX_DIRTY for sovereign/linker
# legs; CPI-U NSA + seasonal factors for TIPS carry; OTR history;
# true O/N OIS).  See ``docs/technical_debt.md`` item #24 for the
# data prerequisites and ``docs/architecture/backtest.md`` for the
# methodology spec.


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info(
        "Starting Workflows MCP server (stdio transport); registered "
        "templates: %s",
        known_template_ids(),
    )
    mcp.run(transport="stdio")
