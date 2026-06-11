"""Tests for shared.operators.cross_sectional_zscore — Track-A A3.

Covers the contract surface every standard operator must satisfy:

  - parity vs a manual pandas (frame − mean) / std reference for both
    ddof values
  - per-row properties: z-scores at each date are mean-zero and (for
    ddof used) unit-variance across the non-NaN members
  - NaN policy: NaN member → NaN z, others standardised among
    themselves; date below min_members → all-NaN row; ZERO-dispersion
    date (all members equal) → all-NaN row, never ±Inf; all-NaN output
    → typed refusal
  - units: Z_SCORE on every key; mixed-unit input refused outright
  - SeriesSet lineage mechanics: set chain extends input by one step;
    get_series composes a complete chain ending with this step
  - OPR8 params=None succeeds + matches explicit config resolution;
    schema mirrors YAML
  - OPR14 rerun determinism + ddof-changes-identity
  - OPR6 finance-blindness (non-rates synthetic data)
  - composition: type-gate accepts the operator and a z-then-rank
    screen DAG executes end-to-end
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
from shared.operators.cross_sectional_zscore import (
    CONFIG_PATH,
    CrossSectionalZscoreError,
    CrossSectionalZscoreParams,
    cross_sectional_zscore,
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


_N = 25
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _aligned_set(n_members: int = 5, seed: int = 13):
    rng = np.random.RandomState(seed)
    members = [
        _series(f"m{i}", dates=_DATES, values=rng.randn(_N).cumsum() + 2 * i)
        for i in range(n_members)
    ]
    return align_series(members), members


# ===========================================================================
# 1. Parity + per-row properties
# ===========================================================================


class TestHappyPath:
    @pytest.mark.parametrize("ddof", [0, 1])
    def test_matches_manual_reference(self, ddof):
        sset, members = _aligned_set()
        out = cross_sectional_zscore(
            sset, params=CrossSectionalZscoreParams(ddof=ddof),
        )
        frame = pd.DataFrame({m.series_key: m.payload for m in members})
        expected = frame.sub(frame.mean(axis=1), axis=0).div(
            frame.std(axis=1, ddof=ddof), axis=0,
        )
        for k in expected.columns:
            pd.testing.assert_series_equal(
                out.series_by_key[k], expected[k], check_names=False,
            )
        assert isinstance(out, SeriesSet)  # OPR2 constant output type
        assert all(
            u == TimeSeriesUnits.Z_SCORE for u in out.units_by_key.values()
        )

    def test_rows_are_mean_zero_unit_variance(self):
        sset, _ = _aligned_set(n_members=8, seed=3)
        out = cross_sectional_zscore(
            sset, params=CrossSectionalZscoreParams(ddof=1),
        )
        frame = pd.DataFrame(out.series_by_key)
        np.testing.assert_allclose(
            frame.mean(axis=1).to_numpy(), 0.0, atol=1e-12,
        )
        np.testing.assert_allclose(
            frame.std(axis=1, ddof=1).to_numpy(), 1.0, atol=1e-12,
        )

    def test_all_keys_index_frequency_passthrough(self):
        sset, _ = _aligned_set()
        out = cross_sectional_zscore(sset)
        assert out.keys() == sset.keys()
        assert out.common_index.equals(sset.common_index)
        assert out.frequency == sset.frequency


# ===========================================================================
# 2. NaN policy + zero-dispersion
# ===========================================================================


class TestNanPolicy:
    def test_nan_member_standardised_among_the_rest(self):
        rng = np.random.RandomState(5)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[6] = np.nan
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = cross_sectional_zscore(sset)
        d = _DATES[6]
        assert np.isnan(out.series_by_key["a"].loc[d])
        pair = np.array([v1[6], v2[6]])
        expected_b = (pair[0] - pair.mean()) / pair.std(ddof=1)
        assert out.series_by_key["b"].loc[d] == pytest.approx(expected_b)

    def test_zero_dispersion_date_is_all_nan_never_inf(self):
        members = [
            _series("a", dates=_DATES, values=[5.0] * _N),
            _series("b", dates=_DATES, values=[5.0] * _N),
            _series("c", dates=_DATES, values=list(np.linspace(1, 9, _N))),
        ]
        # only date where a==b==c could have dispersion; engineer one
        # all-equal date by making c hit 5.0 exactly at one date:
        vals = np.linspace(1, 9, _N)
        vals[12] = 5.0
        members[2] = _series("c", dates=_DATES, values=vals)
        sset = align_series(members)
        out = cross_sectional_zscore(sset)
        d = _DATES[12]
        for k in ("a", "b", "c"):
            assert np.isnan(out.series_by_key[k].loc[d])
        # no ±Inf anywhere (std mask runs before division)
        frame = pd.DataFrame(out.series_by_key)
        assert not np.isinf(frame.to_numpy()).any()

    def test_date_below_min_members_is_all_nan(self):
        rng = np.random.RandomState(9)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[3], v1[3] = np.nan, np.nan  # only one member left at date 3
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = cross_sectional_zscore(sset)
        d = _DATES[3]
        for k in ("a", "b", "c"):
            assert np.isnan(out.series_by_key[k].loc[d])

    def test_all_nan_output_refused(self):
        members = [
            _series("a", dates=_DATES, values=[7.0] * _N),
            _series("b", dates=_DATES, values=[7.0] * _N),
        ]
        sset = align_series(members)
        with pytest.raises(CrossSectionalZscoreError, match="all-NaN"):
            cross_sectional_zscore(sset)


# ===========================================================================
# 3. OPR13 refusals + OPR11 units
# ===========================================================================


class TestRefusals:
    def test_non_seriesset_input_raises(self):
        with pytest.raises(CrossSectionalZscoreError, match="SeriesSet"):
            cross_sectional_zscore(3.14)  # type: ignore[arg-type]

    def test_single_member_set_refused(self):
        m = _series("solo", dates=_DATES, values=list(range(_N)))
        sset = align_series([m])
        with pytest.raises(CrossSectionalZscoreError, match="at least 2"):
            cross_sectional_zscore(sset)

    def test_mixed_units_refused_with_convert_units_remedy(self):
        a = _series("a", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.BPS)
        b = _series("b", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.PERCENT)
        sset = align_series([a, b])
        with pytest.raises(CrossSectionalZscoreError, match="convert_units"):
            cross_sectional_zscore(sset)


# ===========================================================================
# 4. Lineage mechanics (OPR10) + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_set_chain_extends_and_get_series_composes(self):
        sset, _ = _aligned_set()
        out = cross_sectional_zscore(sset)
        assert len(out.lineage.steps) == len(sset.lineage.steps) + 1
        extracted = out.get_series("m2")
        names = [s.name for s in extracted.lineage.steps]
        assert names[-1] == "cross_sectional_zscore"
        assert names[-2] == "align_series"
        assert extracted.units == TimeSeriesUnits.Z_SCORE

    def test_rerun_is_deterministic(self):
        sset, _ = _aligned_set()
        out1 = cross_sectional_zscore(sset)
        out2 = cross_sectional_zscore(sset)
        assert out1.lineage.head_hash == out2.lineage.head_hash

    def test_ddof_changes_identity(self):
        sset, _ = _aligned_set()
        h1 = cross_sectional_zscore(
            sset, params=CrossSectionalZscoreParams(ddof=1),
        ).lineage.head_hash
        h0 = cross_sectional_zscore(
            sset, params=CrossSectionalZscoreParams(ddof=0),
        ).lineage.head_hash
        assert h1 != h0


# ===========================================================================
# 5. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        sset, _ = _aligned_set()
        out_default = cross_sectional_zscore(sset)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = CrossSectionalZscoreParams(
            ddof=int(cfg.default_value("ddof")),
            min_members=int(cfg.default_value("min_members")),
        )
        out_explicit = cross_sectional_zscore(sset, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = CrossSectionalZscoreParams()
        assert p.ddof == int(cfg.default_value("ddof"))
        assert p.min_members == int(cfg.default_value("min_members"))

    def test_bounds_enforced(self):
        with pytest.raises(ValueError):
            CrossSectionalZscoreParams(ddof=2)
        with pytest.raises(ValueError):
            CrossSectionalZscoreParams(min_members=1)


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(53)
    members = [
        _series(
            f"sensor_{i}", dates=_DATES, values=rng.randn(_N) * (i + 1),
            units=TimeSeriesUnits.RATIO,
        )
        for i in range(4)
    ]
    sset = align_series(members)
    out = cross_sectional_zscore(sset)
    frame = pd.DataFrame(out.series_by_key)
    np.testing.assert_allclose(frame.mean(axis=1).to_numpy(), 0.0, atol=1e-12)


# ===========================================================================
# 7. Composition — z-then-rank screen DAG
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
            workflow_id="cs_zscore_composition",
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
                    node_id="z", operator_name="cross_sectional_zscore",
                ),
                OperatorNode(
                    node_id="rank", operator_name="cross_sectional_rank",
                    params={"ascending": False},
                ),
                OperatorNode(
                    node_id="sel", operator_name="select_from_series_set",
                    params={"series_key": "u1"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p1", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p2", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="p3", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="z",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="z", target_node_id="rank",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="rank", target_node_id="sel",
                             target_input_slot="series_set"),
            ],
            terminal_node_id="sel",
        )
        return wf, _resolve

    def test_type_gate_accepts_z_then_rank_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_z_then_rank(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.COUNT
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
        zf = frame.sub(frame.mean(axis=1), axis=0).div(
            frame.std(axis=1, ddof=1), axis=0,
        )
        expected = zf.rank(axis=1, method="average", ascending=False)["u1"]
        np.testing.assert_allclose(
            terminal.payload.to_numpy(), expected.to_numpy(),
        )
        names = [s.name for s in terminal.lineage.steps]
        assert names[-3:] == [
            "cross_sectional_zscore", "cross_sectional_rank",
            "select_from_series_set",
        ]
        clear_tool_config_cache()
