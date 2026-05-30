"""Tests for shared.operators.cointegration.

Covers the contract surface every v2.0 multi-artifact operator must
satisfy:

  - happy path on a KNOWN cointegrated pair (y = x + small noise)
    → low p-value, very negative test stat
  - control on a KNOWN non-cointegrated pair (two independent random
    walks under a fixed seed) → high p-value
  - OPR2 constant output type (always ScalarMetric, RATIO units)
  - OPR8 config-default resolution + schema-default mirror + honest
    refusal for declared-but-unbuilt method (defensive)
  - OPR9 typed I/O (non-Series input refused)
  - OPR10 lineage extension (N -> N+1; right's chain in
    auxiliary_lineages) + OPR14 determinism
  - OPR11 strict-by-default frequency + missingness + unit-INVARIANCE
  - OPR13 typed CointegrationError on every degenerate input
    (index mismatch, < min_periods, zero-variance, ±Inf test stat from
    near-perfect colinearity)
  - OPR6 finance-blindness (runs on non-rates synthetic data)
  - Diagnostic recovery: beta + p-value + n_obs land in step.params
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
    ScalarMetric,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.cointegration import (
    CONFIG_PATH,
    CointegrationParams,
    cointegration,
)
from shared.operators.cointegration.operator import CointegrationError


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
# 1. Happy path: known cointegrated pair vs known non-cointegrated pair
# ===========================================================================


class TestHappyPath:
    def test_known_cointegrated_pair_rejects_null(self):
        """y = x + small_noise — a textbook cointegrated pair: x is I(1)
        (random walk), y is x plus a stationary disturbance, so the
        residual y - β·x is stationary by construction.  Expect a very
        negative ADF stat and a very small p-value."""
        rng = np.random.RandomState(42)
        n = 200
        x = rng.randn(n).cumsum()
        noise = rng.randn(n) * 0.1  # small stationary disturbance
        y = x + noise
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("y", dates=dates, values=list(y))
        b = _series("x", dates=dates, values=list(x))
        out = cointegration(a, b)

        assert isinstance(out, ScalarMetric)
        assert out.units == TimeSeriesUnits.RATIO
        # Very negative test stat → strongly rejects H0=no cointegration
        assert out.value < -4.0
        # p-value (in lineage params) very small
        head = out.lineage.steps[-1]
        assert head.params["p_value"] < 0.05
        assert head.params["n_obs"] == n

    def test_known_non_cointegrated_pair_does_not_reject(self):
        """Two independent random walks have no cointegrating relationship —
        the residual y - β·x is itself a random walk and the ADF test
        should fail to reject the unit-root null.  Expect a p-value that
        is NOT small."""
        rng = np.random.RandomState(7)
        n = 200
        x = rng.randn(n).cumsum()
        y = rng.randn(n).cumsum()  # independent
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("y", dates=dates, values=list(y))
        b = _series("x", dates=dates, values=list(x))
        out = cointegration(a, b)
        head = out.lineage.steps[-1]
        # The p-value should be well above 0.05 for two independent walks
        # under a fixed seed; the exact value depends on the realization.
        assert head.params["p_value"] > 0.10

    def test_metric_key_names_both_inputs(self):
        rng = np.random.RandomState(13)
        n = 100
        x = rng.randn(n).cumsum()
        y = x + rng.randn(n) * 0.1
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("ust_10y", dates=dates, values=list(y))
        b = _series("ois_10y", dates=dates, values=list(x))
        out = cointegration(a, b)
        assert "ust_10y" in out.metric_key
        assert "ois_10y" in out.metric_key

    def test_beta_intercept_recorded_in_lineage(self):
        """The OLS regression's beta and intercept land in step.params."""
        rng = np.random.RandomState(19)
        n = 150
        x = rng.randn(n).cumsum()
        true_beta = 1.7
        true_intercept = 0.5
        y = true_intercept + true_beta * x + rng.randn(n) * 0.1
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("y", dates=dates, values=list(y))
        b = _series("x", dates=dates, values=list(x))
        out = cointegration(a, b)
        head = out.lineage.steps[-1]
        assert head.params["regression_beta"] == pytest.approx(true_beta, rel=0.05)
        assert head.params["regression_intercept"] == pytest.approx(
            true_intercept, abs=0.5,
        )

    def test_trend_n_skips_intercept(self):
        """trend='n' fits no constant — the recorded intercept is None."""
        rng = np.random.RandomState(23)
        n = 150
        x = rng.randn(n).cumsum()
        y = 1.0 * x + rng.randn(n) * 0.1  # zero true intercept
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("y", dates=dates, values=list(y))
        b = _series("x", dates=dates, values=list(x))
        out = cointegration(a, b, params=CointegrationParams(trend="n"))
        head = out.lineage.steps[-1]
        assert head.params["regression_intercept"] is None
        assert head.params["trend"] == "n"


# ===========================================================================
# 2. OPR8 — config defaults + schema-default mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_resolves_from_config(self):
        rng = np.random.RandomState(29)
        n = 150
        x = rng.randn(n).cumsum()
        y = x + rng.randn(n) * 0.1
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("y", dates=dates, values=list(y))
        b = _series("x", dates=dates, values=list(x))

        cfg = load_operator_config(CONFIG_PATH)
        raw_max_lag = cfg.default_value("max_lag")
        raw_autolag = cfg.default_value("autolag")
        explicit = CointegrationParams(
            method=cfg.default_value("method"),
            trend=cfg.default_value("trend"),
            max_lag=(int(raw_max_lag) if raw_max_lag is not None else None),
            autolag=(raw_autolag if raw_autolag is not None else None),
            min_periods=int(cfg.default_value("min_periods")),
        )
        out_a = cointegration(a, b, params=None)
        out_b = cointegration(a, b, params=explicit)
        assert out_a.lineage.head_hash == out_b.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = CointegrationParams()
        assert p.method == cfg.default_value("method")
        assert p.trend == cfg.default_value("trend")
        assert p.max_lag == cfg.default_value("max_lag")
        assert p.autolag == cfg.default_value("autolag")
        assert p.min_periods == int(cfg.default_value("min_periods"))
        assert p.require_matching_frequency is True
        assert p.require_matching_missingness is True


# ===========================================================================
# 3. OPR13 — typed refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        rng = np.random.RandomState(31)
        n = 80
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("a", dates=dates, values=list(rng.randn(n).cumsum()))
        with pytest.raises(CointegrationError, match="must be Series"):
            cointegration(a, [1, 2, 3])  # type: ignore[arg-type]

    def test_misaligned_index_raises(self):
        rng = np.random.RandomState(37)
        n = 80
        a = _series("a", dates=pd.bdate_range("2024-01-01", periods=n),
                    values=list(rng.randn(n).cumsum()))
        b = _series("b", dates=pd.bdate_range("2025-01-01", periods=n),
                    values=list(rng.randn(n).cumsum()))
        with pytest.raises(CointegrationError, match="identical DatetimeIndex"):
            cointegration(a, b)

    def test_insufficient_observations_raises(self):
        dates = pd.bdate_range("2024-01-01", periods=20)
        rng = np.random.RandomState(41)
        a = _series("a", dates=dates, values=list(rng.randn(20).cumsum()))
        b = _series("b", dates=dates, values=list(rng.randn(20).cumsum()))
        with pytest.raises(CointegrationError, match="min_periods"):
            cointegration(a, b)  # default min_periods=30

    def test_zero_variance_left_raises(self):
        dates = pd.bdate_range("2024-01-01", periods=100)
        rng = np.random.RandomState(43)
        a = _series("flat", dates=dates, values=[2.5] * 100)
        b = _series("walk", dates=dates, values=list(rng.randn(100).cumsum()))
        with pytest.raises(CointegrationError, match="zero variance"):
            cointegration(a, b)

    def test_perfect_colinear_raises(self):
        """Identical series → statsmodels returns -inf test stat →
        operator refuses with typed error (NEVER emits a non-finite
        ScalarMetric, which the validator rejects)."""
        rng = np.random.RandomState(47)
        n = 100
        x = list(rng.randn(n).cumsum())
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("a", dates=dates, values=x)
        b = _series("b", dates=dates, values=x)  # identical
        with pytest.raises(CointegrationError, match="non-finite"):
            cointegration(a, b)


# ===========================================================================
# 4. OPR11 — strict frequency + missingness; unit-invariance
# ===========================================================================


class TestStructuralMetadata:
    def test_frequency_mismatch_strict_raises(self):
        rng = np.random.RandomState(53)
        n = 100
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("a", dates=dates, values=list(rng.randn(n).cumsum()),
                    frequency="B")
        b = _series("b", dates=dates, values=list(rng.randn(n).cumsum()),
                    frequency="W")
        with pytest.raises(CointegrationError, match="incompatible frequencies"):
            cointegration(a, b)

    def test_missingness_mismatch_strict_raises(self):
        rng = np.random.RandomState(59)
        n = 100
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("a", dates=dates, values=list(rng.randn(n).cumsum()))
        b = _series(
            "b", dates=dates, values=list(rng.randn(n).cumsum()),
            missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(CointegrationError, match="missingness"):
            cointegration(a, b)

    def test_cross_unit_input_is_allowed(self):
        """Cointegration is unit-INVARIANT — the test stat is dimensionless,
        so cross-unit pairs are accepted."""
        rng = np.random.RandomState(61)
        n = 150
        x = rng.randn(n).cumsum()
        y = x + rng.randn(n) * 0.1
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("a", dates=dates, values=list(y), units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=dates, values=list(x), units=TimeSeriesUnits.BPS)
        out = cointegration(a, b)
        assert out.units == TimeSeriesUnits.RATIO
        head = out.lineage.steps[-1]
        assert head.params["left_units"] == "percent"
        assert head.params["right_units"] == "bps"


# ===========================================================================
# 5. OPR10 lineage + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_by_one_step_with_aux(self):
        rng = np.random.RandomState(67)
        n = 100
        x = rng.randn(n).cumsum()
        y = x + rng.randn(n) * 0.1
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("a", dates=dates, values=list(y))
        b = _series("b", dates=dates, values=list(x))
        out = cointegration(a, b)
        assert len(out.lineage.steps) == len(a.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "cointegration"
        assert head.version == "1.0.0"
        # right operand's chain rides in auxiliary_lineages (OPR10).
        assert head.auxiliary_lineages == (b.lineage,)

    def test_rerun_is_deterministic(self):
        rng = np.random.RandomState(71)
        n = 120
        x = rng.randn(n).cumsum()
        y = x + rng.randn(n) * 0.1
        dates = pd.bdate_range("2024-01-01", periods=n)
        a = _series("a", dates=dates, values=list(y))
        b = _series("b", dates=dates, values=list(x))
        out1 = cointegration(a, b)
        out2 = cointegration(a, b)
        assert out1.lineage.head_hash == out2.lineage.head_hash
        assert out1.value == out2.value


# ===========================================================================
# 6. OPR6 — finance-blindness + ScalarMetric finiteness contract
# ===========================================================================


def test_runs_on_synthetic_zscore_pair():
    rng = np.random.RandomState(73)
    n = 150
    x = rng.randn(n).cumsum()
    y = x + rng.randn(n) * 0.2
    dates = pd.bdate_range("2024-01-01", periods=n)
    a = _series("a", dates=dates, values=list(y), units=TimeSeriesUnits.Z_SCORE)
    b = _series("b", dates=dates, values=list(x), units=TimeSeriesUnits.Z_SCORE)
    out = cointegration(a, b)
    assert isinstance(out, ScalarMetric)
    assert np.isfinite(out.value)


def test_scalar_metric_value_is_finite():
    """ART11: every emitted ScalarMetric has a finite value (non-finite
    statistic is a typed refusal upstream)."""
    rng = np.random.RandomState(79)
    n = 100
    x = rng.randn(n).cumsum()
    y = x + rng.randn(n) * 0.1
    dates = pd.bdate_range("2024-01-01", periods=n)
    a = _series("a", dates=dates, values=list(y))
    b = _series("b", dates=dates, values=list(x))
    out = cointegration(a, b)
    assert np.isfinite(out.value)
