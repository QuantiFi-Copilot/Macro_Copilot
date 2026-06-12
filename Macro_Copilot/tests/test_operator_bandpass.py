"""Tests for shared.operators.bandpass — Track-A A1.

Covers the contract surface every standard operator must satisfy:

  - the BAND-PASS property (the correctness centerpiece): an in-band
    sinusoid passes, an out-of-band one is blocked
  - parity vs statsmodels cffilter on the interior block
  - the two-sided look-ahead pinned behaviorally + filter_scope in
    lineage
  - low/high REQUIRED (params=None typed refusal naming the fields);
    high > low enforced at the schema
  - NaN two-tier: interior refused with remedy; edge warmup passed
    through; the rolling-chain composes
  - the 2*high floor refused; units/frequency/missingness passthrough
  - OPR14 determinism + band-change identity; OPR6 non-rates case
  - composition bandpass→summarize_series(std) DAG
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel
from statsmodels.tsa.filters.cf_filter import cffilter

from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    FetchStep,
    Lineage,
    ScalarMetric,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.bandpass import (
    BandpassError,
    BandpassParams,
    bandpass,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


def _series(
    series_key: str,
    *,
    values,
    units: TimeSeriesUnits = TimeSeriesUnits.BPS,
    frequency=None,
    start: str = "2024-01-01",
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
    dates = pd.bdate_range(start, periods=len(values))
    payload = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency=frequency,
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


_N = 400
_T = np.arange(_N)


def _sinusoid(period: float, key: str = "x"):
    return _series(key, values=np.sin(2 * np.pi * _T / period))


# ===========================================================================
# 1. The band-pass property + statsmodels parity
# ===========================================================================


class TestBandPassProperty:
    def test_in_band_sinusoid_passes(self):
        # period 12 is inside [6, 32] -> survives the filter.
        s = _sinusoid(12)
        out = bandpass(s, params=BandpassParams(low=6, high=32))
        assert float(out.payload.std()) > 0.4  # ~0.7 amplitude survives
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.BPS  # passthrough

    def test_slow_out_of_band_sinusoid_blocked(self):
        # period 120 is slower than high=32 -> blocked.
        s = _sinusoid(120)
        out = bandpass(s, params=BandpassParams(low=6, high=32))
        assert float(out.payload.std()) < 0.1

    def test_fast_out_of_band_sinusoid_blocked(self):
        # period 3 is faster than low=6 -> blocked.
        s = _sinusoid(3)
        out = bandpass(s, params=BandpassParams(low=6, high=32))
        assert float(out.payload.std()) < 0.1

    def test_matches_statsmodels(self):
        s = _sinusoid(15)
        out = bandpass(s, params=BandpassParams(low=6, high=32))
        cycle, _trend = cffilter(
            s.payload, low=6, high=32, drift=True,
        )
        np.testing.assert_allclose(
            out.payload.to_numpy(),
            np.asarray(cycle, dtype=float),
        )

    def test_metadata_passthrough_and_key(self):
        s = _series("y", values=np.sin(2 * np.pi * _T / 12),
                    frequency="B")
        out = bandpass(s, params=BandpassParams(low=6, high=32))
        assert out.frequency == "B"
        assert out.missingness_policy == s.missingness_policy
        assert out.series_key == "bandpass_cycle__y"
        assert out.units == TimeSeriesUnits.BPS


# ===========================================================================
# 2. The two-sided look-ahead + lineage disclosure
# ===========================================================================


class TestLookAheadAndLineage:
    def test_two_sided_look_ahead_pinned(self):
        """A change in the LATE segment must move EARLY cycle values —
        the CF filter is asymmetric/full-sample (disclosed)."""
        base = np.sin(2 * np.pi * _T / 12)
        s1 = _series("a", values=base)
        v2 = base.copy()
        v2[350:] += 50.0
        s2 = _series("a", values=v2)
        c1 = bandpass(s1, params=BandpassParams(low=6, high=32))
        c2 = bandpass(s2, params=BandpassParams(low=6, high=32))
        assert c1.payload.iloc[0] != pytest.approx(c2.payload.iloc[0])

    def test_lineage_records_band_scope_and_drift(self):
        s = _sinusoid(12)
        out = bandpass(s, params=BandpassParams(low=6, high=32))
        head = out.lineage.steps[-1]
        assert head.name == "bandpass"
        assert head.params["low"] == 6
        assert head.params["high"] == 32
        assert head.params["drift"] is True
        assert head.params["filter"] == "christiano_fitzgerald"
        assert head.params["filter_scope"] == "full_sample_two_sided"
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1


# ===========================================================================
# 3. OPR13 refusals
# ===========================================================================


class TestRefusals:
    def test_params_none_refused_naming_band(self):
        s = _sinusoid(12)
        with pytest.raises(BandpassError, match="low and high"):
            bandpass(s)

    def test_non_series_input_raises(self):
        with pytest.raises(BandpassError, match="must be a Series"):
            bandpass(
                [1.0, 2.0],  # type: ignore[arg-type]
                params=BandpassParams(low=6, high=32),
            )

    def test_interior_nan_refused_with_actionable_remedy(self):
        vals = np.sin(2 * np.pi * _T / 12)
        vals[200] = np.nan
        s = _series("g", values=vals)
        with pytest.raises(BandpassError, match="INTERIOR"):
            bandpass(s, params=BandpassParams(low=6, high=32))

    def test_below_floor_refused(self):
        # n_finite=50 < 2*high=64.
        s = _series("few", values=np.sin(2 * np.pi * np.arange(50) / 12))
        with pytest.raises(BandpassError, match="2 \\* high = 64"):
            bandpass(s, params=BandpassParams(low=6, high=32))

    def test_band_bounds_enforced_at_schema(self):
        with pytest.raises(ValueError):
            BandpassParams(low=6)  # type: ignore[call-arg] — high required
        with pytest.raises(ValueError):
            BandpassParams(low=1, high=32)  # low < 2
        with pytest.raises(ValueError, match="must be > low"):
            BandpassParams(low=32, high=6)  # high <= low


# ===========================================================================
# 3b. Edge-warmup NaN passthrough (the rolling/ewm/lag upstream case)
# ===========================================================================


class TestEdgeNanPassthrough:
    def test_leading_warmup_nan_tolerated_and_passed_through(self):
        vals = np.sin(2 * np.pi * _T / 12)
        vals[:10] = np.nan
        s = _series("w", values=vals)
        out = bandpass(s, params=BandpassParams(low=6, high=32))
        assert out.payload.iloc[:10].isna().all()
        assert out.payload.iloc[10:].notna().all()
        # Parity vs statsmodels on the interior block alone.
        cycle, _trend = cffilter(
            pd.Series(vals[10:]), low=6, high=32, drift=True,
        )
        np.testing.assert_allclose(
            out.payload.iloc[10:].to_numpy(),
            np.asarray(cycle, dtype=float),
        )
        head = out.lineage.steps[-1]
        assert head.params["n_leading_nan"] == 10
        assert head.params["n_trailing_nan"] == 0

    def test_trailing_nan_tolerated(self):
        vals = np.sin(2 * np.pi * _T / 12)
        vals[-8:] = np.nan
        s = _series("t", values=vals)
        out = bandpass(s, params=BandpassParams(low=6, high=32))
        assert out.payload.iloc[-8:].isna().all()
        assert out.payload.iloc[:-8].notna().all()
        assert out.lineage.steps[-1].params["n_trailing_nan"] == 8

    def test_rolling_statistic_chain_composes(self):
        """A rolling output (warmup head) feeds bandpass without
        refusal — the exact upstream the two-tier policy exists for."""
        from shared.operators.rolling_statistic import (
            RollingStatisticParams,
            rolling_statistic,
        )
        s = _series("r", values=np.sin(2 * np.pi * _T / 12)
                    + 0.05 * _T)
        smoothed = rolling_statistic(
            s, params=RollingStatisticParams(statistic="mean", window=10),
        )
        assert smoothed.payload.iloc[:9].isna().all()  # warmup head
        out = bandpass(smoothed, params=BandpassParams(low=6, high=32))
        assert out.payload.iloc[:9].isna().all()
        assert out.payload.iloc[9:].notna().all()


# ===========================================================================
# 4. Determinism + band identity
# ===========================================================================


class TestDeterminism:
    def test_rerun_is_deterministic(self):
        s = _sinusoid(12)
        p = BandpassParams(low=6, high=32)
        assert (
            bandpass(s, params=p).lineage.head_hash
            == bandpass(s, params=p).lineage.head_hash
        )

    def test_band_change_identity(self):
        s = _sinusoid(12)
        h1 = bandpass(
            s, params=BandpassParams(low=6, high=32),
        ).lineage.head_hash
        h2 = bandpass(
            s, params=BandpassParams(low=8, high=32),
        ).lineage.head_hash
        h3 = bandpass(
            s, params=BandpassParams(low=6, high=40),
        ).lineage.head_hash
        assert h1 != h2
        assert h1 != h3


# ===========================================================================
# 5. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(107)
    vals = np.sin(2 * np.pi * _T / 12) + rng.randn(_N) * 0.1
    s = _series("sensor", values=vals, units=TimeSeriesUnits.RATIO)
    out = bandpass(s, params=BandpassParams(low=6, high=32))
    cycle, _trend = cffilter(s.payload, low=6, high=32, drift=True)
    np.testing.assert_allclose(
        out.payload.to_numpy(), np.asarray(cycle, dtype=float),
    )
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 6. Composition — bandpass → summarize_series(std)
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
            n_rows: int = 120
            base_value: float = 100.0
            drift: float = 0.3
            pattern_mult: int = 7919
            pattern_mod: int = 13
            units: str = "bps"

        class _SynthMetrics(BaseModel):
            as_of_date: str

        class _SynthOutput(BaseModel):
            current_metrics: _SynthMetrics
            time_series: TimeSeries

        def _synth(*, engine, params: _SynthInput, config) -> dict:
            bdays = pd.bdate_range("2024-01-01", periods=params.n_rows)
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
            workflow_id="bandpass_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="bp", operator_name="bandpass",
                    params={"low": 6, "high": 32},
                ),
                OperatorNode(
                    node_id="vol", operator_name="summarize_series",
                    params={"statistic": "std", "dispersion": "none"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="bp",
                             target_input_slot="series"),
                WorkflowEdge(source_node_id="bp", target_node_id="vol",
                             target_input_slot="series"),
            ],
            terminal_node_id="vol",
        )
        return wf, _resolve

    def test_type_gate_accepts_bandpass_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_band_then_std(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.BPS
        n = 120
        vals = pd.Series([
            100.0 + i * 0.3 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        cycle, _trend = cffilter(vals, low=6, high=32, drift=True)
        expected_std = float(pd.Series(np.asarray(cycle)).std(ddof=1))
        assert terminal.value == pytest.approx(expected_std, rel=1e-9)
        clear_tool_config_cache()
