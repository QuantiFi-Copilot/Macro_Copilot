"""Tests for shared.operators.event_windows.

Covers:

  - schema validation (degenerate window rejection)
  - structural-input checks (events / target type, index identity)
  - offsets vector construction (pre/post/inclusive_event_day)
  - windowing math on raw_value basis
  - level_change basis: subtraction at event day + PERCENT→BPS unit
    transition (verified against an INDEPENDENTLY hand-computed reference)
  - non-PERCENT level_change cases preserve units (BPS-BPS=BPS, etc.)
  - incomplete_window_policy: drop vs pad_nan
  - overlap-pair statistics recorded in lineage
  - per-event metadata carries source-event context + window context
  - lineage propagation (events.lineage as primary, target.lineage as
    auxiliary on the operator step — PR #51 contract)
  - YAML default authority — caller-omitted fields resolve from config
  - finance-blindness on Z_SCORE inputs
  - composition: full Q1-shape DAG ending in event_windows
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd
import pytest

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
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.align_series import align_series, AlignSeriesParams
from shared.operators.event_windows import (
    CONFIG_PATH,
    EventWindowsParams,
    event_windows,
)
from shared.operators.event_windows.operator import EventWindowsError
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


def _events_from(series: Series, *, dates_to_fire: List[pd.Timestamp],
                  source_series_key=None) -> EventSet:
    """Build a synthetic EventSet whose mask fires on the given dates.
    Lineage is the source series + a synthetic OperatorStep.  Useful
    when we want to control exact event dates without going through
    threshold_events first."""
    mask_values = [d in dates_to_fire for d in series.payload.index]
    mask = pd.Series(mask_values, index=series.payload.index)
    op_step = OperatorStep.build(
        name="threshold_events", version="1.0.0",
        params={"synthetic": True, "n_events": int(mask.sum())},
        input_hashes=(series.lineage.head_hash,),
    )
    return EventSet(
        mask=mask,
        event_dates=[d for d in series.payload.index if d in dates_to_fire],
        per_event_metadata=[{"source": "synthetic"}] * int(mask.sum()),
        source_series_key=source_series_key or series.series_key,
        lineage=series.lineage.append(op_step),
    )


# ===========================================================================
# 1. Schema validation
# ===========================================================================


class TestSchemaValidation:
    def test_degenerate_window_rejected(self):
        """pre=0, post=0, inclusive=False produces a zero-width window
        — almost certainly a caller bug.  Schema must refuse."""
        with pytest.raises(ValueError, match="zero-width"):
            EventWindowsParams(
                pre_window=0, post_window=0, inclusive_event_day=False,
            )

    def test_negative_pre_window_rejected(self):
        with pytest.raises(ValueError):
            EventWindowsParams(pre_window=-1)

    def test_negative_post_window_rejected(self):
        with pytest.raises(ValueError):
            EventWindowsParams(post_window=-1)


# ===========================================================================
# 2. Structural-input validation
# ===========================================================================


class TestStructuralInputChecks:
    def test_non_eventset_input_raises(self):
        s = _series("x", dates=["2026-01-02"], values=[1.0])
        with pytest.raises(EventWindowsError, match="must be an EventSet"):
            event_windows("not an EventSet", s,  # type: ignore[arg-type]
                          EventWindowsParams(post_window=1))

    def test_non_series_target_raises(self):
        s = _series("x", dates=["2026-01-02"], values=[1.0])
        es = _events_from(s, dates_to_fire=[s.payload.index[0]])
        with pytest.raises(EventWindowsError, match="target must be a Series"):
            event_windows(es, "not a Series",  # type: ignore[arg-type]
                          EventWindowsParams(post_window=1))

    def test_index_mismatch_raises(self):
        s_a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0])
        s_b = _series("b", dates=["2026-01-02", "2026-01-06"], values=[1.0, 2.0])
        es = _events_from(s_a, dates_to_fire=[s_a.payload.index[0]])
        with pytest.raises(EventWindowsError, match="must be identical"):
            event_windows(es, s_b, EventWindowsParams(post_window=1))


# ===========================================================================
# 3. Offsets vector
# ===========================================================================


class TestOffsetsVector:
    def test_inclusive_event_day_default(self):
        s = _series("x", dates=list(pd.bdate_range("2026-01-02", periods=10)),
                    values=list(range(10)))
        es = _events_from(s, dates_to_fire=[s.payload.index[5]])
        panel = event_windows(es, s, EventWindowsParams(pre_window=2, post_window=3))
        assert panel.offsets == [-2, -1, 0, 1, 2, 3]

    def test_exclusive_event_day(self):
        s = _series("x", dates=list(pd.bdate_range("2026-01-02", periods=10)),
                    values=list(range(10)))
        es = _events_from(s, dates_to_fire=[s.payload.index[5]])
        panel = event_windows(
            es, s,
            EventWindowsParams(
                pre_window=2, post_window=3, inclusive_event_day=False,
            ),
        )
        assert panel.offsets == [-2, -1, 1, 2, 3]

    def test_post_only_window(self):
        s = _series("x", dates=list(pd.bdate_range("2026-01-02", periods=10)),
                    values=list(range(10)))
        es = _events_from(s, dates_to_fire=[s.payload.index[3]])
        panel = event_windows(es, s, EventWindowsParams(post_window=5))
        assert panel.offsets == [0, 1, 2, 3, 4, 5]


# ===========================================================================
# 4. Windowing math — raw_value basis
# ===========================================================================


class TestRawValueWindowing:
    def test_extracts_correct_values(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        vals = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
        s = _series("x", dates=idx, values=vals)
        # Event at index 5 (value=15.0).  Window [-2, +2] inclusive.
        es = _events_from(s, dates_to_fire=[idx[5]])
        panel = event_windows(
            es, s,
            EventWindowsParams(pre_window=2, post_window=2, units_basis="raw"),
        )
        # Expected window: [13, 14, 15, 16, 17]
        np.testing.assert_array_equal(
            panel.payload[0],
            np.array([13.0, 14.0, 15.0, 16.0, 17.0]),
        )

    def test_raw_basis_preserves_units(self):
        s = _series(
            "x",
            dates=list(pd.bdate_range("2026-01-02", periods=10)),
            values=list(range(10)),
            units=TimeSeriesUnits.PERCENT,
        )
        es = _events_from(s, dates_to_fire=[s.payload.index[5]])
        panel = event_windows(
            es, s,
            EventWindowsParams(pre_window=1, post_window=1, units_basis="raw"),
        )
        assert panel.units == TimeSeriesUnits.PERCENT


# ===========================================================================
# 5. Level-change basis + PERCENT → BPS transition
# ===========================================================================


class TestLevelChangeUnitTransition:
    """Build plan v5 contract: units_basis='level_change' on a PERCENT
    target produces a BPS-units WindowedPanel.  Verified against an
    independent hand-computed reference."""

    def test_percent_input_emits_bps_panel(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        vals = [4.10, 4.12, 4.15, 4.18, 4.20, 4.50, 4.45, 4.40, 4.35, 4.30]
        s = _series("ust_10y", dates=idx, values=vals,
                    units=TimeSeriesUnits.PERCENT)
        # Event at index 5 (value=4.50).
        es = _events_from(s, dates_to_fire=[idx[5]])
        panel = event_windows(
            es, s,
            EventWindowsParams(
                pre_window=2, post_window=2, units_basis="level_change",
            ),
        )
        # Independent hand-computation:
        # event_day = 4.50.  Window slice = [4.18, 4.20, 4.50, 4.45, 4.40].
        # level_change = window - 4.50 = [-0.32, -0.30, 0.0, -0.05, -0.10]
        # × 100 = [-32, -30, 0, -5, -10] (in BPS).
        expected = np.array([-32.0, -30.0, 0.0, -5.0, -10.0])
        np.testing.assert_allclose(panel.payload[0], expected, rtol=1e-9, atol=1e-9)
        # Output unit transition recorded.
        assert panel.units == TimeSeriesUnits.BPS

    def test_lineage_records_unit_transition(self):
        s = _series(
            "x",
            dates=list(pd.bdate_range("2026-01-02", periods=10)),
            values=list(range(10)),
            units=TimeSeriesUnits.PERCENT,
        )
        es = _events_from(s, dates_to_fire=[s.payload.index[5]])
        panel = event_windows(
            es, s,
            EventWindowsParams(post_window=2, units_basis="level_change"),
        )
        params = panel.lineage.steps[-1].params
        assert params["input_units"] == "percent"
        assert params["output_units"] == "bps"
        assert params["units_basis"] == "level_change"

    def test_bps_input_level_change_stays_bps(self):
        """Non-PERCENT inputs are unit-preserving under level_change.
        BPS - BPS = BPS (no ×100 transition)."""
        s = _series(
            "x",
            dates=list(pd.bdate_range("2026-01-02", periods=5)),
            values=[10.0, 20.0, 30.0, 40.0, 50.0],
            units=TimeSeriesUnits.BPS,
        )
        es = _events_from(s, dates_to_fire=[s.payload.index[2]])
        panel = event_windows(
            es, s,
            EventWindowsParams(
                pre_window=1, post_window=1, units_basis="level_change",
            ),
        )
        # event_day = 30.  Window slice = [20, 30, 40].  level_change
        # = [-10, 0, 10] in BPS (no ×100 because input was BPS).
        np.testing.assert_array_equal(panel.payload[0], [-10.0, 0.0, 10.0])
        assert panel.units == TimeSeriesUnits.BPS


# ===========================================================================
# 6. Edge handling — incomplete_window_policy
# ===========================================================================


class TestIncompleteWindowPolicy:
    def test_drop_skips_edge_events(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=list(range(10)))
        # Events at indices 0 (cannot satisfy pre=2) and 5 (full window).
        es = _events_from(s, dates_to_fire=[idx[0], idx[5]])
        panel = event_windows(
            es, s,
            EventWindowsParams(
                pre_window=2, post_window=2,
                incomplete_window_policy="drop",
                units_basis="raw",
            ),
        )
        # Only the index-5 event survives.
        assert panel.n_events == 1
        assert panel.event_dates[0] == idx[5]
        # Lineage records the dropped count.
        params = panel.lineage.steps[-1].params
        assert params["n_events_in"] == 2
        assert params["n_events_kept"] == 1
        assert params["n_events_dropped_for_edge"] == 1

    def test_pad_nan_keeps_partial_windows(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=list(range(10)))
        # Event at index 0 with pre=2 has 2 NaN cells (offsets -2, -1)
        # and 3 real cells (offsets 0, 1, 2).
        es = _events_from(s, dates_to_fire=[idx[0]])
        panel = event_windows(
            es, s,
            EventWindowsParams(
                pre_window=2, post_window=2,
                incomplete_window_policy="pad_nan",
                units_basis="raw",
            ),
        )
        assert panel.n_events == 1
        row = panel.payload[0]
        # offsets [-2, -1, 0, 1, 2] → [NaN, NaN, 0, 1, 2]
        assert pd.isna(row[0]) and pd.isna(row[1])
        np.testing.assert_array_equal(row[2:], [0.0, 1.0, 2.0])

    def test_drop_with_no_events_left_yields_empty_panel(self):
        idx = list(pd.bdate_range("2026-01-02", periods=5))
        s = _series("x", dates=idx, values=list(range(5)))
        # Event at first index with pre=10 cannot fit; drop produces 0 events.
        es = _events_from(s, dates_to_fire=[idx[0]])
        panel = event_windows(
            es, s,
            EventWindowsParams(
                pre_window=10, post_window=2,
                incomplete_window_policy="drop",
            ),
        )
        assert panel.n_events == 0
        assert panel.payload.shape == (0, 13)  # 0 events × (10+1+2) cells


# ===========================================================================
# 7. Overlap statistics
# ===========================================================================


class TestOverlapStats:
    def test_no_overlap_when_events_separated(self):
        idx = list(pd.bdate_range("2026-01-02", periods=20))
        s = _series("x", dates=idx, values=list(range(20)))
        # Events at indices 5 and 15 — windows pre=1 post=1 don't overlap.
        es = _events_from(s, dates_to_fire=[idx[5], idx[15]])
        panel = event_windows(
            es, s,
            EventWindowsParams(pre_window=1, post_window=1),
        )
        assert panel.lineage.steps[-1].params["overlap_pairs_in_kept_events"] == 0

    def test_overlap_counted_when_events_close(self):
        idx = list(pd.bdate_range("2026-01-02", periods=20))
        s = _series("x", dates=idx, values=list(range(20)))
        # Events at indices 5 and 6 — pre=1 post=1 windows overlap.
        es = _events_from(s, dates_to_fire=[idx[5], idx[6]])
        panel = event_windows(
            es, s,
            EventWindowsParams(pre_window=1, post_window=1),
        )
        assert panel.lineage.steps[-1].params["overlap_pairs_in_kept_events"] == 1


# ===========================================================================
# 8. Per-event metadata
# ===========================================================================


class TestPerEventMetadata:
    def test_carries_source_metadata_and_adds_window_context(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=list(range(10)))
        es = _events_from(s, dates_to_fire=[idx[3]])
        panel = event_windows(
            es, s,
            EventWindowsParams(pre_window=1, post_window=2, units_basis="raw"),
        )
        meta = panel.per_event_metadata[0]
        # Source metadata threaded through.
        assert meta["source"] == "synthetic"
        # Plus window-specific context.
        assert meta["event_relative_offsets"] == [-1, 0, 1, 2]
        assert meta["event_day_value"] == 3.0


# ===========================================================================
# 9. Lineage propagation (PR #51 binary-provenance contract)
# ===========================================================================


class TestLineagePropagation:
    def test_appends_operator_step(self):
        s = _series("x", dates=list(pd.bdate_range("2026-01-02", periods=10)),
                    values=list(range(10)))
        es = _events_from(s, dates_to_fire=[s.payload.index[5]])
        panel = event_windows(es, s, EventWindowsParams(post_window=1))
        # events.lineage = [fetch, adapter, operator (events)] →
        # this op appends a fourth step.
        kinds = [step.kind for step in panel.lineage.steps]
        assert kinds == ["fetch", "adapter", "operator", "operator"]
        assert panel.lineage.steps[-1].name == "event_windows"

    def test_target_lineage_in_auxiliary_lineages(self):
        """Target's lineage chain must be persisted on the operator
        step so a methodology summary can walk back into it from the
        head WindowedPanel alone (PR #51 contract)."""
        s = _series("x", dates=list(pd.bdate_range("2026-01-02", periods=10)),
                    values=list(range(10)))
        es = _events_from(s, dates_to_fire=[s.payload.index[5]])
        # Use a different series as target to make the test
        # meaningful (target ≠ events' source).
        target = _series(
            "y", dates=list(pd.bdate_range("2026-01-02", periods=10)),
            values=list(range(100, 110)),
        )
        panel = event_windows(es, target, EventWindowsParams(post_window=1))

        op_step = panel.lineage.steps[-1]
        # Both upstream heads in input_hashes.
        assert set(op_step.input_hashes) == {
            es.lineage.head_hash, target.lineage.head_hash,
        }
        # Target's full lineage in auxiliary_lineages.
        assert len(op_step.auxiliary_lineages) == 1
        assert op_step.auxiliary_lineages[0].head_hash == target.lineage.head_hash

    def test_lineage_json_roundtrip(self):
        s = _series("x", dates=list(pd.bdate_range("2026-01-02", periods=10)),
                    values=list(range(10)))
        es = _events_from(s, dates_to_fire=[s.payload.index[5]])
        panel = event_windows(es, s, EventWindowsParams(post_window=2))
        as_dict = panel.lineage.model_dump(mode="json")
        rec = Lineage.model_validate(as_dict)
        assert rec.head_hash == panel.lineage.head_hash
        assert len(rec.steps) == len(panel.lineage.steps)


# ===========================================================================
# 10. YAML default authority (matches the threshold_events pattern)
# ===========================================================================


class TestYamlDefaultAuthority:
    def test_caller_omits_iwp_and_basis_resolves_from_yaml(self):
        s = _series("x", dates=list(pd.bdate_range("2026-01-02", periods=10)),
                    values=list(range(10)))
        es = _events_from(s, dates_to_fire=[s.payload.index[5]])
        # Caller omits both iwp and units_basis — schema leaves them
        # None, operator must resolve from YAML.
        params = EventWindowsParams(post_window=2)
        assert params.incomplete_window_policy is None
        assert params.units_basis is None
        panel = event_windows(es, s, params)
        step_params = panel.lineage.steps[-1].params
        # YAML defaults: iwp=drop, units_basis=raw.
        assert step_params["incomplete_window_policy"] == "drop"
        assert step_params["units_basis"] == "raw"

    def test_yaml_default_change_propagates_at_runtime(self, tmp_path):
        """Regression guard against schema-vs-YAML drift — tweak the
        YAML and verify the operator follows it."""
        import yaml as _yaml
        from shared.config.operator_config import (
            clear_operator_config_cache,
            load_operator_config,
        )
        bundled = _yaml.safe_load(CONFIG_PATH.read_text())
        bundled["defaults"]["units_basis"]["value"] = "level_change"
        tweaked_path = tmp_path / "config.yaml"
        tweaked_path.write_text(_yaml.safe_dump(bundled))

        clear_operator_config_cache()
        tweaked_cfg = load_operator_config(tweaked_path)
        assert tweaked_cfg.default_value("units_basis") == "level_change"

        s = _series(
            "x", dates=list(pd.bdate_range("2026-01-02", periods=5)),
            values=[1.0, 2.0, 3.0, 4.0, 5.0],
            units=TimeSeriesUnits.PERCENT,
        )
        es = _events_from(s, dates_to_fire=[s.payload.index[2]])
        # Caller omits units_basis — must pick up tweaked YAML's
        # level_change → output units = BPS.
        panel = event_windows(
            es, s, EventWindowsParams(pre_window=1, post_window=1),
            config=tweaked_cfg,
        )
        assert panel.units == TimeSeriesUnits.BPS
        assert panel.lineage.steps[-1].params["units_basis"] == "level_change"


# ===========================================================================
# 11. Bundled config + finance-blindness
# ===========================================================================


class TestBundledConfig:
    def test_config_path_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads_with_correct_identity(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "event_windows"
        assert cfg.operator.method_family == "windowing"

    def test_config_defaults_are_safe(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.default_value("incomplete_window_policy") == "drop"
        assert cfg.default_value("units_basis") == "raw"
        assert cfg.default_value("require_matching_frequency") is True


class TestFinanceBlindness:
    def test_runs_unchanged_on_z_score_input(self):
        s = _series(
            "ny_temp",
            dates=list(pd.bdate_range("2026-01-02", periods=10)),
            values=list(range(10)),
            units=TimeSeriesUnits.Z_SCORE,
        )
        es = _events_from(s, dates_to_fire=[s.payload.index[5]])
        panel = event_windows(
            es, s,
            EventWindowsParams(
                pre_window=1, post_window=1, units_basis="level_change",
            ),
        )
        # level_change on Z_SCORE: no PERCENT→BPS transition.
        assert panel.units == TimeSeriesUnits.Z_SCORE


# ===========================================================================
# 12. Composition — full Q1-shape DAG ends in event_windows
# ===========================================================================


class TestComposition:
    def test_align_arithmetic_threshold_eventwindows_endtoend(self):
        """The full Q1 DAG, sans the final aggregator: build a swap-
        spread, threshold it, then extract event windows of the
        target (10Y UST yield).  Output WindowedPanel must carry
        every upstream step's lineage end-to-end, including the
        target's chain via auxiliary_lineages on this op step AND
        the right-hand chain via auxiliary_lineages on the
        arithmetic step (PR #51 contract)."""
        idx = list(pd.bdate_range("2026-01-02", periods=20))
        # Pair: 2Y UST + 2Y OIS, swap-spread crosses 0.30 on day 12.
        ust_2y = _series(
            "ust_2y", dates=idx,
            values=[4.50] * 12 + [4.85, 4.55, 4.55, 4.55, 4.55, 4.55, 4.55, 4.55],
        )
        ois_2y = _series("ois_2y", dates=idx, values=[4.30] * 20)
        ust_10y = _series(
            "ust_10y", dates=idx,
            values=[4.20] * 12 + [4.30, 4.40, 4.35, 4.30, 4.28, 4.26, 4.24, 4.22],
        )

        aligned = align_series([ust_2y, ois_2y, ust_10y], AlignSeriesParams())
        spread = series_arithmetic(
            aligned.get_series("ust_2y"),
            "subtract",
            aligned.get_series("ois_2y"),
        )
        events = threshold_events(spread, ThresholdEventsParams(
            rule="above", threshold=0.30,
        ))
        assert events.n_events == 1

        panel = event_windows(
            events, aligned.get_series("ust_10y"),
            EventWindowsParams(
                pre_window=0, post_window=5,
                units_basis="level_change",
            ),
        )
        assert panel.n_events == 1
        assert panel.units == TimeSeriesUnits.BPS

        # Lineage end-to-end:
        # events.lineage = fetch + adapter + align + arithmetic +
        # threshold; this op appends a 6th step.
        kinds = [step.kind for step in panel.lineage.steps]
        assert kinds == [
            "fetch", "adapter",
            "operator", "operator", "operator", "operator",
        ]
        op_names = [
            step.name for step in panel.lineage.steps if step.kind == "operator"
        ]
        assert op_names == [
            "align_series", "series_arithmetic", "threshold_events",
            "event_windows",
        ]

        # The final operator's auxiliary_lineages contains the
        # target's chain (ust_10y).
        ew_step = panel.lineage.steps[-1]
        assert len(ew_step.auxiliary_lineages) == 1
        target_chain = ew_step.auxiliary_lineages[0]
        target_chain_kinds = [s.kind for s in target_chain.steps]
        # ust_10y came through align_series.get_series, so its chain
        # is fetch + adapter + align_series.
        assert target_chain_kinds == ["fetch", "adapter", "operator"]
        assert target_chain.steps[-1].name == "align_series"

        # The arithmetic step (PR #51 contract) STILL carries the
        # right-hand (ois_2y) chain in its auxiliary_lineages.
        arithmetic_step = panel.lineage.steps[3]
        assert len(arithmetic_step.auxiliary_lineages) == 1
        right_chain = arithmetic_step.auxiliary_lineages[0]
        right_chain_kinds = [s.kind for s in right_chain.steps]
        assert right_chain_kinds == ["fetch", "adapter", "operator"]

    def test_composition_lineage_json_roundtrips(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        a = _series("a", dates=idx, values=[1.0 + i * 0.1 for i in range(10)])
        b = _series("b", dates=idx, values=[0.5] * 10)
        aligned = align_series([a, b])
        diff = series_arithmetic(
            aligned.get_series("a"), "subtract", aligned.get_series("b"),
        )
        events = threshold_events(diff, ThresholdEventsParams(
            rule="above", threshold=0.5,
        ))
        panel = event_windows(
            events, aligned.get_series("a"),
            EventWindowsParams(post_window=2, units_basis="raw"),
        )
        as_dict = panel.lineage.model_dump(mode="json")
        rec = Lineage.model_validate(as_dict)
        assert rec.head_hash == panel.lineage.head_hash
        # Auxiliary lineages survive through the chain.
        rec_arithmetic = rec.steps[3]
        rec_ew = rec.steps[-1]
        assert len(rec_arithmetic.auxiliary_lineages) == 1
        assert len(rec_ew.auxiliary_lineages) == 1


# ===========================================================================
# 13. Frequency-tag enforcement (Codex P1 follow-up — was a no-op before)
# ===========================================================================


class TestFrequencyEnforcement:
    """Build plan v5 + Codex P1: require_matching_frequency must be a
    real check.  EventSet now carries a ``frequency`` field
    propagated from the source Series in ``threshold_events``;
    event_windows enforces it against the target's ``frequency``."""

    def _events_with_frequency(
        self, series: Series, *, frequency: object,
    ) -> EventSet:
        """Build an EventSet whose ``frequency`` we control directly,
        so we can test the enforcement without depending on the
        threshold_events propagation chain."""
        # Use the helper, then rebuild with the requested frequency.
        es = _events_from(series, dates_to_fire=[series.payload.index[5]])
        return EventSet(
            mask=es.mask,
            event_dates=es.event_dates,
            per_event_metadata=es.per_event_metadata,
            source_series_key=es.source_series_key,
            frequency=frequency,
            lineage=es.lineage,
        )

    def test_matching_frequencies_pass(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=list(range(10)), frequency="B")
        events = self._events_with_frequency(s, frequency="B")
        target = _series("y", dates=idx, values=list(range(100, 110)),
                         frequency="B")
        # No exception — both tagged "B".
        panel = event_windows(events, target,
                              EventWindowsParams(post_window=2))
        assert panel.n_events == 1

    def test_both_none_frequencies_pass(self):
        """``None`` matches ``None`` under strict mode."""
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=list(range(10)))  # frequency=None
        events = self._events_with_frequency(s, frequency=None)
        target = _series("y", dates=idx, values=list(range(100, 110)))
        panel = event_windows(events, target,
                              EventWindowsParams(post_window=2))
        assert panel.n_events == 1

    def test_mismatched_frequencies_strict_raises(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=list(range(10)), frequency="B")
        events = self._events_with_frequency(s, frequency="B")
        target = _series("y", dates=idx, values=list(range(100, 110)),
                         frequency="W")
        with pytest.raises(EventWindowsError, match="incompatible frequencies"):
            event_windows(events, target, EventWindowsParams(post_window=2))

    def test_partial_tagging_strict_raises(self):
        """Events tagged but target not (or vice versa) — partial
        metadata is the exact silent-mismatch failure mode the strict
        check exists to surface."""
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=list(range(10)), frequency="B")
        events = self._events_with_frequency(s, frequency="B")
        # Target has no frequency tag.
        target = _series("y", dates=idx, values=list(range(100, 110)))
        with pytest.raises(EventWindowsError, match="incompatible frequencies"):
            event_windows(events, target, EventWindowsParams(post_window=2))

    def test_lenient_mode_accepts_mismatch_and_records_choice(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=list(range(10)), frequency="B")
        events = self._events_with_frequency(s, frequency="B")
        target = _series("y", dates=idx, values=list(range(100, 110)),
                         frequency="W")
        panel = event_windows(
            events, target,
            EventWindowsParams(
                post_window=2, require_matching_frequency=False,
            ),
        )
        # No exception; choice recorded in lineage.
        assert panel.lineage.steps[-1].params["require_matching_frequency"] is False

    def test_threshold_events_propagates_frequency_to_eventset(self):
        """End-to-end check: threshold_events on a frequency-tagged
        Series must produce an EventSet that carries that frequency
        forward, so event_windows' check has something to compare
        against without the caller hand-building the EventSet."""
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=[1.0, 2.0, 3.0, 4.0, 5.0,
                                             6.0, 7.0, 8.0, 9.0, 10.0],
                    frequency="B")
        es = threshold_events(s, ThresholdEventsParams(rule="above", threshold=5.0))
        assert es.frequency == "B"


# ===========================================================================
# 14. NaN event-day drop accounting (Codex P2 follow-up)
# ===========================================================================


class TestNanEventDayHandling:
    """Codex P2 follow-up: NaN event-day under level_change must be
    counted in its own counter, NOT folded into n_events_dropped_for_edge.
    The lineage metadata must be honest about WHY events were dropped."""

    def _build_target_with_nan_at_event_day(self):
        """Target whose event-day position has a NaN value, and a
        non-edge surrounding window so edge handling is not the
        reason for any drop."""
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        # NaN at index 5 (the event day).  All other cells finite.
        vals = [1.0, 2.0, 3.0, 4.0, 5.0, np.nan, 7.0, 8.0, 9.0, 10.0]
        # Construct a Series artifact — the wrapper validator allows
        # NaN values (drops only NaT index), so this passes.
        fetch = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"series_key": "y"},
        )
        adapter = AdapterStep.build(
            name="raw_dataframe_to_artifact_series", version="1.0.0",
            params={"series_key": "y", "units": "percent"},
            input_hashes=(fetch.hash,),
        )
        payload = pd.Series(vals, index=pd.DatetimeIndex(idx), dtype=float)
        target = Series(
            series_key="y", payload=payload,
            units=TimeSeriesUnits.PERCENT, frequency=None,
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            lineage=Lineage.from_steps([fetch, adapter]),
        )
        # Events: a single fire at index 5 (the NaN cell).
        s_for_events = _series("x", dates=idx, values=list(range(10)))
        es = _events_from(s_for_events, dates_to_fire=[idx[5]])
        return target, es, idx

    def test_level_change_drop_when_event_day_is_nan(self):
        """Under units_basis='level_change' + drop, an event whose
        event-day target value is NaN must be dropped — and the
        drop must be counted in n_events_dropped_for_nan_event_day,
        NOT n_events_dropped_for_edge."""
        target, events, _ = self._build_target_with_nan_at_event_day()
        panel = event_windows(
            events, target,
            EventWindowsParams(
                pre_window=1, post_window=1,
                units_basis="level_change",
                incomplete_window_policy="drop",
            ),
        )
        # Event was dropped (NaN event-day, can't compute level_change).
        assert panel.n_events == 0
        params = panel.lineage.steps[-1].params
        # Edge counter is NOT incremented (the window had no edge issue).
        assert params["n_events_dropped_for_edge"] == 0
        # NaN-event-day counter IS incremented.
        assert params["n_events_dropped_for_nan_event_day"] == 1
        # And totals add up: in (1) = kept (0) + dropped_edge (0) +
        # dropped_nan (1).
        assert params["n_events_in"] == 1
        assert params["n_events_kept"] == 0

    def test_level_change_pad_nan_when_event_day_is_nan(self):
        """Under pad_nan, the event is kept but the entire row is NaN
        because the level-change subtraction is undefined."""
        target, events, _ = self._build_target_with_nan_at_event_day()
        panel = event_windows(
            events, target,
            EventWindowsParams(
                pre_window=1, post_window=1,
                units_basis="level_change",
                incomplete_window_policy="pad_nan",
            ),
        )
        assert panel.n_events == 1
        # Whole row should be NaN (cannot subtract from NaN).
        assert np.all(np.isnan(panel.payload[0]))
        # Neither drop counter is incremented in pad_nan path.
        params = panel.lineage.steps[-1].params
        assert params["n_events_dropped_for_edge"] == 0
        assert params["n_events_dropped_for_nan_event_day"] == 0

    def test_edge_drop_does_not_increment_nan_counter(self):
        """Regression guard: verify the EDGE drop case still increments
        ONLY the edge counter — the split should be clean both ways."""
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        s = _series("x", dates=idx, values=list(range(10)))
        # Events at index 0 with pre=2 — pure edge case, no NaN involved.
        es = _events_from(s, dates_to_fire=[idx[0]])
        panel = event_windows(
            es, s,
            EventWindowsParams(
                pre_window=2, post_window=2,
                incomplete_window_policy="drop",
            ),
        )
        params = panel.lineage.steps[-1].params
        assert params["n_events_dropped_for_edge"] == 1
        assert params["n_events_dropped_for_nan_event_day"] == 0
