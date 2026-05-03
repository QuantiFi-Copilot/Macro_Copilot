from __future__ import annotations

from unittest.mock import patch

from fx_agent.forwards.tools.forward_curve.schemas import (
    FXForwardCurveOutput,
    FXForwardCurveRow,
)
from fx_agent.forwards.tools.fx_carry.schemas import FXCarryOutput, FXCarryRow
from fx_agent.spot.tools.spot_levels.schemas import (
    FXSpotLevelMetrics,
    FXSpotLevelOutput,
)
from fx_agent.vol.tools.realized_vol.schemas import (
    FXRealizedVolMetrics,
    FXRealizedVolOutput,
)


def test_fx_spot_detail_route_calls_tool():
    from api.routes.fx import detail as detail_module

    output = FXSpotLevelOutput(
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

    with patch.object(detail_module, "get_fx_spot_level", return_value=output) as mock_tool:
        result = detail_module.fx_spot_level_detail(
            pair="EURUSD",
            lookback_days=365,
            field_name=None,
        )

    assert result == output
    params = mock_tool.call_args.kwargs["params"]
    assert params.pair == "EURUSD"


def test_fx_carry_detail_route_calls_tool():
    from api.routes.fx import detail as detail_module

    output = FXCarryOutput(
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

    with patch.object(detail_module, "get_fx_carry", return_value=output) as mock_tool:
        result = detail_module.fx_carry_detail(tenor="1M")

    assert result == output
    assert mock_tool.call_args.kwargs["params"].tenor == "1M"


def test_fx_forward_curve_detail_route_calls_tool():
    from api.routes.fx import detail as detail_module

    output = FXForwardCurveOutput(
        pair="EURUSD",
        rows=[
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
            )
        ],
    )

    with patch.object(detail_module, "get_fx_forward_curve", return_value=output) as mock_tool:
        result = detail_module.fx_forward_curve_detail(pair="EURUSD")

    assert result == output
    assert mock_tool.call_args.kwargs["params"].pair == "EURUSD"


def test_fx_realized_vol_detail_route_calls_tool():
    from api.routes.fx import detail as detail_module

    output = FXRealizedVolOutput(
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

    with patch.object(detail_module, "get_fx_realized_vol", return_value=output) as mock_tool:
        result = detail_module.fx_realized_vol_detail(
            pair="EURUSD",
            window_observations=21,
            lookback_days=365,
            return_type="log_return",
            field_name=None,
        )

    assert result == output
    params = mock_tool.call_args.kwargs["params"]
    assert params.pair == "EURUSD"
    assert params.window_observations == 21
