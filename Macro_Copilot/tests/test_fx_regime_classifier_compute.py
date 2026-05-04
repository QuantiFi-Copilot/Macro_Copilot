from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fx_agent.macro.tools.regime_classifier import (
    FXRegimeClassifierInput,
    classify_fx_regime,
)


def test_fx_regime_classifier_combines_constructive_components():
    usd = SimpleNamespace(
        as_of_date="2026-04-30",
        pressure_regime="broad USD weakness",
        usd_pressure_score=-1.2,
        dxy_monthly_change_pct=-1.5,
        rows=[],
    )
    risk = SimpleNamespace(
        as_of_date="2026-04-30",
        risk_regime="risk-on / USD softer",
        regime_score=1.4,
        implications=[],
    )
    vol_scan = SimpleNamespace(
        rows=[
            SimpleNamespace(vol_risk_premium_pct=0.1),
            SimpleNamespace(vol_risk_premium_pct=0.2),
        ]
    )
    carry = SimpleNamespace(
        long_legs=[SimpleNamespace(pair="EURUSD"), SimpleNamespace(pair="GBPUSD")],
        excluded_pairs=[],
    )

    with patch(
        "fx_agent.macro.tools.regime_classifier.compute.scan_usd_pressure",
        return_value=usd,
    ), patch(
        "fx_agent.macro.tools.regime_classifier.compute.get_fx_macro_risk_overlay",
        return_value=risk,
    ), patch(
        "fx_agent.macro.tools.regime_classifier.compute.scan_fx_vol_risk_premium",
        return_value=vol_scan,
    ), patch(
        "fx_agent.macro.tools.regime_classifier.compute.build_fx_carry_basket",
        return_value=carry,
    ):
        out = classify_fx_regime(FXRegimeClassifierInput(anchor_pair="eur/usd"))

    assert out.anchor_pair == "EURUSD"
    assert out.usd_regime == "USD weakness"
    assert out.risk_regime == "risk-on"
    assert out.vol_regime == "vol cheap/calm"
    assert out.carry_regime == "carry-friendly"
    assert out.confidence == "high"
    assert "risk-on" in out.overall_regime


def test_fx_regime_classifier_flags_defensive_regime():
    usd = SimpleNamespace(
        as_of_date="2026-04-30",
        pressure_regime="broad USD strength",
        usd_pressure_score=1.1,
        dxy_monthly_change_pct=2.0,
        rows=[],
    )
    risk = SimpleNamespace(
        as_of_date="2026-04-30",
        risk_regime="risk-off / USD support",
        regime_score=-1.2,
        implications=[],
    )
    vol_scan = SimpleNamespace(
        rows=[
            SimpleNamespace(vol_risk_premium_pct=3.0),
            SimpleNamespace(vol_risk_premium_pct=2.5),
        ]
    )
    carry = SimpleNamespace(long_legs=[], excluded_pairs=["EURUSD: vol high"])

    with patch(
        "fx_agent.macro.tools.regime_classifier.compute.scan_usd_pressure",
        return_value=usd,
    ), patch(
        "fx_agent.macro.tools.regime_classifier.compute.get_fx_macro_risk_overlay",
        return_value=risk,
    ), patch(
        "fx_agent.macro.tools.regime_classifier.compute.scan_fx_vol_risk_premium",
        return_value=vol_scan,
    ), patch(
        "fx_agent.macro.tools.regime_classifier.compute.build_fx_carry_basket",
        return_value=carry,
    ):
        out = classify_fx_regime(FXRegimeClassifierInput())

    assert out.usd_regime == "USD strength"
    assert out.risk_regime == "risk-off"
    assert out.vol_regime == "vol rich"
    assert out.carry_regime == "carry unattractive"
    assert out.total_score < 0
    assert out.risks

