"""Tests for shared.operators.align_series.

Covers:

  - happy-path inner / outer joins
  - fill_policy raw vs ffill (with and without fill_limit)
  - per-series unit / missingness preservation
  - SeriesSet.get_series propagates upstream lineage + appends alignment
    step (build plan v5 / R2)
  - structural-metadata compatibility checks (empty, duplicates,
    DatetimeIndex)
  - error envelope phrases
  - end-to-end composition: clean_single_series → adapter → align_series
    over synthetic fetch-shaped DataFrames (the canonical Q1 source path
    minus the live DB call)
  - admission-checklist sanity: align_series is finance-blind (runs on
    synthetic non-rates data of the right shape)
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from shared.analytics.levels import clean_single_series
from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    FetchStep,
    Lineage,
    RawNoCleaning,
    Series,
    SeriesSet,
    TimeSeriesUnits,
)
from shared.artifacts.adapters import raw_dataframe_to_artifact_series
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.align_series import (
    CONFIG_PATH,
    AlignSeriesParams,
    align_series,
)
from shared.operators.align_series.operator import AlignSeriesError


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
) -> Series:
    """Build a Series artifact with a synthesised fetch+adapter lineage."""
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


# ===========================================================================
# Happy path
# ===========================================================================


class TestAlignSeriesHappyPath:
    def test_inner_join_intersects_indexes(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05", "2026-01-06"], values=[1, 2, 3])
        b = _series("b", dates=["2026-01-05", "2026-01-06", "2026-01-07"], values=[10, 20, 30])
        out = align_series([a, b], AlignSeriesParams(join_policy="inner"))
        assert isinstance(out, SeriesSet)
        assert list(out.common_index) == [pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")]
        assert out.keys() == ["a", "b"]

    def test_outer_join_unions_indexes_introduces_nans(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2])
        b = _series("b", dates=["2026-01-05", "2026-01-06"], values=[10, 20])
        out = align_series([a, b], AlignSeriesParams(join_policy="outer", fill_policy="raw"))
        # Common index is the union
        assert list(out.common_index) == [
            pd.Timestamp("2026-01-02"),
            pd.Timestamp("2026-01-05"),
            pd.Timestamp("2026-01-06"),
        ]
        # 'a' is NaN at 2026-01-06; 'b' is NaN at 2026-01-02
        a_aligned = out.get_series("a").payload
        b_aligned = out.get_series("b").payload
        assert pd.isna(a_aligned.loc["2026-01-06"])
        assert pd.isna(b_aligned.loc["2026-01-02"])

    def test_outer_join_with_ffill_bridges_gaps(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0])
        b = _series("b", dates=["2026-01-05", "2026-01-06"], values=[10.0, 20.0])
        out = align_series(
            [a, b],
            AlignSeriesParams(join_policy="outer", fill_policy="ffill"),
        )
        a_aligned = out.get_series("a").payload
        # 'a' had no value at 2026-01-06; ffill carries 2.0 from 2026-01-05
        assert a_aligned.loc["2026-01-06"] == 2.0

    def test_ffill_limit_caps_filling(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        b = _series("b", dates=["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"],
                    values=[10, 20, 30, 40])
        # Limit ffill to 2 cells.  After 2 fills the rest stays NaN.
        out = align_series(
            [a, b],
            AlignSeriesParams(
                join_policy="outer", fill_policy="ffill", fill_limit=2,
            ),
        )
        a_aligned = out.get_series("a").payload
        # 2026-01-02: 1.0 (original)
        # 2026-01-05: 1.0 (1st ffill)
        # 2026-01-06: 1.0 (2nd ffill)
        # 2026-01-07: NaN (3rd consecutive gap, capped at limit=2)
        assert a_aligned.loc["2026-01-02"] == 1.0
        assert a_aligned.loc["2026-01-05"] == 1.0
        assert a_aligned.loc["2026-01-06"] == 1.0
        assert pd.isna(a_aligned.loc["2026-01-07"])

    def test_units_and_missingness_preserved_per_key(self):
        # Different units across inputs; align_series must NOT enforce
        # unit equality (it is a permitted heterogeneous metadata).
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2],
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=["2026-01-02", "2026-01-05"], values=[1, 2],
                    units=TimeSeriesUnits.BPS)
        out = align_series([a, b])
        assert out.get_series("a").units == TimeSeriesUnits.PERCENT
        assert out.get_series("b").units == TimeSeriesUnits.BPS

    def test_uses_config_defaults_when_params_omitted(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2])
        b = _series("b", dates=["2026-01-05", "2026-01-06"], values=[10, 20])
        out = align_series([a, b])  # no params -> defaults: inner / raw / null
        # inner join => only 2026-01-05 in common
        assert list(out.common_index) == [pd.Timestamp("2026-01-05")]


# ===========================================================================
# Lineage propagation (build plan v5 / R2)
# ===========================================================================


class TestLineagePropagation:
    def test_get_series_appends_align_step(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2])
        b = _series("b", dates=["2026-01-02", "2026-01-05"], values=[10, 20])
        out = align_series([a, b])
        a_view = out.get_series("a")
        # a's upstream lineage was [fetch, adapter]; the retrieved view
        # must have those two PLUS the align_series step.
        assert [s.kind for s in a_view.lineage.steps] == [
            "fetch", "adapter", "operator",
        ]
        assert a_view.lineage.steps[-1].name == "align_series"

    def test_set_lineage_head_is_align_step(self):
        a = _series("a", dates=["2026-01-02"], values=[1])
        b = _series("b", dates=["2026-01-02"], values=[10])
        out = align_series([a, b])
        # The SeriesSet's own lineage has only the alignment step at v1.
        assert len(out.lineage.steps) == 1
        assert out.lineage.steps[0].name == "align_series"

    def test_align_step_hash_invariant_to_input_order(self):
        """Operator architecture: alignment is symmetric under input
        reordering (we sort series_keys inside the operator), so the
        head hash should be identical regardless of input order."""
        a = _series("a", dates=["2026-01-02"], values=[1])
        b = _series("b", dates=["2026-01-02"], values=[10])
        out_ab = align_series([a, b])
        out_ba = align_series([b, a])
        assert out_ab.lineage.head_hash == out_ba.lineage.head_hash

    def test_align_step_hash_changes_with_params(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2])
        b = _series("b", dates=["2026-01-05", "2026-01-06"], values=[10, 20])
        inner = align_series([a, b], AlignSeriesParams(join_policy="inner"))
        outer = align_series([a, b], AlignSeriesParams(join_policy="outer", fill_policy="raw"))
        assert inner.lineage.head_hash != outer.lineage.head_hash

    def test_lineage_json_roundtrip_through_alignment(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2])
        b = _series("b", dates=["2026-01-02", "2026-01-05"], values=[10, 20])
        out = align_series([a, b])
        view = out.get_series("a")
        as_dict = view.lineage.model_dump(mode="json")
        rec = Lineage.model_validate(as_dict)
        assert rec.head_hash == view.lineage.head_hash
        assert len(rec.steps) == 3


# ===========================================================================
# Structural-metadata + error envelopes
# ===========================================================================


class TestStructuralChecksAndErrors:
    def test_empty_input_raises(self):
        with pytest.raises(AlignSeriesError, match="at least 1"):
            align_series([])

    def test_duplicate_keys_raises(self):
        a = _series("dupe", dates=["2026-01-02"], values=[1])
        b = _series("dupe", dates=["2026-01-02"], values=[10])
        with pytest.raises(AlignSeriesError, match="duplicate series_key"):
            align_series([a, b])

    def test_inner_join_empty_intersection_raises(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2])
        b = _series("b", dates=["2026-02-02", "2026-02-05"], values=[10, 20])
        with pytest.raises(AlignSeriesError, match="combined index is empty"):
            align_series([a, b], AlignSeriesParams(join_policy="inner"))

    def test_single_series_alignment_is_identity(self):
        """align_series on N=1 should still produce a valid SeriesSet
        (useful for orchestration uniformity)."""
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2])
        out = align_series([a])
        assert out.keys() == ["a"]
        assert list(out.common_index) == [pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")]


# ===========================================================================
# Composition test (canonical Q1-shape source path; no DB)
# ===========================================================================


class TestComposition:
    """The load-bearing test for the operator architecture: a real
    fetch → clean → adapt → align chain produces a SeriesSet whose
    lineage object lists every step + its parameters.  This is the
    Week 5 milestone exit criterion's smallest verifiable form,
    minus the live DB call (we feed a synthetic fetch DataFrame).
    """

    def _synthetic_fetch_df(self, n_days: int, seed: int = 7) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=n_days * 2), date(2026, 4, 30),
        )[-n_days:]
        return pd.DataFrame({
            "trade_date": bdays.date,
            "field_value": 4.0 + np.cumsum(rng.normal(0, 0.01, len(bdays))),
        })

    def test_fetch_clean_adapt_align_endtoend(self):
        # Two separate fetches simulate the Q1 source path.
        raw_a = self._synthetic_fetch_df(50, seed=1)
        raw_b = self._synthetic_fetch_df(50, seed=2)

        from shared.artifacts.lineage import CleanStep

        # 1. Pretend we did a real fetch — record it as a FetchStep.
        fetch_a = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y", "field_name": "YLD_YTM_MID"},
        )
        fetch_b = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "2Y", "field_name": "YLD_YTM_MID"},
        )

        # 2. Clean the fetched DataFrames the same way every existing
        #    primitive does, and record the step.
        clean_a_df = clean_single_series(raw_a, ffill_limit=5)
        clean_a = CleanStep.build(
            name="clean_single_series", version="1.0.0",
            params={"ffill_limit": 5},
            input_hashes=(fetch_a.hash,),
        )
        clean_b_df = clean_single_series(raw_b, ffill_limit=5)
        clean_b = CleanStep.build(
            name="clean_single_series", version="1.0.0",
            params={"ffill_limit": 5},
            input_hashes=(fetch_b.hash,),
        )

        # 3. Adapt cleaned DataFrames to typed Series artifacts.
        s_a = raw_dataframe_to_artifact_series(
            clean_a_df,
            series_key="ust_10y",
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={"curve_family": "UST", "tenor": "10Y"},
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            upstream_lineage=(fetch_a, clean_a),
        )
        s_b = raw_dataframe_to_artifact_series(
            clean_b_df,
            series_key="ust_2y",
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={"curve_family": "UST", "tenor": "2Y"},
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            upstream_lineage=(fetch_b, clean_b),
        )

        # 4. Align them.
        out = align_series([s_a, s_b])

        # ----- Assertions on the composition -----
        # The retrieved per-series lineage must include all four step
        # kinds in order: fetch → clean → adapter → operator.
        view_a = out.get_series("ust_10y")
        kinds_a = [s.kind for s in view_a.lineage.steps]
        assert kinds_a == ["fetch", "clean", "adapter", "operator"], (
            f"unexpected per-series lineage kinds: {kinds_a}"
        )
        assert view_a.lineage.steps[-1].name == "align_series"

        # Hash-stability under serialization round-trip.
        as_json = view_a.lineage.model_dump(mode="json")
        recovered = Lineage.model_validate(as_json)
        assert recovered.head_hash == view_a.lineage.head_hash
        # And the per-step hashes survive byte-identically.
        for o, r in zip(view_a.lineage.steps, recovered.steps):
            assert o.hash == r.hash

    def test_finance_blind_runs_on_non_rates_shape(self):
        """Admission criterion — same operator, different domain.

        Build two synthetic 'temperature' series (units=z_score is the
        closest unit-enum stand-in for 'unitless physical' in v1) and
        verify align_series produces a SeriesSet without any rates-
        specific handling.  This is the canonical 'could this run on
        non-rates data?' check from operator_architecture.md."""
        idx = pd.bdate_range("2024-01-01", "2024-01-12")
        a = _series(
            "ny_temp",
            dates=list(idx[:5]),
            values=[10.0, 11.0, 12.0, 11.5, 10.5],
            units=TimeSeriesUnits.Z_SCORE,
        )
        b = _series(
            "sf_temp",
            dates=list(idx[2:7]),
            values=[15.0, 16.0, 14.0, 13.5, 12.5],
            units=TimeSeriesUnits.Z_SCORE,
        )
        out = align_series([a, b])
        assert out.keys() == ["ny_temp", "sf_temp"]
        # Inner intersection gives 3 overlapping dates.
        assert len(out.common_index) == 3


# ===========================================================================
# Bundled config consistency
# ===========================================================================


class TestBundledConfig:
    def test_config_path_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads_with_correct_identity(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "align_series"
        assert cfg.operator.method_family == "alignment"

    def test_operator_uses_bundled_config_when_omitted(self):
        """Verifies that the operator wires through to its bundled
        config without an explicit caller-supplied OperatorConfig."""
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2])
        b = _series("b", dates=["2026-01-05", "2026-01-06"], values=[10, 20])
        # No params, no config — defaults must come from disk.
        out = align_series([a, b])
        head_step = out.lineage.steps[-1]
        assert head_step.params["join_policy"] == "inner"
        assert head_step.params["fill_policy"] == "raw"
        assert head_step.params["fill_limit"] is None
