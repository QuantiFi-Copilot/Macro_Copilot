"""Tests for shared.operators.granger_causality — Track-A A2 operator.

Covers the contract surface every standard operator must satisfy:

  - parity vs statsmodels.tsa.stattools.grangercausalitytests
    (ssr_ftest F AND p-value) — the independent reference
  - a strongly Granger-causal synthetic (right driven by left's lags)
    yields a large F / tiny p; an independent pair does not
  - non-commutativity (direction matters by construction)
  - OPR2 constant output type (always ScalarMetric, RATIO)
  - OPR8 params=None succeeds + matches explicit config resolution;
    schema mirrors YAML
  - OPR11 strict-by-default frequency + missingness with opt-outs;
    cross-unit inputs accepted with both units recorded
  - OPR13 typed refusals: misaligned index, too few rows for the lag
    order (the df2 >= 1 invariant), zero-variance arms, perfect-fit
    unbounded F, self-test collinearity
  - OPR10 lineage diagnostics (F, p_value, df1, df2, n_obs, direction)
    + OPR14 rerun determinism
  - OPR6 finance-blindness (non-rates synthetic data)
  - composition: type-gate accepts granger_causality and a DAG executes
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
    RawNoCleaning,
    ScalarMetric,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.granger_causality import (
    CONFIG_PATH,
    GrangerCausalityError,
    GrangerCausalityParams,
    granger_causality,
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
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
    frequency=None,
    missingness_policy=None,
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
        missingness_policy=missingness_policy or CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


_N = 200
_DATES = pd.bdate_range("2025-01-02", periods=_N)


def _causal_pair(seed: int = 19, strength: float = 0.8):
    """right_t = strength·left_{t−1} − 0.3·left_{t−2} + noise: left's
    lags genuinely drive right."""
    rng = np.random.RandomState(seed)
    left = rng.randn(_N)
    right = np.zeros(_N)
    for t in range(2, _N):
        right[t] = (
            strength * left[t - 1]
            - 0.3 * left[t - 2]
            + 0.2 * right[t - 1]
            + rng.randn() * 0.4
        )
    return (
        _series("driver", dates=_DATES, values=left),
        _series("response", dates=_DATES, values=right),
        left,
        right,
    )


def _independent_pair(seed: int = 23):
    rng = np.random.RandomState(seed)
    return (
        _series("ia", dates=_DATES, values=rng.randn(_N)),
        _series("ib", dates=_DATES, values=rng.randn(_N)),
    )


def _statsmodels_reference(left_vals, right_vals, p):
    """ssr_ftest from statsmodels: tests whether the SECOND column
    Granger-causes the FIRST, so [right, left] tests left → right."""
    from statsmodels.tsa.stattools import grangercausalitytests
    data = pd.DataFrame({"y": right_vals, "x": left_vals})
    res = grangercausalitytests(data[["y", "x"]], maxlag=[p])
    f, pval, _, _ = res[p][0]["ssr_ftest"]
    return float(f), float(pval)


# ===========================================================================
# 1. Parity vs statsmodels + signal behaviour
# ===========================================================================


class TestHappyPath:
    def test_matches_statsmodels_ssr_ftest(self):
        left, right, lv, rv = _causal_pair()
        out = granger_causality(
            left, right, params=GrangerCausalityParams(n_lags=2),
        )
        assert isinstance(out, ScalarMetric)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.RATIO
        f_ref, p_ref = _statsmodels_reference(lv, rv, 2)
        assert out.value == pytest.approx(f_ref, rel=1e-9)
        head = out.lineage.steps[-1]
        assert head.params["p_value"] == pytest.approx(p_ref, rel=1e-9)

    def test_matches_statsmodels_at_other_lag_orders(self):
        left, right, lv, rv = _causal_pair(seed=31)
        for p in (1, 4):
            out = granger_causality(
                left, right, params=GrangerCausalityParams(n_lags=p),
            )
            f_ref, p_ref = _statsmodels_reference(lv, rv, p)
            assert out.value == pytest.approx(f_ref, rel=1e-9), f"p={p}"
            assert out.lineage.steps[-1].params["p_value"] == pytest.approx(
                p_ref, rel=1e-9,
            ), f"p={p}"

    def test_causal_pair_yields_large_f_tiny_p(self):
        left, right, _, _ = _causal_pair()
        out = granger_causality(
            left, right, params=GrangerCausalityParams(n_lags=2),
        )
        assert out.value > 20.0
        assert out.lineage.steps[-1].params["p_value"] < 1e-6

    def test_independent_pair_yields_small_f_large_p(self):
        left, right = _independent_pair()
        out = granger_causality(
            left, right, params=GrangerCausalityParams(n_lags=2),
        )
        assert out.lineage.steps[-1].params["p_value"] > 0.01

    def test_not_commutative_direction_matters(self):
        """right is driven by left's lags, so left→right has a much
        larger F than right→left."""
        left, right, _, _ = _causal_pair()
        p = GrangerCausalityParams(n_lags=2)
        forward = granger_causality(left, right, params=p)
        reverse = granger_causality(right, left, params=p)
        assert forward.value > 5.0 * reverse.value

    def test_metric_key_names_direction(self):
        left, right, _, _ = _causal_pair()
        out = granger_causality(
            left, right, params=GrangerCausalityParams(n_lags=2),
        )
        assert out.metric_key == "granger__driver__response"

    def test_nan_rows_complete_case_match_statsmodels_on_clean_subset(self):
        """NaN-bearing inputs: the operator drops incomplete rows; on
        an input whose NaNs sit at the head, the complete-case window
        equals the clean tail, so statsmodels on the tail must agree."""
        left, right, lv, rv = _causal_pair(seed=37)
        lv2 = lv.copy()
        lv2[:10] = np.nan
        left2 = _series("d2", dates=_DATES, values=lv2)
        out = granger_causality(
            left2, right, params=GrangerCausalityParams(n_lags=2),
        )
        f_ref, _ = _statsmodels_reference(lv2[10:], rv[10:], 2)
        assert out.value == pytest.approx(f_ref, rel=1e-9)


# ===========================================================================
# 2. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        left, right, _, _ = _causal_pair()
        out_default = granger_causality(left, right)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = GrangerCausalityParams(
            n_lags=int(cfg.default_value("n_lags")),
            condition_number_threshold=float(
                cfg.default_value("condition_number_threshold")
            ),
        )
        out_explicit = granger_causality(left, right, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = GrangerCausalityParams()
        assert p.n_lags == int(cfg.default_value("n_lags"))
        assert p.condition_number_threshold == float(
            cfg.default_value("condition_number_threshold")
        )

    def test_n_lags_floor_enforced(self):
        with pytest.raises(ValueError):
            GrangerCausalityParams(n_lags=0)


# ===========================================================================
# 3. OPR13 — typed refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        left, _, _, _ = _causal_pair()
        with pytest.raises(GrangerCausalityError, match="must be Series"):
            granger_causality(left, [1, 2, 3])  # type: ignore[arg-type]

    def test_misaligned_index_raises(self):
        left, _, _, _ = _causal_pair()
        other = _series(
            "z", dates=pd.bdate_range("2027-01-04", periods=_N),
            values=list(range(_N)),
        )
        with pytest.raises(
            GrangerCausalityError, match="identical DatetimeIndex",
        ):
            granger_causality(left, other)

    def test_too_few_rows_for_lag_order_raises(self):
        n = 10
        dates = pd.bdate_range("2026-01-02", periods=n)
        rng = np.random.RandomState(3)
        left = _series("s1", dates=dates, values=rng.randn(n))
        right = _series("s2", dates=dates, values=rng.randn(n))
        with pytest.raises(GrangerCausalityError, match="df2"):
            granger_causality(
                left, right, params=GrangerCausalityParams(n_lags=4),
            )

    def test_zero_variance_response_raises(self):
        left, _, _, _ = _causal_pair()
        const = _series("c", dates=_DATES, values=[3.0] * _N)
        with pytest.raises(GrangerCausalityError, match="zero variance"):
            granger_causality(
                left, const, params=GrangerCausalityParams(n_lags=2),
            )

    def test_zero_variance_driver_raises(self):
        _, right, _, _ = _causal_pair()
        const = _series("c", dates=_DATES, values=[3.0] * _N)
        with pytest.raises(GrangerCausalityError, match="zero variance"):
            granger_causality(
                const, right, params=GrangerCausalityParams(n_lags=2),
            )

    def test_self_test_refused_as_degenerate(self):
        """left == right makes the unrestricted design perfectly
        collinear (left's lags duplicate right's lags) → a typed
        refusal, never a meaningless F."""
        left, _, lv, _ = _causal_pair()
        twin = _series("twin", dates=_DATES, values=lv)
        with pytest.raises(GrangerCausalityError):
            granger_causality(
                left, twin, params=GrangerCausalityParams(n_lags=2),
            )

    def test_perfect_fit_unbounded_f_refused(self):
        """right_t = left_{t−1} EXACTLY (no noise): the unrestricted
        model reproduces the response to machine precision, the F is
        unbounded/meaningless → typed refusal (the guard is relative
        machine-epsilon, since float noise keeps SSR_u just above an
        exact 0)."""
        rng = np.random.RandomState(5)
        lv = rng.randn(_N)
        rv = np.roll(lv, 1)
        rv[0] = 0.0
        left = _series("dl", dates=_DATES, values=lv)
        right = _series("dr", dates=_DATES, values=rv)
        with pytest.raises(GrangerCausalityError, match="perfectly"):
            granger_causality(
                left, right, params=GrangerCausalityParams(n_lags=1),
            )


# ===========================================================================
# 4. OPR11 — units / frequency / missingness
# ===========================================================================


class TestStructuralMetadata:
    def test_cross_unit_inputs_accepted_units_recorded(self):
        left, right, lv, _ = _causal_pair()
        left_bps = _series(
            "db", dates=_DATES, values=lv, units=TimeSeriesUnits.BPS,
        )
        out = granger_causality(
            left_bps, right, params=GrangerCausalityParams(n_lags=2),
        )
        head = out.lineage.steps[-1]
        assert head.params["left_units"] == "bps"
        assert head.params["right_units"] == "percent"
        assert out.units == TimeSeriesUnits.RATIO

    def test_frequency_mismatch_strict_raises_and_opt_in(self):
        left, right, lv, rv = _causal_pair()
        l2 = _series("l2", dates=_DATES, values=lv, frequency="B")
        r2 = _series("r2", dates=_DATES, values=rv, frequency="W")
        with pytest.raises(
            GrangerCausalityError, match="incompatible frequencies",
        ):
            granger_causality(
                l2, r2, params=GrangerCausalityParams(n_lags=2),
            )
        out = granger_causality(
            l2, r2,
            params=GrangerCausalityParams(
                n_lags=2, require_matching_frequency=False,
            ),
        )
        assert isinstance(out, ScalarMetric)

    def test_missingness_mismatch_strict_raises_and_opt_in(self):
        left, right, _, rv = _causal_pair()
        r2 = _series(
            "r2", dates=_DATES, values=rv,
            missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(GrangerCausalityError, match="missingness"):
            granger_causality(
                left, r2, params=GrangerCausalityParams(n_lags=2),
            )
        out = granger_causality(
            left, r2,
            params=GrangerCausalityParams(
                n_lags=2, require_matching_missingness=False,
            ),
        )
        assert isinstance(out, ScalarMetric)


# ===========================================================================
# 5. OPR10 lineage diagnostics + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_records_test_diagnostics(self):
        left, right, _, _ = _causal_pair()
        out = granger_causality(
            left, right, params=GrangerCausalityParams(n_lags=2),
        )
        # The output is anchored on the RESPONSE (right) chain.
        assert len(out.lineage.steps) == len(right.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "granger_causality"
        assert head.auxiliary_lineages == (left.lineage,)
        assert head.params["f_statistic"] == pytest.approx(out.value)
        assert head.params["df1"] == 2
        assert head.params["df2"] == head.params["n_obs"] - 5
        assert "left -> right" in head.params["direction"]

    def test_rerun_is_deterministic(self):
        left, right, _, _ = _causal_pair()
        p = GrangerCausalityParams(n_lags=2)
        out1 = granger_causality(left, right, params=p)
        out2 = granger_causality(left, right, params=p)
        assert out1.lineage.head_hash == out2.lineage.head_hash

    def test_n_lags_changes_identity(self):
        left, right, _, _ = _causal_pair()
        h1 = granger_causality(
            left, right, params=GrangerCausalityParams(n_lags=2),
        ).lineage.head_hash
        h2 = granger_causality(
            left, right, params=GrangerCausalityParams(n_lags=3),
        ).lineage.head_hash
        assert h1 != h2


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(43)
    n = 150
    x = rng.randn(n)
    y = np.zeros(n)
    for t in range(1, n):
        y[t] = 0.9 * x[t - 1] + rng.randn() * 0.3
    dates = pd.bdate_range("2026-01-01", periods=n)
    left = _series("sx", dates=dates, values=x, units=TimeSeriesUnits.Z_SCORE)
    right = _series("sy", dates=dates, values=y, units=TimeSeriesUnits.Z_SCORE)
    out = granger_causality(
        left, right, params=GrangerCausalityParams(n_lags=1),
    )
    assert out.value > 50.0  # strongly driven by construction


# ===========================================================================
# 7. Composition — type-gate + DAG execution
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
            n_rows: int = 80
            base_value: float = 100.0
            drift: float = 0.5
            # distinct pseudo-noise pattern per node so the two series
            # are NOT collinear (a shared pattern makes the Granger
            # design degenerate — correctly refused by the operator).
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
            workflow_id="granger_composition",
            nodes=[
                PrimitiveNode(
                    node_id="pa", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_a", "drift": 0.5,
                            "pattern_mult": 7919, "pattern_mod": 13},
                ),
                PrimitiveNode(
                    node_id="pb", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_b", "drift": -0.25,
                            "pattern_mult": 104729, "pattern_mod": 17},
                ),
                OperatorNode(node_id="align", operator_name="align_series"),
                OperatorNode(
                    node_id="sel_a", operator_name="select_from_series_set",
                    params={"series_key": "s_a"},
                ),
                OperatorNode(
                    node_id="sel_b", operator_name="select_from_series_set",
                    params={"series_key": "s_b"},
                ),
                OperatorNode(
                    node_id="g", operator_name="granger_causality",
                    params={"n_lags": 2},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="pa", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="pb", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="sel_a",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="align", target_node_id="sel_b",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="sel_a", target_node_id="g",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="sel_b", target_node_id="g",
                             target_input_slot="right"),
            ],
            terminal_node_id="g",
        )
        return wf, _resolve

    def test_type_gate_accepts_granger_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_and_matches_statsmodels(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        n = 80
        xv = np.array([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        yv = np.array([
            100.0 + i * -0.25 + ((i * 104729) % 17) * 0.25 for i in range(n)
        ])
        f_ref, _ = _statsmodels_reference(xv, yv, 2)
        assert terminal.value == pytest.approx(f_ref, rel=1e-9)
        assert terminal.lineage.steps[-1].name == "granger_causality"
        clear_tool_config_cache()
