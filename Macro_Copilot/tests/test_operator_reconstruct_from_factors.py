"""Tests for shared.operators.reconstruct_from_factors — Track-A A4.

Covers the contract surface every standard operator must satisfy:

  - recovery: the residual of a target that is a factor-combo + a known
    eps recovers eps; K = n_features -> ~zero residual
  - UNITS PASSTHROUGH (the target column's units, NOT FACTOR_LEVEL);
    series_key pca_residual__<target>; full Panel index
  - order-insensitive NaN-row drop -> NaN residual on dropped dates
  - refusals: non-Panel; params=None; bad target_column; < 2 features;
    n_components out of range; too few rows; zero-var column
  - OPR8 bounds; OPR14 determinism; OPR6 non-rates; composition
    Panel->reconstruct_from_factors DAG
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from shared.artifacts.lineage import AdapterStep, FetchStep, Lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.reconstruct_from_factors import (
    ReconstructFromFactorsError,
    ReconstructFromFactorsParams,
    reconstruct_from_factors,
)
from shared.quant.pca import fit_pca, reconstruct_residual


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


def _panel(
    frame: pd.DataFrame,
    *,
    key: str = "p",
    unit: TimeSeriesUnits = TimeSeriesUnits.BPS,
) -> Panel:
    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0.0",
        params={"series_key": key},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series", version="1.0.0",
        params={"series_key": key, "units": unit.value},
        input_hashes=(fetch.hash,),
    )
    return Panel(
        payload=frame,
        units_by_column={str(c): unit for c in frame.columns},
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


def _factor_frame(seed=0, n=300, cols=("feat_a", "feat_b", "feat_c")):
    rng = np.random.RandomState(seed)
    f1, f2 = rng.randn(n), rng.randn(n)
    eps = 0.05 * rng.randn(n)
    target = 1.5 * f1 - 0.8 * f2 + eps  # the 3rd column = factor combo + eps
    return pd.DataFrame(
        np.column_stack([f1, f2, target]),
        index=pd.bdate_range("2020-01-01", periods=n),
        columns=list(cols),
    ), eps


# ===========================================================================
# 1. Recovery + output contract
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_and_recovers_eps(self):
        frame, eps = _factor_frame()
        p = _panel(frame)
        out = reconstruct_from_factors(
            p, params=ReconstructFromFactorsParams(
                n_components=2, target_column="feat_c"),
        )
        # Parity vs the quant on the sorted-column matrix.
        x = frame[sorted(frame.columns)].to_numpy(dtype=float)
        fit = fit_pca(x, 2)
        ref = reconstruct_residual(fit, x, sorted(frame.columns).index("feat_c"))
        assert isinstance(out, Series)  # OPR2 constant output type
        np.testing.assert_allclose(out.payload.to_numpy(), ref)
        # The residual recovers the planted eps.
        assert np.corrcoef(out.payload.to_numpy(), eps)[0, 1] > 0.99
        assert out.series_key == "pca_residual__feat_c"
        assert len(out.payload) == len(frame)
        assert out.frequency is None

    def test_units_passthrough_not_factor_level(self):
        frame, _ = _factor_frame()
        p = _panel(frame, unit=TimeSeriesUnits.PERCENT)
        out = reconstruct_from_factors(
            p, params=ReconstructFromFactorsParams(
                n_components=2, target_column="feat_a"),
        )
        assert out.units == TimeSeriesUnits.PERCENT  # the target's units
        assert out.units != TimeSeriesUnits.FACTOR_LEVEL

    def test_zero_residual_at_full_rank(self):
        frame, _ = _factor_frame()
        p = _panel(frame)
        out = reconstruct_from_factors(
            p, params=ReconstructFromFactorsParams(
                n_components=3, target_column="feat_c"),
        )
        assert np.abs(out.payload.to_numpy()).max() < 1e-8

    def test_lineage_records_target_and_locks(self):
        p = _panel(_factor_frame()[0])
        out = reconstruct_from_factors(
            p, params=ReconstructFromFactorsParams(
                n_components=2, target_column="feat_c"),
        )
        head = out.lineage.steps[-1]
        assert head.name == "reconstruct_from_factors"
        assert head.params["target_column"] == "feat_c"
        assert head.params["n_components"] == 2
        assert head.params["pca_kind"] == "correlation"
        assert head.params["fit_scope"] == "full_sample"
        assert (
            head.params["residual_definition"]
            == "observed_minus_topk_reconstruction"
        )


# ===========================================================================
# 2. NaN-row drop (order-insensitive)
# ===========================================================================


def test_nan_row_gets_nan_residual():
    frame, _ = _factor_frame()
    frame.iloc[50, 0] = np.nan
    frame.iloc[120, 2] = np.nan
    p = _panel(frame)
    out = reconstruct_from_factors(
        p, params=ReconstructFromFactorsParams(
            n_components=2, target_column="feat_c"),
    )
    assert np.isnan(out.payload.iloc[50])
    assert np.isnan(out.payload.iloc[120])
    assert out.lineage.steps[-1].params["n_dropped_rows"] == 2


# ===========================================================================
# 3. Refusals + discipline
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_params_none_refused(self):
        p = _panel(_factor_frame()[0])
        with pytest.raises(ReconstructFromFactorsError, match="n_components"):
            reconstruct_from_factors(p)

    def test_non_panel_input_raises(self):
        with pytest.raises(ReconstructFromFactorsError, match="must be a Panel"):
            reconstruct_from_factors(
                0.5,  # type: ignore[arg-type]
                params=ReconstructFromFactorsParams(
                    n_components=2, target_column="x"),
            )

    def test_bad_target_column_refused(self):
        p = _panel(_factor_frame()[0])
        with pytest.raises(ReconstructFromFactorsError, match="not one of"):
            reconstruct_from_factors(
                p, params=ReconstructFromFactorsParams(
                    n_components=2, target_column="nope"),
            )

    def test_single_feature_refused(self):
        p = _panel(_factor_frame()[0][["feat_a"]])
        with pytest.raises(ReconstructFromFactorsError, match="at least 2"):
            reconstruct_from_factors(
                p, params=ReconstructFromFactorsParams(
                    n_components=1, target_column="feat_a"),
            )

    def test_n_components_over_features_refused(self):
        p = _panel(_factor_frame()[0])
        with pytest.raises(ReconstructFromFactorsError, match="cannot exceed"):
            reconstruct_from_factors(
                p, params=ReconstructFromFactorsParams(
                    n_components=4, target_column="feat_a"),
            )

    def test_too_few_rows_refused(self):
        frame, _ = _factor_frame(n=10)
        p = _panel(frame)
        with pytest.raises(ReconstructFromFactorsError,
                           match="complete-case rows"):
            reconstruct_from_factors(
                p, params=ReconstructFromFactorsParams(
                    n_components=2, target_column="feat_a"),
            )

    def test_zero_variance_column_refused(self):
        frame, _ = _factor_frame()
        frame["feat_b"] = 4.0
        p = _panel(frame)
        with pytest.raises(ReconstructFromFactorsError, match="zero variance"):
            reconstruct_from_factors(
                p, params=ReconstructFromFactorsParams(
                    n_components=2, target_column="feat_a"),
            )

    def test_params_bounds_at_schema(self):
        with pytest.raises(ValueError):
            ReconstructFromFactorsParams(n_components=0, target_column="x")
        with pytest.raises(ValueError):
            ReconstructFromFactorsParams(n_components=2, target_column="")

    def test_rerun_is_deterministic(self):
        p = _panel(_factor_frame()[0])
        prm = ReconstructFromFactorsParams(n_components=2, target_column="feat_c")
        assert (
            reconstruct_from_factors(p, params=prm).lineage.head_hash
            == reconstruct_from_factors(p, params=prm).lineage.head_hash
        )

    def test_identity_changes_with_target_and_k(self):
        p = _panel(_factor_frame()[0])
        h_a = reconstruct_from_factors(
            p, params=ReconstructFromFactorsParams(
                n_components=2, target_column="feat_a"),
        ).lineage.head_hash
        h_c = reconstruct_from_factors(
            p, params=ReconstructFromFactorsParams(
                n_components=2, target_column="feat_c"),
        ).lineage.head_hash
        h_c1 = reconstruct_from_factors(
            p, params=ReconstructFromFactorsParams(
                n_components=1, target_column="feat_c"),
        ).lineage.head_hash
        assert h_a != h_c
        assert h_c != h_c1


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates generic feature names)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    frame, _ = _factor_frame(seed=9, cols=("sensor_x", "sensor_y", "sensor_z"))
    p = _panel(frame, unit=TimeSeriesUnits.RATIO)
    out = reconstruct_from_factors(
        p, params=ReconstructFromFactorsParams(
            n_components=2, target_column="sensor_z"),
    )
    assert isinstance(out, Series)
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 5. Composition — primitives → pairwise_spread_matrix(Panel) → reconstruct
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

        # 3 primitives -> align -> pairwise_spread_matrix (Panel cols
        # u1__minus__u2, u1__minus__u3, u2__minus__u3; rank-2) ->
        # reconstruct_from_factors(n_components=2, target=a spread col).
        wf = Workflow(
            workflow_id="reconstruct_from_factors_composition",
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
                OperatorNode(node_id="mat",
                             operator_name="pairwise_spread_matrix"),
                OperatorNode(
                    node_id="rec",
                    operator_name="reconstruct_from_factors",
                    params={"n_components": 2,
                            "target_column": "u1__minus__u2"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p1", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p2", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p3", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="mat",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="mat", target_node_id="rec",
                             target_input_slot="features"),
            ],
            terminal_node_id="rec",
        )
        return wf, _resolve

    def test_type_gate_accepts_reconstruct_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_residual(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.series_key == "pca_residual__u1__minus__u2"
        assert len(terminal.payload) == 60
        clear_tool_config_cache()
