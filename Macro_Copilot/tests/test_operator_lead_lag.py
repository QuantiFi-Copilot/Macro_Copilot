"""Tests for shared.operators.lead_lag — Track-A A2 operator.

Covers the contract surface every standard operator must satisfy:

  - happy path vs a manual shifted-correlation reference at every lag
  - known-lag synthetic signal recovers the peak at the right lag AND
    the pinned sign convention (positive k = left leads right)
  - reversal property: lead_lag(left, right) at k == (right, left) at −k
  - lag-0 equals the full-sample correlation; self-CCF peaks at 1.0
  - the offset-anchor encoding equals conditional_aggregate's
    design-locked anchor (consistency test)
  - OPR2 constant output type (always Series, RATIO, frequency=None)
  - OPR8 params=None succeeds + matches explicit config resolution;
    schema mirrors YAML; kendall refuses cleanly
  - OPR11 strict-by-default frequency + missingness with opt-outs;
    cross-unit inputs accepted with both units recorded
  - OPR13: per-lag NaN for insufficient overlap / zero variance;
    all-NaN profile → typed refusal; misaligned index refusal
  - OPR10 lineage (lag vector, sign convention, per-lag overlap
    counts) + OPR14 rerun determinism
  - OPR6 finance-blindness (non-rates synthetic data)
  - composition: type-gate accepts lead_lag and a DAG executes
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
    RawNoCleaning,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.lead_lag import (
    CONFIG_PATH,
    LeadLagError,
    LeadLagParams,
    lead_lag,
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
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
    frequency=None,
    missingness_policy=None,
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
        frequency=frequency,
        missingness_policy=missingness_policy or CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


_N = 120
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _pair(seed: int = 13):
    rng = np.random.RandomState(seed)
    xv = rng.randn(_N).cumsum()
    yv = 0.5 * xv + rng.randn(_N) * 0.4
    return (
        _series("a", dates=_DATES, values=xv),
        _series("b", dates=_DATES, values=yv),
        xv,
        yv,
    )


def _manual_ccf(xv, yv, k, method="pearson"):
    """corr(left_t, right_{t+k}) computed by hand with numpy slices."""
    n = len(xv)
    if k >= 0:
        l, r = xv[: n - k] if k else xv, yv[k:]
    else:
        l, r = xv[-k:], yv[: n + k]
    mask = np.isfinite(l) & np.isfinite(r)
    return float(
        pd.Series(l[mask]).corr(pd.Series(r[mask]), method=method),
    )


# ===========================================================================
# 1. Happy path vs manual reference + signal-recovery + properties
# ===========================================================================


class TestHappyPath:
    def test_matches_manual_reference_at_every_lag(self):
        left, right, xv, yv = _pair()
        out = lead_lag(left, right, params=LeadLagParams(max_lag=5))
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.RATIO
        assert out.frequency is None
        assert len(out.payload) == 11
        for i, k in enumerate(range(-5, 6)):
            assert out.payload.iloc[i] == pytest.approx(
                _manual_ccf(xv, yv, k),
            ), f"lag {k}"

    def test_known_lag_signal_recovers_peak_and_sign(self):
        """right_t = left_{t-3} + noise: left's moves appear in right
        3 rows LATER, so left LEADS right by 3 and the CCF must peak
        at lag +3 under the pinned convention."""
        rng = np.random.RandomState(7)
        base = rng.randn(_N).cumsum()
        lagged = np.roll(base, 3) + rng.randn(_N) * 0.05
        lagged[:3] = np.nan
        left = _series("leader", dates=_DATES, values=base)
        right = _series("follower", dates=_DATES, values=lagged)
        out = lead_lag(left, right, params=LeadLagParams(max_lag=6))
        lags = list(range(-6, 7))
        peak_lag = lags[int(np.nanargmax(out.payload.to_numpy()))]
        assert peak_lag == 3
        assert float(np.nanmax(out.payload.to_numpy())) > 0.95

    def test_reversal_property(self):
        """lead_lag(left, right) at lag k == lead_lag(right, left) at
        lag −k — documented reversibility, never a refusal."""
        left, right, _, _ = _pair()
        p = LeadLagParams(max_lag=4)
        ab = lead_lag(left, right, params=p).payload.to_numpy()
        ba = lead_lag(right, left, params=p).payload.to_numpy()
        np.testing.assert_allclose(ab, ba[::-1], rtol=0, atol=1e-12)

    def test_lag_zero_equals_full_sample_correlation(self):
        left, right, xv, yv = _pair()
        out = lead_lag(left, right, params=LeadLagParams(max_lag=2))
        lag0 = out.payload.iloc[2]
        expected = pd.Series(xv).corr(pd.Series(yv))
        assert lag0 == pytest.approx(float(expected))

    def test_self_ccf_peaks_at_one_at_lag_zero(self):
        left, _, xv, _ = _pair()
        left2 = _series("a2", dates=_DATES, values=xv)
        out = lead_lag(left, left2, params=LeadLagParams(max_lag=3))
        assert out.payload.iloc[3] == pytest.approx(1.0)

    def test_spearman_variant(self):
        left, right, xv, yv = _pair()
        out = lead_lag(
            left, right, params=LeadLagParams(max_lag=2, method="spearman"),
        )
        for i, k in enumerate(range(-2, 3)):
            assert out.payload.iloc[i] == pytest.approx(
                _manual_ccf(xv, yv, k, method="spearman"),
            ), f"lag {k}"

    def test_offset_anchor_matches_conditional_aggregate(self):
        """DESIGN LOCK: the substrate has ONE offset-encoding anchor."""
        from shared.operators.conditional_aggregate.operator import (
            _OFFSET_ANCHOR as CA_ANCHOR,
        )
        from shared.operators.lead_lag.operator import (
            _OFFSET_ANCHOR as LL_ANCHOR,
        )
        assert LL_ANCHOR == CA_ANCHOR

    def test_index_is_anchor_plus_lag_days(self):
        left, right, _, _ = _pair()
        out = lead_lag(left, right, params=LeadLagParams(max_lag=2))
        expected = pd.DatetimeIndex(
            [pd.Timestamp("1970-01-01") + pd.Timedelta(days=k)
             for k in range(-2, 3)]
        )
        assert out.payload.index.equals(expected)

    def test_series_key_names_both_inputs(self):
        left, right, _, _ = _pair()
        out = lead_lag(left, right, params=LeadLagParams(max_lag=2))
        assert out.series_key == "lead_lag__a__b"


# ===========================================================================
# 2. OPR8 — config defaults + schema mirror + honest refusal
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        left, right, _, _ = _pair()
        out_default = lead_lag(left, right)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = LeadLagParams(
            max_lag=int(cfg.default_value("max_lag")),
            method=cfg.default_value("method"),
            min_periods=int(cfg.default_value("min_periods")),
        )
        out_explicit = lead_lag(left, right, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash
        assert len(out_default.payload) == 21  # 2*10+1 at YAML max_lag=10

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = LeadLagParams()
        assert p.max_lag == int(cfg.default_value("max_lag"))
        assert p.method == cfg.default_value("method")
        assert p.min_periods == int(cfg.default_value("min_periods"))

    def test_kendall_refuses_cleanly(self):
        left, right, _, _ = _pair()
        with pytest.raises(NotImplementedError, match="kendall"):
            lead_lag(left, right, params=LeadLagParams(method="kendall"))


# ===========================================================================
# 3. OPR13 — per-lag NaN policy + typed refusals
# ===========================================================================


class TestRefusalsAndNanPolicy:
    def test_non_series_input_raises(self):
        left, _, _, _ = _pair()
        with pytest.raises(LeadLagError, match="must be Series"):
            lead_lag(left, [1, 2, 3])  # type: ignore[arg-type]

    def test_misaligned_index_raises(self):
        left, _, _, _ = _pair()
        other = _series(
            "z", dates=pd.bdate_range("2027-01-04", periods=_N),
            values=list(range(_N)),
        )
        with pytest.raises(LeadLagError, match="identical DatetimeIndex"):
            lead_lag(left, other)

    def test_insufficient_overlap_at_extreme_lags_is_per_lag_nan(self):
        """A short series with a large max_lag: extreme lags have fewer
        than min_periods pairs → NaN at those lags only."""
        n = 12
        dates = pd.bdate_range("2026-01-02", periods=n)
        rng = np.random.RandomState(3)
        left = _series("s1", dates=dates, values=rng.randn(n).cumsum())
        right = _series("s2", dates=dates, values=rng.randn(n).cumsum())
        out = lead_lag(
            left, right, params=LeadLagParams(max_lag=11, min_periods=5),
        )
        vals = out.payload.to_numpy()
        lags = np.array(range(-11, 12))
        # |k| > n - min_periods = 7 → fewer than 5 pairs → NaN
        assert np.isnan(vals[np.abs(lags) > 7]).all()
        assert np.isfinite(vals[np.abs(lags) <= 7]).all()

    def test_zero_variance_arm_is_per_lag_nan_and_all_nan_refuses(self):
        left = _series("const", dates=_DATES, values=[5.0] * _N)
        _, right, _, _ = _pair()
        with pytest.raises(LeadLagError, match="all-NaN"):
            lead_lag(left, right, params=LeadLagParams(max_lag=3))

    def test_all_nan_from_no_overlap_refuses(self):
        n = 8
        dates = pd.bdate_range("2026-01-02", periods=n)
        left = _series("s1", dates=dates, values=[1.0] + [np.nan] * (n - 1))
        right = _series(
            "s2", dates=dates, values=[np.nan] * (n - 1) + [2.0],
        )
        with pytest.raises(LeadLagError, match="all-NaN"):
            lead_lag(left, right, params=LeadLagParams(max_lag=2))


# ===========================================================================
# 4. OPR11 — units / frequency / missingness
# ===========================================================================


class TestStructuralMetadata:
    def test_cross_unit_inputs_accepted_units_recorded(self):
        left, right, _, yv = _pair()
        right_bps = _series(
            "bb", dates=_DATES, values=yv, units=TimeSeriesUnits.BPS,
        )
        out = lead_lag(left, right_bps, params=LeadLagParams(max_lag=2))
        head = out.lineage.steps[-1]
        assert head.params["left_units"] == "percent"
        assert head.params["right_units"] == "bps"
        assert out.units == TimeSeriesUnits.RATIO

    def test_frequency_mismatch_strict_raises_and_opt_in(self):
        left, right, xv, yv = _pair()
        l2 = _series("l2", dates=_DATES, values=xv, frequency="B")
        r2 = _series("r2", dates=_DATES, values=yv, frequency="W")
        with pytest.raises(LeadLagError, match="incompatible frequencies"):
            lead_lag(l2, r2, params=LeadLagParams(max_lag=2))
        out = lead_lag(
            l2, r2,
            params=LeadLagParams(max_lag=2, require_matching_frequency=False),
        )
        assert out.frequency is None

    def test_missingness_mismatch_strict_raises_and_opt_in(self):
        left, right, _, yv = _pair()
        r2 = _series(
            "r2", dates=_DATES, values=yv, missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(LeadLagError, match="missingness"):
            lead_lag(left, r2, params=LeadLagParams(max_lag=2))
        out = lead_lag(
            left, r2,
            params=LeadLagParams(
                max_lag=2, require_matching_missingness=False,
            ),
        )
        # output values are fresh derivations on a synthetic index
        assert isinstance(out.missingness_policy, RawNoCleaning)


# ===========================================================================
# 5. OPR10 lineage + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_records_lag_semantics(self):
        left, right, _, _ = _pair()
        out = lead_lag(left, right, params=LeadLagParams(max_lag=3))
        assert len(out.lineage.steps) == len(left.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "lead_lag"
        assert head.auxiliary_lineages == (right.lineage,)
        assert head.params["lag_values"] == list(range(-3, 4))
        assert "left LEADS right" in head.params["sign_convention"]
        assert head.params["offset_anchor"] == "1970-01-01"
        assert len(head.params["n_overlapping_pairs_per_lag"]) == 7
        assert head.params["n_overlapping_pairs_per_lag"][3] == _N  # lag 0

    def test_rerun_is_deterministic(self):
        left, right, _, _ = _pair()
        p = LeadLagParams(max_lag=4)
        out1 = lead_lag(left, right, params=p)
        out2 = lead_lag(left, right, params=p)
        assert out1.lineage.head_hash == out2.lineage.head_hash

    def test_max_lag_changes_identity(self):
        left, right, _, _ = _pair()
        h1 = lead_lag(
            left, right, params=LeadLagParams(max_lag=3),
        ).lineage.head_hash
        h2 = lead_lag(
            left, right, params=LeadLagParams(max_lag=4),
        ).lineage.head_hash
        assert h1 != h2


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(41)
    n = 150
    x = rng.randn(n).cumsum()
    y = np.roll(x, 2) + rng.randn(n) * 0.1
    y[:2] = np.nan
    dates = pd.bdate_range("2026-01-01", periods=n)
    left = _series("sx", dates=dates, values=x, units=TimeSeriesUnits.Z_SCORE)
    right = _series("sy", dates=dates, values=y, units=TimeSeriesUnits.Z_SCORE)
    out = lead_lag(left, right, params=LeadLagParams(max_lag=5))
    lags = list(range(-5, 6))
    peak_lag = lags[int(np.nanargmax(out.payload.to_numpy()))]
    assert peak_lag == 2  # left leads by 2 by construction


# ===========================================================================
# 7. Composition — type-gate + DAG execution
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
                + ((i * 7919) % 13) * 0.25
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
            workflow_id="lead_lag_composition",
            nodes=[
                PrimitiveNode(
                    node_id="pa", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_a", "drift": 0.5},
                ),
                PrimitiveNode(
                    node_id="pb", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_b", "drift": -0.25},
                ),
                OperatorNode(node_id="align", operator_name="align_series"),
                OperatorNode(
                    node_id="sel_a", operator_name="select_from_series_set",
                    params={"series_key": "s_a"},
                ),
                OperatorNode(
                    node_id="sel_b", operator_name="select_from_series_set",
                    params={"series_key": "s_b"},
                ),
                OperatorNode(
                    node_id="ccf", operator_name="lead_lag",
                    params={"max_lag": 4},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="pa", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="pb", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="sel_a",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="align", target_node_id="sel_b",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="sel_a", target_node_id="ccf",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="sel_b", target_node_id="ccf",
                             target_input_slot="right"),
            ],
            terminal_node_id="ccf",
        )
        return wf, _resolve

    def test_type_gate_accepts_lead_lag_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_and_matches_manual_ccf(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert len(terminal.payload) == 9
        n = 60
        xv = np.array([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        yv = np.array([
            100.0 + i * -0.25 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        for i, k in enumerate(range(-4, 5)):
            assert terminal.payload.iloc[i] == pytest.approx(
                _manual_ccf(xv, yv, k),
            ), f"lag {k}"
        assert terminal.lineage.steps[-1].name == "lead_lag"
        clear_tool_config_cache()
