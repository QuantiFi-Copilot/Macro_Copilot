"""Offline compute tests for sovereign_curve_regime (the first Bucket-2
model-state primitive).

The HMM fit itself has no SQL/closed-form oracle (EM + Viterbi), so
correctness here is pinned by construction: a synthetic curve with a
KNOWN calm→stressed realized-vol shift must be recovered as two regimes,
NAMED low/high volatility, with the fitted model state surfaced.  The DB
is mocked — these are pure unit tests.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.sovereign_curve_regime import (
    CONFIG_PATH,
    SovereignCurveRegimeInput,
    calculate_sovereign_curve_regime,
)
from shared.config import clear_tool_config_cache, load_tool_config

_FETCH = (
    "rates_agent.sovereign_bonds.tools.sovereign_curve_regime.compute."
    "fetch_instrument_panel"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _curve(*, n=800, regime_split=400, calm_vol=0.02, stressed_vol=0.12,
           curve="UST", seed=0):
    """A 3-tenor sovereign curve whose 10Y realized-vol STEPS UP at
    ``regime_split`` (a known calm→stressed regime shift)."""
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    vol = np.where(np.arange(n) < regime_split, calm_vol, stressed_vol)
    # Mean-reverting 10Y around 3.5% so the LEVEL stays in a realistic
    # yield range while the realized-VOL regime shift is the signal the
    # HMM separates on.
    y10 = np.empty(n)
    y10[0] = 3.5
    for t in range(1, n):
        y10[t] = y10[t - 1] + 0.02 * (3.5 - y10[t - 1]) + rng.randn() * vol[t]
    y2 = y10 - 0.5 + rng.randn(n) * 0.01      # ~50bp 2s10s
    y5 = (y2 + y10) / 2 + 0.05 + rng.randn(n) * 0.01
    return pd.DataFrame(
        {f"{curve}_2Y": y2, f"{curve}_5Y": y5, f"{curve}_10Y": y10},
        index=idx,
    )


class TestHappyPath:
    def test_recovers_two_vol_regimes_and_names_them(self):
        wide = _curve()
        with patch(_FETCH, return_value=wide):
            out = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST", n_states=2),
            )
        assert "error" not in out, out
        cm = out["current_metrics"]
        # The fitted model state is surfaced (Bucket-2 "the fit").
        assert cm["fit_scope"] == "full_sample"
        assert cm["converged"] is True
        assert cm["n_states"] == 2
        assert isinstance(cm["loglik"], float)
        assert 0.0 <= cm["regime_persistence"] <= 1.0
        assert cm["feature_columns"] == [
            "curvature", "level", "realized_vol", "slope",
        ]
        # Naming: the two regimes are low/high volatility, and the
        # low-vol regime really has a lower realized-vol mean.
        by_name = {r["regime_name"]: r for r in cm["per_regime"]}
        assert set(by_name) == {"low_volatility", "high_volatility"}
        assert (
            by_name["low_volatility"]["realized_vol_pct"]
            < by_name["high_volatility"]["realized_vol_pct"]
        )
        # The data ends in the stressed half ⇒ current regime is high-vol.
        assert cm["current_regime_name"] == "high_volatility"

    def test_per_regime_means_are_natural_units_not_zscored(self):
        # If the primitive (wrongly) pre-standardized, the per-regime
        # LEVEL means would sit near 0 (z-scores).  Real yields sit in
        # the 2-6% range — a direct guard against double-standardization.
        wide = _curve()
        with patch(_FETCH, return_value=wide):
            out = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST", n_states=2),
            )
        for r in out["current_metrics"]["per_regime"]:
            assert 1.0 < r["level_pct"] < 8.0  # plausible yield %, not a z

    def test_passes_raw_features_to_operator(self):
        # Capture the Panel handed to fit_regime_hmm and assert its
        # ``level`` column equals the RAW 10Y yields (NOT standardized).
        wide = _curve()
        captured = {}

        real_panel = {}
        from shared.operators import fit_regime_hmm as _hmm_pkg

        def _spy(features, *, params, config):
            captured["level"] = features.payload["level"].copy()
            return _hmm_pkg.fit_regime_hmm(
                features, params=params, config=config,
            )

        with patch(_FETCH, return_value=wide), patch(
            "rates_agent.sovereign_bonds.tools.sovereign_curve_regime."
            "compute.fit_regime_hmm",
            side_effect=_spy,
        ):
            out = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST", n_states=2),
            )
        assert "error" not in out
        # The level feature must be the raw 10Y yield, untouched.
        np.testing.assert_allclose(
            captured["level"].dropna().to_numpy(),
            wide["UST_10Y"].reindex(captured["level"].index).dropna().to_numpy(),
        )

    def test_regime_time_series_is_factor_level(self):
        wide = _curve()
        with patch(_FETCH, return_value=wide):
            out = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST", n_states=2),
            )
        ts = out["time_series_regime"]
        assert ts["units"] == "factor_level"
        assert ts["series_name"] == "UST_curve_regime_k2"
        decoded = [r["value"] for r in ts["rows"] if r["value"] is not None]
        assert set(decoded) <= {0.0, 1.0}
        assert len(decoded) > 0

    def test_deterministic_rerun(self):
        wide = _curve()
        with patch(_FETCH, return_value=wide):
            a = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST", n_states=2),
            )
            b = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST", n_states=2),
            )
        assert a["current_metrics"]["loglik"] == b["current_metrics"]["loglik"]
        assert (
            a["current_metrics"]["current_regime_index"]
            == b["current_metrics"]["current_regime_index"]
        )


class TestConfigAndConventions:
    def test_config_loads_cleanly(self):
        config = load_tool_config(CONFIG_PATH)
        assert config.tool.name == "calculate_sovereign_curve_regime_tool"
        assert config.tool.category == "quant_standard_analytic"
        assert config.convention_value("default_field_name") == "YLD_YTM_MID"
        assert config.convention_value("ffill_limit_days") == 5

    def test_vol_window_convention_changes_the_fit(self):
        # A different realized-vol window ⇒ a different feature ⇒ a
        # different fit (loglik moves).  Proves the convention is wired,
        # not hardcoded.
        wide = _curve()
        base = load_tool_config(CONFIG_PATH)
        bumped = base.model_copy(deep=True)
        # Replace the dict VALUE (allowed — the dict is mutable) with a
        # model_copy-updated Convention (the model itself is frozen).
        bumped.conventions["realized_vol_window"] = (
            base.conventions["realized_vol_window"].model_copy(
                update={"value": 63},
            )
        )
        with patch(_FETCH, return_value=wide):
            out_a = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST"),
                config=base,
            )
            out_b = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST"),
                config=bumped,
            )
        assert (
            out_a["current_metrics"]["loglik"]
            != out_b["current_metrics"]["loglik"]
        )


class TestRefusals:
    def test_empty_fetch_returns_error_envelope(self):
        with patch(_FETCH, return_value=pd.DataFrame()):
            out = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="ZZZ"),
            )
        assert "error" in out
        assert "No observations" in out["error"]

    def test_missing_leg_returns_error_envelope(self):
        wide = _curve()
        wide["UST_5Y"] = np.nan  # belly leg fully missing
        with patch(_FETCH, return_value=wide):
            out = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST"),
            )
        assert "error" in out
        assert "tenor leg" in out["error"]

    def test_too_little_history_returns_error_envelope(self):
        wide = _curve(n=40)  # below the HMM's max(12, K*10) floor after warmup
        with patch(_FETCH, return_value=wide):
            out = calculate_sovereign_curve_regime(
                engine=None,
                params=SovereignCurveRegimeInput(curve_family="UST", n_states=3),
            )
        assert "error" in out
        assert "regime fit failed" in out["error"]

    def test_identical_tenors_rejected_at_schema(self):
        with pytest.raises(ValueError):
            SovereignCurveRegimeInput(
                curve_family="UST", short_tenor="5Y", belly_tenor="5Y",
                long_tenor="10Y",
            )
