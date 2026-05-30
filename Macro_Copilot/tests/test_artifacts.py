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
    PrimitiveStep,
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
        with pytest.raises(ValueError, match="duplicate"):
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
# PrimitiveStep — Phase 1B bridge prerequisite
# ===========================================================================


def _example_primitive_step(**overrides) -> PrimitiveStep:
    """Build a PrimitiveStep matching the canonical OIS curve_spread
    invocation shape, with overrides for individual tests."""
    defaults = dict(
        name="calculate_ois_curve_spread_tool",
        params={
            "curve_family": "USD_SOFR_OIS",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
        tool_config_hash="conv_v1_abc123",
        output_field="time_series_spread",
        as_of_date="2026-04-30",
    )
    defaults.update(overrides)
    return PrimitiveStep.build(**defaults)


class TestPrimitiveStep:
    """Phase 1B prerequisite: extend the closed lineage family with a
    primitive/tool step kind so the primitive→operator adapter
    (``shared.artifacts.adapters.from_time_series``, next PR) can
    construct typed ``Series`` artifacts whose lineage chain
    starts with a ``PrimitiveStep`` rather than a fabricated
    ``AdapterStep``.

    The schema decisions under test:

      - ``kind`` is the discriminator value 'primitive' (closed-family)
      - ``name`` is plain ``str`` (primitives are an extensible family)
      - hash is deterministic over the four identity bits
        (params / tool_config_hash / output_field / as_of_date)
      - ``tool_config_path`` is bookkeeping only (NOT in hash)
      - ``input_hashes`` defaults to ``()`` (primitives have no
        artifact inputs in v1) but the slot is reserved
      - JSON round-trip preserves the type via the ``kind``
        discriminator
      - ``Lineage.append`` works on a PrimitiveStep-rooted chain
        AND on a chain that mixes existing step kinds with a
        PrimitiveStep
    """

    # ----- kind discriminator + body shape ---------------------------------

    def test_kind_is_primitive(self):
        step = _example_primitive_step()
        assert step.kind == "primitive"

    def test_default_version_matches_other_steps(self):
        step = _example_primitive_step()
        # Same default as Fetch / Clean / Adapter / Operator.
        assert step.version == "1.0.0"

    def test_input_hashes_default_is_empty(self):
        """v1: primitives fetch from the DB themselves; no artifact
        inputs.  The slot is reserved for Phase 2 primitives that
        ever take an artifact input."""
        step = _example_primitive_step()
        assert step.input_hashes == ()

    def test_tool_config_path_default_is_none(self):
        """``tool_config_path`` is bookkeeping for human debugging;
        NOT in the hash.  Default None is fine."""
        step = _example_primitive_step()
        assert step.tool_config_path is None

    def test_explicit_tool_config_path_round_trips(self):
        step = _example_primitive_step(
            tool_config_path="rates_agent/ois/tools/curve_spread/config.yaml",
        )
        assert step.tool_config_path == (
            "rates_agent/ois/tools/curve_spread/config.yaml"
        )

    def test_frozen_no_extra_fields(self):
        """Same closed-family discipline as the other step kinds:
        frozen + extra='forbid'."""
        step = _example_primitive_step()
        with pytest.raises(Exception):
            # Pydantic's frozen=True raises ValidationError on assignment.
            step.name = "different_tool"  # type: ignore[misc]

    # ----- hash determinism -------------------------------------------------

    def test_hash_is_deterministic(self):
        a = _example_primitive_step()
        b = _example_primitive_step()
        assert a.hash == b.hash

    def test_hash_invariant_to_params_dict_order(self):
        a = _example_primitive_step(params={
            "curve_family": "USD_SOFR_OIS",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        })
        b = _example_primitive_step(params={
            "field_name": None,
            "lookback_days": 365,
            "long_tenor": "10Y",
            "short_tenor": "2Y",
            "curve_family": "USD_SOFR_OIS",
        })
        assert a.hash == b.hash

    def test_hash_changes_on_params_change(self):
        """Different ``*Input`` produces a different hash."""
        a = _example_primitive_step(params={"curve_family": "USD_SOFR_OIS",
                                            "short_tenor": "2Y",
                                            "long_tenor": "10Y"})
        b = _example_primitive_step(params={"curve_family": "USD_SOFR_OIS",
                                            "short_tenor": "5Y",
                                            "long_tenor": "10Y"})
        assert a.hash != b.hash

    def test_hash_changes_on_tool_config_hash_change(self):
        """Same ``*Input``, different YAML content (e.g. someone bumped
        ``z_score_window_days``) MUST produce different step hashes.
        This is the load-bearing identity bit that lets a future cache
        invalidate stale results when the methodology config changes."""
        a = _example_primitive_step(tool_config_hash="conv_v1_abc")
        b = _example_primitive_step(tool_config_hash="conv_v1_xyz")
        assert a.hash != b.hash

    def test_hash_changes_on_output_field_change(self):
        """Different ``time_series*`` extracted = different artifact =
        different hash.  Otherwise a downstream cache would conflate
        ``time_series_spread`` and ``time_series_zscore``, which are
        completely different series."""
        a = _example_primitive_step(output_field="time_series_spread")
        b = _example_primitive_step(output_field="time_series_zscore")
        assert a.hash != b.hash

    def test_hash_changes_on_as_of_date_change(self):
        """Replay-determinism: a re-run tomorrow with the same params
        has a different ``as_of_date`` (DB has a new latest), and the
        hash MUST reflect that the underlying data is different."""
        a = _example_primitive_step(as_of_date="2026-04-30")
        b = _example_primitive_step(as_of_date="2026-05-01")
        assert a.hash != b.hash

    def test_hash_invariant_to_tool_config_path(self):
        """``tool_config_path`` is bookkeeping only.  Two callers that
        load the same YAML from different paths (test fixture vs prod
        bundled) MUST produce the same hash if the content is
        identical."""
        a = _example_primitive_step(
            tool_config_path="rates_agent/ois/tools/curve_spread/config.yaml",
        )
        b = _example_primitive_step(
            tool_config_path="/abs/test/fixture/curve_spread.yaml",
        )
        assert a.hash == b.hash

    def test_hash_changes_on_name_change(self):
        a = _example_primitive_step(name="calculate_ois_curve_spread_tool")
        b = _example_primitive_step(name="get_ois_rate_level_tool")
        assert a.hash != b.hash

    def test_hash_changes_on_version_change(self):
        a = _example_primitive_step()
        b = _example_primitive_step()
        # Build a v2 with explicit version override.
        b2 = PrimitiveStep.build(
            name=b.name,
            version="2.0.0",
            params=b.params,
            tool_config_hash=b.tool_config_hash,
            output_field=b.output_field,
            as_of_date=b.as_of_date,
        )
        assert a.hash != b2.hash

    # ----- JSON round-trip via the discriminated union ----------------------

    def test_json_round_trip_via_step_directly(self):
        original = _example_primitive_step(
            tool_config_path="rates_agent/ois/tools/curve_spread/config.yaml",
        )
        as_dict = original.model_dump(mode="json")
        recovered = PrimitiveStep.model_validate(as_dict)
        assert recovered == original
        assert recovered.hash == original.hash
        assert recovered.tool_config_path == original.tool_config_path

    def test_json_round_trip_through_lineage_discriminator(self):
        """Pydantic must resolve the right concrete class on
        deserialization based on ``kind``.  This is what makes
        ``Lineage`` JSON-portable across step kinds."""
        step = _example_primitive_step()
        ln = Lineage.from_steps([step])
        as_dict = ln.model_dump(mode="json")

        # Discriminator visible on the wire.
        assert as_dict["steps"][0]["kind"] == "primitive"

        recovered = Lineage.model_validate(as_dict)
        assert isinstance(recovered.steps[0], PrimitiveStep)
        assert recovered.steps[0].hash == step.hash
        assert recovered.head_hash == ln.head_hash

    # ----- Lineage chain composition ----------------------------------------

    def test_primitive_can_be_root_of_lineage_chain(self):
        """Phase 1B narrative: artifact lineage starts with a
        PrimitiveStep when the artifact came from a primitive's
        canonical ``TimeSeries``, NOT a fabricated AdapterStep."""
        prim = _example_primitive_step()
        ln = Lineage.from_steps([prim])
        assert ln.head_hash == prim.hash
        assert len(ln.steps) == 1

    def test_operator_step_appended_after_primitive(self):
        """The bridge milestone end-state: primitive → operator
        composition produces a 2-step chain
        ``(PrimitiveStep, OperatorStep)``."""
        prim = _example_primitive_step()
        ln = Lineage.from_steps([prim])
        op = OperatorStep.build(
            name="align_series", version="1.0.0",
            params={"join_policy": "outer"},
            input_hashes=(prim.hash,),
        )
        ln2 = ln.append(op)
        assert len(ln2.steps) == 2
        assert ln2.steps[0].kind == "primitive"
        assert ln2.steps[1].kind == "operator"
        assert ln2.head_hash == op.hash

    def test_primitive_step_appended_after_other_kinds(self):
        """Even though primitives are the typical chain ROOT, the
        type system must permit a PrimitiveStep anywhere in the chain
        (e.g. a future composite primitive that takes an artifact
        input).  Pin the contract by appending one to a Fetch+Adapter
        chain."""
        ln = _trivial_lineage("ust_10y")
        prim = _example_primitive_step(input_hashes=(ln.head_hash,))
        ln2 = ln.append(prim)
        assert len(ln2.steps) == 3
        assert [s.kind for s in ln2.steps] == ["fetch", "adapter", "primitive"]
        assert ln2.head_hash == prim.hash

    # ----- discriminator union resolution -----------------------------------

    def test_lineage_step_union_resolves_primitive_kind(self):
        """An untyped dict with ``kind='primitive'`` must validate as
        a PrimitiveStep through the LineageStep discriminated union
        (this is what `Lineage.steps` uses on JSON load)."""
        # Build a step, dump it, load it back through the union.
        original = _example_primitive_step()
        as_dict = original.model_dump(mode="json")
        # Embed in a single-step Lineage, since Pydantic's
        # discriminator activation is on the union field.
        ln = Lineage.model_validate({
            "steps": [as_dict],
            "head_hash": as_dict["hash"],
        })
        assert isinstance(ln.steps[0], PrimitiveStep)
        assert ln.steps[0].name == original.name


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
        with pytest.raises(ValueError, match="must equal the mask"):
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


# ===========================================================================
# Shared artifact validators (ART11 / ART13) — the strict construction-time
# net added in Phase 1+2 step 3 (one shared index/finiteness/dtype validator
# across every indexed/numeric artifact + EventSet semantic invariants +
# WindowedPanel offset checks).  Negative tests so a future refactor cannot
# silently drop any invariant.
# ===========================================================================


class TestSharedArtifactValidators:
    @staticmethod
    def _lin() -> Lineage:
        return Lineage.from_steps(
            [OperatorStep.build(name="x", version="1", params={}, input_hashes=())]
        )

    # --- finiteness: +/-Inf forbidden, NaN allowed ----------------------

    def test_series_rejects_inf(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        with pytest.raises(ValueError, match="Inf"):
            Series(
                series_key="x",
                payload=pd.Series([1.0, np.inf], index=idx, dtype=float),
                units=TimeSeriesUnits.PERCENT,
                frequency=None,
                missingness_policy=RawNoCleaning(),
                lineage=_trivial_lineage(),
            )

    def test_series_allows_nan(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        s = Series(
            series_key="x",
            payload=pd.Series([1.0, np.nan], index=idx, dtype=float),
            units=TimeSeriesUnits.PERCENT,
            frequency=None,
            missingness_policy=RawNoCleaning(),
            lineage=_trivial_lineage(),
        )
        assert len(s) == 2

    def test_panel_rejects_inf_column(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        with pytest.raises(ValueError, match="Inf"):
            Panel(
                payload=pd.DataFrame({"a": [1.0, np.inf]}, index=idx),
                units_by_column={"a": TimeSeriesUnits.PERCENT},
                missingness_policy=RawNoCleaning(),
                lineage=self._lin(),
            )

    def test_windowed_panel_rejects_inf(self):
        with pytest.raises(ValueError, match="Inf"):
            WindowedPanel(
                payload=np.array([[0.0, np.inf, 2.0]]),
                offsets=[0, 1, 2],
                event_dates=[pd.Timestamp("2026-01-05")],
                per_event_metadata=[{}],
                target_series_key="ust_10y",
                units=TimeSeriesUnits.BPS,
                lineage=self._lin(),
            )

    def test_windowed_panel_allows_nan(self):
        wp = WindowedPanel(
            payload=np.array([[np.nan, 1.0, 2.0]]),
            offsets=[0, 1, 2],
            event_dates=[pd.Timestamp("2026-01-05")],
            per_event_metadata=[{}],
            target_series_key="ust_10y",
            units=TimeSeriesUnits.BPS,
            lineage=self._lin(),
        )
        assert wp.n_events == 1

    # --- index: duplicates / unsorted (shared validator) ----------------

    def test_panel_rejects_unsorted_index(self):
        idx = pd.DatetimeIndex(["2026-01-05", "2026-01-02"])  # descending
        with pytest.raises(ValueError, match="sorted ascending"):
            Panel(
                payload=pd.DataFrame({"a": [1.0, 2.0]}, index=idx),
                units_by_column={"a": TimeSeriesUnits.PERCENT},
                missingness_policy=RawNoCleaning(),
                lineage=self._lin(),
            )

    def test_eventset_rejects_duplicate_index(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-02"])
        with pytest.raises(ValueError, match="duplicate"):
            EventSet(
                mask=pd.Series([True, False], index=idx),
                event_dates=[pd.Timestamp("2026-01-02")],
                per_event_metadata=[{}],
                source_series_key="s",
                lineage=self._lin(),
            )

    # --- Panel non-numeric column ---------------------------------------

    def test_panel_rejects_non_numeric_column(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        with pytest.raises(ValueError, match="numeric"):
            Panel(
                payload=pd.DataFrame({"a": ["x", "y"]}, index=idx),
                units_by_column={"a": TimeSeriesUnits.PERCENT},
                missingness_policy=RawNoCleaning(),
                lineage=self._lin(),
            )

    # --- EventSet semantic invariants -----------------------------------

    def test_eventset_rejects_out_of_order_event_dates(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05", "2026-01-06"])
        with pytest.raises(ValueError, match="must equal the mask"):
            EventSet(
                mask=pd.Series([True, False, True], index=idx),
                event_dates=[pd.Timestamp("2026-01-06"), pd.Timestamp("2026-01-02")],
                per_event_metadata=[{}, {}],
                source_series_key="s",
                lineage=self._lin(),
            )

    def test_eventset_rejects_event_date_not_a_true_position(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05"])
        with pytest.raises(ValueError, match="must equal the mask"):
            EventSet(
                mask=pd.Series([True, False], index=idx),
                event_dates=[pd.Timestamp("2026-01-05")],  # the False date
                per_event_metadata=[{}],
                source_series_key="s",
                lineage=self._lin(),
            )

    def test_eventset_accepts_consistent(self):
        idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05", "2026-01-06"])
        es = EventSet(
            mask=pd.Series([True, False, True], index=idx),
            event_dates=[pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-06")],
            per_event_metadata=[{}, {}],
            source_series_key="s",
            lineage=self._lin(),
        )
        assert es.n_events == 2

    # --- WindowedPanel offsets strictly increasing + unique -------------

    def test_windowed_panel_rejects_unsorted_offsets(self):
        with pytest.raises(ValueError, match="strictly increasing"):
            WindowedPanel(
                payload=np.array([[0.0, 1.0, 2.0]]),
                offsets=[2, 0, 1],
                event_dates=[pd.Timestamp("2026-01-05")],
                per_event_metadata=[{}],
                target_series_key="ust_10y",
                units=TimeSeriesUnits.BPS,
                lineage=self._lin(),
            )

    def test_windowed_panel_rejects_duplicate_offsets(self):
        with pytest.raises(ValueError, match="strictly increasing"):
            WindowedPanel(
                payload=np.array([[0.0, 1.0, 2.0]]),
                offsets=[0, 1, 1],
                event_dates=[pd.Timestamp("2026-01-05")],
                per_event_metadata=[{}],
                target_series_key="ust_10y",
                units=TimeSeriesUnits.BPS,
                lineage=self._lin(),
            )
