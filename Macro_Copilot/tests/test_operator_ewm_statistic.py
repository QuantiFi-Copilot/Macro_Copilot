"""Tests for shared.operators.ewm_statistic — Track-A A1.

Covers the contract surface every standard operator must satisfy:

  - parity vs pandas ewm(span, min_periods, adjust=True).mean() and
    .std(bias=False) — the design-locked weighting
  - min_periods None → span (strict warmup), explicit min_periods
    allows earlier values
  - units/frequency/missingness passthrough
  - all-NaN refusal; non-Series refusal
  - OPR8 params=None + schema-mirrors-YAML; OPR14 determinism +
    variant-changes-identity; design locks recorded in lineage
  - OPR6 non-rates case; composition diff→ewm_std chain
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
from shared.operators.ewm_statistic import (
    CONFIG_PATH,
    EwmStatisticError,
    EwmStatisticParams,
    ewm_statistic,
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


_N = 60
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _input(seed: int = 41):
    rng = np.random.RandomState(seed)
    vals = rng.randn(_N).cumsum() + 5.0
    return _series("x", dates=_DATES, values=vals), vals


# ===========================================================================
# 1. Parity vs pandas (the design-locked weighting)
# ===========================================================================


class TestHappyPath:
    def test_mean_matches_pandas(self):
        s, vals = _input()
        out = ewm_statistic(s, params=EwmStatisticParams(span=10))
        expected = pd.Series(vals, index=_DATES).ewm(
            span=10, min_periods=10, adjust=True,
        ).mean()
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.BPS  # passthrough

    def test_std_matches_pandas_debiased(self):
        s, vals = _input(seed=7)
        out = ewm_statistic(
            s, params=EwmStatisticParams(statistic="std", span=15),
        )
        expected = pd.Series(vals, index=_DATES).ewm(
            span=15, min_periods=15, adjust=True,
        ).std(bias=False)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert out.units == TimeSeriesUnits.BPS  # passthrough (NOT RATIO)

    def test_min_periods_none_defaults_to_span_strict_warmup(self):
        s, _ = _input()
        out = ewm_statistic(s, params=EwmStatisticParams(span=12))
        assert out.payload.iloc[:11].isna().all()
        assert out.payload.iloc[11:].notna().all()

    def test_explicit_min_periods_allows_earlier_values(self):
        s, vals = _input()
        out = ewm_statistic(
            s, params=EwmStatisticParams(span=12, min_periods=3),
        )
        expected = pd.Series(vals, index=_DATES).ewm(
            span=12, min_periods=3, adjust=True,
        ).mean()
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert out.payload.iloc[2:].notna().all()

    def test_metadata_passthrough_and_key(self):
        s, _ = _input()
        s2 = _series("y", dates=_DATES, values=s.payload.to_numpy(),
                     frequency="B")
        out = ewm_statistic(s2, params=EwmStatisticParams(span=10))
        assert out.frequency == "B"
        assert out.missingness_policy == s2.missingness_policy
        assert out.series_key == "ewm_mean__y"

    def test_look_ahead_safe_shifts_by_one(self):
        """The rolling family's shared hygiene knob: with
        look_ahead_safe=True the value at t reflects data <= t-1."""
        s, vals = _input()
        plain = ewm_statistic(s, params=EwmStatisticParams(span=10))
        safe = ewm_statistic(
            s, params=EwmStatisticParams(span=10, look_ahead_safe=True),
        )
        pd.testing.assert_series_equal(
            safe.payload, plain.payload.shift(1), check_names=False,
        )

    def test_overflow_ew_std_scrubbed_to_nan_missingness(self):
        """Critic finding (OPR13/ART11): the EW std squares deviations
        inside pandas' accumulator, so extreme-but-finite inputs can
        overflow to ±Inf — scrubbed to NaN-missingness (the
        rolling_statistic windowed-reduction precedent), never a raw
        constructor crash."""
        vals = np.ones(_N)
        vals[30:] = [(-1) ** i * 1e200 for i in range(_N - 30)]
        s = _series("big", dates=_DATES, values=vals)
        out = ewm_statistic(
            s,
            params=EwmStatisticParams(
                statistic="std", span=10, min_periods=5,
            ),
        )
        arr = out.payload.to_numpy(dtype=float)
        assert not np.isinf(arr).any()
        assert np.isfinite(arr).any()  # the pre-extreme region survives


# ===========================================================================
# 2. OPR13 refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        with pytest.raises(EwmStatisticError, match="must be a Series"):
            ewm_statistic([1, 2, 3])  # type: ignore[arg-type]

    def test_all_nan_output_refused(self):
        s, _ = _input()
        with pytest.raises(EwmStatisticError, match="all-NaN"):
            ewm_statistic(s, params=EwmStatisticParams(span=100))


# ===========================================================================
# 3. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        s, _ = _input()
        out_default = ewm_statistic(s)
        cfg = load_operator_config(CONFIG_PATH)
        raw_mp = cfg.default_value("min_periods")
        explicit = EwmStatisticParams(
            statistic=cfg.default_value("statistic"),
            span=int(cfg.default_value("span")),
            min_periods=(int(raw_mp) if raw_mp is not None else None),
            look_ahead_safe=bool(cfg.default_value("look_ahead_safe")),
        )
        out_explicit = ewm_statistic(s, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = EwmStatisticParams()
        assert p.statistic == cfg.default_value("statistic")
        assert p.span == int(cfg.default_value("span"))
        raw_mp = cfg.default_value("min_periods")
        assert p.min_periods == (int(raw_mp) if raw_mp is not None else None)
        assert p.look_ahead_safe == bool(cfg.default_value("look_ahead_safe"))

    def test_span_floor_enforced(self):
        with pytest.raises(ValueError):
            EwmStatisticParams(span=1)


# ===========================================================================
# 4. Lineage + determinism (design locks recorded)
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_records_design_locks(self):
        s, _ = _input()
        out = ewm_statistic(s, params=EwmStatisticParams(span=10))
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "ewm_statistic"
        assert head.params["adjust"] is True
        assert head.params["std_bias"] is False
        assert head.params["ignore_na"] is False
        assert head.params["look_ahead_safe"] is False
        assert head.params["span"] == 10
        assert head.params["min_periods"] == 10

    def test_rerun_is_deterministic(self):
        s, _ = _input()
        p = EwmStatisticParams(span=10)
        assert (
            ewm_statistic(s, params=p).lineage.head_hash
            == ewm_statistic(s, params=p).lineage.head_hash
        )

    def test_statistic_changes_identity(self):
        s, _ = _input()
        h_mean = ewm_statistic(
            s, params=EwmStatisticParams(statistic="mean", span=10),
        ).lineage.head_hash
        h_std = ewm_statistic(
            s, params=EwmStatisticParams(statistic="std", span=10),
        ).lineage.head_hash
        assert h_mean != h_std


# ===========================================================================
# 5. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(79)
    vals = rng.randn(_N)
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.Z_SCORE)
    out = ewm_statistic(s, params=EwmStatisticParams(span=8))
    expected = pd.Series(vals, index=_DATES).ewm(
        span=8, min_periods=8, adjust=True,
    ).mean()
    pd.testing.assert_series_equal(out.payload, expected, check_names=False)
    assert out.units == TimeSeriesUnits.Z_SCORE


# ===========================================================================
# 6. Composition — diff → ewm_std chain (unannualized change dispersion)
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
            n_rows: int = 50
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
            workflow_id="ewm_statistic_composition",
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
                OperatorNode(
                    node_id="ev", operator_name="ewm_statistic",
                    params={"statistic": "std", "span": 10},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="d",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="d", target_node_id="ev",
                             target_input_slot="series"),
            ],
            terminal_node_id="ev",
        )
        return wf, _resolve

    def test_type_gate_accepts_ewm_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_diff_then_ewm_std(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.BPS
        n = 50
        vals = pd.Series([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        expected = vals.diff().ewm(
            span=10, min_periods=10, adjust=True,
        ).std(bias=False)
        np.testing.assert_allclose(
            terminal.payload.to_numpy(), expected.to_numpy(),
        )
        assert terminal.lineage.steps[-1].name == "ewm_statistic"
        clear_tool_config_cache()
