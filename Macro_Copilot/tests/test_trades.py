"""tests/test_trades.py — TradeSet artifact tests (Phase 1 PR 12).

Substrate tests for the new closed-family member.  No Postgres
needed — this file exercises the Pydantic models directly.

Tests cover:

  - LegSpec validation: side vs weight sign consistency, units
    optional, weight cannot equal 0 (caller-bug surface).
  - Trade validation: at least one leg, exit_date >= entry_date,
    duplicate instrument_keys rejected.
  - TradeSet validation: monotone entry-date ordering, empty
    TradeSet allowed.
  - records round-trip: TradeSet.to_records() → TradeSet.from_records()
    preserves trade list + leg specs byte-identically.

These tests are deliberately substrate-level — operator tests in
``tests/test_operator_construct_trades.py`` exercise the trade
construction path; this file exercises the artifact itself.
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
from shared.artifacts.trades import LegSpec, Trade, TradeSet  # noqa: E402


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def minimal_lineage() -> Lineage:
    step = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y", "test": "trades"},
    )
    return Lineage.from_steps([step])


# ============================================================================
# LegSpec
# ============================================================================


class TestLegSpec:
    def test_long_leg_round_trips(self):
        leg = LegSpec(
            instrument_key="UST.10Y.yield_mid", weight=1.0, side="long",
        )
        assert leg.weight == 1.0
        assert leg.side == "long"
        assert leg.units is None

    def test_short_leg_round_trips(self):
        leg = LegSpec(
            instrument_key="UST.2Y.yield_mid",
            weight=-1.0,
            side="short",
            units="bps",
        )
        assert leg.weight == -1.0
        assert leg.side == "short"
        assert leg.units == "bps"

    def test_positive_weight_with_short_side_rejected(self):
        with pytest.raises(ValueError, match="weight"):
            LegSpec(instrument_key="x", weight=1.0, side="short")

    def test_negative_weight_with_long_side_rejected(self):
        with pytest.raises(ValueError, match="weight"):
            LegSpec(instrument_key="x", weight=-1.0, side="long")

    def test_zero_weight_with_long_side_allowed_artifact_level(self):
        """The artifact-level ``LegSpec`` allows zero weight; the
        zero-weight rejection lives on the input-side
        ``LegSpecInput`` (operator schema) where caller-bug surface
        matters most.  This is the layered-validation pattern."""
        # weight = 0 -> side must be 'long' per the validator
        leg = LegSpec(instrument_key="x", weight=0.0, side="long")
        assert leg.weight == 0.0


# ============================================================================
# Trade
# ============================================================================


class TestTrade:
    def test_basic_trade(self, minimal_lineage):
        leg = LegSpec(instrument_key="x", weight=1.0, side="long")
        trade = Trade(
            entry_date=pd.Timestamp("2024-01-01"),
            exit_date=pd.Timestamp("2024-01-21"),
            leg_specs=(leg,),
        )
        assert trade.n_legs == 1
        assert trade.holding_days == 20
        assert trade.methodology_ref is None

    def test_empty_legs_rejected(self):
        with pytest.raises(ValueError, match="at least one leg"):
            Trade(
                entry_date=pd.Timestamp("2024-01-01"),
                exit_date=pd.Timestamp("2024-01-02"),
                leg_specs=(),
            )

    def test_exit_before_entry_rejected(self):
        leg = LegSpec(instrument_key="x", weight=1.0, side="long")
        with pytest.raises(ValueError, match="exit_date"):
            Trade(
                entry_date=pd.Timestamp("2024-01-21"),
                exit_date=pd.Timestamp("2024-01-01"),
                leg_specs=(leg,),
            )

    def test_same_day_entry_exit_allowed(self):
        """Same-day entry+exit is a degenerate but valid case
        (zero-day holding); accepted with ``holding_days == 0``."""
        leg = LegSpec(instrument_key="x", weight=1.0, side="long")
        trade = Trade(
            entry_date=pd.Timestamp("2024-01-01"),
            exit_date=pd.Timestamp("2024-01-01"),
            leg_specs=(leg,),
        )
        assert trade.holding_days == 0

    def test_duplicate_instrument_keys_rejected(self):
        legs = (
            LegSpec(instrument_key="x", weight=1.0, side="long"),
            LegSpec(instrument_key="x", weight=-1.0, side="short"),
        )
        with pytest.raises(ValueError, match="duplicate instrument_keys"):
            Trade(
                entry_date=pd.Timestamp("2024-01-01"),
                exit_date=pd.Timestamp("2024-01-02"),
                leg_specs=legs,
            )

    def test_multi_leg_trade(self):
        legs = (
            LegSpec(instrument_key="a", weight=1.0, side="long"),
            LegSpec(instrument_key="b", weight=-0.5, side="short"),
            LegSpec(instrument_key="c", weight=2.0, side="long"),
        )
        trade = Trade(
            entry_date=pd.Timestamp("2024-01-01"),
            exit_date=pd.Timestamp("2024-01-21"),
            leg_specs=legs,
        )
        assert trade.n_legs == 3


# ============================================================================
# TradeSet
# ============================================================================


def _trade(entry: str, exit_: str, weight: float = 1.0) -> Trade:
    side = "long" if weight >= 0 else "short"
    return Trade(
        entry_date=pd.Timestamp(entry),
        exit_date=pd.Timestamp(exit_),
        leg_specs=(LegSpec(instrument_key="x", weight=weight, side=side),),
    )


class TestTradeSet:
    def test_basic_trade_set(self, minimal_lineage):
        ts = TradeSet(
            trades=(
                _trade("2024-01-01", "2024-01-21"),
                _trade("2024-02-01", "2024-02-21"),
            ),
            source_event_key="x",
            methodology_policy="fixed_horizon_v1",
            lineage=minimal_lineage,
        )
        assert ts.n_trades == 2
        assert ts.source_event_key == "x"
        assert ts.methodology_policy == "fixed_horizon_v1"

    def test_empty_trade_set_allowed(self, minimal_lineage):
        ts = TradeSet(
            trades=(),
            source_event_key=None,
            methodology_policy="fixed_horizon_v1",
            lineage=minimal_lineage,
        )
        assert ts.n_trades == 0
        assert ts.entry_dates == []
        assert ts.exit_dates == []

    def test_entry_date_ordering_enforced(self, minimal_lineage):
        with pytest.raises(ValueError, match="ordered by entry_date"):
            TradeSet(
                trades=(
                    _trade("2024-02-01", "2024-02-21"),
                    _trade("2024-01-01", "2024-01-21"),  # out of order
                ),
                source_event_key="x",
                methodology_policy="fixed_horizon_v1",
                lineage=minimal_lineage,
            )

    def test_same_day_entry_allowed(self, minimal_lineage):
        ts = TradeSet(
            trades=(
                _trade("2024-01-01", "2024-01-21"),
                _trade("2024-01-01", "2024-01-21"),
            ),
            source_event_key="x",
            methodology_policy="fixed_horizon_v1",
            lineage=minimal_lineage,
        )
        assert ts.n_trades == 2

    def test_blank_methodology_policy_rejected(self, minimal_lineage):
        with pytest.raises(ValueError):
            TradeSet(
                trades=(),
                source_event_key=None,
                methodology_policy="",
                lineage=minimal_lineage,
            )

    def test_records_round_trip(self, minimal_lineage):
        leg_a = LegSpec(instrument_key="UST.10Y", weight=1.0, side="long")
        leg_b = LegSpec(instrument_key="UST.2Y", weight=-1.0, side="short")
        original = TradeSet(
            trades=(
                Trade(
                    entry_date=pd.Timestamp("2024-01-01"),
                    exit_date=pd.Timestamp("2024-01-21"),
                    leg_specs=(leg_a, leg_b),
                    methodology_ref=42,
                ),
                Trade(
                    entry_date=pd.Timestamp("2024-02-01"),
                    exit_date=pd.Timestamp("2024-02-21"),
                    leg_specs=(leg_a, leg_b),
                ),
            ),
            source_event_key="UST.10Y.zscore",
            methodology_policy="fixed_horizon_v1",
            lineage=minimal_lineage,
        )

        records = original.to_records()
        recovered = TradeSet.from_records(
            records,
            source_event_key=original.source_event_key,
            methodology_policy=original.methodology_policy,
            lineage=original.lineage,
        )
        assert recovered.n_trades == original.n_trades
        for t_orig, t_rec in zip(original.trades, recovered.trades):
            assert t_orig.entry_date == t_rec.entry_date
            assert t_orig.exit_date == t_rec.exit_date
            assert t_orig.methodology_ref == t_rec.methodology_ref
            assert len(t_orig.leg_specs) == len(t_rec.leg_specs)
            for leg_o, leg_r in zip(t_orig.leg_specs, t_rec.leg_specs):
                assert leg_o.instrument_key == leg_r.instrument_key
                assert leg_o.weight == leg_r.weight
                assert leg_o.side == leg_r.side
                assert leg_o.units == leg_r.units

    def test_frozen_artifact(self, minimal_lineage):
        """``model_config = ConfigDict(frozen=True)`` — assigning to
        a field MUST raise."""
        leg = LegSpec(instrument_key="x", weight=1.0, side="long")
        trade = Trade(
            entry_date=pd.Timestamp("2024-01-01"),
            exit_date=pd.Timestamp("2024-01-21"),
            leg_specs=(leg,),
        )
        ts = TradeSet(
            trades=(trade,),
            source_event_key=None,
            methodology_policy="fixed_horizon_v1",
            lineage=minimal_lineage,
        )
        with pytest.raises(Exception):
            ts.methodology_policy = "rebound"  # type: ignore[misc]
