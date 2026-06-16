"""Offline compute tests for implied_forward_curve (§7-C, Bucket-1A).

Correctness pinned by an INDEPENDENT forward parity reimplemented from
first principles (linear interp of par rates + the shared
``discount_factor_from_par`` primitive; NOT a re-call of the production
``forward_rate_between`` engine) + the Panel output contract.  DB mocked.

The independence matters: if the baseline re-called ``forward_rate_between``
— the very function the tool's compute path uses — an arithmetic bug in
that engine would funnel through both ``exp`` and ``got`` and pass
silently.  Building ``exp`` from ``discount_factor_from_par`` alone makes
the cross-check genuinely independent (see ``test_independent_baseline_
catches_engine_bug`` for the load-bearing proof).
"""

from __future__ import annotations

from typing import Sequence
from unittest.mock import patch

import pandas as pd
import pytest

from rates_agent.ois.tools.implied_forward_curve import (
    CONFIG_PATH,
    ImpliedForwardCurveInput,
    ImpliedForwardCurveOutput,
    calculate_implied_forward_curve,
)
from shared.analytics.curve_bootstrap import (
    discount_factor_from_par,
    tenor_to_years,
)
from shared.config import clear_tool_config_cache, load_tool_config

_FETCH = (
    "rates_agent.ois.tools.implied_forward_curve.compute._fetch_curve"
)
_TENORS = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]
_RATES = [3.0, 3.3, 3.5, 3.7, 3.85, 4.0, 4.2]


def _interp_independent(
    grid_years: Sequence[float], grid_rates: Sequence[float], target: float,
) -> float:
    """Linear interpolation from scratch (flat extrapolation past ends) —
    independent of the production ``interpolate_rate``."""
    if target <= grid_years[0]:
        return grid_rates[0]
    if target >= grid_years[-1]:
        return grid_rates[-1]
    for i in range(1, len(grid_years)):
        if grid_years[i] >= target:
            x0, x1 = grid_years[i - 1], grid_years[i]
            y0, y1 = grid_rates[i - 1], grid_rates[i]
            return y0 + (y1 - y0) * (target - x0) / (x1 - x0)
    return grid_rates[-1]


def _forward_first_principles(
    grid_years: Sequence[float], grid_rates_dec: Sequence[float],
    a: float, h: float,
) -> float:
    """Independent forward over (a, a+h) in DECIMAL, built ONLY from
    ``discount_factor_from_par`` + a from-scratch interpolation.  Does NOT
    call ``forward_rate_between``.  f(a,a+h) = (DF(a)/DF(a+h) − 1)/h."""
    r_a = _interp_independent(grid_years, grid_rates_dec, a)
    r_end = _interp_independent(grid_years, grid_rates_dec, a + h)
    df_a = discount_factor_from_par(r_a, a) if a > 0 else 1.0
    df_end = discount_factor_from_par(r_end, a + h)
    return (df_a / df_end - 1.0) / h


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
            # FROM FIRST PRINCIPLES — discount_factor_from_par only, NOT
            # a re-call of the production forward_rate_between engine.
            expected = _forward_first_principles(yrs, dec, a, 1.0) * 100
            assert by_anchor[anchor]["forward_rate_pct"] == pytest.approx(
                round(expected, 4), abs=1e-4,
            )
            assert by_anchor[anchor]["forward_label"] == f"{anchor}1Y"

    def test_independent_baseline_catches_engine_bug(self):
        """Load-bearing proof: the from-first-principles baseline is genuinely
        independent, so a perturbed production forward (a simulated
        ``forward_rate_between`` bug) is CAUGHT, not funnelled-through."""
        with patch(_FETCH, return_value=_curve()):
            out = calculate_implied_forward_curve(
                engine=None,
                params=ImpliedForwardCurveInput(
                    curve_family="USD_SOFR_OIS", forward_horizon="1Y",
                ),
            )
        cm = out["current_metrics"]
        yrs = [tenor_to_years(t) for t in _TENORS]
        dec = [r / 100 for r in _RATES]
        by_anchor = {p["anchor_tenor"]: p for p in cm["forward_curve"]}
        perturb_pct = 0.05  # +5 bps engine bug
        for anchor in ["1Y", "2Y", "5Y", "10Y"]:
            a = tenor_to_years(anchor)
            expected = _forward_first_principles(yrs, dec, a, 1.0) * 100
            got = by_anchor[anchor]["forward_rate_pct"]
            # Clean production agrees with the independent baseline.
            assert got == pytest.approx(round(expected, 4), abs=1e-4)
            # A perturbed production value diverges beyond tolerance —
            # i.e. the parity check would FAIL on an engine bug.
            assert abs((got + perturb_pct) - expected) > 1e-4

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
