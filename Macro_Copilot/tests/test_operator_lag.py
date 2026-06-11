"""Tests for shared.operators.lag — Track-A A1.

Covers the contract surface every standard operator must satisfy:

  - parity vs pandas shift(k); the sign convention pinned (the value
    from k rows EARLIER appears at each position; first k rows NaN)
  - index identity (row-count shifting on the SAME index)
  - units/frequency/missingness passthrough
  - refusals: non-Series input; periods >= input length (all-NaN)
  - OPR8 params=None + schema-mirrors-YAML; OPR14 determinism +
    periods-changes-identity; negative periods rejected at the schema
  - OPR6 non-rates case; composition momentum DAG
    (lag + original → series_arithmetic(subtract))
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
from shared.operators.lag import CONFIG_PATH, LagError, LagParams, lag


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


_N = 20
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _input(seed: int = 43):
    rng = np.random.RandomState(seed)
    vals = rng.randn(_N).cumsum()
    return _series("x", dates=_DATES, values=vals), vals


# ===========================================================================
# 1. Parity + sign convention
# ===========================================================================


class TestHappyPath:
    @pytest.mark.parametrize("k", [1, 3, 7])
    def test_matches_pandas_shift(self, k):
        s, vals = _input()
        out = lag(s, params=LagParams(periods=k))
        expected = pd.Series(vals, index=_DATES).shift(k)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert isinstance(out, Series)  # OPR2 constant output type

    def test_sign_convention_value_from_k_rows_earlier(self):
        """Pinned: lag(5) places the value from 5 rows EARLIER at the
        current position."""
        s, vals = _input()
        out = lag(s, params=LagParams(periods=5))
        assert out.payload.iloc[10] == pytest.approx(vals[5])
        assert out.payload.iloc[:5].isna().all()
        assert out.payload.iloc[5:].notna().all()

    def test_same_index_no_calendar_reshaping(self):
        s, _ = _input()
        out = lag(s, params=LagParams(periods=2))
        assert out.payload.index.equals(s.payload.index)

    def test_metadata_passthrough_and_key(self):
        s, _ = _input()
        s2 = _series("y", dates=_DATES, values=s.payload.to_numpy(),
                     frequency="B")
        out = lag(s2, params=LagParams(periods=3))
        assert out.units == TimeSeriesUnits.BPS
        assert out.frequency == "B"
        assert out.missingness_policy == s2.missingness_policy
        assert out.series_key == "lag3__y"


# ===========================================================================
# 2. OPR13 refusals + schema bounds
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        with pytest.raises(LagError, match="must be a Series"):
            lag([1, 2, 3])  # type: ignore[arg-type]

    def test_periods_at_input_length_refused(self):
        s, _ = _input()
        with pytest.raises(LagError, match="all-NaN"):
            lag(s, params=LagParams(periods=_N))

    def test_negative_and_zero_periods_rejected_at_schema(self):
        """Lead (negative shift) is deliberately unsupported — the
        schema floor enforces it before any compute."""
        with pytest.raises(ValueError):
            LagParams(periods=0)
        with pytest.raises(ValueError):
            LagParams(periods=-1)


# ===========================================================================
# 3. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        s, _ = _input()
        out_default = lag(s)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = LagParams(periods=int(cfg.default_value("periods")))
        out_explicit = lag(s, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = LagParams()
        assert p.periods == int(cfg.default_value("periods"))


# ===========================================================================
# 4. Lineage + determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_records_sign_convention(self):
        s, _ = _input()
        out = lag(s, params=LagParams(periods=4))
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "lag"
        assert head.params["periods"] == 4
        assert "BACK" in head.params["sign_convention"]

    def test_rerun_is_deterministic(self):
        s, _ = _input()
        p = LagParams(periods=2)
        assert (
            lag(s, params=p).lineage.head_hash
            == lag(s, params=p).lineage.head_hash
        )

    def test_periods_changes_identity(self):
        s, _ = _input()
        h1 = lag(s, params=LagParams(periods=1)).lineage.head_hash
        h2 = lag(s, params=LagParams(periods=2)).lineage.head_hash
        assert h1 != h2


# ===========================================================================
# 5. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(83)
    vals = rng.randn(_N)
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.RATIO)
    out = lag(s, params=LagParams(periods=2))
    expected = pd.Series(vals, index=_DATES).shift(2)
    pd.testing.assert_series_equal(out.payload, expected, check_names=False)
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 6. Composition — x − lag(x) momentum DAG
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
            workflow_id="lag_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="lg", operator_name="lag",
                    params={"periods": 5},
                ),
                OperatorNode(
                    node_id="mom", operator_name="series_arithmetic",
                    params={"op": "subtract"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="lg",
                             target_input_slot="series"),
                WorkflowEdge(source_node_id="p", target_node_id="mom",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="lg", target_node_id="mom",
                             target_input_slot="right"),
            ],
            terminal_node_id="mom",
        )
        return wf, _resolve

    def test_type_gate_accepts_momentum_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_x_minus_lag_x(self, tmp_path):
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
        expected = vals - vals.shift(5)
        np.testing.assert_allclose(
            terminal.payload.to_numpy(), expected.to_numpy(),
        )
        clear_tool_config_cache()
