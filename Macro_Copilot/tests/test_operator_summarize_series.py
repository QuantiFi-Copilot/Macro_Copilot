"""tests/test_operator_summarize_series.py — Phase 2A operator.

Covers the "compare across regimes" sentinel-aligned summary surface
of the ``regime_conditioned_relationship`` archetype.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from shared.artifacts.lineage import Lineage, OperatorStep, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.series_arithmetic import (
    series_arithmetic,
    SeriesArithmeticParams,
)
from shared.operators.summarize_series import (
    CONFIG_PATH,
    summarize_series,
    SummarizeSeriesError,
    SUMMARY_SENTINEL_DATE,
    SummarizeSeriesParams,
)
from shared.workflow.registry import OPERATOR_REGISTRY


def _primitive_lineage(series_key: str) -> Lineage:
    step = PrimitiveStep.build(
        name="synthetic_primitive",
        version="1.0.0",
        params={"series_key": series_key},
        tool_config_hash="test_config_hash",
        output_field="time_series",
        as_of_date="2025-01-01",
    )
    return Lineage.from_steps([step])


def _make_series(
    *,
    series_key: str,
    values: list,
    start: str = "2025-01-01",
    units: TimeSeriesUnits = TimeSeriesUnits.RATIO,
) -> Series:
    idx = pd.bdate_range(start, periods=len(values))
    payload = pd.Series(values, index=idx, name=series_key, dtype=float)
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency="B",
        missingness_policy=RawNoCleaning(),
        lineage=_primitive_lineage(series_key),
    )


# ===========================================================================
# 1. Bundled config
# ===========================================================================


class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "summarize_series"
        assert cfg.operator.method_family == "aggregation"

    def test_required_defaults_present(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.default_value("statistic") == "mean"
        assert cfg.default_value("dispersion") == "std"


# ===========================================================================
# 2. Statistic computation
# ===========================================================================


class TestStatisticComputation:
    def test_mean_of_arithmetic_series(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0, 5.0])
        out = summarize_series(s)
        assert len(out.payload) == 1
        assert out.payload.iloc[0] == pytest.approx(3.0)

    def test_median_with_outlier(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0, 100.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="median"),
        )
        assert out.payload.iloc[0] == pytest.approx(3.0)

    def test_std_recovers_sample_sd(self):
        s = _make_series(series_key="x", values=[2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="std"),
        )
        # numpy ddof=1 std of {2,4,4,4,5,5,7,9} = 2.138...
        assert out.payload.iloc[0] == pytest.approx(2.13809, abs=1e-4)

    def test_sum(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="sum"),
        )
        assert out.payload.iloc[0] == pytest.approx(6.0)

    def test_count_excludes_nan(self):
        s = _make_series(
            series_key="x",
            values=[1.0, math.nan, 3.0, math.nan, 5.0],
        )
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="count"),
        )
        # count is the n_used after dropna.
        assert out.payload.iloc[0] == 3.0


# ===========================================================================
# 3. Sentinel date contract
# ===========================================================================


class TestSentinelDate:
    def test_payload_index_is_single_sentinel(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        assert len(out.payload.index) == 1
        assert out.payload.index[0] == SUMMARY_SENTINEL_DATE

    def test_two_summaries_share_sentinel_so_subtract_works(self):
        """Load-bearing contract: two per-regime summaries fed into
        series_arithmetic.subtract MUST share an index, otherwise the
        downstream subtract has no overlapping dates."""
        a = _make_series(
            series_key="high_betas",
            values=[1.0, 2.0, 3.0, 4.0, 5.0],
            start="2025-01-01",
        )
        b = _make_series(
            series_key="low_betas",
            # Disjoint dates by construction.
            values=[10.0, 20.0, 30.0],
            start="2026-01-01",
        )
        sum_a = summarize_series(a)
        sum_b = summarize_series(b)
        # Both summaries share the sentinel index → subtract works.
        diff = series_arithmetic(
            sum_a, "subtract", sum_b,
            params=SeriesArithmeticParams(op="subtract"),
        )
        # Output is a single-row series with the difference of means.
        assert len(diff.payload) == 1
        assert diff.payload.iloc[0] == pytest.approx(3.0 - 20.0)


# ===========================================================================
# 4. Dispersion recording
# ===========================================================================


class TestDispersionRecording:
    def test_std_dispersion_recorded_in_lineage(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0, 5.0])
        out = summarize_series(s)
        head = out.lineage.steps[-1]
        assert head.params["dispersion"] == "std"
        # std of {1,2,3,4,5} ddof=1 = 1.5811...
        assert head.params["dispersion_value"] == pytest.approx(
            1.58114, abs=1e-4,
        )

    def test_mad_dispersion(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0, 100.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(
                statistic="mean", dispersion="mad",
            ),
        )
        head = out.lineage.steps[-1]
        # MAD: median |{1,2,3,4,100} - 3| = median {2,1,0,1,97} = 1
        assert head.params["dispersion_value"] == pytest.approx(1.0)

    def test_none_dispersion_records_None(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(
                statistic="mean", dispersion="none",
            ),
        )
        head = out.lineage.steps[-1]
        assert head.params["dispersion_value"] is None


# ===========================================================================
# 5. Empty / degenerate input refusals
# ===========================================================================


class TestRefusals:
    def test_all_nan_input_raises(self):
        s = _make_series(
            series_key="x",
            values=[math.nan, math.nan, math.nan],
        )
        with pytest.raises(SummarizeSeriesError, match="0 finite"):
            summarize_series(s)

    def test_std_on_single_observation_raises(self):
        s = _make_series(series_key="x", values=[5.0])
        with pytest.raises(SummarizeSeriesError, match=r">=2"):
            summarize_series(
                s, params=SummarizeSeriesParams(statistic="std"),
            )

    def test_mean_on_single_observation_works(self):
        """Central tendency is well-defined on n=1; dispersion is
        undefined and recorded as None (OPR10)."""
        s = _make_series(series_key="x", values=[5.0])
        out = summarize_series(s)
        assert out.payload.iloc[0] == pytest.approx(5.0)
        head = out.lineage.steps[-1]
        # n<2 ⇒ dispersion undefined → recorded as None in lineage
        # (OPR10: NaN/Inf cannot enter lineage params — there is no
        # canonical-JSON form; None is the sentinel.  Recording NaN here
        # was the default-path crash the audit flagged).
        assert head.params["dispersion_value"] is None


# ===========================================================================
# 6. Metadata propagation
# ===========================================================================


class TestMetadataPropagation:
    def test_units_inherited(self):
        s = _make_series(
            series_key="x",
            values=[1.0, 2.0, 3.0],
            units=TimeSeriesUnits.BPS,
        )
        out = summarize_series(s)
        assert out.units == TimeSeriesUnits.BPS

    def test_frequency_resets_to_None(self):
        """A 1-row sentinel summary has no meaningful business-day
        cadence — frequency must be None."""
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        assert out.frequency is None

    def test_missingness_policy_propagates(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        assert out.missingness_policy == s.missingness_policy

    def test_series_key_propagates(self):
        s = _make_series(series_key="my_series", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        assert out.series_key == "my_series"


# ===========================================================================
# 7. Lineage propagation
# ===========================================================================


class TestLineagePropagation:
    def test_lineage_head_is_summarize_step(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        head = out.lineage.steps[-1]
        assert isinstance(head, OperatorStep)
        assert head.name == "summarize_series"

    def test_n_observations_recorded(self):
        s = _make_series(
            series_key="x",
            values=[1.0, math.nan, 3.0, 4.0, math.nan],
        )
        out = summarize_series(s)
        head = out.lineage.steps[-1]
        assert head.params["n_observations"] == 3
        assert head.params["n_dropped"] == 2

    def test_central_value_in_lineage_matches_payload_value(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        head = out.lineage.steps[-1]
        assert head.params["central_value"] == pytest.approx(
            float(out.payload.iloc[0]),
        )

    def test_upstream_chain_preserved(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        names = [step.name for step in out.lineage.steps]
        assert "synthetic_primitive" in names
        assert "summarize_series" in names


# ===========================================================================
# 8. Config-discipline
# ===========================================================================


class TestConfigDiscipline:
    def test_wrong_config_name_raises(self):
        from shared.operators.align_series import (
            CONFIG_PATH as OTHER_CONFIG_PATH,
        )
        align_cfg = load_operator_config(OTHER_CONFIG_PATH)
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        with pytest.raises(OperatorConfigError, match="config name mismatch"):
            summarize_series(s, config=align_cfg)


# ===========================================================================
# 9. Registry entry
# ===========================================================================


class TestRegistryEntry:
    def test_registered(self):
        assert "summarize_series" in OPERATOR_REGISTRY

    def test_input_slots(self):
        spec = OPERATOR_REGISTRY["summarize_series"]
        assert spec.input_slots == {"series": "Series"}

    def test_output_type(self):
        spec = OPERATOR_REGISTRY["summarize_series"]
        assert spec.output_type == "Series"

    def test_callable_resolves(self):
        spec = OPERATOR_REGISTRY["summarize_series"]
        assert spec.callable is summarize_series

    def test_params_class_resolves(self):
        spec = OPERATOR_REGISTRY["summarize_series"]
        assert spec.params_class is SummarizeSeriesParams
