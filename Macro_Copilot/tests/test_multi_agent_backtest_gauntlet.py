"""tests/test_multi_agent_backtest_gauntlet.py

Phase 1 PR 21.

Backtest archetype gauntlet — 10 prompts covering the canonical
signal × leg-construction × holding-rule × negative-case matrix.

Two layers of validation:

  1. DETERMINISTIC LAYER (default — runs in CI):
     For each prompt, asserts the workflow template binds its slot
     values without errors AND pre-flight validates against the
     real primitive resolver.  This catches "the canonical binding
     would break" failures without invoking any LLM.

  2. LLM ROUTING LAYER (opt-in via env var ``GAUNTLET_MODE=on``):
     For each prompt, dispatches through the real workflow router
     to confirm the LLM routes the prompt to the backtest
     archetype with slot bindings that match the canonical
     expectation within a tolerance.  Runs only when explicitly
     requested because it costs Anthropic API tokens.

Adding a new prompt
-------------------
Add a ``BacktestPromptCase`` entry to ``BACKTEST_PROMPT_CASES``.
Each entry carries:
  - ``case_id``        — stable test identifier
  - ``prompt``         — the user-facing NL prompt
  - ``expected_action``— "ROUTE_BACKTEST" or "OUT_OF_SCOPE"
  - ``expected_slots`` — slot dict the LLM should produce (used by
                        the LLM-routing layer; ignored when
                        expected_action != ROUTE_BACKTEST)

The 9 backtest-routing cases below cover:
  - 3 signal variations (level z-score / spread / breakeven)
  - 3 leg constructions (single-leg / long-short / butterfly)
  - 3 holding-rule windows (5d / 20d / 125d)

Plus 1 negative case (snapshot question that should NOT route to
the backtest archetype).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# Prompt-case dataclass
# ============================================================================


@dataclass(frozen=True)
class BacktestPromptCase:
    """One row in the gauntlet matrix."""

    case_id: str
    prompt: str
    expected_action: str  # "ROUTE_BACKTEST" or "OUT_OF_SCOPE"
    expected_slots: Optional[Dict[str, Any]] = None


# ============================================================================
# The 10 prompts — 9 backtest-routing + 1 negative case
# ============================================================================


# Common building blocks reused across cases.
_BT_2Y_SIGNAL = {
    "signal_tool_name": "calculate_zscore_custom_tool",
    "signal_params": {
        "curve_family": "UST",
        "tenor": "2Y",
        "z_score_window_days": 60,
        "lookback_days": 365,
        "field_name": "YLD_YTM_MID",
    },
    "signal_output_field": "time_series",
    "signal_threshold": 1.5,
}


def _ust_tips_legs(weight_long: float = -1.0, weight_short: float = 1.0):
    return {
        "long_leg_curve_family": "USD_TIPS",
        "long_leg_tenor": "10Y",
        "long_leg_instrument_key": "USD_TIPS_10Y",
        "long_leg_weight": weight_long,
        "short_leg_curve_family": "UST",
        "short_leg_tenor": "2Y",
        "short_leg_instrument_key": "UST_2Y",
        "short_leg_weight": weight_short,
    }


def _financing_oip(curve: str = "USD_SOFR_OIS"):
    return {
        "financing_method": "overnight_index_proxy",
        "financing_proxy_curve": curve,
        "financing_constant_rate_pct": None,
        "financing_basis": "act_360",
    }


def _date_window():
    return {"start_date": "2022-01-03", "end_date": "2023-12-29"}


BACKTEST_PROMPT_CASES = [
    # ----- Signal variations × Long-short × 20d hold -----
    BacktestPromptCase(
        case_id="signal_level_zscore",
        prompt=(
            "Backtest buying USD_TIPS 10Y and shorting UST 2Y whenever "
            "the UST 2Y yield z-score exceeds 1.5σ.  Hold each trade 20 "
            "business days.  Use SOFR overnight proxy for financing."
        ),
        expected_action="ROUTE_BACKTEST",
        expected_slots={
            **_BT_2Y_SIGNAL,
            **_ust_tips_legs(),
            "holding_window_days": 20,
            **_date_window(),
            **_financing_oip(),
        },
    ),
    BacktestPromptCase(
        case_id="signal_spread_widening",
        prompt=(
            "Backtest a TIPS-vs-UST 2Y long-short trade triggered when "
            "the UST 2s10s curve spread widens by more than 1.5σ in a "
            "single day.  20-day hold, finance at SOFR."
        ),
        expected_action="ROUTE_BACKTEST",
        expected_slots={
            "signal_tool_name": "calculate_curve_spread_tool",
            "signal_params": {
                "curve_family": "UST",
                "short_tenor": "2Y",
                "long_tenor": "10Y",
                "lookback_days": 1825,
                "field_name": "YLD_YTM_MID",
            },
            "signal_output_field": "time_series_zscore",
            "signal_threshold": 1.5,
            **_ust_tips_legs(),
            "holding_window_days": 20,
            **_date_window(),
            **_financing_oip(),
        },
    ),
    BacktestPromptCase(
        case_id="signal_breakeven_zscore",
        prompt=(
            "Buy USD_TIPS 10Y vs short UST 2Y whenever the 10Y breakeven "
            "inflation z-score exceeds 1.5σ.  Hold 20 business days, "
            "finance at SOFR overnight proxy."
        ),
        expected_action="ROUTE_BACKTEST",
        expected_slots={
            "signal_tool_name": "calculate_breakeven_inflation_tool",
            "signal_params": {
                "nominal_curve_family": "UST",
                "real_curve_family": "USD_TIPS",
                "tenor": "10Y",
                "lookback_days": 1825,
            },
            "signal_output_field": "time_series_zscore",
            "signal_threshold": 1.5,
            **_ust_tips_legs(),
            "holding_window_days": 20,
            **_date_window(),
            **_financing_oip(),
        },
    ),
    # ----- Leg-construction variations -----
    BacktestPromptCase(
        case_id="legs_single_long",
        prompt=(
            "Backtest a long-only USD_TIPS 10Y position when the UST 2Y "
            "z-score is above 1.5σ.  20-day hold."
        ),
        expected_action="ROUTE_BACKTEST",
        # Single-leg implemented as long-short with zero short weight.
        expected_slots={
            **_BT_2Y_SIGNAL,
            "long_leg_curve_family": "USD_TIPS",
            "long_leg_tenor": "10Y",
            "long_leg_instrument_key": "USD_TIPS_10Y",
            "long_leg_weight": -1.0,
            "short_leg_curve_family": "USD_TIPS",
            "short_leg_tenor": "10Y",
            "short_leg_instrument_key": "USD_TIPS_10Y",
            "short_leg_weight": 0.0,
            "holding_window_days": 20,
            **_date_window(),
            **_financing_oip(),
        },
    ),
    BacktestPromptCase(
        case_id="legs_long_short",
        prompt=(
            "Long USD_TIPS 10Y, short UST 2Y when z-score > 1.5σ.  "
            "20-day hold, SOFR financing."
        ),
        expected_action="ROUTE_BACKTEST",
        expected_slots={
            **_BT_2Y_SIGNAL,
            **_ust_tips_legs(),
            "holding_window_days": 20,
            **_date_window(),
            **_financing_oip(),
        },
    ),
    BacktestPromptCase(
        case_id="legs_butterfly_2s10s30s",
        prompt=(
            "Backtest a UST 2s10s30s butterfly trade — receive the belly "
            "10Y, pay the wings 2Y and 30Y, when the curvature z-score "
            "exceeds 1.5σ.  Hold 20 business days."
        ),
        # The current backtest template only supports 2 legs.  A
        # butterfly request must either decompose to 2-leg or route
        # to a follow-up template — we expect OUT_OF_SCOPE today.
        expected_action="OUT_OF_SCOPE",
    ),
    # ----- Holding-rule variations -----
    BacktestPromptCase(
        case_id="holding_5d",
        prompt=(
            "Backtest long TIPS, short UST 2Y on signal > 1.5σ.  Hold for "
            "5 business days only (short-window momentum), SOFR financing."
        ),
        expected_action="ROUTE_BACKTEST",
        expected_slots={
            **_BT_2Y_SIGNAL,
            **_ust_tips_legs(),
            "holding_window_days": 5,
            **_date_window(),
            **_financing_oip(),
        },
    ),
    BacktestPromptCase(
        case_id="holding_20d",
        prompt=(
            "Backtest long TIPS, short UST 2Y on signal > 1.5σ.  20-day "
            "hold, SOFR financing."
        ),
        expected_action="ROUTE_BACKTEST",
        expected_slots={
            **_BT_2Y_SIGNAL,
            **_ust_tips_legs(),
            "holding_window_days": 20,
            **_date_window(),
            **_financing_oip(),
        },
    ),
    BacktestPromptCase(
        case_id="holding_125d_6months",
        prompt=(
            "Backtest long TIPS, short UST 2Y on signal > 1.5σ.  Hold "
            "each trade for approximately 6 months (125 business days), "
            "finance at SOFR overnight proxy."
        ),
        expected_action="ROUTE_BACKTEST",
        expected_slots={
            **_BT_2Y_SIGNAL,
            **_ust_tips_legs(),
            "holding_window_days": 125,
            **_date_window(),
            **_financing_oip(),
        },
    ),
    # ----- Negative case (should NOT route to backtest) -----
    BacktestPromptCase(
        case_id="negative_snapshot_question",
        prompt=(
            "What's the current UST 2Y yield, and how has it moved over "
            "the last week?"
        ),
        expected_action="OUT_OF_SCOPE",
    ),
]


# ============================================================================
# DETERMINISTIC LAYER — runs in CI, no LLM
# ============================================================================


@pytest.mark.parametrize(
    "case", BACKTEST_PROMPT_CASES, ids=lambda c: c.case_id
)
def test_canonical_slot_binding_validates(case: BacktestPromptCase):
    """For each backtest-routing prompt's expected slot dict, verify
    that the template binds without errors AND pre-flight validates
    against the real primitive resolver.

    This catches binding contract drift — e.g. if a slot is renamed
    or removed, the canonical bindings would fail to bind.

    Skipped for negative cases (no expected_slots to validate).
    """
    if case.expected_action != "ROUTE_BACKTEST":
        pytest.skip(
            f"Case {case.case_id!r} is a negative-routing case; "
            "no canonical slot binding to validate."
        )
    assert case.expected_slots is not None, (
        f"Backtest case {case.case_id!r} missing expected_slots."
    )

    # Importing rates_agent.workflows registers all templates.  We
    # also call ``register()`` explicitly because some other test
    # files (notably ``test_workflow_template_system.py``) install
    # an autouse fixture that clears the global template registry —
    # since module imports are cached, the side-effect register on
    # first import won't re-fire on a subsequent import.
    import rates_agent.workflows  # noqa: F401
    import rates_agent.workflows.backtest as _backtest_pkg  # noqa: F401
    _backtest_pkg.register()

    from shared.workflow import get_template
    from shared.workflow.validate import validate_workflow
    from rates_agent.workflows import rates_primitive_resolver

    template = get_template("backtest")
    workflow = template.bind(case.expected_slots)
    # Pre-flight validate — catches unknown slots, unknown tools,
    # type-incompat edges, etc.
    validate_workflow(workflow, primitive_resolver=rates_primitive_resolver)


def test_gauntlet_covers_signal_leg_holding_negative_matrix():
    """The 10-prompt set must cover the full matrix per the Week 7-8
    spec: 3 signal variations + 3 leg constructions + 3 holding
    rules + 1 negative case."""
    case_ids = {c.case_id for c in BACKTEST_PROMPT_CASES}
    expected_minimum = {
        "signal_level_zscore",
        "signal_spread_widening",
        "signal_breakeven_zscore",
        "legs_single_long",
        "legs_long_short",
        "legs_butterfly_2s10s30s",
        "holding_5d",
        "holding_20d",
        "holding_125d_6months",
        "negative_snapshot_question",
    }
    missing = expected_minimum - case_ids
    assert not missing, (
        f"Gauntlet missing expected matrix cases: {sorted(missing)}.  "
        f"Present cases: {sorted(case_ids)}"
    )
    # Negative case is present.
    negatives = [
        c for c in BACKTEST_PROMPT_CASES
        if c.expected_action == "OUT_OF_SCOPE"
    ]
    assert len(negatives) >= 1, (
        "Gauntlet must include at least one negative-routing case to "
        "guard against the workflow router over-fitting to the new "
        "archetype."
    )


# ============================================================================
# LLM ROUTING LAYER — opt-in via GAUNTLET_MODE=on
# ============================================================================


_GAUNTLET_ENABLED = os.getenv("GAUNTLET_MODE", "").lower() in {"on", "1", "true"}


@pytest.mark.skipif(
    not _GAUNTLET_ENABLED,
    reason=(
        "Gauntlet LLM-routing layer is opt-in.  Set GAUNTLET_MODE=on "
        "to enable.  Costs Anthropic API tokens per prompt."
    ),
)
@pytest.mark.parametrize(
    "case", BACKTEST_PROMPT_CASES, ids=lambda c: c.case_id
)
def test_llm_routes_prompt_correctly(case: BacktestPromptCase):
    """Dispatches each prompt through the real WorkflowRouter and
    asserts the routing decision matches expected.

    Opt-in: gated on ``GAUNTLET_MODE=on`` env var so CI doesn't burn
    tokens on every run.  Engineers run this manually before shipping
    archetype-router changes:

        GAUNTLET_MODE=on pytest tests/test_multi_agent_backtest_gauntlet.py
            -k test_llm_routes_prompt_correctly
    """
    # Lazy imports — the LangChain stack may not be installed in the
    # state-layer CI lane.
    try:
        from orchestrator.workflow_router import WorkflowRouter
        from orchestrator.workflow_contracts import WorkflowRouteAction
    except ImportError as e:
        pytest.skip(f"Workflow router unavailable: {e}")

    router = WorkflowRouter()
    decision = router.route(case.prompt)

    if case.expected_action == "ROUTE_BACKTEST":
        assert decision.action == WorkflowRouteAction.ROUTE, (
            f"Case {case.case_id!r}: expected ROUTE; got "
            f"{decision.action.value}.  Rationale: {decision.rationale}"
        )
        assert decision.template_id == "backtest", (
            f"Case {case.case_id!r}: expected template_id='backtest'; "
            f"got {decision.template_id!r}"
        )
        # Spot-check: the LLM should at minimum identify the
        # signal_tool_name + holding_window_days correctly.  Full slot
        # equality is too brittle for free-form NL prompts.
        if case.expected_slots:
            assert decision.slot_values.get("signal_tool_name") == (
                case.expected_slots.get("signal_tool_name")
            ), (
                f"Case {case.case_id!r}: signal_tool_name mismatch.\n"
                f"  Expected: {case.expected_slots.get('signal_tool_name')!r}\n"
                f"  Got:      {decision.slot_values.get('signal_tool_name')!r}"
            )
            assert decision.slot_values.get("holding_window_days") == (
                case.expected_slots.get("holding_window_days")
            ), (
                f"Case {case.case_id!r}: holding_window_days mismatch.\n"
                f"  Expected: {case.expected_slots.get('holding_window_days')}\n"
                f"  Got:      {decision.slot_values.get('holding_window_days')}"
            )
    else:
        assert decision.action == WorkflowRouteAction.OUT_OF_SCOPE, (
            f"Case {case.case_id!r}: expected OUT_OF_SCOPE; got "
            f"{decision.action.value}.  Rationale: {decision.rationale}"
        )
