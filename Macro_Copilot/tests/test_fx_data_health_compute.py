from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from fx_agent.diagnostics.tools.data_health import (
    FXDataHealthInput,
    get_fx_data_health,
)


def _coverage_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "instrument_type": "fx_spot",
                "vendor_ticker": "EURUSD Curncy",
                "pair": "EURUSD",
                "tenor": "",
                "observation_count": 260,
                "first_date": "2025-05-01",
                "last_date": "2026-04-30",
            },
            {
                "instrument_type": "fx_forward",
                "vendor_ticker": "EURUSD1M Curncy",
                "pair": "EURUSD",
                "tenor": "1M",
                "observation_count": 250,
                "first_date": "2025-05-01",
                "last_date": "2026-04-30",
            },
            {
                "instrument_type": "fx_vol",
                "vendor_ticker": "EURUSDV1M Curncy",
                "pair": "EURUSD",
                "tenor": "1M",
                "observation_count": 240,
                "first_date": "2025-05-01",
                "last_date": "2026-04-29",
            },
            {
                "instrument_type": "risk_proxy",
                "vendor_ticker": "DXY Curncy",
                "pair": "",
                "tenor": "",
                "observation_count": 260,
                "first_date": "2025-05-01",
                "last_date": "2026-04-30",
            },
            {
                "instrument_type": "fx_spot",
                "vendor_ticker": "GBPUSD Curncy",
                "pair": "GBPUSD",
                "tenor": "",
                "observation_count": 0,
                "first_date": None,
                "last_date": None,
            },
        ]
    )


def test_fx_data_health_reports_family_and_pair_coverage():
    with patch(
        "fx_agent.diagnostics.tools.data_health.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.diagnostics.tools.data_health.compute.pd.read_sql",
        return_value=_coverage_df(),
    ):
        out = get_fx_data_health(FXDataHealthInput(stale_after_days=3))

    assert out.as_of_date == "2026-04-30"
    assert out.summary["instrument_count"] == 5
    assert out.summary["risk_proxy_count"] >= 1
    assert any(family.instrument_type == "fx_spot" for family in out.families)

    eurusd = next(row for row in out.pairs if row.pair == "EURUSD")
    assert eurusd.has_spot is True
    assert eurusd.forward_tenors == ["1M"]
    assert eurusd.vol_tenors == ["1M"]
    assert eurusd.status == "partial"
    assert "forwards:1W,3M,6M" in eurusd.missing

    gbpusd = next(row for row in out.pairs if row.pair == "GBPUSD")
    assert gbpusd.has_spot is False
    assert gbpusd.status == "missing spot"
    assert "fx_spot:GBPUSD" in out.stale_series


def test_fx_data_health_handles_empty_universe():
    with patch(
        "fx_agent.diagnostics.tools.data_health.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.diagnostics.tools.data_health.compute.pd.read_sql",
        return_value=pd.DataFrame(),
    ):
        out = get_fx_data_health(FXDataHealthInput())

    assert out.status == "no fx instruments found"
    assert out.summary["instrument_count"] == 0
    assert "EURUSD" in out.missing_pairs

