"""
test_pca_yield_curve_compute.py — Unit tests for the pca_yield_curve
tool.

Covers (full Codex-review-pattern coverage):
  1. Bundled config.yaml is structurally valid + loads cleanly +
     ``category=quant_standard_analytic``.
  2. compute() runs end-to-end against synthetic input and returns a
     well-formed snapshot (loadings + variance shares + factor
     scores) plus per-component time_series.
  3. Numerical correctness: synthetic level/slope/curvature data
     recovers the planted variance ratios within tolerance.
  4. Sign anchor: each non-degenerate component's loading at the
     longest tenor is non-negative.
  5. Honest-placeholder guard: NotImplementedError on a
     non-default sign_anchor (mirrors butterfly's
     trailing_range_window_days + rolling_regression's add_constant).
  6. Degenerate-component handling: rank-deficient panel produces
     ``component_metadata.quality_flag == "degenerate"`` for the
     affected component, with NaN loadings/scores.
  7. sign_anchor_tied tie-break: an exact-zero longest-tenor loading
     AND exact-zero longest-shortest difference produces
     ``quality_flag == "sign_anchor_tied"``.
  8. Cross-layer guard: change panel below min_observations →
     controlled error envelope (FastAPI maps to HTTP 422).
  9. Schema-layer behaviour: tenor / n_components bounds; field_name
     sentinel; unknown tenor doesn't crash; n_components > available
     tenors → controlled error envelope.
  10. Boundary rounding: every YAML rounding knob (loading,
      variance_share, factor) reaches its respective surface (snapshot
      AND, where applicable, time_series rows).
  11. Tenors are numeric-sorted: '10Y' must follow '2Y', not precede
      it (alphabetic sort would invert).
  12. Three import paths still resolve to the same Pydantic class.

Tests are fully offline — fetch is mocked, today is frozen.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
    CONFIG_PATH,
    PcaYieldCurveInput,
    calculate_pca_yield_curve,
)
from shared.config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)
from shared.schemas import PastedPcaLoadings, TimeSeriesUnits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


class _FrozenDate(date):
    _frozen_value: date = date(2026, 5, 3)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "default_n_components": 3,
        "default_change_frequency": "daily",
        "min_observations_for_pca": 252,
        "sign_anchor": "lock_pc_long_tenor_positive",
        "degenerate_variance_share_threshold": 1e-12,
        "ffill_limit_days": 5,
        "loading_round_decimals": 4,
        "variance_share_round_decimals": 4,
        "factor_round_decimals": 4,
        "default_field_name": "YLD_YTM_MID",
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(
            name="t", domain="d", description="x",
            category="quant_standard_analytic",
        ),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


def _synthetic_curve_df(
    *,
    tenors=("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"),
    n_obs: int = 1500,
    var_ratios=(0.80, 0.15, 0.05),
    noise_scale: float = 0.001,
    frozen_today: date = date(2026, 5, 3),
    seed: int = 42,
) -> pd.DataFrame:
    """Build a long-format yield panel matching fetch_tenor_group's
    return shape, with a planted level / slope / curvature factor
    structure."""
    rng = np.random.default_rng(seed)
    T = np.array([float(t.rstrip("Y")) for t in tenors])

    level = np.ones(len(tenors)) / np.sqrt(len(tenors))
    slope = (T - T.mean()) / np.linalg.norm(T - T.mean())
    curv = (T - T.mean()) ** 2 - ((T - T.mean()) ** 2).mean()
    curv = curv / np.linalg.norm(curv)

    f1 = rng.normal(0, np.sqrt(var_ratios[0]), n_obs)
    f2 = rng.normal(0, np.sqrt(var_ratios[1]), n_obs)
    f3 = rng.normal(0, np.sqrt(var_ratios[2]), n_obs)
    changes = (
        np.outer(f1, level)
        + np.outer(f2, slope)
        + np.outer(f3, curv)
        + rng.normal(0, noise_scale, (n_obs, len(tenors)))
    )
    yields = changes.cumsum(axis=0) + 4.0
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=n_obs * 2), frozen_today,
    )
    bdays = bdays[-n_obs:]

    rows = []
    for j, t in enumerate(tenors):
        for d, v in zip(bdays, yields[:, j]):
            rows.append({
                "trade_date": d.date(),
                "tenor": t,
                "field_value": float(v),
            })
    return pd.DataFrame(rows)


def _run(params, fetched_df, config=None):
    """Patch fetch_tenor_group + date inside the compute module and
    run the tool."""
    def fake_fetch(
        *, engine, curve_family, tenors, field_name, start_date, end_date=None,
    ):
        # fake_fetch: filter rows to the requested tenors and the
        # requested calendar window (start, and end when supplied) so
        # lookback semantics stay honest in unit tests.
        mask = (
            fetched_df["tenor"].isin(tenors)
            & (fetched_df["trade_date"] >= start_date)
        )
        if end_date is not None:
            mask &= fetched_df["trade_date"] <= end_date
        return fetched_df.loc[mask].copy()

    with patch(
        "rates_agent.sovereign_bonds.tools.pca_yield_curve.compute.fetch_tenor_group",
        side_effect=fake_fetch,
    ), patch(
        "rates_agent.sovereign_bonds.tools.pca_yield_curve.compute.date",
        _FrozenDate,
    ):
        return calculate_pca_yield_curve(
            engine=None, params=params, config=config,
        )


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "pca_yield_curve_tool"
        assert cfg.tool.domain == "sovereign_bonds"

    def test_category_is_quant_standard_analytic(self):
        cfg = load_tool_config(CONFIG_PATH)
        # PCA is a textbook quant primitive — universal mathematics
        # but configuration must be specified before use.
        assert cfg.tool.category == "quant_standard_analytic"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "default_n_components",
            "default_change_frequency",
            "min_observations_for_pca",
            "sign_anchor",
            "degenerate_variance_share_threshold",
            "ffill_limit_days",
            "loading_round_decimals",
            "variance_share_round_decimals",
            "factor_round_decimals",
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("default_n_components") == 3
        assert cfg.convention_value("default_change_frequency") == "daily"
        assert cfg.convention_value("min_observations_for_pca") == 252
        assert cfg.convention_value("sign_anchor") == "lock_pc_long_tenor_positive"
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 4
        joined = " | ".join(cfg.methodology.planned_extensions).lower()
        # Documented sibling-tool hooks: robust PCA, sparse PCA,
        # correlation PCA, dense-bond panel, alternative anchors.
        assert "robust" in joined or "sparse" in joined
        assert "correlation" in joined or "anchor" in joined


# ===========================================================================
# 2. Happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_returns_well_formed_output(self):
        df = _synthetic_curve_df()
        params = PcaYieldCurveInput(
            curve_family="UST",
            n_components=3,
            lookback_days=1825,
            change_frequency="daily",
        )
        out = _run(params, df)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        # Snapshot fields present
        for k in (
            "as_of_date", "fit_window_start", "fit_window_end",
            "curve_family", "tenors_used",
            "lookback_days_used", "n_components_returned",
            "change_frequency_used", "sign_anchor_used",
            "loadings", "variance_explained",
            "total_variance_explained", "current_factor_levels",
            "component_metadata", "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # fit_window_end is just an alias for as_of_date.
        assert cm["fit_window_end"] == cm["as_of_date"]
        # fit_window_start must be strictly before fit_window_end
        # (the centered-change panel has at least one diff step).
        assert cm["fit_window_start"] < cm["fit_window_end"]

        # Tenors numeric-ascending
        assert cm["tenors_used"] == [
            "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y",
        ]
        assert cm["n_components_returned"] == 3
        assert cm["change_frequency_used"] == "daily"
        assert cm["sign_anchor_used"] == "lock_pc_long_tenor_positive"

        # 8 loading rows × pc1+pc2+pc3 keys per row
        assert len(cm["loadings"]) == 8
        for row in cm["loadings"]:
            assert "tenor" in row
            for comp in ("pc1", "pc2", "pc3"):
                assert comp in row

        # 3 variance-share rows
        assert len(cm["variance_explained"]) == 3
        for row in cm["variance_explained"]:
            assert "component_name" in row
            assert "variance_share" in row
            assert "cumulative_share" in row

        # All component_metadata 'ok' on clean synthetic data
        flags = {m["quality_flag"] for m in cm["component_metadata"]}
        assert flags == {"ok"}

        # 3 TimeSeries factors — units=FACTOR_LEVEL
        assert len(out["time_series_factors"]) == 3
        for ts in out["time_series_factors"]:
            assert ts["units"] == TimeSeriesUnits.FACTOR_LEVEL.value
            assert len(ts["rows"]) > 0

    def test_explicit_config_matches_auto_loaded(self):
        df = _synthetic_curve_df()
        params = PcaYieldCurveInput(
            curve_family="UST", n_components=3, lookback_days=1825,
        )
        out_auto = _run(params, df, config=None)
        out_explicit = _run(params, df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Numerical correctness
# ===========================================================================

class TestNumericalCorrectness:
    def test_recovers_planted_variance_ratios(self):
        """Plant 80% level + 15% slope + 5% curvature.  PCA should
        recover ratios in the same order with PC1 dominant."""
        df = _synthetic_curve_df(var_ratios=(0.80, 0.15, 0.05))
        params = PcaYieldCurveInput(
            curve_family="UST", n_components=3, lookback_days=1825,
        )
        out = _run(params, df)
        var_rows = out["current_metrics"]["variance_explained"]
        pc1_share = var_rows[0]["variance_share"]
        pc2_share = var_rows[1]["variance_share"]
        pc3_share = var_rows[2]["variance_share"]
        # Top component dominant on planted level structure.
        assert pc1_share > 0.5
        assert pc1_share > pc2_share > pc3_share
        # Cumulative share at PC3 close to 1 (the noise we added is
        # tiny so PC1+PC2+PC3 ≈ all variance).
        assert var_rows[-1]["cumulative_share"] > 0.95

    def test_pc1_is_level_like_on_normal_data(self):
        """For normal sovereign panels, PC1 loadings should be all
        same-sign (the 'level' factor).  Doesn't enforce equal —
        just same sign across all tenors."""
        df = _synthetic_curve_df()
        params = PcaYieldCurveInput(
            curve_family="UST", n_components=3, lookback_days=1825,
        )
        out = _run(params, df)
        pc1_loadings = [row["pc1"] for row in out["current_metrics"]["loadings"]]
        signs = {1 if v > 0 else (-1 if v < 0 else 0) for v in pc1_loadings}
        # Sign anchor guarantees non-negative at longest tenor; on
        # level-dominant data ALL tenors should be positive.
        assert signs == {1}, f"PC1 loadings have mixed signs: {pc1_loadings}"


# ===========================================================================
# 4. Sign anchor
# ===========================================================================

class TestSignAnchor:
    def test_longest_tenor_loading_is_nonnegative_for_each_pc(self):
        df = _synthetic_curve_df()
        params = PcaYieldCurveInput(
            curve_family="UST", n_components=3, lookback_days=1825,
        )
        out = _run(params, df)
        loadings = out["current_metrics"]["loadings"]
        # Find the longest-tenor row (last in numeric order = "30Y")
        longest = next(r for r in loadings if r["tenor"] == "30Y")
        for comp in ("pc1", "pc2", "pc3"):
            v = longest[comp]
            assert v is None or v >= 0, (
                f"sign-anchor violated: {comp} loading at 30Y = {v}"
            )


# ===========================================================================
# 5. Honest-placeholder guard on sign_anchor
# ===========================================================================

class TestHonestPlaceholderGuard:
    def test_unsupported_sign_anchor_raises_not_implemented(self):
        df = _synthetic_curve_df()
        params = PcaYieldCurveInput(
            curve_family="UST", n_components=3, lookback_days=1825,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params, df,
                config=_custom_config(sign_anchor="max_abs_loading_positive"),
            )
        msg = str(exc_info.value)
        assert "sign_anchor" in msg
        assert "max_abs_loading_positive" in msg
        assert "planned_extensions" in msg


# ===========================================================================
# 6. Degenerate-component handling
# ===========================================================================

class TestDegenerateComponent:
    def test_rank_deficient_panel_flags_degenerate_component(self):
        """Build a panel where two tenors are EXACTLY identical (so
        they're a perfect linear combination).  PCA on 3 unique
        directions → rank ≤ 7 < 8 → at least one component should
        be degenerate (variance share ≈ 0)."""
        rng = np.random.default_rng(11)
        n_obs = 1000
        tenors = ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y")
        # Generate 7 independent factor changes; tenor "30Y" copies "20Y"
        # exactly so the panel is rank-7.
        changes = rng.normal(0, 0.01, (n_obs, 8))
        changes[:, 7] = changes[:, 6]  # 30Y = 20Y exactly

        bdays = pd.bdate_range(
            date(2026, 5, 3) - timedelta(days=n_obs * 2), date(2026, 5, 3),
        )
        bdays = bdays[-n_obs:]
        yields = changes.cumsum(axis=0) + 4.0
        rows = []
        for j, t in enumerate(tenors):
            for d, v in zip(bdays, yields[:, j]):
                rows.append({
                    "trade_date": d.date(), "tenor": t,
                    "field_value": float(v),
                })
        df = pd.DataFrame(rows)

        params = PcaYieldCurveInput(
            curve_family="UST", n_components=8, lookback_days=1825,
        )
        out = _run(params, df)
        flags = [m["quality_flag"] for m in out["current_metrics"]["component_metadata"]]
        # At least the bottom component (PC8) is degenerate.
        assert "degenerate" in flags

        # Find the degenerate component(s) and verify their loadings
        # are NaN (None in the wire JSON).
        degenerate_names = [
            m["component_name"] for m in out["current_metrics"]["component_metadata"]
            if m["quality_flag"] == "degenerate"
        ]
        assert len(degenerate_names) >= 1
        for row in out["current_metrics"]["loadings"]:
            for name in degenerate_names:
                assert row[name] is None, (
                    f"degenerate component {name} should have None "
                    f"loading at tenor {row['tenor']}, got {row[name]}"
                )


# ===========================================================================
# 7. sign_anchor_tied tie-break
# ===========================================================================

class TestSignAnchorTiedTieBreak:
    """If a non-degenerate component's loading at the longest tenor
    is exactly zero AND the longest-shortest difference is exactly
    zero, the sign anchor can't pick a side — flag
    sign_anchor_tied + apply no flip.

    We construct this by directly running pca_yield_changes on a
    pre-built panel with a known eigenvector that's anti-symmetric
    around the middle tenor; the SVD gives a loading whose entries
    sum to zero AND whose first/last entries are equal (so longest
    minus shortest = 0).
    """

    def test_tied_anchor_flags_quality(self):
        from shared.analytics.stats import pca_yield_changes

        # Three tenors; build a pre-centered panel whose ONLY non-
        # degenerate eigenvector is [-a, 0, a] (symmetric around the
        # middle).  Then loading[longest] = a, loading[shortest] = -a,
        # so the longest-tenor loading is non-zero and the sign-anchor
        # picks +a — not the tied case.  To force tied: use [a, 0, -a]
        # → after sign anchor, [-a, 0, a] (longest = +a > 0); also
        # not tied.  The tied case requires loading[longest]=0 AND
        # loading[shortest]=0.  Construct that directly.
        n_obs = 500
        rng = np.random.default_rng(17)
        # 3 tenors; only the middle column has variance.  Other
        # columns are exactly zero (zero-variance), so the SVD's
        # principal eigenvector is the canonical e_2 = [0, 1, 0],
        # whose longest-tenor loading is exactly 0 AND shortest-
        # tenor loading is also 0 → tied.
        x = np.zeros((n_obs, 3))
        x[:, 1] = rng.normal(0, 1.0, n_obs)
        idx = pd.bdate_range("2024-01-01", periods=n_obs)
        panel = pd.DataFrame(x.cumsum(axis=0), index=idx,
                             columns=["1Y", "10Y", "30Y"])
        res = pca_yield_changes(
            panel,
            tenors_ordered=["1Y", "10Y", "30Y"],
            n_components=1,
            change_frequency="daily",
            sign_anchor="lock_pc_long_tenor_positive",
            min_observations=252,
        )
        flag = res.component_metadata[0].quality_flag
        assert flag == "sign_anchor_tied", (
            f"expected sign_anchor_tied, got {flag!r}; "
            f"loadings={res.loadings.values.tolist()}"
        )


# ===========================================================================
# 8. Cross-layer min_observations guard
# ===========================================================================

class TestSmallWindowGuard:
    def test_below_min_obs_returns_controlled_error(self):
        # 100 observations is far below default min_observations=252.
        df = _synthetic_curve_df(n_obs=100)
        params = PcaYieldCurveInput(
            curve_family="UST", n_components=3, lookback_days=1825,
        )
        out = _run(params, df)
        assert "error" in out
        assert "is smaller than the YAML's" in out["error"]
        assert "min_observations_for_pca" in out["error"]


# ===========================================================================
# 9. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = PcaYieldCurveInput(curve_family="UST")
        assert params.field_name is None

    def test_default_n_components(self):
        params = PcaYieldCurveInput(curve_family="UST")
        assert params.n_components == 3

    def test_default_change_frequency(self):
        params = PcaYieldCurveInput(curve_family="UST")
        assert params.change_frequency == "daily"

    def test_change_frequency_literal_enforced(self):
        with pytest.raises(Exception):
            PcaYieldCurveInput(
                curve_family="UST", change_frequency="monthly",
            )

    def test_n_components_bounds(self):
        with pytest.raises(Exception):
            PcaYieldCurveInput(curve_family="UST", n_components=0)
        with pytest.raises(Exception):
            PcaYieldCurveInput(curve_family="UST", n_components=20)

    def test_lookback_days_lower_bound(self):
        with pytest.raises(Exception):
            PcaYieldCurveInput(curve_family="UST", lookback_days=399)

    def test_n_components_exceeds_available_tenors_returns_error(self):
        """Caller asks for 5 components but the panel only has 3
        tenors — controlled error envelope, not a crash."""
        df = _synthetic_curve_df(
            tenors=("2Y", "10Y", "30Y"), n_obs=1000,
        )
        params = PcaYieldCurveInput(
            curve_family="UST", tenors=["2Y", "10Y", "30Y"],
            n_components=5, lookback_days=1825,
        )
        out = _run(params, df)
        assert "error" in out
        assert "n_components" in out["error"]

    def test_explicit_missing_tenor_returns_error_not_silent_shrink(self):
        df = _synthetic_curve_df(tenors=("2Y", "5Y", "10Y"), n_obs=1000)
        params = PcaYieldCurveInput(
            curve_family="UST",
            tenors=["2Y", "5Y", "10Y", "30Y"],
            n_components=3,
            lookback_days=1825,
        )
        out = _run(params, df)
        assert "error" in out
        assert "Missing tenor" in out["error"]
        assert "30Y" in out["error"]

    def test_duplicate_explicit_tenors_return_error(self):
        df = _synthetic_curve_df(tenors=("2Y", "5Y", "10Y", "30Y"), n_obs=1000)
        params = PcaYieldCurveInput(
            curve_family="UST",
            tenors=["2Y", "5Y", "5Y", "10Y"],
            n_components=3,
            lookback_days=1825,
        )
        out = _run(params, df)
        assert "error" in out
        assert "Duplicate tenor" in out["error"]

    def test_default_playbook_universe_missing_tenor_returns_error(self):
        df = _synthetic_curve_df(
            tenors=("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y"),
            n_obs=1000,
        )
        params = PcaYieldCurveInput(
            curve_family="UST",
            n_components=3,
            lookback_days=1825,
        )
        out = _run(params, df)
        assert "error" in out
        assert "Missing tenor" in out["error"]
        assert "30Y" in out["error"]


# ===========================================================================
# 10. Boundary rounding
# ===========================================================================

class TestBoundaryRounding:
    """Each YAML rounding knob must reach its specified surface.
    Surface map for this tool:

        loading_round_decimals        → loadings[*].pc1, pc2, pc3, ...
        variance_share_round_decimals → variance_explained[*].variance_share +
                                         cumulative_share + total_variance_explained
        factor_round_decimals         → current_factor_levels[*] AND
                                         time_series_factors[*].rows[*].value

    Each test pins (a) snapshot at decimals=N differs from
    decimals=N+2 and (b) round-back contract.
    """

    def _params(self):
        return PcaYieldCurveInput(
            curve_family="UST", n_components=3, lookback_days=1825,
        )

    def _run_at(self, decimals_kw: dict):
        df = _synthetic_curve_df(noise_scale=0.0017)
        return _run(self._params(), df, config=_custom_config(**decimals_kw))

    def test_loading_round_decimals_reaches_loadings(self):
        out_4 = self._run_at({"loading_round_decimals": 4})
        out_6 = self._run_at({"loading_round_decimals": 6})
        # Pick PC1 at the longest tenor; both must be non-None, non-zero.
        l_4 = next(r for r in out_4["current_metrics"]["loadings"]
                   if r["tenor"] == "30Y")["pc1"]
        l_6 = next(r for r in out_6["current_metrics"]["loadings"]
                   if r["tenor"] == "30Y")["pc1"]
        assert l_4 is not None and l_6 is not None
        assert round(l_6, 4) == l_4
        assert l_4 != l_6, (
            f"loading_round_decimals=6 produced same value as =4 ({l_4})"
        )

    def test_variance_share_round_decimals_reaches_variance_explained(self):
        out_4 = self._run_at({"variance_share_round_decimals": 4})
        out_6 = self._run_at({"variance_share_round_decimals": 6})
        v_4 = out_4["current_metrics"]["variance_explained"][0]["variance_share"]
        v_6 = out_6["current_metrics"]["variance_explained"][0]["variance_share"]
        assert round(v_6, 4) == v_4
        assert v_4 != v_6

    def test_factor_round_decimals_reaches_both_surfaces(self):
        out_4 = self._run_at({"factor_round_decimals": 4})
        out_6 = self._run_at({"factor_round_decimals": 6})
        # Snapshot side: current_factor_levels for PC1
        f_snap_4 = out_4["current_metrics"]["current_factor_levels"]["pc1"]
        f_snap_6 = out_6["current_metrics"]["current_factor_levels"]["pc1"]
        assert round(f_snap_6, 4) == f_snap_4
        assert f_snap_4 != f_snap_6
        # And the snapshot's PC1 latest value must match the LAST row
        # of time_series_factors[pc1] at the same decimals.
        pc1_ts_6 = next(
            ts for ts in out_6["time_series_factors"]
            if "pc1" in ts["series_name"]
        )
        assert pc1_ts_6["rows"][-1]["value"] == f_snap_6


# ===========================================================================
# 11. Tenors are numeric-sorted
# ===========================================================================

class TestTenorNumericSort:
    def test_tenors_used_is_numeric_ascending_not_alphabetic(self):
        df = _synthetic_curve_df()
        params = PcaYieldCurveInput(
            # Pass tenors out of order on purpose
            curve_family="UST",
            tenors=["10Y", "1Y", "20Y", "2Y", "30Y", "3Y", "5Y", "7Y"],
            n_components=3, lookback_days=1825,
        )
        out = _run(params, df)
        assert out["current_metrics"]["tenors_used"] == [
            "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y",
        ], (
            "tenors_used should be numeric-ascending — alphabetic sort "
            "would put '10Y' before '1Y' (but we sort by years)."
        )


# ===========================================================================
# 12. Import-path back-compat
# ===========================================================================

class TestImportPathBackCompat:
    def test_calculate_via_package_init_and_compute_match(self):
        from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
            calculate_pca_yield_curve as via_package,
        )
        from rates_agent.sovereign_bonds.tools.pca_yield_curve.compute import (
            calculate_pca_yield_curve as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_two_paths(self):
        from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
            PcaYieldCurveInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.pca_yield_curve.schemas import (
            PcaYieldCurveInput as via_schemas,
        )
        assert via_package is via_schemas

    def test_config_path_identity(self):
        from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.pca_yield_curve.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute


# ===========================================================================
# 13. Shared pasted-PCA contract
# ===========================================================================

class TestPastedPcaLoadingsContract:
    def test_accepts_component_metadata_and_suppressed_loadings(self):
        pasted = PastedPcaLoadings(
            curve_family="UST",
            tenors=["1Y", "10Y", "30Y"],
            components=[
                [0.4, 0.5, 0.6],
                [None, None, None],
            ],
            component_names=["pc1", "pc2"],
            variance_shares=[0.95, 0.0],
            component_metadata=[
                {
                    "component_name": "pc1",
                    "quality_flag": "ok",
                    "quality_note": None,
                },
                {
                    "component_name": "pc2",
                    "quality_flag": "degenerate",
                    "quality_note": "suppressed",
                },
            ],
            fit_window_start="2021-01-01",
            fit_window_end="2026-01-01",
            change_frequency_used="daily",
            n_observations_in_fit=900,
            sign_anchor_used="lock_pc_long_tenor_positive",
        )
        assert pasted.components[1] == [None, None, None]
        assert pasted.component_metadata[1].quality_flag == "degenerate"


# ===========================================================================
# 13. Curve-family-agnostic scope (Round 3 Stage 2, work item A3 — PR5
#     coverage extension)
# ===========================================================================
#
# The pre-A3 implementation hardcoded a single PLAYBOOK_PATH to
# sovereign_bonds.yml so any non-sovereign curve_family raised
# "Missing curve ... in the sovereign playbook".  After A3, the
# discovery layer scans every tenor-keyed playbook under
# rates_agent/playbooks/ — sovereign + OIS + ZCIS + sovereign-linker
# curves are all PCA-fittable through the same code path.  Per-
# playbook field-name auto-discovery picks the right Bloomberg
# primary metric per curve_family without caller intervention.
#
# Tests below pin (a) end-to-end runs on >=3 non-sovereign curve_
# families, (b) the per-playbook field-name auto-discovery, (c) the
# unknown-curve_family controlled-error envelope, and (d) the
# sovereign-callers-unchanged invariant (backward compat).


class TestCurveFamilyAgnosticScope:
    """Round 3 A3: pca_yield_curve accepts any tenor-keyed rates
    curve_family declared in any playbook under rates_agent/playbooks/.

    The fetcher (shared.analytics.rates_fetch.fetch_tenor_group) is
    already instrument-type agnostic; the change here is in
    compute()'s playbook lookup + field-name resolution."""

    @staticmethod
    def _run_capture(params, fetched_df, captured: dict):
        """Variant of _run() that also captures the resolved
        field_name passed to fetch_tenor_group, so tests can assert
        per-playbook auto-discovery happened correctly."""
        def fake_fetch(
            *, engine, curve_family, tenors, field_name, start_date,
            end_date=None,
        ):
            captured["field_name"] = field_name
            captured["curve_family"] = curve_family
            captured["tenors"] = list(tenors)
            mask = (
                fetched_df["tenor"].isin(tenors)
                & (fetched_df["trade_date"] >= start_date)
            )
            if end_date is not None:
                mask &= fetched_df["trade_date"] <= end_date
            return fetched_df.loc[mask].copy()

        with patch(
            "rates_agent.sovereign_bonds.tools.pca_yield_curve.compute.fetch_tenor_group",
            side_effect=fake_fetch,
        ), patch(
            "rates_agent.sovereign_bonds.tools.pca_yield_curve.compute.date",
            _FrozenDate,
        ):
            return calculate_pca_yield_curve(
                engine=None, params=params, config=None,
            )

    # -----------------------------------------------------------------
    # End-to-end runs on >=3 non-sovereign curve_families
    # -----------------------------------------------------------------

    def test_runs_on_usd_sofr_ois(self):
        """OIS curve_family — discovered from ois.yml, default field
        PX_LAST."""
        # USD_SOFR_OIS has 13 tenors in the playbook (1W..30Y).  Use
        # a 5-tenor subset to keep the synthetic panel small + ensure
        # n_components <= n_tenors.
        tenors = ("1Y", "2Y", "5Y", "10Y", "30Y")
        df = _synthetic_curve_df(tenors=tenors, n_obs=1000)
        params = PcaYieldCurveInput(
            curve_family="USD_SOFR_OIS",
            tenors=list(tenors),
            n_components=3,
            lookback_days=1825,
        )
        captured: dict = {}
        out = self._run_capture(params, df, captured)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        # Curve_family echoed honestly.
        assert cm["curve_family"] == "USD_SOFR_OIS"
        # n_components honoured.
        assert cm["n_components_returned"] == 3
        # All loadings rows present + per-component metadata.
        assert len(cm["loadings"]) == 5
        assert len(cm["variance_explained"]) == 3
        # Per-playbook field auto-discovery: ois.yml's target_metrics[0]
        # is PX_LAST, so that should be what was passed to fetch_tenor_group.
        assert captured["field_name"] == "PX_LAST", (
            f"USD_SOFR_OIS should auto-discover PX_LAST from ois.yml; "
            f"got {captured['field_name']!r}"
        )
        # 3 factor TimeSeries with curve_family-aware naming.
        assert len(out["time_series_factors"]) == 3
        for ts in out["time_series_factors"]:
            assert ts["series_name"].startswith("usd_sofr_ois_pc")
            assert ts["units"] == TimeSeriesUnits.FACTOR_LEVEL.value

    def test_runs_on_usd_zcis(self):
        """Inflation-swap curve_family — discovered from
        inflation_swaps.yml, default field PX_MID."""
        # USD_ZCIS has 7 tenors (1Y..30Y); use 5.
        tenors = ("1Y", "2Y", "5Y", "10Y", "30Y")
        df = _synthetic_curve_df(tenors=tenors, n_obs=1000)
        params = PcaYieldCurveInput(
            curve_family="USD_ZCIS",
            tenors=list(tenors),
            n_components=3,
            lookback_days=1825,
        )
        captured: dict = {}
        out = self._run_capture(params, df, captured)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["curve_family"] == "USD_ZCIS"
        # Per-playbook field auto-discovery: inflation_swaps.yml's
        # target_metrics[0] is PX_MID.
        assert captured["field_name"] == "PX_MID", (
            f"USD_ZCIS should auto-discover PX_MID from "
            f"inflation_swaps.yml; got {captured['field_name']!r}"
        )

    def test_runs_on_usd_tips(self):
        """Sovereign-linker curve_family — discovered from
        inflation_indexed_bonds.yml, default field YLD_YTM_MID.
        USD_TIPS has only 4 tenors (5Y, 10Y, 20Y, 30Y); n_components=3
        is the maximum that fits."""
        tenors = ("5Y", "10Y", "20Y", "30Y")
        df = _synthetic_curve_df(tenors=tenors, n_obs=1000)
        params = PcaYieldCurveInput(
            curve_family="USD_TIPS",
            tenors=list(tenors),
            n_components=3,
            lookback_days=1825,
        )
        captured: dict = {}
        out = self._run_capture(params, df, captured)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["curve_family"] == "USD_TIPS"
        assert cm["tenors_used"] == ["5Y", "10Y", "20Y", "30Y"]
        # Per-playbook field auto-discovery: inflation_indexed_bonds.yml's
        # target_metrics[0] is YLD_YTM_MID (same as sovereign — both are
        # yield-to-maturity).
        assert captured["field_name"] == "YLD_YTM_MID", (
            f"USD_TIPS should auto-discover YLD_YTM_MID from "
            f"inflation_indexed_bonds.yml; got {captured['field_name']!r}"
        )

    # -----------------------------------------------------------------
    # Per-playbook field auto-discovery + explicit override precedence
    # -----------------------------------------------------------------

    def test_explicit_field_name_wins_over_playbook_default(self):
        """params.field_name takes precedence over the per-playbook
        auto-discovered default — backward compat for callers that
        already pass an explicit field."""
        tenors = ("1Y", "2Y", "5Y", "10Y")
        df = _synthetic_curve_df(tenors=tenors, n_obs=1000)
        params = PcaYieldCurveInput(
            curve_family="USD_SOFR_OIS",
            tenors=list(tenors),
            n_components=3,
            lookback_days=1825,
            field_name="EXPLICIT_OVERRIDE_FIELD",
        )
        captured: dict = {}
        # Note: the fake_fetch returns the synthetic data regardless of
        # field_name (it filters by tenor only), so the explicit override
        # doesn't change the output — but it should appear in the
        # captured field_name passed to fetch_tenor_group.
        _ = self._run_capture(params, df, captured)
        assert captured["field_name"] == "EXPLICIT_OVERRIDE_FIELD"

    def test_sovereign_curve_field_default_unchanged(self):
        """Backward-compat invariant: a UST call with field_name=None
        still resolves to YLD_YTM_MID (the same value as pre-A3 — both
        the YAML default AND the sovereign playbook's target_metrics[0]
        are YLD_YTM_MID; the per-playbook discovery picks the playbook
        value, identical to the legacy YAML fallback)."""
        df = _synthetic_curve_df(
            tenors=("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"),
            n_obs=1000,
        )
        params = PcaYieldCurveInput(
            curve_family="UST",
            n_components=3,
            lookback_days=1825,
        )
        captured: dict = {}
        out = self._run_capture(params, df, captured)
        assert "error" not in out, out.get("error")
        assert captured["field_name"] == "YLD_YTM_MID"

    # -----------------------------------------------------------------
    # Unknown-curve_family controlled error envelope
    # -----------------------------------------------------------------

    def test_unknown_curve_family_returns_controlled_error(self):
        """An unknown curve_family produces a controlled error envelope
        (not a crash) and the message names the rejection rule."""
        df = _synthetic_curve_df()  # data shape irrelevant — discovery fails first
        params = PcaYieldCurveInput(
            curve_family="DOES_NOT_EXIST",
            n_components=3,
            lookback_days=1825,
        )
        out = _run(params, df)
        assert "error" in out
        assert "DOES_NOT_EXIST" in out["error"]
        assert "Unknown curve_family" in out["error"]

    # -----------------------------------------------------------------
    # Discovery boundary — cash-bond playbook excluded
    # -----------------------------------------------------------------

    def test_ust_resolves_to_sovereign_benchmark_playbook(self):
        """UST appears in BOTH sovereign_bonds.yml (benchmark generics)
        AND sovereign_cash_bonds.yml (specific cusips).  The discovery
        rule excludes cash-bond playbooks
        (instrument_type='sovereign_cash_bond'), so UST resolves to
        sovereign_bonds.yml — preserving the pre-A3 lookup behaviour.

        This test pins the discovery boundary: a future expansion that
        accidentally admits cash-bond playbooks would surface as a
        ValueError ("UST is declared in both ... and ...") at discovery
        time, which then surfaces as a controlled error envelope on
        every UST call.  Catching the regression at unit-test time
        prevents that production blast radius.
        """
        from shared.analytics.playbook_discovery import (
            playbook_curve_family_index,
        )
        idx = playbook_curve_family_index()
        ust_entry = idx.get("UST")
        assert ust_entry is not None, (
            "UST must be discoverable for PCA backward compat"
        )
        assert ust_entry["playbook"] == "sovereign_bonds.yml", (
            f"UST must resolve to sovereign_bonds.yml (benchmark "
            f"playbook), got {ust_entry['playbook']!r} — cash-bond "
            f"playbook bleed-through is a regression."
        )

    def test_at_least_three_non_sovereign_curve_families_discoverable(self):
        """The work order's A3 acceptance requires PCA work on >=3
        non-sovereign curve_families.  This test pins discoverability
        of one OIS + one ZCIS + one linker curve_family — three
        independent playbook sources."""
        from shared.analytics.playbook_discovery import (
            playbook_curve_family_index,
        )
        idx = playbook_curve_family_index()
        for cf in ("USD_SOFR_OIS", "USD_ZCIS", "USD_TIPS"):
            assert cf in idx, f"{cf} must be discoverable post-A3"
            entry = idx[cf]
            # Each discovered curve_family carries a non-empty tenor
            # universe + the owning playbook's default field name.
            assert len(entry["tenors"]) >= 1
            assert entry["default_field"]
            assert entry["playbook"].endswith(".yml")
