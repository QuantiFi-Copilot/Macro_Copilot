"""Offline compute tests for implied_forward_curve (§7-C, Bucket-1B).

Correctness pinned by independent forward parity (forward_rate_between per
anchor) + the Panel output contract.  DB mocked.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from rates_agent.ois.tools.implied_forward_curve import (
    CONFIG_PATH,
    ImpliedForwardCurveInput,
    ImpliedForwardCurveOutput,
    calculate_implied_forward_curve,
)
from shared.analytics.curve_bootstrap import forward_rate_between, tenor_to_years
from shared.config import clear_tool_config_cache, load_tool_config

_FETCH = (
    "rates_agent.ois.tools.implied_forward_curve.compute._fetch_curve"
)
_TENORS = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]
_RATES = [3.0, 3.3, 3.5, 3.7, 3.85, 4.0, 4.2]


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _curve(n=12, cf="USD_SOFR_OIS"):
    dates = pd.bdate_range("2026-05-01", periods=n)
    return pd.DataFrame([
        {"trade_date": d, "tenor": t, "field_value": r}
        for d in dates for t, r in zip(_TENORS, _RATES)
    ])


class TestForwardStrip:
    def test_matches_independent_forwards(self):
        with patch(_FETCH, return_value=_curve()):
            out = calculate_implied_forward_curve(
                engine=None,
                params=ImpliedForwardCurveInput(
                    curve_family="USD_SOFR_OIS", forward_horizon="1Y",
                ),
            )
        assert "error" not in out, out
        cm = out["current_metrics"]
        yrs = [tenor_to_years(t) for t in _TENORS]
        dec = [r / 100 for r in _RATES]
        by_anchor = {p["anchor_tenor"]: p for p in cm["forward_curve"]}
        for anchor in ["1Y", "2Y", "5Y", "10Y"]:
            a = tenor_to_years(anchor)
            expected = forward_rate_between(yrs, dec, a, a + 1.0) * 100
            assert by_anchor[anchor]["forward_rate_pct"] == pytest.approx(
                round(expected, 4), abs=1e-4,
            )
            assert by_anchor[anchor]["forward_label"] == f"{anchor}1Y"

    def test_panel_output_contract(self):
        with patch(_FETCH, return_value=_curve()):
            out = calculate_implied_forward_curve(
                engine=None, params=ImpliedForwardCurveInput(curve_family="X"),
            )
        # The bridge reconstructs the typed Panel via model_validate.
        validated = ImpliedForwardCurveOutput.model_validate(out)
        panel = validated.forward_strip
        assert list(panel.payload.columns) == [f"{a}_fwd" for a in
                                                ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]]
        assert len(panel.payload) == 12
        assert all(u.value == "percent" for u in panel.units_by_column.values())
        assert out["current_metrics"]["n_anchors"] == 6

    def test_default_horizon_applied(self):
        with patch(_FETCH, return_value=_curve()):
            out = calculate_implied_forward_curve(
                engine=None, params=ImpliedForwardCurveInput(curve_family="X"),
            )
        assert out["current_metrics"]["forward_horizon"] == "1Y"

    def test_deterministic(self):
        c = _curve()
        with patch(_FETCH, return_value=c):
            a = calculate_implied_forward_curve(
                engine=None, params=ImpliedForwardCurveInput(curve_family="X"))
            b = calculate_implied_forward_curve(
                engine=None, params=ImpliedForwardCurveInput(curve_family="X"))
        assert a["current_metrics"] == b["current_metrics"]


class TestConfig:
    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_implied_forward_curve_tool"
        assert cfg.tool.domain == "ois"
        assert cfg.convention_value("default_swap_rate_field") == "PX_LAST"
        assert cfg.convention_value("default_forward_horizon") == "1Y"


class TestRefusals:
    def test_empty_fetch_error(self):
        with patch(_FETCH, return_value=pd.DataFrame()):
            out = calculate_implied_forward_curve(
                engine=None, params=ImpliedForwardCurveInput(curve_family="ZZZ"))
        assert "error" in out and "No OIS data" in out["error"]

    def test_anchors_past_grid_skipped(self):
        # All anchors past the 30Y grid → no strip → error.
        with patch(_FETCH, return_value=_curve()):
            out = calculate_implied_forward_curve(
                engine=None,
                params=ImpliedForwardCurveInput(
                    curve_family="X", anchor_tenors=["40Y", "50Y"],
                ),
            )
        assert "error" in out and "could not compute" in out["error"]

    def test_too_few_anchors_rejected_at_schema(self):
        with pytest.raises(ValueError):
            ImpliedForwardCurveInput(curve_family="X", anchor_tenors=["5Y"])

    def test_duplicate_anchors_rejected_at_schema(self):
        with pytest.raises(ValueError):
            ImpliedForwardCurveInput(
                curve_family="X", anchor_tenors=["5Y", "5Y", "10Y"])
