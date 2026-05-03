"""Tests for shared.artifacts — typed wrappers + lineage + adapter.

Phase 1A foundation tests.  Cover:

  - Series / SeriesSet / EventSet / WindowedPanel / Panel construction
    and structural-metadata validation
  - Lineage hash determinism + JSON round-trip
  - raw_dataframe_to_artifact_series adapter behaviour (long & indexed
    inputs, missingness policies, lineage shape)
"""

from __future__ import annotations

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
    Panel,
    RawNoCleaning,
    Series,
    SeriesSet,
    TimeSeriesUnits,
    WindowedPanel,
)
from shared.artifacts.adapters import raw_dataframe_to_artifact_series


# ===========================================================================
# Helpers
# ===========================================================================


def _trivial_lineage(series_key: str = "x") -> Lineage:
    fetch = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y", "field_name": "YLD_YTM_MID"},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series",
        version="1.0.0",
        params={"series_key": series_key, "units": "percent"},
        input_hashes=(fetch.hash,),
    )
    return Lineage.from_steps([fetch, adapter])


def _series(series_key: str, *, dates, values, units=TimeSeriesUnits.PERCENT) -> Series:
    payload = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency=None,
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=_trivial_lineage(series_key),
    )


# ===========================================================================
# Series
# ===========================================================================


class TestSeries:
    def test_basic_construction(self):
        s = _series("ust_10y", dates=["2026-01-02", "2026-01-05"], values=[4.10, 4.15])
        assert s.series_key == "ust_10y"
        assert s.units == TimeSeriesUnits.PERCENT
        assert len(s) == 2

    def test_rejects_non_datetime_index(self):
        bad = pd.Series([1.0, 2.0], index=[0, 1], dtype=float)
        with pytest.raises(ValueError, match="DatetimeIndex"):
            Series(
                series_key="x",
                payload=bad,
                units=TimeSeriesUnits.PERCENT,
                frequency=None,
                missingness_policy=RawNoCleaning(),
                lineage=_trivial_lineage(),
            )

    def test_rejects_unsorted_index(self):
        idx = pd.DatetimeIndex(["2026-01-05", "2026-01-02"])
        bad = pd.Series([1.0, 2.0], index=idx, dtype=float)
        with pytest.raises(ValueError, match="sorted ascending"):
            Series(
                series_key="x",
                payload=bad,
                units=TimeSeriesUnits.PERCENT,
                frequency=None,
                missingness_policy=RawNoCleaning(),
                lineage=_trivial_lineage(),
            )

    def test_rejects_duplicate_index(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-02"])
        bad = pd.Series([1.0, 2.0], index=idx, dtype=float)
        with pytest.raises(ValueError, match="duplicates"):
            Series(
                series_key="x",
                payload=bad,
                units=TimeSeriesUnits.PERCENT,
                frequency=None,
                missingness_policy=RawNoCleaning(),
                lineage=_trivial_lineage(),
            )

    def test_rejects_non_numeric_dtype(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        bad = pd.Series(["a", "b"], index=idx)
        with pytest.raises(ValueError, match="numeric"):
            Series(
                series_key="x",
                payload=bad,
                units=TimeSeriesUnits.PERCENT,
                frequency=None,
                missingness_policy=RawNoCleaning(),
                lineage=_trivial_lineage(),
            )


# ===========================================================================
# SeriesSet (keyed retrieval contract — build plan v5 / R2)
# ===========================================================================


class TestSeriesSet:
    def _make_set(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        align_step = OperatorStep.build(
            name="align_series",
            version="1.0.0",
            params={"join_policy": "inner", "fill_policy": "raw", "fill_limit": None},
            input_hashes=("a" * 64, "b" * 64),
        )
        return SeriesSet(
            series_by_key={
                "x": pd.Series([1.0, 2.0], index=idx, dtype=float),
                "y": pd.Series([10.0, 20.0], index=idx, dtype=float),
            },
            units_by_key={"x": TimeSeriesUnits.PERCENT, "y": TimeSeriesUnits.PERCENT},
            missingness_by_key={
                "x": CleanSingleSeriesV1(ffill_limit=5),
                "y": CleanSingleSeriesV1(ffill_limit=5),
            },
            upstream_lineage_by_key={
                "x": _trivial_lineage("x"),
                "y": _trivial_lineage("y"),
            },
            common_index=idx,
            frequency=None,
            lineage=Lineage.from_steps([align_step]),
        )

    def test_keys_returns_sorted(self):
        s = self._make_set()
        assert s.keys() == ["x", "y"]

    def test_get_series_returns_correct_payload(self):
        ss = self._make_set()
        x = ss.get_series("x")
        assert isinstance(x, Series)
        assert x.series_key == "x"
        assert list(x.payload.values) == [1.0, 2.0]

    def test_get_series_appends_alignment_step_to_lineage(self):
        """R2: retrieved Series carries the alignment step appended to
        its upstream lineage."""
        ss = self._make_set()
        x = ss.get_series("x")
        upstream_steps = _trivial_lineage("x").steps
        assert len(x.lineage.steps) == len(upstream_steps) + 1
        # The appended step must be the alignment step.
        assert x.lineage.steps[-1].name == "align_series"
        # And the upstream prefix must be byte-identical to the input
        # series's lineage.
        for u, c in zip(upstream_steps, x.lineage.steps):
            assert u.hash == c.hash

    def test_unknown_key_raises_keyerror(self):
        ss = self._make_set()
        with pytest.raises(KeyError, match="zzz"):
            ss.get_series("zzz")

    def test_misaligned_member_index_rejected(self):
        idx_a = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        idx_b = pd.DatetimeIndex(["2026-01-02", "2026-01-06"])  # mismatch!
        align_step = OperatorStep.build(
            name="align_series", version="1.0.0",
            params={}, input_hashes=(),
        )
        with pytest.raises(ValueError, match="alignment contract"):
            SeriesSet(
                series_by_key={
                    "x": pd.Series([1.0, 2.0], index=idx_a, dtype=float),
                    "y": pd.Series([1.0, 2.0], index=idx_b, dtype=float),
                },
                units_by_key={"x": TimeSeriesUnits.PERCENT, "y": TimeSeriesUnits.PERCENT},
                missingness_by_key={
                    "x": RawNoCleaning(),
                    "y": RawNoCleaning(),
                },
                upstream_lineage_by_key={
                    "x": _trivial_lineage("x"),
                    "y": _trivial_lineage("y"),
                },
                common_index=idx_a,
                frequency=None,
                lineage=Lineage.from_steps([align_step]),
            )


# ===========================================================================
# Lineage (content-addressed hashes, JSON round-trip)
# ===========================================================================


class TestLineage:
    def test_hash_is_deterministic(self):
        a = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y"},
        )
        b = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y"},
        )
        assert a.hash == b.hash

    def test_hash_invariant_to_param_dict_order(self):
        a = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y"},
        )
        b = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"tenor": "10Y", "curve_family": "UST"},
        )
        assert a.hash == b.hash

    def test_hash_invariant_to_input_hash_order(self):
        # Operators that don't depend on input order should produce the
        # same hash regardless of how inputs were arranged.  align_series
        # is one of those (sort happens inside the operator).
        op_a = OperatorStep.build(
            name="align_series", version="1.0.0", params={"join": "inner"},
            input_hashes=("a" * 64, "b" * 64),
        )
        op_b = OperatorStep.build(
            name="align_series", version="1.0.0", params={"join": "inner"},
            input_hashes=("b" * 64, "a" * 64),
        )
        assert op_a.hash == op_b.hash

    def test_hash_changes_on_param_change(self):
        a = FetchStep.build(name="fetch_single_tenor", version="1.0.0",
                            params={"tenor": "2Y"})
        b = FetchStep.build(name="fetch_single_tenor", version="1.0.0",
                            params={"tenor": "10Y"})
        assert a.hash != b.hash

    def test_lineage_json_roundtrip(self):
        ln = _trivial_lineage("ust_10y")
        as_dict = ln.model_dump(mode="json")
        recovered = Lineage.model_validate(as_dict)
        assert recovered.head_hash == ln.head_hash
        assert len(recovered.steps) == len(ln.steps)
        for orig, rec in zip(ln.steps, recovered.steps):
            assert orig.hash == rec.hash
            assert orig.name == rec.name

    def test_append_returns_new_object(self):
        ln = _trivial_lineage("x")
        op_step = OperatorStep.build(
            name="align_series", version="1.0.0",
            params={"join_policy": "inner"},
            input_hashes=(ln.head_hash,),
        )
        ln2 = ln.append(op_step)
        assert ln.head_hash != ln2.head_hash
        assert len(ln2.steps) == len(ln.steps) + 1
        # Original is untouched (frozen).
        assert len(ln.steps) == 2


# ===========================================================================
# raw_dataframe_to_artifact_series
# ===========================================================================


class TestRawDataframeAdapter:
    def test_long_format_input(self):
        df = pd.DataFrame({
            "trade_date": ["2026-01-02", "2026-01-05", "2026-01-06"],
            "field_value": [4.10, 4.15, 4.18],
        })
        s = raw_dataframe_to_artifact_series(
            df,
            series_key="ust_10y",
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={"curve_family": "UST", "tenor": "10Y"},
            missingness_policy=RawNoCleaning(),
        )
        assert s.series_key == "ust_10y"
        assert s.units == TimeSeriesUnits.PERCENT
        assert len(s) == 3
        # Lineage = [FetchStep, AdapterStep]
        assert len(s.lineage.steps) == 2
        assert s.lineage.steps[0].kind == "fetch"
        assert s.lineage.steps[-1].kind == "adapter"

    def test_indexed_format_input(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05", "2026-01-06"])
        df = pd.DataFrame({"field_value": [4.10, 4.15, 4.18]}, index=idx)
        s = raw_dataframe_to_artifact_series(
            df,
            series_key="ust_10y",
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={"curve_family": "UST", "tenor": "10Y"},
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        assert len(s) == 3

    def test_handles_unsorted_input(self):
        df = pd.DataFrame({
            "trade_date": ["2026-01-06", "2026-01-02", "2026-01-05"],
            "field_value": [4.18, 4.10, 4.15],
        })
        s = raw_dataframe_to_artifact_series(
            df,
            series_key="x",
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={"curve_family": "UST"},
            missingness_policy=RawNoCleaning(),
        )
        # Index must be sorted ascending after adapter.
        assert s.payload.index.is_monotonic_increasing

    def test_drops_duplicate_dates_keeping_last(self):
        df = pd.DataFrame({
            "trade_date": ["2026-01-02", "2026-01-02", "2026-01-05"],
            "field_value": [4.00, 4.10, 4.15],
        })
        s = raw_dataframe_to_artifact_series(
            df,
            series_key="x",
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={},
            missingness_policy=RawNoCleaning(),
        )
        assert len(s) == 2
        # The "last" duplicate is kept — value at 2026-01-02 should be 4.10.
        assert s.payload.iloc[0] == 4.10

    def test_missing_value_column_raises(self):
        df = pd.DataFrame({"trade_date": ["2026-01-02"], "wrong_col": [1.0]})
        with pytest.raises(ValueError, match="must contain"):
            raw_dataframe_to_artifact_series(
                df,
                series_key="x",
                units=TimeSeriesUnits.PERCENT,
                source_kind="fetch_single_tenor",
                source_params={},
                missingness_policy=RawNoCleaning(),
            )

    def test_empty_after_coercion_raises(self):
        df = pd.DataFrame({
            "trade_date": ["2026-01-02"],
            "field_value": ["nonsense"],
        })
        with pytest.raises(ValueError, match="empty series"):
            raw_dataframe_to_artifact_series(
                df,
                series_key="x",
                units=TimeSeriesUnits.PERCENT,
                source_kind="fetch_single_tenor",
                source_params={},
                missingness_policy=RawNoCleaning(),
            )

    def test_explicit_upstream_lineage_preserved(self):
        """When the caller supplies upstream_lineage, the adapter
        prepends it instead of synthesising a FetchStep — the
        canonical Q1 path with explicit FetchStep + CleanStep."""
        from shared.artifacts.lineage import CleanStep
        fetch = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y"},
        )
        clean = CleanStep.build(
            name="clean_single_series", version="1.0.0",
            params={"ffill_limit": 5},
            input_hashes=(fetch.hash,),
        )
        df = pd.DataFrame({
            "trade_date": ["2026-01-02"],
            "field_value": [4.10],
        })
        s = raw_dataframe_to_artifact_series(
            df,
            series_key="ust_10y",
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={"curve_family": "UST", "tenor": "10Y"},
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            upstream_lineage=(fetch, clean),
        )
        # Lineage = [Fetch, Clean, Adapter]
        assert [step.kind for step in s.lineage.steps] == [
            "fetch", "clean", "adapter",
        ]


# ===========================================================================
# EventSet / Panel / WindowedPanel — basic construction sanity.
# ===========================================================================


class TestOtherArtifacts:
    def test_event_set_basic(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05", "2026-01-06"])
        mask = pd.Series([False, True, False], index=idx)
        op_step = OperatorStep.build(
            name="threshold_events", version="1.0.0",
            params={}, input_hashes=("h" * 64,),
        )
        es = EventSet(
            mask=mask,
            event_dates=[pd.Timestamp("2026-01-05")],
            per_event_metadata=[{"trigger_value": 1.6}],
            source_series_key="swap_spread",
            lineage=Lineage.from_steps([op_step]),
        )
        assert es.n_events == 1

    def test_event_set_count_mismatch_rejected(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        mask = pd.Series([True, True], index=idx)
        op_step = OperatorStep.build(name="x", version="1", params={}, input_hashes=())
        with pytest.raises(ValueError, match="True count"):
            EventSet(
                mask=mask,
                event_dates=[pd.Timestamp("2026-01-05")],  # only 1; mask has 2
                per_event_metadata=[{}],
                source_series_key="s",
                lineage=Lineage.from_steps([op_step]),
            )

    def test_windowed_panel_basic(self):
        op_step = OperatorStep.build(name="event_windows", version="1", params={}, input_hashes=())
        wp = WindowedPanel(
            payload=np.array([[0.0, 1.0, 2.0], [0.0, -1.0, -2.0]]),
            offsets=[0, 1, 2],
            event_dates=[pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-12")],
            per_event_metadata=[{}, {}],
            target_series_key="ust_10y",
            units=TimeSeriesUnits.BPS,
            lineage=Lineage.from_steps([op_step]),
        )
        assert wp.n_events == 2
        assert wp.window_length == 3

    def test_panel_basic(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        df = pd.DataFrame({"a": [1.0, 2.0], "b": [10.0, 20.0]}, index=idx)
        op_step = OperatorStep.build(name="x", version="1", params={}, input_hashes=())
        p = Panel(
            payload=df,
            units_by_column={"a": TimeSeriesUnits.PERCENT, "b": TimeSeriesUnits.PERCENT},
            missingness_policy=RawNoCleaning(),
            lineage=Lineage.from_steps([op_step]),
        )
        assert list(p.payload.columns) == ["a", "b"]
