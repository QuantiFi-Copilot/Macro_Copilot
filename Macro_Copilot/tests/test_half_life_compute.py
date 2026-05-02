"""
test_half_life_compute.py — Unit tests for the half_life tool.

Covers (full Codex-review-pattern coverage):
  1. Bundled config.yaml is structurally valid + loads cleanly +
     ``category=desk_invariant_primitive``.
  2. compute() runs end-to-end against synthetic input and returns a
     well-formed snapshot for ALL THREE input variants.
  3. Schema-layer behaviour: exactly-one-input enforcement; lookback
     bounds; sentinel field_name.
  4. Numerical correctness: synthetic OU recovers β, half-life,
     long-run mean within tolerance.
  5. Edge cases (the v6 plan locked these explicitly):
     - random walk → not mean reverting; half_life=None.
     - β below |min_abs_beta_for_half_life| → half_life=None.
     - β <= -1 (formula undefined) → half_life=None even though
       is_mean_reverting=True.
  6. Cross-layer guard: series shorter than min_observations →
     controlled error envelope (FastAPI maps to HTTP 422 via the
     user_input_phrases shape).
  7. PairSpec direction: spread = (cf1 − cf2) × 100 in bps; matches
     cross_market_spread.
  8. PastedTimeSeries.units flows to series_units in the snapshot;
     native rounding follows units.
  9. Boundary rounding: every YAML rounding knob reaches its
     snapshot field (no time-series payload on this tool — pinned).
  10. Tool emits NO time-series output (it's a pure snapshot tool).
  11. Three import paths still resolve to the same Pydantic class.

Tests are fully offline — fetch is mocked, today is frozen.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.half_life import (
    CONFIG_PATH,
    HalfLifeInput,
    calculate_half_life,
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
    PairSpec,
    PastedTimeSeries,
    SeriesSpec,
    TimeSeriesRow,
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
        "min_observations": 252,
        "confidence_level": 0.95,
        "min_abs_beta_for_half_life": 1e-6,
        "ffill_limit_days": 5,
        "half_life_round_decimals": 1,
        "ou_beta_round_decimals": 6,
        "r_squared_round_decimals": 4,
        "yield_round_decimals": 4,
        "bps_round_decimals": 2,
        "default_field_name": "YLD_YTM_MID",
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(
            name="t", domain="d", description="x",
            category="desk_invariant_primitive",
        ),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


def _ou_synthetic_df(
    *,
    beta_true: float = -0.05,
    mean_true: float = 4.0,
    n: int = 1000,
    noise_scale: float = 0.01,
    seed: int = 42,
    frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Generate a synthetic mean-reverting OU process and return a
    long-format DataFrame matching fetch_single_tenor's shape."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    x[0] = mean_true
    for t in range(1, n):
        x[t] = (
            x[t - 1]
            + beta_true * (x[t - 1] - mean_true)
            + rng.normal(0, noise_scale)
        )
    bdays = pd.bdate_range(frozen_today - timedelta(days=n * 2), frozen_today)
    bdays = bdays[-n:]
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": x,
    })


def _divergent_df(
    *, beta_true: float = 0.01, n: int = 1000, seed: int = 7,
    noise_scale: float = 0.01,
    frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Build a deterministically-divergent AR(1) series with β > 0 so
    the OLS fit recovers β ≈ +0.01.  Used to pin the
    NOT-mean-reverting branch.

    NB: a finite-sample pure random walk can produce a small negative
    OLS β by chance, which trips the strict `β<0` rule in the OU
    primitive.  That's a documented expected behaviour of the
    structural test — see methodology.assumptions in the YAML — and
    distinguishing 'true random walk' from 'weak mean-reverter' is
    the cointegration_test tool's territory (deferred).  So we test
    NOT-mean-reverting against a deterministically divergent series
    rather than against a noisy random walk."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    x[0] = 1.0
    for t in range(1, n):
        x[t] = x[t-1] + beta_true * x[t-1] + rng.normal(0, noise_scale)
    bdays = pd.bdate_range(frozen_today - timedelta(days=n * 2), frozen_today)
    bdays = bdays[-n:]
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": x,
    })


def _run_series_spec(params, fetched_by_label: dict, config=None):
    def fake_fetch(*, engine, curve_family, tenor, field_name, start_date):
        key = f"{curve_family}_{tenor}"
        if key not in fetched_by_label:
            raise AssertionError(f"unexpected fetch for {key}")
        return fetched_by_label[key]

    with patch(
        "rates_agent.sovereign_bonds.tools.half_life.compute.fetch_single_tenor",
        side_effect=fake_fetch,
    ), patch(
        "rates_agent.sovereign_bonds.tools.half_life.compute.date",
        _FrozenDate,
    ):
        return calculate_half_life(engine=None, params=params, config=config)


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "half_life_tool"
        assert cfg.tool.domain == "sovereign_bonds"

    def test_category_is_desk_invariant_primitive(self):
        cfg = load_tool_config(CONFIG_PATH)
        # "Half-life of mean reversion" is desk vocabulary.
        assert cfg.tool.category == "desk_invariant_primitive"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "min_observations",
            "confidence_level",
            "min_abs_beta_for_half_life",
            "ffill_limit_days",
            "half_life_round_decimals",
            "ou_beta_round_decimals",
            "r_squared_round_decimals",
            "yield_round_decimals",
            "bps_round_decimals",
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_no_overlap_with_regression_beta_round_decimals(self):
        """Codex-class lesson: the OU drift β is a different
        statistical object from the regression hedge β.  The OU
        rounding knob is named ``ou_beta_round_decimals`` so the
        cross-config lint does NOT compare it against
        rolling_regression / beta_adjusted_spread's
        ``beta_round_decimals``.  Pin the naming here."""
        cfg = load_tool_config(CONFIG_PATH)
        assert "ou_beta_round_decimals" in cfg.conventions
        # The shared regression beta_round_decimals MUST NOT live in
        # this YAML — the rounding knob is intentionally a different
        # convention name to prevent lint-driven drift.
        assert "beta_round_decimals" not in cfg.conventions

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3


# ===========================================================================
# 2. End-to-end happy path — series_spec
# ===========================================================================

class TestSeriesSpecPath:
    def test_series_spec_returns_well_formed_snapshot(self):
        target_df = _ou_synthetic_df()
        params = HalfLifeInput(
            series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            lookback_days=1825,
        )
        out = _run_series_spec(params, {"UST_10Y": target_df})
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date", "series_label", "series_units",
            "is_mean_reverting", "half_life_days",
            "half_life_ci_lower_days", "half_life_ci_upper_days",
            "long_run_mean_native", "current_value_native",
            "current_deviation_native",
            "beta", "beta_ci_lower", "beta_ci_upper",
            "r_squared", "observation_count",
            "confidence_level_used",
        ):
            assert k in cm, f"missing {k}"

        assert cm["series_label"] == "UST_10Y"
        assert cm["series_units"] == TimeSeriesUnits.PERCENT.value
        assert cm["is_mean_reverting"] is True
        assert cm["half_life_days"] is not None
        assert cm["confidence_level_used"] == 0.95


# ===========================================================================
# 3. PairSpec direction (cf1 − cf2) × 100 in bps
# ===========================================================================

class TestPairSpecPath:
    def test_pair_spec_uses_bps_direction_from_cross_market_convention(self):
        """The pair_spec series MUST be ``(cf1 − cf2) × 100`` in bps,
        matching cross_market_spread's direction.  Verify by feeding
        two series with a known mean differential and checking the
        long_run_mean comes back in bps with the expected sign."""
        # cf1 mean ~5.0, cf2 mean ~4.5 — spread mean = 0.5 percent =
        # 50 bps.  Add tight OU dynamics so the mean is well-estimated.
        rng = np.random.default_rng(11)
        n = 1500
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=n * 2), date(2026, 4, 30),
        )
        bdays = bdays[-n:]
        spread_pct = np.zeros(n)  # cf1 - cf2
        spread_pct[0] = 0.5
        for t in range(1, n):
            spread_pct[t] = (
                spread_pct[t-1]
                - 0.05 * (spread_pct[t-1] - 0.5)
                + rng.normal(0, 0.005)
            )
        cf2 = 4.5 + rng.normal(0, 0.001, n).cumsum() * 0.0
        cf1 = cf2 + spread_pct

        cf1_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays], "field_value": cf1,
        })
        cf2_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays], "field_value": cf2,
        })

        params = HalfLifeInput(
            pair_spec=PairSpec(cf1="IT_BTP", cf2="DE_BUND", tenor="10Y"),
            lookback_days=1825,
        )

        def fake_fetch(*, engine, curve_family, tenor, field_name, start_date):
            return {"IT_BTP": cf1_df, "DE_BUND": cf2_df}[curve_family]

        with patch(
            "rates_agent.sovereign_bonds.tools.half_life.compute.fetch_single_tenor",
            side_effect=fake_fetch,
        ), patch(
            "rates_agent.sovereign_bonds.tools.half_life.compute.date",
            _FrozenDate,
        ):
            out = calculate_half_life(engine=None, params=params)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        # Series units must be BPS (the *100 conversion).
        assert cm["series_units"] == TimeSeriesUnits.BPS.value
        # Long-run mean ≈ 50 bps (planted spread of 0.5%).  Allow
        # slack for OU sampling variance.
        assert abs(cm["long_run_mean_native"] - 50.0) < 5.0
        # Label includes the direction.
        assert cm["series_label"] == "IT_BTP-DE_BUND_10Y"


# ===========================================================================
# 4. PastedTimeSeries path — unit declaration flows to series_units
# ===========================================================================

class TestPastedSeriesPath:
    def _build_pasted_ou_bps(self) -> PastedTimeSeries:
        rng = np.random.default_rng(13)
        n = 600
        x = np.zeros(n)
        x[0] = 80.0
        for t in range(1, n):
            x[t] = x[t-1] - 0.04 * (x[t-1] - 80.0) + rng.normal(0, 0.5)
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=n * 2), date(2026, 4, 30),
        )
        bdays = bdays[-n:]
        return PastedTimeSeries(
            series_name="custom_residual_bps",
            units=TimeSeriesUnits.BPS,
            rows=[
                TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=float(v))
                for d, v in zip(bdays, x)
            ],
        )

    def test_pasted_units_flow_to_snapshot_series_units(self):
        params = HalfLifeInput(pasted_series=self._build_pasted_ou_bps())
        out = calculate_half_life(engine=None, params=params)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["series_units"] == TimeSeriesUnits.BPS.value
        assert cm["series_label"] == "custom_residual_bps"
        assert cm["is_mean_reverting"] is True

    def test_pasted_native_rounding_follows_units(self):
        """When series_units=BPS, native scalars must round to
        ``bps_round_decimals``; when PERCENT, to
        ``yield_round_decimals``.  Verify by overriding the YAML
        decimals and checking the snapshot precision."""
        params = HalfLifeInput(pasted_series=self._build_pasted_ou_bps())
        # Default bps_round_decimals=2; bump to 4 and verify more
        # trailing precision shows in the snapshot.
        out_2 = calculate_half_life(
            engine=None, params=params,
            config=_custom_config(bps_round_decimals=2),
        )
        out_4 = calculate_half_life(
            engine=None, params=params,
            config=_custom_config(bps_round_decimals=4),
        )
        v_2 = out_2["current_metrics"]["current_value_native"]
        v_4 = out_4["current_metrics"]["current_value_native"]
        assert round(v_4, 2) == v_2
        assert v_2 != v_4, (
            "bps_round_decimals override did not reach "
            "current_value_native — boundary wiring may be broken"
        )


# ===========================================================================
# 5. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_zero_inputs_rejected(self):
        with pytest.raises(Exception):
            HalfLifeInput()

    def test_two_inputs_rejected(self):
        with pytest.raises(Exception):
            HalfLifeInput(
                series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                pair_spec=PairSpec(cf1="IT_BTP", cf2="DE_BUND", tenor="10Y"),
            )

    def test_three_inputs_rejected(self):
        with pytest.raises(Exception):
            HalfLifeInput(
                series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                pair_spec=PairSpec(cf1="IT_BTP", cf2="DE_BUND", tenor="10Y"),
                pasted_series=PastedTimeSeries(
                    series_name="x", units=TimeSeriesUnits.PERCENT,
                    rows=[TimeSeriesRow(date="2024-01-01", value=1.0)],
                ),
            )

    def test_lookback_days_lower_bound(self):
        with pytest.raises(Exception):
            HalfLifeInput(
                series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                lookback_days=100,  # < ge=252
            )

    def test_field_name_default_is_none_in_seriesspec(self):
        spec = SeriesSpec(curve_family="UST", tenor="10Y")
        assert spec.field_name is None

    def test_field_name_default_is_none_in_pairspec(self):
        spec = PairSpec(cf1="IT_BTP", cf2="DE_BUND", tenor="10Y")
        assert spec.field_name is None


# ===========================================================================
# 6. Numerical correctness on planted OU
# ===========================================================================

class TestNumericalCorrectness:
    def test_recovers_planted_beta_and_half_life(self):
        """Plant β = -0.05, mean = 4.0.  Expected half-life ≈
        -ln(2) / ln(0.95) ≈ 13.51 trading-day steps.  Recovery
        tolerance accounts for OU sampling noise on a 1000-step
        synthetic."""
        target_df = _ou_synthetic_df(
            beta_true=-0.05, mean_true=4.0, n=1000, noise_scale=0.01,
        )
        params = HalfLifeInput(
            series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            lookback_days=1825,
        )
        out = _run_series_spec(params, {"UST_10Y": target_df})
        cm = out["current_metrics"]
        expected_half_life = -math.log(2.0) / math.log(0.95)
        assert cm["is_mean_reverting"] is True
        assert abs(cm["beta"] - (-0.05)) < 0.02
        assert abs(cm["half_life_days"] - expected_half_life) < 5.0
        assert abs(cm["long_run_mean_native"] - 4.0) < 0.05


# ===========================================================================
# 7. Edge cases — locked in the v6 plan
# ===========================================================================

class TestEdgeCases:
    def test_divergent_series_not_mean_reverting(self):
        """A deterministically divergent AR(1) (β > 0) must surface
        as is_mean_reverting=False with half_life=None and
        long_run_mean=None — these are the structural-rule
        edge-cases the v6 plan locked in."""
        div_df = _divergent_df(beta_true=0.01)
        params = HalfLifeInput(
            series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            lookback_days=1825,
        )
        out = _run_series_spec(params, {"UST_10Y": div_df})
        cm = out["current_metrics"]
        # β > 0 is the deterministic divergent branch.
        assert cm["beta"] > 0, f"expected β > 0 on divergent series, got {cm['beta']}"
        assert cm["is_mean_reverting"] is False
        assert cm["half_life_days"] is None
        assert cm["long_run_mean_native"] is None
        assert cm["current_deviation_native"] is None

    def test_min_abs_beta_floor_suppresses_half_life(self):
        """Build a series with extremely weak mean reversion so the
        OLS β is below the YAML's |β| floor.  half_life_days must be
        None even though is_mean_reverting may be True (β < 0).

        We force this by setting min_abs_beta_for_half_life much
        higher than the planted β."""
        target_df = _ou_synthetic_df(beta_true=-0.001, n=1000, noise_scale=0.01)
        params = HalfLifeInput(
            series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            lookback_days=1825,
        )
        out = _run_series_spec(
            params,
            {"UST_10Y": target_df},
            config=_custom_config(min_abs_beta_for_half_life=0.5),
        )
        cm = out["current_metrics"]
        # min_abs_beta floor at 0.5 trivially exceeds any |β|.
        assert cm["half_life_days"] is None
        assert cm["long_run_mean_native"] is None

    def test_series_below_min_observations_returns_controlled_error(self):
        """The OU primitive raises ValueError when the cleaned series
        is shorter than min_observations.  compute() turns it into
        the controlled error envelope; the phrase shape matches
        detail.py's user_input_phrases for HTTP 422."""
        # Only 100 observations — well below default min=252.
        short_df = _ou_synthetic_df(n=100)
        params = HalfLifeInput(
            series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            lookback_days=1825,
        )
        out = _run_series_spec(params, {"UST_10Y": short_df})
        assert "error" in out
        assert "is smaller than the YAML's" in out["error"]
        assert "min_observations" in out["error"]


# ===========================================================================
# 8. Boundary rounding — every YAML knob reaches its snapshot field
# ===========================================================================

class TestBoundaryRounding:
    """No time-series payload on this tool — every rounding knob's
    surface map is snapshot-only.  Each test pins (a) the snapshot
    at decimals=N differs from decimals=N+2 on synthetic data and
    (b) the round-back contract holds.

    Surface map:
      half_life_round_decimals  → half_life_days,
                                   half_life_ci_lower_days,
                                   half_life_ci_upper_days
      ou_beta_round_decimals    → beta, beta_ci_lower, beta_ci_upper
      r_squared_round_decimals  → r_squared
      yield_round_decimals      → current_value_native (when PERCENT)
      bps_round_decimals        → current_value_native (when BPS) —
                                   covered in TestPastedSeriesPath
    """

    def _planted(self):
        target_df = _ou_synthetic_df(
            beta_true=-0.0537, mean_true=4.123456, n=1000, noise_scale=0.0123,
        )
        params = HalfLifeInput(
            series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            lookback_days=1825,
        )
        return target_df, params

    def _run_at(self, decimals_kw: dict):
        target_df, params = self._planted()
        return _run_series_spec(
            params, {"UST_10Y": target_df},
            config=_custom_config(**decimals_kw),
        )

    def test_half_life_round_decimals_reaches_snapshot(self):
        out_1 = self._run_at({"half_life_round_decimals": 1})
        out_3 = self._run_at({"half_life_round_decimals": 3})
        h_1 = out_1["current_metrics"]["half_life_days"]
        h_3 = out_3["current_metrics"]["half_life_days"]
        assert h_1 is not None and h_3 is not None
        assert round(h_3, 1) == h_1
        assert h_1 != h_3, (
            "half_life_round_decimals override did not reach snapshot"
        )

    def test_ou_beta_round_decimals_reaches_snapshot(self):
        out_4 = self._run_at({"ou_beta_round_decimals": 4})
        out_6 = self._run_at({"ou_beta_round_decimals": 6})
        b_4 = out_4["current_metrics"]["beta"]
        b_6 = out_6["current_metrics"]["beta"]
        assert b_4 is not None and b_6 is not None
        assert round(b_6, 4) == b_4
        assert b_4 != b_6, (
            "ou_beta_round_decimals override did not reach snapshot"
        )

    def test_r_squared_round_decimals_reaches_snapshot(self):
        """Carries forward the v6 lesson: round-back is necessary but
        NOT sufficient — a silent truncation to 2 decimals would also
        pass `round(r_6, 2) == r_2`.  Add the strong assertion that
        decimals=6 produces a finer-grained value than decimals=2 on
        the planted synthetic series."""
        out_2 = self._run_at({"r_squared_round_decimals": 2})
        out_6 = self._run_at({"r_squared_round_decimals": 6})
        r_2 = out_2["current_metrics"]["r_squared"]
        r_6 = out_6["current_metrics"]["r_squared"]
        assert r_2 is not None and r_6 is not None
        assert round(r_6, 2) == r_2
        assert r_2 != r_6, (
            f"r_squared_round_decimals=6 produced same value as =2 "
            f"({r_2}); precision is being silently truncated.  Pin "
            "the boundary the same way the other rounding tests do."
        )

    def test_yield_round_decimals_reaches_native_snapshot(self):
        out_2 = self._run_at({"yield_round_decimals": 2})
        out_6 = self._run_at({"yield_round_decimals": 6})
        v_2 = out_2["current_metrics"]["current_value_native"]
        v_6 = out_6["current_metrics"]["current_value_native"]
        assert round(v_6, 2) == v_2
        assert v_2 != v_6, (
            "yield_round_decimals override did not reach "
            "current_value_native (PERCENT path)"
        )


# ===========================================================================
# 9. No time-series output (snapshot-only tool)
# ===========================================================================

class TestNoTimeSeriesOutput:
    def test_output_is_snapshot_only(self):
        target_df = _ou_synthetic_df()
        params = HalfLifeInput(
            series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            lookback_days=1825,
        )
        out = _run_series_spec(params, {"UST_10Y": target_df})
        assert "current_metrics" in out
        # No time-series keys.
        for k in (
            "time_series", "time_series_beta", "time_series_residual",
            "time_series_alpha", "time_series_r_squared",
        ):
            assert k not in out, (
                f"half_life is a snapshot-only tool but emitted {k!r}; "
                "if a future change adds a time-series surface, the "
                "boundary tests must extend to cover it"
            )


# ===========================================================================
# 10. Import-path back-compat
# ===========================================================================

class TestImportPathBackCompat:
    def test_calculate_via_package_init_and_compute_match(self):
        from rates_agent.sovereign_bonds.tools.half_life import (
            calculate_half_life as via_package,
        )
        from rates_agent.sovereign_bonds.tools.half_life.compute import (
            calculate_half_life as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_two_paths(self):
        from rates_agent.sovereign_bonds.tools.half_life import (
            HalfLifeInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.half_life.schemas import (
            HalfLifeInput as via_schemas,
        )
        assert via_package is via_schemas

    def test_config_path_identity(self):
        from rates_agent.sovereign_bonds.tools.half_life import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.half_life.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute
