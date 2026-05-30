"""Phase 1+2 step 5 — operator error + determinism guards.

Locks the ERR-2 / ERR-8 typed guards and the F-DET-1 / F-DET-2
lineage-determinism fixes added in step 5, so a future refactor cannot
silently drop them.
"""

from __future__ import annotations

import pandas as pd
import pytest

from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.operators.align_series import align_series, AlignSeriesParams
from shared.operators.apply_mask import apply_mask
from shared.operators.apply_mask.operator import ApplyMaskError
from shared.operators.rolling_regression import (
    rolling_regression,
    RollingRegressionError,
    RollingRegressionParams,
)
from shared.operators.series_arithmetic import (
    series_arithmetic,
    SeriesArithmeticParams,
)
from shared.operators.series_arithmetic.operator import SeriesArithmeticError
from shared.operators.summarize_series import summarize_series
from shared.operators.summarize_series.operator import SummarizeSeriesError


def _lin(key: str = "x") -> Lineage:
    step = PrimitiveStep.build(
        name="synthetic", version="1.0.0", params={"series_key": key},
        tool_config_hash="h", output_field="time_series",
        as_of_date="2025-01-01",
    )
    return Lineage.from_steps([step])


def _series(key, values, start="2025-01-01", units=TimeSeriesUnits.PERCENT) -> Series:
    idx = pd.bdate_range(start, periods=len(values))
    return Series(
        series_key=key, payload=pd.Series(values, index=idx, dtype=float),
        units=units, frequency="B", missingness_policy=RawNoCleaning(),
        lineage=_lin(key),
    )


# --- ERR-8: typed input guards (no raw AttributeError on wrong types) ----


def test_summarize_series_non_series_input_raises_typed():
    with pytest.raises(SummarizeSeriesError, match="Series artifact"):
        summarize_series("not a series")


def test_apply_mask_non_series_input_raises_typed():
    with pytest.raises(ApplyMaskError, match="Series artifact"):
        apply_mask("not a series", "not a mask")


def test_rolling_regression_non_series_input_raises_typed():
    with pytest.raises(RollingRegressionError, match="Series artifact"):
        rolling_regression(
            "not a series", "also not",
            params=RollingRegressionParams(window=30),
        )


# --- ERR-2: series_arithmetic never emits ±Inf into a payload -----------


def test_series_arithmetic_pct_change_div_by_zero_raises_typed():
    # A 0 predecessor makes pct_change emit +inf; the operator must refuse
    # with its own typed error rather than leak a bare artifact
    # ValidationError (OPR14b / OPR13).
    s = _series("a", [0.0, 1.0, 2.0, 3.0])
    with pytest.raises(SeriesArithmeticError, match=r"non-finite|inf"):
        series_arithmetic(
            s, "pct_change", None,
            params=SeriesArithmeticParams(op="pct_change"),
        )


# --- F-DET-1: binary series_arithmetic nulls the meaningless `period` ----


def test_series_arithmetic_binary_period_is_null_in_lineage():
    a = _series("a", [1.0, 2.0, 3.0])
    b = _series("b", [4.0, 5.0, 6.0])
    out = series_arithmetic(
        a, "add", b, params=SeriesArithmeticParams(op="add", period=3),
    )
    head = out.lineage.steps[-1]
    assert head.params["period"] is None  # nulled for binary ops


def test_series_arithmetic_unary_period_preserved():
    a = _series("a", [1.0, 2.0, 3.0, 4.0])
    out = series_arithmetic(
        a, "diff", None, params=SeriesArithmeticParams(op="diff", period=2),
    )
    head = out.lineage.steps[-1]
    assert head.params["period"] == 2


# --- F-DET-2: align_series nulls the ignored `fill_limit` under raw ------


def test_align_series_raw_fill_limit_is_null_in_lineage():
    a = _series("a", [1.0, 2.0, 3.0])
    out = align_series(
        [a],
        params=AlignSeriesParams(
            join_policy="outer", fill_policy="raw", fill_limit=7,
            require_matching_frequency=False,
            require_matching_missingness=False,
        ),
    )
    head = out.lineage.steps[-1]
    assert head.params["fill_limit"] is None  # nulled under raw


def test_align_series_ffill_fill_limit_preserved():
    a = _series("a", [1.0, 2.0, 3.0])
    out = align_series(
        [a],
        params=AlignSeriesParams(
            join_policy="outer", fill_policy="ffill", fill_limit=7,
            require_matching_frequency=False,
            require_matching_missingness=False,
        ),
    )
    head = out.lineage.steps[-1]
    assert head.params["fill_limit"] == 7
