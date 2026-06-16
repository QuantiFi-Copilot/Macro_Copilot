"""Tests for shared.operators.fit_kalman — Track-A A4 (the last engine).

Covers the contract surface:

  - parity vs the shared/quant DLM filter (the filtered coefficient paths)
  - recovery of a KNOWN time-varying beta; fit_scope='filtered'
  - the SeriesSet output contract (keys beta_<regressor> + alpha, RATIO /
    target units, full input index, warmup-head NaN, frequency preserved)
  - two-tier NaN: contiguous edge tolerated; INTERIOR refused
  - refusals: non-SeriesSet; params=None; target_key absent; no regressor;
    too few rows; rank-deficient design
  - OPR8 bounds; OPR14 determinism; OPR6 non-rates; composition
    primitives -> align_series(SeriesSet) -> fit_kalman
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from shared.artifacts.lineage import AdapterStep, FetchStep, Lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.fit_kalman import (
    FitKalmanError,
    FitKalmanParams,
    fit_kalman,
)
from shared.quant.kalman import dlm_filter


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


def _lineage(key: str, unit: TimeSeriesUnits) -> Lineage:
    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0.0",
        params={"series_key": key},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series", version="1.0.0",
        params={"series_key": key, "units": unit.value},
        input_hashes=(fetch.hash,),
    )
    return Lineage.from_steps([fetch, adapter])


def _series_set(
    frame: pd.DataFrame,
    *,
    unit: TimeSeriesUnits = TimeSeriesUnits.RATIO,
    frequency="B",
) -> SeriesSet:
    cols = [str(c) for c in frame.columns]
    return SeriesSet(
        series_by_key={c: frame[c].astype(float) for c in cols},
        units_by_key={c: unit for c in cols},
        missingness_by_key={c: RawNoCleaning() for c in cols},
        upstream_lineage_by_key={c: _lineage(c, unit) for c in cols},
        common_index=frame.index,
        frequency=frequency,
        lineage=_lineage("set", unit),
    )


def _tvp_frame(seed=0, n=600, cols=("y", "x1", "x2")):
    """A frame whose target ``y`` has a STEP in its beta on x1."""
    rng = np.random.RandomState(seed)
    x1 = rng.randn(n)
    x2 = rng.randn(n)
    beta = np.where(np.arange(n) < n // 2, 0.5, 2.0)
    y = beta * x1 + 0.3 * x2 + 0.05 * rng.randn(n)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame({cols[0]: y, cols[1]: x1, cols[2]: x2}, index=idx)


# ===========================================================================
# 1. Parity + recovery + output contract
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_and_output_contract(self):
        frame = _tvp_frame()
        ss = _series_set(frame)
        out = fit_kalman(
            ss,
            params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.05,
                basis="raw_value",
            ),
        )
        # Oracle: regressors sorted, constant last (the operator's order).
        X = np.column_stack([
            frame["x1"].to_numpy(), frame["x2"].to_numpy(),
            np.ones(len(frame)),
        ])
        ref = dlm_filter(frame["y"].to_numpy(dtype=float), X, 0.05)
        assert isinstance(out, SeriesSet)  # OPR2 constant output type
        assert list(out.series_by_key.keys()) == [
            "beta_x1", "beta_x2", "alpha",
        ]
        assert out.units_by_key["beta_x1"] == TimeSeriesUnits.RATIO
        assert out.units_by_key["alpha"] == TimeSeriesUnits.RATIO
        assert out.common_index.equals(frame.index)
        assert out.frequency == "B"
        np.testing.assert_array_equal(
            out.series_by_key["beta_x1"].to_numpy(), ref.filtered_states[:, 0],
        )

    def test_recovers_time_varying_beta(self):
        frame = _tvp_frame()
        out = fit_kalman(
            _series_set(frame),
            params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.05,
                basis="raw_value",
            ),
        )
        b = out.series_by_key["beta_x1"]
        assert b.iloc[100:280].mean() == pytest.approx(0.5, abs=0.25)
        assert b.iloc[420:580].mean() == pytest.approx(2.0, abs=0.25)

    def test_warmup_head_is_nan(self):
        out = fit_kalman(
            _series_set(_tvp_frame()),
            params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.05,
                basis="raw_value",
            ),
        )
        b = out.series_by_key["beta_x1"]
        # n_params = 2 regressors + constant = 3 ⇒ warmup = 2.
        assert b.iloc[:2].isna().all()
        assert b.iloc[2:].notna().all()

    def test_lineage_records_locks_and_scope(self):
        out = fit_kalman(
            _series_set(_tvp_frame()),
            params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.05,
                basis="raw_value",
            ),
        )
        head = out.lineage.steps[-1].params
        assert head["fit_scope"] == "filtered"          # NOT smoothed
        assert head["state_model"] == "random_walk_coefficients"
        assert head["estimator"] == "dlm_forward_filter"
        assert head["noise_calibration"] == "full_sample_ols_residual_variance"
        assert head["target_key"] == "y"
        assert head["regressor_keys"] == ["x1", "x2"]
        assert head["output_keys"] == ["beta_x1", "beta_x2", "alpha"]
        assert head["signal_to_noise_ratio"] == 0.05
        assert len(head["static_ols_coef"]) == 3
        assert head["observation_variance"] > 0.0

    def test_level_change_basis_loses_one_warmup_row(self):
        # level_change diffs every member ⇒ the first finite row drops,
        # so the first emitted index shifts by one extra row.
        out = fit_kalman(
            _series_set(_tvp_frame()),
            params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.05,
                basis="level_change",
            ),
        )
        head = out.lineage.steps[-1].params
        assert head["basis"] == "level_change"
        assert head["n_leading_nan"] == 1  # the diff drop
        # row 0 (the dropped diff) + the warmup are NaN.
        assert out.series_by_key["beta_x1"].iloc[0] != out.series_by_key[
            "beta_x1"
        ].iloc[0]  # NaN

    def test_add_constant_false_drops_alpha_and_one_warmup_row(self):
        """m24 / OPR16.2: the ``add_constant=False`` branch is lineage-
        affecting and was previously untested (every other test uses the
        True default).  Without the intercept the output-key set flips to
        EXACTLY [beta_x1, beta_x2] (NO ``alpha``), and ``n_params`` drops by
        one, so the diffuse-prior warmup shrinks to ``n_regressors - 1``."""
        out = fit_kalman(
            _series_set(_tvp_frame()),
            params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.05,
                basis="raw_value", add_constant=False,
            ),
        )
        # Output keys: one beta per regressor, and NO alpha intercept.
        assert list(out.series_by_key.keys()) == ["beta_x1", "beta_x2"]
        assert "alpha" not in out.series_by_key

        head = out.lineage.steps[-1].params
        assert head["add_constant"] is False
        assert head["output_keys"] == ["beta_x1", "beta_x2"]
        n_regressors = len(head["regressor_keys"])  # == 2 (x1, x2)
        # add_constant=False ⇒ n_params == n_regressors (no intercept column),
        # so the warmup head is n_regressors - 1 rows (vs n_regressors with
        # the constant).  static_ols_coef has one fewer entry too.
        assert head["n_params"] == n_regressors
        assert head["n_warmup"] == n_regressors - 1
        assert len(head["static_ols_coef"]) == n_regressors
        # Concretely, raw_value basis ⇒ exactly the first (n_regressors - 1)
        # rows are the warmup NaN, the rest finite.
        beta = out.series_by_key["beta_x1"]
        assert beta.iloc[:n_regressors - 1].isna().all()
        assert beta.iloc[n_regressors - 1:].notna().all()

    def test_filter_is_causal_truncation_invariant_given_fixed_R(self):
        """m23 / OPR16.2: the FILTER-not-smoother property at the OPERATOR
        level (the byte-identical truncation proof previously lived only at
        the quant layer).  The operator auto-calibrates the noise scale R
        full-sample (the disclosed exception), so we PIN R via the engine
        and prove the recursion is prefix-only: the operator's emitted
        ``beta_x1[:t]`` is BYTE-IDENTICAL (np.array_equal on the non-NaN
        tail) to an independent engine run on the input TRUNCATED after t
        with that same R pinned.  A smoother (which peeks ahead) would
        differ.  Not circular: the comparison is a SEPARATE dlm_filter
        call on a TRUNCATED input, not a re-call of the operator."""
        frame = _tvp_frame(n=600)
        ss = _series_set(frame)
        params = FitKalmanParams(
            target_key="y", signal_to_noise_ratio=0.05,
            basis="raw_value", add_constant=True,
        )
        out = fit_kalman(ss, params=params)
        head = out.lineage.steps[-1].params
        R = float(head["observation_variance"])  # the calibrated noise scale

        # Reconstruct the operator's OWN (y, X) for raw_value basis with a
        # trailing constant column (the operator's coefficient order is
        # [regressors..., alpha]); no leading/trailing NaN in this fixture.
        y = frame["y"].to_numpy(dtype=float)
        X = np.column_stack([
            frame["x1"].to_numpy(dtype=float),
            frame["x2"].to_numpy(dtype=float),
            np.ones(len(frame), dtype=float),
        ])

        t0 = 400
        # Engine run on the TRUNCATED prefix, R PINNED so the only change is
        # the sample length (Q = snr * R is therefore identical too).
        trunc = dlm_filter(
            y[: t0 + 1], X[: t0 + 1], 0.05, observation_variance=R,
        )
        # beta_x1 is the FIRST state (regressor order is [x1, x2, alpha]).
        op_beta_x1 = out.series_by_key["beta_x1"].to_numpy(dtype=float)
        trunc_beta_x1 = trunc.filtered_states[:, 0]

        # Compare on the non-NaN tail of the prefix [:t0+1] — byte-identical.
        op_prefix = op_beta_x1[: t0 + 1]
        finite = ~np.isnan(op_prefix)
        assert finite.sum() > 0  # there IS a finite tail to compare
        np.testing.assert_array_equal(
            op_prefix[finite], trunc_beta_x1[finite],
        )


# ===========================================================================
# 2. Two-tier NaN
# ===========================================================================


class TestTwoTierNan:
    def test_edge_nan_tolerated(self):
        frame = _tvp_frame()
        frame.iloc[:4, frame.columns.get_loc("x1")] = np.nan
        frame.iloc[-3:, frame.columns.get_loc("y")] = np.nan
        out = fit_kalman(
            _series_set(frame),
            params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.05,
                basis="raw_value",
            ),
        )
        head = out.lineage.steps[-1].params
        assert head["n_leading_nan"] == 4
        assert head["n_trailing_nan"] == 3
        assert out.series_by_key["beta_x1"].iloc[:4].isna().all()
        assert out.series_by_key["beta_x1"].iloc[-3:].isna().all()

    def test_leading_nan_and_level_change_compose(self):
        # The fiddliest index path: L leading-NaN rows AND a level_change
        # diff (which drops the FIRST finite row).  n_leading_nan must be
        # L + 1, and exactly those rows NaN — a misalignment here would
        # silently map a filtered value to the wrong date.
        frame = _tvp_frame()
        frame.iloc[:4, frame.columns.get_loc("x1")] = np.nan
        out = fit_kalman(
            _series_set(frame),
            params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.05,
                basis="level_change",
            ),
        )
        head = out.lineage.steps[-1].params
        assert head["n_leading_nan"] == 5  # 4 leading NaN + the diff drop
        b = out.series_by_key["beta_x1"]
        assert b.iloc[:5].isna().all()
        assert b.iloc[5 + head["n_warmup"]:].notna().all()

    def test_interior_nan_refused(self):
        frame = _tvp_frame()
        frame.iloc[300, frame.columns.get_loc("x2")] = np.nan
        with pytest.raises(FitKalmanError, match="INTERIOR"):
            fit_kalman(
                _series_set(frame),
                params=FitKalmanParams(
                    target_key="y", signal_to_noise_ratio=0.05,
                    basis="raw_value",
                ),
            )


# ===========================================================================
# 3. Refusals + discipline
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_params_none_refused(self):
        with pytest.raises(FitKalmanError, match="target_key"):
            fit_kalman(_series_set(_tvp_frame()))

    def test_non_seriesset_input_raises(self):
        with pytest.raises(FitKalmanError, match="must be a SeriesSet"):
            fit_kalman(
                0.5,  # type: ignore[arg-type]
                params=FitKalmanParams(
                    target_key="y", signal_to_noise_ratio=0.05,
                ),
            )

    def test_target_key_absent_raises(self):
        with pytest.raises(FitKalmanError, match="not a"):
            fit_kalman(
                _series_set(_tvp_frame()),
                params=FitKalmanParams(
                    target_key="nope", signal_to_noise_ratio=0.05,
                ),
            )

    def test_single_member_no_regressor_refused(self):
        frame = _tvp_frame()[["y"]]
        with pytest.raises(FitKalmanError, match="at least one regressor"):
            fit_kalman(
                _series_set(frame),
                params=FitKalmanParams(
                    target_key="y", signal_to_noise_ratio=0.05,
                    basis="raw_value",
                ),
            )

    def test_too_few_rows_refused(self):
        frame = _tvp_frame(n=3)
        with pytest.raises(FitKalmanError, match="too few"):
            fit_kalman(
                _series_set(frame),
                params=FitKalmanParams(
                    target_key="y", signal_to_noise_ratio=0.05,
                    basis="raw_value",
                ),
            )

    def test_rank_deficient_design_refused(self):
        frame = _tvp_frame()
        frame["x2"] = 2.0 * frame["x1"]  # x2 collinear with x1
        with pytest.raises(FitKalmanError, match="rank-deficient"):
            fit_kalman(
                _series_set(frame),
                params=FitKalmanParams(
                    target_key="y", signal_to_noise_ratio=0.05,
                    basis="raw_value",
                ),
            )

    def test_params_bounds_at_schema(self):
        with pytest.raises(ValueError):
            FitKalmanParams(target_key="y", signal_to_noise_ratio=0.0)
        with pytest.raises(ValueError):
            FitKalmanParams(target_key="", signal_to_noise_ratio=0.05)

    def test_rerun_is_deterministic(self):
        ss = _series_set(_tvp_frame())
        prm = FitKalmanParams(
            target_key="y", signal_to_noise_ratio=0.05, basis="raw_value",
        )
        assert (
            fit_kalman(ss, params=prm).lineage.head_hash
            == fit_kalman(ss, params=prm).lineage.head_hash
        )

    def test_identity_changes_with_snr_and_target(self):
        ss = _series_set(_tvp_frame())
        h_a = fit_kalman(
            ss, params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.05, basis="raw_value",
            ),
        ).lineage.head_hash
        h_snr = fit_kalman(
            ss, params=FitKalmanParams(
                target_key="y", signal_to_noise_ratio=0.10, basis="raw_value",
            ),
        ).lineage.head_hash
        h_tgt = fit_kalman(
            ss, params=FitKalmanParams(
                target_key="x1", signal_to_noise_ratio=0.05, basis="raw_value",
            ),
        ).lineage.head_hash
        assert h_a != h_snr
        assert h_a != h_tgt


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates generic names)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    frame = _tvp_frame(seed=9, cols=("sensor_out", "sensor_a", "sensor_b"))
    out = fit_kalman(
        _series_set(frame),
        params=FitKalmanParams(
            target_key="sensor_out", signal_to_noise_ratio=0.05,
            basis="raw_value",
        ),
    )
    assert isinstance(out, SeriesSet)
    assert list(out.series_by_key.keys()) == [
        "beta_sensor_a", "beta_sensor_b", "alpha",
    ]


# ===========================================================================
# 5. Composition — primitives -> align_series(SeriesSet) -> fit_kalman
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
            n_rows: int = 120
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
            bdays = pd.bdate_range("2026-01-01", periods=params.n_rows)
            values = [
                params.base_value + i * params.drift
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
            workflow_id="fit_kalman_composition",
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
                PrimitiveNode(
                    node_id="p3", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "u3", "drift": 0.1,
                            "pattern_mult": 1299709, "pattern_mod": 11},
                ),
                OperatorNode(node_id="align", operator_name="align_series"),
                OperatorNode(
                    node_id="kal", operator_name="fit_kalman",
                    params={"target_key": "u1",
                            "signal_to_noise_ratio": 0.01,
                            "basis": "level_change"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p1", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p2", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p3", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="kal",
                             target_input_slot="series_set"),
            ],
            terminal_node_id="kal",
        )
        return wf, _resolve

    def test_type_gate_accepts_fit_kalman_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_time_varying_betas(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, SeriesSet)
        assert "beta_u2" in terminal.series_by_key
        assert "alpha" in terminal.series_by_key
        clear_tool_config_cache()
