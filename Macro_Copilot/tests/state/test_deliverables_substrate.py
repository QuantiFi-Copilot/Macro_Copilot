"""tests/state/test_deliverables_substrate.py — contract tests for the
futures-deliverables substrate (ADR 0011, work order C3).

C3 ships the DB-side substrate for bond-future deliverable baskets:
``macro_data.futures_deliverables`` (schema.sql Section 8), the
``_normalize_deliverables_record`` helper, and the ``upsert_futures_deliverables``
writer (full-row UPSERT on the natural key
``(instrument_id, contract_code, deliverable_cusip)``).

These tests pin the Python side of that substrate:

  * ``_normalize_deliverables_record`` validates the three natural-key fields,
    shapes a record into the uniform key set ``futures_deliverables`` expects,
    casts FK columns to int.
  * ``upsert_futures_deliverables`` honours the ``_txn``-Connectable contract
    (handed a ``Connection`` it runs on the caller's transaction and never
    opens a sub-transaction) and targets the
    ``(instrument_id, contract_code, deliverable_cusip)`` natural key.

No Postgres is touched. The normalisation logic is pure Python; the upsert
test stubs the SQLAlchemy ``Table`` reflection and ``insert`` builder exactly
as ``test_event_calendar_substrate.py`` does.

Related contracts:
  * ADR 0011 — ``docs_revamped/05_decisions/0011-futures-deliverables-substrate.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Connection

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from database.database import (  # noqa: E402
    _normalize_deliverables_record,
)


# ============================================================================
# _normalize_deliverables_record — natural-key validation + uniform shaping
# ============================================================================


class TestNormalizeDeliverablesRecord:
    """``_normalize_deliverables_record`` validates the natural-key triple
    and shapes a record into the uniform key set futures_deliverables expects."""

    def _valid(self, **overrides: Any) -> dict:
        rec = {
            "instrument_id": 7,
            "contract_code": "TYZ24",
            "deliverable_cusip": "91282CAB1",
        }
        rec.update(overrides)
        return rec

    def test_valid_record_is_shaped(self) -> None:
        out = _normalize_deliverables_record(self._valid())
        assert out["instrument_id"] == 7
        assert out["contract_code"] == "TYZ24"
        assert out["deliverable_cusip"] == "91282CAB1"

    def test_optional_columns_default_to_none(self) -> None:
        out = _normalize_deliverables_record(self._valid())
        for col in (
            "deliverable_isin",
            "deliverable_instrument_id",
            "conversion_factor",
            "first_delivery_date",
            "last_delivery_date",
            "first_notice_date",
            "last_notice_date",
            "attributes",
            "load_id",
        ):
            assert out[col] is None, f"{col} should default to None"

    def test_typed_value_fields_carried_through(self) -> None:
        out = _normalize_deliverables_record(
            self._valid(
                deliverable_isin="US91282CAB12",
                conversion_factor=0.8234,
                first_delivery_date="2024-12-02",
                last_delivery_date="2024-12-31",
                first_notice_date="2024-11-29",
                last_notice_date="2024-12-30",
            )
        )
        assert out["deliverable_isin"] == "US91282CAB12"
        assert out["conversion_factor"] == 0.8234
        assert out["first_delivery_date"] == "2024-12-02"
        assert out["last_delivery_date"] == "2024-12-31"
        assert out["first_notice_date"] == "2024-11-29"
        assert out["last_notice_date"] == "2024-12-30"

    def test_fk_columns_cast_to_int(self) -> None:
        """instrument_id / deliverable_instrument_id / load_id are FK integers —
        cast when present (a parquet round-trip can yield numpy int64 / str)."""
        out = _normalize_deliverables_record(
            self._valid(
                instrument_id="7",
                deliverable_instrument_id="2236",
                load_id="42",
            )
        )
        assert out["instrument_id"] == 7
        assert isinstance(out["instrument_id"], int)
        assert out["deliverable_instrument_id"] == 2236
        assert isinstance(out["deliverable_instrument_id"], int)
        assert out["load_id"] == 42
        assert isinstance(out["load_id"], int)

    def test_uniform_key_set_across_records(self) -> None:
        """Two records with disjoint optional fields produce the same key
        set — required for a coherent bulk insert column list."""
        a = _normalize_deliverables_record(self._valid())
        b = _normalize_deliverables_record(
            self._valid(conversion_factor=0.9, deliverable_isin="US...", load_id=5)
        )
        assert set(a.keys()) == set(b.keys())

    def test_missing_instrument_id_raises(self) -> None:
        rec = self._valid()
        del rec["instrument_id"]
        with pytest.raises(ValueError, match="instrument_id"):
            _normalize_deliverables_record(rec)

    def test_missing_contract_code_raises(self) -> None:
        rec = self._valid()
        del rec["contract_code"]
        with pytest.raises(ValueError, match="contract_code"):
            _normalize_deliverables_record(rec)

    def test_missing_deliverable_cusip_raises(self) -> None:
        rec = self._valid()
        del rec["deliverable_cusip"]
        with pytest.raises(ValueError, match="deliverable_cusip"):
            _normalize_deliverables_record(rec)

    def test_zero_instrument_id_does_not_raise(self) -> None:
        """``instrument_id == 0`` is unusual but technically valid as an int
        FK; the normaliser only rejects None / missing, not zero."""
        out = _normalize_deliverables_record(self._valid(instrument_id=0))
        assert out["instrument_id"] == 0


# ============================================================================
# upsert_futures_deliverables — _txn-Connectable contract
# ============================================================================


class _FakeColumn:
    """Reflected-column stand-in: a ``.name`` plus attribute assignment (the
    ``none_as_null`` type override sets ``.type``). Mirrors
    test_event_calendar_substrate's _FakeColumn."""

    def __init__(self, name: str):
        self.name = name


class _FakeColumns:
    """Stand-in for a reflected Table's ``.c``: iterable and attribute-
    addressable."""

    def __init__(self, names: List[str]):
        self._cols = [_FakeColumn(n) for n in names]
        for col in self._cols:
            setattr(self, col.name, col)

    def __iter__(self):
        return iter(self._cols)


_DELIVERABLES_COLUMN_NAMES = [
    "deliverable_id",
    "instrument_id",
    "contract_code",
    "deliverable_cusip",
    "deliverable_isin",
    "deliverable_instrument_id",
    "conversion_factor",
    "first_delivery_date",
    "last_delivery_date",
    "first_notice_date",
    "last_notice_date",
    "attributes",
    "load_id",
    "created_at",
]


def _make_connection_stub() -> MagicMock:
    """Connection-like stub that records ``.execute`` and must never see
    ``.begin()`` (the helper must reuse the caller's transaction)."""
    conn = MagicMock(spec=Connection)
    conn.begin = MagicMock()  # MUST NOT be called
    result = MagicMock()
    result.rowcount = 1
    conn.execute.return_value = result
    return conn


class TestUpsertFuturesDeliverables:
    """``upsert_futures_deliverables(connection, records)`` runs its
    INSERT-ON-CONFLICT on the caller's connection and opens no sub-transaction."""

    def test_empty_records_is_a_noop(self) -> None:
        from database import database as db_mod

        conn = _make_connection_stub()
        assert db_mod.upsert_futures_deliverables(conn, []) == 0
        conn.execute.assert_not_called()
        conn.begin.assert_not_called()

    def test_upsert_under_connection_does_not_begin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from database import database as db_mod

        conn = _make_connection_stub()

        fake_table = MagicMock()
        fake_table.c = _FakeColumns(_DELIVERABLES_COLUMN_NAMES)
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: fake_table)

        fake_stmt = MagicMock()
        fake_stmt.values.return_value = fake_stmt
        fake_stmt.on_conflict_do_update.return_value = "<deliverables_upsert_stmt>"
        monkeypatch.setattr(db_mod, "insert", lambda *a, **k: fake_stmt)

        affected = db_mod.upsert_futures_deliverables(
            conn,
            [
                {
                    "instrument_id": 7,
                    "contract_code": "TYZ24",
                    "deliverable_cusip": "91282CAB1",
                }
            ],
        )

        assert affected == 1
        conn.execute.assert_called_once_with("<deliverables_upsert_stmt>")
        conn.begin.assert_not_called()
        # Natural-key UPSERT.
        _, kwargs = fake_stmt.on_conflict_do_update.call_args
        assert kwargs["index_elements"] == [
            "instrument_id", "contract_code", "deliverable_cusip",
        ]

    def test_on_conflict_overwrites_every_non_key_column(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FULL-ROW UPSERT contract: ON CONFLICT DO UPDATE sets EVERY non-key,
        non-immutable column from ``excluded``. No surgical COALESCE exception
        applies (cf. ADR 0009 §5 on event_calendar.related_instrument_id) —
        a futures_deliverables row is written by ONE pipeline (the
        deliverables/ ingester route, which itself populates
        deliverable_instrument_id from a CUSIP lookup before writing)."""
        from database import database as db_mod

        conn = _make_connection_stub()
        fake_table = MagicMock()
        fake_table.c = _FakeColumns(_DELIVERABLES_COLUMN_NAMES)
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: fake_table)

        fake_stmt = MagicMock()
        fake_stmt.values.return_value = fake_stmt
        fake_stmt.on_conflict_do_update.return_value = "<stmt>"
        monkeypatch.setattr(db_mod, "insert", lambda *a, **k: fake_stmt)

        db_mod.upsert_futures_deliverables(
            conn,
            [
                {
                    "instrument_id": 7,
                    "contract_code": "TYZ24",
                    "deliverable_cusip": "91282CAB1",
                }
            ],
        )

        _, kwargs = fake_stmt.on_conflict_do_update.call_args
        update_keys = set(kwargs["set_"].keys())
        # The natural key + the immutable identity / audit columns are excluded;
        # EVERY other column must be in the update set.
        key_and_immutable = {
            "deliverable_id",
            "instrument_id",
            "contract_code",
            "deliverable_cusip",
            "created_at",
        }
        expected = set(_DELIVERABLES_COLUMN_NAMES) - key_and_immutable
        assert update_keys == expected, (
            "ON CONFLICT DO UPDATE must overwrite every non-key column — "
            "this is the full-row-upsert contract"
        )

    def test_invalid_record_raises_before_any_execute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A record missing a required field fails in
        _normalize_deliverables_record — no DB statement is executed."""
        from database import database as db_mod

        conn = _make_connection_stub()
        fake_table = MagicMock()
        fake_table.c = _FakeColumns(_DELIVERABLES_COLUMN_NAMES)
        monkeypatch.setattr(db_mod, "Table", lambda *a, **k: fake_table)
        monkeypatch.setattr(db_mod, "insert", lambda *a, **k: MagicMock())

        with pytest.raises(ValueError, match="deliverable_cusip"):
            db_mod.upsert_futures_deliverables(
                conn,
                [{"instrument_id": 7, "contract_code": "TYZ24"}],
            )
        conn.execute.assert_not_called()
