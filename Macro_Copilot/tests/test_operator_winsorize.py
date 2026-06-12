"""Tests for shared.operators.winsorize — Track-A A1.

Covers the contract surface every standard operator must satisfy:

  - quantile-mode parity vs manual clip at the full-sample [q, 1−q]
    empirical quantiles; the LOOK-AHEAD pinned (early values clipped
    by bounds that include later data)
  - absolute mode (both bounds; one-sided lower-only / upper-only)
  - NaN passthrough; clipped-count + resolved bounds in lineage;
    quantile nulled in lineage under absolute mode (OPR14)
  - refusals: non-Series; absolute without bounds; lower >= upper;
    explicit bounds in quantile mode; all-NaN input
  - units/frequency/missingness passthrough
  - OPR8 params=None + schema-mirrors-YAML; OPR14 determinism +
    mode-changes-identity; OPR6 non-rates case
  - composition winsorize→rolling_zscore DAG
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
from shared.operators.winsorize import (
    CONFIG_PATH,
    WinsorizeError,
    WinsorizeParams,
    winsorize,
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
    frequency=None,
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
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


_N = 40
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _input(seed: int = 51):
    rng = np.random.RandomState(seed)
    vals = rng.randn(_N) * 3
    vals[5] = 25.0   # upper outlier
    vals[20] = -25.0  # lower outlier
    return _series("x", dates=_DATES, values=vals), vals


# ===========================================================================
# 1. Quantile mode
# ===========================================================================


class TestQuantileMode:
    def test_matches_manual_full_sample_clip(self):
        s, vals = _input()
        out = winsorize(s, params=WinsorizeParams(quantile=0.05))
        ref = pd.Series(vals, index=_DATES)
        lo, hi = ref.quantile(0.05), ref.quantile(0.95)
        expected = ref.clip(lower=lo, upper=hi)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert isinstance(out, Series)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.BPS  # passthrough

    def test_look_ahead_pinned_early_value_clipped_by_later_data(self):
        """The full-sample scope: an early extreme is clipped using
        quantiles computed over the WHOLE sample — including data that
        comes after it."""
        vals = np.zeros(_N)
        vals[0] = 100.0          # early extreme
        vals[1:] = np.linspace(-1, 1, _N - 1)
        s = _series("la", dates=_DATES, values=vals)
        out = winsorize(s, params=WinsorizeParams(quantile=0.1))
        assert out.payload.iloc[0] < 100.0  # clipped by later data

    def test_outliers_clipped_interior_untouched(self):
        s, vals = _input()
        out = winsorize(s, params=WinsorizeParams(quantile=0.05))
        ref = pd.Series(vals, index=_DATES)
        lo, hi = ref.quantile(0.05), ref.quantile(0.95)
        interior = (ref >= lo) & (ref <= hi)
        pd.testing.assert_series_equal(
            out.payload[interior], ref[interior], check_names=False,
        )
        assert out.payload.max() == pytest.approx(hi)
        assert out.payload.min() == pytest.approx(lo)


# ===========================================================================
# 2. Absolute mode
# ===========================================================================


class TestAbsoluteMode:
    def test_both_bounds(self):
        s, vals = _input()
        out = winsorize(
            s,
            params=WinsorizeParams(
                mode="absolute", lower_bound=-5.0, upper_bound=5.0,
            ),
        )
        expected = pd.Series(vals, index=_DATES).clip(-5.0, 5.0)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )

    def test_upper_only(self):
        s, vals = _input()
        out = winsorize(
            s, params=WinsorizeParams(mode="absolute", upper_bound=5.0),
        )
        expected = pd.Series(vals, index=_DATES).clip(upper=5.0)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )
        assert out.payload.min() == pytest.approx(min(vals))  # lower kept

    def test_lower_only(self):
        s, vals = _input()
        out = winsorize(
            s, params=WinsorizeParams(mode="absolute", lower_bound=-5.0),
        )
        expected = pd.Series(vals, index=_DATES).clip(lower=-5.0)
        pd.testing.assert_series_equal(
            out.payload, expected, check_names=False,
        )


# ===========================================================================
# 3. NaN + lineage audit trail
# ===========================================================================


class TestNanAndLineage:
    def test_nan_positions_untouched(self):
        vals = [1.0, np.nan, 100.0, -100.0, 2.0]
        s = _series("g", dates=_DATES[:5], values=vals)
        out = winsorize(
            s,
            params=WinsorizeParams(
                mode="absolute", lower_bound=-5.0, upper_bound=5.0,
            ),
        )
        assert np.isnan(out.payload.iloc[1])
        assert out.payload.iloc[2] == pytest.approx(5.0)

    def test_lineage_records_resolved_bounds_and_count(self):
        s, vals = _input()
        out = winsorize(s, params=WinsorizeParams(quantile=0.05))
        head = out.lineage.steps[-1]
        assert head.name == "winsorize"
        ref = pd.Series(vals)
        assert head.params["resolved_lower"] == pytest.approx(
            ref.quantile(0.05),
        )
        assert head.params["resolved_upper"] == pytest.approx(
            ref.quantile(0.95),
        )
        assert head.params["n_clipped"] == int(
            ((ref < ref.quantile(0.05)) | (ref > ref.quantile(0.95))).sum()
        )
        assert head.params["quantile_scope"] == "full_sample"

    def test_quantile_nulled_in_lineage_under_absolute_mode(self):
        """OPR14: two absolute calls differing only in the (ignored)
        quantile field must hash identically."""
        s, _ = _input()
        h1 = winsorize(
            s,
            params=WinsorizeParams(
                mode="absolute", quantile=0.05, upper_bound=5.0,
            ),
        ).lineage.head_hash
        h2 = winsorize(
            s,
            params=WinsorizeParams(
                mode="absolute", quantile=0.25, upper_bound=5.0,
            ),
        ).lineage.head_hash
        assert h1 == h2


# ===========================================================================
# 4. OPR13 refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        with pytest.raises(WinsorizeError, match="must be a Series"):
            winsorize(7)  # type: ignore[arg-type]

    def test_absolute_without_bounds_refused(self):
        s, _ = _input()
        with pytest.raises(WinsorizeError, match="at least one"):
            winsorize(s, params=WinsorizeParams(mode="absolute"))

    def test_degenerate_bounds_refused(self):
        s, _ = _input()
        with pytest.raises(WinsorizeError, match="degenerate"):
            winsorize(
                s,
                params=WinsorizeParams(
                    mode="absolute", lower_bound=5.0, upper_bound=-5.0,
                ),
            )

    def test_bounds_in_quantile_mode_refused(self):
        s, _ = _input()
        with pytest.raises(WinsorizeError, match="forbidden"):
            winsorize(
                s,
                params=WinsorizeParams(mode="quantile", upper_bound=5.0),
            )

    def test_all_nan_input_refused(self):
        s = _series("nn", dates=_DATES, values=[np.nan] * _N)
        with pytest.raises(WinsorizeError, match="no finite values"):
            winsorize(s)

    def test_non_finite_bounds_refused(self):
        """Critic finding (OPR13/P6): a NaN/Inf bound would silently
        degrade to 'no bound' — refused per the threshold_events
        non-finite-threshold precedent."""
        s, _ = _input()
        with pytest.raises(WinsorizeError, match="must be finite"):
            winsorize(
                s,
                params=WinsorizeParams(
                    mode="absolute", lower_bound=float("nan"),
                ),
            )
        with pytest.raises(WinsorizeError, match="must be finite"):
            winsorize(
                s,
                params=WinsorizeParams(
                    mode="absolute", upper_bound=float("inf"),
                ),
            )
        with pytest.raises(WinsorizeError, match="must be finite"):
            winsorize(
                s,
                params=WinsorizeParams(
                    mode="absolute",
                    lower_bound=float("nan"),
                    upper_bound=5.0,
                ),
            )

    def test_quantile_bounds_enforced_at_schema(self):
        with pytest.raises(ValueError):
            WinsorizeParams(quantile=0.5)
        with pytest.raises(ValueError):
            WinsorizeParams(quantile=0.0)


# ===========================================================================
# 5. OPR8 + determinism + metadata
# ===========================================================================


class TestParamsConfigDeterminism:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        s, _ = _input()
        out_default = winsorize(s)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = WinsorizeParams(
            mode=cfg.default_value("mode"),
            quantile=float(cfg.default_value("quantile")),
        )
        out_explicit = winsorize(s, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = WinsorizeParams()
        assert p.mode == cfg.default_value("mode")
        assert p.quantile == float(cfg.default_value("quantile"))

    def test_rerun_is_deterministic(self):
        s, _ = _input()
        assert (
            winsorize(s).lineage.head_hash == winsorize(s).lineage.head_hash
        )

    def test_mode_changes_identity(self):
        s, _ = _input()
        h_q = winsorize(s).lineage.head_hash
        h_a = winsorize(
            s, params=WinsorizeParams(mode="absolute", upper_bound=5.0),
        ).lineage.head_hash
        assert h_q != h_a

    def test_metadata_passthrough(self):
        s, _ = _input()
        s2 = _series("y", dates=_DATES, values=s.payload.to_numpy(),
                     frequency="B")
        out = winsorize(s2)
        assert out.frequency == "B"
        assert out.missingness_policy == s2.missingness_policy
        assert out.series_key == "winsorized__y"


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(97)
    vals = rng.randn(_N)
    vals[3] = 50.0
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.RATIO)
    out = winsorize(s, params=WinsorizeParams(quantile=0.1))
    ref = pd.Series(vals, index=_DATES)
    expected = ref.clip(ref.quantile(0.1), ref.quantile(0.9))
    pd.testing.assert_series_equal(out.payload, expected, check_names=False)
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 7. Composition — winsorize → rolling_zscore DAG
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
            n_rows: int = 50
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
            workflow_id="winsorize_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="w", operator_name="winsorize",
                    params={"quantile": 0.1},
                ),
                OperatorNode(
                    node_id="z", operator_name="rolling_zscore",
                    params={"window": 20},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="w",
                             target_input_slot="series"),
                WorkflowEdge(source_node_id="w", target_node_id="z",
                             target_input_slot="series"),
            ],
            terminal_node_id="z",
        )
        return wf, _resolve

    def test_type_gate_accepts_winsorize_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_winsorize_then_zscore(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.Z_SCORE
        w_art = result.node_artifacts["w"]
        n = 50
        vals = pd.Series([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        expected = vals.clip(vals.quantile(0.1), vals.quantile(0.9))
        np.testing.assert_allclose(
            w_art.payload.to_numpy(), expected.to_numpy(),
        )
        clear_tool_config_cache()
