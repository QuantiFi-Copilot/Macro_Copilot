"""Phase 1+2 step 6 — bridge frequency derivation (ADR 0016 Decision 3).

Locks the coarse-frequency derivation that makes every operator's
frequency-match check load-bearing instead of a None==None no-op.
"""

from __future__ import annotations

import pandas as pd

from shared.artifacts.adapters.from_time_series import (
    _coarse_frequency,
    _infer_frequency,
)


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
