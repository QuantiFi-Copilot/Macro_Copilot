from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fx_agent.macro.tools.usd_thesis_monitor import (
    FXUSDThesisInput,
    get_fx_usd_thesis_monitor,
)


def _pressure_row(pair: str, pressure: float, z_score: float = 0.5) -> SimpleNamespace:
    return SimpleNamespace(
        pair=pair,
        as_of_date="2026-04-30",
        spot=1.1,
        monthly_change_pct=-pressure,
        usd_pressure_pct=pressure,
        z_score=z_score,
        signal="USD strength" if pressure > 0 else "USD weakness",
    )


def test_usd_thesis_monitor_flags_hostile_long_usd_regime():
    pressure = SimpleNamespace(
        as_of_date="2026-04-30",
        pressure_regime="broad USD weakness",
        usd_pressure_score=-2.1,
        dxy_monthly_change_pct=-1.6,
        rows=[
            _pressure_row("EURUSD", -1.2, 0.6),
            _pressure_row("AUDUSD", -3.9, 2.1),
        ],
    )
    regime = SimpleNamespace(
        as_of_date="2026-04-30",
        usd_regime="USD weakness",
        risk_regime="risk-on",
        vol_regime="vol cheap/calm",
        carry_regime="carry-friendly",
    )
    scanner = SimpleNamespace(
        rows=[
            SimpleNamespace(
                pair="AUDUSD",
                z_score=2.1,
                monthly_change_pct=3.9,
                signal="Bullish breakout",
            )
        ]
    )
    beta = SimpleNamespace(
        dominant_driver="DXY",
        dominant_correlation=-0.89,
        summary="EURUSD's dominant macro sensitivity is DXY.",
    )

    with patch(
        "fx_agent.macro.tools.usd_thesis_monitor.compute.scan_usd_pressure",
        return_value=pressure,
    ), patch(
        "fx_agent.macro.tools.usd_thesis_monitor.compute.classify_fx_regime",
        return_value=regime,
    ), patch(
        "fx_agent.macro.tools.usd_thesis_monitor.compute.run_fx_scanner",
        return_value=scanner,
    ), patch(
        "fx_agent.macro.tools.usd_thesis_monitor.compute.get_fx_correlation_beta",
        return_value=beta,
    ):
        out = get_fx_usd_thesis_monitor(FXUSDThesisInput(usd_view="long_usd"))

    assert out.thesis_status == "hostile"
    assert out.confidence == "high"
    assert out.challenges
    assert not out.best_expressions
    assert out.stretched_counter_moves[0].pair == "AUDUSD"


def test_usd_thesis_monitor_supports_short_usd_regime():
    pressure = SimpleNamespace(
        as_of_date="2026-04-30",
        pressure_regime="broad USD weakness",
        usd_pressure_score=-1.4,
        dxy_monthly_change_pct=-1.1,
        rows=[
            _pressure_row("EURUSD", -1.2, 0.6),
            _pressure_row("GBPUSD", -1.0, 0.8),
        ],
    )
    regime = SimpleNamespace(
        as_of_date="2026-04-30",
        usd_regime="USD weakness",
        risk_regime="risk-on",
        vol_regime="vol cheap/calm",
        carry_regime="carry-friendly",
    )
    scanner = SimpleNamespace(rows=[])
    beta = SimpleNamespace(
        dominant_driver="DXY",
        dominant_correlation=-0.7,
        summary="EURUSD's dominant macro sensitivity is DXY.",
    )

    with patch(
        "fx_agent.macro.tools.usd_thesis_monitor.compute.scan_usd_pressure",
        return_value=pressure,
    ), patch(
        "fx_agent.macro.tools.usd_thesis_monitor.compute.classify_fx_regime",
        return_value=regime,
    ), patch(
        "fx_agent.macro.tools.usd_thesis_monitor.compute.run_fx_scanner",
        return_value=scanner,
    ), patch(
        "fx_agent.macro.tools.usd_thesis_monitor.compute.get_fx_correlation_beta",
        return_value=beta,
    ):
        out = get_fx_usd_thesis_monitor(FXUSDThesisInput(usd_view="short_usd"))

    assert out.thesis_status == "supportive"
    assert out.best_expressions
    assert out.best_expressions[0].expression == "long EURUSD"
    assert out.confirmations

