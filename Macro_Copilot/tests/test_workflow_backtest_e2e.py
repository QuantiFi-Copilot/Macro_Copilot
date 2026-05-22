"""tests/test_workflow_backtest_e2e.py — end-to-end backtest workflow
execution test.

Phase 1 PR 20.

Drives ``run_template_with_resolver("backtest", {...})`` end-to-end
through the workflow executor with the REAL primitive resolver
(``rates_primitive_resolver``).  Database fetchers are mocked at the
``shared/analytics/`` boundary so the test runs without a Postgres
instance.

This test ASSERTS the full execution chain compiles + dispatches:

  1. Slot binding (no nested format-string substitution gaps).
  2. Pre-flight validation passes (every primitive + operator is
     registered in their respective registries).
  3. Primitive nodes resolve via ``rates_primitive_resolver``:
     - ``calculate_zscore_custom_tool`` (Series-producing)
     - ``build_sovereign_yield_panel_tool`` (Panel-producing)
     - ``compute_financing_rate_tool`` (Panel-producing)
  4. The Series + Panel bridges both fire correctly.
  5. Operator nodes dispatch via ``OPERATOR_REGISTRY``:
     - ``threshold_events`` (Series → EventSet)
     - ``construct_trades`` (EventSet → TradeSet)
     - ``evaluate_trades`` (TradeSet + Panel + Panel → Panel)
     - ``summarize_trades`` (Panel → Panel)
  6. Terminal artifact is summarized into a JSON-friendly envelope.

When the synthetic test (test_workflow_backtest_synthetic.py) passes
this test STILL adds value: the synthetic test bypasses the executor
entirely (it builds artifacts by hand and calls operators directly).
THIS test drives the executor + bridges + registries — the layer
where PR 19 had its blockers.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# Synthetic data fixtures
# ============================================================================


def _make_zscore_signal_df(n_days: int = 500):
    """Synthetic single-tenor signal series shaped like what
    ``fetch_single_tenor`` returns: long-format DataFrame with
    ``trade_date`` + ``field_value`` columns.

    Engineers a few high-|z| events so threshold_events emits a
    non-empty EventSet.
    """
    idx = pd.bdate_range(start="2022-01-03", periods=n_days)
    # Base series oscillates around 4.0 with periodic large spikes
    values = 4.0 + 0.05 * np.sin(np.arange(n_days) / 10.0)
    # Inject 3 large positive spikes
    for i in [50, 200, 400]:
        if i < n_days:
            values[i] = 6.0  # well above the rolling 4.0 mean
    return pd.DataFrame({
        "trade_date": idx,
        "field_value": values,
    })


def _make_yield_panel_df(n_days: int = 500):
    """Synthetic wide multi-leg yield Panel DataFrame.  Two columns
    matching the canonical TIPS-vs-2Y instrument keys."""
    idx = pd.bdate_range(start="2022-01-03", periods=n_days)
    tips = 2.0 + 0.001 * np.arange(n_days)  # gentle drift up
    ust = 4.5 - 0.0005 * np.arange(n_days)  # gentle drift down
    return pd.DataFrame(
        {"USD_TIPS_10Y": tips, "UST_2Y": ust},
        index=idx,
    )


def _make_financing_rate_series(n_days: int = 500):
    """Synthetic SOFR overnight proxy series."""
    idx = pd.bdate_range(start="2022-01-03", periods=n_days)
    return pd.Series(
        data=np.full(n_days, 5.30, dtype=float),
        index=idx,
        name="financing_rate_pct",
    )


# ============================================================================
# E2E TEST — full backtest workflow execution
# ============================================================================


def test_backtest_workflow_executes_end_to_end():
    """The canonical TIPS-vs-Nominal backtest binding runs through
    the workflow executor with mocked data fetchers.

    Asserts the entire chain works:
      slot binding → validate → execute → summarize → envelope.
    """
    # Import the runner + resolver.  Importing
    # ``rates_agent.workflows`` registers all templates including
    # backtest as a side-effect.
    import rates_agent.workflows  # noqa: F401 — side-effect: template registration
    import rates_agent.workflows.backtest  # noqa: F401 — template registration

    from rates_agent.workflows._runner import run_template_with_resolver
    from rates_agent.workflows import rates_primitive_resolver

    signal_df = _make_zscore_signal_df()
    yield_panel_df = _make_yield_panel_df()
    financing_series = _make_financing_rate_series()

    slot_values = {
        # Signal: UST 2Y yield z-score via zscore_custom (Z_SCORE units)
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
        # Trade legs: long USD_TIPS 10Y, short UST 2Y
        "long_leg_curve_family": "USD_TIPS",
        "long_leg_tenor": "10Y",
        "long_leg_instrument_key": "USD_TIPS_10Y",
        "long_leg_weight": -1.0,
        "short_leg_curve_family": "UST",
        "short_leg_tenor": "2Y",
        "short_leg_instrument_key": "UST_2Y",
        "short_leg_weight": 1.0,
        "holding_window_days": 20,
        # Price + financing fetch window
        "start_date": "2022-01-03",
        "end_date": "2023-12-29",
        "financing_method": "overnight_index_proxy",
        "financing_proxy_curve": "USD_SOFR_OIS",
        "financing_constant_rate_pct": None,
        "financing_basis": "act_360",
    }

    # Mock the three fetchers at their canonical import sites.
    with patch(
        "rates_agent.sovereign_bonds.tools.zscore_custom.compute.fetch_single_tenor",
        return_value=signal_df,
    ), patch(
        "rates_agent.sovereign_bonds.tools.sovereign_yield_panel.compute."
        "fetch_instrument_panel",
        return_value=yield_panel_df,
    ), patch(
        "rates_agent.ois.tools.financing_rate.compute."
        "fetch_overnight_index_series",
        return_value=financing_series,
    ), patch(
        # zscore_custom also calls ``date.today()`` — pin to a date
        # WITHIN the synthetic data range so the lookback window
        # covers actual rows.
        "rates_agent.sovereign_bonds.tools.zscore_custom.compute.date",
        wraps=__import__("datetime").date,
    ) as mock_date:
        # Configure mock_date.today() to return a date inside the
        # synthetic data range (the data spans 2022-01-03 to ~2023-12-29).
        mock_date.today.return_value = date(2023, 12, 1)

        envelope = run_template_with_resolver(
            "backtest",
            slot_values,
            engine=None,
            primitive_resolver=rates_primitive_resolver,
        )

    # ----- Envelope-shape assertions -----
    assert isinstance(envelope, dict), (
        f"Expected dict envelope; got {type(envelope).__name__}"
    )
    assert envelope.get("ok") is True, (
        f"Workflow returned ok=False; envelope={envelope}"
    )
    assert envelope.get("template_id") == "backtest"

    terminal = envelope.get("terminal_artifact")
    assert isinstance(terminal, dict), (
        f"Expected terminal_artifact dict; got {type(terminal).__name__}"
    )
    # The backtest's terminal artifact is the summary Panel (one row,
    # one column per summary metric).
    assert terminal.get("type") == "Panel", (
        f"Expected terminal Panel; got type={terminal.get('type')!r}"
    )
    # Summary panel has exactly one row (the summary date).
    assert terminal.get("n_rows") == 1, (
        f"Expected single-row summary Panel; got n_rows="
        f"{terminal.get('n_rows')}"
    )
    # Summary metric columns from summarize_trades V1.
    expected_metric_cols = {
        "hit_rate", "mean_pnl", "sharpe_annualized", "max_drawdown",
        "p10_pnl", "p50_pnl", "p90_pnl",
    }
    summary_cols = set(terminal.get("columns", []))
    assert summary_cols == expected_metric_cols, (
        f"Summary metric columns drifted from V1 contract.\n"
        f"  Expected: {sorted(expected_metric_cols)}\n"
        f"  Got:      {sorted(summary_cols)}"
    )

    # ----- Lineage sanity -----
    lineage_summary = envelope.get("workflow_lineage_summary")
    assert lineage_summary is not None
    assert isinstance(lineage_summary, str)
    # The lineage should mention the workflow id.
    assert "backtest" in lineage_summary


# ============================================================================
# E2E TEST — Panel-bridge dispatch end-to-end
# ============================================================================


def test_panel_bridge_dispatch_via_executor():
    """A focused executor-level test: a workflow with ONLY a Panel-
    producing primitive node should route through the Panel bridge
    and produce a Panel terminal artifact.

    This confirms the ``output_artifact_type == 'Panel'`` dispatch
    on PrimitiveSpec actually picks up.
    """
    from datetime import date as _date

    from shared.workflow import (
        get_template, register_template,
    )
    from shared.workflow.types import (
        PrimitiveNode, Workflow,
    )
    from shared.workflow.executor import execute_workflow
    from rates_agent.workflows import rates_primitive_resolver

    yield_panel_df = _make_yield_panel_df(n_days=20)

    # Build a tiny single-node workflow that just calls
    # build_sovereign_yield_panel_tool.  No template needed — we
    # construct the Workflow directly to keep this test focused on
    # the bridge dispatch.
    wf = Workflow(
        workflow_id="panel_bridge_test",
        nodes=[
            PrimitiveNode(
                node_id="panel",
                kind="primitive",
                tool_name="build_sovereign_yield_panel_tool",
                output_field="panel",
                params={
                    "legs": [
                        {"curve_family": "USD_TIPS", "tenor": "10Y"},
                        {"curve_family": "UST", "tenor": "2Y"},
                    ],
                    "start_date": "2022-01-03",
                    "end_date": "2022-01-31",
                },
            ),
        ],
        edges=[],
        literal_bindings=[],
        terminal_node_id="panel",
    )

    with patch(
        "rates_agent.sovereign_bonds.tools.sovereign_yield_panel.compute."
        "fetch_instrument_panel",
        return_value=yield_panel_df,
    ):
        result = execute_workflow(
            wf, engine=None,
            primitive_resolver=rates_primitive_resolver,
        )

    # Terminal artifact must be a Panel (not a Series).
    from shared.artifacts.types import Panel
    assert isinstance(result.terminal_artifact, Panel), (
        f"Expected Panel; got {type(result.terminal_artifact).__name__}"
    )
    # Columns match the bound legs.
    cols = list(result.terminal_artifact.payload.columns)
    assert cols == ["USD_TIPS_10Y", "UST_2Y"], (
        f"Panel column order drifted; got {cols}"
    )


# ============================================================================
# E2E TEST — Series-bridge backwards compat
# ============================================================================


def test_series_bridge_still_works_for_existing_primitives():
    """A Series-producing primitive (with the default
    ``output_artifact_type='Series'`` on its PrimitiveSpec) must
    still route through the original Series bridge unchanged.

    This is the PR 12 contract: any primitive registered before
    PR 20 keeps working bit-for-bit because the executor's bridge
    dispatch defaults to ``Series``.
    """
    from shared.workflow.types import PrimitiveNode, Workflow
    from shared.workflow.executor import execute_workflow
    from rates_agent.workflows import rates_primitive_resolver

    # Use calculate_curve_spread_tool — a canonical Series-producing
    # primitive from before PR 20.
    signal_df = _make_zscore_signal_df()

    # curve_spread fetches via fetch_tenor_pair (returns 2-tenor
    # long-format DataFrame).  Build a 2-tenor synthetic.
    idx = pd.bdate_range(start="2022-01-03", periods=400)
    raw = []
    for d in idx:
        raw.append({"trade_date": d, "tenor": "2Y", "field_value": 4.5})
        raw.append({"trade_date": d, "tenor": "10Y", "field_value": 5.0})
    raw_df = pd.DataFrame(raw)

    wf = Workflow(
        workflow_id="series_bridge_test",
        nodes=[
            PrimitiveNode(
                node_id="spread",
                kind="primitive",
                tool_name="calculate_curve_spread_tool",
                output_field="time_series_spread",
                params={
                    "curve_family": "UST",
                    "short_tenor": "2Y",
                    "long_tenor": "10Y",
                    "lookback_days": 365,
                    "field_name": "YLD_YTM_MID",
                },
            ),
        ],
        edges=[],
        literal_bindings=[],
        terminal_node_id="spread",
    )

    with patch(
        "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
        return_value=raw_df,
    ), patch(
        "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
        wraps=__import__("datetime").date,
    ) as mock_date:
        mock_date.today.return_value = date(2023, 6, 1)
        result = execute_workflow(
            wf, engine=None,
            primitive_resolver=rates_primitive_resolver,
        )

    # Terminal artifact must be a Series (NOT a Panel).
    from shared.artifacts.types import Series
    assert isinstance(result.terminal_artifact, Series), (
        f"Expected Series (PR 12 contract); got "
        f"{type(result.terminal_artifact).__name__}"
    )
    # Units must propagate from the primitive's output_field_units.
    from shared.artifacts.units import TimeSeriesUnits
    assert result.terminal_artifact.units == TimeSeriesUnits.BPS


# ============================================================================
# E2E TEST — operator registry includes the trade operators
# ============================================================================


def test_trade_operators_registered_in_substrate():
    """PR 12 omission fix: the trade operators must be in
    OPERATOR_REGISTRY so the executor can dispatch to them."""
    from shared.workflow.registry import OPERATOR_REGISTRY

    for op_name in ("construct_trades", "evaluate_trades", "summarize_trades"):
        assert op_name in OPERATOR_REGISTRY, (
            f"Trade operator {op_name!r} missing from OPERATOR_REGISTRY.  "
            "Without registration the workflow executor cannot dispatch "
            "to it; the backtest workflow would fail at validate time."
        )

    # Sanity: output types match the closed family.
    assert OPERATOR_REGISTRY["construct_trades"].output_type == "TradeSet"
    assert OPERATOR_REGISTRY["evaluate_trades"].output_type == "Panel"
    assert OPERATOR_REGISTRY["summarize_trades"].output_type == "Panel"


# ============================================================================
# E2E TEST — TradeSet recognised as closed-family artifact
# ============================================================================


def test_tradeset_is_recognised_artifact_type():
    """``artifact_type_name`` must return ``'TradeSet'`` for a
    TradeSet instance — otherwise the executor's artifact-type
    validation refuses ``construct_trades``' output."""
    from shared.artifacts.lineage import Lineage, OperatorStep
    from shared.artifacts.trades import TradeSet
    from shared.workflow.registry import (
        ARTIFACT_TYPE_NAMES, artifact_type_name,
    )

    assert "TradeSet" in ARTIFACT_TYPE_NAMES

    step = OperatorStep.build(
        name="construct_trades", version="1.0.0",
        params={"n_trades": 0}, input_hashes=(),
    )
    ts = TradeSet(
        trades=(),
        source_event_key="test",
        methodology_policy="fixed_horizon_v1",
        lineage=Lineage.from_steps([step]),
    )
    assert artifact_type_name(ts) == "TradeSet"
