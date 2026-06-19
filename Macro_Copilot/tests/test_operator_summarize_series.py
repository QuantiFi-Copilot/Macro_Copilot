"""tests/test_operator_summarize_series.py — Phase 2A operator.

``summarize_series`` collapses a typed Series to ONE scalar summary
statistic (mean / median / std / sum / count / last / first), emitted
as a ``ScalarMetric``.  This is the canonical "what is the average /
std / current value of X" terminal.

Migration note: this operator previously emitted a single-row ``Series``
at the fake sentinel date ``1900-01-01`` (for the now-paused
``regime_conditioned_relationship`` template's series_arithmetic.subtract
wiring).  It now emits a real ``ScalarMetric`` — the sentinel-Series and
the cross-regime-subtract contract are gone.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from shared.artifacts.lineage import Lineage, OperatorStep, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import ScalarMetric, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.summarize_series import (
    CONFIG_PATH,
    summarize_series,
    SummarizeSeriesError,
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
# 2. Statistic computation — emits a ScalarMetric
# ===========================================================================


class TestStatisticComputation:
    def test_output_is_scalar_metric(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0, 5.0])
        out = summarize_series(s)
        assert isinstance(out, ScalarMetric)
        assert out.metric_key == "mean"

    def test_mean_of_arithmetic_series(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0, 5.0])
        out = summarize_series(s)
        assert out.value == pytest.approx(3.0)

    def test_median_with_outlier(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0, 100.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="median"),
        )
        assert out.metric_key == "median"
        assert out.value == pytest.approx(3.0)

    def test_std_recovers_sample_sd(self):
        s = _make_series(series_key="x", values=[2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="std"),
        )
        # numpy ddof=1 std of {2,4,4,4,5,5,7,9} = 2.138...
        assert out.value == pytest.approx(2.13809, abs=1e-4)

    def test_sum(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="sum"),
        )
        assert out.value == pytest.approx(6.0)

    def test_count_excludes_nan(self):
        s = _make_series(
            series_key="x",
            values=[1.0, math.nan, 3.0, math.nan, 5.0],
        )
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="count"),
        )
        # count is the n_used after dropna.
        assert out.value == 3.0

    def test_count_on_all_nan_is_zero(self):
        """FM-6 count-of-zero: an all-NaN input is a legitimate count
        of 0 — the ONE statistic that bypasses the
        zero-finite-observations refusal."""
        s = _make_series(
            series_key="x",
            values=[math.nan, math.nan, math.nan],
        )
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="count"),
        )
        assert isinstance(out, ScalarMetric)
        assert out.metric_key == "count"
        assert out.value == 0.0
        # u06: a count is dimensionless (COUNT), not the input's units.
        from shared.artifacts.units import TimeSeriesUnits
        assert out.units == TimeSeriesUnits.COUNT
        # Normal lineage discipline — same fields as any other run.
        head = out.lineage.steps[-1]
        assert head.params["central_value"] == 0.0
        assert head.params["n_observations"] == 0
        assert head.params["n_dropped"] == 3
        assert head.params["dispersion_value"] is None

    def test_count_on_empty_payload_is_zero(self):
        """FM-6: count on a typed EMPTY Series (the apply_mask
        zero-match output shape) returns 0.0."""
        s = _make_series(series_key="x", values=[])
        assert len(s.payload) == 0
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="count"),
        )
        assert out.value == 0.0
        head = out.lineage.steps[-1]
        assert head.params["n_observations"] == 0
        assert head.params["n_dropped"] == 0

    def test_last_returns_latest_finite_value(self):
        """statistic='last' = the 'current value' of the series — the
        latest finite observation (NaN tail dropped)."""
        s = _make_series(
            series_key="x",
            values=[1.0, 2.0, 3.0, 4.0, math.nan],
        )
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="last"),
        )
        assert out.metric_key == "last"
        assert out.value == pytest.approx(4.0)

    def test_first_returns_earliest_finite_value(self):
        s = _make_series(
            series_key="x",
            values=[math.nan, 2.0, 3.0, 4.0, 5.0],
        )
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="first"),
        )
        assert out.metric_key == "first"
        assert out.value == pytest.approx(2.0)


# ===========================================================================
# 3. Dispersion recording (in lineage)
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
# 4. Empty / degenerate input refusals
# ===========================================================================


class TestRefusals:
    def test_all_nan_input_raises(self):
        s = _make_series(
            series_key="x",
            values=[math.nan, math.nan, math.nan],
        )
        with pytest.raises(SummarizeSeriesError, match="0 finite"):
            summarize_series(s)

    def test_non_count_statistics_on_all_nan_still_raise(self):
        """FM-6 regression guard: the count-of-zero special case must
        NOT relax the zero-finite-observations refusal for any other
        statistic."""
        s = _make_series(
            series_key="x",
            values=[math.nan, math.nan, math.nan],
        )
        for stat in ("mean", "median", "std", "sum", "last", "first",
                     "quantile"):
            with pytest.raises(SummarizeSeriesError, match="0 finite"):
                summarize_series(
                    s, params=SummarizeSeriesParams(statistic=stat),
                )

    def test_count_emits_count_units_not_input_units(self):
        """u06: a count must be dimensionless (COUNT), not the input
        series' units — otherwise the L6 layer mis-narrates a count of
        days as a percentage."""
        from shared.artifacts.units import TimeSeriesUnits
        # A percent-denominated input (e.g. a yield level).
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="count"),
        )
        assert out.metric_key == "count"
        assert out.value == 3.0
        assert out.units == TimeSeriesUnits.COUNT
        # A non-count statistic still inherits the input's units.
        mean_out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="mean"),
        )
        assert mean_out.units == s.units

    def test_empty_input_raises_with_units_hint(self):
        """R3 diagnostics (I2): a non-count statistic on an EMPTY input
        (the apply_mask zero-match shape) raises with the bps/percent
        units hint AND preserves the '0 finite observations' substring
        the pipeline recompose remediation keys on."""
        s = _make_series(series_key="x", values=[])
        for stat in ("mean", "median", "last", "sum"):
            with pytest.raises(SummarizeSeriesError) as ei:
                summarize_series(
                    s, params=SummarizeSeriesParams(statistic=stat),
                )
            msg = str(ei.value)
            assert "0 finite observations" in msg   # recompose trigger
            assert "EMPTY" in msg
            assert "convert_units" in msg and "bps" in msg

    def test_all_nan_nonempty_keeps_generic_message(self):
        """An all-NaN-but-non-empty input is a data-gap case, NOT a
        zero-match — it must NOT claim the series is empty."""
        s = _make_series(series_key="x", values=[math.nan, math.nan])
        with pytest.raises(SummarizeSeriesError) as ei:
            summarize_series(s)
        msg = str(ei.value)
        assert "0 finite observations" in msg
        assert "EMPTY" not in msg

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
        assert out.value == pytest.approx(5.0)
        head = out.lineage.steps[-1]
        # n<2 ⇒ dispersion undefined → recorded as None in lineage
        # (OPR10: NaN/Inf cannot enter lineage params — there is no
        # canonical-JSON form; None is the sentinel.  Recording NaN here
        # was the default-path crash the audit flagged).
        assert head.params["dispersion_value"] is None


# ===========================================================================
# 5. Metadata propagation
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

    def test_metric_key_is_statistic_name(self):
        s = _make_series(series_key="my_series", values=[1.0, 2.0, 3.0])
        out = summarize_series(
            s, params=SummarizeSeriesParams(statistic="sum"),
        )
        assert out.metric_key == "sum"


# ===========================================================================
# 6. Lineage propagation
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

    def test_central_value_in_lineage_matches_scalar_value(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        head = out.lineage.steps[-1]
        assert head.params["central_value"] == pytest.approx(out.value)

    def test_upstream_chain_preserved(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        names = [step.name for step in out.lineage.steps]
        assert "synthetic_primitive" in names
        assert "summarize_series" in names

    @pytest.mark.parametrize("params", [
        SummarizeSeriesParams(statistic="mean"),
        SummarizeSeriesParams(statistic="quantile", q=0.05),
    ])
    def test_rerun_is_deterministic(self, params):
        """OPR14(a) / OPR16.2: ``op(x) == op(x)`` produces an identical
        head_hash — the mandatory-per-operator rerun-equality assert
        (the two siblings rolling_statistic / correlation ship this; it
        was the one gap m10 names).  Covers a unit-preserving statistic
        (mean) and the q-bearing quantile (whose metric_key folds q)."""
        s = _make_series(
            series_key="x",
            values=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
        )
        out1 = summarize_series(s, params=params)
        out2 = summarize_series(s, params=params)
        assert out1.lineage.head_hash == out2.lineage.head_hash


# ===========================================================================
# 7. Config-discipline
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
# 8. Registry entry
# ===========================================================================


class TestRegistryEntry:
    def test_registered(self):
        assert "summarize_series" in OPERATOR_REGISTRY

    def test_input_slots(self):
        # PART B refactor: input_slots values are SlotDescriptor instances.
        spec = OPERATOR_REGISTRY["summarize_series"]
        assert set(spec.input_slots) == {"series"}
        assert spec.input_slots["series"].artifact_type == "Series"

    def test_output_type(self):
        # PART B refactor: output is an OutputDescriptor.  Migrated from
        # Series (sentinel-date hack) to ScalarMetric.
        spec = OPERATOR_REGISTRY["summarize_series"]
        assert spec.output.artifact_type == "ScalarMetric"

    def test_callable_resolves(self):
        spec = OPERATOR_REGISTRY["summarize_series"]
        assert spec.callable is summarize_series

    def test_params_class_resolves(self):
        spec = OPERATOR_REGISTRY["summarize_series"]
        assert spec.params_class is SummarizeSeriesParams


# ===========================================================================
# v1.1.0 — statistic='quantile' (Track-A extension per the OPR4
# extend-don't-add ruling; the rolling_statistic 1.1.0 precedent).
# ===========================================================================


class TestQuantile:
    def test_quantile_matches_pandas_in_input_units(self):
        vals = [1.0, 2.0, 3.0, 4.0, 100.0]
        s = _make_series(series_key="x", values=vals)
        out = summarize_series(
            s,
            SummarizeSeriesParams(statistic="quantile", q=0.05),
        )
        assert out.value == pytest.approx(
            float(pd.Series(vals).quantile(0.05)),
        )
        # PASSTHROUGH units: the quantile of a unit-bearing series is
        # a value in that unit, never a ratio.
        assert out.units == s.units
        assert out.metric_key == "quantile_0.05"

    def test_q_default_is_median_equivalent(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        s = _make_series(series_key="x", values=vals)
        out_q = summarize_series(
            s, SummarizeSeriesParams(statistic="quantile"),
        )
        out_med = summarize_series(
            s, SummarizeSeriesParams(statistic="median"),
        )
        assert out_q.value == pytest.approx(out_med.value)
        assert out_q.metric_key == "quantile_0.5"

    def test_nan_dropped_before_quantile(self):
        vals = [1.0, float("nan"), 3.0, float("nan"), 5.0]
        s = _make_series(series_key="x", values=vals)
        out = summarize_series(
            s, SummarizeSeriesParams(statistic="quantile", q=0.5),
        )
        assert out.value == pytest.approx(3.0)

    def test_q_bounds_enforced_at_schema(self):
        with pytest.raises(ValueError):
            SummarizeSeriesParams(statistic="quantile", q=0.0)
        with pytest.raises(ValueError):
            SummarizeSeriesParams(statistic="quantile", q=1.0)

    def test_q_nulled_in_lineage_for_other_statistics(self):
        """OPR14: q is consumed only by statistic='quantile' — two mean
        calls differing only in q must hash identically."""
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        h1 = summarize_series(
            s, SummarizeSeriesParams(statistic="mean", q=0.1),
        ).lineage.head_hash
        h2 = summarize_series(
            s, SummarizeSeriesParams(statistic="mean", q=0.9),
        ).lineage.head_hash
        assert h1 == h2

    def test_q_changes_identity_for_quantile(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0])
        h1 = summarize_series(
            s, SummarizeSeriesParams(statistic="quantile", q=0.1),
        ).lineage.head_hash
        h2 = summarize_series(
            s, SummarizeSeriesParams(statistic="quantile", q=0.9),
        ).lineage.head_hash
        assert h1 != h2

    def test_ddof_nulled_when_no_std_in_play(self):
        """The 1.1.0 OPR14 alignment: ddof is nulled unless statistic
        or dispersion is 'std'."""
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0])
        h1 = summarize_series(
            s,
            SummarizeSeriesParams(
                statistic="mean", dispersion="none", ddof=1,
            ),
        ).lineage.head_hash
        h0 = summarize_series(
            s,
            SummarizeSeriesParams(
                statistic="mean", dispersion="none", ddof=0,
            ),
        ).lineage.head_hash
        assert h1 == h0

    def test_ddof_kept_when_dispersion_is_std(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0, 4.0])
        h1 = summarize_series(
            s,
            SummarizeSeriesParams(
                statistic="mean", dispersion="std", ddof=1,
            ),
        ).lineage.head_hash
        h0 = summarize_series(
            s,
            SummarizeSeriesParams(
                statistic="mean", dispersion="std", ddof=0,
            ),
        ).lineage.head_hash
        assert h1 != h0

    def test_version_bumped_in_lineage(self):
        s = _make_series(series_key="x", values=[1.0, 2.0, 3.0])
        out = summarize_series(s)
        assert out.lineage.steps[-1].version == "1.1.0"

    def test_yaml_q_default_pinned(self):
        from shared.operators.summarize_series import CONFIG_PATH
        cfg = load_operator_config(CONFIG_PATH)
        assert float(cfg.default_value("q")) == 0.5
        assert SummarizeSeriesParams().q == 0.5

    def test_quantile_works_at_n1(self):
        """An order statistic is defined on a single observation."""
        s = _make_series(series_key="x", values=[7.0])
        out = summarize_series(
            s, SummarizeSeriesParams(statistic="quantile", q=0.25),
        )
        assert out.value == pytest.approx(7.0)
