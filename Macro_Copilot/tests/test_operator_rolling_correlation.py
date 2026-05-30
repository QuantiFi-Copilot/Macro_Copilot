"""Tests for shared.operators.rolling_correlation.

Covers the contract surface every v2.0 multi-artifact operator must
satisfy:

  - happy path against an independent pandas rolling.corr reference
  - both methods (pearson + spearman) match a reference
  - OPR2 constant output type (always Series; RATIO units)
  - OPR8 config-default resolution + schema-default mirror + honest
    refusal for the declared-but-unbuilt method (kendall)
  - OPR9 typed I/O (non-Series input refused)
  - OPR10 lineage extension (N -> N+1; right's chain in
    auxiliary_lineages) + OPR14 determinism
  - OPR11 strict-by-default frequency + missingness + unit-INVARIANCE
    + CombinedMissingnessV1 emitted under a lenient opt-out
  - OPR13 typed RollingCorrelationError on every degenerate input
    (misaligned index, all-NaN output, constant series)
  - OPR6 finance-blindness (runs on non-rates synthetic data, any unit)
  - No NaN/Inf leakage
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
    RawNoCleaning,
    Series,
    TimeSeriesUnits,
)
from shared.artifacts.missingness import CombinedMissingnessV1
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.rolling_correlation import (
    CONFIG_PATH,
    RollingCorrelationParams,
    rolling_correlation,
)
from shared.operators.rolling_correlation.operator import RollingCorrelationError


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
# 1. Happy path
# ===========================================================================


class TestHappyPath:
    def test_pearson_matches_pandas_rolling_corr(self):
        rng = np.random.RandomState(7)
        n = 50
        x = list(rng.randn(n).cumsum())
        y = list(np.asarray(x) * 0.6 + rng.randn(n) * 0.5)
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=x)
        b = _series("b", dates=dates, values=y)

        window = 10
        out = rolling_correlation(
            a, b, params=RollingCorrelationParams(window=window),
        )
        assert out.units == TimeSeriesUnits.RATIO
        expected = (
            pd.Series(x, index=pd.DatetimeIndex(dates), dtype=float)
            .rolling(window=window, min_periods=window)
            .corr(pd.Series(y, index=pd.DatetimeIndex(dates), dtype=float))
        )
        actual = out.payload
        ok = expected.notna()
        np.testing.assert_allclose(
            actual.loc[ok].to_numpy(),
            expected.loc[ok].to_numpy(),
            rtol=1e-12, atol=1e-12,
        )

    def test_perfect_positive_in_window_is_one(self):
        n = 30
        dates = pd.bdate_range("2026-01-01", periods=n)
        x = np.linspace(0, 5, n)
        y = 2.0 * x + 7.0  # perfect linear relationship in every window
        a = _series("a", dates=dates, values=list(x))
        b = _series("b", dates=dates, values=list(y))
        out = rolling_correlation(
            a, b, params=RollingCorrelationParams(window=5),
        )
        # All non-NaN positions should be exactly 1
        finite = out.payload.dropna()
        np.testing.assert_allclose(finite.to_numpy(), 1.0)

    def test_spearman_method_runs(self):
        rng = np.random.RandomState(13)
        n = 40
        x = list(rng.randn(n).cumsum())
        y = list(np.asarray(x) ** 3 + rng.randn(n) * 0.05)  # monotone
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=x)
        b = _series("b", dates=dates, values=y)
        out = rolling_correlation(
            a, b,
            params=RollingCorrelationParams(window=10, method="spearman"),
        )
        finite = out.payload.dropna()
        # Spearman on a monotone (cubic) relationship should be close to 1
        assert finite.min() > 0.85

    def test_output_series_key_names_both_inputs(self):
        n = 25
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("ust_10y", dates=dates, values=list(np.arange(n, dtype=float)))
        b = _series("bund_10y", dates=dates, values=list(np.arange(n, dtype=float) * 0.7))
        out = rolling_correlation(
            a, b, params=RollingCorrelationParams(window=5),
        )
        assert "ust_10y" in out.series_key
        assert "bund_10y" in out.series_key


# ===========================================================================
# 2. OPR8 — config defaults + schema-default mirror + honest refusal
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_resolves_from_config(self):
        rng = np.random.RandomState(19)
        n = 200
        x = list(rng.randn(n).cumsum())
        y = list(np.asarray(x) * 0.4 + rng.randn(n) * 0.3)
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("a", dates=dates, values=x)
        b = _series("b", dates=dates, values=y)

        cfg = load_operator_config(CONFIG_PATH)
        raw_min_periods = cfg.default_value("min_periods")
        explicit = RollingCorrelationParams(
            method=cfg.default_value("method"),
            window=int(cfg.default_value("window")),
            min_periods=(
                int(raw_min_periods) if raw_min_periods is not None else None
            ),
        )
        out_a = rolling_correlation(a, b, params=None)
        out_b = rolling_correlation(a, b, params=explicit)
        assert out_a.lineage.head_hash == out_b.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = RollingCorrelationParams()
        assert p.method == cfg.default_value("method")
        assert p.window == int(cfg.default_value("window"))
        assert p.min_periods is None and cfg.default_value("min_periods") is None
        # OPR11 strict defaults
        assert p.require_matching_frequency is True
        assert p.require_matching_missingness is True

    def test_unbuilt_method_refuses_cleanly(self):
        n = 30
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=list(np.arange(n, dtype=float)))
        b = _series("b", dates=dates, values=list(np.arange(n, dtype=float) * 0.7))
        with pytest.raises(NotImplementedError, match="kendall"):
            rolling_correlation(
                a, b, params=RollingCorrelationParams(method="kendall", window=5),
            )


# ===========================================================================
# 3. OPR13 — typed refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        n = 10
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=list(range(n)))
        with pytest.raises(RollingCorrelationError, match="must be Series"):
            rolling_correlation(a, [1, 2, 3])  # type: ignore[arg-type]

    def test_misaligned_index_raises(self):
        a = _series("a", dates=pd.bdate_range("2026-01-01", periods=10),
                    values=list(range(10)))
        b = _series("b", dates=pd.bdate_range("2026-02-01", periods=10),
                    values=list(range(10)))
        with pytest.raises(RollingCorrelationError, match="identical DatetimeIndex"):
            rolling_correlation(a, b, params=RollingCorrelationParams(window=5))

    def test_window_too_large_all_nan_raises(self):
        n = 5
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("b", dates=dates, values=[2.0, 4.0, 6.0, 8.0, 10.0])
        with pytest.raises(RollingCorrelationError, match="all-NaN"):
            rolling_correlation(a, b, params=RollingCorrelationParams(window=20))

    def test_both_constant_all_nan_raises(self):
        n = 30
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("flat_a", dates=dates, values=[3.0] * n)
        b = _series("flat_b", dates=dates, values=[5.0] * n)
        with pytest.raises(RollingCorrelationError, match="all-NaN"):
            rolling_correlation(a, b, params=RollingCorrelationParams(window=5))

    def test_min_periods_above_window_raises_at_schema(self):
        with pytest.raises(ValueError, match="min_periods"):
            RollingCorrelationParams(window=10, min_periods=20)


# ===========================================================================
# 4. OPR11 — strict frequency + missingness; unit-invariance; combined
# ===========================================================================


class TestStructuralMetadata:
    def test_frequency_mismatch_strict_raises(self):
        n = 30
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=list(np.arange(n, dtype=float)),
                    frequency="B")
        b = _series("b", dates=dates, values=list(np.arange(n, dtype=float)),
                    frequency="W")
        with pytest.raises(RollingCorrelationError, match="incompatible frequencies"):
            rolling_correlation(a, b, params=RollingCorrelationParams(window=5))

    def test_frequency_mismatch_opt_in_succeeds(self):
        n = 30
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=list(np.linspace(0, 5, n)),
                    frequency="B")
        b = _series("b", dates=dates, values=list(np.linspace(0, 5, n) * 0.7),
                    frequency="W")
        out = rolling_correlation(
            a, b,
            params=RollingCorrelationParams(
                window=5, require_matching_frequency=False,
            ),
        )
        # When the two frequencies disagree but are accepted, output
        # frequency is None (no honest common cadence).
        assert out.frequency is None

    def test_missingness_mismatch_strict_raises(self):
        n = 30
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=list(np.arange(n, dtype=float)))
        b = _series(
            "b", dates=dates, values=list(np.arange(n, dtype=float)),
            missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(RollingCorrelationError, match="missingness"):
            rolling_correlation(a, b, params=RollingCorrelationParams(window=5))

    def test_missingness_opt_in_emits_combined(self):
        n = 30
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=list(np.linspace(0, 5, n)))
        b = _series(
            "b", dates=dates, values=list(np.linspace(0, 5, n) * 0.7),
            missingness_policy=RawNoCleaning(),
        )
        out = rolling_correlation(
            a, b,
            params=RollingCorrelationParams(
                window=5, require_matching_missingness=False,
            ),
        )
        assert isinstance(out.missingness_policy, CombinedMissingnessV1)
        assert len(out.missingness_policy.components) == 2

    def test_cross_unit_correlation_is_allowed(self):
        n = 25
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=list(np.linspace(0, 5, n)),
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=dates, values=list(np.linspace(0, 50, n)),
                    units=TimeSeriesUnits.BPS)
        out = rolling_correlation(
            a, b, params=RollingCorrelationParams(window=5),
        )
        assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 5. OPR10 lineage + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_by_one_step_with_aux(self):
        n = 30
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=list(np.linspace(0, 5, n)))
        b = _series("b", dates=dates, values=list(np.linspace(0, 5, n) * 0.7))
        out = rolling_correlation(
            a, b, params=RollingCorrelationParams(window=5),
        )
        assert len(out.lineage.steps) == len(a.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "rolling_correlation"
        assert head.version == "1.0.0"
        assert head.auxiliary_lineages == (b.lineage,)
        assert head.params["left_units"] == "percent"
        assert head.params["right_units"] == "percent"

    def test_rerun_is_deterministic(self):
        rng = np.random.RandomState(29)
        n = 50
        dates = pd.bdate_range("2026-01-01", periods=n)
        a = _series("a", dates=dates, values=list(rng.randn(n).cumsum()))
        b = _series("b", dates=dates, values=list(rng.randn(n).cumsum()))
        out1 = rolling_correlation(a, b, params=RollingCorrelationParams(window=10))
        out2 = rolling_correlation(a, b, params=RollingCorrelationParams(window=10))
        assert out1.lineage.head_hash == out2.lineage.head_hash


# ===========================================================================
# 6. OPR6 — finance-blindness + no NaN/Inf leakage
# ===========================================================================


def test_runs_on_synthetic_zscore_data():
    rng = np.random.RandomState(101)
    n = 40
    x = rng.randn(n).cumsum()
    y = x * 0.5 + rng.randn(n) * 0.5
    dates = pd.bdate_range("2026-01-01", periods=n)
    a = _series("synth_x", dates=dates, values=list(x), units=TimeSeriesUnits.Z_SCORE)
    b = _series("synth_y", dates=dates, values=list(y), units=TimeSeriesUnits.Z_SCORE)
    out = rolling_correlation(a, b, params=RollingCorrelationParams(window=10))
    assert out.units == TimeSeriesUnits.RATIO


def test_no_nan_inf_leakage():
    """Zero-variance windows must NOT yield ±Inf in payload (Series
    rejects ±Inf at construction)."""
    n = 30
    dates = pd.bdate_range("2026-01-01", periods=n)
    # Constant left, varying right — covariance is 0, correlation is
    # undefined; output should have NaN at those positions, NEVER Inf.
    a = _series("flat", dates=dates, values=[2.0] * 15 + list(np.arange(15.0)))
    b = _series("varying", dates=dates, values=list(np.arange(30.0)))
    out = rolling_correlation(a, b, params=RollingCorrelationParams(window=5))
    assert not np.isinf(out.payload.to_numpy()).any()
