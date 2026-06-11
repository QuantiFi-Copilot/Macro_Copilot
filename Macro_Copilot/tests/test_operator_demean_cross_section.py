"""Tests for shared.operators.demean_cross_section — Track-A A3.

Covers the contract surface every standard operator must satisfy:

  - parity vs manual pandas frame.sub(frame.mean(axis=1), axis=0)
  - row-mean-zero property over the non-NaN members
  - units PASSTHROUGH (BPS in → BPS out); mixed-unit refusal
  - the all-EQUAL date demeans to a legitimate 0.0 row (NO dispersion
    barrier — the deliberate divergence from cross_sectional_zscore)
  - NaN policy: NaN member stays NaN, others demeaned among
    themselves; below-floor dates all-NaN; all-NaN output refused
  - SeriesSet lineage induction (get_series chain order)
  - OPR8 params=None + schema-mirrors-YAML; OPR14 determinism
  - OPR6 finance-blindness; composition demean→rank DAG
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
from shared.operators.demean_cross_section import (
    CONFIG_PATH,
    DemeanCrossSectionError,
    DemeanCrossSectionParams,
    demean_cross_section,
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


_N = 20
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _aligned_set(n_members: int = 4, seed: int = 21):
    rng = np.random.RandomState(seed)
    members = [
        _series(f"m{i}", dates=_DATES, values=rng.randn(_N) * 2 + i)
        for i in range(n_members)
    ]
    return align_series(members), members


# ===========================================================================
# 1. Parity + properties
# ===========================================================================


class TestHappyPath:
    def test_matches_manual_reference(self):
        sset, members = _aligned_set()
        out = demean_cross_section(sset)
        frame = pd.DataFrame({m.series_key: m.payload for m in members})
        expected = frame.sub(frame.mean(axis=1), axis=0)
        for k in expected.columns:
            pd.testing.assert_series_equal(
                out.series_by_key[k], expected[k], check_names=False,
            )
        assert isinstance(out, SeriesSet)  # OPR2 constant output type

    def test_units_passthrough(self):
        sset, _ = _aligned_set()
        out = demean_cross_section(sset)
        assert all(
            u == TimeSeriesUnits.BPS for u in out.units_by_key.values()
        )

    def test_rows_are_mean_zero(self):
        sset, _ = _aligned_set(n_members=7, seed=3)
        out = demean_cross_section(sset)
        frame = pd.DataFrame(out.series_by_key)
        np.testing.assert_allclose(
            frame.mean(axis=1).to_numpy(), 0.0, atol=1e-12,
        )

    def test_all_equal_date_demeans_to_zero_row_not_nan(self):
        """Deliberate divergence from cross_sectional_zscore: there is
        no dispersion barrier — an all-equal date demeans to 0.0."""
        vals = np.linspace(1, 9, _N)
        members = [
            _series("a", dates=_DATES, values=[5.0] * _N),
            _series("b", dates=_DATES, values=[5.0] * _N),
            _series("c", dates=_DATES, values=vals),
        ]
        v2 = vals.copy()
        v2[8] = 5.0  # all three equal at date 8
        members[2] = _series("c", dates=_DATES, values=v2)
        sset = align_series(members)
        out = demean_cross_section(sset)
        d = _DATES[8]
        for k in ("a", "b", "c"):
            assert out.series_by_key[k].loc[d] == pytest.approx(0.0)

    def test_keys_index_frequency_passthrough(self):
        sset, _ = _aligned_set()
        out = demean_cross_section(sset)
        assert out.keys() == sset.keys()
        assert out.common_index.equals(sset.common_index)
        assert out.frequency == sset.frequency


# ===========================================================================
# 2. NaN policy
# ===========================================================================


class TestNanPolicy:
    def test_nan_member_stays_nan_others_demeaned_among_rest(self):
        rng = np.random.RandomState(5)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[6] = np.nan
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = demean_cross_section(sset)
        d = _DATES[6]
        assert np.isnan(out.series_by_key["a"].loc[d])
        pair_mean = (v1[6] + v2[6]) / 2
        assert out.series_by_key["b"].loc[d] == pytest.approx(
            v1[6] - pair_mean,
        )

    def test_below_floor_date_is_all_nan(self):
        rng = np.random.RandomState(7)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[2], v1[2] = np.nan, np.nan
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = demean_cross_section(sset)
        d = _DATES[2]
        for k in ("a", "b", "c"):
            assert np.isnan(out.series_by_key[k].loc[d])

    def test_all_nan_output_refused(self):
        members = [
            _series("a", dates=_DATES, values=[np.nan] * _N),
            _series("b", dates=_DATES, values=[np.nan] * _N),
        ]
        sset = align_series(members)
        with pytest.raises(DemeanCrossSectionError, match="all-NaN"):
            demean_cross_section(sset)

    def test_overflow_demeaned_value_raises_typed_error(self):
        """Critic finding (OPR13/ART11): member − cross-mean of extreme
        finite values can overflow to ±Inf — a typed refusal, never a
        raw artifact-constructor crash."""
        v0 = np.ones(_N)
        v1 = np.ones(_N)
        v2 = np.ones(_N)
        v0[5], v1[5], v2[5] = 1.7e308, -1.7e308, -1.7e308
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        with pytest.raises(DemeanCrossSectionError, match="overflow"):
            demean_cross_section(sset)


# ===========================================================================
# 3. OPR13 refusals + OPR11 units
# ===========================================================================


class TestRefusals:
    def test_non_seriesset_input_raises(self):
        with pytest.raises(DemeanCrossSectionError, match="SeriesSet"):
            demean_cross_section(42)  # type: ignore[arg-type]

    def test_single_member_set_refused(self):
        m = _series("solo", dates=_DATES, values=list(range(_N)))
        sset = align_series([m])
        with pytest.raises(DemeanCrossSectionError, match="at least 2"):
            demean_cross_section(sset)

    def test_mixed_units_refused_with_convert_units_remedy(self):
        a = _series("a", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.BPS)
        b = _series("b", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.PERCENT)
        sset = align_series([a, b])
        with pytest.raises(DemeanCrossSectionError, match="convert_units"):
            demean_cross_section(sset)


# ===========================================================================
# 4. Lineage + determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_set_chain_extends_and_get_series_composes(self):
        sset, _ = _aligned_set()
        out = demean_cross_section(sset)
        assert len(out.lineage.steps) == len(sset.lineage.steps) + 1
        extracted = out.get_series("m1")
        names = [s.name for s in extracted.lineage.steps]
        assert names[-1] == "demean_cross_section"
        assert names[-2] == "align_series"
        assert extracted.units == TimeSeriesUnits.BPS  # passthrough

    def test_rerun_is_deterministic(self):
        sset, _ = _aligned_set()
        out1 = demean_cross_section(sset)
        out2 = demean_cross_section(sset)
        assert out1.lineage.head_hash == out2.lineage.head_hash

    def test_min_members_changes_identity(self):
        sset, _ = _aligned_set()
        h2 = demean_cross_section(
            sset, params=DemeanCrossSectionParams(min_members=2),
        ).lineage.head_hash
        h3 = demean_cross_section(
            sset, params=DemeanCrossSectionParams(min_members=3),
        ).lineage.head_hash
        assert h2 != h3


# ===========================================================================
# 5. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        sset, _ = _aligned_set()
        out_default = demean_cross_section(sset)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = DemeanCrossSectionParams(
            min_members=int(cfg.default_value("min_members")),
        )
        out_explicit = demean_cross_section(sset, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = DemeanCrossSectionParams()
        assert p.min_members == int(cfg.default_value("min_members"))

    def test_floor_enforced(self):
        with pytest.raises(ValueError):
            DemeanCrossSectionParams(min_members=1)


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(61)
    members = [
        _series(
            f"sensor_{i}", dates=_DATES, values=rng.randn(_N) + 3 * i,
            units=TimeSeriesUnits.RATIO,
        )
        for i in range(5)
    ]
    sset = align_series(members)
    out = demean_cross_section(sset)
    frame = pd.DataFrame(out.series_by_key)
    np.testing.assert_allclose(frame.mean(axis=1).to_numpy(), 0.0, atol=1e-12)
    assert all(u == TimeSeriesUnits.RATIO for u in out.units_by_key.values())


# ===========================================================================
# 7. Composition — demean → rank DAG
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
            n_rows: int = 30
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
            workflow_id="demean_composition",
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
                    node_id="dm", operator_name="demean_cross_section",
                ),
                OperatorNode(
                    node_id="sel", operator_name="select_from_series_set",
                    params={"series_key": "u3"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p1", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p2", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p3", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="dm",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="dm", target_node_id="sel",
                             target_input_slot="series_set"),
            ],
            terminal_node_id="sel",
        )
        return wf, _resolve

    def test_type_gate_accepts_demean_dag(self, tmp_path):
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
        assert terminal.units == TimeSeriesUnits.BPS  # passthrough
        n = 30
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
        expected = frame.sub(frame.mean(axis=1), axis=0)["u3"]
        np.testing.assert_allclose(
            terminal.payload.to_numpy(), expected.to_numpy(),
        )
        clear_tool_config_cache()
