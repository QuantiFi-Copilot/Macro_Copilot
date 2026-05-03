from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from fx_agent.vol.tools.realized_vol.schemas import (
    FXRealizedVolMetrics,
    FXRealizedVolOutput,
    FXRealizedVolTimeSeriesRow,
)
from fx_agent.vol.tools.vol_risk_premium import (
    FXVolRiskPremiumInput,
    get_fx_vol_risk_premium,
)


def _implied_df() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=90)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "implied_vol_pct": [7.0 + i * 0.01 for i in range(len(dates))],
        }
    )


def _realized_output() -> FXRealizedVolOutput:
    dates = pd.bdate_range("2026-01-01", periods=90)
    rows = [
        FXRealizedVolTimeSeriesRow(
            date=date.strftime("%Y-%m-%d"),
            realized_vol_annualized_pct=5.0 + i * 0.005,
        )
        for i, date in enumerate(dates)
    ]
    return FXRealizedVolOutput(
        current_metrics=FXRealizedVolMetrics(
            as_of_date=dates[-1].strftime("%Y-%m-%d"),
            pair="EURUSD",
            spot=1.17,
            window_observations=21,
            realized_vol_annualized_pct=rows[-1].realized_vol_annualized_pct,
            realized_vol_z_score=0.2,
            daily_return_pct=0.1,
            observation_count=90,
        ),
        time_series=rows,
    )


def test_fx_vol_risk_premium_compares_implied_and_realized():
    with patch(
        "fx_agent.vol.tools.vol_risk_premium.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.vol.tools.vol_risk_premium.compute.pd.read_sql",
        return_value=_implied_df(),
    ), patch(
        "fx_agent.vol.tools.vol_risk_premium.compute.get_fx_realized_vol",
        return_value=_realized_output(),
    ):
        out = get_fx_vol_risk_premium(FXVolRiskPremiumInput(pair="eur/usd"))

    metrics = out.current_metrics
    assert metrics.pair == "EURUSD"
    assert metrics.tenor == "1M"
    assert metrics.implied_vol_pct > metrics.realized_vol_annualized_pct
    assert metrics.vol_risk_premium_pct is not None
    assert metrics.signal in {"vol rich", "vol cheap", "fair"}
    assert len(out.time_series) == 90
