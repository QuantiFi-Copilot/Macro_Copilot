from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from fx_agent.macro.tools.risk_overlay import (
    FXMacroRiskOverlayInput,
    get_fx_macro_risk_overlay,
)


def _fx_df() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=90)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "field_value": [1.10 + i * 0.001 for i in range(len(dates))],
        }
    )


def _proxy_df() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=90)
    rows = []
    proxies = [
        ("DXY Curncy", "DXY", "usd", 100.0, -0.02),
        ("VIX Index", "VIX", "equity_vol", 18.0, -0.01),
        ("MOVE Index", "MOVE", "rates_vol", 110.0, -0.015),
        ("SPX Index", "S&P 500", "equity", 5000.0, 0.02),
        ("XAU Curncy", "Gold Spot", "commodity", 2300.0, 0.01),
    ]
    for ticker, label, family, start, drift in proxies:
        for i, date in enumerate(dates):
            rows.append(
                {
                    "vendor_ticker": ticker,
                    "label": label,
                    "proxy_family": family,
                    "trade_date": date,
                    "field_value": start * (1 + drift * i / len(dates)),
                }
            )
    return pd.DataFrame(rows)


def test_fx_macro_risk_overlay_returns_proxy_rows_and_regime():
    with patch(
        "fx_agent.macro.tools.risk_overlay.compute.get_db_engine",
        return_value=MagicMock(name="engine"),
    ), patch(
        "fx_agent.macro.tools.risk_overlay.compute.pd.read_sql",
        side_effect=[_fx_df(), _proxy_df()],
    ):
        out = get_fx_macro_risk_overlay(
            FXMacroRiskOverlayInput(pair="eur/usd", correlation_window_observations=63)
        )

    assert out.pair == "EURUSD"
    assert out.spot > 1.0
    assert out.risk_regime in {
        "risk-on / USD softer",
        "risk-off / USD support",
        "mixed macro risk",
    }
    assert len(out.proxy_rows) == 5
    assert any(row.label == "DXY" for row in out.proxy_rows)
    assert all(row.correlation_to_pair is not None for row in out.proxy_rows)
