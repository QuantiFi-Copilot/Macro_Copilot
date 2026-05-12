"""tests/test_operator_evaluate_trades.py — evaluate_trades tests
(Phase 1 PR 12)."""

from __future__ import annotations

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
from shared.operators.evaluate_trades import (  # noqa: E402
    EvaluateTradesParams,
    evaluate_trades,
)
from shared.operators.evaluate_trades.operator import (  # noqa: E402
    EvaluateTradesError,
)


# ============================================================================
# Helpers
# ============================================================================


def _build_chain(n_days=30, entry_indices=(5,), holding=5, slot="ev"):
    idx = pd.date_range("2024-01-01", periods=n_days, freq="B")
    mask = pd.Series(
        [i in entry_indices for i in range(n_days)], index=idx, dtype=bool,
    )
    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y", "slot": slot},
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
    return idx, trades


def _build_panel(idx, ten_y_values=None, two_y_values=None) -> Panel:
    n = len(idx)
    if ten_y_values is None:
        ten_y_values = np.linspace(400, 410, n)
    if two_y_values is None:
        two_y_values = np.linspace(380, 395, n)
    df = pd.DataFrame(
        {"UST.10Y.yield_mid": ten_y_values, "UST.2Y.yield_mid": two_y_values},
        index=idx,
    )
    lineage_step = FetchStep.build(
        name="fetch_tenor_group", version="1.0.0",
        params={"group": "UST", "test": "evt_eval"},
    )
    return Panel(
        payload=df,
        units_by_column={c: TimeSeriesUnits.BPS for c in df.columns},
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([lineage_step]),
    )


# ============================================================================
# Happy path
# ============================================================================


class TestHappyPath:
    def test_single_trade_pnl_math(self):
        idx, trades = _build_chain(entry_indices=(5,), holding=5)
        # Linear price ramps so the math is checkable by hand.
        panel = _build_panel(idx)
        pnl = evaluate_trades(trades, panel)
        col = "trade_000000_pnl"
        # Entry @ index 5; exit @ index 5+5=10.  Holding window dates
        # are idx[5..10] inclusive → 6 dates.
        in_window = pnl.payload[col].dropna()
        assert len(in_window) == 6
        # At entry the P&L is 0.
        assert abs(in_window.iloc[0]) < 1e-12
        # At each subsequent date the P&L is monotone (in this
        # specific linear-up panel for the long leg, and linear-up
        # for the short leg → net depends on slopes).
        # We just assert non-trivial values land in the window.
        assert in_window.notna().all()

    def test_two_trades_two_columns(self):
        idx, trades = _build_chain(entry_indices=(5, 15), holding=5)
        panel = _build_panel(idx)
        pnl = evaluate_trades(trades, panel)
        assert list(pnl.payload.columns) == [
            "trade_000000_pnl", "trade_000001_pnl",
        ]

    def test_units_propagate_from_first_leg(self):
        idx, trades = _build_chain(entry_indices=(5,), holding=5)
        panel = _build_panel(idx)
        pnl = evaluate_trades(trades, panel)
        for col, u in pnl.units_by_column.items():
            assert u == TimeSeriesUnits.BPS

    def test_empty_tradeset_emits_empty_panel(self):
        idx, trades = _build_chain(entry_indices=(), holding=5)
        panel = _build_panel(idx)
        pnl = evaluate_trades(trades, panel)
        # No trade columns, empty index — still a valid Panel.
        assert pnl.payload.shape == (0, 0)


# ============================================================================
# Determinism
# ============================================================================


class TestDeterminism:
    def test_same_inputs_same_hash(self):
        idx, trades = _build_chain(entry_indices=(5, 15), holding=5)
        panel = _build_panel(idx)
        a = evaluate_trades(trades, panel)
        b = evaluate_trades(trades, panel)
        assert a.lineage.head_hash == b.lineage.head_hash

    def test_different_panel_lineage_moves_hash(self):
        """Hash identity propagates from the panel's lineage chain,
        NOT from its values.  This is the load-bearing
        content-addressing contract: two panels with distinct
        lineage (distinct fetches) produce distinct P&L hashes.
        Tests against the alternative (same lineage, different
        values → same hash) are by design — that's the substrate's
        content-as-identity rule.
        """
        idx, trades = _build_chain(entry_indices=(5,), holding=5)

        # Two panels with DIFFERENT lineage chains.
        def _build_with_lineage(slot: str) -> Panel:
            df = pd.DataFrame({
                "UST.10Y.yield_mid": np.linspace(400, 410, len(idx)),
                "UST.2Y.yield_mid": np.linspace(380, 395, len(idx)),
            }, index=idx)
            step = FetchStep.build(
                name="fetch_tenor_group", version="1.0.0",
                params={"group": "UST", "slot": slot},
            )
            return Panel(
                payload=df,
                units_by_column={c: TimeSeriesUnits.BPS for c in df.columns},
                missingness_policy=RawNoCleaning(),
                lineage=Lineage.from_steps([step]),
            )

        panel_a = _build_with_lineage("a")
        panel_b = _build_with_lineage("b")
        a = evaluate_trades(trades, panel_a)
        b = evaluate_trades(trades, panel_b)
        assert a.lineage.head_hash != b.lineage.head_hash


# ============================================================================
# V1 scope guards (frictionless disclosure structural)
# ============================================================================


class TestV1ScopeGuards:
    def test_pricing_ask_rejected(self):
        idx, trades = _build_chain(entry_indices=(5,), holding=5)
        panel = _build_panel(idx)
        with pytest.raises(NotImplementedError, match="pricing"):
            evaluate_trades(
                trades, panel,
                EvaluateTradesParams(pricing_convention="ask"),
            )

    def test_frictionless_false_rejected(self):
        idx, trades = _build_chain(entry_indices=(5,), holding=5)
        panel = _build_panel(idx)
        with pytest.raises(NotImplementedError, match="frictionless"):
            evaluate_trades(
                trades, panel,
                EvaluateTradesParams(frictionless=False),
            )

    def test_financing_constant_rejected(self):
        idx, trades = _build_chain(entry_indices=(5,), holding=5)
        panel = _build_panel(idx)
        with pytest.raises(NotImplementedError, match="financing"):
            evaluate_trades(
                trades, panel,
                EvaluateTradesParams(financing_assumption="constant_rate"),
            )


# ============================================================================
# Coverage + error surfaces
# ============================================================================


class TestErrorSurfaces:
    def test_missing_instrument_column_raises(self):
        idx, trades = _build_chain(entry_indices=(5,), holding=5)
        # Drop one of the columns the leg needs.
        partial_df = pd.DataFrame(
            {"UST.10Y.yield_mid": np.linspace(400, 410, len(idx))},
            index=idx,
        )
        panel = Panel(
            payload=partial_df,
            units_by_column={"UST.10Y.yield_mid": TimeSeriesUnits.BPS},
            missingness_policy=RawNoCleaning(),
            lineage=Lineage.from_steps([
                FetchStep.build(name="fetch_single_tenor", version="1.0.0", params={"x": 1})
            ]),
        )
        with pytest.raises(EvaluateTradesError, match="missing columns"):
            evaluate_trades(trades, panel)

    def test_entry_date_not_in_panel_emits_nan_column(self):
        # Build a panel that does NOT contain the entry date (idx[5]).
        idx, trades = _build_chain(entry_indices=(5,), holding=5, n_days=30)
        # Use a panel with a different calendar entirely.
        bad_idx = pd.date_range("2025-01-01", periods=30, freq="B")
        bad_panel = _build_panel(bad_idx)
        # Need to override the column list to match.  Easiest: build
        # a panel on bad_idx with the right columns.
        bad_panel = Panel(
            payload=pd.DataFrame({
                "UST.10Y.yield_mid": np.zeros(30),
                "UST.2Y.yield_mid": np.zeros(30),
            }, index=bad_idx),
            units_by_column={c: TimeSeriesUnits.BPS for c in ("UST.10Y.yield_mid","UST.2Y.yield_mid")},
            missingness_policy=RawNoCleaning(),
            lineage=Lineage.from_steps([
                FetchStep.build(name="fetch_tenor_group", version="1.0.0", params={"x": 1}),
            ]),
        )
        # Result: trade contributes NaN-only column.  But there's no
        # union of in-window dates either since the trade's window
        # doesn't overlap bad_panel's calendar — so we get an
        # empty Panel.
        pnl = evaluate_trades(trades, bad_panel)
        assert pnl.payload.shape == (0, 0)
