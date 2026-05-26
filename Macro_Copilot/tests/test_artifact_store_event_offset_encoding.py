"""tests/test_artifact_store_event_offset_encoding.py — PR-C detector coverage.

Locks the artifact-store-side detector that promotes the
``index_encoding`` blob onto the stored payload when the producing
operator recorded event-relative offsets.

PR-C extends the detector's allowed-operator list from just
``{conditional_aggregate}`` to ``{conditional_aggregate,
series_arithmetic}``.  The companion check is in
``tests/test_series_arithmetic_offset_propagation.py`` which exercises
the operator-side write.  Here we confirm the WRITE → PERSIST →
PROMOTION round-trip end to end.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import pytest

from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    FetchStep,
    Lineage,
    Series,
    TimeSeriesUnits,
)
from shared.artifacts.lineage import OperatorStep
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.series_arithmetic import (
    SeriesArithmeticParams,
    series_arithmetic,
)
from state.artifact_store import (
    _detect_event_offset_encoding,
    _series_to_stored,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


# ---------------------------------------------------------------------------
# Fixture helpers — mirror the structure used in
# test_series_arithmetic_offset_propagation.py.
# ---------------------------------------------------------------------------

_ANCHOR = "1970-01-01"


def _cond_agg_series(
    series_key: str, *, offsets: List[int]
) -> Series:
    fetch = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"series_key": series_key},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series",
        version="1.0.0",
        params={"series_key": series_key, "units": "bps"},
        input_hashes=(fetch.hash,),
    )
    cond_agg = OperatorStep.build(
        name="conditional_aggregate",
        version="1.0.0",
        params={
            "aggregator": "mean",
            "n_events_in": 50,
            "window_length": len(offsets),
            "offset_anchor": _ANCHOR,
            "event_relative_offsets": offsets,
        },
        input_hashes=(adapter.hash,),
    )
    index = pd.DatetimeIndex(
        [pd.Timestamp(_ANCHOR) + pd.Timedelta(days=int(o)) for o in offsets]
    )
    payload = pd.Series([float(i) for i in range(len(offsets))], index=index)
    return Series(
        series_key=series_key,
        payload=payload,
        units=TimeSeriesUnits.BPS,
        frequency=None,
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter, cond_agg]),
    )


def _plain_calendar_series(series_key: str) -> Series:
    fetch = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"series_key": series_key},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series",
        version="1.0.0",
        params={"series_key": series_key, "units": "bps"},
        input_hashes=(fetch.hash,),
    )
    payload = pd.Series(
        [1.0, 2.0, 3.0],
        index=pd.DatetimeIndex(["2024-01-01", "2024-01-02", "2024-01-03"]),
    )
    return Series(
        series_key=series_key,
        payload=payload,
        units=TimeSeriesUnits.BPS,
        frequency=None,
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


# ---------------------------------------------------------------------------
# §A — Backward compatibility: conditional_aggregate detection still works.
# ---------------------------------------------------------------------------


class TestDetectorBackwardCompat:
    """Pre-PR-C the detector recognised ONLY conditional_aggregate.
    PR-C must NOT break that path."""

    def test_conditional_aggregate_last_step_is_recognised(self):
        s = _cond_agg_series("UST_10Y/cond_agg", offsets=[-1, 0, 1, 2])
        encoding = _detect_event_offset_encoding(s)
        assert encoding is not None
        assert encoding["kind"] == "event_offset"
        assert encoding["anchor"] == _ANCHOR
        assert encoding["offsets"] == [-1, 0, 1, 2]

    def test_conditional_aggregate_round_trip_via_series_to_stored(self):
        s = _cond_agg_series("UST_10Y/cond_agg", offsets=[0, 1, 2])
        _, payload = _series_to_stored(s)
        assert payload.get("index_encoding") == {
            "kind": "event_offset",
            "anchor": _ANCHOR,
            "offsets": [0, 1, 2],
        }


# ---------------------------------------------------------------------------
# §B — PR-C: series_arithmetic last step now also recognised.
# ---------------------------------------------------------------------------


class TestDetectorRecognisesSeriesArithmetic:
    """The event-study compare case: cond_agg − cond_agg →
    series_arithmetic with offset metadata in its step params."""

    def test_event_study_compare_pattern_promotes_encoding(self):
        offsets = [-2, -1, 0, 1, 2]
        cond = _cond_agg_series("UST_10Y/cond_agg", offsets=offsets)
        uncond = _cond_agg_series("UST_10Y/uncond_agg", offsets=offsets)
        compare = series_arithmetic(
            left=cond,
            right=uncond,
            params=SeriesArithmeticParams(op="subtract"),
        )
        # Sanity — operator-side propagation worked.
        assert compare.lineage.steps[-1].name == "series_arithmetic"
        assert (
            compare.lineage.steps[-1].params.get("event_relative_offsets")
            == offsets
        )

        # Detector-side promotion: the encoding shows up on the stored
        # payload that the UI's SeriesWidget reads via the artifact-
        # payload endpoint.
        encoding = _detect_event_offset_encoding(compare)
        assert encoding == {
            "kind": "event_offset",
            "anchor": _ANCHOR,
            "offsets": offsets,
        }

    def test_compare_round_trip_via_series_to_stored(self):
        offsets = [0, 1, 2, 3, 4, 5]
        cond = _cond_agg_series("UST_10Y/cond_agg", offsets=offsets)
        uncond = _cond_agg_series("UST_10Y/uncond_agg", offsets=offsets)
        compare = series_arithmetic(
            left=cond,
            right=uncond,
            params=SeriesArithmeticParams(op="subtract"),
        )
        _, payload = _series_to_stored(compare)
        assert payload.get("index_encoding") == {
            "kind": "event_offset",
            "anchor": _ANCHOR,
            "offsets": offsets,
        }

    def test_unary_diff_on_cond_agg_promotes_encoding(self):
        # Less common but should still work end-to-end.
        offsets = [0, 1, 2, 3]
        cond = _cond_agg_series("UST_10Y/cond_agg", offsets=offsets)
        diffed = series_arithmetic(
            left=cond,
            right=None,
            params=SeriesArithmeticParams(op="diff", period=1),
        )
        _, payload = _series_to_stored(diffed)
        assert payload.get("index_encoding") == {
            "kind": "event_offset",
            "anchor": _ANCHOR,
            "offsets": offsets,
        }


# ---------------------------------------------------------------------------
# §C — Negative paths: ensure the detector does NOT over-promote.
# ---------------------------------------------------------------------------


class TestDetectorDoesNotOverPromote:
    def test_plain_calendar_series_yields_no_encoding(self):
        # No operator step at all → never produces an encoding.
        s = _plain_calendar_series("UST_10Y/yield")
        assert _detect_event_offset_encoding(s) is None
        _, payload = _series_to_stored(s)
        assert "index_encoding" not in payload

    def test_plain_series_arithmetic_yields_no_encoding(self):
        # series_arithmetic between two calendar Series — neither
        # operand has the metadata, so the operator does NOT write
        # the fields → detector finds nothing → no encoding promoted.
        a = _plain_calendar_series("a")
        b = _plain_calendar_series("b")
        out = series_arithmetic(
            left=a, right=b, params=SeriesArithmeticParams(op="subtract"),
        )
        # Sanity — operator-side write did NOT add the fields (PR-C
        # preserves the existing hash for calendar arithmetic).
        params = out.lineage.steps[-1].params
        assert "event_relative_offsets" not in params
        assert "offset_anchor" not in params
        # Detector returns None → no encoding on the stored payload.
        assert _detect_event_offset_encoding(out) is None
        _, payload = _series_to_stored(out)
        assert "index_encoding" not in payload

    def test_mismatched_operand_metadata_does_not_promote(self):
        # Both operands have the encoding but the offsets disagree —
        # operator-side rule refuses to propagate → detector finds
        # nothing.
        left = _cond_agg_series("a", offsets=[0, 1, 2])
        right = _cond_agg_series("b", offsets=[3, 4, 5])
        # Force index alignment so the op proceeds.
        right.payload.index = left.payload.index
        out = series_arithmetic(
            left=left, right=right, params=SeriesArithmeticParams(op="subtract"),
        )
        assert _detect_event_offset_encoding(out) is None
        _, payload = _series_to_stored(out)
        assert "index_encoding" not in payload


# ---------------------------------------------------------------------------
# §D — Detector defensive shape checks.
# ---------------------------------------------------------------------------


class TestDetectorShapeChecks:
    """The detector must return None on every malformed shape rather
    than producing a half-built encoding that could mislabel the UI."""

    def test_malformed_anchor_returns_none(self):
        # Build a Series whose last step claims to be cond_agg but
        # has a non-string offset_anchor.
        s = _cond_agg_series("a", offsets=[0, 1])
        # Mutate the step's params in a fresh model copy (steps are
        # frozen Pydantic models).
        bad_step = s.lineage.steps[-1].model_copy(
            update={
                "params": {
                    **(s.lineage.steps[-1].params or {}),
                    "offset_anchor": None,  # bad type
                }
            }
        )
        s2 = Series(
            series_key=s.series_key,
            payload=s.payload,
            units=s.units,
            frequency=s.frequency,
            missingness_policy=s.missingness_policy,
            lineage=Lineage.from_steps(
                list(s.lineage.steps[:-1]) + [bad_step]
            ),
        )
        assert _detect_event_offset_encoding(s2) is None

    def test_malformed_offsets_returns_none(self):
        s = _cond_agg_series("a", offsets=[0, 1])
        bad_step = s.lineage.steps[-1].model_copy(
            update={
                "params": {
                    **(s.lineage.steps[-1].params or {}),
                    "event_relative_offsets": "not-a-list",
                }
            }
        )
        s2 = Series(
            series_key=s.series_key,
            payload=s.payload,
            units=s.units,
            frequency=s.frequency,
            missingness_policy=s.missingness_policy,
            lineage=Lineage.from_steps(
                list(s.lineage.steps[:-1]) + [bad_step]
            ),
        )
        assert _detect_event_offset_encoding(s2) is None

    def test_unrelated_operator_name_returns_none(self):
        # An operator named something other than conditional_aggregate
        # / series_arithmetic must NOT trigger promotion even if its
        # params happen to carry the fields.
        s = _plain_calendar_series("a")
        rogue_step = OperatorStep.build(
            name="some_other_operator",
            version="1.0.0",
            params={
                "offset_anchor": _ANCHOR,
                "event_relative_offsets": [0, 1, 2],
            },
            input_hashes=(s.lineage.head_hash,),
        )
        s2 = Series(
            series_key=s.series_key,
            payload=s.payload,
            units=s.units,
            frequency=s.frequency,
            missingness_policy=s.missingness_policy,
            lineage=s.lineage.append(rogue_step),
        )
        assert _detect_event_offset_encoding(s2) is None
