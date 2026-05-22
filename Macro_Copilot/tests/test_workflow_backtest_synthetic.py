"""tests/test_workflow_backtest_synthetic.py — synthetic end-to-end
backtest chain test.

Phase 1 PR 19.

Drives the full backtest operator chain against hand-crafted
synthetic artifacts (no DB / no LLM / no MCP).  Validates:

  1. The 3 new primitives + 1 extended operator all compose into a
     working chain.
  2. The financing-extension to ``evaluate_trades`` correctly adds
     per-leg cumulative carry to price P&L.
  3. ``financing_assumption='none'`` preserves the PR 12 contract
     exactly (no financing carry; backwards-compatible).
  4. The workflow template loads and registers via the substrate.
  5. The closed-family archetype list now includes ``backtest``.

This is the V1 substrate proof test.  Live-DB / LLM-driven tests
of the same workflow ship as separate integration tests that hit
the test Postgres.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# Fixtures — synthetic Panels + TradeSet
# ============================================================================


def _make_price_panel(
    n_days: int = 60,
    base_long: float = 2.0,
    base_short: float = 4.5,
    drift_long_bp_per_day: float = 0.5,
    drift_short_bp_per_day: float = -0.2,
):
    """Build a 2-column synthetic price Panel.

    Long column = TIPS 10Y real-yield-style series rising over the
    window (so a -1 weight on the long leg gains as yield falls
    relative to entry... no, here we deliberately make it RISE so
    the negative weight produces a negative P&L gain on the long
    leg — easier to reason about).

    Short column = UST 2Y nominal-style series falling (negative
    drift) — so positive weight on short leg gains as yield falls
    too.

    Both in PERCENT.  Returns a Panel.
    """
    from shared.artifacts.lineage import Lineage, PrimitiveStep
    from shared.artifacts.missingness import RawNoCleaning
    from shared.artifacts.types import Panel
    from shared.artifacts.units import TimeSeriesUnits

    idx = pd.bdate_range(start="2024-01-02", periods=n_days)
    long_values = np.array(
        [base_long + drift_long_bp_per_day * i / 100.0 for i in range(n_days)]
    )
    short_values = np.array(
        [base_short + drift_short_bp_per_day * i / 100.0 for i in range(n_days)]
    )
    df = pd.DataFrame(
        {"USD_TIPS_10Y": long_values, "UST_2Y": short_values},
        index=idx,
    )
    # Synthetic primitive step (the test stand-in for a real
    # sovereign_yield_panel run).
    step = PrimitiveStep.build(
        name="build_sovereign_yield_panel_tool",
        version="1.0.0",
        params={
            "legs": [
                {"curve_family": "USD_TIPS", "tenor": "10Y", "field_name": "YLD_YTM_MID"},
                {"curve_family": "UST", "tenor": "2Y", "field_name": "YLD_YTM_MID"},
            ],
            "start_date": "2024-01-02",
            "end_date": idx[-1].strftime("%Y-%m-%d"),
            "n_observations": n_days,
        },
        tool_config_hash="synthetic_test_hash",
        output_field="panel",
        as_of_date=idx[-1].strftime("%Y-%m-%d"),
    )
    return Panel(
        payload=df,
        units_by_column={
            "USD_TIPS_10Y": TimeSeriesUnits.PERCENT,
            "UST_2Y": TimeSeriesUnits.PERCENT,
        },
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([step]),
    )


def _make_financing_panel(price_panel, rate_pct: float = 5.30):
    """Build a single-column constant financing-rate Panel aligned
    to the price panel's calendar."""
    from shared.artifacts.lineage import Lineage, PrimitiveStep
    from shared.artifacts.missingness import RawNoCleaning
    from shared.artifacts.types import Panel
    from shared.artifacts.units import TimeSeriesUnits

    idx = price_panel.payload.index
    df = pd.DataFrame(
        {"financing_constant_5pct": np.full(len(idx), rate_pct, dtype=float)},
        index=idx,
    )
    step = PrimitiveStep.build(
        name="compute_financing_rate_tool",
        version="1.0.0",
        params={
            "method": "constant_rate",
            "constant_rate_pct": rate_pct,
            "start_date": idx[0].strftime("%Y-%m-%d"),
            "end_date": idx[-1].strftime("%Y-%m-%d"),
            "n_observations": len(idx),
        },
        tool_config_hash="synthetic_test_hash",
        output_field="panel",
        as_of_date=idx[-1].strftime("%Y-%m-%d"),
    )
    return Panel(
        payload=df,
        units_by_column={"financing_constant_5pct": TimeSeriesUnits.PERCENT},
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([step]),
    )


def _make_simple_tradeset(entry_dates, exit_dates):
    """Build a small TradeSet with TIPS-vs-2Y leg structure for
    each (entry, exit) pair."""
    from shared.artifacts.lineage import Lineage, OperatorStep
    from shared.artifacts.trades import LegSpec, Trade, TradeSet

    # NB: LegSpec invariant is ``side == 'long' iff weight >= 0`` —
    # the side label tracks the weight SIGN, not the user's
    # conventional-finance semantic intent.  PR 12's yield-change
    # P&L formula treats positive weight as betting yield UP
    # (semantically SHORT a bond), so weight=+1 + side='long' here
    # is consistent with the codebase's convention even though it
    # doesn't match a conventional "long bond" reading.
    trades = []
    for e, x in zip(entry_dates, exit_dates):
        trades.append(
            Trade(
                entry_date=pd.Timestamp(e),
                exit_date=pd.Timestamp(x),
                leg_specs=(
                    LegSpec(
                        instrument_key="USD_TIPS_10Y",
                        weight=-1.0,
                        side="short",
                        units="PERCENT",
                    ),
                    LegSpec(
                        instrument_key="UST_2Y",
                        weight=1.0,
                        side="long",
                        units="PERCENT",
                    ),
                ),
                methodology_ref=None,
            )
        )
    # Build a stand-in operator step so the TradeSet has a valid
    # lineage chain.  In production this comes from construct_trades.
    step = OperatorStep.build(
        name="construct_trades",
        version="1.0.0",
        params={"n_trades": len(trades), "holding_rule": "fixed_horizon"},
        input_hashes=(),
    )
    return TradeSet(
        trades=tuple(trades),
        source_event_key="synthetic",
        methodology_policy="fixed_horizon_v1",
        lineage=Lineage.from_steps([step]),
    )


# ============================================================================
# Workflow registration tests
# ============================================================================


def test_backtest_archetype_is_registered_in_closed_family() -> None:
    """The closed-family Literal + tuple include ``backtest``."""
    from shared.workflow.template import WorkflowArchetype, WORKFLOW_ARCHETYPES

    assert "backtest" in WORKFLOW_ARCHETYPES, (
        f"backtest archetype missing from WORKFLOW_ARCHETYPES tuple; "
        f"got {WORKFLOW_ARCHETYPES}."
    )
    # The Literal type's args are the same closed set.
    assert "backtest" in WorkflowArchetype.__args__, (
        "backtest archetype missing from WorkflowArchetype Literal; "
        "the tuple and Literal must stay in sync."
    )


def test_backtest_template_loads_and_registers() -> None:
    """Importing the package auto-registers the template."""
    import rates_agent.workflows.backtest as bt
    from shared.workflow import get_template

    template = bt.load_backtest_template()
    assert template.template_id == "backtest"
    assert template.archetype == "backtest"
    assert template.terminal_node_id == "summarize"

    # Round-trip via the registry.
    registered = get_template("backtest")
    assert registered.template_id == "backtest"


def test_backtest_template_dag_shape() -> None:
    """The DAG topology is the locked 7-node chain."""
    import rates_agent.workflows.backtest as bt

    template = bt.load_backtest_template()
    node_ids = {n.node_id for n in template.nodes}
    assert node_ids == {
        "signal", "events", "trades",
        "price_panel", "financing", "evaluate", "summarize",
    }
    # 6 edges: signal→events→trades→evaluate, price_panel→evaluate,
    #          financing→evaluate, evaluate→summarize.
    assert len(template.edges) == 6


# ============================================================================
# Operator chain end-to-end tests (no DB)
# ============================================================================


def test_evaluate_trades_financing_none_preserves_pr12_contract() -> None:
    """Backwards compat: financing_assumption='none' produces the
    IDENTICAL output PR 12 produced.  No financing carry."""
    from shared.operators.evaluate_trades import evaluate_trades
    from shared.operators.evaluate_trades.schemas import EvaluateTradesParams

    price_panel = _make_price_panel(n_days=40)
    entry_dates = [price_panel.payload.index[5]]
    exit_dates = [price_panel.payload.index[25]]
    trades = _make_simple_tradeset(entry_dates, exit_dates)

    params = EvaluateTradesParams(financing_assumption="none")
    pnl_panel = evaluate_trades(trades=trades, price_panel=price_panel, params=params)

    assert pnl_panel.payload.shape[1] == 1  # 1 trade column
    col = pnl_panel.payload.columns[0]
    # Entry-day P&L is 0 (price[entry] - price[entry] = 0).
    entry_pnl = pnl_panel.payload.loc[entry_dates[0], col]
    assert abs(entry_pnl) < 1e-9, (
        f"Entry-day P&L should be 0; got {entry_pnl}."
    )
    # Exit-day P&L should reflect the synthetic drifts.
    exit_pnl = pnl_panel.payload.loc[exit_dates[0], col]
    # Long leg: weight=-1, price change = +(20 days × 0.5bp/day) / 100 = +0.10%
    #   leg P&L = -1 × +0.10 = -0.10
    # Short leg: weight=+1, price change = (20 days × -0.2bp/day) / 100 = -0.04%
    #   leg P&L = +1 × -0.04 = -0.04
    # Total = -0.14
    expected = -0.14
    assert abs(exit_pnl - expected) < 1e-6, (
        f"Expected exit P&L ≈ {expected}; got {exit_pnl}."
    )


def test_evaluate_trades_financing_external_series_adds_carry() -> None:
    """When financing_assumption='external_series', cumulative
    financing carry is added per leg per day.

    For a +1 weight short bond at 5.3%/360 day rate, the carry is
    POSITIVE (short receives financing) over ~20 days:
        20 × 5.3 / 360 ≈ 0.294 PCT
    For a -1 weight long bond at the same rate: NEGATIVE 0.294 PCT.

    Net trade-level financing carry: ~zero (long pays, short receives,
    same notional).  BUT the price P&L from the drift remains.  So
    the financed-trade exit P&L should be approximately the same as
    the unfinanced (since the legs cancel each other's financing).
    """
    from shared.operators.evaluate_trades import evaluate_trades
    from shared.operators.evaluate_trades.schemas import EvaluateTradesParams

    price_panel = _make_price_panel(n_days=40)
    financing_panel = _make_financing_panel(price_panel, rate_pct=5.30)
    entry_dates = [price_panel.payload.index[5]]
    exit_dates = [price_panel.payload.index[25]]
    trades = _make_simple_tradeset(entry_dates, exit_dates)

    params_unfinanced = EvaluateTradesParams(financing_assumption="none")
    pnl_unfinanced = evaluate_trades(
        trades=trades, price_panel=price_panel, params=params_unfinanced,
    )

    params_financed = EvaluateTradesParams(
        financing_assumption="external_series",
        financing_rate_panel=financing_panel,
        financing_basis="act_360",
    )
    pnl_financed = evaluate_trades(
        trades=trades, price_panel=price_panel, params=params_financed,
    )

    col = pnl_unfinanced.payload.columns[0]
    unfinanced_exit = pnl_unfinanced.payload.loc[exit_dates[0], col]
    financed_exit = pnl_financed.payload.loc[exit_dates[0], col]
    # For a long-short trade with equal/opposite weights at the same
    # rate, the financing legs cancel exactly.  Both legs accrue 20
    # business days × 5.3% / 360 = 0.2944% but with opposite signs.
    # Sum ≈ 0.
    assert abs(financed_exit - unfinanced_exit) < 1e-6, (
        f"Long-short equal-weight financing should cancel; saw "
        f"financed={financed_exit}, unfinanced={unfinanced_exit}, "
        f"diff={financed_exit - unfinanced_exit}."
    )


def test_evaluate_trades_financing_single_leg_long_pays_financing() -> None:
    """A solitary +1-weight long bond (in PR 12 sign convention) at
    5.3% over 20 days should PAY financing → financed P&L < unfinanced.

    Actually under PR 12's yield-change sign convention:
      weight=+1 means betting yield UP (= short bond).
    So weight=+1 + positive rate = receives financing (short gets paid).
    Daily accrual = -weight × rate / basis = -1 × 5.3 / 360 = -0.0147/day
                  ... but our docstring sign convention says
                  ``-leg.weight × rate / basis_days``.
    For weight=+1: -1 × 5.3 / 360 = -0.0147/day  ← NEGATIVE accrual
    Over 20 days (entry day = 0): ~ -0.294 PCT cumulative drag.

    Verify this matches the docstring's documented sign convention.
    """
    from shared.artifacts.lineage import Lineage, OperatorStep
    from shared.artifacts.trades import LegSpec, Trade, TradeSet
    from shared.operators.evaluate_trades import evaluate_trades
    from shared.operators.evaluate_trades.schemas import EvaluateTradesParams

    price_panel = _make_price_panel(n_days=40)
    financing_panel = _make_financing_panel(price_panel, rate_pct=5.30)
    entry = price_panel.payload.index[5]
    exit = price_panel.payload.index[25]

    # Single +1 weight leg on USD_TIPS_10Y; side="long" per the
    # LegSpec invariant (positive weight ↔ side="long").
    step = OperatorStep.build(
        name="construct_trades", version="1.0.0",
        params={"n_trades": 1, "holding_rule": "fixed_horizon"},
        input_hashes=(),
    )
    one_leg_trades = TradeSet(
        trades=(
            Trade(
                entry_date=pd.Timestamp(entry),
                exit_date=pd.Timestamp(exit),
                leg_specs=(
                    LegSpec(
                        instrument_key="USD_TIPS_10Y",
                        weight=1.0,
                        side="long",
                        units="PERCENT",
                    ),
                ),
                methodology_ref=None,
            ),
        ),
        source_event_key="synthetic",
        methodology_policy="fixed_horizon_v1",
        lineage=Lineage.from_steps([step]),
    )

    params_unfinanced = EvaluateTradesParams(financing_assumption="none")
    pnl_unfinanced = evaluate_trades(
        trades=one_leg_trades, price_panel=price_panel,
        params=params_unfinanced,
    )
    params_financed = EvaluateTradesParams(
        financing_assumption="external_series",
        financing_rate_panel=financing_panel,
        financing_basis="act_360",
    )
    pnl_financed = evaluate_trades(
        trades=one_leg_trades, price_panel=price_panel,
        params=params_financed,
    )
    col = pnl_unfinanced.payload.columns[0]
    diff = (
        pnl_financed.payload.loc[exit, col]
        - pnl_unfinanced.payload.loc[exit, col]
    )
    # Expected accrual: 20 days × (-1) × 5.30 / 360 = -0.2944 PCT.
    expected = 20 * (-1) * 5.30 / 360.0
    assert abs(diff - expected) < 1e-6, (
        f"Expected financing carry ≈ {expected}; got {diff}."
    )


def test_evaluate_trades_external_series_requires_panel() -> None:
    """Calling with financing_assumption='external_series' but no
    financing_rate_panel raises EvaluateTradesError."""
    from shared.operators.evaluate_trades import evaluate_trades
    from shared.operators.evaluate_trades.operator import EvaluateTradesError
    from shared.operators.evaluate_trades.schemas import EvaluateTradesParams

    price_panel = _make_price_panel(n_days=20)
    trades = _make_simple_tradeset(
        [price_panel.payload.index[2]], [price_panel.payload.index[12]],
    )
    params = EvaluateTradesParams(financing_assumption="external_series")
    with pytest.raises(EvaluateTradesError, match="requires a financing_rate_panel"):
        evaluate_trades(trades=trades, price_panel=price_panel, params=params)


def test_evaluate_trades_legacy_constant_rate_raises_with_pointer() -> None:
    """The deprecated ``constant_rate`` / ``overnight_repo_curve``
    placeholder values still raise NotImplementedError, but the
    message now points the caller at the correct external_series
    path via the financing_rate primitive."""
    from shared.operators.evaluate_trades import evaluate_trades
    from shared.operators.evaluate_trades.schemas import EvaluateTradesParams

    price_panel = _make_price_panel(n_days=20)
    trades = _make_simple_tradeset(
        [price_panel.payload.index[2]], [price_panel.payload.index[12]],
    )
    params = EvaluateTradesParams(financing_assumption="constant_rate")
    with pytest.raises(NotImplementedError, match="compute_financing_rate_tool"):
        evaluate_trades(trades=trades, price_panel=price_panel, params=params)

    params2 = EvaluateTradesParams(financing_assumption="overnight_repo_curve")
    with pytest.raises(NotImplementedError, match="compute_financing_rate_tool"):
        evaluate_trades(trades=trades, price_panel=price_panel, params=params2)
