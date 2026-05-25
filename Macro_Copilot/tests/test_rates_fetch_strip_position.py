"""
test_rates_fetch_strip_position.py — focused unit tests for the
                                       strip-position fetchers

Covers the three helpers used by the policy_futures domain
(``futures_price_level`` and forthcoming calendar_spread /
butterfly_simple / pack_average_simple / strip_snapshot /
scan_policy_futures_extremes primitives):

  - ``fetch_strip_position``           (extended in this build with
                                        an optional ``end_date``)
  - ``fetch_strip_position_reference`` (NEW — exposes the per-strip
                                        inverse_pricing flag + the
                                        as_of-bounded SCD2 metadata)
  - ``fetch_strip_position_max_date``  (NEW — universe MAX probe for
                                        the future-anchor guard)

These tests exercise the helpers directly (not through the primitive)
so a future regression in the SQL — e.g. the JSONB strip_position
extraction silently coerces to TEXT, the SCD2 lookup picks the
"latest effective_from" instead of the as_of-bounded window, or the
inverse_pricing flag is missing — fires here BEFORE it shows up as a
confusing mismatch in a downstream primitive test.

DB-backed (read-only, no writes). Skipped automatically when the DB
is unreachable.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import text

from shared.analytics.rates_fetch import (
    fetch_strip_position,
    fetch_strip_position_max_date,
    fetch_strip_position_reference,
)


@pytest.fixture(scope="module")
def engine():
    """Live engine via ``database.database.get_db_engine`` — same path
    the primitives use. Skips the module when the connection fails so
    the suite stays runnable offline."""
    try:
        from database.database import get_db_engine
        eng = get_db_engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng
    except Exception as exc:  # pragma: no cover - skip path
        pytest.skip(f"DB engine unavailable: {exc}")


class TestFetchStripPosition:
    def test_returns_long_format_for_sfr1(self, engine):
        """SFR1 is the front SOFR strip slot — guaranteed to have
        history in any policy_futures-ingested substrate."""
        df = fetch_strip_position(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            field_name="PX_LAST",
            start_date=date.today() - timedelta(days=400),
        )
        assert not df.empty, "SFR1 should have >=1 row in the lookback window"
        assert list(df.columns) == ["trade_date", "field_value"], (
            f"expected long-format ['trade_date', 'field_value'], "
            f"got {list(df.columns)}"
        )
        # Dates must be sorted ascending.
        sorted_dates = pd.to_datetime(df["trade_date"]).is_monotonic_increasing
        assert sorted_dates, "rows must be returned in trade_date ascending order"

    def test_distinct_strip_positions_return_distinct_rows(self, engine):
        """SFR1 (front) and SFR2 (front+1) should return DIFFERENT row
        sets. If they returned identical rows, the JSONB
        strip_position extraction has silently collapsed both queries
        onto the same instrument."""
        start = date.today() - timedelta(days=120)
        sfr1 = fetch_strip_position(
            engine=engine,
            curve_family="SOFR_FUT", strip_position=1,
            field_name="PX_LAST", start_date=start,
        )
        sfr2 = fetch_strip_position(
            engine=engine,
            curve_family="SOFR_FUT", strip_position=2,
            field_name="PX_LAST", start_date=start,
        )
        assert not sfr1.empty and not sfr2.empty
        if len(sfr1) == len(sfr2):
            assert (sfr1["field_value"].values != sfr2["field_value"].values).any(), (
                "SFR1 and SFR2 returned identical rows — the JSONB "
                "strip_position extraction is not narrowing to a "
                "single instrument"
            )

    def test_end_date_bounds_the_result_set(self, engine):
        """Supplying ``end_date`` must cap the returned rows at that
        date — the deterministic-anchor scope the policy-futures
        monitor relies on."""
        start = date.today() - timedelta(days=400)
        cap = date.today() - timedelta(days=30)
        df = fetch_strip_position(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            field_name="PX_LAST",
            start_date=start,
            end_date=cap,
        )
        if df.empty:
            pytest.skip(
                "SFR1 has no rows in the start..end window on this DB; "
                "cannot exercise the end_date cap"
            )
        observed_max = pd.to_datetime(df["trade_date"]).max().date()
        assert observed_max <= cap, (
            f"end_date cap is leaking rows past the requested anchor: "
            f"observed_max={observed_max} cap={cap}"
        )

    def test_end_date_none_preserves_unbounded_query(self, engine):
        """``end_date=None`` (the pre-extension behavior) must NOT
        impose any upper bound — the fetcher's only ``trade_date``
        filter is the lower bound."""
        start = date.today() - timedelta(days=120)
        df_no_cap = fetch_strip_position(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            field_name="PX_LAST",
            start_date=start,
        )
        df_explicit_none = fetch_strip_position(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            field_name="PX_LAST",
            start_date=start,
            end_date=None,
        )
        # The two should be IDENTICAL — the explicit-None path is just
        # the default reaffirmed.
        assert len(df_no_cap) == len(df_explicit_none)
        if not df_no_cap.empty:
            assert (
                df_no_cap["field_value"].values
                == df_explicit_none["field_value"].values
            ).all()


class TestFetchStripPositionReference:
    def test_returns_metadata_for_sfr1(self, engine):
        """SFR1 must resolve to a metadata dict carrying the
        ``inverse_pricing`` flag and the per-strip disclosure fields."""
        ref = fetch_strip_position_reference(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            as_of_date=date.today(),
        )
        assert ref is not None, (
            "SFR1 must exist on instrument_master as a strip-position-"
            "keyed rolling contract"
        )
        # Required keys for the monitor.
        for key in (
            "curve_family", "contract_code", "strip_position",
            "inverse_pricing", "underlying_contract_code", "expiry_date",
            "security_name", "tick_size", "tick_value", "contract_size",
        ):
            assert key in ref, f"missing key: {key}"

        assert ref["curve_family"] == "SOFR_FUT"
        assert ref["contract_code"] == "SFR1"
        assert ref["strip_position"] == 1
        # SOFR futures are inverse-priced (100 - rate).
        assert ref["inverse_pricing"] is True

    def test_returns_none_for_unknown_strip(self, engine):
        ref = fetch_strip_position_reference(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=99,  # not in the V1 universe
            as_of_date=date.today(),
        )
        assert ref is None

    def test_inverse_pricing_flag_consistent_across_universe(self, engine):
        """All V1 policy_futures strips carry inverse_pricing=True per
        the playbook. If a future direct-priced family lands, this
        test (and the regime map in the YAML) needs updating in the
        SAME PR — the playbook is the source of truth."""
        for curve_family in ("SOFR_FUT", "EUR_SHORT_RATE_FUT", "SONIA_FUT"):
            ref = fetch_strip_position_reference(
                engine=engine,
                curve_family=curve_family,
                strip_position=1,
                as_of_date=date.today(),
            )
            assert ref is not None, f"strip slot missing for {curve_family} 1"
            assert ref["inverse_pricing"] is True, (
                f"{curve_family} 1 is direct-priced — update the YAML "
                "regime map and this test in the same PR"
            )

    def test_scd2_lookup_uses_as_of_bounded_window(self, engine):
        """Policy-futures SCD2 history is pre-populated with future
        windows (e.g. SFR1 runs out to 2035). The naive 'latest
        effective_from' lookup would return the FAR-end SFRU35 row;
        the as_of-bounded window predicate (``effective_from <=
        as_of_date AND (effective_to IS NULL OR effective_to >
        as_of_date)``) returns the actual current-front contract.

        Verify by querying two as_of dates that fall in DIFFERENT SCD2
        windows and confirming the underlying_contract_code rotates."""
        ref_early = fetch_strip_position_reference(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            as_of_date=date(2020, 1, 15),
        )
        ref_late = fetch_strip_position_reference(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            as_of_date=date(2026, 1, 15),
        )
        if ref_early is None or ref_late is None:
            pytest.skip(
                "SFR1 has no SCD2 row for one of the probed dates; "
                "cannot exercise the rotation check"
            )
        # The underlying_contract_code must differ — if it did NOT,
        # the SCD2 lookup is degenerate.
        early_uc = ref_early.get("underlying_contract_code")
        late_uc = ref_late.get("underlying_contract_code")
        assert early_uc != late_uc, (
            f"SCD2 rotation not detected: SFR1 underlying_contract_code "
            f"={early_uc!r} on 2020-01-15 and ={late_uc!r} on "
            "2026-01-15 — the as_of-bounded window predicate is not "
            "narrowing to a single window"
        )


class TestFetchStripPositionMaxDate:
    def test_returns_a_date_for_sfr1(self, engine):
        max_date = fetch_strip_position_max_date(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            field_name="PX_LAST",
        )
        assert max_date is not None
        assert isinstance(max_date, date)

    def test_max_date_agrees_with_series_fetcher(self, engine):
        """The MAX(trade_date) probe and the series fetcher must agree
        on 'what's this strip's last trading day' — they are wired to
        return consistent answers, and the future-anchor guard relies
        on that consistency to refuse off-data anchors."""
        max_via_probe = fetch_strip_position_max_date(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            field_name="PX_LAST",
        )
        df = fetch_strip_position(
            engine=engine,
            curve_family="SOFR_FUT",
            strip_position=1,
            field_name="PX_LAST",
            start_date=date.today() - timedelta(days=400),
        )
        assert not df.empty
        max_via_series = pd.to_datetime(df["trade_date"]).max().date()
        assert max_via_probe == max_via_series, (
            f"max_date probe ({max_via_probe}) disagrees with the "
            f"series fetcher ({max_via_series}) — the two helpers "
            "must return consistent answers"
        )
