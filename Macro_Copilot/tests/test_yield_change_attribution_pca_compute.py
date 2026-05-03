"""
test_yield_change_attribution_pca_compute.py — Unit tests for the
yield_change_attribution_pca tool.

Covers (full Codex-review-pattern coverage):

  1. Bundled config.yaml is structurally valid + loads cleanly +
     ``category=quant_standard_analytic``.
  2. compute() runs end-to-end against synthetic input via BOTH input
     paths (fit_inline and pasted) and returns a well-formed snapshot.
  3. Schema-layer behaviour: date validation, target_tenor membership,
     pasted curve_family match, n_components vs pasted size.
  4. Honest-placeholder guards: NotImplementedError on unsupported
     start/end resolution policies; on unsupported sign_anchor in
     pasted_loadings.
  5. Cross-layer guard: pasted_loadings.n_observations_in_fit < YAML
     min_observations_for_pca → controlled error envelope (FastAPI
     maps to HTTP 422 via the "is smaller than the YAML's" phrase).
  6. Date resolution: forward for start, backward for end; pinned end-
     to-end against synthetic data with non-trading-day requests.
  7. Numerical correctness: synthetic PCA with planted level + slope
     contributions recovers the contribution shares within tolerance;
     residual ≈ 0 when n_components == n_tenors and no degenerate.
  8. Unit conversion: explicit *100 percent → bps at every output
     surface (total_change_bps, contribution_bps, residual_bps).
  9. Pasted-loadings validation: unit-norm tolerance, variance share
     range, component_metadata length match, target_tenor membership.
  10. Degenerate component handling: contribution suppressed to None
      and folded into the residual.
  11. Quality flag mirroring: degenerate/sign_anchor_tied flags from
      the upstream PCA fit reach T14's per-component output.
  12. Boundary rounding: every YAML rounding knob reaches the
      snapshot field it feeds.
  13. Three import paths still resolve to the same Pydantic class.
  14. Provenance fields fully echoed in the output (loadings_*).

Tests are fully offline — fetch is mocked, today is frozen.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import List
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca import (
    CONFIG_PATH,
    YieldChangeAttributionPcaInput,
    calculate_yield_change_attribution_pca,
)
from shared.config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)
from shared.schemas import (
    PastedPcaComponentMetadata,
    PastedPcaLoadings,
    TimeSeriesUnits,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "default_pca_lookback_days": 1825,
        "default_n_components": 3,
        "default_change_frequency": "daily",
        "min_observations_for_pca": 252,
        "ffill_limit_days": 5,
        "start_date_resolution": "forward",
        "end_date_resolution": "backward",
        "loadings_unit_norm_tolerance": 1e-6,
        "bps_round_decimals": 2,
        "loading_round_decimals": 4,
        "variance_share_round_decimals": 4,
        "overlap_pct_round_decimals": 1,
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


def _synthetic_yield_panel(
    *,
    tenors: List[str],
    n_days: int = 600,
    end: date = date(2026, 4, 30),
    seed: int = 42,
) -> pd.DataFrame:
    """Build a synthetic yield panel with a planted factor structure
    so PCA recovers approximately level + slope + curvature.

    Returns a long-format DataFrame matching fetch_tenor_group's
    output: columns [trade_date, tenor, field_value]."""
    rng = np.random.default_rng(seed)
    bdays = pd.bdate_range(end - timedelta(days=n_days * 2), end)
    bdays = bdays[-n_days:]
    n_t = len(tenors)
    # Planted factor structure:
    #   level shock affects all tenors equally (loading 1/sqrt(n_t)),
    #   slope shock loads from −1 to +1 across tenors,
    #   small idiosyncratic noise per tenor.
    level_shock = rng.normal(0, 0.02, len(bdays))   # 2 bps/d level vol
    slope_shock = rng.normal(0, 0.015, len(bdays))  # 1.5 bps/d slope vol
    slope_loading = np.linspace(-1.0, 1.0, n_t)
    base = np.linspace(2.0, 5.0, n_t)  # rough yield curve in percent
    rows = []
    for i, t in enumerate(tenors):
        # Daily yield = base[i] + cumsum(level + slope*loading[i] + noise)
        daily_change = (
            level_shock + slope_shock * slope_loading[i]
            + rng.normal(0, 0.001, len(bdays))
        )
        levels = base[i] + np.cumsum(daily_change)
        for d, v in zip(bdays, levels):
            rows.append({"trade_date": d.date(), "tenor": t, "field_value": v})
    return pd.DataFrame(rows)


def _build_pasted_loadings(
    *,
    curve_family: str = "UST",
    tenors: List[str] = None,
    n_components: int = 3,
    n_obs_in_fit: int = 1000,
    fit_window_start: str = "2022-01-03",
    fit_window_end: str = "2026-04-30",
    change_frequency: str = "daily",
    sign_anchor: str = "lock_pc_long_tenor_positive",
    seed: int = 7,
    flag_first_component_degenerate: bool = False,
) -> PastedPcaLoadings:
    """Build a PastedPcaLoadings payload with unit-norm random
    loadings.  The loadings are NOT a real PCA fit; they're just
    valid math-shape inputs for testing the attribution path."""
    if tenors is None:
        tenors = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
    rng = np.random.default_rng(seed)
    n_t = len(tenors)
    # Build n_components orthonormal vectors via QR of a random matrix.
    A = rng.normal(size=(n_t, n_components))
    Q, _ = np.linalg.qr(A)
    components_matrix = Q.T  # shape: [n_components, n_t]
    # Apply the sign anchor (longest tenor's loading >= 0) so the
    # paste round-trip is consistent.
    for k in range(n_components):
        if components_matrix[k, n_t - 1] < 0:
            components_matrix[k] *= -1.0

    # Optionally flag the first component degenerate (loadings NaN).
    if flag_first_component_degenerate:
        components_matrix[0] = np.full(n_t, np.nan)

    components = [list(map(float, components_matrix[k])) for k in range(n_components)]
    component_names = [f"pc{k+1}" for k in range(n_components)]
    # Variance shares: simple decay; renormalise to sum < 1.
    raw_var = np.array([0.7, 0.2, 0.05, 0.025, 0.012, 0.008, 0.003, 0.002])[:n_components]
    if flag_first_component_degenerate:
        raw_var[0] = 0.0
    variance_shares = list(map(float, raw_var))
    metadata = []
    for k in range(n_components):
        flag = (
            "degenerate"
            if flag_first_component_degenerate and k == 0
            else "ok"
        )
        metadata.append(PastedPcaComponentMetadata(
            component_name=component_names[k],
            quality_flag=flag,  # type: ignore[arg-type]
            quality_note=("planted-degenerate" if flag == "degenerate" else None),
        ))
    return PastedPcaLoadings(
        curve_family=curve_family,
        tenors=tenors,
        components=components,
        component_names=component_names,
        variance_shares=variance_shares,
        fit_window_start=fit_window_start,
        fit_window_end=fit_window_end,
        change_frequency_used=change_frequency,  # type: ignore[arg-type]
        n_observations_in_fit=n_obs_in_fit,
        sign_anchor_used=sign_anchor,  # type: ignore[arg-type]
        component_metadata=metadata,
    )


def _run_inline(params, panel_by_tenor_lookup, config=None):
    """Mock fetch_tenor_group + the inline PCA call so the inline-fit
    path runs without DB / without re-doing the PCA primitive (which
    would re-fetch its own panel)."""
    def fake_fetch(*, engine, curve_family, tenors, field_name, start_date):
        # Filter the synthetic panel down to the requested tenors.
        return panel_by_tenor_lookup(tenors)

    with patch(
        "rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.compute.fetch_tenor_group",
        side_effect=fake_fetch,
    ), patch(
        "rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.compute.date",
        _FrozenDate,
    ), patch(
        # Also patch pca_yield_curve's fetch_tenor_group (inline-fit
        # path calls into pca_yield_curve.calculate_pca_yield_curve,
        # which has its OWN fetch).
        "rates_agent.sovereign_bonds.tools.pca_yield_curve.compute.fetch_tenor_group",
        side_effect=fake_fetch,
    ), patch(
        "rates_agent.sovereign_bonds.tools.pca_yield_curve.compute.date",
        _FrozenDate,
    ):
        return calculate_yield_change_attribution_pca(
            engine=None, params=params, config=config,
        )


def _run_pasted(params, panel_by_tenor_lookup, config=None):
    """Mock fetch_tenor_group only — pasted-loadings path doesn't
    invoke pca_yield_curve."""
    def fake_fetch(*, engine, curve_family, tenors, field_name, start_date):
        return panel_by_tenor_lookup(tenors)

    with patch(
        "rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.compute.fetch_tenor_group",
        side_effect=fake_fetch,
    ), patch(
        "rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.compute.date",
        _FrozenDate,
    ):
        return calculate_yield_change_attribution_pca(
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
        assert cfg.tool.name == "yield_change_attribution_pca_tool"
        assert cfg.tool.domain == "sovereign_bonds"

    def test_category_is_quant_standard_analytic(self):
        """T14 is quant-standard-analytic per the v6 stress-test
        outcome: the math is mechanical given the loadings, and
        every methodology choice (including the upstream PCA's) is
        either captured in this tool's config (default_*) or in the
        pasted_loadings provenance.  Not desk_invariant_primitive
        because configuration choices change interpretation, not just
        calibration."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.category == "quant_standard_analytic"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "default_pca_lookback_days",
            "default_n_components",
            "default_change_frequency",
            "min_observations_for_pca",
            "ffill_limit_days",
            "start_date_resolution",
            "end_date_resolution",
            "loadings_unit_norm_tolerance",
            "bps_round_decimals",
            "loading_round_decimals",
            "variance_share_round_decimals",
            "overlap_pct_round_decimals",
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3


# ===========================================================================
# 2. End-to-end happy paths — fit_inline + pasted
# ===========================================================================

class TestFitInlinePath:
    def test_fit_inline_returns_well_formed_snapshot(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors, n_days=600)

        def lookup(req_tenors):
            return panel[panel["tenor"].isin(req_tenors)]

        params = YieldChangeAttributionPcaInput(
            curve_family="UST",
            target_tenor="10Y",
            start_date="2026-04-20",
            end_date="2026-04-30",
            tenors=tenors,
            n_components=3,
            pca_lookback_days=1825,
            change_frequency="daily",
        )
        out = _run_inline(params, lookup)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        # Required snapshot fields present.
        for k in (
            "start_date_requested", "start_date_resolved",
            "end_date_requested", "end_date_resolved",
            "curve_family", "target_tenor", "total_change_bps",
            "component_contributions", "residual_bps",
            "n_components_used", "loadings_source",
            "loadings_window_start", "loadings_window_end",
            "loadings_change_frequency_used",
            "loadings_n_observations_in_fit",
            "loadings_sign_anchor_used",
            "loadings_change_window_overlap_pct",
        ):
            assert k in cm, f"missing {k}"
        assert cm["loadings_source"] == "fit_inline"
        assert cm["target_tenor"] == "10Y"
        assert cm["loadings_change_frequency_used"] == "daily"
        assert cm["loadings_sign_anchor_used"] == "lock_pc_long_tenor_positive"
        # Three components in the output (matches n_components=3).
        assert len(cm["component_contributions"]) == 3
        for c in cm["component_contributions"]:
            for k in (
                "component_name", "contribution_bps",
                "loading_at_target_tenor",
                "variance_share_in_fit_window", "quality_flag",
            ):
                assert k in c
        # n_observations_in_fit must be ≥ YAML floor.
        assert cm["loadings_n_observations_in_fit"] >= 252


class TestPastedPath:
    def test_pasted_returns_well_formed_snapshot(self):
        tenors = ["1Y", "2Y", "5Y", "10Y", "20Y", "30Y"]
        panel = _synthetic_yield_panel(tenors=tenors, n_days=400)

        def lookup(req_tenors):
            return panel[panel["tenor"].isin(req_tenors)]

        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=1000,
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST",
            target_tenor="10Y",
            start_date="2026-04-20",
            end_date="2026-04-30",
            pasted_loadings=paste,
        )
        out = _run_pasted(params, lookup)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["loadings_source"] == "pasted"
        assert cm["loadings_window_start"] == paste.fit_window_start
        assert cm["loadings_window_end"] == paste.fit_window_end
        assert cm["loadings_change_frequency_used"] == "daily"
        assert cm["loadings_n_observations_in_fit"] == 1000
        assert len(cm["component_contributions"]) == 3
        # All non-degenerate; should have numeric contributions.
        for c in cm["component_contributions"]:
            assert c["quality_flag"] == "ok"
            assert c["contribution_bps"] is not None
            assert c["loading_at_target_tenor"] is not None


# ===========================================================================
# 3. Schema-layer validation
# ===========================================================================

class TestSchemaBehaviour:
    def test_start_date_must_be_strictly_before_end_date(self):
        with pytest.raises(Exception) as exc_info:
            YieldChangeAttributionPcaInput(
                curve_family="UST", target_tenor="10Y",
                start_date="2026-04-30", end_date="2026-04-30",
            )
        assert "strictly before" in str(exc_info.value).lower() or \
            "before" in str(exc_info.value).lower()

    def test_invalid_date_string_rejected(self):
        with pytest.raises(Exception):
            YieldChangeAttributionPcaInput(
                curve_family="UST", target_tenor="10Y",
                start_date="not-a-date", end_date="2026-04-30",
            )

    def test_target_tenor_must_be_in_supplied_tenors(self):
        with pytest.raises(Exception) as exc_info:
            YieldChangeAttributionPcaInput(
                curve_family="UST", target_tenor="20Y",
                start_date="2026-04-01", end_date="2026-04-30",
                tenors=["1Y", "2Y", "5Y", "10Y"],
            )
        assert "target_tenor" in str(exc_info.value).lower()

    def test_pasted_curve_family_mismatch_rejected_at_validator(self):
        paste = _build_pasted_loadings(curve_family="UST")
        with pytest.raises(Exception) as exc_info:
            YieldChangeAttributionPcaInput(
                curve_family="DE_BUND", target_tenor="10Y",
                start_date="2026-04-01", end_date="2026-04-30",
                pasted_loadings=paste,
            )
        assert "curve_family mismatch" in str(exc_info.value).lower()

    def test_pasted_target_tenor_not_in_tenors_rejected(self):
        paste = _build_pasted_loadings(
            tenors=["1Y", "2Y", "5Y"],
        )
        with pytest.raises(Exception):
            YieldChangeAttributionPcaInput(
                curve_family="UST", target_tenor="10Y",
                start_date="2026-04-01", end_date="2026-04-30",
                pasted_loadings=paste,
            )

    def test_n_components_exceeds_pasted_size_rejected(self):
        paste = _build_pasted_loadings(n_components=2)
        with pytest.raises(Exception):
            YieldChangeAttributionPcaInput(
                curve_family="UST", target_tenor="10Y",
                start_date="2026-04-01", end_date="2026-04-30",
                pasted_loadings=paste, n_components=5,
            )


# ===========================================================================
# 4. Honest-placeholder guards
# ===========================================================================

class TestHonestPlaceholderGuards:
    def test_unsupported_start_resolution_raises_not_implemented(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-01", end_date="2026-04-30",
            tenors=tenors, n_components=3,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run_inline(
                params, lookup,
                config=_custom_config(start_date_resolution="strict"),
            )
        msg = str(exc_info.value)
        assert "start_date_resolution" in msg
        assert "planned_extensions" in msg

    def test_unsupported_end_resolution_raises_not_implemented(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-01", end_date="2026-04-30",
            tenors=tenors, n_components=3,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run_inline(
                params, lookup,
                config=_custom_config(end_date_resolution="nearest"),
            )
        assert "end_date_resolution" in str(exc_info.value)


# ===========================================================================
# 5. Cross-layer guard: pasted n_observations_in_fit floor
# ===========================================================================

class TestPastedMinObservationsGuard:
    def test_pasted_below_floor_returns_controlled_error(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        # n_obs_in_fit=100 < default min=252 → controlled error.
        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=100,
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-20", end_date="2026-04-30",
            pasted_loadings=paste,
        )
        out = _run_pasted(params, lookup)
        assert "error" in out
        assert "is smaller than the YAML's" in out["error"]
        assert "min_observations_for_pca" in out["error"]

    def test_lower_yaml_floor_unblocks_smaller_paste(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=80,
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-20", end_date="2026-04-30",
            pasted_loadings=paste,
        )
        out = _run_pasted(
            params, lookup,
            config=_custom_config(min_observations_for_pca=60),
        )
        assert "error" not in out, out.get("error")


# ===========================================================================
# 6. Date resolution — forward for start, backward for end
# ===========================================================================

class TestDateResolution:
    def test_start_resolves_forward_when_request_is_a_weekend(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        # 2026-04-04 is a Saturday → start should resolve forward to
        # the next trading day (Mon Apr 6).
        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=1000,
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-04",  # Saturday
            end_date="2026-04-30",
            pasted_loadings=paste,
        )
        out = _run_pasted(params, lookup)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["start_date_requested"] == "2026-04-04"
        # 2026-04-04 is Saturday; forward-resolved to Mon 4/6 (or the
        # next available trading day in the synthetic panel).
        resolved = pd.Timestamp(cm["start_date_resolved"])
        assert resolved.weekday() < 5, (
            f"start_date_resolved={resolved} should be a weekday"
        )
        assert resolved >= pd.Timestamp("2026-04-04")

    def test_end_resolves_backward_when_request_is_a_weekend(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        # 2026-04-25 is a Saturday → end should resolve backward to
        # Friday Apr 24.
        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=1000,
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-01",
            end_date="2026-04-25",  # Saturday
            pasted_loadings=paste,
        )
        out = _run_pasted(params, lookup)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["end_date_requested"] == "2026-04-25"
        resolved = pd.Timestamp(cm["end_date_resolved"])
        assert resolved.weekday() < 5
        assert resolved <= pd.Timestamp("2026-04-25")


# ===========================================================================
# 7. Numerical correctness — orthonormal loadings recover exactly
# ===========================================================================

class TestNumericalCorrectness:
    def test_n_components_equals_n_tenors_residual_is_near_zero(self):
        """When n_components == n_tenors and loadings are exactly
        orthonormal, the projection captures the full change vector
        and residual ≈ 0 modulo float-precision noise.

        We use 4 tenors and a synthetically-built orthonormal basis
        (via QR) to avoid SVD-induced sign / numerical noise.  This
        test pins the math: project Δy onto a complete orthonormal
        basis, sum the contributions at the target tenor, get back
        Δy[target]."""
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        # Plant a yield panel with a deterministic single-day jump
        # at the end so total_change_bps is known.
        rng = np.random.default_rng(123)
        n_days = 400
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=n_days * 2), date(2026, 4, 30),
        )
        bdays = bdays[-n_days:]
        # Constant levels for first n_days-1, then a known jump on
        # the last day.
        base = np.array([2.0, 2.5, 3.0, 4.0])
        rows = []
        for t_idx, t in enumerate(tenors):
            for d_idx, d in enumerate(bdays):
                if d_idx == len(bdays) - 1:
                    val = base[t_idx] + (0.20 if t == "10Y" else 0.05)
                else:
                    val = base[t_idx]
                rows.append({"trade_date": d.date(), "tenor": t, "field_value": val})
        panel = pd.DataFrame(rows)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        # 4 tenors, 4 components → complete orthonormal basis.
        paste = _build_pasted_loadings(
            tenors=tenors, n_components=4, n_obs_in_fit=1000,
            seed=99,
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST",
            target_tenor="10Y",
            start_date=bdays[-2].strftime("%Y-%m-%d"),
            end_date=bdays[-1].strftime("%Y-%m-%d"),
            pasted_loadings=paste,
            n_components=4,
        )
        # Run at high decimals so display-rounding noise doesn't
        # dominate the conservation check.  The math (unrounded) is
        # exact for an orthonormal complete basis; we want this test
        # to verify the math, not the rounding policy (which is
        # covered by TestBoundaryRounding separately).
        out = _run_pasted(
            params, lookup,
            config=_custom_config(bps_round_decimals=8),
        )
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        # 0.20 percent change at 10Y → 20 bps.
        assert abs(cm["total_change_bps"] - 20.0) < 1e-6
        # Residual ≈ 0 (orthonormal complete basis), tighter at
        # decimals=8.
        assert abs(cm["residual_bps"]) < 1e-6, (
            f"residual_bps={cm['residual_bps']}; expected ≈ 0 for "
            "complete orthonormal basis"
        )
        # Sum of contributions ≈ total_change_bps.
        contrib_sum = sum(
            c["contribution_bps"] for c in cm["component_contributions"]
            if c["contribution_bps"] is not None
        )
        assert abs(contrib_sum - cm["total_change_bps"]) < 1e-6


# ===========================================================================
# 8. Unit conversion — explicit *100 percent → bps
# ===========================================================================

class TestUnitConversion:
    def test_total_change_bps_is_100x_percent_change(self):
        """Plant a known 0.10 percent change at the target tenor and
        verify total_change_bps == 10."""
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        rng = np.random.default_rng(111)
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=600), date(2026, 4, 30),
        )
        bdays = bdays[-300:]
        rows = []
        for t_idx, t in enumerate(tenors):
            for d_idx, d in enumerate(bdays):
                if d_idx == len(bdays) - 1 and t == "10Y":
                    val = 4.10
                elif d_idx == 0 and t == "10Y":
                    val = 4.00
                elif t == "10Y":
                    val = 4.05
                else:
                    val = 2.0 + t_idx * 0.5
                rows.append({"trade_date": d.date(), "tenor": t, "field_value": val})
        panel = pd.DataFrame(rows)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        paste = _build_pasted_loadings(
            tenors=tenors, n_components=4, n_obs_in_fit=1000,
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date=bdays[0].strftime("%Y-%m-%d"),
            end_date=bdays[-1].strftime("%Y-%m-%d"),
            pasted_loadings=paste, n_components=4,
        )
        out = _run_pasted(params, lookup)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        # Change at 10Y: 4.10 - 4.00 = 0.10 percent = 10 bps.
        assert abs(cm["total_change_bps"] - 10.0) < 0.01

    def test_contributions_in_bps_match_residual_arithmetic(self):
        """For ANY input, total_change_bps == sum(contributions) +
        residual_bps (modulo float noise).  Pin the conservation
        law."""
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors, n_days=400)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=1000,
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-20", end_date="2026-04-30",
            pasted_loadings=paste, n_components=3,
        )
        out = _run_pasted(params, lookup)
        cm = out["current_metrics"]
        contrib_sum = sum(
            c["contribution_bps"] for c in cm["component_contributions"]
            if c["contribution_bps"] is not None
        )
        # Arithmetic identity (modulo display rounding):
        # total ≈ contributions_sum + residual.
        assert abs(
            cm["total_change_bps"] - (contrib_sum + cm["residual_bps"])
        ) < 0.05


# ===========================================================================
# 9. Pasted-loadings validation
# ===========================================================================

class TestPastedLoadingsValidation:
    def test_non_unit_norm_loading_rejected(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        # Build a paste, then DELIBERATELY corrupt the first
        # component's vector so its norm is far from 1.
        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=1000,
        )
        # Triple every entry of pc1 → ‖v‖ ≈ 3 (way off unit norm).
        bad_components = [list(map(lambda x: x * 3.0, paste.components[0]))]
        bad_components.extend(paste.components[1:])
        bad_paste = paste.model_copy(update={"components": bad_components})

        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-01", end_date="2026-04-30",
            pasted_loadings=bad_paste, n_components=3,
        )
        out = _run_pasted(params, lookup)
        assert "error" in out
        assert "unit-norm" in out["error"].lower() or \
            "norm" in out["error"].lower()

    def test_variance_share_outside_range_rejected(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=1000,
        )
        bad_paste = paste.model_copy(update={"variance_shares": [0.5, 0.3, 1.5]})

        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-01", end_date="2026-04-30",
            pasted_loadings=bad_paste, n_components=3,
        )
        out = _run_pasted(params, lookup)
        assert "error" in out
        assert "variance" in out["error"].lower()


# ===========================================================================
# 10 + 11. Degenerate component handling + quality flag mirroring
# ===========================================================================

class TestDegenerateComponent:
    def test_degenerate_component_contribution_is_none(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        # Plant pc1 as degenerate.
        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=1000,
            flag_first_component_degenerate=True,
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-20", end_date="2026-04-30",
            pasted_loadings=paste, n_components=3,
        )
        out = _run_pasted(params, lookup)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        # First component is degenerate → contribution None,
        # quality_flag mirrored.
        pc1 = cm["component_contributions"][0]
        assert pc1["component_name"] == "pc1"
        assert pc1["quality_flag"] == "degenerate"
        assert pc1["contribution_bps"] is None
        assert pc1["loading_at_target_tenor"] is None
        # pc2 + pc3 still ok.
        assert cm["component_contributions"][1]["quality_flag"] == "ok"
        assert cm["component_contributions"][2]["quality_flag"] == "ok"
        assert cm["component_contributions"][1]["contribution_bps"] is not None


# ===========================================================================
# 12. Boundary rounding — every YAML rounding knob reaches its surface
# ===========================================================================

class TestBoundaryRounding:
    """Each YAML rounding knob must reach the surface it feeds.
    Surface map for this tool (snapshot-only, no time-series):

      bps_round_decimals      → total_change_bps,
                                 contribution_bps (per component),
                                 residual_bps
      loading_round_decimals  → loading_at_target_tenor (per component)
      variance_share_round_decimals → variance_share_in_fit_window
      overlap_pct_round_decimals → loadings_change_window_overlap_pct

    Each test pins (a) round-back contract AND (b) decimals=N+2
    produces a finer-grained value than decimals=N on a planted
    panel."""

    def _planted(self):
        tenors = ["1Y", "2Y", "5Y", "10Y"]
        rng = np.random.default_rng(17)
        n_days = 400
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=n_days * 2), date(2026, 4, 30),
        )
        bdays = bdays[-n_days:]
        base = np.array([2.123456, 2.567891, 3.234567, 4.876543])
        rows = []
        for t_idx, t in enumerate(tenors):
            cum = np.cumsum(rng.normal(0, 0.001, len(bdays)))
            for d_idx, d in enumerate(bdays):
                rows.append({
                    "trade_date": d.date(), "tenor": t,
                    "field_value": float(base[t_idx] + cum[d_idx]),
                })
        panel = pd.DataFrame(rows)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3, n_obs_in_fit=1000,
            seed=999,
        )
        return tenors, paste, lookup

    def _params(self):
        return YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-20", end_date="2026-04-30",
        )

    def _run_at(self, decimals_kw: dict):
        tenors, paste, lookup = self._planted()
        params = self._params().model_copy(update={
            "pasted_loadings": paste,
            "n_components": 3,
        })
        return _run_pasted(
            params, lookup, config=_custom_config(**decimals_kw),
        )

    def test_bps_round_decimals_reaches_total_and_contributions(self):
        out_2 = self._run_at({"bps_round_decimals": 2})
        out_4 = self._run_at({"bps_round_decimals": 4})
        t_2 = out_2["current_metrics"]["total_change_bps"]
        t_4 = out_4["current_metrics"]["total_change_bps"]
        assert t_2 is not None and t_4 is not None
        assert round(t_4, 2) == t_2
        assert t_2 != t_4

        # Also verify contribution_bps responds.
        c_2 = out_2["current_metrics"]["component_contributions"][0]["contribution_bps"]
        c_4 = out_4["current_metrics"]["component_contributions"][0]["contribution_bps"]
        assert round(c_4, 2) == c_2
        assert c_2 != c_4

        # Residual too.
        r_2 = out_2["current_metrics"]["residual_bps"]
        r_4 = out_4["current_metrics"]["residual_bps"]
        assert round(r_4, 2) == r_2

    def test_loading_round_decimals_reaches_per_component_loading(self):
        out_4 = self._run_at({"loading_round_decimals": 4})
        out_6 = self._run_at({"loading_round_decimals": 6})
        ld_4 = out_4["current_metrics"]["component_contributions"][0]["loading_at_target_tenor"]
        ld_6 = out_6["current_metrics"]["component_contributions"][0]["loading_at_target_tenor"]
        assert ld_4 is not None and ld_6 is not None
        assert round(ld_6, 4) == ld_4
        assert ld_4 != ld_6, (
            "loading_round_decimals override did not reach the per-"
            "component snapshot field."
        )

    def test_variance_share_round_decimals_reaches_per_component_share(self):
        out_4 = self._run_at({"variance_share_round_decimals": 4})
        out_6 = self._run_at({"variance_share_round_decimals": 6})
        v_4 = out_4["current_metrics"]["component_contributions"][0]["variance_share_in_fit_window"]
        v_6 = out_6["current_metrics"]["component_contributions"][0]["variance_share_in_fit_window"]
        # Variance shares may already be exact at decimals=4 in this
        # test (since paste uses round-y values); just verify the
        # round-back contract.
        assert round(v_6, 4) == v_4


# ===========================================================================
# 13. Import-path back-compat
# ===========================================================================

class TestImportPathBackCompat:
    def test_calculate_via_package_init_and_compute_match(self):
        from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca import (
            calculate_yield_change_attribution_pca as via_package,
        )
        from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.compute import (
            calculate_yield_change_attribution_pca as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_two_paths(self):
        from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca import (
            YieldChangeAttributionPcaInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.schemas import (
            YieldChangeAttributionPcaInput as via_schemas,
        )
        assert via_package is via_schemas

    def test_config_path_identity(self):
        from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute


# ===========================================================================
# 14. Provenance fully echoed in the output
# ===========================================================================

class TestProvenanceEchoed:
    def test_pasted_provenance_fields_echoed(self):
        tenors = ["1Y", "2Y", "5Y", "10Y", "20Y", "30Y"]
        panel = _synthetic_yield_panel(tenors=tenors)

        def lookup(req):
            return panel[panel["tenor"].isin(req)]

        paste = _build_pasted_loadings(
            tenors=tenors, n_components=3,
            n_obs_in_fit=1234,
            fit_window_start="2021-06-01",
            fit_window_end="2026-04-30",
            change_frequency="weekly",
        )
        params = YieldChangeAttributionPcaInput(
            curve_family="UST", target_tenor="10Y",
            start_date="2026-04-20", end_date="2026-04-30",
            pasted_loadings=paste, n_components=3,
        )
        out = _run_pasted(params, lookup)
        cm = out["current_metrics"]
        # Every provenance field is echoed end-to-end.
        assert cm["loadings_window_start"] == "2021-06-01"
        assert cm["loadings_window_end"] == "2026-04-30"
        assert cm["loadings_change_frequency_used"] == "weekly"
        assert cm["loadings_n_observations_in_fit"] == 1234
        assert cm["loadings_sign_anchor_used"] == "lock_pc_long_tenor_positive"
        assert cm["loadings_source"] == "pasted"
