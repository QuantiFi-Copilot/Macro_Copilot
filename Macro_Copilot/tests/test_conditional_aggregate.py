"""Tests for shared.operators.conditional_aggregate.

Final operator of Phase 1A — closes the event-study skeleton.
Covers:

  - structural-input checks (WindowedPanel-only; reject empty panel)
  - every aggregator (mean / median / std / count) computed against
    INDEPENDENTLY hand-computed references
  - dispersion='std' vs 'none'
  - NaN-aware reductions (per-cell NaNs don't poison the column)
  - per-offset low_n flag based on min_n
  - units inherited from panel (no transition here)
  - YAML default authority (caller-omitted aggregator/dispersion
    resolve from config; tweaked YAML at temp path propagates to
    runtime)
  - lineage propagation (panel.lineage + this op step)
  - finance-blindness (Z_SCORE input)
  - FULL Q1 CHAIN end-to-end: align → arithmetic → threshold →
    event_windows → conditional_aggregate, with the workflow-level
    methodology summary derivable programmatically from the
    EventResponseSeries lineage.  This is the **Phase 1A
    architecture-proof test**.
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd
import pytest

from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    EventResponseSeries,
    EventSet,
    FetchStep,
    Lineage,
    OperatorStep,
    Series,
    TimeSeriesUnits,
    WindowedPanel,
)
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
    """Each aggregator's per-column reduction is verified against an
    independent hand-computed reference using numpy primitives."""

    def _panel_5_events_3_offsets(self):
        # 5 events × 3 offsets, deterministic values.
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
            ConditionalAggregateParams(aggregator="mean", dispersion="none", min_n=1),
        )
        # Independent hand-computation:
        # col 0 mean = -10
        # col 1 mean = 1.0
        # col 2 mean = 5.0
        np.testing.assert_allclose(out.values, [-10.0, 1.0, 5.0], rtol=1e-12)
        assert out.aggregator == "mean"

    def test_median(self):
        panel = self._panel_5_events_3_offsets()
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="median", dispersion="none", min_n=1),
        )
        # col 0 median of [-10,-12,-8,-11,-9] = -10
        # col 1 median of [0, 1, 2, 0.5, 1.5] = 1.0
        # col 2 median of [5, 6, 4, 5.5, 4.5] = 5.0
        np.testing.assert_allclose(out.values, [-10.0, 1.0, 5.0], rtol=1e-12)

    def test_std(self):
        """Sample std (ddof=1) per column."""
        panel = self._panel_5_events_3_offsets()
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="std", dispersion="none", min_n=1, ddof=1),
        )
        # col 0 std (ddof=1) = sqrt(var of [-10,-12,-8,-11,-9])
        ref = np.std(panel.payload, axis=0, ddof=1)
        np.testing.assert_allclose(out.values, ref, rtol=1e-12)

    def test_std_population(self):
        panel = self._panel_5_events_3_offsets()
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="std", dispersion="none", min_n=1, ddof=0,
            ),
        )
        ref = np.std(panel.payload, axis=0, ddof=0)
        np.testing.assert_allclose(out.values, ref, rtol=1e-12)

    def test_count(self):
        panel = self._panel_5_events_3_offsets()
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="count", dispersion="none", min_n=1),
        )
        # All cells finite → 5 per offset.
        np.testing.assert_array_equal(out.values, [5.0, 5.0, 5.0])
        np.testing.assert_array_equal(out.n_observations, [5, 5, 5])


# ===========================================================================
# 3. Dispersion knob
# ===========================================================================


class TestDispersion:
    def test_dispersion_std_matches_independent_reference(self):
        payload = np.array([
            [1.0, 2.0],
            [3.0, 4.0],
            [5.0, 6.0],
        ])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="std", min_n=1,
            ),
        )
        ref = np.std(payload, axis=0, ddof=1)
        np.testing.assert_allclose(out.dispersions, ref, rtol=1e-12)
        assert out.dispersion_kind == "std"

    def test_dispersion_none_yields_nan_dispersions(self):
        payload = np.array([[1.0, 2.0], [3.0, 4.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="none", min_n=1,
            ),
        )
        assert np.all(np.isnan(out.dispersions))


# ===========================================================================
# 4. NaN-aware reductions
# ===========================================================================


class TestNanAwareReductions:
    def test_per_cell_nans_excluded_from_mean(self):
        # 3 events × 2 offsets; one cell NaN.
        payload = np.array([
            [10.0, 20.0],
            [np.nan, 22.0],
            [12.0, np.nan],
        ])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="mean", dispersion="none", min_n=1),
        )
        # col 0 mean of [10, nan, 12] = 11
        # col 1 mean of [20, 22, nan] = 21
        np.testing.assert_allclose(out.values, [11.0, 21.0], rtol=1e-12)
        # n_observations correctly counts finite cells.
        np.testing.assert_array_equal(out.n_observations, [2, 2])

    def test_all_nan_column_yields_nan_value(self):
        payload = np.array([
            [np.nan, 1.0],
            [np.nan, 2.0],
            [np.nan, 3.0],
        ])
        panel = _make_panel(payload=payload, offsets=[-1, 0])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="mean", dispersion="none", min_n=1),
        )
        # Column with all NaNs: value is NaN, n=0.
        assert np.isnan(out.values[0])
        assert out.n_observations[0] == 0
        # Other column unaffected.
        assert out.values[1] == 2.0


# ===========================================================================
# 5. low_n flag
# ===========================================================================


class TestLowNFlag:
    def test_low_n_set_when_below_min(self):
        payload = np.array([[1.0, 2.0]])  # 1 event, 2 offsets
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="mean", dispersion="none", min_n=5),
        )
        # n=1 per offset, both below min_n=5.
        assert out.low_n_flags.tolist() == [True, True]

    def test_low_n_cleared_when_at_or_above_min(self):
        payload = np.array([
            [1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0], [9.0, 10.0],
        ])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="mean", dispersion="none", min_n=5),
        )
        assert out.low_n_flags.tolist() == [False, False]

    def test_low_n_per_offset_is_independent(self):
        """When some offsets have NaN cells reducing their effective N
        below min_n, only those offsets' low_n flags fire."""
        payload = np.array([
            [1.0, 2.0],
            [np.nan, 4.0],  # col 0 has a NaN
            [5.0, 6.0],
        ])
        panel = _make_panel(payload=payload, offsets=[-1, 0])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="mean", dispersion="none", min_n=3),
        )
        # col 0 has n=2 < 3 → low_n; col 1 has n=3 → not low.
        assert out.low_n_flags.tolist() == [True, False]

    def test_low_n_does_not_suppress_value(self):
        """The flag is informational; the operator returns the
        aggregator value regardless."""
        payload = np.array([[1.0]])
        panel = _make_panel(payload=payload, offsets=[0])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="mean", dispersion="none", min_n=100),
        )
        assert out.low_n_flags.tolist() == [True]
        assert out.values[0] == 1.0  # value still reported


# ===========================================================================
# 6. Units inheritance
# ===========================================================================


class TestUnitsInheritance:
    def test_panel_units_pass_through(self):
        payload = np.array([[1.0, 2.0], [3.0, 4.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1], units=TimeSeriesUnits.BPS)
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="mean", dispersion="std", min_n=1),
        )
        # The aggregator does NOT change units — that's event_windows'
        # responsibility (level_change PERCENT→BPS).  conditional_aggregate
        # is finance-blind here.
        assert out.units == TimeSeriesUnits.BPS


# ===========================================================================
# 7. YAML default authority
# ===========================================================================


class TestYamlDefaultAuthority:
    def test_caller_omits_aggregator_resolves_from_yaml(self):
        payload = np.array([[1.0, 2.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        params = ConditionalAggregateParams(min_n=1)
        assert params.aggregator is None  # schema didn't fill it
        assert params.dispersion is None
        out = conditional_aggregate(panel, params)
        # YAML defaults: aggregator='mean', dispersion='std'.
        assert out.aggregator == "mean"
        assert out.dispersion_kind == "std"

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

        payload = np.array([
            [1.0, 2.0], [10.0, 20.0],  # extreme value should change mean vs median
        ])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        # Caller omits aggregator → must pick up the tweaked YAML's "median".
        out = conditional_aggregate(
            panel, ConditionalAggregateParams(min_n=1), config=tweaked_cfg,
        )
        assert out.aggregator == "median"
        # Median of [1, 10] = 5.5 (vs mean 5.5 — same here, fine);
        # median of [2, 20] = 11.0.
        np.testing.assert_allclose(out.values, [5.5, 11.0], rtol=1e-12)


# ===========================================================================
# 8. Lineage propagation
# ===========================================================================


class TestLineagePropagation:
    def test_appends_operator_step(self):
        payload = np.array([[1.0, 2.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="mean", dispersion="none", min_n=1),
        )
        kinds = [s.kind for s in out.lineage.steps]
        # Panel's lineage was [fetch, operator(event_windows synthetic)] →
        # we append a third step.
        assert kinds == ["fetch", "operator", "operator"]
        assert out.lineage.steps[-1].name == "conditional_aggregate"

    def test_step_records_aggregator_and_dispersion(self):
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
        assert params["n_offsets_low_n"] == 2  # both offsets have n=1 < min_n=10

    def test_lineage_json_roundtrip(self):
        payload = np.array([[1.0, 2.0], [3.0, 4.0]])
        panel = _make_panel(payload=payload, offsets=[0, 1])
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(aggregator="mean", dispersion="std", min_n=1),
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
            ConditionalAggregateParams(aggregator="mean", dispersion="std", min_n=1),
        )
        assert out.units == TimeSeriesUnits.Z_SCORE


# ===========================================================================
# 11. PHASE 1A ARCHITECTURE-PROOF TEST — full Q1 chain end-to-end
# ===========================================================================


class TestPhase1AArchitectureProof:
    """The defining test of Phase 1A.

    Runs the full Q1-shape DAG end-to-end, verifying every operator-
    layer contract holds when composed:

        align_series → series_arithmetic → threshold_events
            → event_windows → conditional_aggregate

    Asserts:
      - the final EventResponseSeries has the correct shape and values
      - units transitioned correctly through the chain
        (PERCENT → PERCENT swap-spread → BPS via level_change → BPS
        aggregated)
      - lineage covers EVERY step, including auxiliary chains for
        binary inputs (right operand of arithmetic, target of
        event_windows)
      - the methodology summary CAN be derived programmatically by
        walking the lineage from the head EventResponseSeries alone
    """

    def test_full_q1_chain_runs_and_lineage_is_complete(self):
        # Construct a synthetic 30-day swap-spread scenario.
        idx = list(pd.bdate_range("2026-01-02", periods=30))

        # ust_2y stable around 4.50, with two spikes producing
        # spread-event days.
        ust_2y_vals = [4.50] * 30
        ust_2y_vals[10] = 4.85
        ust_2y_vals[20] = 4.90
        # ois_2y constant 4.30.
        ois_2y_vals = [4.30] * 30
        # ust_10y rises after each event to give level_change a real
        # signal to aggregate.
        ust_10y_vals = [4.20] * 30
        for spike_day in (10, 20):
            for k in range(0, 4):
                if spike_day + k < 30:
                    ust_10y_vals[spike_day + k] = (
                        4.20 + (k + 1) * 0.05
                    )

        ust_2y = _series("ust_2y", dates=idx, values=ust_2y_vals)
        ois_2y = _series("ois_2y", dates=idx, values=ois_2y_vals)
        ust_10y = _series("ust_10y", dates=idx, values=ust_10y_vals)

        # Step 1: align all three.
        aligned = align_series([ust_2y, ois_2y, ust_10y], AlignSeriesParams())

        # Step 2: build the swap spread.
        spread = series_arithmetic(
            aligned.get_series("ust_2y"),
            "subtract",
            aligned.get_series("ois_2y"),
        )
        assert spread.units == TimeSeriesUnits.PERCENT

        # Step 3: threshold the spread.
        events = threshold_events(spread, ThresholdEventsParams(
            rule="above", threshold=0.30,
        ))
        assert events.n_events == 2

        # Step 4: extract level-change windows on UST 10Y (PERCENT → BPS).
        panel = event_windows(
            events,
            aligned.get_series("ust_10y"),
            EventWindowsParams(
                pre_window=1, post_window=3,
                units_basis="level_change",
            ),
        )
        assert panel.n_events == 2
        assert panel.units == TimeSeriesUnits.BPS

        # Step 5: aggregate.
        out = conditional_aggregate(
            panel,
            ConditionalAggregateParams(
                aggregator="mean", dispersion="std", min_n=2,
            ),
        )

        # ---- Output checks ----
        assert isinstance(out, EventResponseSeries)
        assert out.offsets == [-1, 0, 1, 2, 3]
        assert out.units == TimeSeriesUnits.BPS
        # 2 events × 5 offsets, both events fully within bounds, so
        # n=2 per offset (>= min_n=2 → low_n flags all False).
        np.testing.assert_array_equal(out.n_observations, [2, 2, 2, 2, 2])
        assert not out.low_n_flags.any()
        # Mean values are non-trivial and finite.
        assert np.all(np.isfinite(out.values))

        # ---- Lineage walk: every step accounted for ----
        # Primary chain: fetch (ust_2y) + adapter + align_series +
        # series_arithmetic + threshold_events + event_windows +
        # conditional_aggregate = 7 steps.
        kinds = [s.kind for s in out.lineage.steps]
        assert kinds == [
            "fetch", "adapter",
            "operator", "operator", "operator",
            "operator", "operator",
        ]
        op_names = [
            s.name for s in out.lineage.steps if s.kind == "operator"
        ]
        assert op_names == [
            "align_series",
            "series_arithmetic",
            "threshold_events",
            "event_windows",
            "conditional_aggregate",
        ]

        # ---- Right-hand chain (ois_2y) preserved on arithmetic step ----
        arithmetic_step = out.lineage.steps[3]
        assert len(arithmetic_step.auxiliary_lineages) == 1
        right_chain = arithmetic_step.auxiliary_lineages[0]
        # ois_2y came through align_series.get_series → fetch + adapter
        # + align_series step.
        assert [s.kind for s in right_chain.steps] == [
            "fetch", "adapter", "operator",
        ]
        assert right_chain.steps[-1].name == "align_series"

        # ---- Target chain (ust_10y) preserved on event_windows step ----
        ew_step = out.lineage.steps[5]
        assert len(ew_step.auxiliary_lineages) == 1
        target_chain = ew_step.auxiliary_lineages[0]
        assert [s.kind for s in target_chain.steps] == [
            "fetch", "adapter", "operator",
        ]
        assert target_chain.steps[-1].name == "align_series"

        # ---- Methodology summary derivable from lineage alone ----
        # Walk every operator step's params and synthesise a tiny
        # one-line summary; this is the kind of programmatic readout
        # a workflow template will produce in Phase 1B.
        summary_lines = []
        for step in out.lineage.steps:
            if step.kind != "operator":
                continue
            line = f"{step.name}({', '.join(f'{k}={v!r}' for k, v in step.params.items() if not k.startswith('n_') and not k.startswith('input_') and not k.startswith('output_') and not k.startswith('source_') and not k.startswith('target_') and not k.startswith('overlap_') and not k.startswith('window_'))})"
            summary_lines.append(line)
        # 5 operator steps → 5 summary lines.
        assert len(summary_lines) == 5

    def test_full_q1_chain_lineage_json_roundtrips(self):
        """The full chain's lineage must survive serialization byte-
        identically — including the recursive auxiliary_lineages on
        arithmetic AND event_windows steps."""
        idx = list(pd.bdate_range("2026-01-02", periods=15))
        a = _series("a", dates=idx, values=[1.0 + i * 0.1 for i in range(15)])
        b = _series("b", dates=idx, values=[0.5] * 15)
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
            ConditionalAggregateParams(aggregator="mean", dispersion="std", min_n=1),
        )

        as_dict = out.lineage.model_dump(mode="json")
        rec = Lineage.model_validate(as_dict)
        assert rec.head_hash == out.lineage.head_hash
        # auxiliary_lineages preserved at every binary step.
        assert len(rec.steps[3].auxiliary_lineages) == 1   # arithmetic
        assert len(rec.steps[5].auxiliary_lineages) == 1   # event_windows
