"""Tests for shared.operators.cross_sectional_statistic — Track-A A3.

Covers the contract surface every standard operator must satisfy:

  - parity vs manual pandas axis=1 reductions for EVERY statistic
    (mean/median/std/min/max/sum) and both ddof values for std
  - units passthrough for every statistic; mixed-unit refusal
  - NaN policy: reductions over non-NaN members only; the
    sum-of-all-NaN = 0 pandas pitfall corrected to honest NaN by the
    min_members mask; below-floor dates NaN; all-NaN output refused;
    std of an all-equal date is a legitimate 0.0
  - overflow honesty: a sum of extreme values → typed refusal
  - OPR14 meaningless-param normalisation: ddof nulled in lineage for
    non-std statistics (hash equality across ddof under statistic=mean)
  - lineage: output Series chain = input set chain + one step
  - OPR8 params=None + schema-mirrors-YAML; OPR6 non-rates case
  - composition: dispersion-regime DAG (align → cs_statistic(std) →
    rolling_zscore) validate+execute
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
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.align_series import align_series
from shared.operators.cross_sectional_statistic import (
    CONFIG_PATH,
    CrossSectionalStatisticError,
    CrossSectionalStatisticParams,
    cross_sectional_statistic,
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


def _aligned_set(n_members: int = 5, seed: int = 17):
    rng = np.random.RandomState(seed)
    members = [
        _series(f"m{i}", dates=_DATES, values=rng.randn(_N) * 3 + i)
        for i in range(n_members)
    ]
    return align_series(members), members


# ===========================================================================
# 1. Parity vs pandas for every statistic
# ===========================================================================


class TestHappyPath:
    @pytest.mark.parametrize(
        "statistic", ["mean", "median", "std", "min", "max", "sum"],
    )
    def test_matches_pandas_axis1(self, statistic):
        sset, members = _aligned_set()
        out = cross_sectional_statistic(
            sset, params=CrossSectionalStatisticParams(statistic=statistic),
        )
        frame = pd.DataFrame({m.series_key: m.payload for m in members})
        if statistic == "std":
            expected = frame.std(axis=1, ddof=1)
        else:
            expected = getattr(frame, statistic)(axis=1)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.BPS  # passthrough

    def test_std_ddof_zero(self):
        sset, members = _aligned_set(seed=5)
        out = cross_sectional_statistic(
            sset,
            params=CrossSectionalStatisticParams(statistic="std", ddof=0),
        )
        frame = pd.DataFrame({m.series_key: m.payload for m in members})
        pd.testing.assert_series_equal(
            out.payload, frame.std(axis=1, ddof=0), check_names=False,
        )

    def test_std_of_all_equal_date_is_zero(self):
        members = [
            _series("a", dates=_DATES, values=[4.0] * _N),
            _series("b", dates=_DATES, values=[4.0] * _N),
            _series("c", dates=_DATES, values=[4.0] * _N),
        ]
        sset = align_series(members)
        out = cross_sectional_statistic(
            sset, params=CrossSectionalStatisticParams(statistic="std"),
        )
        np.testing.assert_allclose(out.payload.to_numpy(), 0.0)

    def test_series_key_names_statistic_and_size(self):
        sset, _ = _aligned_set()
        out = cross_sectional_statistic(sset)
        assert out.series_key == "cs_mean__5_members"


# ===========================================================================
# 2. NaN policy + overflow
# ===========================================================================


class TestNanAndOverflow:
    def test_reduction_over_non_nan_members_only(self):
        rng = np.random.RandomState(3)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[7] = np.nan
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = cross_sectional_statistic(sset)
        d = _DATES[7]
        assert out.payload.loc[d] == pytest.approx((v1[7] + v2[7]) / 2)

    def test_sum_of_all_nan_row_is_nan_not_zero(self):
        """pandas sum(axis=1) emits 0.0 for an all-NaN row; the
        min_members mask must correct that to honest NaN."""
        rng = np.random.RandomState(9)
        v0, v1 = rng.randn(_N), rng.randn(_N)
        v0[2], v1[2] = np.nan, np.nan
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
        ]
        sset = align_series(members)
        out = cross_sectional_statistic(
            sset, params=CrossSectionalStatisticParams(statistic="sum"),
        )
        assert np.isnan(out.payload.loc[_DATES[2]])

    def test_below_floor_date_is_nan(self):
        rng = np.random.RandomState(11)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[4], v1[4] = np.nan, np.nan  # one member left
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = cross_sectional_statistic(sset)
        assert np.isnan(out.payload.loc[_DATES[4]])

    def test_overflow_sum_raises_typed_error(self):
        big = np.full(_N, 1e308)
        members = [
            _series("a", dates=_DATES, values=big),
            _series("b", dates=_DATES, values=big.copy()),
            _series("c", dates=_DATES, values=big.copy()),
        ]
        sset = align_series(members)
        with pytest.raises(CrossSectionalStatisticError, match="overflow"):
            cross_sectional_statistic(
                sset, params=CrossSectionalStatisticParams(statistic="sum"),
            )

    def test_all_nan_output_refused(self):
        members = [
            _series("a", dates=_DATES, values=[np.nan] * _N),
            _series("b", dates=_DATES, values=[np.nan] * _N),
        ]
        sset = align_series(members)
        with pytest.raises(CrossSectionalStatisticError, match="all-NaN"):
            cross_sectional_statistic(sset)


# ===========================================================================
# 3. OPR13 refusals + OPR11 units
# ===========================================================================


class TestRefusals:
    def test_non_seriesset_input_raises(self):
        with pytest.raises(CrossSectionalStatisticError, match="SeriesSet"):
            cross_sectional_statistic("nope")  # type: ignore[arg-type]

    def test_single_member_set_refused(self):
        m = _series("solo", dates=_DATES, values=list(range(_N)))
        sset = align_series([m])
        with pytest.raises(CrossSectionalStatisticError, match="at least 2"):
            cross_sectional_statistic(sset)

    def test_mixed_units_refused_with_convert_units_remedy(self):
        a = _series("a", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.BPS)
        b = _series("b", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.PERCENT)
        sset = align_series([a, b])
        with pytest.raises(
            CrossSectionalStatisticError, match="convert_units",
        ):
            cross_sectional_statistic(sset)


# ===========================================================================
# 4. Lineage + OPR14 (ddof nulled for non-std)
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_set_chain_by_one_step(self):
        sset, _ = _aligned_set()
        out = cross_sectional_statistic(sset)
        assert len(out.lineage.steps) == len(sset.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "cross_sectional_statistic"
        assert head.params["n_members"] == 5

    def test_ddof_nulled_for_non_std_statistics(self):
        """OPR14: two mean calls differing only in ddof produce
        byte-identical payloads, so their hashes must match (ddof is
        nulled in lineage params)."""
        sset, _ = _aligned_set()
        h1 = cross_sectional_statistic(
            sset,
            params=CrossSectionalStatisticParams(statistic="mean", ddof=1),
        ).lineage.head_hash
        h0 = cross_sectional_statistic(
            sset,
            params=CrossSectionalStatisticParams(statistic="mean", ddof=0),
        ).lineage.head_hash
        assert h1 == h0

    def test_ddof_changes_identity_for_std(self):
        sset, _ = _aligned_set()
        h1 = cross_sectional_statistic(
            sset,
            params=CrossSectionalStatisticParams(statistic="std", ddof=1),
        ).lineage.head_hash
        h0 = cross_sectional_statistic(
            sset,
            params=CrossSectionalStatisticParams(statistic="std", ddof=0),
        ).lineage.head_hash
        assert h1 != h0

    def test_rerun_is_deterministic(self):
        sset, _ = _aligned_set()
        out1 = cross_sectional_statistic(sset)
        out2 = cross_sectional_statistic(sset)
        assert out1.lineage.head_hash == out2.lineage.head_hash


# ===========================================================================
# 5. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        sset, _ = _aligned_set()
        out_default = cross_sectional_statistic(sset)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = CrossSectionalStatisticParams(
            statistic=cfg.default_value("statistic"),
            ddof=int(cfg.default_value("ddof")),
            min_members=int(cfg.default_value("min_members")),
        )
        out_explicit = cross_sectional_statistic(sset, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = CrossSectionalStatisticParams()
        assert p.statistic == cfg.default_value("statistic")
        assert p.ddof == int(cfg.default_value("ddof"))
        assert p.min_members == int(cfg.default_value("min_members"))


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(59)
    members = [
        _series(
            f"sensor_{i}", dates=_DATES, values=rng.randn(_N) + i,
            units=TimeSeriesUnits.RATIO,
        )
        for i in range(4)
    ]
    sset = align_series(members)
    out = cross_sectional_statistic(
        sset, params=CrossSectionalStatisticParams(statistic="median"),
    )
    frame = pd.DataFrame(
        {f"sensor_{i}": m.payload for i, m in enumerate(members)},
    )
    pd.testing.assert_series_equal(
        out.payload, frame.median(axis=1), check_names=False,
    )
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 7. Composition — dispersion-regime DAG
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
            workflow_id="cs_statistic_composition",
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
                    node_id="disp",
                    operator_name="cross_sectional_statistic",
                    params={"statistic": "std"},
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
                WorkflowEdge(source_node_id="align", target_node_id="disp",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="disp", target_node_id="z",
                             target_input_slot="series"),
            ],
            terminal_node_id="z",
        )
        return wf, _resolve

    def test_type_gate_accepts_dispersion_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_dispersion_then_zscore(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.Z_SCORE
        disp = result.node_artifacts["disp"]
        n = 60
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
        np.testing.assert_allclose(
            disp.payload.to_numpy(),
            frame.std(axis=1, ddof=1).to_numpy(),
        )
        assert disp.units == TimeSeriesUnits.BPS  # passthrough
        clear_tool_config_cache()
