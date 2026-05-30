"""Tests for shared.operators.rolling_zscore.

Covers the contract surface every v2.0 operator must satisfy:

  - happy path against an independent NumPy reference
  - look-ahead-safe vs in-sample mechanics (one-period shift)
  - OPR2 constant output type (always Series, Z_SCORE units, input frequency)
  - OPR8 config-default resolution (params=None matches explicit defaults)
  - OPR8 schema-default mirrors the YAML default (no silent divergence)
  - OPR9 typed I/O (non-Series input refused)
  - OPR10 lineage extension (N -> N+1; one OperatorStep) + OPR14 determinism
  - OPR11 single-input operator (does NOT carry require_matching_* flags)
  - OPR13 typed RollingZscoreError on every degenerate input
    (empty input, window-too-large, constant input -> all-NaN output)
  - OPR6 finance-blindness (runs on non-rates synthetic data, any unit)
  - No NaN/Inf leakage: warmup positions are NaN by design; zero-std
    windows become NaN (not ±Inf in payload)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

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
from shared.operators.rolling_zscore import (
    CONFIG_PATH,
    RollingZscoreParams,
    rolling_zscore,
)
from shared.operators.rolling_zscore.operator import RollingZscoreError


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


# ===========================================================================
# Helpers
# ===========================================================================


def _series(
    series_key: str,
    *,
    dates,
    values,
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
    frequency=None,
    missingness_policy=None,
) -> Series:
    """Build a Series artifact with a synthesised fetch+adapter lineage."""
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
# 1. Happy path: independent NumPy reference for the look-ahead-safe case
# ===========================================================================


class TestHappyPath:
    def test_matches_independent_numpy_lookahead_safe(self):
        """Hand-compute the rolling z with NumPy and compare element-wise."""
        rng = np.random.RandomState(7)
        values = list(rng.randn(50).cumsum())
        dates = pd.bdate_range("2026-01-01", periods=50)
        s = _series("synth", dates=dates, values=values)

        window = 10
        out = rolling_zscore(
            s, params=RollingZscoreParams(window=window, look_ahead_safe=True),
        )
        assert out.units == TimeSeriesUnits.Z_SCORE
        assert len(out.payload) == len(values)

        # Independent NumPy computation: at time t, use the window
        # ending at t-1 (look-ahead-safe shift). ddof=1 sample std.
        arr = np.asarray(values, dtype=float)
        expected = np.full(len(arr), np.nan)
        for t in range(window, len(arr)):
            w = arr[t - window:t]  # [t-window, t-1]
            mu = w.mean()
            sd = w.std(ddof=1)
            if sd > 0:
                expected[t] = (arr[t] - mu) / sd
        actual = out.payload.to_numpy()
        np.testing.assert_allclose(
            actual[window:], expected[window:], rtol=1e-12, atol=1e-12,
        )
        # Pre-window positions are NaN — there is no history.
        assert pd.isna(actual[:window]).all()

    def test_in_sample_variant_uses_concurrent_window(self):
        """With look_ahead_safe=False, the window at t includes t."""
        rng = np.random.RandomState(11)
        values = list(rng.randn(40).cumsum())
        dates = pd.bdate_range("2026-01-01", periods=40)
        s = _series("synth", dates=dates, values=values)

        window = 8
        out = rolling_zscore(
            s, params=RollingZscoreParams(window=window, look_ahead_safe=False),
        )
        arr = np.asarray(values, dtype=float)
        expected = np.full(len(arr), np.nan)
        for t in range(window - 1, len(arr)):
            w = arr[t - window + 1:t + 1]  # [t-window+1, t]
            mu = w.mean()
            sd = w.std(ddof=1)
            if sd > 0:
                expected[t] = (arr[t] - mu) / sd
        np.testing.assert_allclose(
            out.payload.to_numpy()[window - 1:],
            expected[window - 1:],
            rtol=1e-12, atol=1e-12,
        )

    def test_output_inherits_frequency_and_missingness(self):
        dates = pd.bdate_range("2026-01-01", periods=30)
        s = _series(
            "x", dates=dates, values=np.linspace(0, 5, 30),
            frequency="B",
            missingness_policy=CleanSingleSeriesV1(ffill_limit=3),
        )
        out = rolling_zscore(s, params=RollingZscoreParams(window=5))
        assert out.frequency == "B"
        assert out.missingness_policy == CleanSingleSeriesV1(ffill_limit=3)
        # series_key preserved (this IS the input series, transformed)
        assert out.series_key == s.series_key

    def test_ddof_zero_changes_std_basis(self):
        """ddof=0 (population) gives a different scale than ddof=1 (sample)."""
        dates = pd.bdate_range("2026-01-01", periods=20)
        # Deterministic, non-constant values so std > 0 across windows
        values = np.asarray([0.0, 1.0, 0.5, 1.5, 2.0,
                             0.2, 0.8, 1.7, 0.3, 1.1,
                             0.4, 1.9, 0.7, 1.3, 0.6,
                             1.4, 0.9, 1.8, 0.1, 1.6])
        s = _series("x", dates=dates, values=list(values))
        out_d1 = rolling_zscore(
            s, params=RollingZscoreParams(window=8, ddof=1, look_ahead_safe=True),
        )
        out_d0 = rolling_zscore(
            s, params=RollingZscoreParams(window=8, ddof=0, look_ahead_safe=True),
        )
        # First valid position is index 8 (after the lookahead shift).
        v1 = float(out_d1.payload.iloc[8])
        v0 = float(out_d0.payload.iloc[8])
        # ddof=0 (divisor N) gives smaller variance/std than ddof=1
        # (divisor N-1), so |z| with ddof=0 is LARGER than with ddof=1.
        # The exact ratio is sqrt((N-1)/N) for the std, so the
        # |z_d1|/|z_d0| ratio equals sqrt((N-1)/N) with N=window=8.
        import math
        assert abs(v0) > abs(v1)
        expected_ratio = math.sqrt((8 - 1) / 8)
        assert (abs(v1) / abs(v0)) == pytest.approx(expected_ratio, rel=1e-12)


# ===========================================================================
# 2. OPR8 — config defaults + schema-default mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_resolves_from_config(self):
        """Calling with params=None must match an explicit-params call
        constructed from config defaults."""
        rng = np.random.RandomState(13)
        values = list(rng.randn(80).cumsum())
        dates = pd.bdate_range("2026-01-01", periods=80)
        s = _series("x", dates=dates, values=values)

        cfg = load_operator_config(CONFIG_PATH)
        explicit = RollingZscoreParams(
            window=int(cfg.default_value("window")),
            min_periods=None,
            ddof=int(cfg.default_value("ddof")),
            look_ahead_safe=bool(cfg.default_value("look_ahead_safe")),
        )
        out_a = rolling_zscore(s, params=None)
        out_b = rolling_zscore(s, params=explicit)
        # Same inputs + same effective params → same head_hash (OPR14).
        assert out_a.lineage.head_hash == out_b.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        """OPR8 parity: schema default == YAML default for every field
        the YAML defaults.  ``min_periods`` is declared null in YAML to
        defer to ``window`` at runtime; the schema mirrors None."""
        cfg = load_operator_config(CONFIG_PATH)
        p = RollingZscoreParams()
        assert p.window == int(cfg.default_value("window"))
        assert p.ddof == int(cfg.default_value("ddof"))
        assert p.look_ahead_safe == bool(cfg.default_value("look_ahead_safe"))
        assert p.min_periods is None and cfg.default_value("min_periods") is None


# ===========================================================================
# 3. OPR13 — typed refusals on degenerate inputs
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        with pytest.raises(RollingZscoreError, match="must be a Series"):
            rolling_zscore([1.0, 2.0, 3.0])  # type: ignore[arg-type]

    def test_empty_series_raises(self):
        s = _series("empty", dates=[], values=[])
        with pytest.raises(RollingZscoreError, match="empty"):
            rolling_zscore(s, params=RollingZscoreParams(window=3))

    def test_window_longer_than_series_all_nan_raises(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        s = _series("x", dates=dates, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        with pytest.raises(RollingZscoreError, match="all-NaN"):
            rolling_zscore(s, params=RollingZscoreParams(window=20))

    def test_constant_input_all_zero_std_raises(self):
        dates = pd.bdate_range("2026-01-01", periods=30)
        s = _series("flat", dates=dates, values=[3.14] * 30)
        with pytest.raises(RollingZscoreError, match="all-NaN"):
            rolling_zscore(s, params=RollingZscoreParams(window=10))

    def test_invalid_min_periods_at_schema_level(self):
        """min_periods > window is incoherent — caught at schema validation."""
        with pytest.raises(ValueError, match="min_periods"):
            RollingZscoreParams(window=10, min_periods=20)


# ===========================================================================
# 4. OPR11 — single-input operator carries NO require_matching_* flags
# ===========================================================================


class TestSingleInputMetadata:
    def test_no_require_matching_flags(self):
        """Single-input operators do not carry the OPR11 multi-input
        controls.  Adding them would be honest-noise."""
        fields = set(RollingZscoreParams.model_fields)
        assert "require_matching_frequency" not in fields
        assert "require_matching_missingness" not in fields


# ===========================================================================
# 5. OPR10 lineage + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_by_one_step(self):
        dates = pd.bdate_range("2026-01-01", periods=30)
        s = _series("x", dates=dates, values=list(np.linspace(0, 5, 30)))
        out = rolling_zscore(s, params=RollingZscoreParams(window=5))
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "rolling_zscore"
        assert head.version == "1.0.0"
        # No auxiliary lineages on single-input operators.
        assert head.auxiliary_lineages == ()
        # Input units recorded for provenance, output is Z_SCORE.
        assert head.params["input_units"] == "percent"
        assert out.units == TimeSeriesUnits.Z_SCORE

    def test_rerun_is_deterministic(self):
        dates = pd.bdate_range("2026-01-01", periods=40)
        values = list(np.sin(np.linspace(0, 6, 40)) + 0.1 * np.arange(40))
        s = _series("x", dates=dates, values=values)
        out1 = rolling_zscore(s, params=RollingZscoreParams(window=8))
        out2 = rolling_zscore(s, params=RollingZscoreParams(window=8))
        assert out1.lineage.head_hash == out2.lineage.head_hash
        np.testing.assert_array_equal(
            out1.payload.to_numpy(), out2.payload.to_numpy(),
        )


# ===========================================================================
# 6. OPR6 — finance-blindness: runs unchanged on any unit
# ===========================================================================


def test_runs_on_z_score_unit_input():
    """A z-score of a z-score is meaningful; the operator does not care
    about the input unit and outputs Z_SCORE regardless."""
    dates = pd.bdate_range("2026-01-01", periods=40)
    rng = np.random.RandomState(99)
    s = _series(
        "synth", dates=dates, values=list(rng.randn(40).cumsum()),
        units=TimeSeriesUnits.Z_SCORE,
    )
    out = rolling_zscore(s, params=RollingZscoreParams(window=10))
    assert out.units == TimeSeriesUnits.Z_SCORE


def test_runs_on_count_unit_input():
    """COUNT input → Z_SCORE output (finance-blind unit promotion)."""
    dates = pd.bdate_range("2026-01-01", periods=40)
    values = list(np.arange(40, dtype=float) + np.sin(np.arange(40)))
    s = _series("counts", dates=dates, values=values, units=TimeSeriesUnits.COUNT)
    out = rolling_zscore(s, params=RollingZscoreParams(window=10))
    assert out.units == TimeSeriesUnits.Z_SCORE


# ===========================================================================
# 7. No NaN/Inf leakage in payload
# ===========================================================================


def test_zero_std_window_emits_nan_not_inf():
    """A run of identical values inside the window makes std=0; the z
    must be NaN at those positions, NEVER ±Inf (artifact layer forbids
    Inf in payload)."""
    dates = pd.bdate_range("2026-01-01", periods=30)
    # First 12 values are constant -> std==0 across any window inside the run
    # Then varying values so the rolling stats become non-degenerate
    values = [5.0] * 12 + list(np.linspace(0, 4, 18))
    s = _series("x", dates=dates, values=values)
    out = rolling_zscore(
        s, params=RollingZscoreParams(window=5, look_ahead_safe=True),
    )
    arr = out.payload.to_numpy()
    # No ±Inf anywhere — that would have been rejected by Series construction.
    assert not np.isinf(arr).any()
    # The constant-window positions should be NaN
    assert pd.isna(out.payload.iloc[6])
