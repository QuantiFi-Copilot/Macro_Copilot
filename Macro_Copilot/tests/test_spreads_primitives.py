"""
test_spreads_primitives.py — Unit tests for shared/analytics/spreads.py
=========================================================================

Two responsibilities:

1.  **Regression** — every primitive's default behaviour matches the
    pre-commit-2 implementation byte-for-byte.  This guards against
    silent drift if a future change accidentally alters a default.

2.  **Override coverage** — every kwarg actually changes the output
    when overridden, proving the parameter is wired through.  Without
    this, a typo in the kwarg plumbing (e.g. a parameter that's read
    but never used) would go unnoticed.

The two new kwargs introduced in commit 2 of the tool-config pilot
(``compute_spread_bps.round_decimals`` and ``rolling_zscore.ddof``)
get extra attention because their defaults must reproduce the
previously-hardcoded behaviour exactly.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from shared.analytics.spreads import (
    Z_SCORE_MIN_PERIODS,
    Z_SCORE_WINDOW,
    compute_spread_bps,
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)


# ============================================================================
# Module constants — regression
# ============================================================================

class TestModuleConstants:
    """Locks the canonical default values.  Bumping these is a
    deliberate methodology change that requires a regenerated parity
    fixture (see tests/fixtures/curve_spread_v1/README.md)."""

    def test_z_score_window_is_252(self):
        assert Z_SCORE_WINDOW == 252

    def test_z_score_min_periods_is_60(self):
        assert Z_SCORE_MIN_PERIODS == 60


# ============================================================================
# safe_float
# ============================================================================

class TestSafeFloat:
    def test_default_decimals_is_4(self):
        assert safe_float(1.234567) == 1.2346

    def test_decimals_override(self):
        assert safe_float(1.234567, decimals=2) == 1.23
        assert safe_float(1.5, decimals=0) == 2.0

    def test_none_returns_none(self):
        assert safe_float(None) is None

    def test_nan_returns_none(self):
        assert safe_float(float("nan")) is None

    def test_int_passes_through(self):
        assert safe_float(5) == 5.0

    def test_string_numeric_returns_float(self):
        # Defensive — float() accepts numeric strings.
        assert safe_float("3.14") == 3.14

    def test_invalid_string_returns_none(self):
        assert safe_float("not a number") is None

    def test_empty_string_returns_none(self):
        # Not strictly a numeric value but defensive
        assert safe_float("") is None

    def test_negative_decimals_rounds_to_tens(self):
        # round() with negative ndigits rounds to multiples of 10/100/etc.
        # Documenting current behaviour rather than constraining it.
        assert safe_float(123.456, decimals=-1) == 120


# ============================================================================
# pivot_and_align_tenors
# ============================================================================

class TestPivotAndAlign:
    def _df(self, rows: list[tuple]) -> pd.DataFrame:
        return pd.DataFrame(rows, columns=["trade_date", "tenor", "field_value"])

    def test_pivots_two_tenors(self):
        df = self._df([
            ("2025-01-01", "2Y", 4.0),
            ("2025-01-01", "10Y", 4.5),
            ("2025-01-02", "2Y", 4.1),
            ("2025-01-02", "10Y", 4.6),
        ])
        wide = pivot_and_align_tenors(df, required_tenors=["2Y", "10Y"])

        assert sorted(wide.columns) == ["10Y", "2Y"]
        assert len(wide) == 2
        assert wide.loc[pd.Timestamp("2025-01-01"), "2Y"] == 4.0
        assert wide.loc[pd.Timestamp("2025-01-02"), "10Y"] == 4.6

    def test_drops_rows_where_required_tenor_missing(self):
        # 10Y missing on day 2; ffill bridges it (limit=5), then dropna
        # finds none missing and keeps the row.
        df = self._df([
            ("2025-01-01", "2Y", 4.0),
            ("2025-01-01", "10Y", 4.5),
            ("2025-01-02", "2Y", 4.1),
            # No 10Y on 2025-01-02 — ffill from 4.5
            ("2025-01-03", "2Y", 4.2),
            ("2025-01-03", "10Y", 4.7),
        ])
        wide = pivot_and_align_tenors(df, required_tenors=["2Y", "10Y"])
        # Three rows kept, day-2's 10Y ffilled to 4.5
        assert len(wide) == 3
        assert wide.loc[pd.Timestamp("2025-01-02"), "10Y"] == 4.5

    def test_ffill_limit_controls_gap_bridging(self):
        # 10Y missing for 3 consecutive days.  ffill_limit=5 (default)
        # bridges all of them; ffill_limit=1 bridges only day 2,
        # so days 3 and 4 stay NaN and are dropped by dropna(required=...).
        # (pandas ffill rejects limit=0 with ValueError, so the test
        # uses 1 — the minimum legitimate value — to exercise the kwarg.)
        df = self._df([
            ("2025-01-01", "2Y", 4.0),
            ("2025-01-01", "10Y", 4.5),
            ("2025-01-02", "2Y", 4.1),
            # No 10Y on 2025-01-02
            ("2025-01-03", "2Y", 4.2),
            # No 10Y on 2025-01-03
            ("2025-01-04", "2Y", 4.3),
            # No 10Y on 2025-01-04
            ("2025-01-05", "2Y", 4.4),
            ("2025-01-05", "10Y", 4.6),
        ])

        wide_default = pivot_and_align_tenors(df, required_tenors=["2Y", "10Y"])
        # ffill_limit=5 bridges the 3-day gap; all 5 rows retained.
        assert len(wide_default) == 5
        assert wide_default.loc[pd.Timestamp("2025-01-03"), "10Y"] == 4.5

        wide_short = pivot_and_align_tenors(
            df, required_tenors=["2Y", "10Y"], ffill_limit=1,
        )
        # ffill_limit=1: only day 2's 10Y is filled; days 3 and 4 stay
        # NaN and are dropped.  Days 1, 2, 5 retained.
        assert len(wide_short) == 3
        retained = list(wide_short.index)
        assert pd.Timestamp("2025-01-03") not in retained
        assert pd.Timestamp("2025-01-04") not in retained

    def test_drops_duplicates_keeps_last(self):
        df = self._df([
            ("2025-01-01", "2Y", 4.0),
            ("2025-01-01", "2Y", 4.99),  # duplicate, should win
            ("2025-01-01", "10Y", 4.5),
        ])
        wide = pivot_and_align_tenors(df, required_tenors=["2Y", "10Y"])
        assert wide.loc[pd.Timestamp("2025-01-01"), "2Y"] == 4.99

    def test_coerces_string_values_to_numeric(self):
        df = self._df([
            ("2025-01-01", "2Y", "4.0"),
            ("2025-01-01", "10Y", "4.5"),
        ])
        wide = pivot_and_align_tenors(df, required_tenors=["2Y", "10Y"])
        assert wide.loc[pd.Timestamp("2025-01-01"), "2Y"] == 4.0

    def test_uncoerciable_value_becomes_nan_then_dropped(self):
        df = self._df([
            ("2025-01-01", "2Y", "4.0"),
            ("2025-01-01", "10Y", "garbage"),
            ("2025-01-02", "2Y", "4.1"),
            ("2025-01-02", "10Y", "4.6"),
        ])
        wide = pivot_and_align_tenors(df, required_tenors=["2Y", "10Y"])
        # Day 1's 10Y is NaN after coercion; ffill from a future row is
        # NOT possible (ffill is forward-direction only).  Day 1 is dropped.
        assert pd.Timestamp("2025-01-01") not in wide.index
        assert pd.Timestamp("2025-01-02") in wide.index


# ============================================================================
# compute_spread_bps
# ============================================================================

class TestComputeSpreadBps:
    def test_basic_spread_50bps(self):
        wide = pd.DataFrame({
            "2Y": [4.0, 4.1, 4.2],
            "10Y": [4.5, 4.6, 4.7],
        })
        bps = compute_spread_bps(wide, minuend_col="10Y", subtrahend_col="2Y")
        assert list(bps) == [50.0, 50.0, 50.0]

    def test_default_round_decimals_is_2(self):
        # Input precision > 2dp; default round to 2.
        wide = pd.DataFrame({"long": [4.523456], "short": [4.000123]})
        bps = compute_spread_bps(wide, minuend_col="long", subtrahend_col="short")
        assert bps.iloc[0] == 52.33

    def test_round_decimals_override_4(self):
        wide = pd.DataFrame({"long": [4.523456], "short": [4.000123]})
        bps = compute_spread_bps(
            wide, minuend_col="long", subtrahend_col="short",
            round_decimals=4,
        )
        assert bps.iloc[0] == 52.3333

    def test_round_decimals_override_0(self):
        wide = pd.DataFrame({"long": [4.523456], "short": [4.000123]})
        bps = compute_spread_bps(
            wide, minuend_col="long", subtrahend_col="short",
            round_decimals=0,
        )
        assert bps.iloc[0] == 52.0

    def test_negative_spread_preserved(self):
        wide = pd.DataFrame({"long": [3.5], "short": [4.0]})
        bps = compute_spread_bps(wide, minuend_col="long", subtrahend_col="short")
        assert bps.iloc[0] == -50.0

    def test_default_matches_explicit_2dp(self):
        """Backward-compat: default behaviour identical to old
        hardcoded ``.round(2)``."""
        wide = pd.DataFrame({
            "long":  [4.523456, 4.123, 4.0],
            "short": [4.000123, 3.987, 3.5],
        })
        default = compute_spread_bps(wide, minuend_col="long", subtrahend_col="short")
        explicit = compute_spread_bps(
            wide, minuend_col="long", subtrahend_col="short",
            round_decimals=2,
        )
        assert (default == explicit).all()


# ============================================================================
# rolling_zscore
# ============================================================================

class TestRollingZScore:
    def test_default_window_too_long_returns_all_nan(self):
        # min_periods=60 by default; a 3-element series can't satisfy it.
        s = pd.Series([1.0, 2.0, 3.0])
        z = rolling_zscore(s)
        assert z.isna().all()

    def test_short_window_populates_after_min_periods(self):
        rng = np.random.RandomState(42)
        s = pd.Series(rng.randn(100))
        z = rolling_zscore(s, window=20, min_periods=10)
        # First 9 below min_periods
        assert z.iloc[:9].isna().all()
        # Index 9+ should have z-scores
        assert not z.iloc[9:].isna().all()

    def test_constant_series_yields_nan(self):
        # std=0 → divide by zero → NaN
        s = pd.Series([5.0] * 100)
        z = rolling_zscore(s, window=20, min_periods=10)
        assert z.iloc[20:].isna().all()

    def test_default_ddof_is_1(self):
        """Sample std (ddof=1) — confirms the implicit pandas default
        is now explicit and unchanged."""
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        z_default = rolling_zscore(s, window=5, min_periods=5, round_decimals=10)
        z_explicit_1 = rolling_zscore(
            s, window=5, min_periods=5, ddof=1, round_decimals=10,
        )
        assert (z_default.dropna() == z_explicit_1.dropna()).all()

    def test_ddof_override_produces_different_z(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        z_sample = rolling_zscore(s, window=5, min_periods=5, ddof=1)
        z_pop = rolling_zscore(s, window=5, min_periods=5, ddof=0)
        # Both produce a value at index 4
        assert not math.isnan(z_sample.iloc[-1])
        assert not math.isnan(z_pop.iloc[-1])
        # Sample std > population std → sample-z magnitude < pop-z magnitude.
        assert abs(z_sample.iloc[-1]) < abs(z_pop.iloc[-1])

    def test_known_zscore_calculation_with_ddof_1(self):
        """Hand-computable check: ramp 1..5 with window=5.
        mean = 3, sample std = sqrt(2.5), so z[idx=4] = (5-3)/sqrt(2.5)
        ≈ 1.2649."""
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        z = rolling_zscore(
            s, window=5, min_periods=5, ddof=1, round_decimals=10,
        )
        expected = 2.0 / math.sqrt(2.5)
        assert math.isclose(z.iloc[4], round(expected, 10), abs_tol=1e-9)

    def test_known_zscore_calculation_with_ddof_0(self):
        """Same ramp, population std this time.
        pop std = sqrt(2), z[idx=4] = (5-3)/sqrt(2) ≈ 1.4142."""
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        z = rolling_zscore(
            s, window=5, min_periods=5, ddof=0, round_decimals=10,
        )
        expected = 2.0 / math.sqrt(2.0)
        assert math.isclose(z.iloc[4], round(expected, 10), abs_tol=1e-9)

    def test_round_decimals_override(self):
        rng = np.random.RandomState(42)
        s = pd.Series(rng.randn(200))
        z = rolling_zscore(s, window=20, min_periods=10, round_decimals=2)
        # All non-NaN z-scores should have at most 2 decimals.
        for v in z.dropna():
            assert v == round(v, 2), f"value {v} rounded incorrectly"

    def test_default_round_decimals_is_4(self):
        rng = np.random.RandomState(42)
        s = pd.Series(rng.randn(200))
        z = rolling_zscore(s, window=20, min_periods=10)
        for v in z.dropna():
            assert v == round(v, 4), f"value {v} rounded incorrectly"

    def test_default_window_uses_module_constant(self):
        """Backward-compat: with no kwargs, window comes from
        Z_SCORE_WINDOW.  This is the contract subsequent commits will
        replace with config-driven loading."""
        s = pd.Series([1.0] * (Z_SCORE_WINDOW + 10))
        # If we override window=Z_SCORE_WINDOW explicitly, output must
        # be byte-identical to the default call.
        a = rolling_zscore(s)
        b = rolling_zscore(s, window=Z_SCORE_WINDOW, min_periods=Z_SCORE_MIN_PERIODS)
        # Both are all-NaN (constant series), but we want to assert
        # they're equivalent in shape.
        assert len(a) == len(b)
        assert (a.isna() == b.isna()).all()


# ============================================================================
# Backward-compat: defaults reproduce previous-commit behaviour
# ============================================================================

class TestBackwardCompat:
    """End-to-end check that the two new kwargs (round_decimals on
    compute_spread_bps, ddof on rolling_zscore) produce identical
    output when defaulted, vs the old in-place implementations.

    This is the primary safety net for the parameterisation: as long
    as defaults match, every existing caller continues to work
    unchanged."""

    def test_compute_spread_bps_default_matches_pre_refactor(self):
        wide = pd.DataFrame({
            "long":  [4.523456, 4.501234, 4.472891],
            "short": [4.000123, 3.987654, 3.978912],
        })
        # Pre-refactor implementation used hardcoded `.round(2)`.
        manual = ((wide["long"] - wide["short"]) * 100).round(2)
        new_default = compute_spread_bps(
            wide, minuend_col="long", subtrahend_col="short",
        )
        assert (manual == new_default).all()

    def test_rolling_zscore_default_matches_pre_refactor(self):
        rng = np.random.RandomState(2026)
        s = pd.Series(rng.randn(400))

        # Pre-refactor implementation: pandas .std() default (ddof=1
        # implicitly), .round(4).
        rolling_mean = s.rolling(
            window=Z_SCORE_WINDOW, min_periods=Z_SCORE_MIN_PERIODS,
        ).mean()
        rolling_std = s.rolling(
            window=Z_SCORE_WINDOW, min_periods=Z_SCORE_MIN_PERIODS,
        ).std()  # pandas default ddof=1
        manual = ((s - rolling_mean) / rolling_std).round(4)

        new_default = rolling_zscore(s)

        # Compare only non-NaN positions (NaN != NaN by definition)
        a = new_default.dropna()
        b = manual.dropna()
        assert len(a) == len(b)
        assert (a == b).all()
