"""tests/state/test_event_calendar_ingestion.py — the event-calendar
ingestion-side pure helpers (work order B2, ADR 0004).

``ingestion/event_calendar.py`` is pure Python — no Bloomberg, no GCS, no DB —
so the parquet → records parser and the ingestability gate are unit-testable in
isolation. These tests pin:

  * ``parquet_to_event_records`` parses each event category (economic release /
    central-bank meeting / auction), normalising dates → ISO strings,
    ``release_time`` → ``HH:MM:SS``, numerics → float|None, ``attributes`` JSON
    → dict, and lower-casing ``event_category``.
  * ``is_ingestable_event`` admits a well-formed row and rejects rows missing a
    natural-key field or carrying an ``event_category`` outside the closed set.

The DB-side ``_normalize_event_record`` / ``upsert_event_calendar`` (B1) are
the enforcing source of truth and are exercised by
``test_event_calendar_substrate.py``; this file covers the parquet-side prep.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ingestion.event_calendar import (  # noqa: E402
    EVENT_CATEGORIES,
    is_ingestable_event,
    parquet_to_event_records,
)


def _one(record: dict) -> dict:
    """Parse a one-row DataFrame and return the single parsed record."""
    recs = parquet_to_event_records(pd.DataFrame([record]))
    assert len(recs) == 1
    return recs[0]


# ============================================================================
# parquet_to_event_records — economic release
# ============================================================================
class TestParseEconomicRelease:
    def test_economic_release_row(self):
        rec = _one({
            "event_type": "cpi_yoy",
            "event_category": "economic_release",
            "country": "US",
            "currency": "USD",
            "release_date": "2026-03-12",
            "release_time": "13:30:00",
            "period": "Feb 2026",
            "actual": 3.1,
            "consensus_median": 3.0,
            "consensus_high": 3.3,
            "consensus_low": 2.8,
            "prior": 3.0,
        })
        assert rec["event_type"] == "cpi_yoy"
        assert rec["event_category"] == "economic_release"
        assert rec["country"] == "US"
        assert rec["release_date"] == "2026-03-12"
        assert rec["release_time"] == "13:30:00"
        assert rec["period"] == "Feb 2026"
        assert rec["actual"] == 3.1
        assert rec["consensus_median"] == 3.0
        # Auction-only columns are absent → None.
        assert rec["high_yield"] is None
        assert rec["tail_bps"] is None

    def test_event_category_is_lowercased(self):
        rec = _one({
            "event_type": "nfp",
            "event_category": "Economic_Release",
            "country": "US",
            "release_date": "2026-03-06",
        })
        assert rec["event_category"] == "economic_release"

    def test_timestamp_release_date_normalised_to_iso(self):
        rec = _one({
            "event_type": "cpi_yoy",
            "event_category": "economic_release",
            "country": "US",
            "release_date": pd.Timestamp("2026-03-12 13:30:00"),
        })
        assert rec["release_date"] == "2026-03-12"

    def test_nan_numerics_become_none(self):
        rec = _one({
            "event_type": "cpi_yoy",
            "event_category": "economic_release",
            "country": "US",
            "release_date": "2026-03-12",
            "actual": float("nan"),
            "consensus_median": None,
        })
        assert rec["actual"] is None
        assert rec["consensus_median"] is None

    def test_release_time_short_form_padded(self):
        rec = _one({
            "event_type": "cpi_yoy",
            "event_category": "economic_release",
            "country": "US",
            "release_date": "2026-03-12",
            "release_time": "8:30",
        })
        assert rec["release_time"] == "08:30:00"

    def test_missing_release_time_is_none(self):
        rec = _one({
            "event_type": "cpi_yoy",
            "event_category": "economic_release",
            "country": "US",
            "release_date": "2026-03-12",
        })
        assert rec["release_time"] is None


# ============================================================================
# parquet_to_event_records — auction & central-bank meeting
# ============================================================================
class TestParseAuctionAndMeeting:
    def test_auction_row(self):
        rec = _one({
            "event_type": "ust_auction_10y",
            "event_category": "auction",
            "country": "US",
            "release_date": "2026-03-11",
            "high_yield": 4.215,
            "bid_to_cover": 2.51,
            "tail_bps": 0.4,
            "indirect_pct": 68.2,
        })
        assert rec["event_category"] == "auction"
        assert rec["high_yield"] == 4.215
        assert rec["bid_to_cover"] == 2.51
        assert rec["tail_bps"] == 0.4
        assert rec["indirect_pct"] == 68.2
        # Economic-release columns are absent → None.
        assert rec["actual"] is None
        assert rec["consensus_median"] is None

    def test_central_bank_meeting_row_with_attributes(self):
        rec = _one({
            "event_type": "fomc_decision",
            "event_category": "central_bank_meeting",
            "country": "US",
            "central_bank": "FOMC",
            "release_date": "2026-03-18",
            "related_instrument_id": 4242,
            "attributes": '{"decision": "hold", "target_upper": 4.5}',
        })
        assert rec["event_category"] == "central_bank_meeting"
        assert rec["central_bank"] == "FOMC"
        assert rec["related_instrument_id"] == 4242
        assert rec["attributes"] == {"decision": "hold", "target_upper": 4.5}

    def test_malformed_attributes_json_becomes_none(self):
        rec = _one({
            "event_type": "fomc_decision",
            "event_category": "central_bank_meeting",
            "country": "US",
            "release_date": "2026-03-18",
            "attributes": "{not valid json",
        })
        assert rec["attributes"] is None

    def test_dict_attributes_passed_through(self):
        rec = _one({
            "event_type": "fomc_decision",
            "event_category": "central_bank_meeting",
            "country": "US",
            "release_date": "2026-03-18",
            "attributes": {"decision": "cut"},
        })
        assert rec["attributes"] == {"decision": "cut"}


# ============================================================================
# is_ingestable_event — the natural-key + closed-family gate
# ============================================================================
class TestIsIngestableEvent:
    _VALID = {
        "event_type": "cpi_yoy",
        "event_category": "economic_release",
        "country": "US",
        "release_date": "2026-03-12",
    }

    def test_valid_record_is_ingestable(self):
        rec = _one(self._VALID)
        assert is_ingestable_event(rec)

    @pytest.mark.parametrize("missing", ["event_type", "country", "release_date"])
    def test_missing_natural_key_field_is_rejected(self, missing):
        row = dict(self._VALID)
        row.pop(missing)
        assert not is_ingestable_event(_one(row))

    def test_event_category_outside_closed_family_is_rejected(self):
        row = dict(self._VALID)
        row["event_category"] = "speech"  # not in EVENT_CATEGORIES
        assert not is_ingestable_event(_one(row))

    def test_all_three_categories_are_ingestable(self):
        for category, etype in [
            ("economic_release", "cpi_yoy"),
            ("central_bank_meeting", "fomc_decision"),
            ("auction", "ust_auction_10y"),
        ]:
            rec = _one({
                "event_type": etype,
                "event_category": category,
                "country": "US",
                "release_date": "2026-03-12",
            })
            assert rec["event_category"] in EVENT_CATEGORIES
            assert is_ingestable_event(rec)


# ============================================================================
# _process_event_blob — the PHASE 5 ingestion route (ADR 0008 + Codex finding 4)
#
# ``ingestion/ingest_parquet.py`` imports the Google Cloud Storage SDK at module
# load, so the fixture stubs ``google.cloud`` in ``sys.modules`` before import,
# mirroring test_otr_resolution_ingestion.py. The DB helpers are monkeypatched
# so no Postgres is touched.
# ============================================================================
@pytest.fixture
def ingest_parquet(monkeypatch):
    for name in ("google", "google.cloud", "google.cloud.storage"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    import ingestion.ingest_parquet as ip  # noqa: WPS433

    return ip


def _valid_event_df(**overrides):
    """A one-row, well-formed event-calendar parquet frame."""
    row = {
        "event_type": "cpi_yoy",
        "event_category": "economic_release",
        "country": "US",
        "currency": "USD",
        "release_date": "2026-03-12",
        "actual": 3.1,
        "playbook_name": "economic_releases",
        "playbook_version": "1.0",
        "playbook_hash": "pbhash",
        "dataset_name": "economic_releases",
        "extraction_mode": "event_calendar",
        "extracted_at": "2026-05-21 10:00:00",
        "requested_start_date": "2024-01-01",
        "requested_end_date": "2026-05-21",
    }
    row.update(overrides)
    return pd.DataFrame([row])


class _Capture:
    """Collects the DB-helper calls _process_event_blob makes."""

    def __init__(self):
        self.audit_status = []   # (status, ...) tuples
        self.upserted = []       # records passed to upsert_event_calendar
        self.load_audit_inserted = False
        self.skipped_duplicate = False


def _wire(ip, monkeypatch, df, *, latest_success=None, data_hash="HASH1"):
    """Monkeypatch _process_event_blob's collaborators; return a _Capture."""
    cap = _Capture()
    monkeypatch.setattr(ip, "_compute_normalized_data_hash", lambda d: data_hash)
    monkeypatch.setattr(ip.pd, "read_parquet", lambda *a, **k: df)
    monkeypatch.setattr(
        ip, "get_latest_successful_load_for_playbook",
        lambda engine, name: latest_success,
    )

    def _insert(engine, rec):
        cap.load_audit_inserted = True
        return 999

    monkeypatch.setattr(ip, "insert_load_audit", _insert)
    monkeypatch.setattr(
        ip, "update_load_audit_status",
        lambda conn, load_id, status, notes: cap.audit_status.append(status),
    )

    def _skip_dup(**kwargs):
        cap.skipped_duplicate = True

    monkeypatch.setattr(ip, "mark_load_audit_skipped_duplicate", _skip_dup)
    monkeypatch.setattr(
        ip, "upsert_event_calendar",
        lambda conn, recs: cap.upserted.extend(recs),
    )
    return cap


def _blob(name="events/economic_releases/test.parquet"):
    blob = MagicMock()
    blob.name = name
    return blob


class TestProcessEventBlob:
    def test_invalid_extraction_mode_is_skipped(self, ingest_parquet, monkeypatch, tmp_path):
        df = _valid_event_df(extraction_mode="time-series")
        cap = _wire(ingest_parquet, monkeypatch, df)
        ok = ingest_parquet._process_event_blob(
            MagicMock(), _blob(), MagicMock(), tmp_path
        )
        assert ok is False
        assert not cap.load_audit_inserted  # rejected before any audit row

    def test_missing_playbook_name_fails_without_audit_row(self, ingest_parquet, monkeypatch, tmp_path):
        df = _valid_event_df()
        df = df.drop(columns=["playbook_name"])
        cap = _wire(ingest_parquet, monkeypatch, df)
        ok = ingest_parquet._process_event_blob(
            MagicMock(), _blob(), MagicMock(), tmp_path
        )
        assert ok is False
        assert not cap.load_audit_inserted  # no junk "unknown" load row

    def test_duplicate_hash_is_skipped(self, ingest_parquet, monkeypatch, tmp_path):
        df = _valid_event_df()
        cap = _wire(
            ingest_parquet, monkeypatch, df,
            latest_success={"source_file_hash": "HASH1"}, data_hash="HASH1",
        )
        ok = ingest_parquet._process_event_blob(
            MagicMock(), _blob(), MagicMock(), tmp_path
        )
        assert ok is True
        assert cap.skipped_duplicate
        assert not cap.load_audit_inserted
        assert not cap.upserted

    def test_malformed_row_fails_the_whole_load(self, ingest_parquet, monkeypatch, tmp_path):
        # Two rows: one valid, one missing event_type. Per Codex finding 4 the
        # whole load FAILS — no partial ingest, no SUCCESS.
        good = _valid_event_df().iloc[0].to_dict()
        bad = dict(good)
        bad["event_type"] = None
        df = pd.DataFrame([good, bad])
        cap = _wire(ingest_parquet, monkeypatch, df)
        ok = ingest_parquet._process_event_blob(
            MagicMock(), _blob(), MagicMock(), tmp_path
        )
        assert ok is False
        assert cap.load_audit_inserted
        assert cap.audit_status == ["FAILED"]
        assert not cap.upserted  # nothing ingested

    def test_well_formed_artifact_ingests_and_succeeds(self, ingest_parquet, monkeypatch, tmp_path):
        df = _valid_event_df()
        cap = _wire(ingest_parquet, monkeypatch, df)
        ok = ingest_parquet._process_event_blob(
            MagicMock(), _blob(), MagicMock(), tmp_path
        )
        assert ok is True
        assert cap.load_audit_inserted
        assert cap.audit_status == ["SUCCESS"]
        assert len(cap.upserted) == 1
        assert cap.upserted[0]["event_type"] == "cpi_yoy"
        assert cap.upserted[0]["load_id"] == 999
