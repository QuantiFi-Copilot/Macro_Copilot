"""Tests for shared.operators.pairwise_spread_matrix — Track-A A3.

Covers the contract surface every standard operator must satisfy:

  - parity vs manual member_i − member_j for every upper-triangle pair
  - antisymmetry sanity (a−b == −(b−a)) and column-name/direction pin
  - column count = N(N−1)/2 over sorted keys
  - NaN pairwise propagation (either member NaN → pair NaN)
  - units passthrough into units_by_column; mixed-unit refusal
  - overflow → typed refusal (demean precedent); all-NaN refusal;
    member-ceiling refusal (>50)
  - Panel lineage = set chain + one step with the design locks recorded
  - OPR8 params=None (empty Params; defaults: {}) + OPR14 determinism
  - OPR6 finance-blindness; composition: validate+execute with Panel
    as the TERMINAL artifact (no live operator consumes Panel)
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
    Panel,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
)
from shared.operators.align_series import align_series
from shared.operators.pairwise_spread_matrix import (
    PairwiseSpreadMatrixError,
    PairwiseSpreadMatrixParams,
    pairwise_spread_matrix,
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
        frequency=None,
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


_N = 12
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _aligned_set(n_members: int = 4, seed: int = 31):
    rng = np.random.RandomState(seed)
    members = [
        _series(f"m{i}", dates=_DATES, values=rng.randn(_N) * 3)
        for i in range(n_members)
    ]
    return align_series(members), members


# ===========================================================================
# 1. Parity + structure
# ===========================================================================


class TestHappyPath:
    def test_matches_manual_differences(self):
        sset, members = _aligned_set()
        out = pairwise_spread_matrix(sset)
        assert isinstance(out, Panel)  # OPR2 constant output type
        by_key = {m.series_key: m.payload for m in members}
        keys = sorted(by_key)
        for i, ki in enumerate(keys):
            for kj in keys[i + 1:]:
                col = f"{ki}__minus__{kj}"
                assert col in out.payload.columns
                pd.testing.assert_series_equal(
                    out.payload[col], by_key[ki] - by_key[kj],
                    check_names=False,
                )

    def test_column_count_is_upper_triangle(self):
        sset, _ = _aligned_set(n_members=5)
        out = pairwise_spread_matrix(sset)
        assert len(out.payload.columns) == 10  # 5*4/2

    def test_antisymmetry_sanity(self):
        """a−b == −(b−a): recomputing the reverse direction by hand
        must equal the negated emitted column."""
        sset, members = _aligned_set(seed=7)
        out = pairwise_spread_matrix(sset)
        by_key = {m.series_key: m.payload for m in members}
        emitted = out.payload["m0__minus__m1"]
        reverse = by_key["m1"] - by_key["m0"]
        np.testing.assert_allclose(
            emitted.to_numpy(), (-reverse).to_numpy(),
        )

    def test_units_passthrough_per_column(self):
        sset, _ = _aligned_set()
        out = pairwise_spread_matrix(sset)
        assert set(out.units_by_column.values()) == {TimeSeriesUnits.BPS}

    def test_index_is_common_index(self):
        sset, _ = _aligned_set()
        out = pairwise_spread_matrix(sset)
        assert out.payload.index.equals(sset.common_index)


# ===========================================================================
# 2. NaN propagation
# ===========================================================================


class TestNanPolicy:
    def test_pairwise_nan_propagation(self):
        rng = np.random.RandomState(5)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[4] = np.nan
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = pairwise_spread_matrix(sset)
        d = _DATES[4]
        assert np.isnan(out.payload["a__minus__b"].loc[d])
        assert np.isnan(out.payload["a__minus__c"].loc[d])
        assert not np.isnan(out.payload["b__minus__c"].loc[d])

    def test_all_nan_output_refused(self):
        members = [
            _series("a", dates=_DATES, values=[np.nan] * _N),
            _series("b", dates=_DATES, values=[np.nan] * _N),
        ]
        sset = align_series(members)
        with pytest.raises(PairwiseSpreadMatrixError, match="all-NaN"):
            pairwise_spread_matrix(sset)


# ===========================================================================
# 3. OPR13 refusals
# ===========================================================================


class TestRefusals:
    def test_non_seriesset_input_raises(self):
        with pytest.raises(PairwiseSpreadMatrixError, match="SeriesSet"):
            pairwise_spread_matrix([1, 2])  # type: ignore[arg-type]

    def test_single_member_set_refused(self):
        m = _series("solo", dates=_DATES, values=list(range(_N)))
        sset = align_series([m])
        with pytest.raises(PairwiseSpreadMatrixError, match="at least 2"):
            pairwise_spread_matrix(sset)

    def test_member_ceiling_refused(self):
        rng = np.random.RandomState(3)
        members = [
            _series(f"k{i:02d}", dates=_DATES, values=rng.randn(_N))
            for i in range(51)
        ]
        sset = align_series(members)
        with pytest.raises(PairwiseSpreadMatrixError, match="ceiling"):
            pairwise_spread_matrix(sset)

    def test_mixed_units_refused_with_convert_units_remedy(self):
        a = _series("a", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.BPS)
        b = _series("b", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.PERCENT)
        sset = align_series([a, b])
        with pytest.raises(
            PairwiseSpreadMatrixError, match="convert_units",
        ):
            pairwise_spread_matrix(sset)

    def test_overflow_difference_raises_typed_error(self):
        v0 = np.ones(_N)
        v1 = np.ones(_N)
        v0[5], v1[5] = 1.7e308, -1.7e308
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
        ]
        sset = align_series(members)
        with pytest.raises(PairwiseSpreadMatrixError, match="overflow"):
            pairwise_spread_matrix(sset)


# ===========================================================================
# 4. Lineage + determinism + OPR8
# ===========================================================================


class TestLineageAndParams:
    def test_lineage_extends_set_chain_and_records_locks(self):
        sset, _ = _aligned_set()
        out = pairwise_spread_matrix(sset)
        assert len(out.lineage.steps) == len(sset.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "pairwise_spread_matrix"
        assert "upper triangle" in head.params["pair_enumeration"]
        assert "a − b" in head.params["direction_convention"]
        assert head.params["max_members"] == 50
        assert head.params["n_pairs"] == 6

    def test_rerun_is_deterministic(self):
        sset, _ = _aligned_set()
        out1 = pairwise_spread_matrix(sset)
        out2 = pairwise_spread_matrix(sset)
        assert out1.lineage.head_hash == out2.lineage.head_hash

    def test_params_none_equals_empty_params(self):
        sset, _ = _aligned_set()
        h_none = pairwise_spread_matrix(sset).lineage.head_hash
        h_empty = pairwise_spread_matrix(
            sset, params=PairwiseSpreadMatrixParams(),
        ).lineage.head_hash
        assert h_none == h_empty

    def test_params_class_is_empty_and_frozen(self):
        assert PairwiseSpreadMatrixParams.model_fields == {}
        with pytest.raises(Exception):
            PairwiseSpreadMatrixParams(extra_field=1)  # extra=forbid


# ===========================================================================
# 5. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(71)
    members = [
        _series(
            f"sensor_{i}", dates=_DATES, values=rng.randn(_N),
            units=TimeSeriesUnits.RATIO,
        )
        for i in range(3)
    ]
    sset = align_series(members)
    out = pairwise_spread_matrix(sset)
    assert len(out.payload.columns) == 3
    assert set(out.units_by_column.values()) == {TimeSeriesUnits.RATIO}


# ===========================================================================
# 6. Composition — Panel as the TERMINAL artifact
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
            n_rows: int = 25
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
            workflow_id="pairwise_spread_matrix_composition",
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
                    node_id="mat",
                    operator_name="pairwise_spread_matrix",
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
            ],
            terminal_node_id="mat",
        )
        return wf, _resolve

    def test_type_gate_accepts_panel_terminal_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_with_panel_terminal(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Panel)
        assert len(terminal.payload.columns) == 3
        n = 25
        u1 = np.array([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        u2 = np.array([
            100.0 + i * -0.2 + ((i * 104729) % 17) * 0.25 for i in range(n)
        ])
        np.testing.assert_allclose(
            terminal.payload["u1__minus__u2"].to_numpy(), u1 - u2,
        )
        assert terminal.lineage.steps[-1].name == "pairwise_spread_matrix"
        clear_tool_config_cache()
