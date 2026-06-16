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
# Per-tenor change-vol multipliers — a deliberately NON-FLAT term structure
# of vol (short-end ~2.5×, long-end ~0.6×) so the σ-weighting is load-bearing:
# on a flat-vol panel a bare-loading solve happens to be ~neutral too, which
# is why the bug hid on the particular real-UST snapshot.  This makes the
# independent neutrality check able to catch the B8 defect.
_VOL = {"2Y": 2.5, "3Y": 1.8, "5Y": 1.0, "7Y": 0.85, "10Y": 0.7, "30Y": 0.6}


def _independent_pca(changes_df):
    """Fresh correlation-PCA on the change panel — NOT shared.quant.pca.

    Independent numpy reproduction: z-score each column (ddof=0, matching
    np.std default / fit_pca), economy SVD, canonical sign = largest-|loading|
    positive.  Returns (loadings (K,d), factor_scores (n,K), column_stds (d,)).
    """
    X = changes_df.to_numpy(dtype=float)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    Z = (X - mu) / sd
    U, S, Vt = np.linalg.svd(Z, full_matrices=False)
    loadings = Vt.copy()
    scores = U * S  # (n, d)
    for k in range(loadings.shape[0]):
        j = int(np.argmax(np.abs(loadings[k])))
        if loadings[k, j] < 0.0:
            loadings[k] = -loadings[k]
            scores[:, k] = -scores[:, k]
    return loadings, scores, sd


def _hand_solve_sigma(loadings, sd, idx, belly=2.0, n_pcs=2):
    """Solve the 2×2 by hand on σ-weighted loadings (the correct system)."""
    i_s, i_b, i_l = idx
    sl = loadings * sd[np.newaxis, :]
    if n_pcs == 2:
        A = np.array([[sl[0, i_s], sl[0, i_l]], [sl[1, i_s], sl[1, i_l]]])
        b = -belly * np.array([sl[0, i_b], sl[1, i_b]])
    else:
        A = np.array([[sl[0, i_s], sl[0, i_l]], [1.0, 1.0]])
        b = np.array([-belly * sl[0, i_b], -belly])
    return np.linalg.solve(A, b)


def _realized_fly_pc_corr(changes_df, scores, idx, w_short, w_long, belly=2.0):
    """corr(raw_fly @ weights, factor_score_k) — the genuine neutrality test."""
    X = changes_df.to_numpy(dtype=float)
    i_s, i_b, i_l = idx
    fly = w_short * X[:, i_s] + belly * X[:, i_b] + w_long * X[:, i_l]
    return {k + 1: float(np.corrcoef(fly, scores[:, k])[0, 1])
            for k in range(scores.shape[1])}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _curve(*, n=800, curve="UST", seed=0, heterogeneous_vol=True):
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    f1, f2, f3 = rng.randn(n), rng.randn(n), rng.randn(n)
    data = {}
    for t in _TENORS:
        a = _LOAD[t]
        # Apply a NON-FLAT per-tenor change-vol so the σ-weighting is
        # load-bearing (see _VOL).  This is what real curves look like and
        # what makes the B8 defect visible.
        s = _VOL[t] if heterogeneous_vol else 1.0
        dy = (a[0] * f1 * 0.04 + a[1] * f2 * 0.02 + a[2] * f3 * 0.01) * s
        data[f"{curve}_{t}"] = 3.5 + np.cumsum(dy)
    return pd.DataFrame(data, index=idx)


def _idx(tenors=_TENORS):
    return (tenors.index("2Y"), tenors.index("5Y"), tenors.index("10Y"))


class TestNeutralization:
    def test_realized_fly_pc_corr_is_zero_independent(self):
        """GENUINE neutrality test (catches B8): form the raw fly on the
        tool's solved weights and project it onto INDEPENDENTLY-computed
        factor scores.  corr(fly, score_k) must be ~0 for the neutralized
        PCs (PC1/PC2) and clearly non-zero for PC3 (curvature retained).

        NOT a re-assertion of the tool's own residual_pc_exposures — it
        re-fits PCA in fresh numpy and measures the realized correlation.
        This is the check the OLD circular test could not do; if the B8
        σ-weighting is reverted, this assertion FAILS (corr(fly,PC1)≈+0.6
        on this heterogeneous-vol panel).
        """
        levels = _curve()  # heterogeneous per-tenor vol
        with patch(_FETCH, return_value=levels):
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

        # Independent reproduction of the movement basis the tool fit on.
        changes = levels[[f"UST_{t}" for t in _TENORS]].diff().dropna(how="any")
        loadings, scores, sd = _independent_pca(changes)
        idx = _idx()
        corr = _realized_fly_pc_corr(
            changes, scores, idx, cm["short_weight"], cm["long_weight"])
        # The fly REALLY is uncorrelated with the neutralized factor scores.
        assert abs(corr[1]) < 1e-2, f"PC1 corr {corr[1]} (B8 regressed?)"
        assert abs(corr[2]) < 1e-2, f"PC2 corr {corr[2]} (B8 regressed?)"
        assert abs(corr[3]) > 0.1, f"PC3 corr {corr[3]} (lost curvature)"

    def test_tool_weights_match_independent_sigma_solve(self):
        """The tool's weights match a fully independent σ-weighted hand
        solve (fresh numpy PCA + 2×2 by hand) to tolerance — proving the
        SOLVE is the σ-weighted system, not the bare-loading one."""
        levels = _curve()
        with patch(_FETCH, return_value=levels):
            out = calculate_pca_neutral_butterfly_weights(
                engine=None,
                params=PcaNeutralButterflyWeightsInput(
                    curve_family="UST", short_tenor="2Y", belly_tenor="5Y",
                    long_tenor="10Y", n_pcs_to_neutralize=2,
                ),
            )
        cm = out["current_metrics"]
        changes = levels[[f"UST_{t}" for t in _TENORS]].diff().dropna(how="any")
        loadings, scores, sd = _independent_pca(changes)
        w_s, w_l = _hand_solve_sigma(loadings, sd, _idx(), n_pcs=2)
        assert cm["short_weight"] == pytest.approx(w_s, abs=1e-3)
        assert cm["long_weight"] == pytest.approx(w_l, abs=1e-3)

    def test_residual_diagnostic_matches_realized_corr_sign(self):
        """The reported residual_pc_exposures (σ-weighted basis) are ~0 for
        the neutralized PCs — consistent with the realized corr being ~0.
        Cross-checks that the diagnostic measures the TRUE exposure."""
        with patch(_FETCH, return_value=_curve()):
            out = calculate_pca_neutral_butterfly_weights(
                engine=None,
                params=PcaNeutralButterflyWeightsInput(
                    curve_family="UST", short_tenor="2Y", belly_tenor="5Y",
                    long_tenor="10Y", n_pcs_to_neutralize=2,
                ),
            )
        by_pc = {r["pc"]: r for r in out["current_metrics"]["residual_pc_exposures"]}
        assert by_pc[1]["neutralized"] and abs(by_pc[1]["fly_loading"]) < 1e-4
        assert by_pc[2]["neutralized"] and abs(by_pc[2]["fly_loading"]) < 1e-4
        assert not by_pc[3]["neutralized"]
        assert abs(by_pc[3]["fly_loading"]) > 0.01  # retains curvature

    def test_n_pcs_1_neutralizes_only_pc1(self):
        levels = _curve()
        with patch(_FETCH, return_value=levels):
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
        # GENUINE check: realized corr(fly, PC1 score) ~0 (independent PCA);
        # PC2/PC3 retained (n_pcs=1 only strips PC1).
        changes = levels[[f"UST_{t}" for t in _TENORS]].diff().dropna(how="any")
        _, scores, _ = _independent_pca(changes)
        corr = _realized_fly_pc_corr(
            changes, scores, _idx(), cm["short_weight"], cm["long_weight"])
        assert abs(corr[1]) < 1e-2, f"PC1 corr {corr[1]} (B8 regressed?)"
        assert abs(corr[2]) > 0.05, f"PC2 corr {corr[2]} (should be retained)"

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
