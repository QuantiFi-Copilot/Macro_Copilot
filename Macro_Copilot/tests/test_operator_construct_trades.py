"""tests/test_operator_construct_trades.py — construct_trades tests
(Phase 1 PR 12).

Pure-Python operator test — exercises construct_trades against
synthesized EventSets (no DB needed).  The full integration with
artifact-store persistence is exercised in
``tests/state/test_artifact_store.py`` (PR 7's round-trip test
extended in PR 12 to cover TradeSet).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from shared.artifacts.lineage import FetchStep, Lineage  # noqa: E402
from shared.artifacts.trades import TradeSet  # noqa: E402
from shared.artifacts.types import EventSet  # noqa: E402
from shared.operators.construct_trades import (  # noqa: E402
    ConstructTradesParams,
    LegSpecInput,
    construct_trades,
)
from shared.operators.construct_trades.operator import ConstructTradesError  # noqa: E402


# ============================================================================
# Helpers
# ============================================================================


def _events(entry_indices, n=20) -> EventSet:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    mask = pd.Series(
        [i in entry_indices for i in range(n)], index=idx, dtype=bool,
    )
    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y", "test": "ctx"},
    )
    return EventSet(
        mask=mask,
        event_dates=[idx[i] for i in entry_indices],
        per_event_metadata=[{} for _ in entry_indices],
        source_series_key="UST.10Y",
        frequency="B",
        lineage=Lineage.from_steps([fetch]),
    )


def _two_leg_params(window: int = 10) -> ConstructTradesParams:
    return ConstructTradesParams(
        legs=(
            LegSpecInput(instrument_key="UST.10Y.yield_mid", weight=1.0, units="bps"),
            LegSpecInput(instrument_key="UST.2Y.yield_mid", weight=-1.0, units="bps"),
        ),
        holding_window_days=window,
    )


# ============================================================================
# Happy path
# ============================================================================


class TestHappyPath:
    def test_two_entries_two_trades(self):
        events = _events([5, 12])
        ts = construct_trades(events, _two_leg_params(window=10))
        assert isinstance(ts, TradeSet)
        assert ts.n_trades == 2
        # Both legs picked up with correct sides.
        assert ts.trades[0].leg_specs[0].side == "long"
        assert ts.trades[0].leg_specs[0].weight == 1.0
        assert ts.trades[0].leg_specs[1].side == "short"
        assert ts.trades[0].leg_specs[1].weight == -1.0

    def test_exit_dates_are_business_day_offset(self):
        events = _events([5])
        ts = construct_trades(events, _two_leg_params(window=10))
        trade = ts.trades[0]
        offset = pd.tseries.offsets.BDay(10)
        assert trade.exit_date == trade.entry_date + offset

    def test_empty_event_set_emits_empty_trade_set(self):
        events = _events([])
        ts = construct_trades(events, _two_leg_params(window=10))
        assert ts.n_trades == 0
        assert ts.methodology_policy == "fixed_horizon_v1"

    def test_methodology_policy_stamped_from_config(self):
        events = _events([5])
        ts = construct_trades(events, _two_leg_params(window=10))
        assert ts.methodology_policy == "fixed_horizon_v1"

    def test_source_event_key_propagated(self):
        events = _events([5])
        ts = construct_trades(events, _two_leg_params(window=10))
        assert ts.source_event_key == "UST.10Y"


# ============================================================================
# Determinism + idempotency at the hash level
# ============================================================================


class TestDeterminism:
    def test_same_inputs_same_lineage_head(self):
        events = _events([5, 12])
        params = _two_leg_params(window=10)
        a = construct_trades(events, params)
        b = construct_trades(events, params)
        assert a.lineage.head_hash == b.lineage.head_hash

    def test_different_window_moves_hash(self):
        events = _events([5, 12])
        a = construct_trades(events, _two_leg_params(window=10))
        b = construct_trades(events, _two_leg_params(window=20))
        assert a.lineage.head_hash != b.lineage.head_hash

    def test_different_legs_moves_hash(self):
        events = _events([5, 12])
        a = construct_trades(events, _two_leg_params(window=10))
        b_params = ConstructTradesParams(
            legs=(LegSpecInput(instrument_key="x", weight=1.0),),
            holding_window_days=10,
        )
        b = construct_trades(events, b_params)
        assert a.lineage.head_hash != b.lineage.head_hash


# ============================================================================
# V1 scope guards (the brief's NotImplementedError surface)
# ============================================================================


class TestV1ScopeGuards:
    def test_signal_exit_holding_rule_rejected(self):
        events = _events([5])
        with pytest.raises(NotImplementedError, match="holding rule"):
            construct_trades(
                events,
                ConstructTradesParams(
                    legs=_two_leg_params().legs,
                    holding_rule="signal_exit",
                ),
            )

    def test_stop_loss_holding_rule_rejected(self):
        events = _events([5])
        with pytest.raises(NotImplementedError, match="holding rule"):
            construct_trades(
                events,
                ConstructTradesParams(
                    legs=_two_leg_params().legs,
                    holding_rule="stop_loss",
                ),
            )

    def test_risk_parity_leg_rule_rejected(self):
        events = _events([5])
        with pytest.raises(NotImplementedError, match="leg construction"):
            construct_trades(
                events,
                ConstructTradesParams(
                    legs=_two_leg_params().legs,
                    leg_construction_rule="risk_parity",
                ),
            )

    def test_gross_unity_leg_rule_rejected(self):
        events = _events([5])
        with pytest.raises(NotImplementedError, match="leg construction"):
            construct_trades(
                events,
                ConstructTradesParams(
                    legs=_two_leg_params().legs,
                    leg_construction_rule="gross_unity",
                ),
            )


# ============================================================================
# Input-schema guards
# ============================================================================


class TestInputSchema:
    def test_zero_weight_leg_input_rejected(self):
        with pytest.raises(ValueError, match="zero"):
            LegSpecInput(instrument_key="x", weight=0.0)

    def test_empty_legs_list_rejected(self):
        with pytest.raises(ValueError):
            ConstructTradesParams(legs=())

    def test_duplicate_instrument_keys_in_params_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            ConstructTradesParams(
                legs=(
                    LegSpecInput(instrument_key="x", weight=1.0),
                    LegSpecInput(instrument_key="x", weight=-1.0),
                ),
            )

    def test_holding_window_out_of_range_rejected(self):
        # ge=1 le=252
        with pytest.raises(ValueError):
            ConstructTradesParams(
                legs=(LegSpecInput(instrument_key="x", weight=1.0),),
                holding_window_days=0,
            )
        with pytest.raises(ValueError):
            ConstructTradesParams(
                legs=(LegSpecInput(instrument_key="x", weight=1.0),),
                holding_window_days=253,
            )
