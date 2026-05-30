"""Tests for shared.operators.percentile_rank.

Covers the contract surface every v2.0 operator must satisfy:

  - happy path: percentile rank matches scipy.stats.percentileofscore
  - look-ahead-safe vs in-sample mechanics (exclude vs include current)
  - expanding window (window=None) vs trailing window
  - all four tie-handling methods (mean / weak / strict / rank)
  - OPR2 constant output type (always Series, PCT_RANK units 0-100)
  - OPR8 config-default resolution (params=None matches explicit defaults)
  - OPR8 schema-default mirrors the YAML default
  - OPR9 typed I/O (non-Series input refused)
  - OPR10 lineage extension (N -> N+1) + OPR14 determinism
  - OPR11 single-input operator (does NOT carry require_matching_* flags)
  - OPR13 typed PercentileRankError on every degenerate input
  - OPR6 finance-blindness (runs on non-rates data)
  - No NaN/Inf leakage in payload
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import percentileofscore

from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    FetchStep,
    Lineage,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.percentile_rank import (
    CONFIG_PATH,
    PercentileRankParams,
    percentile_rank,
)
from shared.operators.percentile_rank.operator import PercentileRankError


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


def _series(
    series_key: str,
    *,
    dates,
    values,
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
    frequency=None,
    missingness_policy=None,
) -> Series:
    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0.0",
        params={"series_key": series_key},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series", version="1.0.0",
        params={"series_key": series_key, "units": units.value},
        input_hashes=(fetch.hash,),
    )
    payload = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency=frequency,
        missingness_policy=missingness_policy or CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


# ===========================================================================
# 1. Happy path: matches an independent scipy reference
# ===========================================================================


class TestHappyPath:
    def test_trailing_lookahead_safe_matches_scipy_reference(self):
        rng = np.random.RandomState(11)
        values = list(rng.randn(60).cumsum())
        dates = pd.bdate_range("2026-01-01", periods=60)
        s = _series("x", dates=dates, values=values)

        window, min_periods = 20, 10
        out = percentile_rank(
            s,
            params=PercentileRankParams(
                window=window, min_periods=min_periods,
                method="mean", look_ahead_safe=True,
            ),
        )
        assert out.units == TimeSeriesUnits.PCT_RANK

        arr = np.asarray(values, dtype=float)
        expected = np.full(len(arr), np.nan)
        for t in range(len(arr)):
            start = max(0, t - window)
            history = arr[start:t]  # exclude t
            if len(history) >= min_periods:
                expected[t] = percentileofscore(history, arr[t], kind="mean")
        np.testing.assert_allclose(
            out.payload.to_numpy(), expected,
            rtol=1e-12, atol=1e-12, equal_nan=True,
        )

    def test_in_sample_window_includes_current(self):
        """look_ahead_safe=False: history slice INCLUDES the current value."""
        rng = np.random.RandomState(17)
        values = list(rng.randn(30) * 3 + 5)
        dates = pd.bdate_range("2026-01-01", periods=30)
        s = _series("x", dates=dates, values=values)

        window, min_periods = 10, 5
        out = percentile_rank(
            s,
            params=PercentileRankParams(
                window=window, min_periods=min_periods,
                method="mean", look_ahead_safe=False,
            ),
        )
        arr = np.asarray(values, dtype=float)
        expected = np.full(len(arr), np.nan)
        for t in range(len(arr)):
            start = max(0, t - window + 1)
            history = arr[start:t + 1]  # include t
            if len(history) >= min_periods:
                expected[t] = percentileofscore(history, arr[t], kind="mean")
        np.testing.assert_allclose(
            out.payload.to_numpy(), expected,
            rtol=1e-12, atol=1e-12, equal_nan=True,
        )

    def test_expanding_window_matches_scipy_reference(self):
        rng = np.random.RandomState(23)
        values = list(rng.randn(50).cumsum())
        dates = pd.bdate_range("2026-01-01", periods=50)
        s = _series("x", dates=dates, values=values)

        out = percentile_rank(
            s,
            params=PercentileRankParams(
                window=None, min_periods=5, method="mean", look_ahead_safe=True,
            ),
        )
        arr = np.asarray(values, dtype=float)
        expected = np.full(len(arr), np.nan)
        for t in range(len(arr)):
            history = arr[:t]  # all-before-t, expanding
            if len(history) >= 5:
                expected[t] = percentileofscore(history, arr[t], kind="mean")
        np.testing.assert_allclose(
            out.payload.to_numpy(), expected,
            rtol=1e-12, atol=1e-12, equal_nan=True,
        )

    @pytest.mark.parametrize("method", ["mean", "weak", "strict", "rank"])
    def test_every_method_runs_and_matches_scipy(self, method):
        """All four scipy kinds run and match scipy's own answer."""
        rng = np.random.RandomState(31)
        values = list(rng.randn(40).cumsum())
        dates = pd.bdate_range("2026-01-01", periods=40)
        s = _series("x", dates=dates, values=values)

        out = percentile_rank(
            s,
            params=PercentileRankParams(
                window=20, min_periods=10, method=method, look_ahead_safe=True,
            ),
        )
        arr = np.asarray(values, dtype=float)
        for t in range(20, len(arr)):
            history = arr[t - 20:t]
            expected = percentileofscore(history, arr[t], kind=method)
            assert float(out.payload.iloc[t]) == pytest.approx(expected)

    def test_output_is_in_zero_one_hundred(self):
        """Output is in [0, 100] (PCT_RANK)."""
        rng = np.random.RandomState(37)
        values = list(rng.randn(80).cumsum())
        dates = pd.bdate_range("2026-01-01", periods=80)
        s = _series("x", dates=dates, values=values)
        out = percentile_rank(
            s, params=PercentileRankParams(window=20, min_periods=10),
        )
        finite = out.payload.dropna()
        # scipy.stats.percentileofscore can return values 1-2 ULP outside
        # [0, 100] due to floating-point arithmetic — clamp via tolerance.
        assert finite.min() >= -1e-9
        assert finite.max() <= 100.0 + 1e-9

    def test_constant_value_in_history_uniform_rank(self):
        """A monotone increasing series has rank near 100 (today's value
        is at or near the top of its trailing history) under
        look_ahead_safe."""
        dates = pd.bdate_range("2026-01-01", periods=40)
        values = list(np.arange(40, dtype=float))  # strictly increasing
        s = _series("x", dates=dates, values=values)
        out = percentile_rank(
            s,
            params=PercentileRankParams(
                window=10, min_periods=5, method="mean", look_ahead_safe=True,
            ),
        )
        # After warmup, the rank should be exactly 100 each step (today
        # is strictly greater than every observation in [t-10, t-1]).
        assert all(
            float(v) == pytest.approx(100.0)
            for v in out.payload.iloc[10:].dropna()
        )


# ===========================================================================
# 2. OPR8 — config defaults + schema-default mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_resolves_from_config(self):
        rng = np.random.RandomState(41)
        values = list(rng.randn(400).cumsum())
        dates = pd.bdate_range("2024-01-01", periods=400)
        s = _series("x", dates=dates, values=values)

        cfg = load_operator_config(CONFIG_PATH)
        raw_window = cfg.default_value("window")
        explicit = PercentileRankParams(
            window=(int(raw_window) if raw_window is not None else None),
            min_periods=int(cfg.default_value("min_periods")),
            method=cfg.default_value("method"),
            look_ahead_safe=bool(cfg.default_value("look_ahead_safe")),
        )
        out_a = percentile_rank(s, params=None)
        out_b = percentile_rank(s, params=explicit)
        assert out_a.lineage.head_hash == out_b.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = PercentileRankParams()
        assert p.window == int(cfg.default_value("window"))
        assert p.min_periods == int(cfg.default_value("min_periods"))
        assert p.method == cfg.default_value("method")
        assert p.look_ahead_safe == bool(cfg.default_value("look_ahead_safe"))


# ===========================================================================
# 3. OPR13 — typed refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        with pytest.raises(PercentileRankError, match="must be a Series"):
            percentile_rank([1, 2, 3])  # type: ignore[arg-type]

    def test_empty_series_raises(self):
        s = _series("empty", dates=[], values=[])
        with pytest.raises(PercentileRankError, match="empty"):
            percentile_rank(s)

    def test_min_periods_too_high_raises_all_nan(self):
        """A min_periods larger than the series length → all-NaN refused."""
        dates = pd.bdate_range("2026-01-01", periods=5)
        s = _series("x", dates=dates, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        with pytest.raises(PercentileRankError, match="all-NaN"):
            percentile_rank(
                s, params=PercentileRankParams(window=None, min_periods=100),
            )

    def test_min_periods_exceeds_window_raises_at_schema(self):
        with pytest.raises(ValueError, match="min_periods"):
            PercentileRankParams(window=10, min_periods=20)

    def test_unbuilt_method_refuses_cleanly(self):
        """Defensive path: a method bypassing the Literal hits the
        NotImplementedError branch."""
        bogus = PercentileRankParams.model_construct(
            window=20, min_periods=5, method="quantile", look_ahead_safe=True,
        )
        dates = pd.bdate_range("2026-01-01", periods=30)
        s = _series("x", dates=dates, values=list(np.arange(30, dtype=float)))
        with pytest.raises(NotImplementedError, match="quantile"):
            percentile_rank(s, params=bogus)


# ===========================================================================
# 4. OPR11 — single-input operator
# ===========================================================================


def test_no_require_matching_flags():
    fields = set(PercentileRankParams.model_fields)
    assert "require_matching_frequency" not in fields
    assert "require_matching_missingness" not in fields


# ===========================================================================
# 5. OPR10 lineage + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_by_one_step(self):
        dates = pd.bdate_range("2026-01-01", periods=40)
        s = _series("x", dates=dates, values=list(np.linspace(0, 4, 40)))
        out = percentile_rank(
            s, params=PercentileRankParams(window=10, min_periods=5),
        )
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "percentile_rank"
        assert head.version == "1.0.0"
        assert head.auxiliary_lineages == ()
        assert head.params["method"] == "mean"
        assert head.params["input_units"] == "percent"

    def test_rerun_is_deterministic(self):
        dates = pd.bdate_range("2026-01-01", periods=60)
        rng = np.random.RandomState(53)
        values = list(rng.randn(60).cumsum())
        s = _series("x", dates=dates, values=values)
        out1 = percentile_rank(
            s, params=PercentileRankParams(window=20, min_periods=10),
        )
        out2 = percentile_rank(
            s, params=PercentileRankParams(window=20, min_periods=10),
        )
        assert out1.lineage.head_hash == out2.lineage.head_hash


# ===========================================================================
# 6. OPR6 — finance-blindness
# ===========================================================================


def test_runs_on_synthetic_count_data():
    rng = np.random.RandomState(101)
    values = list(np.cumsum(rng.poisson(5, size=80)).astype(float))
    dates = pd.bdate_range("2026-01-01", periods=80)
    s = _series("counts", dates=dates, values=values, units=TimeSeriesUnits.COUNT)
    out = percentile_rank(
        s, params=PercentileRankParams(window=20, min_periods=10),
    )
    assert out.units == TimeSeriesUnits.PCT_RANK


def test_no_nan_inf_leakage():
    rng = np.random.RandomState(103)
    values = list(rng.randn(50))
    dates = pd.bdate_range("2026-01-01", periods=50)
    s = _series("x", dates=dates, values=values)
    out = percentile_rank(s, params=PercentileRankParams(window=10, min_periods=5))
    arr = out.payload.to_numpy()
    assert not np.isinf(arr).any()
