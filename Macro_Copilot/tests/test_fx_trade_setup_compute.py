from __future__ import annotations

from unittest.mock import patch

from fx_agent.forwards.tools.forward_curve.schemas import (
    FXForwardCurveOutput,
    FXForwardCurveRow,
)
from fx_agent.forwards.tools.fx_carry.schemas import FXCarryOutput, FXCarryRow
from fx_agent.macro.tools.trade_setup import FXTradeSetupInput, get_fx_trade_setup
from fx_agent.spot.tools.spot_levels.schemas import (
    FXSpotLevelMetrics,
    FXSpotLevelOutput,
)
from fx_agent.vol.tools.realized_vol.schemas import (
    FXRealizedVolMetrics,
    FXRealizedVolOutput,
)


def _spot_output() -> FXSpotLevelOutput:
    return FXSpotLevelOutput(
        current_metrics=FXSpotLevelMetrics(
            as_of_date="2026-04-30",
            pair="EURUSD",
            current_spot=1.1736,
            daily_change_pct=0.5,
            weekly_change_pct=0.4,
            monthly_change_pct=1.2,
            z_score=0.6,
            high_252d=1.20,
            low_252d=1.11,
            percentile_252d=65.0,
            observation_count=259,
        )
    )


def _carry_output() -> FXCarryOutput:
    return FXCarryOutput(
        tenor="1M",
        rows=[
            FXCarryRow(
                pair="EURUSD",
                spot_date="2026-04-30",
                forward_date="2026-04-30",
                spot=1.1736,
                tenor="1M",
                forward_points=17.09,
                forward_points_spot_units=0.001709,
                outright_forward=1.175309,
                carry_bps_spot=14.56,
                carry_annualized_pct=1.75,
                carry_signal="High carry",
            )
        ],
    )


def _forward_curve_output() -> FXForwardCurveOutput:
    return FXForwardCurveOutput(
        pair="EURUSD",
        rows=[
            FXForwardCurveRow(
                pair="EURUSD",
                spot_date="2026-04-30",
                forward_date="2026-04-30",
                spot=1.1736,
                tenor="1W",
                tenor_days=5,
                forward_points=4.1,
                forward_points_spot_units=0.00041,
                outright_forward=1.17401,
                carry_bps_spot=3.49,
                carry_annualized_pct=1.76,
            ),
            FXForwardCurveRow(
                pair="EURUSD",
                spot_date="2026-04-30",
                forward_date="2026-04-30",
                spot=1.1736,
                tenor="1M",
                tenor_days=21,
                forward_points=17.09,
                forward_points_spot_units=0.001709,
                outright_forward=1.175309,
                carry_bps_spot=14.56,
                carry_annualized_pct=1.75,
            ),
        ],
    )


def _vol_output() -> FXRealizedVolOutput:
    return FXRealizedVolOutput(
        current_metrics=FXRealizedVolMetrics(
            as_of_date="2026-04-30",
            pair="EURUSD",
            spot=1.1736,
            window_observations=21,
            realized_vol_annualized_pct=7.5,
            realized_vol_z_score=-0.4,
            daily_return_pct=0.2,
            observation_count=259,
        ),
        time_series=[],
    )


def test_fx_trade_setup_combines_component_tools():
    with patch(
        "fx_agent.macro.tools.trade_setup.compute.get_fx_spot_level",
        return_value=_spot_output(),
    ) as mock_spot, patch(
        "fx_agent.macro.tools.trade_setup.compute.get_fx_carry",
        return_value=_carry_output(),
    ) as mock_carry, patch(
        "fx_agent.macro.tools.trade_setup.compute.get_fx_forward_curve",
        return_value=_forward_curve_output(),
    ) as mock_curve, patch(
        "fx_agent.macro.tools.trade_setup.compute.get_fx_realized_vol",
        return_value=_vol_output(),
    ) as mock_vol:
        out = get_fx_trade_setup(
            FXTradeSetupInput(pair="eur/usd", tenor="1M", vol_window_observations=21)
        )

    assert out.pair == "EURUSD"
    assert out.direction in {"bullish", "bearish", "neutral"}
    assert out.confidence in {"high", "medium", "low"}
    assert out.spot_snapshot["current_spot"] == 1.1736
    assert out.carry_snapshot is not None
    assert len(out.signals) == 4
    assert len(out.forward_curve) == 2
    assert mock_spot.called
    assert mock_carry.called
    assert mock_curve.called
    assert mock_vol.called
