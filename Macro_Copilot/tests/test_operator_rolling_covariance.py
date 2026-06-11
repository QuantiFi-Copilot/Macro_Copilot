"""Tests for shared.operators.rolling_covariance — Track-A A2 operator.

Covers the contract surface every standard operator must satisfy:

  - happy path vs pandas rolling().cov() with ddof variants
  - OPR2 constant output type (always Series)
  - OPR8 params=None resolves from config; schema defaults mirror YAML;
    min_periods<=window cross-field invariant
  - OPR9 typed I/O (non-Series input refused)
  - OPR10 lineage extension + OPR14 rerun determinism
  - OPR11 strict-by-default units (unit-BEARING family doctrine) +
    frequency + missingness, each with explicit opt-in; honest
    CombinedMissingnessV1 under the missingness opt-out
  - OPR13 typed refusals (misaligned index, all-NaN output) — and the
    deliberate NON-error: a constant window yields 0.0, not NaN
  - OPR6 finance-blindness (non-rates synthetic data)
  - composition: the type-gate accepts rolling_covariance as a DAG
    node (incl. the validate-time unit hook + min_periods param-sanity
    hook) and a small synthetic-primitive DAG executes end-to-end
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
from shared.artifacts.missingness import CombinedMissingnessV1
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.rolling_covariance import (
    CONFIG_PATH,
    RollingCovarianceError,
    RollingCovarianceParams,
    rolling_covariance,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


# ===========================================================================
# Helpers
# ===========================================================================


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


_N = 30
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _pair(seed: int = 3):
    rng = np.random.RandomState(seed)
    xv = rng.randn(_N).cumsum() + 10.0
    yv = 0.8 * xv + rng.randn(_N) * 0.3
    return (
        _series("a", dates=_DATES, values=xv),
        _series("b", dates=_DATES, values=yv),
        pd.Series(xv, index=_DATES),
        pd.Series(yv, index=_DATES),
    )


# ===========================================================================
# 1. Happy path + ddof variants + constant output type
# ===========================================================================


class TestHappyPath:
    def test_matches_pandas_rolling_cov(self):
        a, b, xs, ys = _pair()
        out = rolling_covariance(a, b, params=RollingCovarianceParams(window=10))
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.RATIO
        expected = xs.rolling(10, min_periods=10).cov(ys, ddof=1)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )

    def test_population_ddof_zero_differs(self):
        a, b, xs, ys = _pair()
        out0 = rolling_covariance(
            a, b, params=RollingCovarianceParams(window=10, ddof=0),
        )
        expected0 = xs.rolling(10, min_periods=10).cov(ys, ddof=0)
        pd.testing.assert_series_equal(
            out0.payload, expected0, check_names=False,
        )
        out1 = rolling_covariance(
            a, b, params=RollingCovarianceParams(window=10, ddof=1),
        )
        valid = out0.payload.dropna()
        assert not np.allclose(
            valid.to_numpy(),
            out1.payload.dropna().to_numpy(),
        )

    def test_warmup_is_nan_then_values(self):
        a, b, _, _ = _pair()
        out = rolling_covariance(a, b, params=RollingCovarianceParams(window=10))
        assert out.payload.iloc[: 9].isna().all()
        assert out.payload.iloc[9:].notna().all()

    def test_min_periods_allows_earlier_values(self):
        a, b, xs, ys = _pair()
        out = rolling_covariance(
            a, b, params=RollingCovarianceParams(window=10, min_periods=3),
        )
        expected = xs.rolling(10, min_periods=3).cov(ys, ddof=1)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert out.payload.iloc[2:].notna().all()

    def test_constant_window_yields_zero_not_nan(self):
        """Deliberate divergence from rolling_correlation: a constant
        arm makes the correlation undefined but the covariance is a
        legitimate 0.0."""
        a = _series("const", dates=_DATES, values=[5.0] * _N)
        b, _, _, ys = (None, None, None, None)
        rng = np.random.RandomState(1)
        b = _series("var", dates=_DATES, values=rng.randn(_N))
        out = rolling_covariance(a, b, params=RollingCovarianceParams(window=5))
        valid = out.payload.iloc[4:]
        # Finite (not NaN) and zero to floating-point noise — pandas
        # computes the constant-arm covariance as ~1e-16 residuals.
        assert valid.notna().all()
        np.testing.assert_allclose(valid.to_numpy(), 0.0, atol=1e-12)

    def test_series_key_names_both_inputs(self):
        a, b, _, _ = _pair()
        out = rolling_covariance(a, b, params=RollingCovarianceParams(window=10))
        assert out.series_key == "rolling_cov__a__b"

    def test_nan_bearing_inputs_accepted_and_match_pandas(self):
        """Critic finding (OPR13/OPR16): the unexplained-NaN guard's
        ACCEPT direction — sporadic NaNs in both arms with
        min_periods < window must succeed (NaN rows are legitimate
        missingness, classified as explained) and match pandas'
        pairwise rolling covariance exactly."""
        rng = np.random.RandomState(23)
        xv = rng.randn(_N).cumsum() + 5.0
        yv = 0.6 * xv + rng.randn(_N) * 0.4
        xv[[3, 7, 8, 19]] = np.nan
        yv[[5, 7, 14, 25]] = np.nan
        a = _series("nx", dates=_DATES, values=xv)
        b = _series("ny", dates=_DATES, values=yv)
        out = rolling_covariance(
            a, b, params=RollingCovarianceParams(window=10, min_periods=4),
        )
        expected = (
            pd.Series(xv, index=_DATES)
            .rolling(10, min_periods=4)
            .cov(pd.Series(yv, index=_DATES), ddof=1)
        )
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )

    def test_params_none_succeeds_and_matches_explicit_config_resolution(self):
        """Critic finding (OPR8 verify bullet 2): calling with only the
        artifact inputs succeeds and matches the config defaults —
        pinned by head_hash equality against an explicitly
        config-resolved params object."""
        n = 80
        dates = pd.bdate_range("2026-01-02", periods=n)
        rng = np.random.RandomState(31)
        xv = rng.randn(n).cumsum()
        yv = 0.4 * xv + rng.randn(n) * 0.2
        a = _series("pa", dates=dates, values=xv)
        b = _series("pb", dates=dates, values=yv)
        out_default = rolling_covariance(a, b)

        cfg = load_operator_config(CONFIG_PATH)
        raw_mp = cfg.default_value("min_periods")
        explicit = RollingCovarianceParams(
            window=int(cfg.default_value("window")),
            min_periods=(int(raw_mp) if raw_mp is not None else None),
            ddof=int(cfg.default_value("ddof")),
        )
        out_explicit = rolling_covariance(a, b, params=explicit)
        assert (
            out_default.lineage.head_hash == out_explicit.lineage.head_hash
        )
        expected = (
            pd.Series(xv, index=dates)
            .rolling(60, min_periods=60)
            .cov(pd.Series(yv, index=dates), ddof=1)
        )
        pd.testing.assert_series_equal(
            out_default.payload, expected, check_names=False,
        )


# ===========================================================================
# 2. OPR8 — config defaults + schema mirror + cross-field invariant
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_resolves_from_config(self):
        # config default window=60 > 30 rows → all-NaN → typed refusal,
        # which proves the YAML default (not some schema fallback) was
        # used on the params=None path.
        a, b, _, _ = _pair()
        with pytest.raises(RollingCovarianceError, match="window=60"):
            rolling_covariance(a, b)

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = RollingCovarianceParams()
        assert p.window == int(cfg.default_value("window"))
        assert p.ddof == int(cfg.default_value("ddof"))
        raw_mp = cfg.default_value("min_periods")
        assert p.min_periods == (int(raw_mp) if raw_mp is not None else None)

    def test_min_periods_above_window_rejected_at_schema(self):
        with pytest.raises(ValueError, match="cannot exceed window"):
            RollingCovarianceParams(window=10, min_periods=11)

    def test_ddof_bounds_enforced(self):
        with pytest.raises(ValueError):
            RollingCovarianceParams(ddof=2)


# ===========================================================================
# 3. OPR13 — typed refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        a, _, _, _ = _pair()
        with pytest.raises(RollingCovarianceError, match="must be Series"):
            rolling_covariance(a, [1, 2, 3])  # type: ignore[arg-type]

    def test_misaligned_index_raises(self):
        a, _, _, _ = _pair()
        other = _series(
            "c", dates=pd.bdate_range("2027-01-04", periods=_N),
            values=list(range(_N)),
        )
        with pytest.raises(RollingCovarianceError, match="identical DatetimeIndex"):
            rolling_covariance(a, other, params=RollingCovarianceParams(window=5))

    def test_all_nan_output_raises(self):
        a, b, _, _ = _pair()
        with pytest.raises(RollingCovarianceError, match="all-NaN"):
            rolling_covariance(
                a, b, params=RollingCovarianceParams(window=100),
            )

    def test_overflow_window_raises_typed_error(self):
        """Critic finding (OPR13 / decided edge-case table): unlike a
        bounded correlation, a covariance CAN overflow on legal finite
        inputs.  pandas' streaming accumulators NaN-poison the
        overflowing windows, which a silent scrub would mislabel as
        warmup missingness — the operator must refuse with a typed
        error (matching the full-sample covariance sibling): every
        output NaN must be explained by insufficient finite pairs."""
        big = np.ones(_N)
        big[-3:] = [1e308, -1e308, 1e308]
        a = _series("big_a", dates=_DATES, values=big)
        b = _series("big_b", dates=_DATES, values=big.copy())
        with pytest.raises(RollingCovarianceError, match="overflow"):
            rolling_covariance(
                a, b, params=RollingCovarianceParams(window=3, min_periods=3),
            )


# ===========================================================================
# 4. OPR11 — units / frequency / missingness strict-by-default
# ===========================================================================


class TestStructuralMetadata:
    def test_cross_unit_strict_raises(self):
        a, b, _, _ = _pair()
        b2 = _series(
            "b2", dates=_DATES, values=b.payload.to_numpy(),
            units=TimeSeriesUnits.BPS,
        )
        with pytest.raises(RollingCovarianceError, match="convert_units"):
            rolling_covariance(a, b2, params=RollingCovarianceParams(window=10))

    def test_cross_unit_opt_in_succeeds_and_records_units(self):
        a, b, _, _ = _pair()
        b2 = _series(
            "b2", dates=_DATES, values=b.payload.to_numpy(),
            units=TimeSeriesUnits.BPS,
        )
        out = rolling_covariance(
            a, b2,
            params=RollingCovarianceParams(
                window=10, require_matching_units=False,
            ),
        )
        head = out.lineage.steps[-1]
        assert head.params["left_units"] == "percent"
        assert head.params["right_units"] == "bps"
        assert head.params["require_matching_units"] is False

    def test_frequency_mismatch_strict_raises(self):
        a, b, _, _ = _pair()
        a2 = _series("a2", dates=_DATES, values=a.payload.to_numpy(), frequency="B")
        b2 = _series("b2", dates=_DATES, values=b.payload.to_numpy(), frequency="W")
        with pytest.raises(RollingCovarianceError, match="incompatible frequencies"):
            rolling_covariance(a2, b2, params=RollingCovarianceParams(window=10))

    def test_missingness_mismatch_strict_raises(self):
        from shared.artifacts import RawNoCleaning
        a, b, _, _ = _pair()
        b2 = _series(
            "b2", dates=_DATES, values=b.payload.to_numpy(),
            missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(RollingCovarianceError, match="missingness"):
            rolling_covariance(a, b2, params=RollingCovarianceParams(window=10))

    def test_missingness_opt_out_emits_combined_policy(self):
        from shared.artifacts import RawNoCleaning
        a, b, _, _ = _pair()
        b2 = _series(
            "b2", dates=_DATES, values=b.payload.to_numpy(),
            missingness_policy=RawNoCleaning(),
        )
        out = rolling_covariance(
            a, b2,
            params=RollingCovarianceParams(
                window=10, require_matching_missingness=False,
            ),
        )
        assert isinstance(out.missingness_policy, CombinedMissingnessV1)

    def test_frequency_preserved_on_agreement(self):
        a, b, _, _ = _pair()
        a2 = _series("a2", dates=_DATES, values=a.payload.to_numpy(), frequency="B")
        b2 = _series("b2", dates=_DATES, values=b.payload.to_numpy(), frequency="B")
        out = rolling_covariance(a2, b2, params=RollingCovarianceParams(window=10))
        assert out.frequency == "B"


# ===========================================================================
# 5. OPR10 lineage + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_by_one_step(self):
        a, b, _, _ = _pair()
        out = rolling_covariance(a, b, params=RollingCovarianceParams(window=10))
        assert len(out.lineage.steps) == len(a.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "rolling_covariance"
        assert head.version == "1.0.0"
        assert head.auxiliary_lineages == (b.lineage,)
        assert head.params["window"] == 10
        assert head.params["min_periods"] == 10
        assert head.params["ddof"] == 1

    def test_rerun_is_deterministic(self):
        a, b, _, _ = _pair()
        p = RollingCovarianceParams(window=10)
        out1 = rolling_covariance(a, b, params=p)
        out2 = rolling_covariance(a, b, params=p)
        assert out1.lineage.head_hash == out2.lineage.head_hash

    def test_ddof_changes_identity(self):
        a, b, _, _ = _pair()
        h1 = rolling_covariance(
            a, b, params=RollingCovarianceParams(window=10, ddof=1),
        ).lineage.head_hash
        h0 = rolling_covariance(
            a, b, params=RollingCovarianceParams(window=10, ddof=0),
        ).lineage.head_hash
        assert h1 != h0


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(11)
    n = 80
    x = rng.randn(n).cumsum()
    y = 1.7 * x + rng.randn(n)
    dates = pd.bdate_range("2026-01-01", periods=n)
    a = _series("sx", dates=dates, values=x, units=TimeSeriesUnits.Z_SCORE)
    b = _series("sy", dates=dates, values=y, units=TimeSeriesUnits.Z_SCORE)
    out = rolling_covariance(a, b, params=RollingCovarianceParams(window=20))
    expected = pd.Series(x, index=dates).rolling(20, min_periods=20).cov(
        pd.Series(y, index=dates), ddof=1,
    )
    pd.testing.assert_series_equal(out.payload, expected, check_names=False)


# ===========================================================================
# 7. Composition — type-gate + hooks + DAG execution
# ===========================================================================


class TestComposition:
    @staticmethod
    def _synth_resolver(tmp_path, units_by_tool=None):
        from shared.schemas import TimeSeries, TimeSeriesRow
        from shared.workflow import PrimitiveSpec

        class _SynthInput(BaseModel):
            series_name: str = "s"
            n_rows: int = 40
            base_value: float = 100.0
            drift: float = 0.5
            units: str = "bps"

        class _SynthMetrics(BaseModel):
            as_of_date: str

        class _SynthOutput(BaseModel):
            current_metrics: _SynthMetrics
            time_series: TimeSeries

        def _synth(*, engine, params: _SynthInput, config) -> dict:
            bdays = pd.bdate_range("2026-04-01", periods=params.n_rows)
            values = [
                params.base_value + (i % 7) * params.drift
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
        units_by_tool = units_by_tool or {"synthetic_primitive_tool": None}
        specs = {}
        for tool, declared in units_by_tool.items():
            kwargs = dict(
                tool_name=tool, callable=_synth,
                input_class=_SynthInput, output_class=_SynthOutput,
                config_path=cfg,
            )
            if declared is not None:
                kwargs["output_field_units"] = {"time_series": declared}
            specs[tool] = PrimitiveSpec(**kwargs)

        def _resolve(tool_name: str) -> PrimitiveSpec:
            return specs[tool_name]

        return _resolve

    @staticmethod
    def _dag(node_params):
        from shared.workflow import (
            OperatorNode,
            PrimitiveNode,
            Workflow,
            WorkflowEdge,
        )
        return Workflow(
            workflow_id="rolling_covariance_composition",
            nodes=[
                PrimitiveNode(
                    node_id="pa", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_a", "drift": 0.5},
                ),
                PrimitiveNode(
                    node_id="pb", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_b", "drift": -0.25},
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
                    node_id="rcov", operator_name="rolling_covariance",
                    params=node_params,
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
                WorkflowEdge(source_node_id="sel_a", target_node_id="rcov",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="sel_b", target_node_id="rcov",
                             target_input_slot="right"),
            ],
            terminal_node_id="rcov",
        )

    def test_type_gate_accepts_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf = self._dag({"window": 10})
        validate_workflow(wf, primitive_resolver=self._synth_resolver(tmp_path))

    def test_param_sanity_hook_rejects_min_periods_above_window(self, tmp_path):
        from shared.workflow import WorkflowValidationError, validate_workflow
        wf = self._dag({"window": 10, "min_periods": 50})
        with pytest.raises(WorkflowValidationError, match="min_periods"):
            validate_workflow(
                wf, primitive_resolver=self._synth_resolver(tmp_path),
            )

    def test_unit_hook_rejects_declared_mismatch_at_validate_time(self, tmp_path):
        from shared.workflow import (
            OperatorNode,
            PrimitiveNode,
            Workflow,
            WorkflowEdge,
            WorkflowValidationError,
            validate_workflow,
        )
        resolver = self._synth_resolver(
            tmp_path,
            units_by_tool={"tool_pct": "percent", "tool_bps": "bps"},
        )
        wf = Workflow(
            workflow_id="rcov_mixed_units",
            nodes=[
                PrimitiveNode(node_id="pa", tool_name="tool_pct",
                              output_field="time_series", params={}),
                PrimitiveNode(node_id="pb", tool_name="tool_bps",
                              output_field="time_series", params={}),
                OperatorNode(node_id="rcov",
                             operator_name="rolling_covariance",
                             params={"window": 10}),
            ],
            edges=[
                WorkflowEdge(source_node_id="pa", target_node_id="rcov",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="pb", target_node_id="rcov",
                             target_input_slot="right"),
            ],
            terminal_node_id="rcov",
        )
        with pytest.raises(WorkflowValidationError, match="matching units"):
            validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_and_matches_direct_pandas(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf = self._dag({"window": 10})
        result = execute_workflow(
            wf, engine=None,
            primitive_resolver=self._synth_resolver(tmp_path),
        )
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        n = 40
        xs = pd.Series([100.0 + (i % 7) * 0.5 for i in range(n)])
        ys = pd.Series([100.0 + (i % 7) * -0.25 for i in range(n)])
        expected = xs.rolling(10, min_periods=10).cov(ys, ddof=1)
        np.testing.assert_allclose(
            terminal.payload.to_numpy(), expected.to_numpy(),
        )
        assert terminal.lineage.steps[-1].name == "rolling_covariance"
        clear_tool_config_cache()
