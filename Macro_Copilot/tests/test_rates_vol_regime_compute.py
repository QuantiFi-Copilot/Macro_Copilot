"""Offline compute tests for rates_vol_regime (Bucket-2 GARCH vol regime).

Correctness by construction: a synthetic yield series with a KNOWN vol
ramp-up in the tail must read as 'elevated', with the GARCH model state
(persistence) surfaced.  DB mocked.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.rates_vol_regime import (
    CONFIG_PATH,
    RatesVolRegimeInput,
    calculate_rates_vol_regime,
)
from shared.config import clear_tool_config_cache, load_tool_config

_FETCH = (
    "rates_agent.sovereign_bonds.tools.rates_vol_regime.compute."
    "fetch_instrument_panel"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _levels(*, n=1200, calm_vol=0.03, hot_vol=0.10, split=800, curve="UST",
            tenor="10Y", seed=0):
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2021-01-04", periods=n)
    vol = np.where(np.arange(n) < split, calm_vol, hot_vol)
    levels = 3.0 + np.cumsum(rng.randn(n) * vol)
    return pd.DataFrame({f"{curve}_{tenor}": levels}, index=idx)


class TestHappyPath:
    def test_tail_vol_ramp_reads_elevated(self):
        with patch(_FETCH, return_value=_levels()):
            out = calculate_rates_vol_regime(
                engine=None, params=RatesVolRegimeInput(curve_family="UST"),
            )
        assert "error" not in out, out
        cm = out["current_metrics"]
        assert cm["regime_label"] == "elevated"
        assert cm["vol_percentile"] > 67.0
        assert cm["current_conditional_vol_daily_bps"] > 0

    def test_garch_model_state_surfaced(self):
        with patch(_FETCH, return_value=_levels()):
            out = calculate_rates_vol_regime(
                engine=None, params=RatesVolRegimeInput(curve_family="UST"),
            )
        cm = out["current_metrics"]
        assert cm["fit_scope"] == "full_sample"
        # GARCH(1,1): persistence = alpha + beta, in (0,1).
        assert cm["persistence"] == pytest.approx(cm["alpha"] + cm["beta"], abs=1e-3)
        assert 0.0 < cm["persistence"] < 1.0
        assert cm["omega"] > 0.0
        assert cm["is_vol_mean_reverting"] is True
        assert isinstance(cm["near_integrated"], bool)

    def test_annualized_is_sqrt252_of_daily(self):
        with patch(_FETCH, return_value=_levels()):
            out = calculate_rates_vol_regime(
                engine=None, params=RatesVolRegimeInput(curve_family="UST"),
            )
        cm = out["current_metrics"]
        assert cm["current_conditional_vol_annualized_bps"] == pytest.approx(
            cm["current_conditional_vol_daily_bps"] * np.sqrt(252), rel=1e-3,
        )

    def test_passes_DIFFED_returns_to_operator(self):
        # The operator runs on the series AS GIVEN (differenced=False); the
        # primitive MUST hand it yield CHANGES, not levels.
        wide = _levels()
        captured = {}
        from shared.operators import fit_garch as _g_pkg

        def _spy(series, *, params, config):
            captured["payload"] = series.payload.copy()
            return _g_pkg.fit_garch(series, params=params, config=config)

        with patch(_FETCH, return_value=wide), patch(
            "rates_agent.sovereign_bonds.tools.rates_vol_regime.compute."
            "fit_garch", side_effect=_spy,
        ):
            calculate_rates_vol_regime(
                engine=None, params=RatesVolRegimeInput(curve_family="UST"),
            )
        # The captured series must be the DIFF of the levels (not the levels).
        expected = wide["UST_10Y"].diff()
        np.testing.assert_allclose(
            captured["payload"].dropna().to_numpy(),
            expected.dropna().to_numpy(),
        )

    def test_conditional_vol_series_is_bps(self):
        with patch(_FETCH, return_value=_levels()):
            out = calculate_rates_vol_regime(
                engine=None, params=RatesVolRegimeInput(curve_family="UST"),
            )
        ts = out["time_series_conditional_vol"]
        assert ts["units"] == "bps"
        assert ts["series_name"] == "UST_10Y_garch_conditional_vol"
        assert len(ts["rows"]) == 1200

    def test_deterministic_rerun(self):
        with patch(_FETCH, return_value=_levels()):
            a = calculate_rates_vol_regime(
                engine=None, params=RatesVolRegimeInput(curve_family="UST"),
            )
            b = calculate_rates_vol_regime(
                engine=None, params=RatesVolRegimeInput(curve_family="UST"),
            )
        assert a["current_metrics"] == b["current_metrics"]


class TestConfig:
    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_rates_vol_regime_tool"
        assert cfg.tool.category == "quant_standard_analytic"
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("return_method") == "diff"


class TestRefusals:
    def test_empty_fetch_error(self):
        with patch(_FETCH, return_value=pd.DataFrame()):
            out = calculate_rates_vol_regime(
                engine=None, params=RatesVolRegimeInput(curve_family="ZZZ"),
            )
        assert "error" in out and "No observations" in out["error"]

    def test_too_little_history_error(self):
        # < _FAMILY_FLOOR (12) finite return rows after differencing.
        with patch(_FETCH, return_value=_levels(n=10)):
            out = calculate_rates_vol_regime(
                engine=None, params=RatesVolRegimeInput(curve_family="UST"),
            )
        assert "error" in out and "GARCH fit failed" in out["error"]
