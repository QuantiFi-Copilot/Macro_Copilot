"""Tests for shared.operators.cross_sectional_rank — Track-A A3 operator.

Covers the contract surface every standard operator must satisfy:

  - parity vs pandas DataFrame.rank(axis=1) for every ties method,
    both directions, ordinal AND normalized variants
  - NaN policy: a NaN member gets a NaN rank while the others are
    ranked among themselves; a date below min_members emits NaN for
    every member; an all-NaN output is a typed refusal
  - units: COUNT (ordinal) / PCT_RANK (normalized); mixed-unit input
    refused outright with the convert_units remedy
  - SeriesSet lineage mechanics: the set chain extends the input's by
    exactly one step; get_series(key) composes a complete chain ending
    with the rank step (the align_series/get_series inductive pattern)
  - OPR8 params=None succeeds + matches explicit config resolution;
    schema mirrors YAML
  - OPR14 rerun determinism + variant-changes-identity
  - OPR6 finance-blindness (non-rates synthetic data)
  - composition: type-gate accepts the operator and a North-Star-shaped
    DAG (align → rank → select) executes end-to-end
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
    SeriesSet,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.align_series import align_series
from shared.operators.cross_sectional_rank import (
    CONFIG_PATH,
    CrossSectionalRankError,
    CrossSectionalRankParams,
    cross_sectional_rank,
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


_N = 30
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _aligned_set(n_members: int = 4, seed: int = 11, units=None):
    rng = np.random.RandomState(seed)
    members = [
        _series(
            f"m{i}", dates=_DATES, values=rng.randn(_N).cumsum() + i,
            units=(units or TimeSeriesUnits.BPS),
        )
        for i in range(n_members)
    ]
    return align_series(members), members


# ===========================================================================
# 1. Parity vs pandas DataFrame.rank
# ===========================================================================


class TestHappyPath:
    def test_ordinal_matches_pandas_rank(self):
        sset, members = _aligned_set()
        out = cross_sectional_rank(sset)
        frame = pd.DataFrame({m.series_key: m.payload for m in members})
        expected = frame.rank(axis=1, method="average", ascending=True)
        for k in expected.columns:
            pd.testing.assert_series_equal(
                out.series_by_key[k], expected[k],
                check_names=False,
            )
        assert isinstance(out, SeriesSet)  # OPR2 constant output type
        assert all(
            u == TimeSeriesUnits.COUNT for u in out.units_by_key.values()
        )

    @pytest.mark.parametrize("ties", ["average", "min", "max", "first", "dense"])
    @pytest.mark.parametrize("ascending", [True, False])
    def test_every_ties_method_and_direction(self, ties, ascending):
        sset, members = _aligned_set(seed=7)
        out = cross_sectional_rank(
            sset,
            params=CrossSectionalRankParams(ties=ties, ascending=ascending),
        )
        frame = pd.DataFrame({m.series_key: m.payload for m in members})
        expected = frame.rank(axis=1, method=ties, ascending=ascending)
        for k in expected.columns:
            pd.testing.assert_series_equal(
                out.series_by_key[k], expected[k], check_names=False,
            )

    @pytest.mark.parametrize("ties", ["average", "min", "max", "first", "dense"])
    def test_normalized_matches_pandas_pct_times_100(self, ties):
        """Every ties method under the normalized variant — including
        ties=dense, whose pct denominator is the count of DISTINCT
        values (not n_valid), per pandas semantics disclosed in the
        YAML."""
        sset, members = _aligned_set(seed=3)
        out = cross_sectional_rank(
            sset,
            params=CrossSectionalRankParams(
                rank_method="normalized", ties=ties,
            ),
        )
        frame = pd.DataFrame({m.series_key: m.payload for m in members})
        expected = frame.rank(axis=1, method=ties, pct=True) * 100.0
        for k in expected.columns:
            pd.testing.assert_series_equal(
                out.series_by_key[k], expected[k], check_names=False,
            )
        assert all(
            u == TimeSeriesUnits.PCT_RANK for u in out.units_by_key.values()
        )

    def test_normalized_dense_ties_uses_distinct_denominator(self):
        """Pin the dense-pct semantic explicitly: row [1, 1, 2, 3] →
        33.3/33.3/66.7/100 (denominator = 3 distinct values)."""
        dates = pd.bdate_range("2026-01-02", periods=1)
        members = [
            _series("a", dates=dates, values=[1.0]),
            _series("b", dates=dates, values=[1.0]),
            _series("c", dates=dates, values=[2.0]),
            _series("d", dates=dates, values=[3.0]),
        ]
        sset = align_series(members)
        out = cross_sectional_rank(
            sset,
            params=CrossSectionalRankParams(
                rank_method="normalized", ties="dense", min_members=2,
            ),
        )
        got = {k: float(out.series_by_key[k].iloc[0]) for k in out.keys()}
        assert got["a"] == pytest.approx(100.0 / 3)
        assert got["b"] == pytest.approx(100.0 / 3)
        assert got["c"] == pytest.approx(200.0 / 3)
        assert got["d"] == pytest.approx(100.0)

    def test_all_keys_preserved_and_index_passthrough(self):
        sset, _ = _aligned_set(n_members=5)
        out = cross_sectional_rank(sset)
        assert out.keys() == sset.keys()
        assert out.common_index.equals(sset.common_index)
        assert out.frequency == sset.frequency


# ===========================================================================
# 2. NaN policy
# ===========================================================================


class TestNanPolicy:
    def _set_with_nans(self):
        rng = np.random.RandomState(5)
        v0 = rng.randn(_N)
        v1 = rng.randn(_N)
        v2 = rng.randn(_N)
        v0[4] = np.nan          # one member missing at date 4
        v0[10], v1[10] = np.nan, np.nan  # two of three missing at date 10
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        return align_series(members), members

    def test_nan_member_gets_nan_rank_others_ranked(self):
        sset, members = self._set_with_nans()
        out = cross_sectional_rank(sset)
        # date 4: 'a' NaN; 'b','c' ranked among themselves (1 and 2)
        d = _DATES[4]
        assert np.isnan(out.series_by_key["a"].loc[d])
        got = sorted(
            [out.series_by_key["b"].loc[d], out.series_by_key["c"].loc[d]]
        )
        assert got == [1.0, 2.0]

    def test_date_below_min_members_is_all_nan(self):
        sset, _ = self._set_with_nans()
        out = cross_sectional_rank(sset)
        # date 10: only 'c' is non-NaN → below min_members=2 → all NaN
        d = _DATES[10]
        for k in ("a", "b", "c"):
            assert np.isnan(out.series_by_key[k].loc[d])

    def test_all_nan_output_refused(self):
        members = [
            _series("a", dates=_DATES, values=[np.nan] * _N),
            _series("b", dates=_DATES, values=[np.nan] * _N),
        ]
        # align with raw policy keeps all-NaN members
        sset = align_series(members)
        with pytest.raises(CrossSectionalRankError, match="all-NaN"):
            cross_sectional_rank(sset)


# ===========================================================================
# 3. OPR13 refusals + OPR11 units
# ===========================================================================


class TestRefusals:
    def test_non_seriesset_input_raises(self):
        with pytest.raises(CrossSectionalRankError, match="SeriesSet"):
            cross_sectional_rank([1, 2, 3])  # type: ignore[arg-type]

    def test_single_member_set_refused(self):
        # build a 1-member SeriesSet directly (align_series allows it)
        m = _series("solo", dates=_DATES, values=list(range(_N)))
        sset = align_series([m])
        with pytest.raises(CrossSectionalRankError, match="at least 2"):
            cross_sectional_rank(sset)

    def test_mixed_units_refused_with_convert_units_remedy(self):
        a = _series("a", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.BPS)
        b = _series("b", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.PERCENT)
        sset = align_series([a, b])
        with pytest.raises(CrossSectionalRankError, match="convert_units"):
            cross_sectional_rank(sset)


# ===========================================================================
# 4. SeriesSet lineage mechanics (OPR10)
# ===========================================================================


class TestLineageMechanics:
    def test_set_chain_extends_input_by_one_step(self):
        sset, _ = _aligned_set()
        out = cross_sectional_rank(sset)
        assert len(out.lineage.steps) == len(sset.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "cross_sectional_rank"
        assert head.version == "1.0.0"
        assert head.params["n_members"] == 4

    def test_get_series_composes_complete_chain(self):
        """The align_series/get_series inductive pattern: extracting a
        member of the OUTPUT set must yield a chain ending
        [..., fetch, adapter, align_series, cross_sectional_rank]."""
        sset, _ = _aligned_set()
        out = cross_sectional_rank(sset)
        extracted = out.get_series("m1")
        names = [s.name for s in extracted.lineage.steps]
        assert names[-1] == "cross_sectional_rank"
        assert names[-2] == "align_series"
        assert "fetch_single_tenor" in names
        # and the extracted Series is a valid artifact in rank units
        assert extracted.units == TimeSeriesUnits.COUNT

    def test_rerun_is_deterministic(self):
        sset, _ = _aligned_set()
        out1 = cross_sectional_rank(sset)
        out2 = cross_sectional_rank(sset)
        assert out1.lineage.head_hash == out2.lineage.head_hash

    def test_variant_changes_identity(self):
        sset, _ = _aligned_set()
        h1 = cross_sectional_rank(
            sset, params=CrossSectionalRankParams(ascending=True),
        ).lineage.head_hash
        h2 = cross_sectional_rank(
            sset, params=CrossSectionalRankParams(ascending=False),
        ).lineage.head_hash
        assert h1 != h2


# ===========================================================================
# 5. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        sset, _ = _aligned_set()
        out_default = cross_sectional_rank(sset)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = CrossSectionalRankParams(
            rank_method=cfg.default_value("rank_method"),
            ascending=bool(cfg.default_value("ascending")),
            ties=cfg.default_value("ties"),
            min_members=int(cfg.default_value("min_members")),
        )
        out_explicit = cross_sectional_rank(sset, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = CrossSectionalRankParams()
        assert p.rank_method == cfg.default_value("rank_method")
        assert p.ascending == bool(cfg.default_value("ascending"))
        assert p.ties == cfg.default_value("ties")
        assert p.min_members == int(cfg.default_value("min_members"))

    def test_min_members_floor_enforced(self):
        with pytest.raises(ValueError):
            CrossSectionalRankParams(min_members=1)


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(47)
    members = [
        _series(
            f"sensor_{i}", dates=_DATES, values=rng.randn(_N).cumsum(),
            units=TimeSeriesUnits.Z_SCORE,
        )
        for i in range(6)
    ]
    sset = align_series(members)
    out = cross_sectional_rank(
        sset, params=CrossSectionalRankParams(rank_method="normalized"),
    )
    # at every date the normalized ranks of 6 members span (0, 100]
    frame = pd.DataFrame(out.series_by_key)
    assert frame.max(axis=1).max() == pytest.approx(100.0)
    assert (frame.min(axis=1) > 0).all()


# ===========================================================================
# 7. Composition — type-gate + a North-Star-shaped DAG
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

        # North-Star tail shape: [universe members] → align →
        # cross_sectional_rank → select one member's rank path.
        wf = Workflow(
            workflow_id="cross_sectional_rank_composition",
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
                    node_id="rank", operator_name="cross_sectional_rank",
                    params={"ascending": False},
                ),
                OperatorNode(
                    node_id="sel", operator_name="select_from_series_set",
                    params={"series_key": "u2"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p1", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p2", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p3", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="rank",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="rank", target_node_id="sel",
                             target_input_slot="series_set"),
            ],
            terminal_node_id="sel",
        )
        return wf, _resolve

    def test_type_gate_accepts_rank_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_and_matches_pandas(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.COUNT
        n = 40
        cols = {}
        for name, drift, mult, mod in (
            ("u1", 0.5, 7919, 13),
            ("u2", -0.2, 104729, 17),
            ("u3", 0.1, 1299709, 11),
        ):
            cols[name] = [
                100.0 + i * drift + ((i * mult) % mod) * 0.25
                for i in range(n)
            ]
        frame = pd.DataFrame(cols)
        expected = frame.rank(axis=1, method="average", ascending=False)["u2"]
        np.testing.assert_allclose(
            terminal.payload.to_numpy(), expected.to_numpy(),
        )
        assert terminal.lineage.steps[-1].name == "select_from_series_set"
        assert terminal.lineage.steps[-2].name == "cross_sectional_rank"
        clear_tool_config_cache()
