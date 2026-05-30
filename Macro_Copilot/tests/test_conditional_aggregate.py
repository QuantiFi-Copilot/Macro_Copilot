"""Tests for shared.operators.conditional_aggregate.

Final operator of Phase 1A — closes the event-study skeleton.

Per the v5 plan, output is a typed ``Series`` (length =
window_length).  Index is a synthetic ``DatetimeIndex`` anchored at
``1970-01-01`` (offset days encoded as ``anchor + Timedelta(days=k)``);
the integer offsets, dispersions, n_observations, and low_n flags
are recorded in the operator step's lineage params so consumers can
recover them without reverse-engineering the index encoding.

Covers:

  - structural-input checks (WindowedPanel-only; reject empty panel)
  - every aggregator (mean / median / std / count) computed against
    INDEPENDENTLY hand-computed references
  - dispersion='std' vs 'none' (recorded in lineage)
  - count + std combo REJECTED at schema level (Codex P1.B follow-up)
  - count emits COUNT units (Codex P1.B follow-up)
  - NaN-aware reductions
  - per-offset low_n flag recorded in lineage params
  - units inheritance + count override
  - YAML default authority
  - lineage propagation + JSON round-trip
  - finance-blindness
  - PHASE 1A ARCHITECTURE-PROOF test using REAL clean_single_series
    (Codex P2 follow-up — the previous test synthesised lineage and
    skipped the clean boundary)
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd
import pytest

from shared.analytics.levels import clean_single_series
from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    EventSet,
    FetchStep,
    Lineage,
    OperatorStep,
    Series,
    TimeSeriesUnits,
    WindowedPanel,
)
from shared.artifacts.adapters import raw_dataframe_to_artifact_series
from shared.artifacts.lineage import CleanStep
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.align_series import AlignSeriesParams, align_series
from shared.operators.conditional_aggregate import (
    CONFIG_PATH,
    ConditionalAggregateParams,
    conditional_aggregate,
)
from shared.operators.conditional_aggregate.operator import (
    ConditionalAggregateError,
    _OFFSET_ANCHOR,
)
from shared.operators.event_windows import EventWindowsParams, event_windows
from shared.operators.series_arithmetic import series_arithmetic
from shared.operators.threshold_events import (
    ThresholdEventsParams,
    threshold_events,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


# ===========================================================================
# Helpers
# ===========================================================================


def _make_panel(
    *,
    payload: np.ndarray,
    offsets: List[int],
    units: TimeSeriesUnits = TimeSeriesUnits.BPS,
    target_series_key: str = "ust_10y",
) -> WindowedPanel:
    """Build a synthetic WindowedPanel directly for math tests."""
    n_events = payload.shape[0]
    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0",
        params={"target_series_key": target_series_key},
    )
    op = OperatorStep.build(
        name="event_windows", version="1.0",
        params={"synthetic": True}, input_hashes=(fetch.hash,),
    )
    return WindowedPanel(
        payload=payload,
        offsets=offsets,
        event_dates=[pd.Timestamp("2026-01-02") + pd.Timedelta(days=i)
                     for i in range(n_events)],
        per_event_metadata=[{} for _ in range(n_events)],
        target_series_key=target_series_key,
        units=units,
        lineage=Lineage.from_steps([fetch, op]),
    )


def _series(
    series_key: str, *, dates, values,
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
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


def _expected_index(offsets: List[int]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(
        [_OFFSET_ANCHOR + pd.Timedelta(days=k) for k in offsets]
    )


# ===========================================================================
# 1. Structural input checks
# ===========================================================================


class TestStructuralInputChecks:
    def test_non_panel_input_raises(self):
        with pytest.raises(
            ConditionalAggregateError, match="must be a WindowedPanel"
        ):
            conditional_aggregate("not a panel")  # type: ignore[arg-type]

    def test_empty_panel_raises(self):
        empty = _make_panel(
            payload=np.empty((0, 3), dtype=float),
            offsets=[-1, 0, 1],
        )
        with pytest.raises(ConditionalAggregateError, match="0 events"):
            conditional_aggregate(empty)


# ===========================================================================
# 2. Aggregator math (vs independent hand-computed reference)
# ===========================================================================


class TestAggregatorMath:
    def _panel_5_events_3_offsets(self):
        payload = np.array([
            [-10.0, 0.0,  5.0],
            [-12.0, 1.0,  6.0],
            [ -8.0, 2.0,  4.0],
            [-11.0, 0.5,  5.5],
            [ -9.0, 1.5,  4.5],
        ])
        return _make_panel(payload=payload, offsets=[-1, 0, 1])

    def test_mean(self):
        panel = self._panel_5_events_3_offsets()
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=1,
            ),
        )
        assert isinstance(out, Series)
        # Hand reference:
        np.testing.assert_allclose(
            out.payload.values, [-10.0, 1.0, 5.0], rtol=1e-12,
        )
        # Index is the synthetic-anchor DatetimeIndex.
        assert list(out.payload.index) == list(_expected_index([-1, 0, 1]))

    def test_median(self):
        panel = self._panel_5_events_3_offsets()
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="median", dispersion="none", min_n=1,
            ),
        )
        np.testing.assert_allclose(
            out.payload.values, [-10.0, 1.0, 5.0], rtol=1e-12,
        )

    def test_std_uses_sample_ddof_one(self):
        """Codex P2 follow-up: ddof is no longer a public knob;
        operator hardcodes ddof=1 (sample std).  Verify against numpy
        reference."""
        panel = self._panel_5_events_3_offsets()
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="std", dispersion="none", min_n=1,
            ),
        )
        ref = np.std(panel.payload, axis=0, ddof=1)
        np.testing.assert_allclose(out.payload.values, ref, rtol=1e-12)
        # Lineage records the fixed ddof so future readers can audit.
        assert out.lineage.steps[-1].params["ddof_used"] == 1

    def test_count(self):
        panel = self._panel_5_events_3_offsets()
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="count", dispersion="none", min_n=1,
            ),
        )
        np.testing.assert_array_equal(out.payload.values, [5.0, 5.0, 5.0])

    def test_ddof_is_a_caller_param(self):
        """ddof is now a caller-tunable param (OPR7) — default 1 (sample
        std); ddof=0 (population std) is accepted."""
        p = ConditionalAggregateParams(
            aggregator="std", dispersion="none", min_n=1, ddof=0,
        )
        assert p.ddof == 0
        assert ConditionalAggregateParams().ddof == 1  # default


# ===========================================================================
# 3. Dispersion + count + std cross-field constraint
# ===========================================================================


class TestDispersion:
    def test_dispersion_std_recorded_in_lineage_per_offset(self):
        payload = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="std", min_n=1,
            ),
        )
        ref = np.std(payload, axis=0, ddof=1).tolist()
        recorded = out.lineage.steps[-1].params["dispersions_per_offset"]
        np.testing.assert_allclose(recorded, ref, rtol=1e-12)

    def test_dispersion_none_records_none_in_lineage(self):
        payload = np.array([[1.0, 2.0], [3.0, 4.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=1,
            ),
        )
        assert out.lineage.steps[-1].params["dispersions_per_offset"] is None

    def test_count_with_std_dispersion_rejected_at_schema(self):
        """Codex P1.B follow-up: the COUNT units of the values column
        and the panel-units of the dispersion column cannot share
        one ``units`` field coherently — the combo is rejected at
        schema validation."""
        with pytest.raises(ValueError, match="not a coherent combination"):
            ConditionalAggregateParams(
                aggregator="count", dispersion="std", min_n=1,
            )


# ===========================================================================
# 4. NaN-aware reductions
# ===========================================================================


class TestNanAwareReductions:
    def test_per_cell_nans_excluded_from_mean(self):
        payload = np.array([
            [10.0, 20.0],
            [np.nan, 22.0],
            [12.0, np.nan],
        ])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=1,
            ),
        )
        np.testing.assert_allclose(out.payload.values, [11.0, 21.0], rtol=1e-12)
        n = out.lineage.steps[-1].params["n_observations_per_offset"]
        assert n == [2, 2]

    def test_all_nan_column_yields_nan_value(self):
        payload = np.array([
            [np.nan, 1.0],
            [np.nan, 2.0],
            [np.nan, 3.0],
        ])
        panel = _make_panel(payload=payload, offsets=[-1, 0])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=1,
            ),
        )
        assert np.isnan(out.payload.iloc[0])
        n = out.lineage.steps[-1].params["n_observations_per_offset"]
        assert n[0] == 0
        assert out.payload.iloc[1] == 2.0


# ===========================================================================
# 5. low_n flag (recorded in lineage params)
# ===========================================================================


class TestLowNFlag:
    def test_low_n_set_when_below_min(self):
        payload = np.array([[1.0, 2.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=5,
            ),
        )
        flags = out.lineage.steps[-1].params["low_n_flags_per_offset"]
        assert flags == [True, True]

    def test_low_n_cleared_when_at_or_above_min(self):
        payload = np.array([
            [1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0], [9.0, 10.0],
        ])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=5,
            ),
        )
        flags = out.lineage.steps[-1].params["low_n_flags_per_offset"]
        assert flags == [False, False]

    def test_low_n_per_offset_is_independent(self):
        payload = np.array([
            [1.0, 2.0],
            [np.nan, 4.0],
            [5.0, 6.0],
        ])
        panel = _make_panel(payload=payload, offsets=[-1, 0])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=3,
            ),
        )
        flags = out.lineage.steps[-1].params["low_n_flags_per_offset"]
        assert flags == [True, False]

    def test_low_n_does_not_suppress_value(self):
        payload = np.array([[1.0]])
        panel = _make_panel(payload=payload, offsets=[0])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=100,
            ),
        )
        assert out.payload.iloc[0] == 1.0  # value still reported
        assert out.lineage.steps[-1].params["low_n_flags_per_offset"] == [True]


# ===========================================================================
# 6. Units inheritance + count override (Codex P1.B follow-up)
# ===========================================================================


class TestUnitsInheritance:
    def test_panel_units_pass_through_for_mean(self):
        payload = np.array([[1.0, 2.0], [3.0, 4.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1], units=TimeSeriesUnits.BPS)
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="std", min_n=1,
            ),
        )
        assert out.units == TimeSeriesUnits.BPS

    def test_count_aggregator_emits_COUNT_units(self):
        """Codex P1.B follow-up: counts are NOT in the panel's units —
        the operator must override to ``TimeSeriesUnits.COUNT``."""
        payload = np.array([[1.0, 2.0], [3.0, 4.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1], units=TimeSeriesUnits.BPS)
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="count", dispersion="none", min_n=1,
            ),
        )
        assert out.units == TimeSeriesUnits.COUNT
        # Lineage records the unit transition.
        params = out.lineage.steps[-1].params
        assert params["input_units"] == "bps"
        assert params["output_units"] == "count"


# ===========================================================================
# 7. YAML default authority
# ===========================================================================


class TestYamlDefaultAuthority:
    def test_caller_omits_aggregator_resolves_from_yaml(self):
        payload = np.array([[1.0, 2.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        params = ConditionalAggregateParams(min_n=1)
        assert params.aggregator is None
        assert params.dispersion is None
        out = conditional_aggregate(panel, params)
        assert out.lineage.steps[-1].params["aggregator"] == "mean"
        assert out.lineage.steps[-1].params["dispersion"] == "std"

    def test_yaml_default_change_propagates_at_runtime(self, tmp_path):
        import yaml as _yaml
        from shared.config.operator_config import (
            clear_operator_config_cache, load_operator_config,
        )
        bundled = _yaml.safe_load(CONFIG_PATH.read_text())
        bundled["defaults"]["aggregator"]["value"] = "median"
        tweaked_path = tmp_path / "config.yaml"
        tweaked_path.write_text(_yaml.safe_dump(bundled))

        clear_operator_config_cache()
        tweaked_cfg = load_operator_config(tweaked_path)
        assert tweaked_cfg.default_value("aggregator") == "median"

        payload = np.array([[1.0, 2.0], [10.0, 20.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel, ConditionalAggregateParams(min_n=1), config=tweaked_cfg,
        )
        assert out.lineage.steps[-1].params["aggregator"] == "median"
        np.testing.assert_allclose(out.payload.values, [5.5, 11.0], rtol=1e-12)


# ===========================================================================
# 8. Lineage propagation
# ===========================================================================


class TestLineagePropagation:
    def test_appends_operator_step(self):
        payload = np.array([[1.0, 2.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=1,
            ),
        )
        kinds = [s.kind for s in out.lineage.steps]
        # Panel's lineage was [fetch, operator(event_windows synthetic)] →
        # output appends a third step.
        assert kinds == ["fetch", "operator", "operator"]
        assert out.lineage.steps[-1].name == "conditional_aggregate"

    def test_step_records_per_offset_metadata(self):
        payload = np.array([[1.0, 2.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="median", dispersion="std", min_n=10,
            ),
        )
        params = out.lineage.steps[-1].params
        assert params["aggregator"] == "median"
        assert params["dispersion"] == "std"
        assert params["min_n"] == 10
        assert params["n_events_in"] == 1
        assert params["window_length"] == 2
        assert params["n_offsets_low_n"] == 2
        # All four per-offset metadata lists present + correct length.
        for key in (
            "event_relative_offsets",
            "n_observations_per_offset",
            "low_n_flags_per_offset",
            "dispersions_per_offset",
        ):
            assert len(params[key]) == 2

    def test_offset_anchor_recorded_in_lineage(self):
        """Consumers can decode the synthetic-DatetimeIndex without
        reverse-engineering the encoding."""
        payload = np.array([[1.0, 2.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=1,
            ),
        )
        assert out.lineage.steps[-1].params["offset_anchor"] == "1970-01-01"
        assert out.lineage.steps[-1].params["event_relative_offsets"] == [0, 1]

    def test_lineage_json_roundtrip(self):
        payload = np.array([[1.0, 2.0], [3.0, 4.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="std", min_n=1,
            ),
        )
        as_dict = out.lineage.model_dump(mode="json")
        rec = Lineage.model_validate(as_dict)
        assert rec.head_hash == out.lineage.head_hash


# ===========================================================================
# 9. Bundled config
# ===========================================================================


class TestBundledConfig:
    def test_config_path_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads_with_correct_identity(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "conditional_aggregate"
        assert cfg.operator.method_family == "aggregation"


# ===========================================================================
# 10. Finance-blindness
# ===========================================================================


class TestFinanceBlindness:
    def test_runs_unchanged_on_z_score_panel(self):
        payload = np.array([[1.0, 2.0], [3.0, 4.0]])
        panel = _make_panel(
            payload=payload, offsets=[0, 1], units=TimeSeriesUnits.Z_SCORE,
        )
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="std", min_n=1,
            ),
        )
        assert out.units == TimeSeriesUnits.Z_SCORE


# ===========================================================================
# 11. PHASE 1A ARCHITECTURE-PROOF — full Q1 chain through REAL clean_single_series
# ===========================================================================


class TestPhase1AArchitectureProof:
    """The defining test of Phase 1A.

    Codex P2 follow-up: previously this used the local _series()
    helper which only synthesised FetchStep + AdapterStep, skipping
    the real ``clean_single_series`` boundary.  This rewrite runs
    the actual ``shared.analytics.levels.clean_single_series`` on a
    fetch-shaped DataFrame and emits a real ``CleanStep`` in the
    lineage chain — so the agreed
    ``fetch -> clean -> adapter -> align -> arithmetic -> threshold ->
    event_windows -> conditional_aggregate`` path is exercised end-to-end.
    """

    def _build_artifact_series_through_clean(
        self, *, series_key: str, dates, values, frequency=None,
    ) -> Series:
        """Run the REAL clean_single_series on a fetch-shaped DataFrame
        and emit an artifact Series with explicit FetchStep + CleanStep +
        AdapterStep lineage.  Same pattern as PR #53's threshold_events
        composition test."""
        raw_df = pd.DataFrame({
            "trade_date": [d.date() if hasattr(d, "date") else d for d in dates],
            "field_value": values,
        })
        fetch_step = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": series_key,
                    "field_name": "YLD_YTM_MID"},
        )
        cleaned_df = clean_single_series(raw_df, ffill_limit=5)
        clean_step = CleanStep.build(
            name="clean_single_series", version="1.0.0",
            params={"ffill_limit": 5},
            input_hashes=(fetch_step.hash,),
        )
        return raw_dataframe_to_artifact_series(
            cleaned_df,
            series_key=series_key,
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={"curve_family": "UST", "tenor": series_key,
                           "field_name": "YLD_YTM_MID"},
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            frequency=frequency,
            upstream_lineage=(fetch_step, clean_step),
        )

    def test_full_q1_chain_through_real_clean(self):
        """Full DAG end-to-end with the real clean_single_series."""
        idx = list(pd.bdate_range("2026-01-02", periods=30))

        ust_2y_vals = [4.50] * 30
        ust_2y_vals[10] = 4.85
        ust_2y_vals[20] = 4.90
        ois_2y_vals = [4.30] * 30
        ust_10y_vals = [4.20] * 30
        for spike_day in (10, 20):
            for k in range(0, 4):
                if spike_day + k < 30:
                    ust_10y_vals[spike_day + k] = 4.20 + (k + 1) * 0.05

        ust_2y = self._build_artifact_series_through_clean(
            series_key="ust_2y", dates=idx, values=ust_2y_vals,
        )
        ois_2y = self._build_artifact_series_through_clean(
            series_key="ois_2y", dates=idx, values=ois_2y_vals,
        )
        ust_10y = self._build_artifact_series_through_clean(
            series_key="ust_10y", dates=idx, values=ust_10y_vals,
        )

        # All three artifact series must carry a real CleanStep in
        # their lineage — this is what makes the boundary claim honest.
        for s in (ust_2y, ois_2y, ust_10y):
            kinds = [step.kind for step in s.lineage.steps]
            assert kinds == ["fetch", "clean", "adapter"], (
                f"input series {s.series_key} should carry fetch + clean + "
                f"adapter, got {kinds}"
            )

        aligned = align_series([ust_2y, ois_2y, ust_10y], AlignSeriesParams())
        spread = series_arithmetic(
            aligned.get_series("ust_2y"),
            "subtract",
            aligned.get_series("ois_2y"),
        )
        assert spread.units == TimeSeriesUnits.PERCENT

        events = threshold_events(spread, ThresholdEventsParams(
            rule="above", threshold=0.30,
        ))
        assert events.n_events == 2

        panel = event_windows(
            events,
            aligned.get_series("ust_10y"),
            EventWindowsParams(
                pre_window=1, post_window=3,
                units_basis="level_change",
            ),
        )
        assert panel.units == TimeSeriesUnits.PERCENT  # F1: unit-preserving

        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="std", min_n=2,
            ),
        )

        # ---- Output checks (per v5 plan: typed Series) ----
        assert isinstance(out, Series)
        assert out.units == TimeSeriesUnits.PERCENT  # F1: unit-preserving
        assert len(out.payload) == 5  # pre=1 + event_day + post=3
        assert np.all(np.isfinite(out.payload.values))

        # ---- Per-offset metadata in lineage params ----
        params = out.lineage.steps[-1].params
        assert params["event_relative_offsets"] == [-1, 0, 1, 2, 3]
        assert params["n_observations_per_offset"] == [2, 2, 2, 2, 2]
        assert params["low_n_flags_per_offset"] == [False, False, False, False, False]
        assert len(params["dispersions_per_offset"]) == 5

        # ---- Lineage walk: every step accounted for, INCLUDING clean ----
        # Primary chain: fetch (ust_2y) + clean + adapter + align +
        # arithmetic + threshold + event_windows + conditional_aggregate
        # = 8 steps.
        kinds = [s.kind for s in out.lineage.steps]
        assert kinds == [
            "fetch", "clean", "adapter",
            "operator", "operator", "operator",
            "operator", "operator",
        ], f"unexpected lineage kinds: {kinds}"
        # The clean step is the REAL clean_single_series.
        assert out.lineage.steps[1].name == "clean_single_series"

        op_names = [
            s.name for s in out.lineage.steps if s.kind == "operator"
        ]
        assert op_names == [
            "align_series", "series_arithmetic", "threshold_events",
            "event_windows", "conditional_aggregate",
        ]

        # ---- Right-hand chain (ois_2y) preserved on arithmetic step
        #      and INCLUDES the real CleanStep ----
        arithmetic_step = out.lineage.steps[4]
        assert len(arithmetic_step.auxiliary_lineages) == 1
        right_chain = arithmetic_step.auxiliary_lineages[0]
        right_kinds = [s.kind for s in right_chain.steps]
        assert right_kinds == ["fetch", "clean", "adapter", "operator"]
        assert right_chain.steps[1].name == "clean_single_series"

        # ---- Target chain (ust_10y) preserved on event_windows step
        #      and ALSO includes the real CleanStep ----
        ew_step = out.lineage.steps[6]
        assert len(ew_step.auxiliary_lineages) == 1
        target_chain = ew_step.auxiliary_lineages[0]
        target_kinds = [s.kind for s in target_chain.steps]
        assert target_kinds == ["fetch", "clean", "adapter", "operator"]
        assert target_chain.steps[1].name == "clean_single_series"

    def test_full_q1_chain_lineage_json_roundtrips(self):
        """Lineage chain (incl. recursive auxiliary_lineages with real
        CleanSteps) survives JSON round-trip byte-identically."""
        idx = list(pd.bdate_range("2026-01-02", periods=15))
        a = self._build_artifact_series_through_clean(
            series_key="a", dates=idx,
            values=[1.0 + i * 0.1 for i in range(15)],
        )
        b = self._build_artifact_series_through_clean(
            series_key="b", dates=idx, values=[0.5] * 15,
        )
        aligned = align_series([a, b])
        diff = series_arithmetic(
            aligned.get_series("a"), "subtract", aligned.get_series("b"),
        )
        events = threshold_events(diff, ThresholdEventsParams(
            rule="above", threshold=0.7,
        ))
        panel = event_windows(
            events, aligned.get_series("a"),
            EventWindowsParams(post_window=2, units_basis="raw"),
        )
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="std", min_n=1,
            ),
        )

        as_dict = out.lineage.model_dump(mode="json")
        rec = Lineage.model_validate(as_dict)
        assert rec.head_hash == out.lineage.head_hash
        # Real CleanStep survives in primary AND auxiliary chains.
        assert rec.steps[1].name == "clean_single_series"
        rec_arithmetic = rec.steps[4]
        rec_ew = rec.steps[6]
        assert len(rec_arithmetic.auxiliary_lineages) == 1
        assert len(rec_ew.auxiliary_lineages) == 1
        assert rec_arithmetic.auxiliary_lineages[0].steps[1].name == "clean_single_series"
        assert rec_ew.auxiliary_lineages[0].steps[1].name == "clean_single_series"
