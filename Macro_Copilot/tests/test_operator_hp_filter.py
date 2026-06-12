"""Tests for shared.operators.hp_filter — Track-A A1.

Covers the contract surface every standard operator must satisfy:

  - parity vs statsmodels hpfilter for both components; exact
    reconstruction trend + cycle == input
  - λ behavior: larger λ → smoother trend (lower trend variance);
    λ REQUIRED (params=None typed refusal naming the field)
  - the two-sided look-ahead pinned behaviorally (a late segment
    changes EARLY trend values) + filter_scope in lineage
  - NaN refused with the upstream-cleaning remedy; < 3 rows refused
  - units/frequency/missingness passthrough
  - OPR8 component default mirrors YAML; OPR14 determinism +
    component/λ-change identity; OPR6 non-rates case
  - composition hp_filter(cycle)→summarize_series(std) DAG
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel
from statsmodels.tsa.filters.hp_filter import hpfilter

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
from shared.operators.hp_filter import (
    CONFIG_PATH,
    HpFilterError,
    HpFilterParams,
    hp_filter,
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
_LAMB = 1600.0


def _input(seed: int = 67):
    rng = np.random.RandomState(seed)
    vals = (
        0.3 * np.arange(_N)
        + 5.0 * np.sin(np.arange(_N) / 4.0)
        + rng.randn(_N)
    )
    return _series("x", dates=_DATES, values=vals), vals


# ===========================================================================
# 1. Parity + reconstruction + λ behavior
# ===========================================================================


class TestHappyPath:
    @pytest.mark.parametrize("component", ["trend", "cycle"])
    def test_matches_statsmodels(self, component):
        s, vals = _input()
        out = hp_filter(
            s,
            params=HpFilterParams(component=component, lamb=_LAMB),
        )
        cycle, trend = hpfilter(
            pd.Series(vals, index=_DATES), lamb=_LAMB,
        )
        expected = (trend if component == "trend" else cycle).astype(float)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.BPS  # passthrough

    def test_trend_plus_cycle_reconstructs_input(self):
        s, vals = _input()
        t = hp_filter(
            s, params=HpFilterParams(component="trend", lamb=_LAMB),
        )
        c = hp_filter(
            s, params=HpFilterParams(component="cycle", lamb=_LAMB),
        )
        np.testing.assert_allclose(
            (t.payload + c.payload).to_numpy(), vals, atol=1e-8,
        )

    def test_larger_lambda_smooths_more(self):
        s, _ = _input()
        t_lo = hp_filter(
            s, params=HpFilterParams(component="trend", lamb=10.0),
        )
        t_hi = hp_filter(
            s, params=HpFilterParams(component="trend", lamb=1e7),
        )
        # Smoothness via the variance of first differences.
        rough_lo = float(np.var(np.diff(t_lo.payload.to_numpy())))
        rough_hi = float(np.var(np.diff(t_hi.payload.to_numpy())))
        assert rough_hi < rough_lo

    def test_metadata_passthrough_and_key(self):
        s, _ = _input()
        s2 = _series("y", dates=_DATES, values=s.payload.to_numpy(),
                     frequency="B")
        out = hp_filter(
            s2, params=HpFilterParams(component="cycle", lamb=_LAMB),
        )
        assert out.frequency == "B"
        assert out.missingness_policy == s2.missingness_policy
        assert out.series_key == "hp_cycle__y"


# ===========================================================================
# 2. The two-sided look-ahead + lineage disclosure
# ===========================================================================


class TestLookAheadAndLineage:
    def test_two_sided_look_ahead_pinned(self):
        """A change in the LATE segment must move EARLY trend values —
        the filter is two-sided (disclosed, not hidden)."""
        base = np.sin(np.arange(_N) / 5.0)
        s1 = _series("a", dates=_DATES, values=base)
        v2 = base.copy()
        v2[45:] += 50.0
        s2 = _series("a", dates=_DATES, values=v2)
        t1 = hp_filter(
            s1, params=HpFilterParams(component="trend", lamb=_LAMB),
        )
        t2 = hp_filter(
            s2, params=HpFilterParams(component="trend", lamb=_LAMB),
        )
        assert t1.payload.iloc[0] != pytest.approx(t2.payload.iloc[0])

    def test_lineage_records_lambda_scope_and_variance_share(self):
        s, _ = _input()
        out = hp_filter(
            s, params=HpFilterParams(component="cycle", lamb=_LAMB),
        )
        head = out.lineage.steps[-1]
        assert head.name == "hp_filter"
        assert head.params["lamb"] == _LAMB
        assert head.params["filter_scope"] == "full_sample_two_sided"
        assert 0.0 <= head.params["cycle_variance_share"] <= 1.0
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1


# ===========================================================================
# 3. OPR13 refusals
# ===========================================================================


class TestRefusals:
    def test_params_none_refused_naming_lamb(self):
        """λ is methodology — the weighted_combination precedent."""
        s, _ = _input()
        with pytest.raises(HpFilterError, match="lamb"):
            hp_filter(s)

    def test_non_series_input_raises(self):
        with pytest.raises(HpFilterError, match="must be a Series"):
            hp_filter(
                [1.0, 2.0],  # type: ignore[arg-type]
                params=HpFilterParams(lamb=_LAMB),
            )

    def test_interior_nan_refused_with_actionable_remedy(self):
        vals = np.sin(np.arange(_N) / 4.0)
        vals[7] = np.nan
        s = _series("g", dates=_DATES, values=vals)
        with pytest.raises(HpFilterError, match="INTERIOR"):
            hp_filter(s, params=HpFilterParams(lamb=_LAMB))

    def test_fewer_than_three_finite_refused(self):
        s = _series("two", dates=_DATES[:2], values=[1.0, 2.0])
        with pytest.raises(HpFilterError, match="at least 3"):
            hp_filter(s, params=HpFilterParams(lamb=_LAMB))

    def test_lamb_bounds_enforced_at_schema(self):
        with pytest.raises(ValueError):
            HpFilterParams(lamb=0.0)
        with pytest.raises(ValueError):
            HpFilterParams(lamb=-5.0)
        with pytest.raises(ValueError):
            HpFilterParams()  # type: ignore[call-arg] — lamb required


# ===========================================================================
# 3b. Edge-warmup NaN passthrough (the rolling/ewm/lag upstream case)
# ===========================================================================


class TestEdgeNanPassthrough:
    def test_leading_warmup_nan_tolerated_and_passed_through(self):
        """A rolling-style warmup head must NOT refuse: the solve runs
        on the interior block; the head passes through as NaN."""
        vals = np.sin(np.arange(_N) / 4.0) + 0.1 * np.arange(_N)
        vals[:10] = np.nan
        s = _series("w", dates=_DATES, values=vals)
        out = hp_filter(
            s, params=HpFilterParams(component="trend", lamb=_LAMB),
        )
        assert out.payload.iloc[:10].isna().all()
        assert out.payload.iloc[10:].notna().all()
        # Parity vs statsmodels on the interior block alone.
        _cycle, trend = hpfilter(
            pd.Series(vals[10:], index=_DATES[10:]), lamb=_LAMB,
        )
        np.testing.assert_allclose(
            out.payload.iloc[10:].to_numpy(),
            trend.to_numpy(dtype=float),
        )
        head = out.lineage.steps[-1]
        assert head.params["n_leading_nan"] == 10
        assert head.params["n_trailing_nan"] == 0

    def test_trailing_nan_tolerated(self):
        vals = np.cos(np.arange(_N) / 5.0)
        vals[-4:] = np.nan
        s = _series("t", dates=_DATES, values=vals)
        out = hp_filter(
            s, params=HpFilterParams(component="cycle", lamb=_LAMB),
        )
        assert out.payload.iloc[-4:].isna().all()
        assert out.payload.iloc[:-4].notna().all()
        assert out.lineage.steps[-1].params["n_trailing_nan"] == 4

    def test_rolling_statistic_chain_composes(self):
        """The exact upstream the policy exists for: a rolling output
        (warmup head) feeds hp_filter without refusal."""
        from shared.operators.rolling_statistic import (
            RollingStatisticParams,
            rolling_statistic,
        )
        s, _ = _input()
        smoothed = rolling_statistic(
            s, params=RollingStatisticParams(statistic="mean", window=10),
        )
        assert smoothed.payload.iloc[:9].isna().all()  # warmup head
        out = hp_filter(
            smoothed,
            params=HpFilterParams(component="trend", lamb=_LAMB),
        )
        assert out.payload.iloc[:9].isna().all()
        assert out.payload.iloc[9:].notna().all()


# ===========================================================================
# 4. OPR8 + determinism
# ===========================================================================


class TestParamsConfigDeterminism:
    def test_component_default_mirrors_yaml(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = HpFilterParams(lamb=_LAMB)
        assert p.component == cfg.default_value("component")

    def test_rerun_is_deterministic(self):
        s, _ = _input()
        p = HpFilterParams(component="trend", lamb=_LAMB)
        assert (
            hp_filter(s, params=p).lineage.head_hash
            == hp_filter(s, params=p).lineage.head_hash
        )

    def test_component_and_lambda_change_identity(self):
        s, _ = _input()
        h_t = hp_filter(
            s, params=HpFilterParams(component="trend", lamb=_LAMB),
        ).lineage.head_hash
        h_c = hp_filter(
            s, params=HpFilterParams(component="cycle", lamb=_LAMB),
        ).lineage.head_hash
        h_t2 = hp_filter(
            s, params=HpFilterParams(component="trend", lamb=3200.0),
        ).lineage.head_hash
        assert h_t != h_c
        assert h_t != h_t2


# ===========================================================================
# 5. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(107)
    vals = np.cos(np.arange(_N) / 6.0) + rng.randn(_N) * 0.2
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.RATIO)
    out = hp_filter(
        s, params=HpFilterParams(component="trend", lamb=100.0),
    )
    cycle, trend = hpfilter(pd.Series(vals, index=_DATES), lamb=100.0)
    pd.testing.assert_series_equal(
        out.payload, trend.astype(float), check_names=False,
    )
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 6. Composition — hp_filter(cycle) → summarize_series(std)
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
            workflow_id="hp_filter_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="hp", operator_name="hp_filter",
                    params={"component": "cycle", "lamb": 1e5},
                ),
                OperatorNode(
                    node_id="vol", operator_name="summarize_series",
                    params={"statistic": "std", "dispersion": "none"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="hp",
                             target_input_slot="series"),
                WorkflowEdge(source_node_id="hp", target_node_id="vol",
                             target_input_slot="series"),
            ],
            terminal_node_id="vol",
        )
        return wf, _resolve

    def test_type_gate_accepts_hp_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_cycle_then_std(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.BPS
        n = 50
        bdays = pd.bdate_range("2026-04-01", periods=n)
        vals = pd.Series([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ], index=bdays)
        cycle, _trend = hpfilter(vals, lamb=1e5)
        expected_std = float(cycle.std(ddof=1))
        assert terminal.value == pytest.approx(expected_std, rel=1e-9)
        clear_tool_config_cache()
