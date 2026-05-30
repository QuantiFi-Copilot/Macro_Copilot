"""Tests for shared.operators.correlation — the v2.0 reference operator.

Covers the contract surface every standard operator must satisfy:

  - happy path + every implemented method variant (pearson / spearman)
  - OPR2 constant output type (always ScalarMetric, RATIO units)
  - OPR8 honest refusal for the declared-but-unbuilt method (kendall)
  - OPR8 schema-default mirrors the YAML default (no silent divergence)
  - OPR9 typed I/O (non-Series input refused)
  - OPR10 lineage extension (N -> N+1; one OperatorStep; right chain in
    auxiliary_lineages) + OPR14 rerun determinism (stable head_hash)
  - OPR11 strict-by-default frequency + missingness, with explicit
    opt-in; unit-INVARIANCE (cross-unit correlation is allowed)
  - OPR13 typed CorrelationError on every degenerate input
    (misaligned index, insufficient overlap, zero variance)
  - OPR6 finance-blindness (runs on non-rates Z_SCORE / synthetic data)
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
    ScalarMetric,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.correlation import (
    CONFIG_PATH,
    CorrelationParams,
    correlation,
)
from shared.operators.correlation.operator import CorrelationError


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
    """Build a Series artifact with a synthesised fetch+adapter lineage
    (same pattern as the rest of the operator test-suite)."""
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


_DATES = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


# ===========================================================================
# 1. Happy path + variants + constant output type
# ===========================================================================


class TestHappyPath:
    def test_perfect_positive_correlation_is_one(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("b", dates=_DATES, values=[10.0, 20.0, 30.0, 40.0, 50.0])
        out = correlation(a, b)
        assert isinstance(out, ScalarMetric)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.RATIO
        assert out.value == pytest.approx(1.0)

    def test_perfect_negative_correlation_is_minus_one(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("b", dates=_DATES, values=[50.0, 40.0, 30.0, 20.0, 10.0])
        out = correlation(a, b)
        assert out.value == pytest.approx(-1.0)

    def test_pearson_matches_pandas(self):
        av = [1.0, 3.0, 2.0, 5.0, 4.0]
        bv = [2.0, 1.0, 4.0, 3.0, 6.0]
        a = _series("a", dates=_DATES, values=av)
        b = _series("b", dates=_DATES, values=bv)
        expected = pd.Series(av).corr(pd.Series(bv), method="pearson")
        out = correlation(a, b)
        assert out.value == pytest.approx(float(expected))

    def test_spearman_variant(self):
        av = [1.0, 3.0, 2.0, 5.0, 4.0]
        bv = [2.0, 1.0, 4.0, 3.0, 6.0]
        a = _series("a", dates=_DATES, values=av)
        b = _series("b", dates=_DATES, values=bv)
        expected = pd.Series(av).corr(pd.Series(bv), method="spearman")
        out = correlation(a, b, params=CorrelationParams(method="spearman"))
        assert out.value == pytest.approx(float(expected))

    def test_metric_key_names_both_inputs(self):
        a = _series("ust_10y", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("bund_10y", dates=_DATES, values=[2.0, 4.0, 6.0, 8.0, 10.0])
        out = correlation(a, b)
        assert "ust_10y" in out.metric_key and "bund_10y" in out.metric_key


# ===========================================================================
# 2. OPR8 — config defaults, schema-default mirror, honest refusal
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_resolves_from_config(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("b", dates=_DATES, values=[5.0, 4.0, 3.0, 2.0, 1.0])
        # No params -> defaults come from config.yaml (method=pearson).
        out = correlation(a, b, params=None)
        assert out.value == pytest.approx(-1.0)

    def test_schema_defaults_mirror_yaml_defaults(self):
        """OPR8: a param is schema-default OR YAML-authoritative, never
        silently divergent.  The schema defaults exist for ergonomic
        partial overrides; this test pins that they equal the YAML."""
        cfg = load_operator_config(CONFIG_PATH)
        p = CorrelationParams()
        assert p.method == cfg.default_value("method")
        assert p.min_periods == int(cfg.default_value("min_periods"))

    def test_unbuilt_method_refuses_cleanly(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("b", dates=_DATES, values=[2.0, 4.0, 6.0, 8.0, 10.0])
        with pytest.raises(NotImplementedError, match="kendall"):
            correlation(a, b, params=CorrelationParams(method="kendall"))


# ===========================================================================
# 3. OPR13 — typed refusals on degenerate inputs
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        with pytest.raises(CorrelationError, match="must be Series"):
            correlation(a, [1, 2, 3])  # type: ignore[arg-type]

    def test_misaligned_index_raises(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series(
            "b",
            dates=["2026-02-02", "2026-02-05", "2026-02-06", "2026-02-07", "2026-02-08"],
            values=[10.0, 20.0, 30.0, 40.0, 50.0],
        )
        with pytest.raises(CorrelationError, match="identical DatetimeIndex"):
            correlation(a, b)

    def test_insufficient_overlap_raises(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        b = _series("b", dates=["2026-01-02"], values=[2.0])
        with pytest.raises(CorrelationError, match="min_periods"):
            correlation(a, b)

    def test_zero_variance_raises(self):
        a = _series("a", dates=_DATES, values=[3.0, 3.0, 3.0, 3.0, 3.0])
        b = _series("b", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        with pytest.raises(CorrelationError, match="zero variance"):
            correlation(a, b)


# ===========================================================================
# 4. OPR11 — frequency / missingness strict-by-default; unit invariance
# ===========================================================================


class TestStructuralMetadata:
    def test_frequency_mismatch_strict_raises(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0], frequency="B")
        b = _series("b", dates=_DATES, values=[5.0, 4.0, 3.0, 2.0, 1.0], frequency="W")
        with pytest.raises(CorrelationError, match="incompatible frequencies"):
            correlation(a, b)

    def test_frequency_mismatch_opt_in_succeeds(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0], frequency="B")
        b = _series("b", dates=_DATES, values=[5.0, 4.0, 3.0, 2.0, 1.0], frequency="W")
        out = correlation(a, b, params=CorrelationParams(require_matching_frequency=False))
        assert out.value == pytest.approx(-1.0)

    def test_missingness_mismatch_strict_raises(self):
        from shared.artifacts import RawNoCleaning
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series(
            "b", dates=_DATES, values=[5.0, 4.0, 3.0, 2.0, 1.0],
            missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(CorrelationError, match="missingness"):
            correlation(a, b)

    def test_cross_unit_correlation_is_allowed(self):
        """A correlation is dimensionless: correlating BPS vs PERCENT is
        meaningful and must NOT raise a unit error (OPR11 — refuse rule
        does not apply to unit-invariant operators)."""
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0],
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=_DATES, values=[10.0, 20.0, 30.0, 40.0, 50.0],
                    units=TimeSeriesUnits.BPS)
        out = correlation(a, b)
        assert out.units == TimeSeriesUnits.RATIO
        assert out.value == pytest.approx(1.0)


# ===========================================================================
# 5. OPR10 lineage + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_by_one_step(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("b", dates=_DATES, values=[2.0, 4.0, 6.0, 8.0, 10.0])
        out = correlation(a, b)
        assert len(out.lineage.steps) == len(a.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "correlation"
        assert head.version == "1.0.0"
        # right operand's chain rides in auxiliary_lineages (OPR10).
        assert head.auxiliary_lineages == (b.lineage,)
        # both inputs' units recorded for provenance.
        assert head.params["left_units"] == "percent"
        assert head.params["right_units"] == "percent"

    def test_rerun_is_deterministic(self):
        a = _series("a", dates=_DATES, values=[1.0, 3.0, 2.0, 5.0, 4.0])
        b = _series("b", dates=_DATES, values=[2.0, 1.0, 4.0, 3.0, 6.0])
        out1 = correlation(a, b)
        out2 = correlation(a, b)
        assert out1.lineage.head_hash == out2.lineage.head_hash
        assert out1.value == out2.value


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_zscore_data():
    rng = np.random.RandomState(0)
    x = rng.randn(40).cumsum()
    y = x * 0.7 + rng.randn(40) * 0.1
    dates = pd.bdate_range("2026-01-01", periods=40)
    a = _series("synthetic_x", dates=dates, values=x, units=TimeSeriesUnits.Z_SCORE)
    b = _series("synthetic_y", dates=dates, values=y, units=TimeSeriesUnits.Z_SCORE)
    out = correlation(a, b)
    assert isinstance(out, ScalarMetric)
    assert -1.0 <= out.value <= 1.0
    # strongly positively related by construction
    assert out.value > 0.9


def test_scalar_metric_rejects_non_finite_value():
    """ART11: ScalarMetric.value must be finite."""
    a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
    out = correlation(a, _series("b", dates=_DATES, values=[2.0, 4.0, 6.0, 8.0, 10.0]))
    with pytest.raises(ValueError, match="must be finite"):
        ScalarMetric(
            metric_key="bad", value=float("inf"),
            units=TimeSeriesUnits.RATIO, lineage=out.lineage,
        )
