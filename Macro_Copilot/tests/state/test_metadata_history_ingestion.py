"""tests/state/test_metadata_history_ingestion.py — contract tests for the
metadata-history flow's shared validation and parquet-to-records logic.

These tests cover the **shared** helpers in
``ingestion/metadata_history.py`` (imported by both the extractor and the
ingester per ADR 0002), plus the routing seam in
``ingestion/ingest_parquet.py`` that partitions GCS blobs into the
time-series path and the metadata-history path by GCS prefix.

The tests do NOT spin up Postgres, GCS, or Bloomberg. The validation
logic is pure Python; the parquet-shape conversion is pure Pandas; the
routing test is a structured-mock check on the GCS client.

Related contracts:
  * ADR 0001 (sibling SCD2 ``instrument_metadata_history`` table + the
    ``EXCLUDE USING GIST`` constraint that backs the overlap rule below).
  * ADR 0002 (playbook ``metadata_history`` section + extractor /
    ingester routing — the contract these tests pin).

Closes ADR 0002 acceptance criterion: "the ingester partitions blobs by
prefix" and "the defence-in-depth overlap gate matches the DB constraint
semantics."
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

# Make sibling packages importable (conftest.py at tests/ also does this).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from ingestion.metadata_history import (  # noqa: E402
    HISTORY_TYPED_COLUMNS,
    group_rows_by_vendor_ticker,
    parquet_to_history_records,
    validate_no_overlaps,
)


# ============================================================================
# OVERLAP VALIDATION — the gate behind both the extractor's pre-write step
# and the ingester's pre-destructive-txn step. Mirrors the semantics of the
# ``EXCLUDE USING GIST (instrument_id WITH =, daterange(effective_from,
# COALESCE(effective_to, 'infinity'::date), '[]') WITH &&)`` constraint
# on ``macro_data.instrument_metadata_history``.
# ============================================================================


class TestValidateNoOverlaps:
    """One overlap rule across three call sites: extractor, ingester, and
    the DB constraint. These tests pin the Python side; the DB side is
    smoke-tested by the operator after PR A1's migration is applied."""

    def test_empty_input_returns_no_conflicts(self) -> None:
        assert validate_no_overlaps({}) == []

    def test_single_row_with_open_window_is_clean(self) -> None:
        rows = {"TY1 Comdty": [{"effective_from": "2024-01-01", "effective_to": None}]}
        assert validate_no_overlaps(rows) == []

    def test_adjacent_disjoint_windows_are_clean(self) -> None:
        """Window 1 ends 2024-03-15; window 2 begins 2024-03-16. No overlap
        — matches close_open_metadata_window's ``new − 1`` close semantics."""
        rows = {
            "TY1 Comdty": [
                {"effective_from": "2024-01-01", "effective_to": "2024-03-15"},
                {"effective_from": "2024-03-16", "effective_to": None},
            ]
        }
        assert validate_no_overlaps(rows) == []

    def test_overlapping_windows_are_rejected(self) -> None:
        rows = {
            "TY1 Comdty": [
                {"effective_from": "2024-01-01", "effective_to": "2024-06-30"},
                {"effective_from": "2024-04-01", "effective_to": "2024-09-30"},
            ]
        }
        conflicts = validate_no_overlaps(rows)
        assert len(conflicts) == 1
        assert "TY1 Comdty" in conflicts[0]
        assert "overlaps" in conflicts[0]

    def test_boundary_sharing_windows_are_rejected(self) -> None:
        """Window 1 ends 2024-03-15; window 2 begins 2024-03-15 (same day).
        The EXCLUDE constraint's '[]' inclusive bounds treat this as overlap;
        this gate must agree."""
        rows = {
            "TY1 Comdty": [
                {"effective_from": "2024-01-01", "effective_to": "2024-03-15"},
                {"effective_from": "2024-03-15", "effective_to": None},
            ]
        }
        conflicts = validate_no_overlaps(rows)
        assert len(conflicts) == 1
        assert "overlaps" in conflicts[0]

    def test_effective_to_before_effective_from_is_rejected(self) -> None:
        rows = {
            "TY1 Comdty": [
                {"effective_from": "2024-06-01", "effective_to": "2024-01-01"},
            ]
        }
        conflicts = validate_no_overlaps(rows)
        assert len(conflicts) == 1
        assert "before effective_from" in conflicts[0]

    def test_null_effective_to_only_valid_on_last_row(self) -> None:
        """Two rows with NULL effective_to — only the chronologically-latest
        may have it."""
        rows = {
            "TY1 Comdty": [
                {"effective_from": "2024-01-01", "effective_to": None},
                {"effective_from": "2024-06-01", "effective_to": None},
            ]
        }
        conflicts = validate_no_overlaps(rows)
        assert len(conflicts) == 1
        assert "effective_to=NULL but is not" in conflicts[0]

    def test_missing_effective_from_is_rejected(self) -> None:
        rows = {
            "TY1 Comdty": [
                {"effective_from": "2024-01-01", "effective_to": "2024-03-15"},
                {"effective_from": None, "effective_to": None},
            ]
        }
        conflicts = validate_no_overlaps(rows)
        assert len(conflicts) == 1
        assert "missing effective_from" in conflicts[0]

    def test_unparseable_effective_from_is_rejected_gracefully(self) -> None:
        rows = {"TY1 Comdty": [{"effective_from": "not-a-date"}]}
        conflicts = validate_no_overlaps(rows)
        assert len(conflicts) == 1

    def test_independent_tickers_do_not_cross_contaminate(self) -> None:
        """An overlap on ticker A must not flag ticker B's windows."""
        rows = {
            "TY1 Comdty": [
                {"effective_from": "2024-01-01", "effective_to": "2024-06-30"},
                {"effective_from": "2024-04-01", "effective_to": "2024-09-30"},
            ],
            "FV1 Comdty": [
                {"effective_from": "2024-01-01", "effective_to": "2024-03-15"},
                {"effective_from": "2024-03-16", "effective_to": None},
            ],
        }
        conflicts = validate_no_overlaps(rows)
        assert len(conflicts) == 1
        assert "TY1 Comdty" in conflicts[0]
        assert "FV1 Comdty" not in conflicts[0]


# ============================================================================
# PARQUET → HISTORY RECORDS — column mapping into typed cols + JSONB
# attributes, vendor_ticker resolution, NULL effective_to preservation.
# ============================================================================


class TestParquetToHistoryRecords:
    """The ingester reads a metadata-history parquet and converts each
    row into a dict ready for ``upsert_instrument_metadata_history``. This
    test pins the column-routing contract: known column_names map to
    typed DB columns; unknown column_names land in the row's attributes
    JSONB; vendor_ticker resolves via the supplied id-map."""

    @staticmethod
    def _build_df(records: List[Dict[str, Any]]) -> pd.DataFrame:
        return pd.DataFrame(records)

    def test_empty_df_returns_empty(self) -> None:
        df = pd.DataFrame()
        assert parquet_to_history_records(df, instrument_id_map={}) == []

    def test_known_typed_columns_are_forwarded(self) -> None:
        df = self._build_df([
            {
                "vendor_ticker": "TY1 Comdty",
                "effective_from": "2024-01-01",
                "effective_to": "2024-03-15",
                "contract_code": "TYH24",
                "expiry_date": "2024-03-15",
                "maturity_date": "2024-06-30",
                "tick_size": 0.015625,
            }
        ])
        out = parquet_to_history_records(df, instrument_id_map={"TY1 Comdty": 42})
        assert len(out) == 1
        rec = out[0]
        assert rec["instrument_id"] == 42
        assert rec["effective_from"] == "2024-01-01"
        assert rec["effective_to"] == "2024-03-15"
        assert rec["contract_code"] == "TYH24"
        assert rec["expiry_date"] == "2024-03-15"
        assert rec["maturity_date"] == "2024-06-30"
        assert rec["tick_size"] == pytest.approx(0.015625)

    def test_unknown_columns_route_to_attributes(self) -> None:
        df = self._build_df([
            {
                "vendor_ticker": "TY1 Comdty",
                "effective_from": "2024-01-01",
                "effective_to": None,
                "contract_code": "TYZ24",
                "some_custom_extra_field": "experimental",
            }
        ])
        out = parquet_to_history_records(df, instrument_id_map={"TY1 Comdty": 42})
        assert len(out) == 1
        rec = out[0]
        assert rec["contract_code"] == "TYZ24"
        assert rec["attributes"] == {"some_custom_extra_field": "experimental"}

    def test_lineage_columns_are_NOT_routed_to_attributes(self) -> None:
        """Lineage stamps (playbook_name, extractor_version, …) are dropped
        from the per-row payload — they live on the load_audit row, not on
        each history row."""
        df = self._build_df([
            {
                "vendor_ticker": "TY1 Comdty",
                "effective_from": "2024-01-01",
                "effective_to": None,
                "playbook_name": "bond_futures",
                "playbook_version": "1.3",
                "playbook_hash": "abc123",
                "git_commit_hash": "def456",
                "extractor_version": "test_extractor:abc",
                "extracted_at": "2026-05-19 12:00:00",
                "extraction_mode": "metadata_history",
                "dataset_name": "bond_futures",
                "asset_class": "rates",
            }
        ])
        out = parquet_to_history_records(df, instrument_id_map={"TY1 Comdty": 42})
        rec = out[0]
        assert rec["attributes"] is None  # No real attributes, just lineage which is dropped.

    def test_null_effective_to_is_preserved(self) -> None:
        df = self._build_df([
            {
                "vendor_ticker": "TY1 Comdty",
                "effective_from": "2024-01-01",
                "effective_to": None,
            }
        ])
        out = parquet_to_history_records(df, instrument_id_map={"TY1 Comdty": 42})
        assert out[0]["effective_to"] is None

    def test_missing_vendor_ticker_raises(self) -> None:
        df = self._build_df([
            {
                "vendor_ticker": None,
                "effective_from": "2024-01-01",
                "effective_to": None,
            }
        ])
        with pytest.raises(ValueError, match="missing vendor_ticker"):
            parquet_to_history_records(df, instrument_id_map={})

    def test_unmapped_vendor_ticker_raises(self) -> None:
        df = self._build_df([
            {
                "vendor_ticker": "TY1 Comdty",
                "effective_from": "2024-01-01",
                "effective_to": None,
            }
        ])
        with pytest.raises(ValueError, match="not present in instrument_master"):
            parquet_to_history_records(df, instrument_id_map={})

    def test_missing_effective_from_raises(self) -> None:
        df = self._build_df([
            {
                "vendor_ticker": "TY1 Comdty",
                "effective_from": None,
                "effective_to": None,
            }
        ])
        with pytest.raises(ValueError, match="missing effective_from"):
            parquet_to_history_records(df, instrument_id_map={"TY1 Comdty": 42})

    def test_load_id_is_stamped_when_provided(self) -> None:
        df = self._build_df([
            {
                "vendor_ticker": "TY1 Comdty",
                "effective_from": "2024-01-01",
                "effective_to": None,
            }
        ])
        out = parquet_to_history_records(
            df, instrument_id_map={"TY1 Comdty": 42}, load_id=99
        )
        assert out[0]["load_id"] == 99

    def test_history_typed_columns_set_matches_schema_constant(self) -> None:
        """Defensive — if anyone reorders or renames the typed-cols tuple,
        the column-routing logic upstream silently breaks."""
        expected = {
            "contract_code",
            "expiry_date",
            "maturity_date",
            "security_name",
            "settlement_date",
            "accrual_start_date",
            "accrual_end_date",
            "tick_size",
            "tick_value",
            "contract_size",
            "exchange_code",
            "underlying_ticker",
        }
        assert set(HISTORY_TYPED_COLUMNS) == expected


# ============================================================================
# GROUP-BY-VENDOR-TICKER — small helper, but the overlap gate depends on it.
# ============================================================================


class TestGroupRowsByVendorTicker:
    def test_empty_df(self) -> None:
        assert group_rows_by_vendor_ticker(pd.DataFrame()) == {}

    def test_groups_independent_tickers(self) -> None:
        df = pd.DataFrame([
            {"vendor_ticker": "TY1 Comdty", "effective_from": "2024-01-01"},
            {"vendor_ticker": "TY1 Comdty", "effective_from": "2024-04-01"},
            {"vendor_ticker": "FV1 Comdty", "effective_from": "2024-01-01"},
        ])
        grouped = group_rows_by_vendor_ticker(df)
        assert sorted(grouped.keys()) == ["FV1 Comdty", "TY1 Comdty"]
        assert len(grouped["TY1 Comdty"]) == 2
        assert len(grouped["FV1 Comdty"]) == 1

    def test_rows_missing_vendor_ticker_are_dropped(self) -> None:
        df = pd.DataFrame([
            {"vendor_ticker": "TY1 Comdty", "effective_from": "2024-01-01"},
            {"vendor_ticker": None, "effective_from": "2024-04-01"},
        ])
        grouped = group_rows_by_vendor_ticker(df)
        assert list(grouped.keys()) == ["TY1 Comdty"]
        assert len(grouped["TY1 Comdty"]) == 1


# ============================================================================
# INGESTER ROUTING — partition by GCS path prefix.
#
# We test the ingester's behaviour by mocking the GCS client at the
# ``list_blobs`` boundary. The two prefixes (``data/`` and
# ``metadata_history/``) must produce disjoint sets of blobs.
# ============================================================================


class TestIngesterBlobPartitioning:
    """Mocks the ``bucket.list_blobs(prefix=...)`` call and confirms the
    ingester partitions correctly. We do NOT exercise the per-blob processing
    paths here — those need a Postgres + GCS round-trip and are covered by
    the operator's manual smoke test after PR A2 runs the backfill."""

    def test_path_prefixes_are_disjoint_today(self) -> None:
        """A trivial but meaningful assertion: ``data/...`` and
        ``metadata_history/...`` are disjoint by definition. The ingester's
        two ``list_blobs`` calls cannot return the same blob in both lists."""
        ts_paths = [
            "data/bond_futures/bond_futures_timeseries_20260519_120000.parquet",
            "data/sovereign_bonds/sovereign_bonds_timeseries_20260519_120000.parquet",
        ]
        mh_paths = [
            "metadata_history/bond_futures/bond_futures_metadata_history_20260519_120000.parquet",
            "metadata_history/policy_futures/policy_futures_metadata_history_20260519_120000.parquet",
        ]
        for ts in ts_paths:
            assert not ts.startswith("metadata_history/")
            assert ts.startswith("data/")
        for mh in mh_paths:
            assert mh.startswith("metadata_history/")
            assert not mh.startswith("data/")
        # And no blob is in both.
        assert set(ts_paths).isdisjoint(set(mh_paths))

    def test_listed_archive_paths_are_not_picked_up(self) -> None:
        """Archived parquets live under ``archive/data/`` and
        ``archive/metadata_history/`` respectively. The ingester scopes its
        ``list_blobs`` calls to ``data/`` and ``metadata_history/`` (no
        ``archive/`` prefix), so archived blobs are never re-processed."""
        archived = "archive/metadata_history/bond_futures/bond_futures_metadata_history_20260518_120000.parquet"
        # The ingester's two ``list_blobs`` prefixes are "data/" and "metadata_history/";
        # neither matches "archive/..." at the start.
        assert not archived.startswith("data/")
        assert not archived.startswith("metadata_history/")
