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
from fx_agent.macro.tools.trade_setup.schemas import (
    FXTradeSetupOutput,
    FXTradeSetupSignal,
)
from fx_agent.macro.tools.risk_overlay.schemas import (
    FXMacroRiskOverlayOutput,
    FXMacroRiskProxyRow,
)
from fx_agent.macro.tools.correlation_beta.schemas import (
    FXCorrelationBetaOutput,
    FXCorrelationBetaRow,
)
from fx_agent.macro.tools.regime_classifier.schemas import (
    FXRegimeClassifierOutput,
    FXRegimeComponent,
)
from fx_agent.vol.tools.vol_risk_premium.schemas import (
    FXVolRiskPremiumMetrics,
    FXVolRiskPremiumOutput,
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


def test_fx_trade_setup_detail_route_calls_tool():
    from api.routes.fx import detail as detail_module

    output = FXTradeSetupOutput(
        pair="EURUSD",
        as_of_date="2026-04-30",
        tenor="1M",
        direction="bullish",
        confidence="medium",
        total_score=1.25,
        summary="EURUSD trade setup is bullish with medium confidence.",
        key_drivers=["Spot momentum positive."],
        risks=["No single signal is extreme."],
        follow_up_questions=["Show me the EURUSD forward curve."],
        signals=[
            FXTradeSetupSignal(
                name="Spot momentum / stretch",
                score=0.5,
                stance="bullish",
                description="Spot momentum positive.",
            )
        ],
        spot_snapshot={"pair": "EURUSD", "current_spot": 1.1736},
        carry_snapshot={"pair": "EURUSD", "carry_annualized_pct": 1.75},
        forward_curve=[],
        realized_vol_snapshot={"pair": "EURUSD", "realized_vol_annualized_pct": 7.5},
    )

    with patch.object(detail_module, "get_fx_trade_setup", return_value=output) as mock_tool:
        result = detail_module.fx_trade_setup_detail(
            pair="EURUSD",
            tenor="1M",
            vol_window_observations=21,
            lookback_days=365,
        )

    assert result == output
    params = mock_tool.call_args.kwargs["params"]
    assert params.pair == "EURUSD"
    assert params.tenor == "1M"


def test_fx_macro_risk_overlay_detail_route_calls_tool():
    from api.routes.fx import detail as detail_module

    output = FXMacroRiskOverlayOutput(
        pair="EURUSD",
        as_of_date="2026-04-30",
        spot=1.1736,
        risk_regime="risk-on / USD softer",
        regime_score=1.2,
        summary="EURUSD macro risk overlay is risk-on / USD softer.",
        implications=["DXY weakness supports non-USD FX versus USD."],
        proxy_rows=[
            FXMacroRiskProxyRow(
                ticker="DXY Curncy",
                label="DXY",
                proxy_family="usd",
                as_of_date="2026-04-30",
                level=99.0,
                daily_change_pct=-0.1,
                monthly_change_pct=-1.2,
                three_month_change_pct=-2.0,
                z_score=-1.1,
                correlation_to_pair=-0.6,
            )
        ],
    )

    with patch.object(detail_module, "get_fx_macro_risk_overlay", return_value=output) as mock_tool:
        result = detail_module.fx_macro_risk_overlay_detail(
            pair="EURUSD",
            lookback_days=365,
            correlation_window_observations=63,
            field_name=None,
        )

    assert result == output
    params = mock_tool.call_args.kwargs["params"]
    assert params.pair == "EURUSD"
    assert params.correlation_window_observations == 63


def test_fx_vol_risk_premium_detail_route_calls_tool():
    from api.routes.fx import detail as detail_module

    output = FXVolRiskPremiumOutput(
        current_metrics=FXVolRiskPremiumMetrics(
            as_of_date="2026-04-30",
            pair="EURUSD",
            tenor="1M",
            implied_vol_pct=6.2,
            realized_vol_annualized_pct=4.9,
            vol_risk_premium_pct=1.3,
            implied_vol_z_score=-0.2,
            premium_z_score=0.5,
            signal="fair",
            suggested_expression="neutral",
            observation_count=252,
        ),
        time_series=[],
    )

    with patch.object(detail_module, "get_fx_vol_risk_premium", return_value=output) as mock_tool:
        result = detail_module.fx_vol_risk_premium_detail(
            pair="EURUSD",
            tenor="1M",
            realized_window_observations=21,
            lookback_days=365,
            field_name=None,
        )

    assert result == output
    params = mock_tool.call_args.kwargs["params"]
    assert params.pair == "EURUSD"
    assert params.tenor == "1M"
    assert params.realized_window_observations == 21


def test_fx_regime_classifier_detail_route_calls_tool():
    from api.routes.fx import detail as detail_module

    output = FXRegimeClassifierOutput(
        as_of_date="2026-04-30",
        anchor_pair="EURUSD",
        overall_regime="risk-on, USD weakness, carry-friendly",
        confidence="high",
        total_score=3.3,
        usd_regime="USD weakness",
        risk_regime="risk-on",
        vol_regime="vol cheap/calm",
        carry_regime="carry-friendly",
        components=[
            FXRegimeComponent(
                name="USD",
                label="USD weakness",
                score=1.0,
                summary="Broad USD weakness.",
            )
        ],
        drivers=["DXY weakness supports EURUSD."],
        risks=[],
        follow_ups=["Show me EURUSD macro risk overlay."],
    )

    with patch.object(detail_module, "classify_fx_regime", return_value=output) as mock_tool:
        result = detail_module.fx_regime_classifier_detail(
            anchor_pair="EURUSD",
            tenor="1M",
            lookback_days=365,
            realized_window_observations=21,
            correlation_window_observations=63,
            field_name=None,
        )

    assert result == output
    params = mock_tool.call_args.kwargs["params"]
    assert params.anchor_pair == "EURUSD"
    assert params.tenor == "1M"


def test_fx_correlation_beta_detail_route_calls_tool():
    from api.routes.fx import detail as detail_module

    output = FXCorrelationBetaOutput(
        pair="EURUSD",
        as_of_date="2026-04-30",
        window_observations=63,
        dominant_driver="DXY",
        dominant_correlation=-0.89,
        rows=[
            FXCorrelationBetaRow(
                ticker="DXY Curncy",
                label="DXY",
                proxy_family="USD",
                observations=63,
                correlation=-0.89,
                beta=-0.93,
                r_squared=0.79,
                proxy_1m_change_pct=-1.5,
                sensitivity_label="high negative sensitivity",
                interpretation="EURUSD tends to fall when DXY rises.",
            )
        ],
        summary="EURUSD's dominant macro sensitivity is DXY.",
        risks=["High macro beta: DXY corr -0.89."],
        follow_ups=["Show me the EURUSD macro risk overlay."],
    )

    with patch.object(detail_module, "get_fx_correlation_beta", return_value=output) as mock_tool:
        result = detail_module.fx_correlation_beta_detail(
            pair="EURUSD",
            lookback_days=365,
            window_observations=63,
            field_name=None,
        )

    assert result == output
    params = mock_tool.call_args.kwargs["params"]
    assert params.pair == "EURUSD"
    assert params.window_observations == 63
