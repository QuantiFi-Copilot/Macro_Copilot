"""Offline compute tests for ois_policy_path_regime (Bucket-2).

Correctness by construction: a synthetic policy-futures strip whose priced
slope shifts tightening→easing must be recovered as two regimes named
tightening_priced / easing_priced, with the inverse-pricing conversion
(implied = 100 − price) applied and the HMM model state surfaced.  DB
mocked.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.ois.tools.ois_policy_path_regime import (
    CONFIG_PATH,
    OISPolicyPathRegimeInput,
    calculate_ois_policy_path_regime,
)
from shared.config import clear_tool_config_cache, load_tool_config

_FETCH = (
    "rates_agent.ois.tools.ois_policy_path_regime.compute."
    "fetch_policy_futures_strip_panel"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _strip(*, n=900, split=450, cf="SOFR_FUT", inverse=True, seed=0):
    """A SOFR strip whose priced slope shifts tightening(+)→easing(−)."""
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2021-01-04", periods=n)
    front_rate = 3.0 + np.cumsum(rng.randn(n) * 0.01)
    slope = np.where(np.arange(n) < split, 1.0, -0.8) + np.cumsum(rng.randn(n) * 0.002)
    back_rate = front_rate + slope
    belly_rate = (front_rate + back_rate) / 2 + 0.05 + rng.randn(n) * 0.005
    if inverse:
        raw = pd.DataFrame({
            f"{cf}|1": 100 - front_rate, f"{cf}|4": 100 - belly_rate,
            f"{cf}|8": 100 - back_rate,
        }, index=idx)
    else:
        raw = pd.DataFrame({
            f"{cf}|1": front_rate, f"{cf}|4": belly_rate, f"{cf}|8": back_rate,
        }, index=idx)
    umeta = pd.DataFrame({
        "curve_family": [cf] * 3, "strip_position": [1, 4, 8],
        "inverse_pricing": [inverse] * 3,
    })
    return raw, umeta, front_rate


class TestHappyPath:
    def test_recovers_priced_regimes_and_names(self):
        raw, umeta, _ = _strip()
        with patch(_FETCH, return_value=(raw, umeta)):
            out = calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT", n_states=2),
            )
        assert "error" not in out, out
        cm = out["current_metrics"]
        by_name = {r["regime_name"]: r for r in cm["per_regime"]}
        assert set(by_name) == {"easing_priced", "tightening_priced"}
        # tightening regime has the higher mean slope.
        assert by_name["tightening_priced"]["strip_slope_bps"] > \
            by_name["easing_priced"]["strip_slope_bps"]
        # The data ends in the easing-priced half.
        assert cm["current_regime_name"] == "easing_priced"
        assert cm["fit_scope"] == "full_sample"
        assert cm["converged"] is True

    def test_inverse_pricing_applied_raw_features_to_operator(self):
        # The front_level feature must equal 100 − price (the implied rate),
        # and that RAW feature must reach the operator un-standardized.
        raw, umeta, front_rate = _strip()
        captured = {}
        from shared.operators import fit_regime_hmm as _hmm_pkg

        def _spy(features, *, params, config):
            captured["panel"] = features.payload.copy()
            return _hmm_pkg.fit_regime_hmm(features, params=params, config=config)

        with patch(_FETCH, return_value=(raw, umeta)), patch(
            "rates_agent.ois.tools.ois_policy_path_regime.compute."
            "fit_regime_hmm", side_effect=_spy,
        ):
            calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT", n_states=2),
            )
        # implied front rate = 100 − price == the original front_rate.
        np.testing.assert_allclose(
            captured["panel"]["front_level"].to_numpy(), front_rate, atol=1e-9,
        )

    def test_model_state_surfaced(self):
        raw, umeta, _ = _strip()
        with patch(_FETCH, return_value=(raw, umeta)):
            out = calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT", n_states=3),
            )
        cm = out["current_metrics"]
        assert cm["n_states"] == 3
        assert isinstance(cm["loglik"], float)
        assert 0.0 <= cm["regime_persistence"] <= 1.0
        assert cm["feature_columns"] == [
            "front_level", "realized_vol", "strip_curvature", "strip_slope",
        ]
        # K=3 ⇒ the middle regime gets the plain neutral label (no suffix).
        names = {r["regime_name"] for r in cm["per_regime"]}
        assert "neutral_priced" in names

    def test_regime_series_is_factor_level(self):
        raw, umeta, _ = _strip()
        with patch(_FETCH, return_value=(raw, umeta)):
            out = calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT", n_states=2),
            )
        ts = out["time_series_regime"]
        assert ts["units"] == "factor_level"
        assert ts["series_name"] == "SOFR_FUT_policy_path_regime_k2"
        assert len(ts["rows"]) == 900

    def test_deterministic_rerun(self):
        raw, umeta, _ = _strip()
        with patch(_FETCH, return_value=(raw, umeta)):
            a = calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT", n_states=2),
            )
            b = calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT", n_states=2),
            )
        assert a["current_metrics"] == b["current_metrics"]


class TestConfig:
    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_ois_policy_path_regime_tool"
        assert cfg.tool.domain == "ois"
        assert cfg.tool.category == "quant_standard_analytic"
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("strip_price_field") == "PX_LAST"


class TestRefusals:
    def test_empty_fetch_error(self):
        with patch(_FETCH, return_value=(pd.DataFrame(), pd.DataFrame())):
            out = calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT"),
            )
        assert "error" in out and "No policy-futures" in out["error"]

    def test_missing_inverse_flag_error(self):
        raw, umeta, _ = _strip()
        umeta["inverse_pricing"] = None  # metadata gap
        with patch(_FETCH, return_value=(raw, umeta)):
            out = calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT"),
            )
        assert "error" in out and "inverse_pricing" in out["error"]

    def test_mixed_inverse_flag_error(self):
        raw, umeta, _ = _strip()
        umeta.loc[1, "inverse_pricing"] = False  # one cell disagrees
        with patch(_FETCH, return_value=(raw, umeta)):
            out = calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT"),
            )
        assert "error" in out and "disagree" in out["error"]

    def test_too_little_history_error(self):
        raw, umeta, _ = _strip(n=20)
        with patch(_FETCH, return_value=(raw, umeta)):
            out = calculate_ois_policy_path_regime(
                engine=None,
                params=OISPolicyPathRegimeInput(curve_family="SOFR_FUT", n_states=3),
            )
        assert "error" in out and "regime fit failed" in out["error"]

    def test_invalid_curve_family_rejected_at_schema(self):
        with pytest.raises(ValueError):
            OISPolicyPathRegimeInput(curve_family="UST")  # not a strip family
