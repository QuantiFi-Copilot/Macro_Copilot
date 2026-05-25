"""
test_rates_fetch_rolling_generic.py — focused unit tests for the
                                        rolling-generic fetchers

Covers the two helpers introduced for the bond_futures domain
(``futures_price_level`` and the upcoming volume/OI + scan
primitives):

  - ``fetch_rolling_generic_series``
  - ``fetch_rolling_generic_reference``

These tests exercise the helpers directly (not through the primitive)
so a future regression in the SQL — e.g. the WHERE clause silently
falls back to the enriched view's per-window contract_code or drops
the ``is_rolling_contract = TRUE`` guard — fires here BEFORE it shows
up as a confusing mismatch in a downstream primitive test.

DB-backed (read-only, no writes). Skipped automatically when the DB
is unreachable.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import text

from shared.analytics.rates_fetch import (
    fetch_rolling_generic_reference,
    fetch_rolling_generic_series,
)


@pytest.fixture(scope="module")
def engine():
    """Live engine via ``database.database.get_db_engine`` — same path
    the primitives use. Skips the module when the connection fails so
    the suite stays runnable offline."""
    try:
        from database.database import get_db_engine
        eng = get_db_engine()
        # Cheap probe: SELECT 1 — proves the connection works without
        # touching any data.
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng
    except Exception as exc:  # pragma: no cover - skip path
        pytest.skip(f"DB engine unavailable: {exc}")


class TestFetchRollingGenericSeries:
    def test_returns_long_format_for_ty1(self, engine):
        """TY1 is the 10Y UST bellwether — guaranteed to have history
        in any bond_futures-ingested substrate."""
        df = fetch_rolling_generic_series(
            engine=engine,
            curve_family="UST_FUT",
            contract_code="TY1",
            field_name="PX_LAST",
            start_date=date.today() - timedelta(days=400),
        )
        assert not df.empty, "TY1 should have >=1 row in the lookback window"
        assert list(df.columns) == ["trade_date", "field_value"], (
            f"expected long-format ['trade_date', 'field_value'], "
            f"got {list(df.columns)}"
        )
        # Dates must be sorted ascending.
        sorted_dates = pd.to_datetime(df["trade_date"]).is_monotonic_increasing
        assert sorted_dates, "rows must be returned in trade_date ascending order"

    def test_filter_on_master_stem_not_history_contract(self, engine):
        """Negative control. The enriched view's ``contract_code`` is
        the SCD2 history's per-window underlying (TYH6 / TYM6 / ...),
        not the stem (TY1) — but our helper filters via
        ``instrument_master.contract_code``, so 'TY1' returns rows.
        If a future regression switched the helper to the enriched
        view, this would return EMPTY (0 rows) because no row in the
        enriched view has contract_code = 'TY1'."""
        df = fetch_rolling_generic_series(
            engine=engine,
            curve_family="UST_FUT",
            contract_code="TY1",
            field_name="PX_LAST",
            start_date=date.today() - timedelta(days=400),
        )
        assert not df.empty, (
            "filtering by master stem 'TY1' must return rows; if this is "
            "empty, the helper has regressed to filtering the SCD2-"
            "overridden enriched view contract_code (per-window: TYH6 "
            "etc.) instead of the master stem"
        )

    def test_disambiguates_ty1_vs_uxy1(self, engine):
        """TY1 and UXY1 share (UST_FUT, 10Y) — the canonical TD#11
        ambiguity. The two stems must return DIFFERENT row sets."""
        start = date.today() - timedelta(days=120)
        ty1 = fetch_rolling_generic_series(
            engine=engine,
            curve_family="UST_FUT", contract_code="TY1",
            field_name="PX_LAST", start_date=start,
        )
        uxy1 = fetch_rolling_generic_series(
            engine=engine,
            curve_family="UST_FUT", contract_code="UXY1",
            field_name="PX_LAST", start_date=start,
        )
        assert not ty1.empty and not uxy1.empty
        # If the disambiguator broke and both filters collapsed to the
        # same instrument, the TWO frames would be identical.
        if len(ty1) == len(uxy1):
            # Different instruments → values must differ on at least
            # one row.
            assert (ty1["field_value"].values != uxy1["field_value"].values).any(), (
                "TY1 and UXY1 returned identical rows — contract_code "
                "disambiguator is not narrowing to a single instrument"
            )

    def test_disambiguates_us1_vs_wn1(self, engine):
        """US1 and WN1 share (UST_FUT, 30Y) — the other half of the
        TD#11 ambiguity. Same disambiguation contract as TY1/UXY1."""
        start = date.today() - timedelta(days=120)
        us1 = fetch_rolling_generic_series(
            engine=engine,
            curve_family="UST_FUT", contract_code="US1",
            field_name="PX_LAST", start_date=start,
        )
        wn1 = fetch_rolling_generic_series(
            engine=engine,
            curve_family="UST_FUT", contract_code="WN1",
            field_name="PX_LAST", start_date=start,
        )
        assert not us1.empty and not wn1.empty
        if len(us1) == len(wn1):
            assert (us1["field_value"].values != wn1["field_value"].values).any()

    def test_empty_for_unknown_stem(self, engine):
        df = fetch_rolling_generic_series(
            engine=engine,
            curve_family="UST_FUT",
            contract_code="DEFINITELY_NOT_A_STEM_X9Z",
            field_name="PX_LAST",
            start_date=date.today() - timedelta(days=120),
        )
        assert df.empty


class TestFetchRollingGenericReference:
    def test_returns_reference_dict_for_ty1(self, engine):
        ref = fetch_rolling_generic_reference(
            engine=engine,
            curve_family="UST_FUT",
            contract_code="TY1",
        )
        assert ref is not None
        assert ref["curve_family"] == "UST_FUT"
        assert ref["contract_code"] == "TY1"
        assert ref["tenor"] == "10Y"
        # TY1 quote_units = "points" per the playbook universe ingest.
        assert ref["quote_units"] == "points"
        # contract_size = 100000 for TY1.
        assert ref["contract_size"] == 100000.0

    def test_returns_reference_dict_for_rx1(self, engine):
        ref = fetch_rolling_generic_reference(
            engine=engine,
            curve_family="DE_FUT",
            contract_code="RX1",
        )
        assert ref is not None
        assert ref["tenor"] == "10Y"
        # RX1 (Bund 10Y) is quoted as a % of par.
        assert ref["quote_units"] == "% of par value"

    def test_returns_none_for_unknown_stem(self, engine):
        ref = fetch_rolling_generic_reference(
            engine=engine,
            curve_family="UST_FUT",
            contract_code="DEFINITELY_NOT_A_STEM_X9Z",
        )
        assert ref is None
