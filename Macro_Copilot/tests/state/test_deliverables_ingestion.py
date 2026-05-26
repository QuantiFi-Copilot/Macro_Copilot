"""tests/state/test_deliverables_ingestion.py — tests for the deliverables
ingestion-side helpers (ADR 0011, work order C3).

C3's ingester route consumes a wide deliverables parquet via
``ingestion.deliverables.parquet_to_deliverables_records`` (the pure parser)
and ``is_ingestable_deliverable`` (the gate). These tests pin both, plus the
grouping helper, against the contract documented in ADR 0011 §Design — shared
validation module.

No Postgres, no GCS, no Bloomberg — these helpers are pure functions of their
inputs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from ingestion.deliverables import (  # noqa: E402
    DELIVERABLES_TYPED_COLUMNS,
    group_rows_by_contract,
    is_ingestable_deliverable,
    parquet_to_deliverables_records,
    validate_per_contract_dates,
)


# ============================================================================
# parquet_to_deliverables_records — vendor_ticker resolution + typed mapping
# ============================================================================


_INSTRUMENT_ID_MAP = {"TY1 Comdty": 100, "RX1 Comdty": 200}


def _basic_row(**overrides):
    rec = {
        "vendor_ticker": "TY1 Comdty",
        "contract_code": "TYZ24",
        "deliverable_cusip": "91282CAB1",
        "deliverable_isin": "US91282CAB12",
        "conversion_factor": 0.8234,
        "first_delivery_date": "2024-12-02",
        "last_delivery_date": "2024-12-31",
        "first_notice_date": "2024-11-29",
        "last_notice_date": "2024-12-30",
    }
    rec.update(overrides)
    return rec


class TestParquetToDeliverablesRecords:
    def test_empty_df_returns_empty_list(self) -> None:
        assert parquet_to_deliverables_records(pd.DataFrame(), _INSTRUMENT_ID_MAP, 7) == []

    def test_vendor_ticker_is_resolved_to_instrument_id(self) -> None:
        df = pd.DataFrame([_basic_row()])
        recs = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP, load_id=5)
        assert recs[0]["instrument_id"] == 100

    def test_unknown_vendor_ticker_raises(self) -> None:
        df = pd.DataFrame([_basic_row(vendor_ticker="UNKNOWN1 Comdty")])
        with pytest.raises(ValueError, match="not present in instrument_master"):
            parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)

    def test_missing_vendor_ticker_raises(self) -> None:
        df = pd.DataFrame([{
            **_basic_row(),
            "vendor_ticker": None,
        }])
        with pytest.raises(ValueError, match="missing vendor_ticker"):
            parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)

    def test_natural_key_columns_carried_through(self) -> None:
        df = pd.DataFrame([_basic_row()])
        rec = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)[0]
        assert rec["contract_code"] == "TYZ24"
        assert rec["deliverable_cusip"] == "91282CAB1"

    def test_typed_columns_mapped(self) -> None:
        """Every column in DELIVERABLES_TYPED_COLUMNS is forwarded to the
        matching key on the record dict."""
        df = pd.DataFrame([_basic_row()])
        rec = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)[0]
        for col in DELIVERABLES_TYPED_COLUMNS:
            assert col in rec, f"typed column {col!r} must appear on the record"
        assert rec["deliverable_isin"] == "US91282CAB12"
        assert rec["conversion_factor"] == 0.8234

    def test_dates_normalised_to_iso_strings(self) -> None:
        """A parquet date column round-trips as ``pd.Timestamp``; the parser
        normalises to ISO ``YYYY-MM-DD``."""
        df = pd.DataFrame([
            {
                **_basic_row(),
                "first_delivery_date": pd.Timestamp("2024-12-02"),
                "last_delivery_date": pd.Timestamp("2024-12-31"),
            }
        ])
        rec = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)[0]
        assert rec["first_delivery_date"] == "2024-12-02"
        assert rec["last_delivery_date"] == "2024-12-31"

    def test_unparseable_dates_become_none(self) -> None:
        df = pd.DataFrame([{
            **_basic_row(),
            "first_delivery_date": "not-a-date",
            "last_delivery_date": None,
        }])
        rec = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)[0]
        assert rec["first_delivery_date"] is None
        assert rec["last_delivery_date"] is None

    def test_deliverable_instrument_id_is_left_to_route(self) -> None:
        """The parser is DB-free; the optional CUSIP -> instrument_master FK is
        resolved by the ingester route, not here."""
        df = pd.DataFrame([_basic_row()])
        rec = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)[0]
        assert rec["deliverable_instrument_id"] is None

    def test_non_typed_columns_land_in_attributes(self) -> None:
        df = pd.DataFrame([{
            **_basic_row(),
            "custom_metric": "spike",
            "another_extra": 1.5,
        }])
        rec = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)[0]
        assert isinstance(rec["attributes"], dict)
        assert rec["attributes"]["custom_metric"] == "spike"
        assert rec["attributes"]["another_extra"] == 1.5

    def test_lineage_columns_excluded_from_attributes(self) -> None:
        """playbook_name etc. are lineage stamps, not row metadata — they
        must NOT leak into the attributes blob."""
        df = pd.DataFrame([{
            **_basic_row(),
            "playbook_name": "bond_futures",
            "playbook_version": "1.6",
            "extracted_at": "2026-05-23 18:00:00",
            "extraction_mode": "deliverables",
        }])
        rec = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)[0]
        # Empty attributes -> stored as None (cleaner JSONB).
        assert rec["attributes"] is None

    def test_load_id_stamped_when_provided(self) -> None:
        df = pd.DataFrame([_basic_row()])
        rec = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP, load_id=42)[0]
        assert rec["load_id"] == 42

    def test_load_id_absent_when_not_provided(self) -> None:
        df = pd.DataFrame([_basic_row()])
        rec = parquet_to_deliverables_records(df, _INSTRUMENT_ID_MAP)[0]
        assert "load_id" not in rec


# ============================================================================
# is_ingestable_deliverable — natural-key gate
# ============================================================================


class TestIsIngestableDeliverable:
    def test_valid_record(self) -> None:
        assert is_ingestable_deliverable({
            "instrument_id": 7,
            "contract_code": "TYZ24",
            "deliverable_cusip": "91282CAB1",
        }) is True

    def test_missing_instrument_id_is_not_ingestable(self) -> None:
        assert is_ingestable_deliverable({
            "instrument_id": None,
            "contract_code": "TYZ24",
            "deliverable_cusip": "91282CAB1",
        }) is False

    def test_zero_instrument_id_is_not_ingestable(self) -> None:
        """Non-positive instrument_id can never be a real FK — gate it out."""
        assert is_ingestable_deliverable({
            "instrument_id": 0,
            "contract_code": "TYZ24",
            "deliverable_cusip": "91282CAB1",
        }) is False

    def test_missing_contract_code_is_not_ingestable(self) -> None:
        assert is_ingestable_deliverable({
            "instrument_id": 7,
            "contract_code": "",
            "deliverable_cusip": "91282CAB1",
        }) is False

    def test_missing_deliverable_cusip_is_not_ingestable(self) -> None:
        assert is_ingestable_deliverable({
            "instrument_id": 7,
            "contract_code": "TYZ24",
            "deliverable_cusip": None,
        }) is False


# ============================================================================
# group_rows_by_contract
# ============================================================================


class TestGroupRowsByContract:
    def test_groups_by_vendor_ticker_and_contract_code(self) -> None:
        df = pd.DataFrame([
            _basic_row(deliverable_cusip="A"),
            _basic_row(deliverable_cusip="B"),
            _basic_row(vendor_ticker="RX1 Comdty", contract_code="RXZ24",
                       deliverable_cusip="C"),
        ])
        grouped = group_rows_by_contract(df)
        assert ("TY1 Comdty", "TYZ24") in grouped
        assert ("RX1 Comdty", "RXZ24") in grouped
        assert len(grouped[("TY1 Comdty", "TYZ24")]) == 2
        assert len(grouped[("RX1 Comdty", "RXZ24")]) == 1

    def test_empty_df_returns_empty_dict(self) -> None:
        assert group_rows_by_contract(pd.DataFrame()) == {}

    def test_rows_missing_keys_are_dropped(self) -> None:
        df = pd.DataFrame([
            _basic_row(),
            {"vendor_ticker": None, "contract_code": "TYZ24", "deliverable_cusip": "X"},
            {"vendor_ticker": "TY1 Comdty", "contract_code": None, "deliverable_cusip": "Y"},
        ])
        grouped = group_rows_by_contract(df)
        assert len(grouped) == 1
        assert ("TY1 Comdty", "TYZ24") in grouped


# ============================================================================
# validate_per_contract_dates — ADR 0011 v2 (Codex finding 5)
# ============================================================================


def _rec(instrument_id: int = 100, contract_code: str = "TYZ24",
         deliverable_cusip: str = "A",
         **overrides) -> dict:
    rec = {
        "instrument_id": instrument_id,
        "contract_code": contract_code,
        "deliverable_cusip": deliverable_cusip,
        "first_delivery_date": "2024-12-02",
        "last_delivery_date":  "2024-12-31",
        "first_notice_date":   "2024-11-29",
        "last_notice_date":    "2024-12-30",
    }
    rec.update(overrides)
    return rec


class TestValidatePerContractDates:
    def test_empty_records_returns_empty(self) -> None:
        assert validate_per_contract_dates([]) == []

    def test_single_basket_with_consistent_dates_is_clean(self) -> None:
        recs = [
            _rec(deliverable_cusip="A"),
            _rec(deliverable_cusip="B"),
            _rec(deliverable_cusip="C"),
        ]
        assert validate_per_contract_dates(recs) == []

    def test_single_row_basket_is_clean(self) -> None:
        """A basket with one row trivially can't disagree with itself."""
        assert validate_per_contract_dates([_rec()]) == []

    def test_two_contracts_disjoint_baskets_are_independent(self) -> None:
        recs = [
            _rec(instrument_id=100, contract_code="TYZ24", deliverable_cusip="A"),
            _rec(instrument_id=100, contract_code="TYZ24", deliverable_cusip="B"),
            _rec(instrument_id=200, contract_code="RXZ24", deliverable_cusip="C",
                 first_delivery_date="2025-01-10"),
            _rec(instrument_id=200, contract_code="RXZ24", deliverable_cusip="D",
                 first_delivery_date="2025-01-10"),
        ]
        assert validate_per_contract_dates(recs) == []

    def test_inconsistent_first_delivery_date_is_reported(self) -> None:
        recs = [
            _rec(deliverable_cusip="A", first_delivery_date="2024-12-02"),
            _rec(deliverable_cusip="B", first_delivery_date="2024-12-03"),
        ]
        conflicts = validate_per_contract_dates(recs)
        assert len(conflicts) == 1
        assert "instrument_id=100" in conflicts[0]
        assert "TYZ24" in conflicts[0]
        assert "first_delivery_date" in conflicts[0]
        # Both values must appear in the message for diagnosis.
        assert "2024-12-02" in conflicts[0]
        assert "2024-12-03" in conflicts[0]

    def test_all_four_date_columns_checked(self) -> None:
        """Each of the four denormalised date columns is individually verified;
        a conflict on any one is reported."""
        for col in (
            "first_delivery_date", "last_delivery_date",
            "first_notice_date",   "last_notice_date",
        ):
            recs = [
                _rec(deliverable_cusip="A", **{col: "2024-01-01"}),
                _rec(deliverable_cusip="B", **{col: "2024-02-02"}),
            ]
            conflicts = validate_per_contract_dates(recs)
            assert any(col in c for c in conflicts), f"missed conflict in {col}"

    def test_none_vs_value_is_a_conflict(self) -> None:
        """``None`` and a real date are distinct values — surface it."""
        recs = [
            _rec(deliverable_cusip="A", first_delivery_date=None),
            _rec(deliverable_cusip="B", first_delivery_date="2024-12-02"),
        ]
        conflicts = validate_per_contract_dates(recs)
        assert len(conflicts) == 1
        # Rendered as NULL for human readability.
        assert "NULL" in conflicts[0]

    def test_records_missing_natural_key_are_ignored(self) -> None:
        """Missing-key records are caught by ``is_ingestable_deliverable`` —
        this validator must not double-report them."""
        recs = [
            _rec(),
            {"instrument_id": None, "contract_code": "TYZ24", "deliverable_cusip": "X"},
        ]
        assert validate_per_contract_dates(recs) == []


# ============================================================================
# _process_deliverables_blob — the PHASE 6 ingestion route
#
# Mirrors test_event_calendar_ingestion.py's TestProcessEventBlob. The GCS SDK
# is stubbed before importing the module; DB calls are monkeypatched and the
# SQLAlchemy reflection / select chain returns controlled rows via SimpleNamespace.
# ============================================================================


@pytest.fixture
def ingest_parquet(monkeypatch):
    for name in ("google", "google.cloud", "google.cloud.storage"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    import ingestion.ingest_parquet as ip  # noqa: WPS433
    return ip


def _valid_deliverables_df(**overrides):
    """One-row, well-formed deliverables parquet frame."""
    row = {
        "vendor_ticker":      "TY1 Comdty",
        "contract_code":      "TYZ24",
        "deliverable_cusip":  "91282CAB1",
        "deliverable_isin":   "US91282CAB12",
        "conversion_factor":  0.8234,
        "first_delivery_date": "2024-12-02",
        "last_delivery_date":  "2024-12-31",
        "first_notice_date":   "2024-11-29",
        "last_notice_date":    "2024-12-30",
        "playbook_name":      "bond_futures__deliverables",
        "playbook_version":   "1.0",
        "playbook_hash":      "pbhash",
        "dataset_name":       "bond_futures",
        "extraction_mode":    "deliverables",
        "extracted_at":       "2026-05-23 18:00:00",
        "requested_start_date": "2024-01-01",
        "requested_end_date":   "2026-05-23",
        "vendor":             "BLOOMBERG",
    }
    row.update(overrides)
    return pd.DataFrame([row])


class _Capture:
    def __init__(self):
        self.audit_status = []
        self.upserted = []
        self.load_audit_inserted = False
        self.skipped_duplicate = False


def _make_fake_engine(vendor_rows, cusip_rows):
    """Fake engine whose two `engine.begin()` lookup blocks return the right
    rows in order. The third begin() block (upsert + audit) needs only
    context-manager behaviour, which MagicMock provides by default."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.fetchall.side_effect = [vendor_rows, cusip_rows]
    return engine


def _wire_deliverables(ip, monkeypatch, df, *, latest_success=None,
                       data_hash="HASH1",
                       vendor_rows=None, cusip_rows=None):
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
        ip, "upsert_futures_deliverables",
        lambda conn, recs: cap.upserted.extend(recs),
    )
    # Stub SQLAlchemy reflection / select so no DB metadata fetch occurs.
    monkeypatch.setattr(ip, "MetaData", lambda *a, **k: MagicMock())
    monkeypatch.setattr(ip, "Table", lambda *a, **k: MagicMock())
    monkeypatch.setattr(ip, "select", lambda *a, **k: MagicMock())
    return cap


def _blob(name="deliverables/bond_futures/test.parquet"):
    blob = MagicMock()
    blob.name = name
    return blob


class TestProcessDeliverablesBlob:
    def test_invalid_extraction_mode_is_skipped(
        self, ingest_parquet, monkeypatch, tmp_path
    ) -> None:
        df = _valid_deliverables_df(extraction_mode="time-series")
        cap = _wire_deliverables(ingest_parquet, monkeypatch, df)
        engine = _make_fake_engine(
            [SimpleNamespace(vendor_ticker="TY1 Comdty", instrument_id=100)],
            [],
        )
        ok = ingest_parquet._process_deliverables_blob(
            MagicMock(), _blob(), engine, tmp_path
        )
        assert ok is False
        assert not cap.load_audit_inserted

    def test_missing_playbook_name_fails_without_audit_row(
        self, ingest_parquet, monkeypatch, tmp_path
    ) -> None:
        df = _valid_deliverables_df()
        df = df.drop(columns=["playbook_name"])
        cap = _wire_deliverables(ingest_parquet, monkeypatch, df)
        engine = _make_fake_engine([], [])
        ok = ingest_parquet._process_deliverables_blob(
            MagicMock(), _blob(), engine, tmp_path
        )
        assert ok is False
        assert not cap.load_audit_inserted

    def test_duplicate_hash_is_skipped(
        self, ingest_parquet, monkeypatch, tmp_path
    ) -> None:
        df = _valid_deliverables_df()
        cap = _wire_deliverables(
            ingest_parquet, monkeypatch, df,
            latest_success={"source_file_hash": "HASH1"}, data_hash="HASH1",
        )
        engine = _make_fake_engine([], [])
        ok = ingest_parquet._process_deliverables_blob(
            MagicMock(), _blob(), engine, tmp_path
        )
        assert ok is True
        assert cap.skipped_duplicate
        assert not cap.load_audit_inserted
        assert not cap.upserted

    def test_unknown_vendor_ticker_aborts(
        self, ingest_parquet, monkeypatch, tmp_path
    ) -> None:
        """A vendor_ticker absent from instrument_master fails the load
        before any record write."""
        df = _valid_deliverables_df()
        cap = _wire_deliverables(ingest_parquet, monkeypatch, df)
        # Vendor lookup returns no rows -> all tickers missing.
        engine = _make_fake_engine([], [])
        ok = ingest_parquet._process_deliverables_blob(
            MagicMock(), _blob(), engine, tmp_path
        )
        assert ok is False
        assert cap.load_audit_inserted
        assert cap.audit_status == ["FAILED"]
        assert not cap.upserted

    def test_per_contract_date_conflict_aborts(
        self, ingest_parquet, monkeypatch, tmp_path
    ) -> None:
        """ADR 0011 v2 / Codex finding 5: a basket whose denormalised
        delivery / notice dates disagree across rows fails the whole load."""
        good = _valid_deliverables_df().iloc[0].to_dict()
        conflicting = dict(good)
        conflicting["deliverable_cusip"] = "91282CXX0"
        conflicting["first_delivery_date"] = "2024-12-03"  # disagrees with row 1
        df = pd.DataFrame([good, conflicting])
        cap = _wire_deliverables(ingest_parquet, monkeypatch, df)
        engine = _make_fake_engine(
            [SimpleNamespace(vendor_ticker="TY1 Comdty", instrument_id=100)],
            [],  # cusip lookup never reached on a date conflict
        )
        ok = ingest_parquet._process_deliverables_blob(
            MagicMock(), _blob(), engine, tmp_path
        )
        assert ok is False
        assert cap.load_audit_inserted
        assert cap.audit_status == ["FAILED"]
        assert not cap.upserted, (
            "date-conflict abort must short-circuit before any upsert"
        )

    def test_well_formed_artifact_ingests_and_links_fk(
        self, ingest_parquet, monkeypatch, tmp_path
    ) -> None:
        df = _valid_deliverables_df()
        cap = _wire_deliverables(ingest_parquet, monkeypatch, df)
        engine = _make_fake_engine(
            [SimpleNamespace(vendor_ticker="TY1 Comdty", instrument_id=100)],
            [SimpleNamespace(cusip="91282CAB1", instrument_id=2236)],
        )
        ok = ingest_parquet._process_deliverables_blob(
            MagicMock(), _blob(), engine, tmp_path
        )
        assert ok is True
        assert cap.load_audit_inserted
        assert cap.audit_status == ["SUCCESS"]
        assert len(cap.upserted) == 1
        rec = cap.upserted[0]
        assert rec["instrument_id"] == 100
        assert rec["contract_code"] == "TYZ24"
        assert rec["deliverable_cusip"] == "91282CAB1"
        # Optional FK populated from the CUSIP lookup.
        assert rec["deliverable_instrument_id"] == 2236
        assert rec["load_id"] == 999

    def test_well_formed_artifact_with_unregistered_cusip_leaves_fk_null(
        self, ingest_parquet, monkeypatch, tmp_path
    ) -> None:
        """The common case: the deliverable bond is NOT registered in
        instrument_master (it's a seasoned issue outside the OTR universe).
        Leave deliverable_instrument_id NULL; the load still succeeds."""
        df = _valid_deliverables_df()
        cap = _wire_deliverables(ingest_parquet, monkeypatch, df)
        engine = _make_fake_engine(
            [SimpleNamespace(vendor_ticker="TY1 Comdty", instrument_id=100)],
            [],  # no CUSIP rows found
        )
        ok = ingest_parquet._process_deliverables_blob(
            MagicMock(), _blob(), engine, tmp_path
        )
        assert ok is True
        assert cap.audit_status == ["SUCCESS"]
        assert cap.upserted[0]["deliverable_instrument_id"] is None
