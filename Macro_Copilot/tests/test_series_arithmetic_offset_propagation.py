"""tests/test_series_arithmetic_offset_propagation.py — PR-C coverage.

Locks the PR-C invariant that ``series_arithmetic`` propagates the
``conditional_aggregate``-emitted ``event_relative_offsets`` /
``offset_anchor`` metadata onto its own lineage step's params when
(and ONLY when) the operands carry consistent encoding.  The
companion check is in
``tests/test_artifact_store_event_offset_encoding.py`` which exercises
the persistence-side detector.

Why this matters
----------------
Pre-PR-C the event-study workflow's terminal ``compare`` Series
(``compare = subtract(conditional_aggregate, unconditional_aggregate)``)
lost the event-relative interpretation because:
  1. ``conditional_aggregate`` synthesises the index as
     ``1970-01-01 + Timedelta(days=offset)`` (a documented synthetic
     anchor).
  2. ``series_arithmetic`` preserved the DatetimeIndex but did NOT
     record the offset metadata on its step.
  3. The artifact-store detector only recognised the metadata when
     the LAST lineage step was ``conditional_aggregate`` — for
     ``compare`` the last step is ``series_arithmetic``, so the
     encoding was dropped from the stored payload.
  4. Build's SeriesWidget then rendered the index as literal
     ``1970-01-NN`` dates instead of ``t-N`` / ``t+N`` labels.

PR-C closes (2) + (3).  This file pins the (2) half — the
operator-side propagation — across the decision matrix.

Decision matrix exercised
-------------------------
  Both operands have the encoding + match     → propagate.
  Both operands have it but anchors differ    → DO NOT propagate.
  Both operands have it but offsets differ    → DO NOT propagate.
  Only one operand has the encoding           → DO NOT propagate.
  Neither operand has the encoding            → DO NOT propagate.
  Unary op (diff / pct_change), left has it   → propagate.
  Scalar op (Series * 3), left has it         → propagate.
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


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


# ===========================================================================
# Fixture helpers
# ===========================================================================
#
# Each helper builds a Series whose LAST lineage step is a synthetic
# ``conditional_aggregate`` step recording the given offsets + anchor.
# This mirrors what the real operator produces in the event-study
# workflow without dragging the conditional_aggregate operator's full
# input wiring into every test.
#
# The DatetimeIndex uses the actual ``1970-01-01 + days`` anchor for
# realism — matches what conditional_aggregate writes on the wire.

_ANCHOR_DEFAULT = "1970-01-01"


def _cond_agg_series(
    series_key: str,
    *,
    offsets: List[int],
    anchor: str = _ANCHOR_DEFAULT,
    values: List[float] | None = None,
    units: TimeSeriesUnits = TimeSeriesUnits.BPS,
) -> Series:
    """Build a Series whose last step is a conditional_aggregate
    OperatorStep recording the given offset metadata."""
    if values is None:
        values = [float(i) for i in range(len(offsets))]
    assert len(values) == len(offsets), "values must align with offsets"

    fetch = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"series_key": series_key},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series",
        version="1.0.0",
        params={"series_key": series_key, "units": units.value},
        input_hashes=(fetch.hash,),
    )
    cond_agg = OperatorStep.build(
        name="conditional_aggregate",
        version="1.0.0",
        params={
            "aggregator": "mean",
            "n_events_in": 100,
            "window_length": len(offsets),
            "offset_anchor": anchor,
            "event_relative_offsets": offsets,
        },
        input_hashes=(adapter.hash,),
    )
    index = pd.DatetimeIndex(
        [pd.Timestamp(anchor) + pd.Timedelta(days=int(o)) for o in offsets]
    )
    payload = pd.Series(values, index=index, dtype=float)
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency=None,
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter, cond_agg]),
    )


def _plain_series(
    series_key: str,
    *,
    dates: List[str],
    values: List[float],
    units: TimeSeriesUnits = TimeSeriesUnits.BPS,
) -> Series:
    """Build a regular calendar Series with NO event-offset metadata
    in its lineage.  The last step is the adapter, not an operator."""
    fetch = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"series_key": series_key},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series",
        version="1.0.0",
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


def _last_step_params(s: Series) -> Dict[str, Any]:
    """Read the params dict of the artifact's last lineage step."""
    return s.lineage.steps[-1].params or {}


# ===========================================================================
# §A — Binary Series ops: propagate when operands agree
# ===========================================================================


class TestBinaryPropagation:
    def test_subtract_matching_metadata_propagates(self):
        """Event-study compare case: cond_agg − unconditional_cond_agg
        where both have identical offsets + anchor."""
        offsets = [-2, -1, 0, 1, 2]
        left = _cond_agg_series(
            "UST_10Y/cond_agg",
            offsets=offsets,
            values=[1.0, 2.0, 3.0, 4.0, 5.0],
        )
        right = _cond_agg_series(
            "UST_10Y/unconditional_agg",
            offsets=offsets,
            values=[0.5, 1.0, 1.5, 2.0, 2.5],
        )
        out = series_arithmetic(
            left=left,
            right=right,
            params=SeriesArithmeticParams(op="subtract"),
        )
        params = _last_step_params(out)
        assert params.get("offset_anchor") == _ANCHOR_DEFAULT
        assert params.get("event_relative_offsets") == offsets

    def test_add_matching_metadata_propagates(self):
        offsets = [0, 1, 2]
        left = _cond_agg_series("a", offsets=offsets)
        right = _cond_agg_series("b", offsets=offsets)
        out = series_arithmetic(
            left=left,
            right=right,
            params=SeriesArithmeticParams(op="add"),
        )
        params = _last_step_params(out)
        assert params["offset_anchor"] == _ANCHOR_DEFAULT
        assert params["event_relative_offsets"] == offsets

    def test_divide_same_unit_matching_metadata_propagates(self):
        # Even when units cancel to ``ratio`` the index semantics stay
        # event-offset.
        offsets = [-1, 0, 1]
        left = _cond_agg_series("a", offsets=offsets)
        right = _cond_agg_series("b", offsets=offsets)
        out = series_arithmetic(
            left=left,
            right=right,
            params=SeriesArithmeticParams(op="divide"),
        )
        params = _last_step_params(out)
        assert params["event_relative_offsets"] == offsets


# ===========================================================================
# §B — Binary Series ops: do NOT propagate on disagreement
# ===========================================================================


class TestBinaryNoPropagationOnMismatch:
    def test_different_offsets_does_not_propagate(self):
        """Both operands have the encoding but the offset lists differ
        → wrong encoding is worse than no encoding."""
        left = _cond_agg_series("a", offsets=[0, 1, 2])
        right_offsets = [3, 4, 5]
        # Right must share the DatetimeIndex for series_arithmetic to
        # accept the op (no align step here), but its lineage records
        # DIFFERENT offsets — simulates a pathological case where the
        # operands somehow got matching indexes but different metadata.
        right = _cond_agg_series("b", offsets=right_offsets)
        # Force the index to match left's so the op proceeds.
        right.payload.index = left.payload.index
        out = series_arithmetic(
            left=left,
            right=right,
            params=SeriesArithmeticParams(op="subtract"),
        )
        params = _last_step_params(out)
        assert "event_relative_offsets" not in params
        assert "offset_anchor" not in params

    def test_different_anchors_does_not_propagate(self):
        offsets = [0, 1, 2]
        left = _cond_agg_series("a", offsets=offsets, anchor="1970-01-01")
        right = _cond_agg_series("b", offsets=offsets, anchor="2000-01-01")
        # Force shared index so the op proceeds.
        right.payload.index = left.payload.index
        out = series_arithmetic(
            left=left,
            right=right,
            params=SeriesArithmeticParams(op="subtract"),
        )
        params = _last_step_params(out)
        assert "event_relative_offsets" not in params
        assert "offset_anchor" not in params

    def test_only_left_has_metadata_does_not_propagate(self):
        offsets = [0, 1, 2]
        left = _cond_agg_series("a", offsets=offsets)
        right = _plain_series(
            "b",
            dates=list(left.payload.index.astype(str)),
            values=[1.0, 2.0, 3.0],
        )
        out = series_arithmetic(
            left=left,
            right=right,
            params=SeriesArithmeticParams(op="subtract"),
        )
        params = _last_step_params(out)
        assert "event_relative_offsets" not in params
        assert "offset_anchor" not in params

    def test_only_right_has_metadata_does_not_propagate(self):
        # Same as above but swapped — confirms the rule is symmetric.
        offsets = [0, 1, 2]
        right = _cond_agg_series("b", offsets=offsets)
        left = _plain_series(
            "a",
            dates=list(right.payload.index.astype(str)),
            values=[1.0, 2.0, 3.0],
        )
        out = series_arithmetic(
            left=left,
            right=right,
            params=SeriesArithmeticParams(op="subtract"),
        )
        params = _last_step_params(out)
        assert "event_relative_offsets" not in params
        assert "offset_anchor" not in params

    def test_neither_has_metadata_does_not_propagate(self):
        # The 99% case — calendar arithmetic with no event-offset
        # context.  PR-C must NOT add the fields here (would change
        # the lineage hash for every existing series_arithmetic call).
        left = _plain_series(
            "a",
            dates=["2024-01-01", "2024-01-02"],
            values=[1.0, 2.0],
        )
        right = _plain_series(
            "b",
            dates=["2024-01-01", "2024-01-02"],
            values=[0.5, 1.0],
        )
        out = series_arithmetic(
            left=left,
            right=right,
            params=SeriesArithmeticParams(op="subtract"),
        )
        params = _last_step_params(out)
        assert "event_relative_offsets" not in params
        assert "offset_anchor" not in params


# ===========================================================================
# §C — Unary + scalar ops: inherit left's metadata
# ===========================================================================


class TestUnaryAndScalarPropagation:
    def test_diff_inherits_left_metadata(self):
        offsets = [-1, 0, 1, 2]
        left = _cond_agg_series("a", offsets=offsets)
        out = series_arithmetic(
            left=left,
            right=None,
            params=SeriesArithmeticParams(op="diff", period=1),
        )
        params = _last_step_params(out)
        assert params["offset_anchor"] == _ANCHOR_DEFAULT
        assert params["event_relative_offsets"] == offsets

    def test_pct_change_inherits_left_metadata(self):
        offsets = [0, 1, 2, 3]
        left = _cond_agg_series("a", offsets=offsets)
        out = series_arithmetic(
            left=left,
            right=None,
            params=SeriesArithmeticParams(op="pct_change", period=1),
        )
        params = _last_step_params(out)
        assert params["event_relative_offsets"] == offsets

    def test_multiply_by_scalar_inherits_left_metadata(self):
        offsets = [0, 1, 2]
        left = _cond_agg_series("a", offsets=offsets)
        out = series_arithmetic(
            left=left,
            right=2.0,
            params=SeriesArithmeticParams(op="multiply"),
        )
        params = _last_step_params(out)
        assert params["offset_anchor"] == _ANCHOR_DEFAULT
        assert params["event_relative_offsets"] == offsets

    def test_divide_by_scalar_inherits_left_metadata(self):
        offsets = [0, 1, 2]
        left = _cond_agg_series("a", offsets=offsets)
        out = series_arithmetic(
            left=left,
            right=2.0,
            params=SeriesArithmeticParams(op="divide"),
        )
        params = _last_step_params(out)
        assert params["event_relative_offsets"] == offsets

    def test_unary_on_plain_series_does_not_record_metadata(self):
        # Unary op on a non-event-offset Series stays unaffected.
        left = _plain_series(
            "a",
            dates=["2024-01-01", "2024-01-02", "2024-01-03"],
            values=[1.0, 2.0, 3.0],
        )
        out = series_arithmetic(
            left=left,
            right=None,
            params=SeriesArithmeticParams(op="diff", period=1),
        )
        params = _last_step_params(out)
        assert "event_relative_offsets" not in params
        assert "offset_anchor" not in params


# ===========================================================================
# §D — Chained propagation: cond_agg → series_arithmetic → series_arithmetic
# ===========================================================================


class TestChainedPropagation:
    """Confirms PR-C's design: each series_arithmetic step reads
    metadata off the IMMEDIATE previous step.  Because the inner
    series_arithmetic propagated, the outer one finds the metadata
    on its operand's last step — no need for a recursive walk past
    arbitrary operators."""

    def test_two_step_chain_preserves_metadata(self):
        offsets = [-1, 0, 1]
        a = _cond_agg_series("a", offsets=offsets)
        b = _cond_agg_series("b", offsets=offsets)
        # First op: a - b → carries the metadata via PR-C propagation.
        intermediate = series_arithmetic(
            left=a,
            right=b,
            params=SeriesArithmeticParams(op="subtract"),
        )
        assert intermediate.lineage.steps[-1].name == "series_arithmetic"
        assert (
            intermediate.lineage.steps[-1].params["event_relative_offsets"]
            == offsets
        )

        # Second op: (a - b) * 3 → unary-by-scalar, inherits.
        final = series_arithmetic(
            left=intermediate,
            right=3.0,
            params=SeriesArithmeticParams(op="multiply"),
        )
        params = _last_step_params(final)
        assert params["offset_anchor"] == _ANCHOR_DEFAULT
        assert params["event_relative_offsets"] == offsets

    def test_chained_binary_preserves_when_matching(self):
        # Two cond_agg sources → first compare → another binary op
        # against a third cond_agg source.  All carry matching
        # offsets → outer op should still propagate.
        offsets = [0, 1, 2, 3]
        a = _cond_agg_series("a", offsets=offsets)
        b = _cond_agg_series("b", offsets=offsets)
        c = _cond_agg_series("c", offsets=offsets)
        intermediate = series_arithmetic(
            left=a, right=b, params=SeriesArithmeticParams(op="subtract"),
        )
        # Now intermediate's last step is series_arithmetic with the
        # metadata.  Combine with c (cond_agg).  Both should agree.
        # Force index alignment defensively.
        c.payload.index = intermediate.payload.index
        final = series_arithmetic(
            left=intermediate,
            right=c,
            params=SeriesArithmeticParams(op="add"),
        )
        params = _last_step_params(final)
        assert params["event_relative_offsets"] == offsets
