"""tests/state/test_event_calendar_substrate.py — contract tests for the
event-calendar substrate landed by PR B1 (ADR 0004).

B1 is substrate-only: it adds the `macro_data.event_calendar` table and the
DB helpers that write/read it. These tests pin the Python side of that
substrate:

  * `_normalize_event_record` validates the four required fields
    (event_type / event_category / country / release_date), shapes a
    record into the uniform key set `event_calendar` expects, casts the
    FK columns to int, and WARNS — does not abort — on an
    event_category outside the documented `EVENT_CATEGORIES`.
  * `upsert_event_calendar` honours the `_txn`-Connectable contract —
    handed a `Connection`, it runs on the caller's transaction and
    never opens a sub-transaction — and targets the
    `(event_type, country, release_date)` natural key.
  * `get_events_in_window` issues a single windowed query and returns a
    list of event dicts.

The tests do NOT spin up Postgres. The normalisation logic is pure
Python; the upsert test stubs the SQLAlchemy `Table` reflection and
`insert` builder exactly as `test_transactional_ingestion.py` and
`test_cash_bond_substrate.py` do; the read test stubs the engine's
connection.

Related contracts:
  * ADR 0004 (event-calendar substrate — the dedicated `event_calendar`
    table).
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
    EVENT_CATEGORIES,
    _normalize_event_record,
)


# ============================================================================
# EVENT RECORD NORMALISATION — _normalize_event_record
# ============================================================================


class TestNormalizeEventRecord:
    """`_normalize_event_record` validates the four required fields and shapes
    a record into the uniform key set event_calendar expects."""

    def _valid(self, **overrides: Any) -> dict:
        rec = {
            "event_type": "cpi_yoy",
            "event_category": "economic_release",
            "country": "US",
            "release_date": "2026-03-12",
        }
        rec.update(overrides)
        return rec

    def test_valid_record_is_shaped(self) -> None:
        out = _normalize_event_record(self._valid())
        assert out["event_type"] == "cpi_yoy"
        assert out["event_category"] == "economic_release"
        assert out["country"] == "US"
        assert out["release_date"] == "2026-03-12"

    def test_optional_columns_default_to_none(self) -> None:
        out = _normalize_event_record(self._valid())
        for col in (
            "currency", "central_bank", "release_time", "period",
            "actual", "consensus_median", "consensus_high", "consensus_low",
            "prior", "revised_prior", "surprise", "surprise_std_dev",
            "high_yield", "bid_to_cover", "tail_bps", "indirect_pct",
            "related_instrument_id", "attributes", "load_id",
        ):
            assert out[col] is None, f"{col} should default to None"

    def test_economic_release_fields_carried_through(self) -> None:
        out = _normalize_event_record(
            self._valid(
                period="Feb 2026",
                actual=3.1,
                consensus_median=3.0,
                prior=2.9,
                surprise=0.1,
            )
        )
        assert out["period"] == "Feb 2026"
        assert out["actual"] == 3.1
        assert out["consensus_median"] == 3.0
        assert out["prior"] == 2.9
        assert out["surprise"] == 0.1

    def test_auction_result_fields_carried_through(self) -> None:
        """Auction result fields are typed columns (ADR 0004) — they ride
        through the normaliser like any other typed column."""
        out = _normalize_event_record(
            self._valid(
                event_type="ust_auction_10y",
                event_category="auction",
                high_yield=4.21,
                bid_to_cover=2.55,
                tail_bps=0.4,
                indirect_pct=68.2,
            )
        )
        assert out["high_yield"] == 4.21
        assert out["bid_to_cover"] == 2.55
        assert out["tail_bps"] == 0.4
        assert out["indirect_pct"] == 68.2

    def test_fk_columns_cast_to_int(self) -> None:
        """related_instrument_id / load_id are FK integers — cast when
        present (a parquet round-trip can yield numpy int64 / str)."""
        out = _normalize_event_record(
            self._valid(related_instrument_id="42", load_id="7")
        )
        assert out["related_instrument_id"] == 42
        assert isinstance(out["related_instrument_id"], int)
        assert out["load_id"] == 7
        assert isinstance(out["load_id"], int)

    def test_uniform_key_set_across_records(self) -> None:
        """Two records with disjoint optional fields produce the same key
        set — required for a coherent bulk insert column list."""
        a = _normalize_event_record(self._valid())
        b = _normalize_event_record(
            self._valid(actual=3.1, central_bank="FOMC", load_id=5)
        )
        assert set(a.keys()) == set(b.keys())

    def test_unknown_event_category_raises(self) -> None:
        """event_category is a closed family — a value outside EVENT_CATEGORIES
        is a contract violation and raises a typed exception; it is NOT
        silently written (P6/P8)."""
        with pytest.raises(ValueError, match="EVENT_CATEGORIES"):
            _normalize_event_record(self._valid(event_category="speech"))

    def test_documented_categories_accepted(self) -> None:
        """Each of the three documented categories normalises without error."""
        for category in EVENT_CATEGORIES:
            out = _normalize_event_record(self._valid(event_category=category))
            assert out["event_category"] == category

    def test_missing_event_type_raises(self) -> None:
        rec = self._valid()
        del rec["event_type"]
        with pytest.raises(ValueError, match="event_type"):
            _normalize_event_record(rec)

    def test_missing_event_category_raises(self) -> None:
        rec = self._valid()
        del rec["event_category"]
        with pytest.raises(ValueError, match="event_category"):
            _normalize_event_record(rec)

    def test_missing_country_raises(self) -> None:
        rec = self._valid()
        del rec["country"]
        with pytest.raises(ValueError, match="country"):
            _normalize_event_record(rec)

    def test_missing_release_date_raises(self) -> None:
        rec = self._valid()
        del rec["release_date"]
        with pytest.raises(ValueError, match="release_date"):
            _normalize_event_record(rec)


# ============================================================================
# EVENT_CATEGORIES — the documented closed set
# ============================================================================


class TestEventCategories:
    def test_event_categories_value(self) -> None:
        """The closed set is exactly the three documented categories."""
        assert EVENT_CATEGORIES == (
            "economic_release",
            "central_bank_meeting",
            "auction",
        )


# ============================================================================
# upsert_event_calendar — _txn-Connectable contract
# ============================================================================


class _FakeColumn:
    """Reflected-column stand-in: carries a `.name` and accepts attribute
    assignment (the `none_as_null` type override sets `.type`)."""

    def __init__(self, name: str):
        self.name = name


class _FakeColumns:
    """Stand-in for a reflected Table's `.c` collection: iterable (so the
    ON CONFLICT update-set comprehension can walk it) and attribute-addressable
    (so `events.c.attributes` resolves to a column)."""

    def __init__(self, names: List[str]):
        self._cols = [_FakeColumn(n) for n in names]
        for col in self._cols:
            setattr(self, col.name, col)

    def __iter__(self):
        return iter(self._cols)


_EVENT_COLUMN_NAMES = [
    "event_id", "event_type", "event_category", "country", "currency",
    "central_bank", "release_date", "release_time", "period",
    "actual", "consensus_median", "consensus_high", "consensus_low",
    "prior", "revised_prior", "surprise", "surprise_std_dev",
    "high_yield", "bid_to_cover", "tail_bps", "indirect_pct",
    "related_instrument_id", "attributes", "load_id", "created_at",
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


class TestUpsertEventCalendar:
    """`upsert_event_calendar(connection, records)` runs its INSERT-ON-CONFLICT
    on the caller's connection and opens no sub-transaction."""

    def test_empty_records_is_a_noop(self) -> None:
        from database import database as db_mod

        conn = _make_connection_stub()
        assert db_mod.upsert_event_calendar(conn, []) == 0
        conn.execute.assert_not_called()
        conn.begin.assert_not_called()

    def test_upsert_under_connection_does_not_begin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from database import database as db_mod

        conn = _make_connection_stub()

        fake_table = MagicMock()
        fake_table.c = _FakeColumns(_EVENT_COLUMN_NAMES)
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: fake_table)

        fake_stmt = MagicMock()
        fake_stmt.values.return_value = fake_stmt
        fake_stmt.on_conflict_do_update.return_value = "<event_upsert_stmt>"
        monkeypatch.setattr(db_mod, "insert", lambda *a, **k: fake_stmt)

        affected = db_mod.upsert_event_calendar(
            conn,
            [
                {
                    "event_type": "cpi_yoy",
                    "event_category": "economic_release",
                    "country": "US",
                    "release_date": "2026-03-12",
                }
            ],
        )

        assert affected == 1
        conn.execute.assert_called_once_with("<event_upsert_stmt>")
        conn.begin.assert_not_called()
        # ON CONFLICT keyed on the (event_type, country, release_date) natural key.
        _, kwargs = fake_stmt.on_conflict_do_update.call_args
        assert kwargs["index_elements"] == ["event_type", "country", "release_date"]

    def test_on_conflict_overwrites_every_non_key_column(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FULL-ROW UPSERT contract: ON CONFLICT DO UPDATE sets EVERY non-key
        column from `excluded`. A caller must therefore pass the complete event
        row on every upsert — an omitted optional field is written NULL, not
        merge-preserved (ADR 0004; the post-auction results upsert re-sends the
        schedule fields). This test locks that behaviour."""
        from database import database as db_mod

        conn = _make_connection_stub()
        fake_table = MagicMock()
        fake_table.c = _FakeColumns(_EVENT_COLUMN_NAMES)
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: fake_table)

        fake_stmt = MagicMock()
        fake_stmt.values.return_value = fake_stmt
        fake_stmt.on_conflict_do_update.return_value = "<stmt>"
        monkeypatch.setattr(db_mod, "insert", lambda *a, **k: fake_stmt)

        db_mod.upsert_event_calendar(
            conn,
            [
                {
                    "event_type": "ust_auction_10y",
                    "event_category": "auction",
                    "country": "US",
                    "release_date": "2026-03-11",
                }
            ],
        )

        _, kwargs = fake_stmt.on_conflict_do_update.call_args
        update_keys = set(kwargs["set_"].keys())
        # The natural key + the immutable identity/audit columns are excluded;
        # EVERY other column must be in the update set.
        key_and_immutable = {
            "event_id", "event_type", "country", "release_date", "created_at",
        }
        expected = set(_EVENT_COLUMN_NAMES) - key_and_immutable
        assert update_keys == expected, (
            "ON CONFLICT DO UPDATE must overwrite every non-key column — "
            "this is the full-row-upsert contract"
        )

    def test_invalid_record_raises_before_any_execute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A record missing a required field fails in _normalize_event_record —
        no DB statement is executed."""
        from database import database as db_mod

        conn = _make_connection_stub()
        fake_table = MagicMock()
        fake_table.c = _FakeColumns(_EVENT_COLUMN_NAMES)
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: fake_table)
        monkeypatch.setattr(db_mod, "insert", lambda *a, **k: MagicMock())

        with pytest.raises(ValueError, match="release_date"):
            db_mod.upsert_event_calendar(
                conn,
                [{"event_type": "nfp", "event_category": "economic_release", "country": "US"}],
            )
        conn.execute.assert_not_called()


# ============================================================================
# get_events_in_window — windowed read
# ============================================================================


class TestGetEventsInWindow:
    """`get_events_in_window` issues one windowed query and returns the rows
    as a list of dicts."""

    def _engine_returning(self, rows: List[dict]) -> MagicMock:
        """An Engine stub whose connect() context yields a conn whose
        execute(...).mappings().all() returns `rows`."""
        conn = MagicMock()
        result = MagicMock()
        result.mappings.return_value.all.return_value = rows
        conn.execute.return_value = result

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)

        engine = MagicMock()
        engine.connect.return_value = ctx
        return engine

    def test_returns_rows_as_dicts(self) -> None:
        from database import database as db_mod

        engine = self._engine_returning(
            [{"event_id": 1, "event_type": "cpi_yoy", "release_date": "2026-03-12"}]
        )
        out = db_mod.get_events_in_window(
            engine, "cpi_yoy", "2026-01-01", "2026-03-31"
        )
        assert out == [
            {"event_id": 1, "event_type": "cpi_yoy", "release_date": "2026-03-12"}
        ]

    def test_empty_window_returns_empty_list(self) -> None:
        from database import database as db_mod

        engine = self._engine_returning([])
        out = db_mod.get_events_in_window(
            engine, "nfp", "2026-01-01", "2026-01-31"
        )
        assert out == []

    def test_query_bound_with_event_type_and_window(self) -> None:
        """The helper passes event_type + ISO-normalised window bounds to the
        single query it issues."""
        from database import database as db_mod

        engine = self._engine_returning([])
        db_mod.get_events_in_window(engine, "fomc_decision", "2026-01-01", "2026-06-30")

        conn = engine.connect.return_value.__enter__.return_value
        conn.execute.assert_called_once()
        _, params = conn.execute.call_args[0]
        assert params == {
            "event_type": "fomc_decision",
            "start_date": "2026-01-01",
            "end_date": "2026-06-30",
        }
