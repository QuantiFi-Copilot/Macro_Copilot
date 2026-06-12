"""Tests for shared.operators.ljung_box — Track-A A5.

Covers the contract surface every standard operator must satisfy:

  - parity vs statsmodels acorr_ljungbox (largest-horizon row exact;
    per-lag trail in lineage)
  - directional sanity: an AR(1) series shows a large Q (small p);
    white noise a small one
  - the auto-lag rule (None → min(10, n//5), resolved value in
    lineage; auto ≡ explicit equal value by hash — OPR14)
  - NaN dropna + interior-drop audit
  - refusals: non-Series; too few rows for the horizon; zero variance
  - OPR8 schema-mirrors-YAML; OPR14 determinism + lags identity
  - OPR6 non-rates case; composition diff→ljung_box DAG
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel
from statsmodels.stats.diagnostic import acorr_ljungbox

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
from shared.operators.ljung_box import (
    CONFIG_PATH,
    LjungBoxError,
    LjungBoxParams,
    ljung_box,
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


_N = 120
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _noise(seed: int = 149):
    rng = np.random.RandomState(seed)
    vals = rng.randn(_N)
    return _series("x", dates=_DATES, values=vals), vals


def _ar1(seed: int = 151, phi: float = 0.8):
    rng = np.random.RandomState(seed)
    eps = rng.randn(_N)
    vals = np.zeros(_N)
    for i in range(1, _N):
        vals[i] = phi * vals[i - 1] + eps[i]
    return _series("ar", dates=_DATES, values=vals), vals


# ===========================================================================
# 1. Parity + directional sanity
# ===========================================================================


class TestHappyPath:
    def test_matches_statsmodels_largest_horizon_row(self):
        s, vals = _noise()
        out = ljung_box(s, params=LjungBoxParams(lags=5))
        ref = acorr_ljungbox(vals, lags=5)
        assert out.value == pytest.approx(
            float(ref["lb_stat"].iloc[-1]), rel=1e-12,
        )
        assert isinstance(out, ScalarMetric)  # OPR2 constant type
        assert out.units == TimeSeriesUnits.RATIO
        assert out.metric_key == "ljung_box__x"
        head = out.lineage.steps[-1]
        assert head.params["p_value"] == pytest.approx(
            float(ref["lb_pvalue"].iloc[-1]),
        )
        np.testing.assert_allclose(
            head.params["per_lag_stats"], ref["lb_stat"].to_numpy(),
        )

    def test_ar1_strongly_autocorrelated(self):
        s, _ = _ar1()
        out = ljung_box(s, params=LjungBoxParams(lags=5))
        assert out.lineage.steps[-1].params["p_value"] < 1e-6

    def test_white_noise_not_significant(self):
        s, _ = _noise(seed=157)
        out = ljung_box(s, params=LjungBoxParams(lags=5))
        assert out.lineage.steps[-1].params["p_value"] > 0.05

    def test_auto_lag_rule_resolves_and_hashes_like_explicit(self):
        """OPR14: the RESOLVED horizon rides in lineage — auto and an
        explicit equal value hash identically."""
        s, _ = _noise()
        out_auto = ljung_box(s)  # n=120 → min(10, 24) = 10
        assert out_auto.lineage.steps[-1].params["lags"] == 10
        out_explicit = ljung_box(s, params=LjungBoxParams(lags=10))
        assert (
            out_auto.lineage.head_hash == out_explicit.lineage.head_hash
        )

    def test_auto_lag_scales_down_for_short_inputs(self):
        s = _series(
            "short", dates=_DATES[:20],
            values=np.random.RandomState(5).randn(20),
        )
        out = ljung_box(s)  # n=20 → min(10, 4) = 4
        assert out.lineage.steps[-1].params["lags"] == 4


# ===========================================================================
# 2. NaN audit
# ===========================================================================


class TestNanAudit:
    def test_interior_gaps_disclosed(self):
        vals = np.random.RandomState(3).randn(_N)
        vals[50] = np.nan
        vals[-3:] = np.nan  # edge
        s = _series("g", dates=_DATES, values=vals)
        out = ljung_box(s, params=LjungBoxParams(lags=5))
        head = out.lineage.steps[-1]
        assert head.params["n_dropped"] == 4
        assert head.params["n_interior_dropped"] == 1


# ===========================================================================
# 3. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_non_series_input_raises(self):
        with pytest.raises(LjungBoxError, match="must be a Series"):
            ljung_box(0.5)  # type: ignore[arg-type]

    def test_too_few_rows_for_horizon_refused(self):
        s = _series(
            "few", dates=_DATES[:6],
            values=np.random.RandomState(9).randn(6),
        )
        with pytest.raises(LjungBoxError, match="lags \\+ 2"):
            ljung_box(s, params=LjungBoxParams(lags=5))

    def test_zero_variance_refused(self):
        s = _series("const", dates=_DATES[:30], values=[2.0] * 30)
        with pytest.raises(LjungBoxError, match="zero variance"):
            ljung_box(s)

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = LjungBoxParams()
        assert p.lags == cfg.default_value("lags")  # both None

    def test_lags_bounds_enforced_at_schema(self):
        with pytest.raises(ValueError):
            LjungBoxParams(lags=0)

    def test_rerun_is_deterministic(self):
        s, _ = _noise()
        assert (
            ljung_box(s).lineage.head_hash
            == ljung_box(s).lineage.head_hash
        )

    def test_lags_changes_identity(self):
        s, _ = _noise()
        h5 = ljung_box(
            s, params=LjungBoxParams(lags=5),
        ).lineage.head_hash
        h7 = ljung_box(
            s, params=LjungBoxParams(lags=7),
        ).lineage.head_hash
        assert h5 != h7


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(163)
    vals = rng.randn(_N)
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.RATIO)
    out = ljung_box(s, params=LjungBoxParams(lags=8))
    ref = acorr_ljungbox(vals, lags=8)
    assert out.value == pytest.approx(
        float(ref["lb_stat"].iloc[-1]), rel=1e-12,
    )
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 5. Composition — diff → ljung_box (change-dependence check)
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
            n_rows: int = 60
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

        # 'Are the day-over-day changes of A serially dependent?'
        wf = Workflow(
            workflow_id="ljung_box_composition",
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
                    node_id="lb", operator_name="ljung_box",
                    params={"lags": 6},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="d",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="d", target_node_id="lb",
                             target_input_slot="series"),
            ],
            terminal_node_id="lb",
        )
        return wf, _resolve

    def test_type_gate_accepts_ljung_box_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_change_dependence(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.RATIO
        n = 60
        vals = pd.Series([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        diffed = vals.diff().dropna().to_numpy()
        ref = acorr_ljungbox(diffed, lags=6)
        assert terminal.value == pytest.approx(
            float(ref["lb_stat"].iloc[-1]), rel=1e-9,
        )
        clear_tool_config_cache()
