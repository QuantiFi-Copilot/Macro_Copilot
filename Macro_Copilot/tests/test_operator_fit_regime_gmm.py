"""Tests for shared.operators.fit_regime_gmm — Track-A A4 (marquee).

Covers the contract surface every standard operator must satisfy:

  - parity vs the shared/quant numeric (the per-date label sequence;
    means/weights in lineage)
  - FACTOR_LEVEL units, synthesized series_key, frequency=None, the
    full-Panel-index output
  - order-insensitive NaN-row drop -> NaN label on dropped dates
  - refusals: non-Panel; params=None (n_states); < 2 features; too few
    complete rows; zero-variance column
  - OPR8 n_states bounds; OPR14 determinism; OPR6 non-rates;
    composition Panel->fit_regime_gmm DAG
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
from shared.operators.fit_regime_gmm import (
    FitRegimeGmmError,
    FitRegimeGmmParams,
    fit_regime_gmm,
)
from shared.quant.gmm import fit_gmm_em


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


def _regime_frame(seed=0, per=100, cols=("feat_a", "feat_b")):
    rng = np.random.RandomState(seed)
    blocks = [
        rng.randn(per, 2) * 0.4 + [0.0, 0.0],
        rng.randn(per, 2) * 0.4 + [6.0, 6.0],
        rng.randn(per, 2) * 0.4 + [0.0, 6.0],
    ]
    X = np.vstack(blocks)
    dates = pd.bdate_range("2020-01-01", periods=len(X))
    return pd.DataFrame(X, index=dates, columns=list(cols))


# ===========================================================================
# 1. Parity + output contract
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_and_output_contract(self):
        frame = _regime_frame()
        p = _panel(frame)
        out = fit_regime_gmm(p, params=FitRegimeGmmParams(n_states=3))
        ref = fit_gmm_em(frame.to_numpy(dtype=float), 3)
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.FACTOR_LEVEL
        assert out.series_key == "regime_gmm__k3__2feat"
        assert out.frequency is None
        assert isinstance(out.missingness_policy, RawNoCleaning)
        assert len(out.payload) == len(frame)  # full Panel index
        np.testing.assert_array_equal(
            out.payload.to_numpy(), ref.labels.astype(float),
        )

    def test_lineage_records_fit_and_locks(self):
        p = _panel(_regime_frame())
        out = fit_regime_gmm(p, params=FitRegimeGmmParams(n_states=3))
        head = out.lineage.steps[-1]
        assert head.name == "fit_regime_gmm"
        assert head.params["n_states"] == 3
        assert len(head.params["means"]) == 3
        assert head.params["covariance_type"] == "full"
        assert head.params["init_method"] == "kmeans2_pp_seed0"
        assert head.params["label_semantics"] == "categorical_nonarithmetic"
        assert head.params["fit_scope"] == "full_sample"
        assert head.params["converged"] is True
        assert head.params["feature_columns"] == ["feat_a", "feat_b"]


# ===========================================================================
# 2. NaN-row drop (order-insensitive)
# ===========================================================================


def test_nan_row_gets_nan_label():
    frame = _regime_frame()
    frame.iloc[50, 0] = np.nan
    frame.iloc[120, 1] = np.nan
    p = _panel(frame)
    out = fit_regime_gmm(p, params=FitRegimeGmmParams(n_states=3))
    assert np.isnan(out.payload.iloc[50])
    assert np.isnan(out.payload.iloc[120])
    head = out.lineage.steps[-1]
    assert head.params["n_dropped_rows"] == 2
    assert head.params["n_complete_rows"] == len(frame) - 2
    # Parity on the complete-case rows.
    complete = frame.dropna()
    ref = fit_gmm_em(complete.to_numpy(dtype=float), 3)
    np.testing.assert_array_equal(
        out.payload.dropna().to_numpy(), ref.labels.astype(float),
    )


# ===========================================================================
# 3. Refusals + discipline
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_params_none_refused_naming_field(self):
        p = _panel(_regime_frame())
        with pytest.raises(FitRegimeGmmError, match="n_states"):
            fit_regime_gmm(p)

    def test_non_panel_input_raises(self):
        with pytest.raises(FitRegimeGmmError, match="must be a Panel"):
            fit_regime_gmm(
                0.5,  # type: ignore[arg-type]
                params=FitRegimeGmmParams(n_states=2),
            )

    def test_single_feature_refused(self):
        frame = _regime_frame()[["feat_a"]]
        p = _panel(frame)
        with pytest.raises(FitRegimeGmmError, match="at least 2 feature"):
            fit_regime_gmm(p, params=FitRegimeGmmParams(n_states=2))

    def test_too_few_complete_rows_refused(self):
        frame = _regime_frame(per=5)  # 15 rows; floor for k=3 is 30
        p = _panel(frame)
        with pytest.raises(FitRegimeGmmError, match="complete-case rows"):
            fit_regime_gmm(p, params=FitRegimeGmmParams(n_states=3))

    def test_zero_variance_column_refused(self):
        frame = _regime_frame()
        frame["feat_b"] = 7.0  # constant feature
        p = _panel(frame)
        with pytest.raises(FitRegimeGmmError, match="zero variance"):
            fit_regime_gmm(p, params=FitRegimeGmmParams(n_states=3))

    def test_n_states_bounds_at_schema(self):
        with pytest.raises(ValueError):
            FitRegimeGmmParams(n_states=1)
        with pytest.raises(ValueError):
            FitRegimeGmmParams()  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        p = _panel(_regime_frame())
        prm = FitRegimeGmmParams(n_states=3)
        assert (
            fit_regime_gmm(p, params=prm).lineage.head_hash
            == fit_regime_gmm(p, params=prm).lineage.head_hash
        )

    def test_n_states_changes_identity(self):
        p = _panel(_regime_frame())
        h2 = fit_regime_gmm(
            p, params=FitRegimeGmmParams(n_states=2),
        ).lineage.head_hash
        h3 = fit_regime_gmm(
            p, params=FitRegimeGmmParams(n_states=3),
        ).lineage.head_hash
        assert h2 != h3


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates generic feature names)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    frame = _regime_frame(seed=9, cols=("sensor_x", "sensor_y"))
    p = _panel(frame, unit=TimeSeriesUnits.RATIO)
    out = fit_regime_gmm(p, params=FitRegimeGmmParams(n_states=3))
    assert isinstance(out, Series)
    assert out.units == TimeSeriesUnits.FACTOR_LEVEL
    assert set(out.payload.dropna().astype(int)) == {0, 1, 2}


# ===========================================================================
# 5. Composition — primitives → pairwise_spread_matrix(Panel) → gmm
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

        # 3 primitives -> align_series -> pairwise_spread_matrix (Panel)
        # -> fit_regime_gmm.
        wf = Workflow(
            workflow_id="fit_regime_gmm_composition",
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
                    node_id="gmm", operator_name="fit_regime_gmm",
                    params={"n_states": 2},
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
                WorkflowEdge(source_node_id="mat", target_node_id="gmm",
                             target_input_slot="features"),
            ],
            terminal_node_id="gmm",
        )
        return wf, _resolve

    def test_type_gate_accepts_gmm_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_regime_labels(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.FACTOR_LEVEL
        assert len(terminal.payload) == 60
        clear_tool_config_cache()
