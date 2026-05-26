"""tests/state/test_otr_resolver_extraction.py — the Bloomberg-side OTR
resolver in ``utils/incremental_extractor.py`` (work order A4-4, ADR 0007).

Covers the resolver's pure logic — the ``otr_resolution`` block parser, the
per-slot row builder with its false-roll-defence sanity checks — plus an
end-to-end ``resolve_otr`` run with ``xbbg`` / GCS mocked.

``xbbg`` is patched in ``sys.modules`` BEFORE importing the extractor, per the
convention in ``test_extractor_dataframe_agnostic.py``, so this collects on a
machine with no Bloomberg terminal.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Stub xbbg AND google.cloud before importing the extractor: this test mocks
# the GCS bucket directly and never needs the real SDK. Stubbing google.cloud
# also keeps this module from doing a real (then patch.dict-unloaded) import of
# the cryptography rust bindings, which would collide with other test modules
# in the same interpreter ("PyO3 ... initialized once per process").
with patch.dict(
    sys.modules,
    {
        "xbbg": MagicMock(),
        "xbbg.blp": MagicMock(),
        "google": MagicMock(),
        "google.cloud": MagicMock(),
        "google.cloud.storage": MagicMock(),
    },
):
    from utils import incremental_extractor as _incr


def _wide_bdp(ticker: str, **fields) -> pd.DataFrame:
    """A classic-pandas (WIDE) xbbg bdp frame: indexed by ticker, field cols."""
    return pd.DataFrame({k: [v] for k, v in fields.items()}, index=[ticker])


# ============================================================================
# _resolve_otr_block — the declarative opt-in parser
# ============================================================================
class TestResolveOtrBlock:
    def test_absent_block_returns_none(self):
        assert _incr._resolve_otr_block({}) is None

    def test_disabled_block_returns_none(self):
        pb = {"otr_resolution": {"enabled": False, "slots": [{"generic_ticker": "GT10 Govt"}]}}
        assert _incr._resolve_otr_block(pb) is None

    def test_slotless_block_returns_none(self):
        pb = {"otr_resolution": {"enabled": True, "slots": []}}
        assert _incr._resolve_otr_block(pb) is None

    def test_enabled_block_is_normalised(self):
        pb = {
            "otr_resolution": {
                "enabled": True,
                "resolution_field": "id_isin",
                "confirmation_runs": 3,
                "reference_fields": ["security_des", "maturity"],
                "slots": [
                    {"slot_id": "US_10Y", "generic_ticker": "GT10 Govt"},
                    {"slot_id": "bad"},  # no generic_ticker — dropped
                ],
            }
        }
        block = _incr._resolve_otr_block(pb)
        assert block is not None
        assert block["resolution_field"] == "ID_ISIN"
        assert block["reference_fields"] == ["SECURITY_DES", "MATURITY"]
        assert block["confirmation_runs"] == 3
        assert len(block["slots"]) == 1

    def test_confirmation_runs_defaults_and_floors_at_one(self):
        pb = {"otr_resolution": {"enabled": True, "slots": [{"generic_ticker": "GT10 Govt"}]}}
        assert _incr._resolve_otr_block(pb)["confirmation_runs"] == 2
        pb["otr_resolution"]["confirmation_runs"] = 0
        assert _incr._resolve_otr_block(pb)["confirmation_runs"] == 1


# ============================================================================
# _build_resolution_row — per-slot row + sanity checks
# ============================================================================
class TestBuildResolutionRow:
    _SLOT = {
        "slot_id": "US_10Y",
        "generic_ticker": "GT10 Govt",
        "country": "US",
        "currency": "USD",
        "curve_family": "UST",
        "tenor": "10Y",
        "expected_isin_prefix": "US",
    }

    def test_ok_row(self):
        bdp = {
            "ID_ISIN": "US91282CQQ77",
            "ID_CUSIP": "91282CQQ7",
            "SECURITY_DES": "T 4 3/8 05/15/36",
            "CPN": 4.375,
            "MATURITY": "2036-05-15",
            "ISSUE_DT": "2026-05-15",
        }
        row = _incr._build_resolution_row(
            self._SLOT, "ID_ISIN", bdp, "2026-05-21", "2026-05-21 17:00:00"
        )
        assert row["status"] == "ok"
        assert row["resolved_isin"] == "US91282CQQ77"
        assert row["instrument_ticker"] == "/isin/US91282CQQ77"
        assert row["maturity_date"] == "2036-05-15"
        assert row["error"] is None

    def test_no_isin_is_failed(self):
        row = _incr._build_resolution_row(
            self._SLOT, "ID_ISIN", {}, "2026-05-21", "2026-05-21 17:00:00"
        )
        assert row["status"] == "failed"
        assert row["resolved_isin"] is None

    def test_isin_prefix_mismatch_is_rejected(self):
        # A DE ISIN under a US slot — a likely-corrupt bdp print; reject it.
        bdp = {"ID_ISIN": "DE000BU2Z064", "MATURITY": "2036-02-15"}
        row = _incr._build_resolution_row(
            self._SLOT, "ID_ISIN", bdp, "2026-05-21", "2026-05-21 17:00:00"
        )
        assert row["status"] == "rejected"
        assert "expected prefix" in row["error"]

    def test_already_matured_bond_is_rejected(self):
        bdp = {"ID_ISIN": "US0000000001", "MATURITY": "2020-01-01"}
        row = _incr._build_resolution_row(
            self._SLOT, "ID_ISIN", bdp, "2026-05-21", "2026-05-21 17:00:00"
        )
        assert row["status"] == "rejected"
        assert "matures" in row["error"]

    def test_currency_falls_back_to_crncy(self):
        slot = dict(self._SLOT)
        slot["currency"] = None
        bdp = {"ID_ISIN": "US91282CQQ77", "CRNCY": "USD", "MATURITY": "2036-05-15"}
        row = _incr._build_resolution_row(
            slot, "ID_ISIN", bdp, "2026-05-21", "2026-05-21 17:00:00"
        )
        assert row["currency"] == "USD"

    def test_implausible_tenor_is_rejected(self):
        # A 2Y slot resolving to a ~10Y bond — gross mis-resolution caught by
        # the maturity-plausibility band, not by the prefix / matured checks.
        slot = dict(self._SLOT)
        slot["tenor"] = "2Y"
        bdp = {"ID_ISIN": "US91282CQQ77", "MATURITY": "2036-05-15"}
        row = _incr._build_resolution_row(
            slot, "ID_ISIN", bdp, "2026-05-21", "2026-05-21 17:00:00"
        )
        assert row["status"] == "rejected"
        assert "implausible" in row["error"]

    def test_plausible_short_tenor_passes(self):
        slot = dict(self._SLOT)
        slot["tenor"] = "2Y"
        bdp = {"ID_ISIN": "US91282CQL80", "MATURITY": "2028-04-30"}
        row = _incr._build_resolution_row(
            slot, "ID_ISIN", bdp, "2026-05-21", "2026-05-21 17:00:00"
        )
        assert row["status"] == "ok"


# ============================================================================
# resolve_otr — end to end with xbbg / GCS mocked
# ============================================================================
class TestResolveOtrEndToEnd:
    _LINEAGE = {
        "playbook_name": "sovereign_cash_bonds",
        "playbook_version": "1.1",
        "asset_class": "rates",
        "dataset_name": "sovereign_cash_bonds",
        "playbook_hash": "seedhash",
        "git_commit_hash": "abc123",
        "extractor_version": "extractorhash",
    }
    _PLAYBOOK = {
        "otr_resolution": {
            "enabled": True,
            "resolution_field": "ID_ISIN",
            "confirmation_runs": 2,
            "reference_fields": ["SECURITY_DES", "MATURITY"],
            "slots": [
                {
                    "slot_id": "US_10Y",
                    "generic_ticker": "GT10 Govt",
                    "country": "US",
                    "currency": "USD",
                    "curve_family": "UST",
                    "tenor": "10Y",
                    "expected_isin_prefix": "US",
                }
            ],
        }
    }

    def test_resolve_uploads_artifact_with_expected_shape(self, tmp_path, monkeypatch):
        captured = {}

        def _fake_upload(local_path):
            captured["df"] = pd.read_parquet(local_path)

        bucket = MagicMock()
        bucket.blob.return_value.upload_from_filename.side_effect = _fake_upload

        monkeypatch.setattr(
            _incr.blp,
            "bdp",
            MagicMock(
                return_value=_wide_bdp(
                    "GT10 Govt",
                    ID_ISIN="US91282CQQ77",
                    SECURITY_DES="T 4 3/8 05/15/36",
                    MATURITY="2036-05-15",
                )
            ),
        )

        ok = _incr.resolve_otr(
            playbook=self._PLAYBOOK,
            lineage_meta=self._LINEAGE,
            bucket=bucket,
            temp_data_dir=tmp_path,
            reference_request_kwargs={},
        )
        assert ok is True

        # Uploaded under the dedicated otr_resolution/ prefix.
        blob_name = bucket.blob.call_args[0][0]
        assert blob_name.startswith("otr_resolution/sovereign_cash_bonds_otr_resolution/")

        df = captured["df"]
        assert len(df) == 1
        rec = df.iloc[0]
        assert rec["status"] == "ok"
        assert rec["resolved_isin"] == "US91282CQQ77"
        assert rec["instrument_ticker"] == "/isin/US91282CQQ77"
        assert rec["extraction_mode"] == "otr_resolution"
        # Audit/dedup isolation: suffixed playbook_name on the artifact.
        assert rec["playbook_name"] == "sovereign_cash_bonds__otr_resolution"
        assert rec["confirmation_runs"] == 2

    def test_no_resolver_block_is_a_clean_skip(self, tmp_path):
        bucket = MagicMock()
        ok = _incr.resolve_otr(
            playbook={},  # no otr_resolution block
            lineage_meta=self._LINEAGE,
            bucket=bucket,
            temp_data_dir=tmp_path,
            reference_request_kwargs={},
        )
        assert ok is True
        bucket.blob.assert_not_called()

    def test_all_slots_failed_uploads_nothing(self, tmp_path, monkeypatch):
        bucket = MagicMock()
        # bdp returns an empty frame -> no ID_ISIN -> status failed.
        monkeypatch.setattr(_incr.blp, "bdp", MagicMock(return_value=pd.DataFrame()))
        ok = _incr.resolve_otr(
            playbook=self._PLAYBOOK,
            lineage_meta=self._LINEAGE,
            bucket=bucket,
            temp_data_dir=tmp_path,
            reference_request_kwargs={},
        )
        assert ok is False
        bucket.blob.assert_not_called()
