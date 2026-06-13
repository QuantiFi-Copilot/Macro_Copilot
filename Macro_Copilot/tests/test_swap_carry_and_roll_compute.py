"""Offline compute tests for swap_carry_and_roll (§7-C, Bucket-1B).

Correctness is pinned by the exact curve-interpolation formula
(roll = s(T)−s(T−h), carry = s(T)−s(h), total = roll+carry) recomputed
independently, plus the sign behaviour on upward vs downward curves.  DB
mocked.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from rates_agent.ois.tools.swap_carry_and_roll import (
    CONFIG_PATH,
    SwapCarryAndRollInput,
    calculate_swap_carry_and_roll,
)
from shared.analytics.curve_bootstrap import interpolate_rate, tenor_to_years
from shared.config import clear_tool_config_cache, load_tool_config

_FETCH = (
    "rates_agent.ois.tools.swap_carry_and_roll.compute._fetch_curve"
)
_TENORS = ["1Y", "2Y", "5Y", "10Y", "30Y"]


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _curve(rates, *, n=15, cf="USD_SOFR_OIS"):
    dates = pd.bdate_range("2026-05-01", periods=n)
    return pd.DataFrame([
        {"trade_date": d, "tenor": t, "field_value": r}
        for d in dates for t, r in zip(_TENORS, rates)
    ])


def _independent(rates, tenor, horizon):
    yrs = [tenor_to_years(t) for t in _TENORS]
    dec = [r / 100 for r in rates]
    T, h = tenor_to_years(tenor), tenor_to_years(horizon)
    sT = interpolate_rate(yrs, dec, T)
    sTmh = interpolate_rate(yrs, dec, T - h)
    sh = interpolate_rate(yrs, dec, h)
    return ((sT - sTmh) * 10000, (sT - sh) * 10000)


class TestFormula:
    def test_matches_independent_formula_upward(self):
        rates = [3.0, 3.3, 3.7, 4.0, 4.2]
        with patch(_FETCH, return_value=_curve(rates)):
            out = calculate_swap_carry_and_roll(
                engine=None,
                params=SwapCarryAndRollInput(
                    curve_family="USD_SOFR_OIS", tenor="10Y", horizon="3M",
                ),
            )
        assert "error" not in out, out
        cm = out["current_metrics"]
        roll, carry = _independent(rates, "10Y", "3M")
        assert cm["roll_bps"] == pytest.approx(roll, abs=0.01)
        assert cm["carry_bps"] == pytest.approx(carry, abs=0.01)
        assert cm["total_carry_roll_bps"] == pytest.approx(
            cm["roll_bps"] + cm["carry_bps"], abs=0.01,
        )
        # Upward curve ⇒ receiver carry+roll both positive.
        assert cm["roll_bps"] > 0 and cm["carry_bps"] > 0

    def test_downward_curve_flips_signs(self):
        rates = [4.5, 4.2, 3.9, 3.6, 3.3]  # inverted
        with patch(_FETCH, return_value=_curve(rates)):
            out = calculate_swap_carry_and_roll(
                engine=None,
                params=SwapCarryAndRollInput(
                    curve_family="USD_SOFR_OIS", tenor="10Y", horizon="3M",
                ),
            )
        cm = out["current_metrics"]
        assert cm["roll_bps"] < 0 and cm["carry_bps"] < 0

    def test_legs_surfaced(self):
        rates = [3.0, 3.3, 3.7, 4.0, 4.2]
        with patch(_FETCH, return_value=_curve(rates)):
            out = calculate_swap_carry_and_roll(
                engine=None,
                params=SwapCarryAndRollInput(curve_family="X", tenor="10Y"),
            )
        cm = out["current_metrics"]
        assert cm["spot_rate_pct"] == pytest.approx(4.0, abs=0.01)
        assert cm["financing_rate_pct"] == pytest.approx(3.0, abs=0.01)
        assert cm["horizon"] == "3M"  # YAML default applied

    def test_composable_total_series_bps(self):
        with patch(_FETCH, return_value=_curve([3.0, 3.3, 3.7, 4.0, 4.2])):
            out = calculate_swap_carry_and_roll(
                engine=None,
                params=SwapCarryAndRollInput(curve_family="X", tenor="10Y"),
            )
        ts = out["time_series_total_carry_roll"]
        assert ts["units"] == "bps"
        assert ts["series_name"] == "X_10Y_3M_carry_roll"
        assert len(ts["rows"]) == 15

    def test_deterministic(self):
        c = _curve([3.0, 3.3, 3.7, 4.0, 4.2])
        with patch(_FETCH, return_value=c):
            a = calculate_swap_carry_and_roll(
                engine=None, params=SwapCarryAndRollInput(curve_family="X", tenor="10Y"))
            b = calculate_swap_carry_and_roll(
                engine=None, params=SwapCarryAndRollInput(curve_family="X", tenor="10Y"))
        assert a["current_metrics"] == b["current_metrics"]


class TestConfig:
    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_swap_carry_and_roll_tool"
        assert cfg.tool.domain == "ois"
        assert cfg.convention_value("default_swap_rate_field") == "PX_LAST"
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("default_horizon") == "3M"


class TestRefusals:
    def test_empty_fetch_error(self):
        with patch(_FETCH, return_value=pd.DataFrame()):
            out = calculate_swap_carry_and_roll(
                engine=None, params=SwapCarryAndRollInput(curve_family="ZZZ"))
        assert "error" in out and "No OIS data" in out["error"]

    def test_horizon_exceeds_tenor_error(self):
        with patch(_FETCH, return_value=_curve([3.0, 3.3, 3.7, 4.0, 4.2])):
            out = calculate_swap_carry_and_roll(
                engine=None,
                params=SwapCarryAndRollInput(
                    curve_family="X", tenor="1Y", horizon="2Y",
                ),
            )
        assert "error" in out and "must exceed the horizon" in out["error"]

    def test_tenor_past_grid_skips_all(self):
        # tenor 40Y is past the 30Y grid max → every day skipped → error.
        with patch(_FETCH, return_value=_curve([3.0, 3.3, 3.7, 4.0, 4.2])):
            out = calculate_swap_carry_and_roll(
                engine=None,
                params=SwapCarryAndRollInput(curve_family="X", tenor="40Y"),
            )
        assert "error" in out and "could not decompose" in out["error"]
