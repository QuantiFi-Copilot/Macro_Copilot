from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fx_agent.forwards.tools.carry_basket import (
    FXCarryBasketInput,
    build_fx_carry_basket,
)
from fx_agent.macro.tools.pair_compare import FXPairCompareInput, compare_fx_pairs
from fx_agent.spot.tools.usd_pressure import FXUSDPressureInput, scan_usd_pressure
from fx_agent.vol.tools.vol_risk_premium_scanner import (
    FXVolRiskPremiumScannerInput,
    scan_fx_vol_risk_premium,
)


def test_scan_fx_vol_risk_premium_ranks_rows():
    def fake_premium(params):
        premium = 1.2 if params.pair == "EURUSD" else 2.4
        return SimpleNamespace(
            current_metrics=SimpleNamespace(
                pair=params.pair,
                as_of_date="2026-04-30",
                implied_vol_pct=6.0,
                realized_vol_annualized_pct=4.5,
                vol_risk_premium_pct=premium,
                premium_z_score=premium / 2,
                signal="vol rich",
                suggested_expression="prefer selling vol",
            )
        )

    with patch(
        "fx_agent.vol.tools.vol_risk_premium_scanner.compute._available_pairs",
        return_value=["EURUSD", "GBPUSD"],
    ), patch(
        "fx_agent.vol.tools.vol_risk_premium_scanner.compute.get_fx_vol_risk_premium",
        side_effect=fake_premium,
    ):
        out = scan_fx_vol_risk_premium(FXVolRiskPremiumScannerInput(top_n=2))

    assert [row.pair for row in out.rows] == ["GBPUSD", "EURUSD"]


def test_scan_usd_pressure_aggregates_pair_signals():
    def fake_spot(params):
        monthly = 1.0 if params.pair in {"EURUSD", "GBPUSD", "AUDUSD"} else -0.5
        return SimpleNamespace(
            current_metrics=SimpleNamespace(
                pair=params.pair,
                as_of_date="2026-04-30",
                current_spot=1.1,
                monthly_change_pct=monthly,
                z_score=0.2,
            )
        )

    with patch(
        "fx_agent.spot.tools.usd_pressure.compute.get_fx_spot_level",
        side_effect=fake_spot,
    ), patch(
        "fx_agent.spot.tools.usd_pressure.compute._dxy_monthly_change",
        return_value=("2026-04-30", -1.5),
    ):
        out = scan_usd_pressure(FXUSDPressureInput())

    assert out.pressure_regime == "broad USD weakness"
    assert len(out.rows) == 6


def test_build_fx_carry_basket_selects_long_and_short_legs():
    carry = SimpleNamespace(
        rows=[
            SimpleNamespace(pair="EURUSD", carry_annualized_pct=1.8),
            SimpleNamespace(pair="GBPUSD", carry_annualized_pct=0.8),
            SimpleNamespace(pair="USDCHF", carry_annualized_pct=-0.4),
        ]
    )
    with patch(
        "fx_agent.forwards.tools.carry_basket.compute.get_fx_carry",
        return_value=carry,
    ), patch(
        "fx_agent.forwards.tools.carry_basket.compute._pair_risk",
        return_value=(0.1, 5.0),
    ):
        out = build_fx_carry_basket(FXCarryBasketInput(basket_size=1))

    assert out.long_legs[0].pair == "EURUSD"
    assert out.short_legs[0].pair == "USDCHF"


def test_compare_fx_pairs_picks_cleaner_expression():
    def fake_setup(params):
        score = 1.4 if params.pair == "EURUSD" else 0.2
        return SimpleNamespace(
            pair=params.pair,
            direction="bullish",
            confidence="medium",
            total_score=score,
            spot_snapshot={"current_spot": 1.1},
            carry_snapshot={"carry_annualized_pct": 1.5},
            realized_vol_snapshot={"realized_vol_annualized_pct": 5.0},
        )

    def fake_overlay(params):
        return SimpleNamespace(risk_regime="risk-on / USD softer", regime_score=1.0)

    def fake_premium(params):
        return SimpleNamespace(current_metrics=SimpleNamespace(vol_risk_premium_pct=1.0))

    with patch(
        "fx_agent.macro.tools.pair_compare.compute.get_fx_trade_setup",
        side_effect=fake_setup,
    ), patch(
        "fx_agent.macro.tools.pair_compare.compute.get_fx_macro_risk_overlay",
        side_effect=fake_overlay,
    ), patch(
        "fx_agent.macro.tools.pair_compare.compute.get_fx_vol_risk_premium",
        side_effect=fake_premium,
    ):
        out = compare_fx_pairs(FXPairCompareInput(pair_1="EURUSD", pair_2="GBPUSD"))

    assert out.preferred_pair == "EURUSD"
    assert len(out.rows) == 2
