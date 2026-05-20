"""tests/state/test_cash_bond_substrate.py — contract tests for the
cash-bond substrate landed by PR A3 (ADR 0003).

A3 is substrate-only: it adds the `cusip` / `isin` typed columns to
`instrument_master`, the `macro_data.otr_history` SCD2 table, and the DB
helpers that write/read them. These tests pin the Python side of that
substrate:

  * `_normalize_instrument_record` maps the new `cusip` / `isin` fields
    onto the typed-column dict (and leaves existing behaviour intact).
  * `_normalize_otr_record` validates the four required fields and shapes
    an OTR record into the uniform key set `otr_history` expects.
  * `upsert_otr_history` / `close_open_otr_window` honour the
    `_txn`-Connectable contract — handed a `Connection`, they run on the
    caller's transaction and never open a sub-transaction.

The tests do NOT spin up Postgres. The normalisation logic is pure
Python; the upsert/close tests stub the SQLAlchemy `Table` reflection
and `insert` builder exactly as `test_transactional_ingestion.py` does,
so no live DB schema is introspected.

Scope note: the ingester's `_build_instrument_attributes` exclude-set
change (cusip/isin excluded from the JSONB payload) is NOT unit-tested
here. `ingestion/ingest_parquet.py` imports the Google Cloud Storage SDK
at module load, so the existing test suite never imports it in a unit
test; that change is covered by `py_compile` + review, consistent with
the rest of the suite.

Related contracts:
  * ADR 0003 (cash-bond substrate — CUSIP/ISIN identity + otr_history).
  * ADR 0001 (the SCD2 + EXCLUDE pattern otr_history re-keys onto the
    (country, tenor) slot).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Connection

# Make sibling packages importable (conftest.py at tests/ also does this).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from database.database import (  # noqa: E402
    _normalize_instrument_record,
    _normalize_otr_record,
)


# ============================================================================
# CASH-BOND IDENTITY — _normalize_instrument_record maps cusip / isin
# ============================================================================


class TestNormalizeInstrumentRecordCashBond:
    """`_normalize_instrument_record` gains `cusip` / `isin` as typed-column
    keys (ADR 0003). Existing mappings are unaffected."""

    def _base_record(self, **overrides: Any) -> dict:
        rec = {
            "vendor_ticker": "912810TZ9 Govt",
            "asset_class": "rates",
            "instrument_type": "sovereign_cash_bond",
        }
        rec.update(overrides)
        return rec

    def test_cusip_and_isin_present_are_mapped(self) -> None:
        out = _normalize_instrument_record(
            self._base_record(cusip="912810TZ9", isin="US912810TZ97")
        )
        assert out["cusip"] == "912810TZ9"
        assert out["isin"] == "US912810TZ97"

    def test_cusip_and_isin_absent_default_to_none(self) -> None:
        """A non-cash-bond record (no cusip/isin) still normalises — the new
        columns simply default to None."""
        out = _normalize_instrument_record(self._base_record())
        assert out["cusip"] is None
        assert out["isin"] is None

    def test_cusip_isin_keys_always_present_in_output(self) -> None:
        """Uniform key set: the normalised dict always carries cusip/isin
        keys so a bulk upsert generates one coherent column list."""
        out = _normalize_instrument_record(self._base_record())
        assert "cusip" in out
        assert "isin" in out

    def test_existing_typed_columns_still_mapped(self) -> None:
        """Regression: adding cusip/isin did not disturb the existing
        typed-column mapping."""
        out = _normalize_instrument_record(
            self._base_record(
                curve_family="UST",
                country="US",
                currency="USD",
                tenor="10Y",
                maturity_date="2034-08-15",
            )
        )
        assert out["curve_family"] == "UST"
        assert out["country"] == "US"
        assert out["currency"] == "USD"
        assert out["tenor"] == "10Y"
        assert out["maturity_date"] == "2034-08-15"
        assert out["instrument_type"] == "sovereign_cash_bond"

    def test_attributes_passthrough_unchanged(self) -> None:
        """`attributes` is still passed through verbatim — coupon /
        issue_date / outstanding ride in here per ADR 0003."""
        attrs = {"coupon": 4.25, "issue_date": "2024-08-15", "outstanding": 42_000_000_000}
        out = _normalize_instrument_record(self._base_record(attributes=attrs))
        assert out["attributes"] == attrs


# ============================================================================
# OTR RECORD NORMALISATION — _normalize_otr_record
# ============================================================================


class TestNormalizeOtrRecord:
    """`_normalize_otr_record` validates the four required fields and shapes a
    record into the uniform key set otr_history expects."""

    def _valid(self, **overrides: Any) -> dict:
        rec = {
            "country": "US",
            "tenor": "10Y",
            "effective_from": "2024-02-15",
            "otr_instrument_id": 1234,
        }
        rec.update(overrides)
        return rec

    def test_valid_record_is_shaped(self) -> None:
        out = _normalize_otr_record(self._valid())
        assert out["country"] == "US"
        assert out["tenor"] == "10Y"
        assert out["effective_from"] == "2024-02-15"
        assert out["otr_instrument_id"] == 1234

    def test_otr_instrument_id_cast_to_int(self) -> None:
        out = _normalize_otr_record(self._valid(otr_instrument_id="1234"))
        assert out["otr_instrument_id"] == 1234
        assert isinstance(out["otr_instrument_id"], int)

    def test_optional_columns_default_to_none(self) -> None:
        """effective_to / attributes / load_id are optional — absent means
        NULL (effective_to NULL = currently on-the-run)."""
        out = _normalize_otr_record(self._valid())
        assert out["effective_to"] is None
        assert out["attributes"] is None
        assert out["load_id"] is None

    def test_optional_columns_carried_through(self) -> None:
        out = _normalize_otr_record(
            self._valid(
                effective_to="2024-05-14",
                attributes={"auction_date": "2024-02-13"},
                load_id=77,
            )
        )
        assert out["effective_to"] == "2024-05-14"
        assert out["attributes"] == {"auction_date": "2024-02-13"}
        assert out["load_id"] == 77

    def test_uniform_key_set_across_records(self) -> None:
        """Two records with disjoint optional fields produce the same key
        set — required for a coherent bulk insert column list."""
        a = _normalize_otr_record(self._valid())
        b = _normalize_otr_record(self._valid(load_id=9, effective_to="2024-05-14"))
        assert set(a.keys()) == set(b.keys())

    def test_missing_country_raises(self) -> None:
        rec = self._valid()
        del rec["country"]
        with pytest.raises(ValueError, match="country"):
            _normalize_otr_record(rec)

    def test_missing_tenor_raises(self) -> None:
        rec = self._valid()
        del rec["tenor"]
        with pytest.raises(ValueError, match="tenor"):
            _normalize_otr_record(rec)

    def test_missing_effective_from_raises(self) -> None:
        rec = self._valid()
        del rec["effective_from"]
        with pytest.raises(ValueError, match="effective_from"):
            _normalize_otr_record(rec)

    def test_missing_otr_instrument_id_raises(self) -> None:
        rec = self._valid()
        del rec["otr_instrument_id"]
        with pytest.raises(ValueError, match="otr_instrument_id"):
            _normalize_otr_record(rec)


# ============================================================================
# OTR HELPERS — _txn-Connectable contract (no sub-transaction under Connection)
# ============================================================================


class _FakeColumn:
    """Reflected-column stand-in. Supports the expression-building the OTR
    helpers do (``==``, ``<``, ``.is_()``) and attribute assignment (the
    ``none_as_null`` type override). The real SQL is never compiled in these
    tests, so every operation yields a throwaway placeholder."""

    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other: Any) -> Any:  # noqa: D105
        return MagicMock()

    def __lt__(self, other: Any) -> Any:  # noqa: D105
        return MagicMock()

    def is_(self, other: Any) -> Any:
        return MagicMock()

    def __hash__(self) -> int:  # noqa: D105
        return id(self)


class _FakeColumns:
    """Stand-in for a reflected Table's ``.c`` collection: iterable (so the
    ON CONFLICT update-set comprehension can walk it) and attribute-addressable
    (so ``otr.c.attributes`` / ``otr.c.country`` resolve to a column)."""

    def __init__(self, names: List[str]):
        self._cols = [_FakeColumn(n) for n in names]
        for col in self._cols:
            setattr(self, col.name, col)

    def __iter__(self):
        return iter(self._cols)


_OTR_COLUMN_NAMES = [
    "otr_history_id",
    "country",
    "tenor",
    "effective_from",
    "effective_to",
    "otr_instrument_id",
    "attributes",
    "load_id",
    "created_at",
]


def _make_connection_stub() -> MagicMock:
    """Connection-like stub that records `.execute` and must never see
    `.begin()` (the helper must reuse the caller's transaction)."""
    conn = MagicMock(spec=Connection)
    conn.begin = MagicMock()  # MUST NOT be called
    result = MagicMock()
    result.rowcount = 1
    conn.execute.return_value = result
    return conn


class TestUpsertOtrHistory:
    """`upsert_otr_history(connection, records)` runs its INSERT-ON-CONFLICT on
    the caller's connection and opens no sub-transaction."""

    def test_empty_records_is_a_noop(self) -> None:
        from database import database as db_mod

        conn = _make_connection_stub()
        assert db_mod.upsert_otr_history(conn, []) == 0
        conn.execute.assert_not_called()
        conn.begin.assert_not_called()

    def test_upsert_under_connection_does_not_begin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from database import database as db_mod

        conn = _make_connection_stub()

        fake_table = MagicMock()
        fake_table.c = _FakeColumns(_OTR_COLUMN_NAMES)
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: fake_table)

        fake_stmt = MagicMock()
        fake_stmt.values.return_value = fake_stmt
        fake_stmt.on_conflict_do_update.return_value = "<otr_upsert_stmt>"
        monkeypatch.setattr(db_mod, "insert", lambda *a, **k: fake_stmt)

        affected = db_mod.upsert_otr_history(
            conn,
            [
                {
                    "country": "US",
                    "tenor": "10Y",
                    "effective_from": "2024-02-15",
                    "otr_instrument_id": 1234,
                }
            ],
        )

        assert affected == 1
        conn.execute.assert_called_once_with("<otr_upsert_stmt>")
        conn.begin.assert_not_called()
        # ON CONFLICT keyed on the (country, tenor, effective_from) natural key.
        _, kwargs = fake_stmt.on_conflict_do_update.call_args
        assert kwargs["index_elements"] == ["country", "tenor", "effective_from"]

    def test_invalid_record_raises_before_any_execute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A record missing a required field fails in _normalize_otr_record —
        no DB statement is executed."""
        from database import database as db_mod

        conn = _make_connection_stub()
        fake_table = MagicMock()
        fake_table.c = _FakeColumns(_OTR_COLUMN_NAMES)
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: fake_table)
        monkeypatch.setattr(db_mod, "insert", lambda *a, **k: MagicMock())

        with pytest.raises(ValueError, match="otr_instrument_id"):
            db_mod.upsert_otr_history(
                conn,
                [{"country": "US", "tenor": "10Y", "effective_from": "2024-02-15"}],
            )
        conn.execute.assert_not_called()


class TestCloseOpenOtrWindow:
    """`close_open_otr_window(connection, ...)` runs its UPDATE on the caller's
    connection and opens no sub-transaction."""

    def _fake_update_table(self) -> MagicMock:
        """A reflected-table stand-in whose update() chain
        (.update().where()*.values()) yields a placeholder statement, and
        whose .c resolves columns that support the WHERE expression-building."""
        chain = MagicMock()
        chain.where.return_value = chain
        chain.values.return_value = "<otr_close_stmt>"
        fake_table = MagicMock()
        fake_table.update.return_value = chain
        fake_table.c = _FakeColumns(_OTR_COLUMN_NAMES)
        return fake_table

    def test_close_under_connection_does_not_begin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from database import database as db_mod

        conn = _make_connection_stub()
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: self._fake_update_table())

        rowcount = db_mod.close_open_otr_window(
            conn, country="US", tenor="10Y", new_effective_from="2024-05-15"
        )

        assert rowcount == 1
        conn.execute.assert_called_once_with("<otr_close_stmt>")
        conn.begin.assert_not_called()

    def test_close_returns_zero_when_no_open_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from database import database as db_mod

        conn = _make_connection_stub()
        conn.execute.return_value.rowcount = 0
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: self._fake_update_table())

        assert (
            db_mod.close_open_otr_window(
                conn, country="JP", tenor="30Y", new_effective_from="2025-01-02"
            )
            == 0
        )
