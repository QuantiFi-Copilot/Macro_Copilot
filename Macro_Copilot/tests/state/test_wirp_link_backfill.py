"""tests/state/test_wirp_link_backfill.py — Stage A of the D-wirp increment
(ADR 0009 §5): the durable cross-pipeline `related_instrument_id` link.

Two halves, both tested here:

  * `upsert_event_calendar` COALESCE-preserves `related_instrument_id`
    (ADR 0009 §5b) — on a natural-key conflict that one column is set to
    `COALESCE(EXCLUDED.related_instrument_id, event_calendar.related_instrument_id)`,
    so a D-cb re-upsert (which always carries the column NULL) cannot wipe a
    backfilled WIRP link. Every OTHER column keeps the full-row-overwrite
    contract.

  * `utils/backfill_wirp_links.py` (ADR 0009 §5a) — the local, idempotent step
    that sets `event_calendar.related_instrument_id` for central-bank-meeting
    rows, joining on `maturity_date` + `attributes->>'central_bank'` and
    linking ONLY WIRP instruments that have `market_data_daily` rows.

No Postgres is touched — the upsert test stubs the SQLAlchemy `Table`
reflection and `insert` builder (as `test_event_calendar_substrate.py` does),
and the backfill test drives `backfill_wirp_links` with a mocked engine.

Related contracts: ADR 0009 (D-wirp), ADR 0004 (`event_calendar` substrate).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import TextClause

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.backfill_wirp_links import (  # noqa: E402
    _BACKFILL_SQL,
    _COUNT_WIRP_SQL,
    _ORPHAN_WIRP_SQL,
    _PREVIEW_LINKS_SQL,
    backfill_wirp_links,
)


# ============================================================================
# upsert_event_calendar — related_instrument_id COALESCE preservation
# ============================================================================
#
# Stubs mirror tests/state/test_event_calendar_substrate.py so this Stage-A
# change is verified self-containedly.


class _FakeColumn:
    """Reflected-column stand-in: a `.name` plus attribute assignment (the
    `none_as_null` type override sets `.type`)."""

    def __init__(self, name: str):
        self.name = name


class _FakeColumns:
    """Stand-in for a reflected Table's `.c`: iterable and attribute-addressable."""

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
    """Connection-like stub that records `.execute` and must never `.begin()`."""
    conn = MagicMock(spec=Connection)
    conn.begin = MagicMock()  # MUST NOT be called
    result = MagicMock()
    result.rowcount = 1
    conn.execute.return_value = result
    return conn


def _build_event_upsert_set(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Run upsert_event_calendar with Table/insert stubbed; return the
    `set_` dict handed to on_conflict_do_update."""
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
                "event_type": "fomc_decision",
                "event_category": "central_bank_meeting",
                "country": "US",
                "release_date": "2026-06-17",
            }
        ],
    )
    _, kwargs = fake_stmt.on_conflict_do_update.call_args
    return kwargs["set_"]


class TestRelatedInstrumentIdPreservation:
    """`upsert_event_calendar` COALESCE-preserves `related_instrument_id`."""

    def test_related_instrument_id_is_a_coalesce_clause(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The column's ON CONFLICT value is COALESCE(EXCLUDED.x, table.x) —
        the incoming value wins only when non-NULL, else the stored link
        is kept."""
        set_ = _build_event_upsert_set(monkeypatch)
        clause = set_["related_instrument_id"]
        assert isinstance(clause, TextClause), (
            "related_instrument_id must be a COALESCE text clause"
        )
        sql = str(clause).lower().replace(" ", "")
        assert "coalesce(" in sql
        assert "excluded.related_instrument_id" in sql
        assert "event_calendar.related_instrument_id" in sql

    def test_excluded_comes_first_in_coalesce(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EXCLUDED (the incoming value) is the FIRST COALESCE arg, so a
        non-NULL incoming value still overwrites; the stored value is only
        the fallback."""
        set_ = _build_event_upsert_set(monkeypatch)
        sql = str(set_["related_instrument_id"]).lower()
        assert sql.index("excluded.") < sql.index("event_calendar."), (
            "EXCLUDED must precede event_calendar so a non-NULL incoming "
            "value still wins"
        )

    def test_every_other_non_key_column_is_plain_overwrite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exception is surgical: every non-key column EXCEPT
        related_instrument_id is still a plain `excluded` overwrite — a
        wrongly-set field can still be corrected back to NULL."""
        set_ = _build_event_upsert_set(monkeypatch)
        key_and_immutable = {
            "event_id", "event_type", "country", "release_date", "created_at",
        }
        non_key = set(_EVENT_COLUMN_NAMES) - key_and_immutable
        for col in non_key - {"related_instrument_id"}:
            assert not isinstance(set_[col], TextClause), (
                f"{col} must remain a plain `excluded` overwrite"
            )

    def test_all_non_key_columns_still_covered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exception adds no column and drops none — the update set is
        still exactly the non-key, non-immutable columns."""
        set_ = _build_event_upsert_set(monkeypatch)
        key_and_immutable = {
            "event_id", "event_type", "country", "release_date", "created_at",
        }
        assert set(set_.keys()) == set(_EVENT_COLUMN_NAMES) - key_and_immutable


# ============================================================================
# backfill_wirp_links — SQL shape
# ============================================================================


class TestBackfillSQLShape:
    """The SQL constants encode the ADR 0009 §5a contract."""

    def test_backfill_is_an_update_of_related_instrument_id(self) -> None:
        sql = _BACKFILL_SQL
        assert "UPDATE macro_data.event_calendar" in sql
        assert "SET related_instrument_id = im.instrument_id" in sql

    def test_backfill_targets_only_wirp_meeting_central_bank_rows(self) -> None:
        sql = _BACKFILL_SQL
        assert "instrument_type = 'wirp_meeting'" in sql
        assert "event_category  = 'central_bank_meeting'" in sql

    def test_backfill_joins_on_maturity_date_and_attributes_central_bank(
        self,
    ) -> None:
        """instrument_master has no typed central_bank/meeting_date column —
        the join uses maturity_date + attributes->>'central_bank' (ADR 0009 §1)."""
        sql = _BACKFILL_SQL
        assert "ec.release_date    = im.maturity_date" in sql
        assert "ec.central_bank    = im.attributes->>'central_bank'" in sql

    def test_backfill_links_only_instruments_with_market_data(self) -> None:
        """The EXISTS gate (ADR 0009 §5a / Codex finding 6) — never link an
        orphan instrument that has no market_data_daily rows."""
        sql = _BACKFILL_SQL
        assert "EXISTS (" in sql
        assert "macro_data.market_data_daily md" in sql
        assert "md.instrument_id = im.instrument_id" in sql

    def test_backfill_is_idempotent(self) -> None:
        """IS DISTINCT FROM makes an already-correct link a no-op on re-run."""
        assert "ec.related_instrument_id IS DISTINCT FROM im.instrument_id" in _BACKFILL_SQL

    def test_preview_is_a_select_not_a_mutation(self) -> None:
        assert _PREVIEW_LINKS_SQL.strip().upper().startswith("SELECT")
        assert "UPDATE" not in _PREVIEW_LINKS_SQL.upper()
        assert "DELETE" not in _PREVIEW_LINKS_SQL.upper()

    def test_orphan_query_finds_wirp_instruments_without_market_data(self) -> None:
        assert "NOT EXISTS" in _ORPHAN_WIRP_SQL
        assert "wirp_meeting" in _ORPHAN_WIRP_SQL
        assert "market_data_daily" in _ORPHAN_WIRP_SQL

    def test_count_query_counts_wirp_meeting_instruments(self) -> None:
        assert "COUNT(*)" in _COUNT_WIRP_SQL
        assert "instrument_type = 'wirp_meeting'" in _COUNT_WIRP_SQL


# ============================================================================
# backfill_wirp_links — flow
# ============================================================================


def _make_engine(
    *,
    wirp_count: int,
    orphans: List[dict],
    pending: List[dict],
    update_rowcount: int = 0,
):
    """Mock engine routing execute() by SQL: count -> scalar, orphan/preview ->
    mappings().all(), UPDATE -> rowcount. Returns (engine, read_conn, write_conn)."""

    def _read_execute(stmt: Any, *a: Any, **k: Any) -> MagicMock:
        sql = str(stmt)
        result = MagicMock()
        if "COUNT(*)" in sql:
            result.scalar.return_value = wirp_count
        elif "NOT EXISTS" in sql:
            result.mappings.return_value.all.return_value = orphans
        else:  # the preview SELECT
            result.mappings.return_value.all.return_value = pending
        return result

    def _write_execute(stmt: Any, *a: Any, **k: Any) -> MagicMock:
        result = MagicMock()
        result.rowcount = update_rowcount
        return result

    read_conn = MagicMock()
    read_conn.execute.side_effect = _read_execute
    write_conn = MagicMock()
    write_conn.execute.side_effect = _write_execute

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = read_conn
    engine.connect.return_value.__exit__.return_value = False
    engine.begin.return_value.__enter__.return_value = write_conn
    engine.begin.return_value.__exit__.return_value = False
    return engine, read_conn, write_conn


class TestBackfillWirpLinksFlow:
    """`backfill_wirp_links(engine, dry_run=...)` orchestration."""

    def test_no_wirp_instruments_is_a_clean_noop(self) -> None:
        """With no wirp_meeting instruments the step returns early — no
        orphan/preview query, no transaction."""
        engine, read_conn, _ = _make_engine(
            wirp_count=0, orphans=[], pending=[]
        )
        summary = backfill_wirp_links(engine, dry_run=False)

        assert summary["wirp_instruments"] == 0
        assert summary["linked"] == 0
        engine.begin.assert_not_called()
        # Only the COUNT query ran.
        assert read_conn.execute.call_count == 1

    def test_dry_run_issues_no_update(self) -> None:
        """A dry run reads the candidates but opens no write transaction."""
        engine, _, write_conn = _make_engine(
            wirp_count=4,
            orphans=[],
            pending=[
                {
                    "event_id": 1, "event_type": "fomc_decision",
                    "central_bank": "FOMC", "release_date": "2026-06-17",
                    "current_link": None, "wirp_instrument_id": 900,
                    "wirp_vendor_ticker": "WIRP:FOMC:2026-06-17",
                },
            ],
        )
        summary = backfill_wirp_links(engine, dry_run=True)

        assert summary["dry_run"] is True
        assert summary["pending_links"] == 1
        assert summary["linked"] == 0
        engine.begin.assert_not_called()
        write_conn.execute.assert_not_called()

    def test_apply_runs_the_update_and_reports_rowcount(self) -> None:
        """A real run opens the write transaction, issues the UPDATE, and
        reports the row count it affected."""
        engine, _, write_conn = _make_engine(
            wirp_count=4,
            orphans=[],
            pending=[
                {
                    "event_id": 1, "event_type": "fomc_decision",
                    "central_bank": "FOMC", "release_date": "2026-06-17",
                    "current_link": None, "wirp_instrument_id": 900,
                    "wirp_vendor_ticker": "WIRP:FOMC:2026-06-17",
                },
                {
                    "event_id": 2, "event_type": "ecb_decision",
                    "central_bank": "ECB", "release_date": "2026-06-04",
                    "current_link": None, "wirp_instrument_id": 901,
                    "wirp_vendor_ticker": "WIRP:ECB:2026-06-04",
                },
            ],
            update_rowcount=2,
        )
        summary = backfill_wirp_links(engine, dry_run=False)

        assert summary["linked"] == 2
        assert summary["pending_links"] == 2
        engine.begin.assert_called_once()
        write_conn.execute.assert_called_once()
        executed_sql = str(write_conn.execute.call_args[0][0])
        assert "UPDATE macro_data.event_calendar" in executed_sql

    def test_all_already_linked_skips_the_update(self) -> None:
        """When every linkable row is already correct, no transaction opens."""
        engine, _, write_conn = _make_engine(
            wirp_count=4, orphans=[], pending=[]
        )
        summary = backfill_wirp_links(engine, dry_run=False)

        assert summary["linked"] == 0
        assert summary["pending_links"] == 0
        engine.begin.assert_not_called()
        write_conn.execute.assert_not_called()

    def test_orphan_instruments_are_reported_not_linked(self) -> None:
        """WIRP instruments with no market data are surfaced in the summary
        and are NOT linked."""
        engine, _, _ = _make_engine(
            wirp_count=3,
            orphans=[
                {"instrument_id": 950, "vendor_ticker": "WIRP:BOJ:2027-01-23"},
            ],
            pending=[],
        )
        summary = backfill_wirp_links(engine, dry_run=False)

        assert summary["orphan_instruments"] == 1
        assert summary["linked"] == 0
