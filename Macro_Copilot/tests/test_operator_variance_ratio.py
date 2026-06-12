"""Tests for shared.operators.variance_ratio — Track-A A5.

Covers the contract surface every standard operator must satisfy:

  - parity vs the shared/quant numeric (value = the z-statistic; VR(q)
    + p-value + m ride in lineage)
  - directional sanity: random walk → VR≈1; mean-reverting → VR<1;
    trending → VR>1
  - the q auto-rule (None → 2, resolved value in lineage; auto ≡
    explicit equal value by hash — OPR14) and the robust knob
  - NaN dropna + interior-drop audit (lag-based test)
  - refusals: non-Series; too few rows for the horizon; zero variance;
    perfect ramp
  - OPR8 schema-mirrors-YAML; OPR14 determinism + q/robust identity
  - OPR6 non-rates case; composition primitive→variance_ratio DAG
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
from shared.operators.variance_ratio import (
    CONFIG_PATH,
    VarianceRatioError,
    VarianceRatioParams,
    variance_ratio,
)
from shared.quant.variance_ratio import variance_ratio_test


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


def _random_walk(seed: int = 167, n: int = _N):
    rng = np.random.RandomState(seed)
    vals = np.cumsum(rng.randn(n))
    dates = pd.bdate_range("2026-01-02", periods=n)
    return _series("x", dates=dates, values=vals), vals


# ===========================================================================
# 1. Parity + directional sanity
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_numeric(self):
        s, vals = _random_walk()
        out = variance_ratio(s, params=VarianceRatioParams(q=4))
        ref = variance_ratio_test(vals, q=4, robust=True)
        assert out.value == pytest.approx(ref.z_statistic, rel=1e-12)
        assert isinstance(out, ScalarMetric)  # OPR2 constant type
        assert out.units == TimeSeriesUnits.RATIO
        assert out.metric_key == "variance_ratio__x"
        head = out.lineage.steps[-1]
        assert head.params["variance_ratio"] == pytest.approx(ref.vr)
        assert head.params["p_value"] == pytest.approx(ref.p_value)
        assert head.params["m"] == pytest.approx(ref.m)
        assert head.params["q"] == 4
        assert head.params["robust"] is True
        assert head.params["overlapping"] is True
        assert head.params["bias_corrected"] is True

    def test_random_walk_not_rejected(self):
        # Mean VR across walks ≈ 1; a representative one stays near 1.
        s, _ = _random_walk(seed=170, n=600)
        out = variance_ratio(s, params=VarianceRatioParams(q=4))
        vr = out.lineage.steps[-1].params["variance_ratio"]
        assert 0.8 < vr < 1.2

    def test_mean_reverting_vr_below_one(self):
        rng = np.random.RandomState(11)
        n = 600
        vals = np.zeros(n)
        for i in range(1, n):
            vals[i] = 0.5 * vals[i - 1] + rng.randn()
        s = _series("mr", dates=pd.bdate_range("2024-01-01", periods=n),
                    values=vals)
        out = variance_ratio(s, params=VarianceRatioParams(q=4))
        assert out.lineage.steps[-1].params["variance_ratio"] < 0.9
        assert out.value < -2.0  # significantly negative z

    def test_trending_vr_above_one(self):
        rng = np.random.RandomState(13)
        n = 600
        e = np.zeros(n)
        for i in range(1, n):
            e[i] = 0.6 * e[i - 1] + rng.randn()
        vals = np.cumsum(e)
        s = _series("tr", dates=pd.bdate_range("2024-01-01", periods=n),
                    values=vals)
        out = variance_ratio(s, params=VarianceRatioParams(q=4))
        assert out.lineage.steps[-1].params["variance_ratio"] > 1.1
        assert out.value > 2.0  # significantly positive z

    def test_auto_q_resolves_and_hashes_like_explicit(self):
        """OPR14: the RESOLVED horizon rides in lineage — auto and an
        explicit q=2 hash identically."""
        s, _ = _random_walk()
        out_auto = variance_ratio(s)
        assert out_auto.lineage.steps[-1].params["q"] == 2
        out_explicit = variance_ratio(s, params=VarianceRatioParams(q=2))
        assert (
            out_auto.lineage.head_hash == out_explicit.lineage.head_hash
        )

    def test_full_sample_scope_in_lineage(self):
        s, _ = _random_walk()
        out = variance_ratio(s)
        assert out.lineage.steps[-1].params["test_scope"] == "full_sample"


# ===========================================================================
# 2. NaN audit
# ===========================================================================


class TestNanAudit:
    def test_interior_gaps_disclosed(self):
        vals = np.cumsum(np.random.RandomState(3).randn(_N))
        vals[50] = np.nan
        vals[-3:] = np.nan  # edge
        s = _series("g", dates=_DATES, values=vals)
        out = variance_ratio(s, params=VarianceRatioParams(q=4))
        head = out.lineage.steps[-1]
        assert head.params["n_dropped"] == 4
        assert head.params["n_interior_dropped"] == 1
        # Parity against the cleaned values.
        ref = variance_ratio_test(
            pd.Series(vals).dropna().to_numpy(), q=4,
        )
        assert out.value == pytest.approx(ref.z_statistic, rel=1e-12)


# ===========================================================================
# 3. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_non_series_input_raises(self):
        with pytest.raises(VarianceRatioError, match="must be a Series"):
            variance_ratio(0.5)  # type: ignore[arg-type]

    def test_too_few_rows_for_horizon_refused(self):
        s = _series(
            "few", dates=_DATES[:10],
            values=np.cumsum(np.random.RandomState(9).randn(10)),
        )
        with pytest.raises(VarianceRatioError, match="max\\(12, q \\+ 2\\)"):
            variance_ratio(s, params=VarianceRatioParams(q=4))

    def test_horizon_floor_binds_above_twelve(self):
        # q=20 → floor max(12, 22) = 22; 15 rows is too few.
        s = _series(
            "mid", dates=pd.bdate_range("2025-01-01", periods=15),
            values=np.cumsum(np.random.RandomState(4).randn(15)),
        )
        with pytest.raises(VarianceRatioError, match="q=20"):
            variance_ratio(s, params=VarianceRatioParams(q=20))

    def test_zero_variance_refused(self):
        s = _series("const", dates=_DATES[:30], values=[2.0] * 30)
        with pytest.raises(VarianceRatioError, match="zero variance"):
            variance_ratio(s)

    def test_perfect_ramp_refused(self):
        s = _series("ramp", dates=_DATES[:30],
                    values=np.arange(30, dtype=float))
        with pytest.raises(VarianceRatioError, match="perfect linear ramp"):
            variance_ratio(s)

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = VarianceRatioParams()
        assert p.q == cfg.default_value("q")  # both None
        assert p.robust is True  # schema-only default (not in YAML)

    def test_robust_not_in_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        # robust is a schema-only structural default (OPR8 — never both
        # config-authoritative AND schema-default); only q is in YAML.
        assert "robust" not in cfg.defaults
        assert "q" in cfg.defaults

    def test_q_bounds_enforced_at_schema(self):
        with pytest.raises(ValueError):
            VarianceRatioParams(q=1)

    def test_rerun_is_deterministic(self):
        s, _ = _random_walk()
        assert (
            variance_ratio(s).lineage.head_hash
            == variance_ratio(s).lineage.head_hash
        )

    def test_q_changes_identity(self):
        s, _ = _random_walk()
        h4 = variance_ratio(
            s, params=VarianceRatioParams(q=4),
        ).lineage.head_hash
        h8 = variance_ratio(
            s, params=VarianceRatioParams(q=8),
        ).lineage.head_hash
        assert h4 != h8

    def test_robust_changes_identity(self):
        s, _ = _random_walk()
        ht = variance_ratio(
            s, params=VarianceRatioParams(q=4, robust=True),
        ).lineage.head_hash
        hf = variance_ratio(
            s, params=VarianceRatioParams(q=4, robust=False),
        ).lineage.head_hash
        assert ht != hf

    def test_homoskedastic_flag_changes_value(self):
        s, vals = _random_walk()
        out = variance_ratio(s, params=VarianceRatioParams(q=4, robust=False))
        ref = variance_ratio_test(vals, q=4, robust=False)
        assert out.value == pytest.approx(ref.z_statistic, rel=1e-12)
        assert out.lineage.steps[-1].params["robust"] is False


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    vals = np.cumsum(np.random.RandomState(181).randn(_N))
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.RATIO)
    out = variance_ratio(s, params=VarianceRatioParams(q=6))
    ref = variance_ratio_test(vals, q=6)
    assert out.value == pytest.approx(ref.z_statistic, rel=1e-12)
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 5. Composition — primitive → variance_ratio (level consumed directly)
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

        # 'Is series A a random walk vs trending/mean-reverting at q=5?'
        # — variance_ratio consumes the LEVEL series directly (no diff).
        wf = Workflow(
            workflow_id="variance_ratio_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="vr", operator_name="variance_ratio",
                    params={"q": 5},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="vr",
                             target_input_slot="series"),
            ],
            terminal_node_id="vr",
        )
        return wf, _resolve

    def test_type_gate_accepts_variance_ratio_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_random_walk_test(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.RATIO
        n = 60
        vals = np.array([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        ref = variance_ratio_test(vals, q=5)
        assert terminal.value == pytest.approx(ref.z_statistic, rel=1e-9)
        clear_tool_config_cache()
