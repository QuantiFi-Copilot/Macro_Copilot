from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from fx_agent.macro.tools.rates_overlay import (
    FXRatesDifferentialInput,
    get_fx_rates_differential_overlay,
)


def _fx_setup(direction: str = "bullish") -> SimpleNamespace:
    return SimpleNamespace(
        summary=f"EURUSD trade setup is {direction}.",
        direction=direction,
        total_score=1.2 if direction == "bullish" else -1.2,
        key_drivers=["FX carry is constructive."],
        risks=["No single FX signal is extreme."],
    )


def _rates_df() -> pd.DataFrame:
    dates = pd.bdate_range("2026-03-01", periods=30)
    rows = []
    for i, date in enumerate(dates):
        rows.append(
            {
                "curve_family": "UST",
                "tenor": "2Y",
                "instrument_type": "sovereign_benchmark",
                "trade_date": date,
                "field_value": 4.0 - i * 0.01,
            }
        )
        rows.append(
            {
                "curve_family": "DE_BUND",
                "tenor": "2Y",
                "instrument_type": "sovereign_benchmark",
                "trade_date": date,
                "field_value": 2.5,
            }
        )
    return pd.DataFrame(rows)


def test_fx_rates_overlay_gracefully_reports_missing_rates_data():
    with patch(
        "fx_agent.macro.tools.rates_overlay.compute.get_fx_trade_setup",
        return_value=_fx_setup("bullish"),
    ), patch(
        "fx_agent.macro.tools.rates_overlay.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.macro.tools.rates_overlay.compute.pd.read_sql",
        return_value=pd.DataFrame(),
    ):
        out = get_fx_rates_differential_overlay(FXRatesDifferentialInput())

    assert out.rates_status == "missing data"
    assert out.consistency_label == "rates unavailable"
    assert out.rates_snapshot is None
    assert out.missing_data
    assert "Rates overlay is unavailable" in out.conclusion


def test_fx_rates_overlay_compares_available_differential():
    with patch(
        "fx_agent.macro.tools.rates_overlay.compute.get_fx_trade_setup",
        return_value=_fx_setup("bullish"),
    ), patch(
        "fx_agent.macro.tools.rates_overlay.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.macro.tools.rates_overlay.compute.pd.read_sql",
        return_value=_rates_df(),
    ):
        out = get_fx_rates_differential_overlay(FXRatesDifferentialInput())

    assert out.rates_status == "available"
    assert out.rates_snapshot is not None
    assert out.rates_snapshot.label == "UST-DE_BUND 2Y"
    assert out.rates_snapshot.monthly_change_bps is not None
    assert out.consistency_label == "rates confirm FX setup"

