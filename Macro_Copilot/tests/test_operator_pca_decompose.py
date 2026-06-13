"""Tests for shared.operators.pca_decompose — Track-A A4.

Covers the contract surface every standard operator must satisfy:

  - parity vs the shared/quant numeric (the factor scores; loadings +
    explained-variance in lineage)
  - the SeriesSet output contract (keys pc1..pcK, FACTOR_LEVEL units,
    full Panel index, frequency=None, RawNoCleaning)
  - order-insensitive NaN-row drop -> NaN factor on dropped dates
  - refusals: non-Panel; params=None (n_components); < 2 features;
    n_components out of [1, n_features]; too few rows; zero-var column
  - OPR8 n_components bounds; OPR14 determinism; OPR6 non-rates;
    composition Panel->pca_decompose DAG
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from shared.artifacts.lineage import AdapterStep, FetchStep, Lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel, SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.pca_decompose import (
    PcaDecomposeError,
    PcaDecomposeParams,
    pca_decompose,
)
from shared.quant.pca import fit_pca


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


def _panel(
    frame: pd.DataFrame,
    *,
    key: str = "p",
    unit: TimeSeriesUnits = TimeSeriesUnits.RATIO,
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


def _feature_frame(seed=0, n=300, cols=("feat_a", "feat_b", "feat_c")):
    rng = np.random.RandomState(seed)
    a = rng.randn(n)
    b = 0.9 * a + 0.1 * rng.randn(n)
    c = rng.randn(n) * 0.5
    return pd.DataFrame(
        np.column_stack([a, b, c]),
        index=pd.bdate_range("2020-01-01", periods=n),
        columns=list(cols),
    )


# ===========================================================================
# 1. Parity + output contract
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_and_output_contract(self):
        frame = _feature_frame()
        p = _panel(frame)
        out = pca_decompose(p, params=PcaDecomposeParams(n_components=2))
        # The quant fits on the SORTED-column matrix.
        x = frame[sorted(frame.columns)].to_numpy(dtype=float)
        ref = fit_pca(x, 2)
        assert isinstance(out, SeriesSet)  # OPR2 constant output type
        assert list(out.series_by_key.keys()) == ["pc1", "pc2"]
        assert all(
            u == TimeSeriesUnits.FACTOR_LEVEL
            for u in out.units_by_key.values()
        )
        assert out.common_index.equals(frame.index)
        assert out.frequency is None
        np.testing.assert_allclose(
            out.series_by_key["pc1"].to_numpy(), ref.factor_scores[:, 0],
        )
        np.testing.assert_allclose(
            out.series_by_key["pc2"].to_numpy(), ref.factor_scores[:, 1],
        )

    def test_lineage_records_loadings_and_locks(self):
        p = _panel(_feature_frame())
        out = pca_decompose(p, params=PcaDecomposeParams(n_components=2))
        head = out.lineage.steps[-1]
        assert head.name == "pca_decompose"
        assert head.params["n_components"] == 2
        assert len(head.params["loadings"]) == 2
        assert len(head.params["explained_variance_ratio"]) == 2
        assert head.params["pca_kind"] == "correlation"
        assert head.params["sign_convention"] == "largest_abs_loading_positive"
        assert head.params["fit_scope"] == "full_sample"
        assert head.params["feature_columns"] == ["feat_a", "feat_b", "feat_c"]


# ===========================================================================
# 2. NaN-row drop (order-insensitive)
# ===========================================================================


def test_nan_row_gets_nan_factor():
    frame = _feature_frame()
    frame.iloc[50, 0] = np.nan
    frame.iloc[120, 2] = np.nan
    p = _panel(frame)
    out = pca_decompose(p, params=PcaDecomposeParams(n_components=2))
    assert np.isnan(out.series_by_key["pc1"].iloc[50])
    assert np.isnan(out.series_by_key["pc1"].iloc[120])
    head = out.lineage.steps[-1]
    assert head.params["n_dropped_rows"] == 2
    assert head.params["n_complete_rows"] == len(frame) - 2


# ===========================================================================
# 3. Refusals + discipline
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_params_none_refused_naming_field(self):
        p = _panel(_feature_frame())
        with pytest.raises(PcaDecomposeError, match="n_components"):
            pca_decompose(p)

    def test_non_panel_input_raises(self):
        with pytest.raises(PcaDecomposeError, match="must be a Panel"):
            pca_decompose(
                0.5,  # type: ignore[arg-type]
                params=PcaDecomposeParams(n_components=2),
            )

    def test_single_feature_refused(self):
        p = _panel(_feature_frame()[["feat_a"]])
        with pytest.raises(PcaDecomposeError, match="at least 2 feature"):
            pca_decompose(p, params=PcaDecomposeParams(n_components=1))

    def test_n_components_over_features_refused(self):
        p = _panel(_feature_frame())  # 3 features
        with pytest.raises(PcaDecomposeError, match="cannot exceed"):
            pca_decompose(p, params=PcaDecomposeParams(n_components=4))

    def test_too_few_complete_rows_refused(self):
        frame = _feature_frame(n=10)  # floor is max(12, 4) = 12
        p = _panel(frame)
        with pytest.raises(PcaDecomposeError, match="complete-case rows"):
            pca_decompose(p, params=PcaDecomposeParams(n_components=2))

    def test_zero_variance_column_refused(self):
        frame = _feature_frame()
        frame["feat_b"] = 5.0  # constant
        p = _panel(frame)
        with pytest.raises(PcaDecomposeError, match="zero variance"):
            pca_decompose(p, params=PcaDecomposeParams(n_components=2))

    def test_n_components_bounds_at_schema(self):
        with pytest.raises(ValueError):
            PcaDecomposeParams(n_components=0)
        with pytest.raises(ValueError):
            PcaDecomposeParams()  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        p = _panel(_feature_frame())
        prm = PcaDecomposeParams(n_components=2)
        assert (
            pca_decompose(p, params=prm).lineage.head_hash
            == pca_decompose(p, params=prm).lineage.head_hash
        )

    def test_n_components_changes_identity(self):
        p = _panel(_feature_frame())
        h1 = pca_decompose(
            p, params=PcaDecomposeParams(n_components=1),
        ).lineage.head_hash
        h2 = pca_decompose(
            p, params=PcaDecomposeParams(n_components=2),
        ).lineage.head_hash
        assert h1 != h2


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates generic feature names)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    frame = _feature_frame(seed=9, cols=("sensor_x", "sensor_y", "sensor_z"))
    p = _panel(frame, unit=TimeSeriesUnits.RATIO)
    out = pca_decompose(p, params=PcaDecomposeParams(n_components=2))
    assert isinstance(out, SeriesSet)
    assert all(
        u == TimeSeriesUnits.FACTOR_LEVEL for u in out.units_by_key.values()
    )


# ===========================================================================
# 5. Composition — primitives → pairwise_spread_matrix(Panel) → pca
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

        # 3 primitives -> align_series -> pairwise_spread_matrix (Panel,
        # rank-2: spread_AC = spread_AB + spread_BC) -> pca_decompose(2).
        wf = Workflow(
            workflow_id="pca_decompose_composition",
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
                    node_id="pca", operator_name="pca_decompose",
                    params={"n_components": 2},
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
                WorkflowEdge(source_node_id="mat", target_node_id="pca",
                             target_input_slot="features"),
            ],
            terminal_node_id="pca",
        )
        return wf, _resolve

    def test_type_gate_accepts_pca_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_factor_scores(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, SeriesSet)
        assert list(terminal.series_by_key.keys()) == ["pc1", "pc2"]
        assert all(
            u == TimeSeriesUnits.FACTOR_LEVEL
            for u in terminal.units_by_key.values()
        )
        clear_tool_config_cache()
