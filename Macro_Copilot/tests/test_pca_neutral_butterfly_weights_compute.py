"""Offline compute tests for pca_neutral_butterfly_weights (§7-C).

The load-bearing property: the solved fly's net loading on the neutralized
PCs is ≈0 (and it retains PC3/curvature exposure).  DB mocked.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.pca_neutral_butterfly_weights import (
    CONFIG_PATH,
    PcaNeutralButterflyWeightsInput,
    calculate_pca_neutral_butterfly_weights,
)
from shared.config import clear_tool_config_cache, load_tool_config

_FETCH = (
    "rates_agent.sovereign_bonds.tools.pca_neutral_butterfly_weights."
    "compute.fetch_instrument_panel"
)
_TENORS = ["2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]
_LOAD = {
    "2Y": (1, -1.0, 0.4), "3Y": (1, -0.6, 0.1), "5Y": (1, -0.1, -0.3),
    "7Y": (1, 0.3, -0.1), "10Y": (1, 0.7, 0.3), "30Y": (1, 1.1, 0.5),
}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _curve(*, n=800, curve="UST", seed=0):
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    f1, f2, f3 = rng.randn(n), rng.randn(n), rng.randn(n)
    data = {}
    for t in _TENORS:
        a = _LOAD[t]
        dy = a[0] * f1 * 0.04 + a[1] * f2 * 0.02 + a[2] * f3 * 0.01
        data[f"{curve}_{t}"] = 3.5 + np.cumsum(dy)
    return pd.DataFrame(data, index=idx)


class TestNeutralization:
    def test_neutralized_pcs_have_zero_fly_loading(self):
        with patch(_FETCH, return_value=_curve()):
            out = calculate_pca_neutral_butterfly_weights(
                engine=None,
                params=PcaNeutralButterflyWeightsInput(
                    curve_family="UST", short_tenor="2Y", belly_tenor="5Y",
                    long_tenor="10Y", n_pcs_to_neutralize=2,
                ),
            )
        assert "error" not in out, out
        cm = out["current_metrics"]
        assert cm["belly_weight"] == 2.0
        # PC1 + PC2 fly loadings ≈ 0 (neutralized); PC3 retained (non-zero).
        by_pc = {r["pc"]: r for r in cm["residual_pc_exposures"]}
        assert by_pc[1]["neutralized"] and abs(by_pc[1]["fly_loading"]) < 1e-4
        assert by_pc[2]["neutralized"] and abs(by_pc[2]["fly_loading"]) < 1e-4
        assert not by_pc[3]["neutralized"]
        assert abs(by_pc[3]["fly_loading"]) > 0.1  # retains curvature

    def test_n_pcs_1_neutralizes_only_pc1(self):
        with patch(_FETCH, return_value=_curve()):
            out = calculate_pca_neutral_butterfly_weights(
                engine=None,
                params=PcaNeutralButterflyWeightsInput(
                    curve_family="UST", n_pcs_to_neutralize=1,
                ),
            )
        cm = out["current_metrics"]
        by_pc = {r["pc"]: r for r in cm["residual_pc_exposures"]}
        assert abs(by_pc[1]["fly_loading"]) < 1e-4  # PC1 neutral
        # cash-neutral normalization: short + belly + long weights sum to 0.
        assert cm["short_weight"] + cm["belly_weight"] + cm["long_weight"] == \
            pytest.approx(0.0, abs=1e-3)

    def test_weights_and_fly_series_bps(self):
        with patch(_FETCH, return_value=_curve()):
            out = calculate_pca_neutral_butterfly_weights(
                engine=None, params=PcaNeutralButterflyWeightsInput(curve_family="UST"),
            )
        cm = out["current_metrics"]
        # Wings should be shorted (negative) for a standard fly.
        assert cm["short_weight"] < 0 and cm["long_weight"] < 0
        assert cm["fit_tenors"] == _TENORS
        ts = out["time_series_neutral_fly"]
        assert ts["units"] == "bps"
        assert ts["series_name"] == "UST_2Y_5Y_10Y_pca_neutral_fly"

    def test_deterministic(self):
        c = _curve()
        with patch(_FETCH, return_value=c):
            a = calculate_pca_neutral_butterfly_weights(
                engine=None, params=PcaNeutralButterflyWeightsInput(curve_family="UST"))
            b = calculate_pca_neutral_butterfly_weights(
                engine=None, params=PcaNeutralButterflyWeightsInput(curve_family="UST"))
        assert a["current_metrics"] == b["current_metrics"]


class TestConfig:
    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_pca_neutral_butterfly_weights_tool"
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("belly_weight") == 2.0


class TestRefusals:
    def test_empty_fetch_error(self):
        with patch(_FETCH, return_value=pd.DataFrame()):
            out = calculate_pca_neutral_butterfly_weights(
                engine=None, params=PcaNeutralButterflyWeightsInput(curve_family="ZZZ"))
        assert "error" in out and "No observations" in out["error"]

    def test_too_little_history_error(self):
        with patch(_FETCH, return_value=_curve(n=8)):
            out = calculate_pca_neutral_butterfly_weights(
                engine=None, params=PcaNeutralButterflyWeightsInput(curve_family="UST"))
        assert "error" in out and "too few" in out["error"]

    def test_identical_fly_tenors_rejected_at_schema(self):
        with pytest.raises(ValueError):
            PcaNeutralButterflyWeightsInput(
                curve_family="UST", short_tenor="5Y", belly_tenor="5Y",
                long_tenor="10Y")
