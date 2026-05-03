from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from fx_agent.forwards.tools.forward_curve import (
    FXForwardCurveInput,
    get_fx_forward_curve,
)


def _forward_curve_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "pair": "EURUSD",
                "spot_date": "2026-04-30",
                "forward_date": "2026-04-30",
                "spot": 1.1736,
                "tenor": "3M",
                "forward_points": 52.1,
            },
            {
                "pair": "EURUSD",
                "spot_date": "2026-04-30",
                "forward_date": "2026-04-30",
                "spot": 1.1736,
                "tenor": "1M",
                "forward_points": 17.09,
            },
            {
                "pair": "EURUSD",
                "spot_date": "2026-04-30",
                "forward_date": "2026-04-30",
                "spot": 1.1736,
                "tenor": "1W",
                "forward_points": 4.1,
            },
        ]
    )


def test_fx_forward_curve_returns_tenor_ordered_rows():
    with patch(
        "fx_agent.forwards.tools.forward_curve.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.forwards.tools.forward_curve.compute.pd.read_sql",
        return_value=_forward_curve_df(),
    ):
        out = get_fx_forward_curve(FXForwardCurveInput(pair="eur/usd"))

    assert out.pair == "EURUSD"
    assert [row.tenor for row in out.rows] == ["1W", "1M", "3M"]

    one_month = out.rows[1]
    assert one_month.tenor_days == 21
    assert one_month.forward_points_spot_units == 0.001709
    assert one_month.outright_forward == 1.175309
    assert one_month.carry_bps_spot > 0
    assert one_month.carry_annualized_pct > 0


def test_fx_forward_curve_ignores_unknown_tenors():
    df = _forward_curve_df()
    df.loc[len(df)] = {
        "pair": "EURUSD",
        "spot_date": "2026-04-30",
        "forward_date": "2026-04-30",
        "spot": 1.1736,
        "tenor": "9M",
        "forward_points": 155.0,
    }

    with patch(
        "fx_agent.forwards.tools.forward_curve.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.forwards.tools.forward_curve.compute.pd.read_sql",
        return_value=df,
    ):
        out = get_fx_forward_curve(FXForwardCurveInput(pair="EURUSD"))

    assert [row.tenor for row in out.rows] == ["1W", "1M", "3M"]
