"""Tests for shared.operators.fit_regime_hmm — Track-A A4 (marquee).

Covers the contract surface every standard operator must satisfy:

  - parity vs the shared/quant numeric (the Viterbi label sequence; the
    transition matrix + initial dist in lineage)
  - FACTOR_LEVEL units, synthesized series_key, full-Panel-index output
  - TWO-TIER NaN: contiguous leading/trailing tolerated; INTERIOR
    refused (the order-sensitive divergence from the gmm twin)
  - refusals: non-Panel; params=None (n_states); < 2 features; too few
    rows; zero-variance column
  - OPR8 n_states bounds; OPR14 determinism; OPR6 non-rates;
    composition Panel->fit_regime_hmm DAG
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
from shared.operators.fit_regime_hmm import (
    FitRegimeHmmError,
    FitRegimeHmmParams,
    fit_regime_hmm,
)
from shared.quant.hmm import fit_hmm_em


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


def _hmm_frame(seed=0, n=400, sep=6.0, diag=0.95, cols=("feat_a", "feat_b")):
    rng = np.random.RandomState(seed)
    means = np.array([[0.0, 0.0], [sep, sep], [0.0, sep]])
    a = np.full((3, 3), (1.0 - diag) / 2)
    np.fill_diagonal(a, diag)
    states = np.zeros(n, dtype=int)
    for t in range(1, n):
        states[t] = rng.choice(3, p=a[states[t - 1]])
    X = np.array([means[states[t]] + rng.randn(2) * 0.4 for t in range(n)])
    return pd.DataFrame(
        X, index=pd.bdate_range("2020-01-01", periods=n), columns=list(cols),
    )


# ===========================================================================
# 1. Parity + output contract
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_and_output_contract(self):
        frame = _hmm_frame()
        p = _panel(frame)
        out = fit_regime_hmm(p, params=FitRegimeHmmParams(n_states=3))
        ref = fit_hmm_em(frame.to_numpy(dtype=float), 3)
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.FACTOR_LEVEL
        assert out.series_key == "regime_hmm__k3__2feat"
        assert out.frequency is None
        assert isinstance(out.missingness_policy, RawNoCleaning)
        assert len(out.payload) == len(frame)
        np.testing.assert_array_equal(
            out.payload.to_numpy(), ref.labels.astype(float),
        )

    def test_lineage_records_transitions_and_locks(self):
        p = _panel(_hmm_frame())
        out = fit_regime_hmm(p, params=FitRegimeHmmParams(n_states=3))
        head = out.lineage.steps[-1]
        assert head.name == "fit_regime_hmm"
        assert head.params["n_states"] == 3
        assert len(head.params["transition_matrix"]) == 3
        assert len(head.params["initial_dist"]) == 3
        assert head.params["decode_method"] == "viterbi"
        assert head.params["algorithm"] == "baum_welch"
        assert head.params["covariance_type"] == "full"
        assert head.params["label_semantics"] == "categorical_nonarithmetic"
        assert head.params["fit_scope"] == "full_sample"
        assert head.params["converged"] is True

    def test_regimes_persist(self):
        # The HMM smooths: far fewer label switches than there are rows.
        p = _panel(_hmm_frame(diag=0.97))
        out = fit_regime_hmm(p, params=FitRegimeHmmParams(n_states=3))
        switches = int((np.diff(out.payload.to_numpy()) != 0).sum())
        assert switches < len(out.payload) // 5  # persistent, not noisy


# ===========================================================================
# 2. Two-tier NaN (the order-sensitive divergence from gmm)
# ===========================================================================


class TestTwoTierNan:
    def test_edge_warmup_nan_tolerated(self):
        frame = _hmm_frame()
        frame.iloc[:8, 0] = np.nan   # leading
        frame.iloc[-4:, 1] = np.nan  # trailing
        p = _panel(frame)
        out = fit_regime_hmm(p, params=FitRegimeHmmParams(n_states=3))
        assert out.payload.iloc[:8].isna().all()
        assert out.payload.iloc[-4:].isna().all()
        assert out.payload.iloc[8:-4].notna().all()
        head = out.lineage.steps[-1]
        assert head.params["n_leading_nan"] == 8
        assert head.params["n_trailing_nan"] == 4

    def test_interior_nan_refused(self):
        frame = _hmm_frame()
        frame.iloc[200, 0] = np.nan
        p = _panel(frame)
        with pytest.raises(FitRegimeHmmError, match="INTERIOR"):
            fit_regime_hmm(p, params=FitRegimeHmmParams(n_states=3))


# ===========================================================================
# 3. Refusals + discipline
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_params_none_refused(self):
        p = _panel(_hmm_frame())
        with pytest.raises(FitRegimeHmmError, match="n_states"):
            fit_regime_hmm(p)

    def test_non_panel_input_raises(self):
        with pytest.raises(FitRegimeHmmError, match="must be a Panel"):
            fit_regime_hmm(
                0.5,  # type: ignore[arg-type]
                params=FitRegimeHmmParams(n_states=2),
            )

    def test_single_feature_refused(self):
        p = _panel(_hmm_frame()[["feat_a"]])
        with pytest.raises(FitRegimeHmmError, match="at least 2 feature"):
            fit_regime_hmm(p, params=FitRegimeHmmParams(n_states=2))

    def test_too_few_rows_refused(self):
        p = _panel(_hmm_frame(n=15))  # floor for k=3 is 30
        with pytest.raises(FitRegimeHmmError, match="contiguous finite"):
            fit_regime_hmm(p, params=FitRegimeHmmParams(n_states=3))

    def test_zero_variance_column_refused(self):
        frame = _hmm_frame()
        frame["feat_b"] = 7.0
        p = _panel(frame)
        with pytest.raises(FitRegimeHmmError, match="zero variance"):
            fit_regime_hmm(p, params=FitRegimeHmmParams(n_states=3))

    def test_n_states_bounds_at_schema(self):
        with pytest.raises(ValueError):
            FitRegimeHmmParams(n_states=1)
        with pytest.raises(ValueError):
            FitRegimeHmmParams()  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        p = _panel(_hmm_frame())
        prm = FitRegimeHmmParams(n_states=3)
        assert (
            fit_regime_hmm(p, params=prm).lineage.head_hash
            == fit_regime_hmm(p, params=prm).lineage.head_hash
        )

    def test_column_permutation_is_invariant(self):
        # O50/OPR14/P4: the marquee GMM-bug-avoidance property — the HMM
        # sorts the feature columns before the fit, so identical data in
        # PERMUTED column order produces an IDENTICAL content head_hash,
        # identical decoded labels, AND a `means` axis aligned with the
        # (sorted) feature_columns it stamps.  This pins the property so a
        # copy-paste of the raw-order to_numpy path cannot regress it
        # undetected.  REAL reproduction: permute -> fit twice -> compare.
        # Names chosen so the presented order is NOT already sorted.
        frame = _hmm_frame(cols=("z_first", "a_second"))
        frame_swapped = frame[["a_second", "z_first"]]  # same data
        assert list(frame.columns) != list(frame_swapped.columns)

        prm = FitRegimeHmmParams(n_states=3)
        out = fit_regime_hmm(_panel(frame), params=prm)
        out_swapped = fit_regime_hmm(_panel(frame_swapped), params=prm)

        # (a) identical content head_hash
        assert out.lineage.head_hash == out_swapped.lineage.head_hash
        # (b) identical decoded (Viterbi) label Series
        np.testing.assert_array_equal(
            out.payload.to_numpy(), out_swapped.payload.to_numpy(),
        )
        # (c) the means axis is consistent AND aligned with the SORTED
        #     feature_columns it stamps.
        head = out.lineage.steps[-1]
        head_swapped = out_swapped.lineage.steps[-1]
        assert head.params["feature_columns"] == ["a_second", "z_first"]
        assert (
            head_swapped.params["feature_columns"]
            == ["a_second", "z_first"]
        )
        np.testing.assert_allclose(
            np.array(head.params["means"]),
            np.array(head_swapped.params["means"]),
        )

    def test_n_states_changes_identity(self):
        p = _panel(_hmm_frame())
        h2 = fit_regime_hmm(
            p, params=FitRegimeHmmParams(n_states=2),
        ).lineage.head_hash
        h3 = fit_regime_hmm(
            p, params=FitRegimeHmmParams(n_states=3),
        ).lineage.head_hash
        assert h2 != h3


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates generic feature names)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    frame = _hmm_frame(seed=9, cols=("sensor_x", "sensor_y"))
    p = _panel(frame, unit=TimeSeriesUnits.RATIO)
    out = fit_regime_hmm(p, params=FitRegimeHmmParams(n_states=3))
    assert isinstance(out, Series)
    assert out.units == TimeSeriesUnits.FACTOR_LEVEL
    assert set(out.payload.dropna().astype(int)) == {0, 1, 2}


# ===========================================================================
# 5. Composition — primitives → pairwise_spread_matrix(Panel) → hmm
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
            workflow_id="fit_regime_hmm_composition",
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
                    node_id="hmm", operator_name="fit_regime_hmm",
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
                WorkflowEdge(source_node_id="mat", target_node_id="hmm",
                             target_input_slot="features"),
            ],
            terminal_node_id="hmm",
        )
        return wf, _resolve

    def test_type_gate_accepts_hmm_dag(self, tmp_path):
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
