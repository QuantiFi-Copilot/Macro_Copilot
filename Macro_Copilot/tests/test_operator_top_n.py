"""Tests for shared.operators.top_n — Track-A A3.

Covers the contract surface every standard operator must satisfy:

  - parity vs a manual pandas rank-and-mask reference for both modes
  - selection correctness: selected values equal the input, exactly n
    kept per fully-valid date, the right members chosen
  - the NaN disclosure semantics: upstream-NaN members are
    non-contenders; a partially-valid date with >= floor but < n valid
    members keeps ALL of them; below-floor dates mask everything
  - deterministic tie-break (design-locked rank method='first')
  - index/frequency pass through UNCHANGED (dates never dropped)
  - units passthrough; mixed-unit refusal; all-NaN refusal
  - SeriesSet lineage induction + the selection rule recorded in params
  - OPR8 params=None + schema-mirrors-YAML; OPR14 determinism +
    variant-changes-identity
  - OPR6 finance-blindness; composition basket DAG
    (zscore → top_n → cross_sectional_statistic)
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
from shared.operators.top_n import (
    CONFIG_PATH,
    TopNError,
    TopNParams,
    top_n,
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


def _aligned_set(n_members: int = 5, seed: int = 27):
    rng = np.random.RandomState(seed)
    members = [
        _series(f"m{i}", dates=_DATES, values=rng.randn(_N) * 4)
        for i in range(n_members)
    ]
    return align_series(members), members


def _manual_mask(members, n, mode):
    frame = pd.DataFrame({m.series_key: m.payload for m in members})
    ranks = frame.rank(axis=1, method="first", ascending=(mode == "bottom"))
    return frame.where(ranks <= n)


# ===========================================================================
# 1. Selection correctness + parity
# ===========================================================================


class TestHappyPath:
    @pytest.mark.parametrize("mode", ["top", "bottom"])
    def test_matches_manual_reference(self, mode):
        sset, members = _aligned_set()
        out = top_n(sset, params=TopNParams(n=2, mode=mode))
        expected = _manual_mask(members, 2, mode)
        for k in expected.columns:
            pd.testing.assert_series_equal(
                out.series_by_key[k], expected[k], check_names=False,
            )
        assert isinstance(out, SeriesSet)  # OPR2 constant output type

    def test_exactly_n_kept_per_fully_valid_date(self):
        sset, _ = _aligned_set()
        out = top_n(sset, params=TopNParams(n=2))
        frame = pd.DataFrame(out.series_by_key)
        assert (frame.notna().sum(axis=1) == 2).all()

    def test_top_mode_keeps_the_largest(self):
        sset, members = _aligned_set(seed=3)
        out = top_n(sset, params=TopNParams(n=1, mode="top"))
        frame_in = pd.DataFrame({m.series_key: m.payload for m in members})
        frame_out = pd.DataFrame(out.series_by_key)
        for d in _DATES:
            kept = frame_out.loc[d].dropna()
            assert len(kept) == 1
            assert kept.iloc[0] == pytest.approx(frame_in.loc[d].max())

    def test_selected_values_equal_input(self):
        sset, members = _aligned_set(seed=5)
        out = top_n(sset, params=TopNParams(n=3))
        frame_in = pd.DataFrame({m.series_key: m.payload for m in members})
        frame_out = pd.DataFrame(out.series_by_key)
        mask = frame_out.notna()
        np.testing.assert_allclose(
            frame_out[mask].to_numpy()[mask.to_numpy()],
            frame_in[mask].to_numpy()[mask.to_numpy()],
        )

    def test_deterministic_tie_break(self):
        """Design-locked rank(method='first'): position order resolves
        ties deterministically — two identical values at a date select
        the earlier-positioned member for n=1."""
        members = [
            _series("a", dates=_DATES, values=[7.0] * _N),
            _series("b", dates=_DATES, values=[7.0] * _N),
            _series("c", dates=_DATES, values=[1.0] * _N),
        ]
        sset = align_series(members)
        out = top_n(sset, params=TopNParams(n=1, mode="top"))
        assert pd.DataFrame(out.series_by_key)["a"].notna().all()
        assert pd.DataFrame(out.series_by_key)["b"].isna().all()

    def test_units_index_frequency_passthrough(self):
        sset, _ = _aligned_set()
        out = top_n(sset, params=TopNParams(n=2))
        assert all(
            u == TimeSeriesUnits.BPS for u in out.units_by_key.values()
        )
        assert out.common_index.equals(sset.common_index)
        assert out.frequency == sset.frequency
        assert out.keys() == sset.keys()


# ===========================================================================
# 2. NaN semantics
# ===========================================================================


class TestNanSemantics:
    def test_upstream_nan_member_is_non_contender(self):
        rng = np.random.RandomState(7)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[4] = np.nan
        v0_alt = v0.copy()
        members = [
            _series("a", dates=_DATES, values=v0_alt),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = top_n(sset, params=TopNParams(n=2))
        d = _DATES[4]
        # 'a' is NaN upstream → non-contender → NaN in output; the two
        # remaining members are both kept (n=2 of 2 valid).
        assert np.isnan(out.series_by_key["a"].loc[d])
        assert not np.isnan(out.series_by_key["b"].loc[d])
        assert not np.isnan(out.series_by_key["c"].loc[d])

    def test_fewer_than_n_valid_members_keeps_all(self):
        rng = np.random.RandomState(9)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[6] = np.nan
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = top_n(sset, params=TopNParams(n=3))  # n > valid at date 6
        d = _DATES[6]
        assert not np.isnan(out.series_by_key["b"].loc[d])
        assert not np.isnan(out.series_by_key["c"].loc[d])

    def test_below_floor_date_masks_everything(self):
        rng = np.random.RandomState(11)
        v0, v1, v2 = rng.randn(_N), rng.randn(_N), rng.randn(_N)
        v0[3], v1[3] = np.nan, np.nan
        members = [
            _series("a", dates=_DATES, values=v0),
            _series("b", dates=_DATES, values=v1),
            _series("c", dates=_DATES, values=v2),
        ]
        sset = align_series(members)
        out = top_n(sset, params=TopNParams(n=1))
        d = _DATES[3]
        for k in ("a", "b", "c"):
            assert np.isnan(out.series_by_key[k].loc[d])

    def test_all_nan_output_refused(self):
        members = [
            _series("a", dates=_DATES, values=[np.nan] * _N),
            _series("b", dates=_DATES, values=[np.nan] * _N),
        ]
        sset = align_series(members)
        with pytest.raises(TopNError, match="all-NaN"):
            top_n(sset)


# ===========================================================================
# 3. OPR13 refusals + OPR11 units
# ===========================================================================


class TestRefusals:
    def test_non_seriesset_input_raises(self):
        with pytest.raises(TopNError, match="SeriesSet"):
            top_n("nope")  # type: ignore[arg-type]

    def test_single_member_set_refused(self):
        m = _series("solo", dates=_DATES, values=list(range(_N)))
        sset = align_series([m])
        with pytest.raises(TopNError, match="at least 2"):
            top_n(sset)

    def test_mixed_units_refused_with_convert_units_remedy(self):
        a = _series("a", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.BPS)
        b = _series("b", dates=_DATES, values=list(range(_N)),
                    units=TimeSeriesUnits.PERCENT)
        sset = align_series([a, b])
        with pytest.raises(TopNError, match="convert_units"):
            top_n(sset)


# ===========================================================================
# 4. Lineage + determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_set_chain_extends_and_records_selection_rule(self):
        sset, _ = _aligned_set()
        out = top_n(sset, params=TopNParams(n=2, mode="bottom"))
        assert len(out.lineage.steps) == len(sset.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "top_n"
        assert head.params["n"] == 2
        assert head.params["mode"] == "bottom"
        assert head.params["tie_method"] == "first"
        assert "NOT SELECTED" in head.params["selection_semantics"]

    def test_get_series_composes_complete_chain(self):
        sset, _ = _aligned_set()
        out = top_n(sset, params=TopNParams(n=2))
        extracted = out.get_series("m1")
        names = [s.name for s in extracted.lineage.steps]
        assert names[-1] == "top_n"
        assert names[-2] == "align_series"

    def test_rerun_is_deterministic(self):
        sset, _ = _aligned_set()
        p = TopNParams(n=2)
        assert (
            top_n(sset, params=p).lineage.head_hash
            == top_n(sset, params=p).lineage.head_hash
        )

    def test_mode_changes_identity(self):
        sset, _ = _aligned_set()
        h_top = top_n(
            sset, params=TopNParams(n=2, mode="top"),
        ).lineage.head_hash
        h_bot = top_n(
            sset, params=TopNParams(n=2, mode="bottom"),
        ).lineage.head_hash
        assert h_top != h_bot


# ===========================================================================
# 5. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        sset, _ = _aligned_set(n_members=7)
        out_default = top_n(sset)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = TopNParams(
            n=int(cfg.default_value("n")),
            mode=cfg.default_value("mode"),
            min_members=int(cfg.default_value("min_members")),
        )
        out_explicit = top_n(sset, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = TopNParams()
        assert p.n == int(cfg.default_value("n"))
        assert p.mode == cfg.default_value("mode")
        assert p.min_members == int(cfg.default_value("min_members"))

    def test_bounds_enforced(self):
        with pytest.raises(ValueError):
            TopNParams(n=0)
        with pytest.raises(ValueError):
            TopNParams(min_members=1)


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(67)
    members = [
        _series(
            f"sensor_{i}", dates=_DATES, values=rng.randn(_N),
            units=TimeSeriesUnits.Z_SCORE,
        )
        for i in range(6)
    ]
    sset = align_series(members)
    out = top_n(sset, params=TopNParams(n=3))
    frame = pd.DataFrame(out.series_by_key)
    assert (frame.notna().sum(axis=1) == 3).all()


# ===========================================================================
# 7. Composition — basket DAG: zscore → top_n → cs_statistic
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

        node_params = [
            ("u1", 0.5, 7919, 13),
            ("u2", -0.2, 104729, 17),
            ("u3", 0.1, 1299709, 11),
            ("u4", 0.3, 15485863, 19),
        ]
        wf = Workflow(
            workflow_id="top_n_composition",
            nodes=[
                *[
                    PrimitiveNode(
                        node_id=f"p{i}",
                        tool_name="synthetic_primitive_tool",
                        output_field="time_series",
                        params={"series_name": nm, "drift": dr,
                                "pattern_mult": mu, "pattern_mod": mo},
                    )
                    for i, (nm, dr, mu, mo) in enumerate(node_params)
                ],
                OperatorNode(node_id="align", operator_name="align_series"),
                OperatorNode(
                    node_id="sel_top", operator_name="top_n",
                    params={"n": 2, "mode": "top"},
                ),
                OperatorNode(
                    node_id="basket_avg",
                    operator_name="cross_sectional_statistic",
                    params={"statistic": "mean"},
                ),
            ],
            edges=[
                *[
                    WorkflowEdge(source_node_id=f"p{i}",
                                 target_node_id="align",
                                 target_input_slot="series_list")
                    for i in range(4)
                ],
                WorkflowEdge(source_node_id="align",
                             target_node_id="sel_top",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="sel_top",
                             target_node_id="basket_avg",
                             target_input_slot="series_set"),
            ],
            terminal_node_id="basket_avg",
        )
        return wf, _resolve, node_params

    def test_type_gate_accepts_basket_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver, _ = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_basket_average(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver, node_params = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.BPS
        n = 30
        cols = {
            nm: [
                100.0 + i * dr + ((i * mu) % mo) * 0.25 for i in range(n)
            ]
            for nm, dr, mu, mo in node_params
        }
        frame = pd.DataFrame(cols)
        ranks = frame.rank(axis=1, method="first", ascending=False)
        masked = frame.where(ranks <= 2)
        expected = masked.mean(axis=1)
        np.testing.assert_allclose(
            terminal.payload.to_numpy(), expected.to_numpy(),
        )
        names = [s.name for s in terminal.lineage.steps]
        assert names[-2:] == ["top_n", "cross_sectional_statistic"]
        clear_tool_config_cache()
