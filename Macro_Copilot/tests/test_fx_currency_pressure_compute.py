from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fx_agent.spot.tools.currency_pressure import (
    FXCurrencyPressureInput,
    scan_currency_pressure,
)


def _spot(pair: str, monthly: float):
    return SimpleNamespace(
        current_metrics=SimpleNamespace(
            pair=pair,
            as_of_date="2026-04-30",
            monthly_change_pct=monthly,
            z_score=0.5,
        )
    )


def test_currency_pressure_aggregates_base_and_quote_contributions():
    monthly_by_pair = {
        "EURUSD": 2.0,
        "GBPUSD": -1.0,
        "EURGBP": 1.5,
    }

    def fake_spot(params):
        if params.pair not in monthly_by_pair:
            raise ValueError("missing")
        return _spot(params.pair, monthly_by_pair[params.pair])

    with patch(
        "fx_agent.spot.tools.currency_pressure.compute.get_fx_spot_level",
        side_effect=fake_spot,
    ):
        out = scan_currency_pressure(
            FXCurrencyPressureInput(currencies=["EUR", "USD", "GBP"])
        )

    by_currency = {row.currency: row for row in out.rows}
    assert by_currency["EUR"].pressure_score_pct == 1.75
    assert by_currency["USD"].pressure_score_pct == -0.5
    assert by_currency["GBP"].pressure_score_pct == -1.25
    assert by_currency["EUR"].signal == "currency strength"
    assert out.strongest_currency == "EUR"


def test_currency_pressure_handles_missing_pairs():
    with patch(
        "fx_agent.spot.tools.currency_pressure.compute.get_fx_spot_level",
        side_effect=ValueError("missing"),
    ):
        out = scan_currency_pressure(FXCurrencyPressureInput(currencies=["EUR"]))

    assert out.rows[0].currency == "EUR"
    assert out.rows[0].pair_count == 0
    assert out.rows[0].signal == "mixed"
