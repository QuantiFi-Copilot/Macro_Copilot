from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from fx_agent.spot.tools.spot_levels import FXSpotLevelInput, get_fx_spot_level


def _spot_df(days: int = 280) -> pd.DataFrame:
    dates = pd.bdate_range("2025-04-01", periods=days)
    values = np.linspace(1.08, 1.18, len(dates))
    return pd.DataFrame(
        {
            "trade_date": dates,
            "field_value": values,
            "vendor_ticker": ["EURUSD Curncy"] * len(dates),
            "attributes": [{"pair": "EURUSD"}] * len(dates),
        }
    )


def test_fx_spot_level_returns_current_metrics():
    with patch(
        "fx_agent.spot.tools.spot_levels.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.spot.tools.spot_levels.compute.pd.read_sql",
        return_value=_spot_df(),
    ):
        out = get_fx_spot_level(
            FXSpotLevelInput(pair="eur/usd", lookback_days=365),
        )

    metrics = out.current_metrics
    assert metrics.pair == "EURUSD"
    assert metrics.as_of_date == "2026-04-27"
    assert metrics.current_spot == 1.18
    assert metrics.daily_change_pct is not None
    assert metrics.weekly_change_pct is not None
    assert metrics.monthly_change_pct is not None
    assert metrics.z_score is not None
    assert metrics.high_252d is not None
    assert metrics.low_252d is not None
    assert metrics.percentile_252d is not None
    assert metrics.observation_count == 280


def test_fx_spot_level_raises_for_empty_data():
    with patch(
        "fx_agent.spot.tools.spot_levels.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.spot.tools.spot_levels.compute.pd.read_sql",
        return_value=pd.DataFrame(),
    ):
        try:
            get_fx_spot_level(FXSpotLevelInput(pair="EURUSD"))
        except ValueError as exc:
            assert "No FX spot data found" in str(exc)
        else:
            raise AssertionError("expected ValueError for empty spot data")
