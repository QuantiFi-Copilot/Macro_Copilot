"""Tests for shared.operators.weighted_combination — Track-A A7.

Covers the contract surface every standard operator must satisfy:

  - parity vs manual Σ w·x including NEGATIVE weights (a 1/−2/1
    three-leg) and a two-leg long/short
  - basket-selection semantics: unnamed members excluded — their NaNs
    are inert; named-member NaN poisons the combination at that date
  - refusals: params=None (no meaningful default — the
    select_from_series_set precedent), <2 named members (with the
    series_arithmetic remedy), unknown named key, zero weight,
    non-finite weight, mixed units across NAMED members (unnamed
    units irrelevant), overflow, all-NaN output
  - units passthrough of the named members' common unit
  - ART10: weights are content-defining — different weights yield
    different head hashes; rerun determinism
  - lineage = set chain + one step with the full weights recorded
  - OPR6 non-rates case; composition fly DAG
    (align(output_keys) → weighted_combination → rolling_zscore)
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
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.align_series import align_series, AlignSeriesParams
from shared.operators.weighted_combination import (
    WeightedCombinationError,
    WeightedCombinationParams,
    weighted_combination,
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


_N = 15
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _aligned_set(n_members: int = 4, seed: int = 37):
    rng = np.random.RandomState(seed)
    members = [
        _series(f"m{i}", dates=_DATES, values=rng.randn(_N) * 2 + i)
        for i in range(n_members)
    ]
    return align_series(members), members


# ===========================================================================
# 1. Parity incl. negative weights
# ===========================================================================


class TestHappyPath:
    def test_three_leg_fly_matches_manual(self):
        sset, members = _aligned_set(n_members=3)
        w = {"m0": 1.0, "m1": -2.0, "m2": 1.0}
        out = weighted_combination(
            sset, params=WeightedCombinationParams(weights=w),
        )
        by_key = {m.series_key: m.payload for m in members}
        expected = by_key["m0"] - 2.0 * by_key["m1"] + by_key["m2"]
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.BPS  # passthrough

    def test_two_leg_long_short(self):
        sset, members = _aligned_set(n_members=2, seed=5)
        out = weighted_combination(
            sset,
            params=WeightedCombinationParams(
                weights={"m0": 0.7, "m1": -0.3},
            ),
        )
        by_key = {m.series_key: m.payload for m in members}
        expected = 0.7 * by_key["m0"] - 0.3 * by_key["m1"]
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )

    def test_unnamed_members_excluded(self):
        """The mapping IS the basket: m3 exists in the set but is not
        named — its values (and NaNs) must be inert."""
        sset, members = _aligned_set(n_members=4, seed=7)
        w = {"m0": 1.0, "m1": 1.0}
        out_with_m3 = weighted_combination(
            sset, params=WeightedCombinationParams(weights=w),
        )
        sset2, members2 = _aligned_set(n_members=2, seed=7)
        out_without = weighted_combination(
            sset2, params=WeightedCombinationParams(weights=w),
        )
        np.testing.assert_allclose(
            out_with_m3.payload.to_numpy(), out_without.payload.to_numpy(),
        )

    def test_series_key_pattern(self):
        sset, _ = _aligned_set(n_members=3)
        out = weighted_combination(
            sset,
            params=WeightedCombinationParams(
                weights={"m0": 1.0, "m1": -2.0, "m2": 1.0},
            ),
        )
        assert out.series_key == "weighted_combo__3_legs"


# ===========================================================================
# 2. NaN semantics
# ===========================================================================


class TestNanSemantics:
    def test_named_member_nan_poisons(self):
        rng = np.random.RandomState(9)
        v0, v1 = rng.randn(_N), rng.randn(_N)
        v0[5] = np.nan
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
        ]
        sset = align_series(members)
        out = weighted_combination(
            sset,
            params=WeightedCombinationParams(weights={"a": 1.0, "b": 1.0}),
        )
        assert np.isnan(out.payload.loc[_DATES[5]])
        assert not np.isnan(out.payload.loc[_DATES[6]])

    def test_unnamed_member_nan_is_inert(self):
        rng = np.random.RandomState(11)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v2[:] = np.nan  # entirely NaN, but unnamed
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = weighted_combination(
            sset,
            params=WeightedCombinationParams(weights={"a": 1.0, "b": 2.0}),
        )
        assert out.payload.notna().all()

    def test_all_nan_output_refused(self):
        members = [
            _series("a", dates=_DATES, values=[np.nan] * _N),
            _series("b", dates=_DATES, values=list(range(_N))),
        ]
        sset = align_series(members)
        with pytest.raises(WeightedCombinationError, match="all-NaN"):
            weighted_combination(
                sset,
                params=WeightedCombinationParams(
                    weights={"a": 1.0, "b": 1.0},
                ),
            )


# ===========================================================================
# 3. OPR13 refusals
# ===========================================================================


class TestRefusals:
    def test_params_none_refused_with_named_field(self):
        sset, _ = _aligned_set()
        with pytest.raises(WeightedCombinationError, match="weights"):
            weighted_combination(sset)

    def test_single_named_member_refused_with_remedy(self):
        # schema floor: min_length=2 fires at construction
        with pytest.raises(ValueError):
            WeightedCombinationParams(weights={"a": 1.0})

    def test_unknown_named_key_refused_with_available_list(self):
        sset, _ = _aligned_set()
        with pytest.raises(WeightedCombinationError, match="Available keys"):
            weighted_combination(
                sset,
                params=WeightedCombinationParams(
                    weights={"m0": 1.0, "nope": 1.0},
                ),
            )

    def test_zero_weight_refused_with_omit_remedy(self):
        sset, _ = _aligned_set()
        with pytest.raises(WeightedCombinationError, match="omit"):
            weighted_combination(
                sset,
                params=WeightedCombinationParams(
                    weights={"m0": 1.0, "m1": 0.0},
                ),
            )

    def test_non_finite_weight_refused(self):
        sset, _ = _aligned_set()
        with pytest.raises(WeightedCombinationError, match="non-finite"):
            weighted_combination(
                sset,
                params=WeightedCombinationParams(
                    weights={"m0": 1.0, "m1": float("inf")},
                ),
            )

    def test_mixed_units_across_named_members_refused(self):
        a = _series("a", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.BPS)
        b = _series("b", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.PERCENT)
        sset = align_series([a, b])
        with pytest.raises(WeightedCombinationError, match="convert_units"):
            weighted_combination(
                sset,
                params=WeightedCombinationParams(
                    weights={"a": 1.0, "b": 1.0},
                ),
            )

    def test_mixed_units_on_unnamed_member_is_irrelevant(self):
        a = _series("a", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.BPS)
        b = _series("b", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.BPS)
        c = _series("c", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.PERCENT)  # unnamed
        sset = align_series([a, b, c])
        out = weighted_combination(
            sset,
            params=WeightedCombinationParams(weights={"a": 1.0, "b": 1.0}),
        )
        assert out.units == TimeSeriesUnits.BPS

    def test_overflow_refused(self):
        big = np.full(_N, 1e308)
        members = [
            _series("a", dates=_DATES, values=big),
            _series("b", dates=_DATES, values=big.copy()),
        ]
        sset = align_series(members)
        with pytest.raises(WeightedCombinationError, match="overflow"):
            weighted_combination(
                sset,
                params=WeightedCombinationParams(
                    weights={"a": 1.0, "b": 1.0},
                ),
            )

    def test_non_seriesset_input_raises(self):
        with pytest.raises(WeightedCombinationError, match="SeriesSet"):
            weighted_combination(
                3.0,  # type: ignore[arg-type]
                params=WeightedCombinationParams(
                    weights={"a": 1.0, "b": 1.0},
                ),
            )


# ===========================================================================
# 4. ART10 identity + lineage
# ===========================================================================


class TestLineageAndIdentity:
    def test_weights_are_content_defining(self):
        """ART10: the weights ARE the recipe — different weights on
        the same set must yield different head hashes."""
        sset, _ = _aligned_set(n_members=3)
        h1 = weighted_combination(
            sset,
            params=WeightedCombinationParams(
                weights={"m0": 1.0, "m1": -2.0, "m2": 1.0},
            ),
        ).lineage.head_hash
        h2 = weighted_combination(
            sset,
            params=WeightedCombinationParams(
                weights={"m0": 1.0, "m1": -1.0, "m2": 1.0},
            ),
        ).lineage.head_hash
        assert h1 != h2

    def test_lineage_extends_set_chain_and_records_weights(self):
        sset, _ = _aligned_set(n_members=3)
        w = {"m0": 1.0, "m1": -2.0, "m2": 1.0}
        out = weighted_combination(
            sset, params=WeightedCombinationParams(weights=w),
        )
        assert len(out.lineage.steps) == len(sset.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "weighted_combination"
        assert head.params["weights"] == w
        assert head.params["n_legs"] == 3

    def test_rerun_is_deterministic(self):
        sset, _ = _aligned_set(n_members=3)
        p = WeightedCombinationParams(
            weights={"m0": 1.0, "m1": -2.0, "m2": 1.0},
        )
        assert (
            weighted_combination(sset, params=p).lineage.head_hash
            == weighted_combination(sset, params=p).lineage.head_hash
        )


# ===========================================================================
# 5. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(73)
    members = [
        _series(
            f"sensor_{i}", dates=_DATES, values=rng.randn(_N),
            units=TimeSeriesUnits.RATIO,
        )
        for i in range(3)
    ]
    sset = align_series(members)
    out = weighted_combination(
        sset,
        params=WeightedCombinationParams(
            weights={"sensor_0": 0.5, "sensor_1": 0.5, "sensor_2": -1.0},
        ),
    )
    by_key = {m.series_key: m.payload for m in members}
    expected = (
        0.5 * by_key["sensor_0"]
        + 0.5 * by_key["sensor_1"]
        - by_key["sensor_2"]
    )
    pd.testing.assert_series_equal(out.payload, expected, check_names=False)
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 6. Composition — fly DAG with template-controlled member names
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
            n_rows: int = 40
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
            workflow_id="weighted_combination_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p1", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "wing_a", "drift": 0.5,
                            "pattern_mult": 7919, "pattern_mod": 13},
                ),
                PrimitiveNode(
                    node_id="p2", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "belly", "drift": -0.2,
                            "pattern_mult": 104729, "pattern_mod": 17},
                ),
                PrimitiveNode(
                    node_id="p3", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "wing_b", "drift": 0.1,
                            "pattern_mult": 1299709, "pattern_mod": 11},
                ),
                OperatorNode(node_id="align", operator_name="align_series"),
                OperatorNode(
                    node_id="combo",
                    operator_name="weighted_combination",
                    params={"weights": {
                        "wing_a": 1.0, "belly": -2.0, "wing_b": 1.0,
                    }},
                ),
                OperatorNode(
                    node_id="z", operator_name="rolling_zscore",
                    params={"window": 20},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p1", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p2", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p3", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="combo",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="combo", target_node_id="z",
                             target_input_slot="series"),
            ],
            terminal_node_id="z",
        )
        return wf, _resolve

    def test_type_gate_accepts_combo_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_fly_then_zscore(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.Z_SCORE
        combo = result.node_artifacts["combo"]
        n = 40
        cols = {}
        for name, drift, mult, mod in (
            ("wing_a", 0.5, 7919, 13),
            ("belly", -0.2, 104729, 17),
            ("wing_b", 0.1, 1299709, 11),
        ):
            cols[name] = np.array([
                100.0 + i * drift + ((i * mult) % mod) * 0.25
                for i in range(n)
            ])
        expected = cols["wing_a"] - 2.0 * cols["belly"] + cols["wing_b"]
        np.testing.assert_allclose(combo.payload.to_numpy(), expected)
        assert combo.units == TimeSeriesUnits.BPS
        clear_tool_config_cache()
