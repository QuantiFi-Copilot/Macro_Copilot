"""tests/state/test_otr_resolution_planner.py — the OTR-resolution planner
(work order A4-4 resolver, ADR 0007).

``ingestion/otr_resolution.py`` is pure Python — no Bloomberg, no GCS, no DB —
so the false-roll-protected SCD2 state machine, the parquet parser and the
instrument-master record builder are all unit-testable in isolation.

The decisions under test:
  * BOOTSTRAP — first observation of a slot with no history; not gated.
  * NO_CHANGE — resolved bond already the open OTR (or a same-date re-ingest).
  * RECORD_CANDIDATE / CONFIRM_ROLL — a roll is recorded only after
    ``confirmation_runs`` DISTINCT-date observations agree on the new ISIN.
  * CLEAR_CANDIDATE — resolver reverted to the open bond; drop the candidate.
  * SKIP_STALE — an out-of-order (older) artifact never rewinds state.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
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
    build_otr_instrument_record,
    is_ingestable_observation,
    parquet_to_resolution_records,
    plan_otr_transition,
)


def _open_row(instrument_id: int, effective_from: str = "2026-02-01", attributes=None):
    return {
        "otr_history_id": 1,
        "country": "US",
        "tenor": "10Y",
        "effective_from": pd.to_datetime(effective_from).date(),
        "effective_to": None,
        "otr_instrument_id": instrument_id,
        "attributes": attributes,
    }


# ============================================================================
# BOOTSTRAP — no open window
# ============================================================================
class TestBootstrap:
    def test_no_open_row_bootstraps_immediately(self):
        plan = plan_otr_transition(
            open_row=None,
            resolution_date="2026-05-21",
            resolved_instrument_id=100,
            resolved_isin="US91282CQQ77",
            confirmation_runs=2,
        )
        assert plan.action == ACTION_BOOTSTRAP
        assert plan.new_effective_from == "2026-05-21"
        assert plan.new_otr_instrument_id == 100
        assert plan.new_attributes["otr_resolution"]["mode"] == "bootstrap"

    def test_bootstrap_is_not_confirmation_gated(self):
        # Even with a high confirmation threshold a brand-new slot bootstraps
        # on the first observation — it closes nothing.
        plan = plan_otr_transition(None, "2026-05-21", 7, "US0000000001", confirmation_runs=5)
        assert plan.action == ACTION_BOOTSTRAP


# ============================================================================
# NO_CHANGE — resolved bond is already the open OTR
# ============================================================================
class TestNoChange:
    def test_resolved_matches_open_no_pending(self):
        plan = plan_otr_transition(_open_row(100), "2026-05-21", 100, "US91282CQQ77", 2)
        assert plan.action == ACTION_NO_CHANGE

    def test_same_date_reingest_of_candidate_is_no_change(self):
        # A pending candidate observed on D1; re-ingesting the SAME D1 artifact
        # must NOT advance the confirmation count (distinct-date rule).
        pending = {
            "pending_candidate": {
                "instrument_id": 200,
                "isin": "US0000000002",
                "first_observed": "2026-05-21",
                "last_observed": "2026-05-21",
                "observation_count": 1,
            }
        }
        plan = plan_otr_transition(
            _open_row(100, attributes=pending), "2026-05-21", 200, "US0000000002", 2
        )
        assert plan.action == ACTION_NO_CHANGE


# ============================================================================
# CLEAR_CANDIDATE — resolver reverted to the open bond
# ============================================================================
class TestClearCandidate:
    def test_revert_to_open_bond_clears_pending(self):
        pending = {
            "pending_candidate": {
                "instrument_id": 200,
                "isin": "US0000000002",
                "first_observed": "2026-05-20",
                "last_observed": "2026-05-20",
                "observation_count": 1,
            }
        }
        plan = plan_otr_transition(
            _open_row(100, attributes=pending), "2026-05-21", 100, "US91282CQQ77", 2
        )
        assert plan.action == ACTION_CLEAR_CANDIDATE
        assert "pending_candidate" not in plan.open_row_attributes


# ============================================================================
# RECORD_CANDIDATE / CONFIRM_ROLL — the two-run confirmation gate
# ============================================================================
class TestConfirmationGate:
    def test_first_observation_records_candidate(self):
        plan = plan_otr_transition(_open_row(100), "2026-05-21", 200, "US0000000002", 2)
        assert plan.action == ACTION_RECORD_CANDIDATE
        cand = plan.open_row_attributes["pending_candidate"]
        assert cand["instrument_id"] == 200
        assert cand["observation_count"] == 1
        assert cand["first_observed"] == "2026-05-21"
        assert cand["last_observed"] == "2026-05-21"

    def test_second_distinct_date_observation_confirms_roll(self):
        pending = {
            "pending_candidate": {
                "instrument_id": 200,
                "isin": "US0000000002",
                "first_observed": "2026-05-21",
                "last_observed": "2026-05-21",
                "observation_count": 1,
            }
        }
        plan = plan_otr_transition(
            _open_row(100, attributes=pending), "2026-05-22", 200, "US0000000002", 2
        )
        assert plan.action == ACTION_CONFIRM_ROLL
        # The new window backdates to the FIRST confirmed sighting, not the
        # confirmation date — the most accurate honest effective_from.
        assert plan.new_effective_from == "2026-05-21"
        assert plan.close_prior_at == "2026-05-21"
        assert plan.new_otr_instrument_id == 200
        prov = plan.new_attributes["otr_resolution"]
        assert prov["mode"] == "roll"
        assert prov["first_observed"] == "2026-05-21"
        assert prov["confirmed_on"] == "2026-05-22"
        assert prov["previous_otr_instrument_id"] == 100

    def test_confirmation_runs_one_rolls_immediately(self):
        plan = plan_otr_transition(_open_row(100), "2026-05-21", 200, "US0000000002", 1)
        assert plan.action == ACTION_CONFIRM_ROLL
        assert plan.new_effective_from == "2026-05-21"

    def test_changed_candidate_resets_the_count(self):
        # A pending candidate for bond 200; a DIFFERENT new bond 300 appears —
        # that is a fresh candidate, count restarts at 1, no roll.
        pending = {
            "pending_candidate": {
                "instrument_id": 200,
                "isin": "US0000000002",
                "first_observed": "2026-05-21",
                "last_observed": "2026-05-21",
                "observation_count": 1,
            }
        }
        plan = plan_otr_transition(
            _open_row(100, attributes=pending), "2026-05-22", 300, "US0000000003", 2
        )
        assert plan.action == ACTION_RECORD_CANDIDATE
        cand = plan.open_row_attributes["pending_candidate"]
        assert cand["instrument_id"] == 300
        assert cand["observation_count"] == 1
        assert cand["first_observed"] == "2026-05-22"

    def test_third_run_needed_when_confirmation_runs_is_three(self):
        pending = {
            "pending_candidate": {
                "instrument_id": 200,
                "isin": "US0000000002",
                "first_observed": "2026-05-21",
                "last_observed": "2026-05-22",
                "observation_count": 2,
            }
        }
        plan = plan_otr_transition(
            _open_row(100, attributes=pending), "2026-05-23", 200, "US0000000002", 3
        )
        assert plan.action == ACTION_CONFIRM_ROLL
        assert plan.new_effective_from == "2026-05-21"


# ============================================================================
# SKIP_STALE — out-of-order artifacts never rewind state
# ============================================================================
class TestSkipStale:
    def test_observation_older_than_last_seen_is_skipped(self):
        pending = {
            "pending_candidate": {
                "instrument_id": 200,
                "isin": "US0000000002",
                "first_observed": "2026-05-21",
                "last_observed": "2026-05-22",
                "observation_count": 2,
            }
        }
        plan = plan_otr_transition(
            _open_row(100, attributes=pending), "2026-05-20", 200, "US0000000002", 5
        )
        assert plan.action == ACTION_SKIP_STALE

    def test_stale_different_candidate_with_no_pending_is_skipped(self):
        # An out-of-order artifact dated ON/BEFORE the open window's
        # effective_from, resolving to a DIFFERENT bond, with NO pending
        # candidate, must NOT open a fresh candidate — it would otherwise
        # rewind state (or trip the EXCLUDE constraint on a confirmed roll).
        plan = plan_otr_transition(
            open_row=_open_row(100, effective_from="2026-05-15"),
            resolution_date="2026-05-10",
            resolved_instrument_id=200,
            resolved_isin="US0000000002",
            confirmation_runs=2,
        )
        assert plan.action == ACTION_SKIP_STALE

    def test_observation_on_window_start_date_is_skipped(self):
        # Boundary: an observation dated exactly on the open window's start —
        # conflicting same-day data — is treated as stale even at
        # confirmation_runs=1 (which would otherwise roll immediately).
        plan = plan_otr_transition(
            open_row=_open_row(100, effective_from="2026-05-15"),
            resolution_date="2026-05-15",
            resolved_instrument_id=200,
            resolved_isin="US0000000002",
            confirmation_runs=1,
        )
        assert plan.action == ACTION_SKIP_STALE

    def test_observation_after_window_start_still_records_candidate(self):
        # Guard against over-correction: an observation STRICTLY AFTER the open
        # window's effective_from is not stale and still opens a candidate.
        plan = plan_otr_transition(
            open_row=_open_row(100, effective_from="2026-05-15"),
            resolution_date="2026-05-16",
            resolved_instrument_id=200,
            resolved_isin="US0000000002",
            confirmation_runs=2,
        )
        assert plan.action == ACTION_RECORD_CANDIDATE


# ============================================================================
# parquet_to_resolution_records / is_ingestable_observation
# ============================================================================
class TestParquetParsing:
    def _df(self, **overrides):
        row = {
            "slot_id": "US_10Y",
            "generic_ticker": "GT10 Govt",
            "country": "US",
            "currency": "USD",
            "curve_family": "UST",
            "tenor": "10Y",
            "resolved_isin": "US91282CQQ77",
            "resolved_cusip": "91282CQQ7",
            "security_name": "T 4 3/8 05/15/36",
            "coupon": 4.375,
            "maturity_date": "2036-05-15",
            "issue_date": "2026-05-15",
            "instrument_ticker": "/isin/US91282CQQ77",
            "resolution_date": "2026-05-21",
            "resolution_timestamp": "2026-05-21 17:00:00",
            "status": "ok",
            "error": None,
        }
        row.update(overrides)
        return pd.DataFrame([row])

    def test_ok_row_parses_and_is_ingestable(self):
        records = parquet_to_resolution_records(self._df())
        assert len(records) == 1
        rec = records[0]
        assert rec["status"] == "ok"
        assert rec["resolution_date"] == "2026-05-21"
        assert rec["maturity_date"] == "2036-05-15"
        assert is_ingestable_observation(rec)

    def test_failed_row_is_not_ingestable(self):
        records = parquet_to_resolution_records(
            self._df(status="failed", resolved_isin=None, instrument_ticker=None)
        )
        assert not is_ingestable_observation(records[0])

    def test_missing_instrument_ticker_is_derived_from_isin(self):
        records = parquet_to_resolution_records(self._df(instrument_ticker=None))
        assert records[0]["instrument_ticker"] == "/isin/US91282CQQ77"

    def test_ok_row_missing_required_field_is_not_ingestable(self):
        records = parquet_to_resolution_records(self._df(resolved_isin=None, instrument_ticker=None))
        assert not is_ingestable_observation(records[0])


# ============================================================================
# build_otr_instrument_record
# ============================================================================
class TestBuildInstrumentRecord:
    def test_vendor_ticker_matches_isin_convention(self):
        obs = {
            "instrument_ticker": "/isin/US91282CQQ77",
            "resolved_isin": "US91282CQQ77",
            "resolved_cusip": "91282CQQ7",
            "country": "US",
            "currency": "USD",
            "curve_family": "UST",
            "tenor": "10Y",
            "security_name": "T 4 3/8 05/15/36",
            "coupon": 4.375,
            "issue_date": "2026-05-15",
            "maturity_date": "2036-05-15",
            "generic_ticker": "GT10 Govt",
        }
        rec = build_otr_instrument_record(obs)
        # vendor_ticker MUST be the /isin/ form so resolution-side and
        # market-data-side upserts converge on one instrument_master row.
        assert rec["vendor_ticker"] == "/isin/US91282CQQ77"
        assert rec["instrument_type"] == "sovereign_cash_bond"
        assert rec["asset_class"] == "rates"
        assert rec["isin"] == "US91282CQQ77"
        assert rec["cusip"] == "91282CQQ7"
        assert rec["maturity_date"] == "2036-05-15"
        assert rec["attributes"]["source"] == "otr_resolver"
        assert rec["attributes"]["coupon"] == 4.375
