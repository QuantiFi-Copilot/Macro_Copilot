"""Tests for shared.operators.detrend — Track-A A1.

Covers the contract surface every standard operator must satisfy:

  - linear: parity vs numpy polyfit residuals; a perfect line detrends
    to ~0; the fitted slope/intercept/R² ride in lineage
  - demean: parity vs x − mean; mean rides in lineage
  - the FULL-SAMPLE look-ahead pinned behaviorally (an extreme late
    segment changes EARLY residuals) + trend_scope in lineage
  - NaN positions stay NaN; fit on finite positions only
  - units/frequency/missingness passthrough
  - refusals: non-Series; <2 finite observations; overflow
  - OPR8 params=None + schema-mirrors-YAML; OPR14 determinism +
    method-changes-identity; OPR6 non-rates case
  - composition detrend→summarize_series(std) DAG
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
    ScalarMetric,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.detrend import (
    CONFIG_PATH,
    DetrendError,
    DetrendParams,
    detrend,
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


_N = 40
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _input(seed: int = 61):
    rng = np.random.RandomState(seed)
    vals = 0.7 * np.arange(_N) + rng.randn(_N) * 2 + 10.0
    return _series("x", dates=_DATES, values=vals), vals


# ===========================================================================
# 1. Linear method
# ===========================================================================


class TestLinear:
    def test_matches_numpy_polyfit_residuals(self):
        s, vals = _input()
        out = detrend(s, params=DetrendParams(method="linear"))
        t = np.arange(_N, dtype=float)
        slope, intercept = np.polyfit(t, vals, 1)
        expected = vals - (intercept + slope * t)
        np.testing.assert_allclose(
            out.payload.to_numpy(), expected, atol=1e-10,
        )
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.BPS  # passthrough

    def test_perfect_line_detrends_to_zero(self):
        vals = 3.0 * np.arange(_N) + 5.0
        s = _series("line", dates=_DATES, values=vals)
        out = detrend(s, params=DetrendParams(method="linear"))
        np.testing.assert_allclose(
            out.payload.to_numpy(), 0.0, atol=1e-9,
        )

    def test_fitted_params_ride_in_lineage(self):
        s, vals = _input()
        out = detrend(s, params=DetrendParams(method="linear"))
        head = out.lineage.steps[-1]
        t = np.arange(_N, dtype=float)
        slope, intercept = np.polyfit(t, vals, 1)
        assert head.params["fitted"]["slope"] == pytest.approx(
            slope, rel=1e-9,
        )
        assert head.params["fitted"]["intercept"] == pytest.approx(
            intercept, rel=1e-9,
        )
        assert 0.0 <= head.params["fitted"]["r_squared"] <= 1.0
        assert head.params["trend_scope"] == "full_sample"


# ===========================================================================
# 2. Demean method
# ===========================================================================


class TestDemean:
    def test_matches_x_minus_mean(self):
        s, vals = _input(seed=7)
        out = detrend(s, params=DetrendParams(method="demean"))
        expected = vals - vals.mean()
        np.testing.assert_allclose(out.payload.to_numpy(), expected)
        head = out.lineage.steps[-1]
        assert head.params["fitted"]["mean"] == pytest.approx(vals.mean())

    def test_residual_sums_to_zero(self):
        s, _ = _input(seed=9)
        out = detrend(s, params=DetrendParams(method="demean"))
        assert out.payload.sum() == pytest.approx(0.0, abs=1e-9)


# ===========================================================================
# 3. Look-ahead pin + NaN policy
# ===========================================================================


class TestLookAheadAndNan:
    def test_full_sample_look_ahead_pinned(self):
        """An extreme LATE segment must change EARLY residuals — the
        fit is full-sample (disclosed, not hidden)."""
        base = np.zeros(_N)
        s1 = _series("a", dates=_DATES, values=base)
        v2 = base.copy()
        v2[30:] = 100.0
        s2 = _series("a", dates=_DATES, values=v2)
        out1 = detrend(s1, params=DetrendParams(method="demean"))
        out2 = detrend(s2, params=DetrendParams(method="demean"))
        # Early residuals differ although early VALUES are identical.
        assert (
            out1.payload.iloc[0] != pytest.approx(out2.payload.iloc[0])
        )

    def test_nan_positions_stay_nan_fit_on_finite(self):
        vals = np.array([1.0, np.nan, 3.0, 4.0, np.nan, 6.0])
        dates = _DATES[:6]
        s = _series("g", dates=dates, values=vals)
        out = detrend(s, params=DetrendParams(method="linear"))
        assert np.isnan(out.payload.iloc[1])
        assert np.isnan(out.payload.iloc[4])
        # finite positions: fit on (0,2,3,5) — perfect line there
        np.testing.assert_allclose(
            out.payload.dropna().to_numpy(), 0.0, atol=1e-9,
        )


# ===========================================================================
# 4. OPR13 refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        with pytest.raises(DetrendError, match="must be a Series"):
            detrend(3.14)  # type: ignore[arg-type]

    def test_fewer_than_two_finite_refused(self):
        s = _series(
            "one", dates=_DATES[:3], values=[5.0, np.nan, np.nan],
        )
        with pytest.raises(DetrendError, match="at least 2"):
            detrend(s)

    def test_all_nan_refused(self):
        s = _series("nn", dates=_DATES, values=[np.nan] * _N)
        with pytest.raises(DetrendError, match="at least 2"):
            detrend(s)

    def test_overflow_residual_raises_typed_error(self):
        """Legal finite inputs whose mean accumulator overflows: the
        residual goes ±Inf and must be a typed refusal, never a raw
        constructor crash (the demean_cross_section precedent)."""
        vals = np.ones(_N)
        vals[5] = 1.7e308
        vals[6] = -1.7e308
        vals[7] = -1.7e308
        vals[8] = -1.7e308
        s = _series("big", dates=_DATES, values=vals)
        with pytest.raises(DetrendError, match="overflow"):
            detrend(s, params=DetrendParams(method="demean"))


# ===========================================================================
# 5. OPR8 + determinism + metadata
# ===========================================================================


class TestParamsConfigDeterminism:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        s, _ = _input()
        out_default = detrend(s)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = DetrendParams(method=cfg.default_value("method"))
        out_explicit = detrend(s, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert DetrendParams().method == cfg.default_value("method")

    def test_rerun_is_deterministic(self):
        s, _ = _input()
        assert detrend(s).lineage.head_hash == detrend(s).lineage.head_hash

    def test_method_changes_identity(self):
        s, _ = _input()
        h_lin = detrend(
            s, params=DetrendParams(method="linear"),
        ).lineage.head_hash
        h_dm = detrend(
            s, params=DetrendParams(method="demean"),
        ).lineage.head_hash
        assert h_lin != h_dm

    def test_metadata_passthrough_and_key(self):
        s, _ = _input()
        s2 = _series("y", dates=_DATES, values=s.payload.to_numpy(),
                     frequency="B")
        out = detrend(s2)
        assert out.frequency == "B"
        assert out.missingness_policy == s2.missingness_policy
        assert out.series_key == "detrended_linear__y"


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(103)
    vals = -0.5 * np.arange(_N) + rng.randn(_N)
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.RATIO)
    out = detrend(s, params=DetrendParams(method="linear"))
    t = np.arange(_N, dtype=float)
    slope, intercept = np.polyfit(t, vals, 1)
    np.testing.assert_allclose(
        out.payload.to_numpy(), vals - (intercept + slope * t), atol=1e-10,
    )
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 7. Composition — detrend → summarize_series(std)
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
            workflow_id="detrend_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(node_id="dt", operator_name="detrend"),
                OperatorNode(
                    node_id="vol", operator_name="summarize_series",
                    params={"statistic": "std", "dispersion": "none"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="dt",
                             target_input_slot="series"),
                WorkflowEdge(source_node_id="dt", target_node_id="vol",
                             target_input_slot="series"),
            ],
            terminal_node_id="vol",
        )
        return wf, _resolve

    def test_type_gate_accepts_detrend_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_detrend_then_std(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.BPS
        n = 30
        vals = np.array([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        t = np.arange(n, dtype=float)
        slope, intercept = np.polyfit(t, vals, 1)
        residual = vals - (intercept + slope * t)
        expected_std = float(pd.Series(residual).std(ddof=1))
        assert terminal.value == pytest.approx(expected_std, rel=1e-9)
        clear_tool_config_cache()
