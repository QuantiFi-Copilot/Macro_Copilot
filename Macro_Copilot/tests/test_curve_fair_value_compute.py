"""Offline compute tests for curve_fair_value (Bucket-2 PCA fair-value).

Correctness is pinned by construction: a synthetic curve driven by 3
latent factors with a KNOWN planted cheapness on one tenor must be
recovered as that tenor being the cheapest, with the fitted PCA model
state surfaced.  The DB is mocked.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.curve_fair_value import (
    CONFIG_PATH,
    CurveFairValueInput,
    calculate_curve_fair_value,
)
from shared.config import clear_tool_config_cache, load_tool_config

_FETCH = (
    "rates_agent.sovereign_bonds.tools.curve_fair_value.compute."
    "fetch_instrument_panel"
)
_TENORS = ["2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]
_BETAS = {
    "2Y": (1, -0.8, 0.3), "3Y": (1, -0.5, 0.1), "5Y": (1, 0.0, -0.2),
    "7Y": (1, 0.3, -0.1), "10Y": (1, 0.6, 0.2), "30Y": (1, 1.0, 0.5),
}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _curve(*, n=700, curve="UST", cheap_tenor="7Y", cheap_bps=0.10, seed=0):
    """A 3-factor sovereign curve with a planted persistent cheapness
    (a constant +cheap_bps yield) on ``cheap_tenor``."""
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    level = 3.0 + np.cumsum(rng.randn(n) * 0.03)
    slope = 0.5 + np.cumsum(rng.randn(n) * 0.01)
    curv = np.cumsum(rng.randn(n) * 0.005)
    data = {}
    for t in _TENORS:
        b = _BETAS[t]
        data[f"{curve}_{t}"] = (
            b[0] * level + b[1] * slope + b[2] * curv + rng.randn(n) * 0.01
        )
    data[f"{curve}_{cheap_tenor}"] = data[f"{curve}_{cheap_tenor}"] + cheap_bps
    return pd.DataFrame(data, index=idx)


class TestHappyPath:
    def test_planted_cheapness_is_ranked_cheapest(self):
        wide = _curve(cheap_tenor="7Y")
        with patch(_FETCH, return_value=wide):
            out = calculate_curve_fair_value(
                engine=None, params=CurveFairValueInput(curve_family="UST"),
            )
        assert "error" not in out, out
        cm = out["current_metrics"]
        assert cm["cheapest_tenor"] == "7Y"
        assert cm["cheapest_residual_bps"] > 0  # cheap = positive residual
        # The planted tenor's verdict should read 'cheap'.
        by_tenor = {r["tenor"]: r for r in cm["per_tenor"]}
        assert by_tenor["7Y"]["verdict"] == "cheap"

    def test_output_contract_and_model_state(self):
        wide = _curve()
        with patch(_FETCH, return_value=wide):
            out = calculate_curve_fair_value(
                engine=None, params=CurveFairValueInput(curve_family="UST"),
            )
        cm = out["current_metrics"]
        assert cm["fit_scope"] == "full_sample"
        assert cm["n_components"] == 3
        assert len(cm["explained_variance_ratio"]) == 3
        assert cm["feature_columns"] == sorted(f"UST_{t}" for t in _TENORS)
        assert cm["tenors_used"] == _TENORS
        assert {r["tenor"] for r in cm["per_tenor"]} == set(_TENORS)
        # richest is the most-negative residual.
        assert cm["richest_residual_bps"] <= cm["cheapest_residual_bps"]

    def test_focus_series_is_bps(self):
        wide = _curve()
        with patch(_FETCH, return_value=wide):
            out = calculate_curve_fair_value(
                engine=None,
                params=CurveFairValueInput(curve_family="UST", focus_tenor="5Y"),
            )
        ts = out["time_series_residual"]
        assert ts["units"] == "bps"
        assert ts["series_name"] == "UST_5Y_pca_residual"
        assert len(ts["rows"]) == 700

    def test_passes_raw_levels_to_operator(self):
        # The operator z-scores internally; the primitive must hand it RAW
        # yields.  Capture the Panel on the first reconstruct call.
        wide = _curve()
        captured = {}
        from shared.operators import reconstruct_from_factors as _rff_pkg

        def _spy(features, *, params, config):
            if "panel" not in captured:
                captured["panel"] = features.payload.copy()
            return _rff_pkg.reconstruct_from_factors(
                features, params=params, config=config,
            )

        with patch(_FETCH, return_value=wide), patch(
            "rates_agent.sovereign_bonds.tools.curve_fair_value.compute."
            "reconstruct_from_factors",
            side_effect=_spy,
        ):
            calculate_curve_fair_value(
                engine=None, params=CurveFairValueInput(curve_family="UST"),
            )
        np.testing.assert_allclose(
            captured["panel"]["UST_10Y"].to_numpy(),
            wide["UST_10Y"].to_numpy(),
        )

    def test_deterministic_rerun(self):
        wide = _curve()
        with patch(_FETCH, return_value=wide):
            a = calculate_curve_fair_value(
                engine=None, params=CurveFairValueInput(curve_family="UST"),
            )
            b = calculate_curve_fair_value(
                engine=None, params=CurveFairValueInput(curve_family="UST"),
            )
        assert a["current_metrics"]["per_tenor"] == b["current_metrics"]["per_tenor"]


class TestConfig:
    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_curve_fair_value_tool"
        assert cfg.tool.category == "quant_standard_analytic"
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("n_components") == 3


class TestRefusals:
    def test_empty_fetch_error(self):
        with patch(_FETCH, return_value=pd.DataFrame()):
            out = calculate_curve_fair_value(
                engine=None, params=CurveFairValueInput(curve_family="ZZZ"),
            )
        assert "error" in out and "No observations" in out["error"]

    def test_missing_leg_error(self):
        wide = _curve()
        wide["UST_30Y"] = np.nan
        with patch(_FETCH, return_value=wide):
            out = calculate_curve_fair_value(
                engine=None, params=CurveFairValueInput(curve_family="UST"),
            )
        assert "error" in out and "tenor leg" in out["error"]

    def test_n_components_over_tenors_error(self):
        # 4 tenors but n_components default is 3 → fine; force the guard
        # by requesting fewer tenors than n_components via a custom config.
        import copy
        wide = _curve()
        base = load_tool_config(CONFIG_PATH)
        bumped = base.model_copy(deep=True)
        bumped.conventions["n_components"] = base.conventions[
            "n_components"
        ].model_copy(update={"value": 5})
        with patch(_FETCH, return_value=wide):
            out = calculate_curve_fair_value(
                engine=None,
                params=CurveFairValueInput(
                    curve_family="UST", tenors=["2Y", "5Y", "10Y", "30Y"],
                    focus_tenor="10Y",
                ),
                config=bumped,
            )
        assert "error" in out and "n_components" in out["error"]

    def test_focus_tenor_not_in_tenors_rejected_at_schema(self):
        with pytest.raises(ValueError):
            CurveFairValueInput(
                curve_family="UST", tenors=["2Y", "5Y", "10Y", "30Y"],
                focus_tenor="7Y",  # not in the list
            )

    def test_too_few_tenors_rejected_at_schema(self):
        with pytest.raises(ValueError):
            CurveFairValueInput(curve_family="UST", tenors=["2Y", "10Y"])
