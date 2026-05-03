from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from fx_agent.vol.tools.realized_vol import FXRealizedVolInput, get_fx_realized_vol


def _spot_df(days: int = 280) -> pd.DataFrame:
    dates = pd.bdate_range("2025-04-01", periods=days)
    wave = np.sin(np.linspace(0, 16, len(dates))) * 0.01
    values = 1.12 + np.linspace(0, 0.05, len(dates)) + wave
    return pd.DataFrame(
        {
            "trade_date": dates,
            "field_value": values,
            "vendor_ticker": ["EURUSD Curncy"] * len(dates),
            "attributes": [{"pair": "EURUSD"}] * len(dates),
        }
    )


def test_fx_realized_vol_returns_current_metrics_and_series():
    with patch(
        "fx_agent.vol.tools.realized_vol.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.vol.tools.realized_vol.compute.pd.read_sql",
        return_value=_spot_df(),
    ):
        out = get_fx_realized_vol(
            FXRealizedVolInput(pair="eur/usd", window_observations=21),
        )

    metrics = out.current_metrics
    assert metrics.pair == "EURUSD"
    assert metrics.as_of_date == "2026-04-27"
    assert metrics.window_observations == 21
    assert metrics.realized_vol_annualized_pct is not None
    assert metrics.realized_vol_z_score is not None
    assert metrics.daily_return_pct is not None
    assert metrics.observation_count == 280
    assert len(out.time_series) == 280


def test_fx_realized_vol_raises_for_empty_data():
    with patch(
        "fx_agent.vol.tools.realized_vol.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.vol.tools.realized_vol.compute.pd.read_sql",
        return_value=pd.DataFrame(),
    ):
        try:
            get_fx_realized_vol(FXRealizedVolInput(pair="EURUSD"))
        except ValueError as exc:
            assert "No FX spot data found" in str(exc)
        else:
            raise AssertionError("expected ValueError for empty spot data")
