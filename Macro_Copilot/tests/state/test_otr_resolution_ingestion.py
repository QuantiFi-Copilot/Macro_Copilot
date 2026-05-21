"""tests/state/test_otr_resolution_ingestion.py — the ingestion-side applier
for the OTR-resolution flow (work order A4-4, ADR 0007).

``_apply_otr_plan`` is the thin dispatcher that turns an ``OtrTransitionPlan``
(from the pure planner, tested in test_otr_resolution_planner.py) into calls on
the sanctioned ``upsert_otr_history`` / ``close_open_otr_window`` helpers. These
tests pin that dispatch: the right helper, the right arguments, the right order
— with the DB helpers mocked, so no Postgres is touched.

``ingestion/ingest_parquet.py`` imports the Google Cloud Storage SDK at module
load, so the fixture stubs ``google.cloud`` in ``sys.modules`` before importing
the module — mirroring test_cash_bond_substrate.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ingestion.otr_resolution import (  # noqa: E402
    ACTION_BOOTSTRAP,
    ACTION_CLEAR_CANDIDATE,
    ACTION_CONFIRM_ROLL,
    ACTION_NO_CHANGE,
    ACTION_RECORD_CANDIDATE,
    ACTION_SKIP_STALE,
    OtrTransitionPlan,
)


@pytest.fixture
def ingest_parquet(monkeypatch):
    """Import ``ingestion.ingest_parquet`` with the GCS SDK stubbed out."""
    for name in ("google", "google.cloud", "google.cloud.storage"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    import ingestion.ingest_parquet as ip  # noqa: WPS433

    return ip


_OBS = {"country": "US", "tenor": "10Y"}
_OPEN_ROW = {"effective_from": "2026-02-01", "otr_instrument_id": 100}


class TestApplyOtrPlan:
    def test_bootstrap_inserts_one_window(self, ingest_parquet, monkeypatch):
        upsert = MagicMock()
        close = MagicMock()
        monkeypatch.setattr(ingest_parquet, "upsert_otr_history", upsert)
        monkeypatch.setattr(ingest_parquet, "close_open_otr_window", close)

        plan = OtrTransitionPlan(
            action=ACTION_BOOTSTRAP,
            reason="baseline",
            new_effective_from="2026-05-21",
            new_otr_instrument_id=100,
            new_attributes={"otr_resolution": {"mode": "bootstrap"}},
        )
        ingest_parquet._apply_otr_plan(MagicMock(), plan, _OBS, None, load_id=7)

        close.assert_not_called()
        upsert.assert_called_once()
        record = upsert.call_args[0][1][0]
        assert record["country"] == "US"
        assert record["tenor"] == "10Y"
        assert record["effective_from"] == "2026-05-21"
        assert record["otr_instrument_id"] == 100
        assert record["effective_to"] is None
        assert record["load_id"] == 7

    @pytest.mark.parametrize("action", [ACTION_NO_CHANGE, ACTION_SKIP_STALE])
    def test_no_change_and_skip_stale_are_noops(self, ingest_parquet, monkeypatch, action):
        upsert = MagicMock()
        close = MagicMock()
        monkeypatch.setattr(ingest_parquet, "upsert_otr_history", upsert)
        monkeypatch.setattr(ingest_parquet, "close_open_otr_window", close)

        plan = OtrTransitionPlan(action=action, reason="noop")
        ingest_parquet._apply_otr_plan(MagicMock(), plan, _OBS, _OPEN_ROW, load_id=7)

        upsert.assert_not_called()
        close.assert_not_called()

    def test_record_candidate_reupserts_the_open_row(self, ingest_parquet, monkeypatch):
        upsert = MagicMock()
        close = MagicMock()
        monkeypatch.setattr(ingest_parquet, "upsert_otr_history", upsert)
        monkeypatch.setattr(ingest_parquet, "close_open_otr_window", close)

        new_attrs = {"pending_candidate": {"instrument_id": 200}}
        plan = OtrTransitionPlan(
            action=ACTION_RECORD_CANDIDATE,
            reason="awaiting confirmation",
            open_row_attributes=new_attrs,
        )
        ingest_parquet._apply_otr_plan(MagicMock(), plan, _OBS, _OPEN_ROW, load_id=7)

        close.assert_not_called()
        record = upsert.call_args[0][1][0]
        # Re-upserts the OPEN row in place: its own effective_from is the key,
        # the window stays open, only the attributes blob changes.
        assert record["effective_from"] == "2026-02-01"
        assert record["otr_instrument_id"] == 100
        assert record["effective_to"] is None
        assert record["attributes"] == new_attrs

    def test_clear_candidate_reupserts_the_open_row(self, ingest_parquet, monkeypatch):
        upsert = MagicMock()
        monkeypatch.setattr(ingest_parquet, "upsert_otr_history", upsert)
        monkeypatch.setattr(ingest_parquet, "close_open_otr_window", MagicMock())

        plan = OtrTransitionPlan(
            action=ACTION_CLEAR_CANDIDATE,
            reason="reverted",
            open_row_attributes={},
        )
        ingest_parquet._apply_otr_plan(MagicMock(), plan, _OBS, _OPEN_ROW, load_id=7)
        record = upsert.call_args[0][1][0]
        assert record["effective_from"] == "2026-02-01"
        assert record["attributes"] == {}

    def test_confirm_roll_closes_before_opening(self, ingest_parquet, monkeypatch):
        # A shared parent records call ORDER: the prior window MUST be closed
        # before the new one is inserted, or the EXCLUDE GIST constraint trips.
        parent = MagicMock()
        monkeypatch.setattr(ingest_parquet, "upsert_otr_history", parent.upsert)
        monkeypatch.setattr(ingest_parquet, "close_open_otr_window", parent.close)

        plan = OtrTransitionPlan(
            action=ACTION_CONFIRM_ROLL,
            reason="confirmed",
            close_prior_at="2026-05-21",
            new_effective_from="2026-05-21",
            new_otr_instrument_id=200,
            new_attributes={"otr_resolution": {"mode": "roll"}},
        )
        conn = MagicMock()
        ingest_parquet._apply_otr_plan(conn, plan, _OBS, _OPEN_ROW, load_id=9)

        assert parent.mock_calls[0] == call.close(conn, "US", "10Y", "2026-05-21")
        record = parent.upsert.call_args[0][1][0]
        assert record["effective_from"] == "2026-05-21"
        assert record["otr_instrument_id"] == 200
        assert record["effective_to"] is None
        assert record["load_id"] == 9

    def test_unknown_action_raises(self, ingest_parquet):
        plan = OtrTransitionPlan(action="WAT", reason="bad")
        with pytest.raises(ValueError, match="Unknown OTR transition plan action"):
            ingest_parquet._apply_otr_plan(MagicMock(), plan, _OBS, _OPEN_ROW, load_id=1)
