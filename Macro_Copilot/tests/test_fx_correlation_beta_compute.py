from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from fx_agent.macro.tools.correlation_beta import (
    FXCorrelationBetaInput,
    get_fx_correlation_beta,
)


def _fx_df(days: int = 90) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=days)
    values = 1.10 + np.linspace(0, 0.08, days)
    return pd.DataFrame({"trade_date": dates, "field_value": values})


def _proxy_df(days: int = 90) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=days)
    dxy = 100 - np.linspace(0, 3, days)
    spx = 5000 + np.linspace(0, 500, days)
    rows = []
    for date, value in zip(dates, dxy):
        rows.append(
            {
                "vendor_ticker": "DXY Curncy",
                "label": "DXY",
                "proxy_family": "USD",
                "trade_date": date,
                "field_value": value,
            }
        )
    for date, value in zip(dates, spx):
        rows.append(
            {
                "vendor_ticker": "SPX Index",
                "label": "S&P 500",
                "proxy_family": "Risk",
                "trade_date": date,
                "field_value": value,
            }
        )
    return pd.DataFrame(rows)


def test_fx_correlation_beta_ranks_dominant_driver():
    with patch(
        "fx_agent.macro.tools.correlation_beta.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.macro.tools.correlation_beta.compute.pd.read_sql",
        side_effect=[_fx_df(), _proxy_df()],
    ):
        out = get_fx_correlation_beta(
            FXCorrelationBetaInput(pair="eur/usd", window_observations=63)
        )

    assert out.pair == "EURUSD"
    assert out.dominant_driver in {"DXY", "S&P 500"}
    assert len(out.rows) == 2
    assert out.rows[0].correlation is not None
    assert out.rows[0].beta is not None
    assert out.rows[0].r_squared is not None


def test_fx_correlation_beta_raises_for_missing_fx_data():
    with patch(
        "fx_agent.macro.tools.correlation_beta.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.macro.tools.correlation_beta.compute.pd.read_sql",
        side_effect=[pd.DataFrame(), _proxy_df()],
    ):
        try:
            get_fx_correlation_beta(FXCorrelationBetaInput(pair="EURUSD"))
        except ValueError as exc:
            assert "No FX spot data found" in str(exc)
        else:
            raise AssertionError("expected ValueError for missing FX data")

