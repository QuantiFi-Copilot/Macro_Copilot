from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from fx_agent.forwards.tools.fx_carry import FXCarryInput, get_fx_carry


def _carry_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "pair": "EURUSD",
                "spot_date": "2026-04-30",
                "forward_date": "2026-04-30",
                "spot": 1.1736,
                "tenor": "1M",
                "forward_points": 17.09,
            },
            {
                "pair": "USDJPY",
                "spot_date": "2026-04-30",
                "forward_date": "2026-04-30",
                "spot": 156.46,
                "tenor": "1M",
                "forward_points": -42.18,
            },
        ]
    )


def test_fx_carry_returns_ranked_rows():
    with patch(
        "fx_agent.forwards.tools.fx_carry.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.forwards.tools.fx_carry.compute.pd.read_sql",
        return_value=_carry_df(),
    ):
        out = get_fx_carry(FXCarryInput(tenor="1m"))

    assert out.tenor == "1M"
    assert [row.pair for row in out.rows] == ["EURUSD", "USDJPY"]

    eurusd = out.rows[0]
    assert eurusd.forward_points_spot_units == 0.001709
    assert eurusd.outright_forward == 1.175309
    assert eurusd.carry_bps_spot > 0
    assert eurusd.carry_annualized_pct > 0
    assert eurusd.carry_signal == "High carry"

    usdjpy = out.rows[1]
    assert usdjpy.forward_points_spot_units == -0.4218
    assert usdjpy.carry_bps_spot < 0
    assert usdjpy.carry_signal == "Low carry"


def test_fx_carry_empty_data_returns_empty_rows():
    with patch(
        "fx_agent.forwards.tools.fx_carry.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.forwards.tools.fx_carry.compute.pd.read_sql",
        return_value=pd.DataFrame(),
    ):
        out = get_fx_carry(FXCarryInput(tenor="3M"))

    assert out.tenor == "3M"
    assert out.rows == []
