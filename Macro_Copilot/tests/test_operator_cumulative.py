"""Tests for shared.operators.cumulative — Track-A A1.

Covers the contract surface every standard operator must satisfy:

  - parity vs pandas cumsum/cummax/cummin for every statistic
  - NaN semantics: NaN positions stay NaN, accumulation continues
    over non-NaN values (pandas skipna pinned)
  - diff→cumsum round trip (rebuilt path = level − first level)
  - units/frequency/missingness passthrough
  - overflow (running sum of extremes) → typed refusal; all-NaN
    refusal; non-Series refusal
  - OPR8 params=None + schema-mirrors-YAML; OPR14 determinism +
    variant-changes-identity
  - OPR6 non-rates case; composition diff→cumulative DAG
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
from shared.operators.cumulative import (
    CONFIG_PATH,
    CumulativeError,
    CumulativeParams,
    cumulative,
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


_N = 25
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _input(seed: int = 47):
    rng = np.random.RandomState(seed)
    vals = rng.randn(_N) * 2
    return _series("x", dates=_DATES, values=vals), vals


# ===========================================================================
# 1. Parity + NaN semantics
# ===========================================================================


class TestHappyPath:
    @pytest.mark.parametrize(
        "statistic,fn",
        [("sum", "cumsum"), ("max", "cummax"), ("min", "cummin")],
    )
    def test_matches_pandas(self, statistic, fn):
        s, vals = _input()
        out = cumulative(s, params=CumulativeParams(statistic=statistic))
        expected = getattr(pd.Series(vals, index=_DATES), fn)()
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.BPS  # passthrough

    def test_nan_positions_stay_nan_accumulation_continues(self):
        vals = [1.0, 2.0, np.nan, 4.0, 5.0]
        dates = _DATES[:5]
        s = _series("g", dates=dates, values=vals)
        out = cumulative(s)
        assert out.payload.iloc[0] == pytest.approx(1.0)
        assert out.payload.iloc[1] == pytest.approx(3.0)
        assert np.isnan(out.payload.iloc[2])  # NaN stays NaN
        assert out.payload.iloc[3] == pytest.approx(7.0)  # skipna continues
        assert out.payload.iloc[4] == pytest.approx(12.0)

    def test_diff_then_cumsum_rebuilds_path(self):
        rng = np.random.RandomState(3)
        levels = rng.randn(_N).cumsum() + 100.0
        s = _series("lvl", dates=_DATES, values=levels)
        diffed = pd.Series(levels, index=_DATES).diff()
        s_d = _series("chg", dates=_DATES, values=diffed.to_numpy())
        out = cumulative(s_d)
        np.testing.assert_allclose(
            out.payload.iloc[1:].to_numpy(),
            (levels - levels[0])[1:],
        )

    def test_metadata_passthrough_and_key(self):
        s, _ = _input()
        s2 = _series("y", dates=_DATES, values=s.payload.to_numpy(),
                     frequency="B")
        out = cumulative(s2, params=CumulativeParams(statistic="max"))
        assert out.frequency == "B"
        assert out.missingness_policy == s2.missingness_policy
        assert out.series_key == "cummax__y"


# ===========================================================================
# 2. OPR13 refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        with pytest.raises(CumulativeError, match="must be a Series"):
            cumulative({"a": 1})  # type: ignore[arg-type]

    def test_overflow_running_sum_raises_typed_error(self):
        vals = np.full(_N, 1e308 / 4)
        s = _series("big", dates=_DATES, values=vals)
        with pytest.raises(CumulativeError, match="overflow"):
            cumulative(s)

    def test_all_nan_output_refused(self):
        s = _series("nn", dates=_DATES, values=[np.nan] * _N)
        with pytest.raises(CumulativeError, match="all-NaN"):
            cumulative(s)


# ===========================================================================
# 3. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        s, _ = _input()
        out_default = cumulative(s)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = CumulativeParams(
            statistic=cfg.default_value("statistic"),
        )
        out_explicit = cumulative(s, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = CumulativeParams()
        assert p.statistic == cfg.default_value("statistic")

    def test_product_rejected_at_schema(self):
        """product is deliberately excluded (dimensional honesty)."""
        with pytest.raises(ValueError):
            CumulativeParams(statistic="product")  # type: ignore[arg-type]


# ===========================================================================
# 4. Lineage + determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_by_one_step(self):
        s, _ = _input()
        out = cumulative(s)
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "cumulative"
        assert head.params["statistic"] == "sum"

    def test_rerun_is_deterministic(self):
        s, _ = _input()
        assert (
            cumulative(s).lineage.head_hash
            == cumulative(s).lineage.head_hash
        )

    def test_statistic_changes_identity(self):
        s, _ = _input()
        h_sum = cumulative(
            s, params=CumulativeParams(statistic="sum"),
        ).lineage.head_hash
        h_max = cumulative(
            s, params=CumulativeParams(statistic="max"),
        ).lineage.head_hash
        assert h_sum != h_max


# ===========================================================================
# 5. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(89)
    vals = rng.randn(_N)
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.COUNT)
    out = cumulative(s, params=CumulativeParams(statistic="min"))
    expected = pd.Series(vals, index=_DATES).cummin()
    pd.testing.assert_series_equal(out.payload, expected, check_names=False)
    assert out.units == TimeSeriesUnits.COUNT


# ===========================================================================
# 6. Composition — diff → cumulative DAG (cumulative change since start)
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
            n_rows: int = 30
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
            bdays = pd.bdate_range("2026-04-01", periods=params.n_rows)
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
            workflow_id="cumulative_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="d", operator_name="series_arithmetic",
                    params={"op": "diff"},
                ),
                OperatorNode(node_id="c", operator_name="cumulative"),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="d",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="d", target_node_id="c",
                             target_input_slot="series"),
            ],
            terminal_node_id="c",
        )
        return wf, _resolve

    def test_type_gate_accepts_cumulative_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_cumulative_change_since_start(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.BPS
        n = 30
        vals = pd.Series([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        expected = vals.diff().cumsum()
        np.testing.assert_allclose(
            terminal.payload.to_numpy(), expected.to_numpy(),
        )
        clear_tool_config_cache()
