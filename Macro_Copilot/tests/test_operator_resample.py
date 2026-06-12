"""Tests for shared.operators.resample — Track-A A1.

Covers the contract surface every standard operator must satisfy:

  - parity vs pandas resample(rule, label='right', closed='right')
    for every method and every target frequency
  - the design locks pinned: weekly labels are FRIDAYS; monthly labels
    are month-ends
  - THE FREQUENCY TRANSITION: output.frequency == target (the only
    licensed change of the tag)
  - downsample-only: known-frequency upsampling/identity refused; the
    row-count fabrication guard for unknown input frequencies
  - partial-period honesty: the final incomplete bucket is INCLUDED
    and final_period_complete=False rides in lineage; complete-ending
    series flag True
  - empty interior buckets emit NaN (mean) — legitimate missingness
  - units/missingness passthrough; all-NaN refusal
  - OPR8 params=None + schema-mirrors-YAML; OPR14 determinism +
    variant-changes-identity; OPR6 non-rates case
  - composition resample→align→spread DAG at the new frequency
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

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
from shared.operators.resample import (
    CONFIG_PATH,
    ResampleError,
    ResampleParams,
    resample,
)


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
    units: TimeSeriesUnits = TimeSeriesUnits.BPS,
    frequency=None,
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
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


# 2026-01-02 is a Friday; 13 full weeks of business days.
_N = 65
_DATES = pd.bdate_range("2026-01-05", periods=_N)  # Monday start


def _input(seed: int = 53, frequency="B"):
    rng = np.random.RandomState(seed)
    vals = rng.randn(_N).cumsum() + 50.0
    return (
        _series("x", dates=_DATES, values=vals, frequency=frequency),
        vals,
    )


_RULES = {"W": "W-FRI", "M": "ME", "Q": "QE", "Y": "YE"}


# ===========================================================================
# 1. Parity + design locks
# ===========================================================================


class TestHappyPath:
    @pytest.mark.parametrize(
        "method", ["last", "mean", "first", "max", "min"],
    )
    def test_weekly_matches_pandas(self, method):
        s, vals = _input()
        out = resample(s, params=ResampleParams(method=method))
        ref = pd.Series(vals, index=_DATES)
        expected = getattr(
            ref.resample("W-FRI", label="right", closed="right"), method,
        )().astype(float)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False, check_freq=False,
        )
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.BPS  # passthrough

    @pytest.mark.parametrize("target", ["W", "M", "Q", "Y"])
    def test_targets_match_pandas(self, target):
        s, vals = _input()
        out = resample(
            s, params=ResampleParams(target_frequency=target),
        )
        ref = pd.Series(vals, index=_DATES)
        expected = ref.resample(
            _RULES[target], label="right", closed="right",
        ).last().astype(float)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False, check_freq=False,
        )

    def test_yearly_label_is_year_end(self):
        s, _ = _input()
        out = resample(s, params=ResampleParams(target_frequency="Y"))
        assert len(out.payload) == 1
        assert out.payload.index[-1] == pd.Timestamp("2026-12-31")
        assert out.frequency == "Y"

    def test_weekly_labels_are_fridays(self):
        s, _ = _input()
        out = resample(s)
        assert all(d.day_name() == "Friday" for d in out.payload.index)

    def test_monthly_labels_are_month_ends(self):
        s, _ = _input()
        out = resample(s, params=ResampleParams(target_frequency="M"))
        assert all(d.is_month_end for d in out.payload.index)

    def test_frequency_tag_stamped_to_target(self):
        """THE licensed frequency transition."""
        s, _ = _input(frequency="B")
        out = resample(s, params=ResampleParams(target_frequency="M"))
        assert out.frequency == "M"
        out_w = resample(s)
        assert out_w.frequency == "W"

    def test_metadata_passthrough_and_key(self):
        s, _ = _input()
        out = resample(s)
        assert out.missingness_policy == s.missingness_policy
        assert out.series_key == "resampled_W__x"


# ===========================================================================
# 2. Partial-period honesty + empty buckets
# ===========================================================================


class TestPartialAndEmpty:
    def test_final_partial_week_included_and_disclosed(self):
        # End on a Wednesday: the last W-FRI bucket is incomplete.
        dates = pd.bdate_range("2026-01-05", "2026-01-21")  # Mon..Wed
        vals = np.arange(len(dates), dtype=float)
        s = _series("p", dates=dates, values=vals, frequency="B")
        out = resample(s, params=ResampleParams(method="last"))
        # Bucket exists, labelled Friday 2026-01-23, holding Wednesday's
        # value.
        assert out.payload.index[-1] == pd.Timestamp("2026-01-23")
        assert out.payload.iloc[-1] == vals[-1]
        assert (
            out.lineage.steps[-1].params["final_period_complete"] is False
        )

    def test_complete_final_week_flagged_true(self):
        dates = pd.bdate_range("2026-01-05", "2026-01-16")  # ends Friday
        vals = np.arange(len(dates), dtype=float)
        s = _series("c", dates=dates, values=vals, frequency="B")
        out = resample(s)
        assert (
            out.lineage.steps[-1].params["final_period_complete"] is True
        )

    def test_complete_month_with_weekend_calendar_end_flags_true(self):
        """Critic finding: May 2026 ends on a Sunday; a B series ending
        Friday 2026-05-29 (the last business day) IS complete — the
        frequency-aware basis must flag True, not compare against the
        calendar label."""
        dates = pd.bdate_range("2026-05-01", "2026-05-29")
        vals = np.arange(len(dates), dtype=float)
        s = _series("mb", dates=dates, values=vals, frequency="B")
        out = resample(s, params=ResampleParams(target_frequency="M"))
        head = out.lineage.steps[-1]
        assert head.params["final_period_complete"] is True
        assert head.params["completeness_basis"] == "expected_b_observation"

    def test_mid_month_b_series_flags_false(self):
        dates = pd.bdate_range("2026-05-01", "2026-05-15")
        vals = np.arange(len(dates), dtype=float)
        s = _series("mm", dates=dates, values=vals, frequency="B")
        out = resample(s, params=ResampleParams(target_frequency="M"))
        assert (
            out.lineage.steps[-1].params["final_period_complete"] is False
        )

    def test_unknown_frequency_uses_one_sided_basis(self):
        dates = pd.bdate_range("2026-01-05", "2026-01-21")
        vals = np.arange(len(dates), dtype=float)
        s = _series("uf", dates=dates, values=vals, frequency=None)
        out = resample(s)
        head = out.lineage.steps[-1]
        assert head.params["completeness_basis"] == "calendar_label_one_sided"
        assert head.params["final_period_complete"] is False

    def test_empty_interior_bucket_emits_nan_for_mean(self):
        # Two weeks of data with the middle week entirely absent.
        dates = list(pd.bdate_range("2026-01-05", "2026-01-09")) + list(
            pd.bdate_range("2026-01-19", "2026-01-23"),
        )
        vals = np.arange(len(dates), dtype=float)
        s = _series("gap", dates=pd.DatetimeIndex(dates), values=vals)
        out = resample(s, params=ResampleParams(method="mean"))
        # Buckets: Jan 9 (full), Jan 16 (EMPTY → NaN), Jan 23 (full).
        assert np.isnan(out.payload.loc[pd.Timestamp("2026-01-16")])
        assert not np.isnan(out.payload.loc[pd.Timestamp("2026-01-09")])


# ===========================================================================
# 3. OPR13 refusals — downsample-only
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        with pytest.raises(ResampleError, match="must be a Series"):
            resample("nope")  # type: ignore[arg-type]

    def test_known_frequency_upsampling_refused(self):
        # Monthly input → weekly target.
        dates = pd.date_range("2026-01-31", periods=6, freq="ME")
        s = _series("m", dates=dates, values=np.arange(6.0), frequency="M")
        with pytest.raises(ResampleError, match="strictly higher"):
            resample(s, params=ResampleParams(target_frequency="W"))

    def test_identity_resample_refused(self):
        dates = pd.date_range("2026-01-31", periods=6, freq="ME")
        s = _series("m", dates=dates, values=np.arange(6.0), frequency="M")
        with pytest.raises(ResampleError, match="strictly higher"):
            resample(s, params=ResampleParams(target_frequency="M"))

    def test_unknown_frequency_fabrication_guard(self):
        # frequency=None but the index is YEARLY-spaced: resampling to
        # W would CREATE ~52 buckets per row — the row-count guard
        # must refuse.
        dates = pd.DatetimeIndex(
            ["2024-12-31", "2025-12-31", "2026-12-31"],
        )
        s = _series("y", dates=dates, values=[1.0, 2.0, 3.0],
                    frequency=None)
        with pytest.raises(ResampleError, match="more rows"):
            resample(s, params=ResampleParams(target_frequency="W"))

    def test_all_nan_output_refused(self):
        s = _series(
            "nn", dates=_DATES, values=[np.nan] * _N, frequency="B",
        )
        with pytest.raises(ResampleError, match="all-NaN"):
            resample(s)


# ===========================================================================
# 4. OPR8 + determinism
# ===========================================================================


class TestParamsConfigDeterminism:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        s, _ = _input()
        out_default = resample(s)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = ResampleParams(
            target_frequency=cfg.default_value("target_frequency"),
            method=cfg.default_value("method"),
        )
        out_explicit = resample(s, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = ResampleParams()
        assert p.target_frequency == cfg.default_value("target_frequency")
        assert p.method == cfg.default_value("method")

    def test_rerun_is_deterministic(self):
        s, _ = _input()
        assert (
            resample(s).lineage.head_hash == resample(s).lineage.head_hash
        )

    def test_method_changes_identity(self):
        s, _ = _input()
        h_last = resample(
            s, params=ResampleParams(method="last"),
        ).lineage.head_hash
        h_mean = resample(
            s, params=ResampleParams(method="mean"),
        ).lineage.head_hash
        assert h_last != h_mean

    def test_lineage_records_design_locks(self):
        s, _ = _input()
        out = resample(s)
        head = out.lineage.steps[-1]
        assert head.name == "resample"
        assert head.params["pandas_rule"] == "W-FRI"
        assert head.params["label"] == "right"
        assert head.params["closed"] == "right"
        assert head.params["input_frequency"] == "B"


# ===========================================================================
# 5. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(101)
    vals = rng.randn(_N)
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.RATIO, frequency="B")
    out = resample(s, params=ResampleParams(method="mean"))
    ref = pd.Series(vals, index=_DATES)
    expected = ref.resample(
        "W-FRI", label="right", closed="right",
    ).mean().astype(float)
    pd.testing.assert_series_equal(
        out.payload, expected, check_names=False, check_freq=False,
    )
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 6. Composition — weekly closes of two series, spread at the new
#    frequency
# ===========================================================================


class TestComposition:
    @staticmethod
    def _dag_and_resolver(tmp_path):
        from shared.schemas import TimeSeries, TimeSeriesRow
        from shared.workflow import (
            OperatorNode,
            PrimitiveNode,
            PrimitiveSpec,
            Workflow,
            WorkflowEdge,
        )

        class _SynthInput(BaseModel):
            series_name: str = "s"
            n_rows: int = 40
            base_value: float = 100.0
            drift: float = 0.5
            pattern_mult: int = 7919
            pattern_mod: int = 13
            units: str = "bps"

        class _SynthMetrics(BaseModel):
            as_of_date: str

        class _SynthOutput(BaseModel):
            current_metrics: _SynthMetrics
            time_series: TimeSeries

        def _synth(*, engine, params: _SynthInput, config) -> dict:
            bdays = pd.bdate_range("2026-04-06", periods=params.n_rows)
            values = [
                params.base_value
                + i * params.drift
                + ((i * params.pattern_mult) % params.pattern_mod) * 0.25
                for i in range(params.n_rows)
            ]
            rows = [
                TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=v)
                for d, v in zip(bdays, values)
            ]
            return {
                "current_metrics": {"as_of_date": rows[-1].date},
                "time_series": {
                    "series_name": params.series_name,
                    "units": params.units,
                    "description": "synthetic",
                    "rows": [r.model_dump() for r in rows],
                },
            }

        cfg = tmp_path / "synthetic_config.yaml"
        cfg.write_text(
            "tool:\n"
            "  name: synthetic_primitive_tool\n"
            "  domain: synthetic\n"
            "  description: Synthetic primitive for composition tests.\n"
            "  category: desk_invariant_primitive\n"
            "conventions:\n"
            "  ffill_limit_days:\n"
            "    value: 5\n"
            "    source: substrate_test_default\n"
            "    rationale: synthetic config for substrate tests\n"
            "methodology:\n"
            "  what_it_does: deterministic walk for composition tests.\n"
        )
        spec = PrimitiveSpec(
            tool_name="synthetic_primitive_tool",
            callable=_synth,
            input_class=_SynthInput,
            output_class=_SynthOutput,
            config_path=cfg,
        )

        def _resolve(tool_name: str) -> PrimitiveSpec:
            if tool_name != "synthetic_primitive_tool":
                raise KeyError(tool_name)
            return spec

        wf = Workflow(
            workflow_id="resample_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p1", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "u1", "drift": 0.5,
                            "pattern_mult": 7919, "pattern_mod": 13},
                ),
                PrimitiveNode(
                    node_id="p2", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "u2", "drift": -0.2,
                            "pattern_mult": 104729, "pattern_mod": 17},
                ),
                OperatorNode(node_id="r1", operator_name="resample"),
                OperatorNode(node_id="r2", operator_name="resample"),
                OperatorNode(
                    node_id="spread", operator_name="series_arithmetic",
                    params={"op": "subtract"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p1", target_node_id="r1",
                             target_input_slot="series"),
                WorkflowEdge(source_node_id="p2", target_node_id="r2",
                             target_input_slot="series"),
                WorkflowEdge(source_node_id="r1", target_node_id="spread",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="r2", target_node_id="spread",
                             target_input_slot="right"),
            ],
            terminal_node_id="spread",
        )
        return wf, _resolve

    def test_type_gate_accepts_resample_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_weekly_spread(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.BPS
        assert terminal.frequency == "W"  # the transition propagated
        n = 40
        bdays = pd.bdate_range("2026-04-06", periods=n)
        cols = {}
        for name, drift, mult, mod in (
            ("u1", 0.5, 7919, 13),
            ("u2", -0.2, 104729, 17),
        ):
            cols[name] = pd.Series([
                100.0 + i * drift + ((i * mult) % mod) * 0.25
                for i in range(n)
            ], index=bdays)
        w1 = cols["u1"].resample("W-FRI", label="right", closed="right").last()
        w2 = cols["u2"].resample("W-FRI", label="right", closed="right").last()
        expected = w1 - w2
        np.testing.assert_allclose(
            terminal.payload.to_numpy(), expected.to_numpy(),
        )
        clear_tool_config_cache()
