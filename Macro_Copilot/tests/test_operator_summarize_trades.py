"""tests/test_operator_summarize_trades.py — summarize_trades tests
(Phase 1 PR 12)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from shared.artifacts.lineage import FetchStep, Lineage  # noqa: E402
from shared.artifacts.missingness import RawNoCleaning  # noqa: E402
from shared.artifacts.types import EventSet, Panel  # noqa: E402
from shared.artifacts.units import TimeSeriesUnits  # noqa: E402
from shared.operators.construct_trades import (  # noqa: E402
    ConstructTradesParams,
    LegSpecInput,
    construct_trades,
)
from shared.operators.evaluate_trades import evaluate_trades  # noqa: E402
from shared.operators.summarize_trades import (  # noqa: E402
    SummarizeTradesParams,
    summarize_trades,
)
from shared.operators.summarize_trades.operator import (  # noqa: E402
    SummarizeTradesError,
)


# ============================================================================
# Helpers
# ============================================================================


def _run_chain(
    entry_indices,
    holding=5,
    n_days=80,
    rng_seed=42,
):
    idx = pd.date_range("2024-01-01", periods=n_days, freq="B")
    mask = pd.Series(
        [i in entry_indices for i in range(n_days)], index=idx, dtype=bool,
    )
    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y", "test": "sum"},
    )
    events = EventSet(
        mask=mask,
        event_dates=[idx[i] for i in entry_indices],
        per_event_metadata=[{} for _ in entry_indices],
        source_series_key="UST.10Y",
        frequency="B",
        lineage=Lineage.from_steps([fetch]),
    )
    params = ConstructTradesParams(
        legs=(
            LegSpecInput(instrument_key="UST.10Y.yield_mid", weight=1.0, units="bps"),
            LegSpecInput(instrument_key="UST.2Y.yield_mid", weight=-1.0, units="bps"),
        ),
        holding_window_days=holding,
    )
    trades = construct_trades(events, params)
    rng = np.random.default_rng(rng_seed)
    panel_df = pd.DataFrame({
        "UST.10Y.yield_mid": 400 + np.cumsum(rng.normal(0, 1, n_days)),
        "UST.2Y.yield_mid": 380 + np.cumsum(rng.normal(0, 0.8, n_days)),
    }, index=idx)
    plineage = FetchStep.build(
        name="fetch_tenor_group", version="1.0.0",
        params={"group": "UST", "test": "sum"},
    )
    panel = Panel(
        payload=panel_df,
        units_by_column={c: TimeSeriesUnits.BPS for c in panel_df.columns},
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([plineage]),
    )
    return evaluate_trades(trades, panel)


# ============================================================================
# Happy path
# ============================================================================


class TestHappyPath:
    def test_summary_columns_and_shape(self):
        pnl = _run_chain([5, 15, 30, 45, 60])
        summary = summarize_trades(pnl)
        assert summary.payload.shape == (1, 7)
        cols = list(summary.payload.columns)
        assert cols == [
            "hit_rate", "mean_pnl", "sharpe_annualized", "max_drawdown",
            "p10_pnl", "p50_pnl", "p90_pnl",
        ]

    def test_hit_rate_in_unit_interval(self):
        pnl = _run_chain([5, 15, 30, 45, 60])
        summary = summarize_trades(pnl)
        h = summary.payload["hit_rate"].iloc[0]
        assert 0.0 <= h <= 1.0

    def test_max_drawdown_nonpositive(self):
        pnl = _run_chain([5, 15, 30, 45, 60])
        summary = summarize_trades(pnl)
        dd = summary.payload["max_drawdown"].iloc[0]
        assert dd <= 0.0

    def test_percentile_monotone(self):
        pnl = _run_chain([5, 15, 30, 45, 60])
        summary = summarize_trades(pnl)
        p10 = summary.payload["p10_pnl"].iloc[0]
        p50 = summary.payload["p50_pnl"].iloc[0]
        p90 = summary.payload["p90_pnl"].iloc[0]
        assert p10 <= p50 <= p90

    def test_units_match_metric_family(self):
        pnl = _run_chain([5, 15, 30, 45, 60])
        summary = summarize_trades(pnl)
        assert summary.units_by_column["hit_rate"] == TimeSeriesUnits.RATIO
        assert summary.units_by_column["sharpe_annualized"] == TimeSeriesUnits.RATIO
        assert summary.units_by_column["mean_pnl"] == TimeSeriesUnits.BPS
        assert summary.units_by_column["max_drawdown"] == TimeSeriesUnits.BPS


# ============================================================================
# Empty / NaN-only inputs
# ============================================================================


class TestEmptyInputs:
    def test_empty_tradeset_nan_metrics(self):
        pnl = _run_chain([])  # zero entries → zero trades → empty pnl
        summary = summarize_trades(pnl)
        assert summary.payload.shape == (1, 7)
        # Every metric is NaN.
        for col in summary.payload.columns:
            assert math.isnan(summary.payload[col].iloc[0])

    def test_empty_raise_policy(self):
        pnl = _run_chain([])
        with pytest.raises(SummarizeTradesError, match="no trade columns"):
            summarize_trades(
                pnl,
                SummarizeTradesParams(empty_tradeset_policy="raise"),
            )


# ============================================================================
# Determinism
# ============================================================================


class TestDeterminism:
    def test_same_inputs_same_hash(self):
        pnl = _run_chain([5, 15, 30])
        a = summarize_trades(pnl)
        b = summarize_trades(pnl)
        assert a.lineage.head_hash == b.lineage.head_hash

    def test_different_trading_days_moves_hash(self):
        pnl = _run_chain([5, 15, 30])
        a = summarize_trades(pnl, SummarizeTradesParams(trading_days_per_year=252))
        b = summarize_trades(pnl, SummarizeTradesParams(trading_days_per_year=260))
        assert a.lineage.head_hash != b.lineage.head_hash


# ============================================================================
# V1 scope guards
# ============================================================================


class TestV1ScopeGuards:
    def test_cumulative_max_aggregation_rejected(self):
        pnl = _run_chain([5])
        with pytest.raises(NotImplementedError, match="final_pnl"):
            summarize_trades(
                pnl,
                SummarizeTradesParams(pnl_aggregation_method="cumulative_max"),
            )

    def test_time_weighted_aggregation_rejected(self):
        pnl = _run_chain([5])
        with pytest.raises(NotImplementedError, match="final_pnl"):
            summarize_trades(
                pnl,
                SummarizeTradesParams(pnl_aggregation_method="time_weighted"),
            )


# ============================================================================
# Lineage holding_window_days extraction
# ============================================================================


class TestHoldingWindowReadback:
    def test_holding_window_reflected_in_lineage_step(self):
        """summarize_trades records the holding_window it pulled
        off the construct_trades step.  The lineage step's
        ``params.holding_window_days`` should match what construct
        used."""
        pnl_a = _run_chain([5], holding=10)
        pnl_b = _run_chain([5], holding=20)
        sa = summarize_trades(pnl_a)
        sb = summarize_trades(pnl_b)
        # Pull the summarize_trades step from each lineage and read
        # the params.
        sa_step = sa.lineage.steps[-1]
        sb_step = sb.lineage.steps[-1]
        assert sa_step.params["holding_window_days"] == 10
        assert sb_step.params["holding_window_days"] == 20
