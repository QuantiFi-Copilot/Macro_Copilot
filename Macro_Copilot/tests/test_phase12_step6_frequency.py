"""Phase 1+2 step 6 — bridge frequency derivation (ADR 0016 Decision 3).

Locks the coarse-frequency derivation that makes every operator's
frequency-match check load-bearing instead of a None==None no-op.
"""

from __future__ import annotations

import pandas as pd

from shared.artifacts.adapters.from_time_series import (
    _coarse_frequency,
    _infer_frequency,
    _is_business_daily_with_holiday_gaps,
)


def _bdays_without(start: str, end: str, drop: list[str]) -> pd.DatetimeIndex:
    """Business-day index over [start, end] minus the given dates —
    the canonical 'business-daily with holiday gaps' fixture."""
    return pd.bdate_range(start, end).difference(pd.DatetimeIndex(drop))


# Roughly the 2025 US holiday calendar (all fall on business days) plus
# a UK-style Boxing Day — ~11 gaps over ~260 steps ≈ 4% gapped.
_HOLIDAYS_2025 = [
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25", "2025-12-26",
]


# --- _infer_frequency: index → coarse tag -------------------------------


def test_business_day_index_infers_B():
    assert _infer_frequency(pd.bdate_range("2025-01-01", periods=12)) == "B"


def test_weekly_index_infers_W():
    idx = pd.date_range("2025-01-05", periods=10, freq="W")
    assert _infer_frequency(idx) == "W"


def test_irregular_index_infers_irregular():
    idx = pd.DatetimeIndex(
        ["2025-01-01", "2025-01-03", "2025-01-09", "2025-02-15"]
    )
    assert _infer_frequency(idx) == "irregular"


def test_too_short_index_is_none():
    # <3 points: inference is unreliable, so the tag stays None.
    assert _infer_frequency(pd.DatetimeIndex(["2025-01-01", "2025-01-02"])) is None


def test_non_datetime_index_is_none():
    assert _infer_frequency(pd.Index([1, 2, 3])) is None


# --- FM-10: holiday-gapped business-day indexes infer 'B' ----------------
#
# Derived-series bridges (curve spreads, breakevens, OIS bridges) produce
# business-daily indexes with holiday gaps; pd.infer_freq returns None on
# those, which used to stamp 'irregular' and made align_series refuse the
# mix against a gap-free 'B' fetch (campaign e04/f05/j04/s11).


def test_holiday_gapped_business_year_infers_B():
    idx = _bdays_without("2025-01-01", "2025-12-31", _HOLIDAYS_2025)
    assert pd.infer_freq(idx) is None  # precondition: pandas can't tag it
    assert _infer_frequency(idx) == "B"


def test_single_one_day_holiday_gap_infers_B():
    # Short window straddling one holiday (gap count <= floor of 2).
    idx = _bdays_without("2025-06-09", "2025-06-27", ["2025-06-19"])
    assert _infer_frequency(idx) == "B"


def test_three_business_day_gap_infers_B():
    # Christmas-style cluster: 3 consecutive business days missing
    # (Wed 24 + Thu 25 + Fri 26) is still a tolerable holiday gap.
    idx = _bdays_without(
        "2025-11-03", "2025-12-31",
        ["2025-12-24", "2025-12-25", "2025-12-26"],
    )
    assert _infer_frequency(idx) == "B"


def test_gap_wider_than_three_business_days_is_irregular():
    # A full missing week (5 consecutive business days) is a data hole,
    # not a holiday — must NOT be tagged 'B'.
    idx = _bdays_without(
        "2025-01-01", "2025-06-30",
        ["2025-03-10", "2025-03-11", "2025-03-12", "2025-03-13", "2025-03-14"],
    )
    assert _infer_frequency(idx) == "irregular"


def test_every_other_business_day_is_irregular():
    # 100% of steps are 2-business-day gaps — a cadence, not holidays.
    idx = pd.bdate_range("2025-01-01", periods=80)[::2]
    assert _infer_frequency(idx) == "irregular"


def test_too_many_gaps_is_irregular():
    # Dropping every 5th business day (~20% gapped) exceeds the
    # occasional-holiday tolerance.
    base = pd.bdate_range("2025-01-01", periods=80)
    idx = base.difference(base[::5])
    assert _infer_frequency(idx) == "irregular"


def test_weekend_dates_stay_irregular():
    # Saturday in the index → not business-daily, even with small gaps.
    idx = pd.DatetimeIndex(
        ["2025-01-02", "2025-01-03", "2025-01-04", "2025-01-06", "2025-01-07"]
    )
    assert _infer_frequency(idx) == "irregular"


def test_pure_business_days_still_infer_B():
    # Unchanged fast path: pandas itself tags a gap-free bdate_range.
    assert _infer_frequency(pd.bdate_range("2025-01-01", periods=30)) == "B"


def test_is_business_daily_helper_direct():
    assert _is_business_daily_with_holiday_gaps(
        _bdays_without("2025-01-01", "2025-12-31", _HOLIDAYS_2025)
    )
    assert _is_business_daily_with_holiday_gaps(
        pd.bdate_range("2025-01-01", periods=10)
    )
    assert not _is_business_daily_with_holiday_gaps(
        pd.date_range("2025-01-01", periods=10, freq="D")  # weekends present
    )
    assert not _is_business_daily_with_holiday_gaps(
        pd.bdate_range("2025-01-01", periods=40)[::2]  # cadence, not gaps
    )


# --- _coarse_frequency: alias normaliser (pandas-version robust) --------


def test_coarse_frequency_alias_mapping():
    assert _coarse_frequency("B") == "B"
    assert _coarse_frequency("D") == "D"
    assert _coarse_frequency("W-SUN") == "W"
    # Month: pandas <2.2 'M', 2.2+ 'ME', business 'BME'.
    assert _coarse_frequency("M") == "M"
    assert _coarse_frequency("ME") == "M"
    assert _coarse_frequency("BME") == "M"
    # Quarter / Year, anchored + renamed variants.
    assert _coarse_frequency("Q") == "Q"
    assert _coarse_frequency("QE-DEC") == "Q"
    assert _coarse_frequency("A-DEC") == "Y"
    assert _coarse_frequency("YE-DEC") == "Y"
    # Unrecognised → irregular (never crashes).
    assert _coarse_frequency("SomethingWeird") == "irregular"
