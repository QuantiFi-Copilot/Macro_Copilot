"""Tests for shared.operators.align_series.

Covers:

  - happy-path inner / outer joins
  - fill_policy raw vs ffill (with and without fill_limit)
  - per-series unit / missingness preservation
  - SeriesSet.get_series propagates upstream lineage + appends alignment
    step (build plan v5 / R2)
  - structural-metadata compatibility checks (build plan v5 / Codex
    follow-up): missingness compatibility, frequency compatibility,
    ffill missingness wrapping, frequency preservation
  - error envelope phrases
  - end-to-end composition: clean_single_series → adapter → align_series
    over synthetic fetch-shaped DataFrames (the canonical Q1 source path
    minus the live DB call)
  - fetch_single_tenor contract: signature + return-shape verified via
    a MagicMock engine so adapter-vs-fetch drift is caught even without
    a live DB
  - admission-checklist sanity: align_series is finance-blind (runs on
    synthetic non-rates data of the right shape)
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from shared.analytics.levels import clean_single_series
from shared.analytics.rates_fetch import fetch_single_tenor
from shared.artifacts import (
    AdapterStep,
    AlignSeriesFFillV1,
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
    frequency=None,
    missingness_policy=None,
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
        frequency=frequency,
        missingness_policy=missingness_policy or CleanSingleSeriesV1(ffill_limit=5),
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
        assert head_step.params["require_matching_frequency"] is True
        assert head_step.params["require_matching_missingness"] is True


# ===========================================================================
# Structural-metadata compatibility (Codex P1 follow-up)
# ===========================================================================


class TestFrequencyCompatibility:
    """Build plan v5 / Codex P1: frequency tags must be checked, and
    the resolved common frequency must flow to the output."""

    def test_matching_frequencies_preserved_on_output(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1, 2],
                    frequency="B")
        b = _series("b", dates=["2026-01-02", "2026-01-05"], values=[10, 20],
                    frequency="B")
        out = align_series([a, b])
        assert out.frequency == "B"
        # And the alignment-step's params record the resolved value.
        assert out.lineage.steps[-1].params["resolved_frequency"] == "B"

    def test_all_none_frequencies_yields_none_output(self):
        a = _series("a", dates=["2026-01-02"], values=[1])  # frequency=None default
        b = _series("b", dates=["2026-01-02"], values=[10])
        out = align_series([a, b])
        assert out.frequency is None

    def test_mismatched_frequencies_strict_raises(self):
        a = _series("a", dates=["2026-01-02"], values=[1], frequency="B")
        b = _series("b", dates=["2026-01-02"], values=[10], frequency="W")
        with pytest.raises(AlignSeriesError, match="incompatible frequencies"):
            align_series([a, b])

    def test_partial_frequency_tagging_strict_raises(self):
        """If some inputs declare a frequency and others don't, in
        strict mode we surface that as a partial-metadata case rather
        than silently picking the tagged one."""
        a = _series("a", dates=["2026-01-02"], values=[1], frequency="B")
        b = _series("b", dates=["2026-01-02"], values=[10])  # no tag
        with pytest.raises(AlignSeriesError, match="some inputs declare"):
            align_series([a, b])

    def test_lenient_mode_allows_mismatch_and_drops_frequency(self):
        """``require_matching_frequency=False`` accepts mixed-frequency
        inputs; the output's ``frequency`` is None because we cannot
        honestly emit a single tag."""
        a = _series("a", dates=["2026-01-02"], values=[1], frequency="B")
        b = _series("b", dates=["2026-01-02"], values=[10], frequency="W")
        out = align_series(
            [a, b],
            AlignSeriesParams(require_matching_frequency=False),
        )
        assert out.frequency is None
        assert out.lineage.steps[-1].params["require_matching_frequency"] is False


class TestMissingnessCompatibility:
    """Build plan v5 / Codex P1: missingness policy is structured
    metadata; mixing it across inputs without explicit opt-in is
    silently unsafe and the operator must surface it."""

    def test_matching_policies_pass(self):
        a = _series("a", dates=["2026-01-02"], values=[1],
                    missingness_policy=CleanSingleSeriesV1(ffill_limit=5))
        b = _series("b", dates=["2026-01-02"], values=[10],
                    missingness_policy=CleanSingleSeriesV1(ffill_limit=5))
        out = align_series([a, b])
        # No exception; payload stays as-is (fill_policy='raw' default).
        assert out.get_series("a").missingness_policy == CleanSingleSeriesV1(
            ffill_limit=5
        )

    def test_different_kinds_strict_raises(self):
        a = _series("a", dates=["2026-01-02"], values=[1],
                    missingness_policy=CleanSingleSeriesV1(ffill_limit=5))
        b = _series("b", dates=["2026-01-02"], values=[10],
                    missingness_policy=RawNoCleaning())
        with pytest.raises(AlignSeriesError, match="incompatible missingness"):
            align_series([a, b])

    def test_same_kind_different_params_strict_raises(self):
        """Same kind (CleanSingleSeriesV1) but different ffill_limit is
        also a mismatch — ``CleanSingleSeriesV1(ffill_limit=5)`` is not
        equivalent to ``CleanSingleSeriesV1(ffill_limit=10)``."""
        a = _series("a", dates=["2026-01-02"], values=[1],
                    missingness_policy=CleanSingleSeriesV1(ffill_limit=5))
        b = _series("b", dates=["2026-01-02"], values=[10],
                    missingness_policy=CleanSingleSeriesV1(ffill_limit=10))
        with pytest.raises(AlignSeriesError, match="incompatible missingness"):
            align_series([a, b])

    def test_lenient_mode_allows_mismatch(self):
        a = _series("a", dates=["2026-01-02"], values=[1],
                    missingness_policy=CleanSingleSeriesV1(ffill_limit=5))
        b = _series("b", dates=["2026-01-02"], values=[10],
                    missingness_policy=RawNoCleaning())
        out = align_series(
            [a, b],
            AlignSeriesParams(require_matching_missingness=False),
        )
        # Per-key policies are preserved unchanged in lenient mode
        # (for the raw fill policy).
        assert isinstance(
            out.get_series("a").missingness_policy, CleanSingleSeriesV1
        )
        assert isinstance(
            out.get_series("b").missingness_policy, RawNoCleaning
        )
        assert out.lineage.steps[-1].params[
            "require_matching_missingness"
        ] is False


class TestFFillMetadataHonesty:
    """Build plan v5 / Codex P1: when fill_policy='ffill' materially
    changes the payload, the output's missingness policy must be
    wrapped in AlignSeriesFFillV1 so consumers see the imputation
    honestly."""

    def test_raw_fill_keeps_upstream_policy(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        b = _series("b", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0])
        out = align_series(
            [a, b],
            AlignSeriesParams(join_policy="outer", fill_policy="raw"),
        )
        # Payload unchanged in shape — upstream policy intact.
        a_view = out.get_series("a")
        assert isinstance(a_view.missingness_policy, CleanSingleSeriesV1)

    def test_ffill_wraps_upstream_policy(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        b = _series("b", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0])
        out = align_series(
            [a, b],
            AlignSeriesParams(
                join_policy="outer", fill_policy="ffill", fill_limit=3,
            ),
        )
        a_view = out.get_series("a")
        # Wrapped policy + upstream preserved underneath.
        assert isinstance(a_view.missingness_policy, AlignSeriesFFillV1)
        assert a_view.missingness_policy.fill_limit == 3
        assert isinstance(
            a_view.missingness_policy.upstream, CleanSingleSeriesV1
        )

    def test_ffill_wrapper_roundtrips_through_json(self):
        """Discriminated-union recursion must serialise cleanly."""
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        b = _series("b", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0])
        out = align_series(
            [a, b],
            AlignSeriesParams(
                join_policy="outer", fill_policy="ffill", fill_limit=2,
            ),
        )
        a_view = out.get_series("a")
        as_dict = a_view.missingness_policy.model_dump(mode="json")
        # Reconstruct via the discriminated union (use Series rebuild
        # path to validate).
        from pydantic import TypeAdapter
        from shared.artifacts.missingness import MissingnessPolicy
        ta = TypeAdapter(MissingnessPolicy)
        recovered = ta.validate_python(as_dict)
        assert isinstance(recovered, AlignSeriesFFillV1)
        assert recovered.fill_limit == 2
        assert isinstance(recovered.upstream, CleanSingleSeriesV1)


class TestNestedPolicyRecomposition:
    """Codex P1 follow-up: ``AlignSeriesFFillV1`` carries a nested
    ``upstream`` policy.  The strict missingness check must handle
    that nesting — comparing two such policies via
    ``tuple(sorted(model_dump.items()))`` raises ``TypeError`` because
    the nested ``upstream`` is a dict.  Switching to a canonical JSON
    string is what makes nested-policy composition work.

    These tests pin the contract that align_series can take its OWN
    output (whose policy is AlignSeriesFFillV1) and re-align it
    without crashing.
    """

    def _ffilled_series(self, series_key: str, dates, values):
        """Helper: produce a Series whose missingness_policy is a real
        AlignSeriesFFillV1 (i.e. the kind of policy that comes out of
        a prior align_series with fill_policy='ffill')."""
        a = _series("__inner_a", dates=dates, values=values)
        b = _series(
            "__inner_b",
            dates=[dates[0], dates[-1]],
            values=[values[0], values[-1]],
        )
        out = align_series(
            [a, b],
            AlignSeriesParams(
                join_policy="outer", fill_policy="ffill", fill_limit=3,
            ),
        )
        view = out.get_series("__inner_a")
        # Sanity: the helper actually produced a wrapper policy.
        assert isinstance(view.missingness_policy, AlignSeriesFFillV1)
        # Re-key the resulting Series so the caller can pass it back
        # in alongside another input under a stable name.
        return Series(
            series_key=series_key,
            payload=view.payload,
            units=view.units,
            frequency=view.frequency,
            missingness_policy=view.missingness_policy,
            lineage=view.lineage,
        )

    def test_two_identical_nested_policies_pass_strict_check(self):
        """Two series both carrying ``AlignSeriesFFillV1(upstream=...,
        fill_limit=3)`` with the same upstream are compatible — the
        strict check must NOT crash and must NOT raise."""
        a = self._ffilled_series(
            "a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0],
        )
        b = self._ffilled_series(
            "b", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0],
        )
        # Same recipe ⇒ identical wrapper policies ⇒ should pass
        # strict missingness compatibility without TypeError.
        out = align_series([a, b])
        assert out.keys() == ["a", "b"]

    def test_nested_policies_with_different_fill_limits_raise_alignseries_error(self):
        """When two nested policies differ only in their inner
        fill_limit, the strict check must raise the controlled
        ``AlignSeriesError`` — not a raw TypeError, and not silent
        acceptance."""
        # First series: AlignSeriesFFillV1(fill_limit=3, upstream=Clean(ffill_limit=5))
        a = self._ffilled_series(
            "a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0],
        )
        # Second series: hand-build a different nested wrapper
        # (fill_limit=7) with the same upstream so the strict check
        # has a concrete mismatch to surface.
        b_inner = _series("b", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0])
        b = Series(
            series_key="b",
            payload=b_inner.payload,
            units=b_inner.units,
            frequency=b_inner.frequency,
            missingness_policy=AlignSeriesFFillV1(
                upstream=CleanSingleSeriesV1(ffill_limit=5),
                fill_limit=7,
            ),
            lineage=b_inner.lineage,
        )
        with pytest.raises(AlignSeriesError, match="incompatible missingness"):
            align_series([a, b])

    def test_nested_vs_flat_policy_mismatch_raises_alignseries_error(self):
        """A nested ``AlignSeriesFFillV1`` and a flat
        ``CleanSingleSeriesV1`` are NOT compatible in strict mode.
        The check must surface this without TypeError."""
        a = self._ffilled_series(
            "a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0],
        )
        b = _series(
            "b", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0],
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        with pytest.raises(AlignSeriesError, match="incompatible missingness"):
            align_series([a, b])

    def test_nested_policy_lenient_mode_passes_through(self):
        """Lenient mode accepts mixed nested/flat policies and
        preserves them per-key (under raw fill)."""
        a = self._ffilled_series(
            "a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0],
        )
        b = _series(
            "b", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0],
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        out = align_series(
            [a, b],
            AlignSeriesParams(require_matching_missingness=False),
        )
        # Per-key policies preserved under raw fill (no further
        # wrapping happens because fill_policy='raw' is the default).
        assert isinstance(
            out.get_series("a").missingness_policy, AlignSeriesFFillV1
        )
        assert isinstance(
            out.get_series("b").missingness_policy, CleanSingleSeriesV1
        )

    def test_nested_policy_re_ffill_double_wraps(self):
        """When a series carrying ``AlignSeriesFFillV1`` goes through
        ``align_series`` again with ``fill_policy='ffill'``, the
        operator must wrap the *current* policy as the new
        ``upstream`` — producing
        ``AlignSeriesFFillV1(upstream=AlignSeriesFFillV1(...), ...)``.
        Honest layered provenance."""
        a = self._ffilled_series(
            "a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0],
        )
        b = self._ffilled_series(
            "b", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0],
        )
        out = align_series(
            [a, b],
            AlignSeriesParams(
                join_policy="outer", fill_policy="ffill", fill_limit=2,
            ),
        )
        view = out.get_series("a")
        assert isinstance(view.missingness_policy, AlignSeriesFFillV1)
        assert view.missingness_policy.fill_limit == 2
        # The new wrapper's upstream is the FIRST wrapper (depth-2).
        assert isinstance(view.missingness_policy.upstream, AlignSeriesFFillV1)
        assert view.missingness_policy.upstream.fill_limit == 3
        # And THAT wrapper's upstream is the original CleanSingleSeriesV1.
        assert isinstance(
            view.missingness_policy.upstream.upstream, CleanSingleSeriesV1
        )


# ===========================================================================
# fetch_single_tenor contract (Codex P2 follow-up)
# ===========================================================================


class TestFetchSingleTenorContract:
    """The Week 1 plan said "prove the fetch boundary."  We can't hit a
    live DB in unit tests, but we can verify the actual function's
    signature + return-shape contract by mocking the engine.  If
    fetch_single_tenor's column names ever drift away from
    ['trade_date', 'field_value'] — which is what the adapter assumes
    — these tests fail."""

    def test_signature_is_compatible_with_adapter(self):
        """Static signature check.  Adapter construction below assumes
        these exact parameter names; if the fetch helper renames any of
        them, the test fails before any DB call."""
        import inspect
        sig = inspect.signature(fetch_single_tenor)
        # The five canonical params expected by every primitive that
        # uses fetch_single_tenor + the orchestration layer.
        for name in ("engine", "curve_family", "tenor", "field_name", "start_date"):
            assert name in sig.parameters, (
                f"fetch_single_tenor signature missing '{name}'; the "
                "adapter / primitive callers depend on this name."
            )

    def test_returns_dataframe_with_expected_columns(self):
        """End-to-end shape contract via mocked engine.  Ensures
        fetch_single_tenor returns a DataFrame with the exact columns
        ('trade_date', 'field_value') the adapter feeds into
        ``raw_dataframe_to_artifact_series``."""
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        mock_result = MagicMock(name="result")
        mock_result.fetchall.return_value = [
            (date(2026, 1, 2), 4.10),
            (date(2026, 1, 5), 4.15),
        ]
        mock_result.keys.return_value = ["trade_date", "field_value"]
        mock_conn.execute.return_value = mock_result

        df = fetch_single_tenor(
            engine=mock_engine,
            curve_family="UST",
            tenor="10Y",
            field_name="YLD_YTM_MID",
            start_date=date(2026, 1, 1),
        )
        # Exact column set the adapter expects.
        assert list(df.columns) == ["trade_date", "field_value"]
        assert len(df) == 2

    def test_instrument_type_default_omits_filter(self):
        """The optional ``instrument_type`` parameter must default to
        ``None`` and the SQL bind parameters must NOT carry an
        ``instrument_type`` key when the caller doesn't provide one.
        Existing callers (yield_levels, OIS rate_level, the workflow
        synthetic fetchers) rely on this — adding a new bind silently
        would change the SQL plan."""
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        mock_result = MagicMock(name="result")
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = ["trade_date", "field_value"]
        mock_conn.execute.return_value = mock_result

        fetch_single_tenor(
            engine=mock_engine,
            curve_family="UST",
            tenor="10Y",
            field_name="YLD_YTM_MID",
            start_date=date(2026, 1, 1),
        )
        bind_params = mock_conn.execute.call_args.args[1]
        assert "instrument_type" not in bind_params, (
            "default instrument_type=None must NOT inject the bind; "
            f"got {sorted(bind_params.keys())}"
        )

    def test_instrument_type_filter_threads_to_sql_binds(self):
        """When the caller passes ``instrument_type='inflation_linker'``,
        the bind dictionary must include it so the WHERE clause filters
        the row set.  This is the load-bearing seam for the linker
        real_yield_level proxy-prevention guarantee."""
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        mock_result = MagicMock(name="result")
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = ["trade_date", "field_value"]
        mock_conn.execute.return_value = mock_result

        fetch_single_tenor(
            engine=mock_engine,
            curve_family="USD_TIPS",
            tenor="10Y",
            field_name="YLD_YTM_MID",
            start_date=date(2026, 1, 1),
            instrument_type="inflation_linker",
        )
        bind_params = mock_conn.execute.call_args.args[1]
        assert bind_params.get("instrument_type") == "inflation_linker"

    def test_full_fetch_clean_adapt_align_chain_with_mocked_engine(self):
        """Full Q1 source path with the real fetch_single_tenor +
        clean_single_series (no DB; engine mocked to return a
        canonical fetch payload).  Catches drift in any link of the
        chain (column names, dtype coercion, lineage shape)."""
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        # Two synthetic fetches (one per series we'll align).
        bdays = pd.bdate_range("2026-01-02", periods=20)
        rng = np.random.default_rng(7)
        rows_a = [(d.date(), float(v)) for d, v in zip(
            bdays, 4.0 + np.cumsum(rng.normal(0, 0.005, 20))
        )]
        rows_b = [(d.date(), float(v)) for d, v in zip(
            bdays, 3.5 + np.cumsum(rng.normal(0, 0.005, 20))
        )]

        # mock_conn.execute is called twice; return different rows
        # each time via side_effect.
        result_a = MagicMock()
        result_a.fetchall.return_value = rows_a
        result_a.keys.return_value = ["trade_date", "field_value"]
        result_b = MagicMock()
        result_b.fetchall.return_value = rows_b
        result_b.keys.return_value = ["trade_date", "field_value"]
        mock_conn.execute.side_effect = [result_a, result_b]

        from shared.artifacts.lineage import CleanStep

        # 1. Real fetch_single_tenor.
        df_a = fetch_single_tenor(
            mock_engine, "UST", "10Y", "YLD_YTM_MID", date(2026, 1, 1),
        )
        df_b = fetch_single_tenor(
            mock_engine, "USD", "2Y", "YLD_YTM_MID", date(2026, 1, 1),
        )
        # 2. Real clean_single_series.
        clean_a = clean_single_series(df_a, ffill_limit=5)
        clean_b = clean_single_series(df_b, ffill_limit=5)

        # 3. Adapter — with explicit FetchStep + CleanStep upstream
        #    lineage so the chain is honest.
        fetch_step_a = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y",
                    "field_name": "YLD_YTM_MID"},
        )
        fetch_step_b = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "USD", "tenor": "2Y",
                    "field_name": "YLD_YTM_MID"},
        )
        clean_step_a = CleanStep.build(
            name="clean_single_series", version="1.0.0",
            params={"ffill_limit": 5},
            input_hashes=(fetch_step_a.hash,),
        )
        clean_step_b = CleanStep.build(
            name="clean_single_series", version="1.0.0",
            params={"ffill_limit": 5},
            input_hashes=(fetch_step_b.hash,),
        )

        s_a = raw_dataframe_to_artifact_series(
            clean_a,
            series_key="ust_10y",
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={"curve_family": "UST", "tenor": "10Y"},
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            upstream_lineage=(fetch_step_a, clean_step_a),
        )
        s_b = raw_dataframe_to_artifact_series(
            clean_b,
            series_key="ois_2y",
            units=TimeSeriesUnits.PERCENT,
            source_kind="fetch_single_tenor",
            source_params={"curve_family": "USD", "tenor": "2Y"},
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            upstream_lineage=(fetch_step_b, clean_step_b),
        )

        # 4. align_series.
        out = align_series([s_a, s_b])
        assert out.keys() == ["ois_2y", "ust_10y"]
        # Per-series lineage covers every step kind.
        view = out.get_series("ust_10y")
        assert [s.kind for s in view.lineage.steps] == [
            "fetch", "clean", "adapter", "operator",
        ]


# ===========================================================================
# output_keys (Codex P2 follow-up to PR #87)
# ===========================================================================
#
# The leaked-implementation-detail-slot fix: align_series now accepts
# an optional ``output_keys`` param so template authors can rename
# SeriesSet members from each input's bridge-derived series_key (a
# wire-naming detail) to template-controlled names like
# ["signal", "target"].  Downstream select_from_series_set then
# references those template-controlled names as hard-coded literals,
# restoring the workflow-architecture contract's "templates expose
# only central analysis knobs, not wiring internals" discipline.


class TestOutputKeysRename:
    def test_default_no_rename(self):
        """Without ``output_keys``, the SeriesSet's per-key dicts use
        each input's series_key verbatim — backward-compatible."""
        a = _series(
            "ust_usd_sofr_ois_2y_swap_spread_change_zscore",
            dates=["2026-01-05", "2026-01-06"], values=[1.0, 2.0],
            units=TimeSeriesUnits.Z_SCORE,
            missingness_policy=RawNoCleaning(),
        )
        b = _series(
            "ust_10y_yield",
            dates=["2026-01-05", "2026-01-06"], values=[4.30, 4.31],
            units=TimeSeriesUnits.PERCENT,
            missingness_policy=RawNoCleaning(),
        )
        out = align_series([a, b], AlignSeriesParams(
            join_policy="inner", require_matching_missingness=False,
        ))
        assert out.keys() == sorted([
            "ust_usd_sofr_ois_2y_swap_spread_change_zscore",
            "ust_10y_yield",
        ])

    def test_explicit_output_keys_renames(self):
        """When ``output_keys`` is supplied, the SeriesSet uses those
        names instead of the inputs' series_keys."""
        a = _series(
            "ust_usd_sofr_ois_2y_swap_spread_change_zscore",
            dates=["2026-01-05", "2026-01-06"], values=[1.0, 2.0],
            units=TimeSeriesUnits.Z_SCORE,
            missingness_policy=RawNoCleaning(),
        )
        b = _series(
            "ust_10y_yield",
            dates=["2026-01-05", "2026-01-06"], values=[4.30, 4.31],
            units=TimeSeriesUnits.PERCENT,
            missingness_policy=RawNoCleaning(),
        )
        out = align_series([a, b], AlignSeriesParams(
            join_policy="inner",
            require_matching_missingness=False,
            output_keys=["signal", "target"],
        ))
        assert out.keys() == ["signal", "target"]
        # Per-key metadata follows declaration order.
        assert out.units_by_key["signal"] == TimeSeriesUnits.Z_SCORE
        assert out.units_by_key["target"] == TimeSeriesUnits.PERCENT
        # Original input series_keys do NOT appear as output keys.
        assert (
            "ust_usd_sofr_ois_2y_swap_spread_change_zscore"
            not in out.series_by_key
        )
        assert "ust_10y_yield" not in out.series_by_key

    def test_get_series_works_against_renamed_keys(self):
        """Downstream operators retrieve members by the new name."""
        a = _series(
            "primitive_a_series",
            dates=["2026-01-05", "2026-01-06"], values=[1.0, 2.0],
            missingness_policy=RawNoCleaning(),
        )
        b = _series(
            "primitive_b_series",
            dates=["2026-01-05", "2026-01-06"], values=[3.0, 4.0],
            missingness_policy=RawNoCleaning(),
        )
        out = align_series([a, b], AlignSeriesParams(
            join_policy="inner",
            require_matching_missingness=False,
            output_keys=["signal", "target"],
        ))
        signal_series = out.get_series("signal")
        assert signal_series.series_key == "signal"
        # The original primitive's series_key survives in the upstream
        # lineage chain (the FetchStep / AdapterStep params carry it).
        chain_params = [s.params for s in signal_series.lineage.steps]
        param_blob = " ".join(str(p) for p in chain_params)
        assert "primitive_a_series" in param_blob

    def test_rename_recorded_in_lineage_step_params(self):
        """The align step's params record both the input keys AND
        the output keys, plus the input→output map, so a downstream
        lineage walker can recover the rename even when the SeriesSet
        is consumed by ordinal."""
        a = _series(
            "primitive_a", dates=["2026-01-05"], values=[1.0],
            missingness_policy=RawNoCleaning(),
        )
        b = _series(
            "primitive_b", dates=["2026-01-05"], values=[2.0],
            missingness_policy=RawNoCleaning(),
        )
        out = align_series([a, b], AlignSeriesParams(
            join_policy="inner",
            require_matching_missingness=False,
            output_keys=["signal", "target"],
        ))
        align_step = out.lineage.steps[-1]
        assert align_step.params["output_series_keys"] == [
            "signal", "target",
        ]
        assert align_step.params["input_to_output_key_map"] == {
            "primitive_a": "signal",
            "primitive_b": "target",
        }

    def test_rename_omitted_does_not_emit_output_keys_in_step_params(self):
        """When output_keys is None (default), the step params don't
        record an empty / null rename — keeps lineage frames small
        and signals to a downstream walker that no rename happened."""
        a = _series(
            "primitive_a", dates=["2026-01-05"], values=[1.0],
            missingness_policy=RawNoCleaning(),
        )
        b = _series(
            "primitive_b", dates=["2026-01-05"], values=[2.0],
            missingness_policy=RawNoCleaning(),
        )
        out = align_series([a, b], AlignSeriesParams(
            join_policy="inner",
            require_matching_missingness=False,
        ))
        align_step = out.lineage.steps[-1]
        assert "output_series_keys" not in align_step.params
        assert "input_to_output_key_map" not in align_step.params

    def test_output_keys_length_mismatch_raises(self):
        a = _series(
            "primitive_a", dates=["2026-01-05"], values=[1.0],
            missingness_policy=RawNoCleaning(),
        )
        b = _series(
            "primitive_b", dates=["2026-01-05"], values=[2.0],
            missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(AlignSeriesError, match="output_keys length"):
            align_series([a, b], AlignSeriesParams(
                join_policy="inner",
                require_matching_missingness=False,
                output_keys=["only_one"],
            ))

    def test_duplicate_output_keys_rejected_at_param_construction(self):
        """The validator on AlignSeriesParams catches duplicate output
        names BEFORE the operator sees them — clearer error path."""
        with pytest.raises(ValueError, match="duplicate"):
            AlignSeriesParams(
                join_policy="inner",
                output_keys=["same", "same"],
            )

    def test_empty_output_key_string_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            AlignSeriesParams(
                join_policy="inner",
                output_keys=["signal", ""],
            )
