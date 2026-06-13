"""Tests for shared.operators.rolling_pca — Track-A A4.

Covers the contract surface every standard operator must satisfy:

  - parity vs the shared/quant numeric (the rolling factor scores)
  - THE POINT-IN-TIME property (truncating after t leaves the score at
    t identical — the inverse of pca_decompose); fit_scope='rolling_window'
  - the SeriesSet output contract (keys pc1..pcK, FACTOR_LEVEL, full
    Panel index, warmup-head NaN, frequency=None, RawNoCleaning)
  - two-tier NaN: contiguous edge tolerated; INTERIOR refused
  - refusals: non-Panel; params=None; < 2 features; n_components out of
    range; window < n_features+1; window > the finite block; interior NaN
  - OPR8 bounds; OPR14 determinism; OPR6 non-rates; composition
    Panel->rolling_pca DAG
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
from shared.operators.rolling_pca import (
    RollingPcaError,
    RollingPcaParams,
    rolling_pca,
)
from shared.quant.pca import rolling_pca_scores


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
    drv = np.cumsum(rng.randn(n))
    return pd.DataFrame(
        np.column_stack([
            drv + 0.3 * rng.randn(n),
            drv + 0.3 * rng.randn(n),
            rng.randn(n),
        ]),
        index=pd.bdate_range("2020-01-01", periods=n),
        columns=list(cols),
    )


# ===========================================================================
# 1. Parity + point-in-time + output contract
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_and_output_contract(self):
        frame = _feature_frame()
        p = _panel(frame)
        out = rolling_pca(p, params=RollingPcaParams(window=60, n_components=2))
        x = frame[sorted(frame.columns)].to_numpy(dtype=float)
        ref = rolling_pca_scores(x, 60, 2)
        assert isinstance(out, SeriesSet)  # OPR2 constant output type
        assert list(out.series_by_key.keys()) == ["pc1", "pc2"]
        assert all(
            u == TimeSeriesUnits.FACTOR_LEVEL
            for u in out.units_by_key.values()
        )
        assert out.common_index.equals(frame.index)
        assert out.frequency is None
        np.testing.assert_array_equal(
            out.series_by_key["pc1"].to_numpy(), ref.scores[:, 0],
        )

    def test_point_in_time_no_look_ahead(self):
        # THE load-bearing property: truncating the Panel after t=150
        # leaves the rolling score at t=150 byte-identical (the inverse
        # of pca_decompose, where this would fail).
        frame = _feature_frame()
        p = _panel(frame)
        full = rolling_pca(p, params=RollingPcaParams(window=60, n_components=2))
        trunc = rolling_pca(
            _panel(frame.iloc[:151]),
            params=RollingPcaParams(window=60, n_components=2),
        )
        assert (
            full.series_by_key["pc1"].iloc[150]
            == trunc.series_by_key["pc1"].iloc[150]
        )
        assert (
            out_scope := full.lineage.steps[-1].params["fit_scope"]
        ) == "rolling_window"  # NOT full_sample

    def test_warmup_head_is_nan(self):
        p = _panel(_feature_frame())
        out = rolling_pca(p, params=RollingPcaParams(window=60, n_components=2))
        assert out.series_by_key["pc1"].iloc[:59].isna().all()
        assert out.series_by_key["pc1"].iloc[59:].notna().all()

    def test_lineage_records_window_and_locks(self):
        p = _panel(_feature_frame())
        out = rolling_pca(p, params=RollingPcaParams(window=60, n_components=2))
        head = out.lineage.steps[-1]
        assert head.name == "rolling_pca"
        assert head.params["window"] == 60
        assert head.params["n_components"] == 2
        assert head.params["fit_scope"] == "rolling_window"
        assert head.params["sign_continuity"] == "previous_window_dot_alignment"
        assert "n_sign_flips" in head.params
        assert len(head.params["last_loadings"]) == 2
        assert head.params["feature_columns"] == ["feat_a", "feat_b", "feat_c"]


# ===========================================================================
# 2. Two-tier NaN
# ===========================================================================


class TestTwoTierNan:
    def test_edge_nan_tolerated(self):
        frame = _feature_frame()
        frame.iloc[:5, 0] = np.nan
        frame.iloc[-3:, 1] = np.nan
        p = _panel(frame)
        out = rolling_pca(p, params=RollingPcaParams(window=60, n_components=2))
        # The first 5 (leading NaN) + the warmup head are NaN.
        assert out.series_by_key["pc1"].iloc[:5].isna().all()
        assert out.series_by_key["pc1"].iloc[-3:].isna().all()
        head = out.lineage.steps[-1]
        assert head.params["n_leading_nan"] == 5
        assert head.params["n_trailing_nan"] == 3

    def test_interior_nan_refused(self):
        frame = _feature_frame()
        frame.iloc[150, 0] = np.nan
        p = _panel(frame)
        with pytest.raises(RollingPcaError, match="INTERIOR"):
            rolling_pca(p, params=RollingPcaParams(window=60, n_components=2))


# ===========================================================================
# 3. Refusals + discipline
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_params_none_refused(self):
        p = _panel(_feature_frame())
        with pytest.raises(RollingPcaError, match="window"):
            rolling_pca(p)

    def test_non_panel_input_raises(self):
        with pytest.raises(RollingPcaError, match="must be a Panel"):
            rolling_pca(
                0.5,  # type: ignore[arg-type]
                params=RollingPcaParams(window=10, n_components=2),
            )

    def test_single_feature_refused(self):
        p = _panel(_feature_frame()[["feat_a"]])
        with pytest.raises(RollingPcaError, match="at least 2 feature"):
            rolling_pca(p, params=RollingPcaParams(window=10, n_components=1))

    def test_window_below_feature_floor_refused(self):
        p = _panel(_feature_frame())  # 3 features -> window must be >= 4
        with pytest.raises(RollingPcaError, match="at least n_features"):
            rolling_pca(p, params=RollingPcaParams(window=3, n_components=2))

    def test_n_components_over_features_refused(self):
        p = _panel(_feature_frame())
        with pytest.raises(RollingPcaError, match="cannot exceed"):
            rolling_pca(p, params=RollingPcaParams(window=60, n_components=4))

    def test_window_over_block_refused(self):
        p = _panel(_feature_frame(n=40))
        with pytest.raises(RollingPcaError, match="exceeds the contiguous"):
            rolling_pca(p, params=RollingPcaParams(window=60, n_components=2))

    def test_params_bounds_at_schema(self):
        with pytest.raises(ValueError):
            RollingPcaParams(window=1, n_components=2)
        with pytest.raises(ValueError):
            RollingPcaParams(window=10, n_components=0)

    def test_rerun_is_deterministic(self):
        p = _panel(_feature_frame())
        prm = RollingPcaParams(window=60, n_components=2)
        assert (
            rolling_pca(p, params=prm).lineage.head_hash
            == rolling_pca(p, params=prm).lineage.head_hash
        )

    def test_identity_changes_with_window_and_k(self):
        p = _panel(_feature_frame())
        h60 = rolling_pca(
            p, params=RollingPcaParams(window=60, n_components=2),
        ).lineage.head_hash
        h80 = rolling_pca(
            p, params=RollingPcaParams(window=80, n_components=2),
        ).lineage.head_hash
        h1 = rolling_pca(
            p, params=RollingPcaParams(window=60, n_components=1),
        ).lineage.head_hash
        assert h60 != h80
        assert h60 != h1


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates generic feature names)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    frame = _feature_frame(seed=9, cols=("sensor_x", "sensor_y", "sensor_z"))
    p = _panel(frame, unit=TimeSeriesUnits.RATIO)
    out = rolling_pca(p, params=RollingPcaParams(window=60, n_components=2))
    assert isinstance(out, SeriesSet)
    assert all(
        u == TimeSeriesUnits.FACTOR_LEVEL for u in out.units_by_key.values()
    )


# ===========================================================================
# 5. Composition — primitives → pairwise_spread_matrix(Panel) → rolling_pca
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

        wf = Workflow(
            workflow_id="rolling_pca_composition",
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
                    node_id="rpca", operator_name="rolling_pca",
                    params={"window": 20, "n_components": 2},
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
                WorkflowEdge(source_node_id="mat", target_node_id="rpca",
                             target_input_slot="features"),
            ],
            terminal_node_id="rpca",
        )
        return wf, _resolve

    def test_type_gate_accepts_rolling_pca_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_rolling_factors(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, SeriesSet)
        assert list(terminal.series_by_key.keys()) == ["pc1", "pc2"]
        clear_tool_config_cache()
