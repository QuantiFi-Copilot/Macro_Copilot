"""tests/state/test_deliverables_extraction.py — ``--mode deliverables``
extractor unit tests (work order C3, ADR 0011 v3).

`run_deliverables_extraction` is mostly orchestration over xbbg + GCS; the
**pinned-by-tests** behaviour lives in four pure helpers extracted from it
(per ADR 0011 v3, Codex finding 4):

  * `_resolve_deliverables_section` — playbook-section validator. Returns
    None for absent / disabled; **raises ValueError** for enabled-but-
    malformed (Codex finding 3).
  * `_filter_universe_by_include_tickers` — applies the optional whitelist
    and surfaces unmatched entries (Codex finding 2).
  * `_evaluate_deliverables_coverage` — strict all-or-nothing coverage gate,
    both generic-level (v2 / Codex finding 2) and contract-level (v3 /
    Codex finding 1).
  * `_stamp_deliverables_audit_suffix` — audit-key isolation (v2 / Codex
    finding 3).

SYNC INVARIANT: each helper is duplicated byte-equivalently in
`utils/historical_extractor.py` (the BBG-PC single-file extractor) — a
parity test guards drift.
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
    from utils import historical_extractor as _hist


# ============================================================================
# _resolve_deliverables_section — section gate + include_tickers shape check
# ============================================================================


def _valid_section(**overrides):
    sec = {
        "enabled": True,
        "chain_field": "FUT_CHAIN",
        "basket_field": "FUT_DLVRBL_BNDS_CUSIPS",
        "basket_cusip_column": "CUSIP",
        "basket_factor_column": "Conversion Factor",
        "static_fields": [
            {"bloomberg_field": "FUT_FIRST_DLV_DT", "column_name": "first_delivery_date"},
            {"bloomberg_field": "FUT_LAST_DLV_DT",  "column_name": "last_delivery_date"},
        ],
    }
    sec.update(overrides)
    return sec


class TestResolveDeliverablesSection:
    """Returns-None cases: section absent / disabled / not a mapping. These
    legitimately mean 'this playbook does not participate' — never a failure."""

    def test_absent_section_returns_none(self) -> None:
        assert _incr._resolve_deliverables_section({}) is None

    def test_non_mapping_section_returns_none(self) -> None:
        assert _incr._resolve_deliverables_section({"deliverables": "oops"}) is None

    def test_disabled_section_returns_none(self) -> None:
        sec = _valid_section(enabled=False)
        assert _incr._resolve_deliverables_section({"deliverables": sec}) is None

    def test_well_formed_enabled_section_returns_it(self) -> None:
        sec = _valid_section()
        result = _incr._resolve_deliverables_section({"deliverables": sec})
        assert result is sec

    def test_well_formed_enabled_with_include_tickers_returns_it(self) -> None:
        sec = _valid_section(include_tickers=["TY1 Comdty", "RX1 Comdty"])
        assert _incr._resolve_deliverables_section({"deliverables": sec}) is sec

    def test_absent_include_tickers_is_a_valid_open_universe(self) -> None:
        sec = _valid_section()
        assert "include_tickers" not in sec
        assert _incr._resolve_deliverables_section({"deliverables": sec}) is sec


class TestResolveDeliverablesSectionRaises:
    """ADR 0011 v3 (Codex finding 3): an ENABLED section that is malformed
    must RAISE, never silently skip. The caller (run_deliverables_extraction)
    catches the ValueError, prints [ABORT], and marks the run failed."""

    @pytest.mark.parametrize(
        "missing",
        ["chain_field", "basket_field", "basket_cusip_column"],
    )
    def test_missing_required_field_raises(self, missing: str) -> None:
        sec = _valid_section()
        del sec[missing]
        with pytest.raises(ValueError, match=missing):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_empty_static_fields_raises(self) -> None:
        sec = _valid_section(static_fields=[])
        with pytest.raises(ValueError, match="static_fields"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_non_list_static_fields_raises(self) -> None:
        sec = _valid_section(static_fields="not a list")
        with pytest.raises(ValueError, match="static_fields"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_empty_include_tickers_list_raises(self) -> None:
        """A YAML author wrote an empty list — that's a misconfiguration, not
        an open universe (omit the key for that)."""
        sec = _valid_section(include_tickers=[])
        with pytest.raises(ValueError, match="include_tickers"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_non_list_include_tickers_raises(self) -> None:
        sec = _valid_section(include_tickers="TY1 Comdty")
        with pytest.raises(ValueError, match="include_tickers"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_non_string_entry_raises(self) -> None:
        sec = _valid_section(include_tickers=["TY1 Comdty", 42])
        with pytest.raises(ValueError, match="include_tickers"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_blank_string_entry_raises(self) -> None:
        sec = _valid_section(include_tickers=["TY1 Comdty", "   "])
        with pytest.raises(ValueError, match="include_tickers"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_disabled_section_with_malformed_body_still_returns_none(self) -> None:
        """`enabled: false` short-circuits BEFORE the required-field check.
        Disabled = full opt-out, malformed body irrelevant."""
        sec = {"enabled": False, "chain_field": None}
        assert _incr._resolve_deliverables_section({"deliverables": sec}) is None


# ============================================================================
# _filter_universe_by_include_tickers — strict exact-match (Codex finding 2)
# ============================================================================


def _u(ticker: str) -> dict:
    return {"ticker": ticker, "is_rolling_contract": True}


class TestFilterUniverseByIncludeTickers:
    _UNIVERSE = [_u("TY1 Comdty"), _u("FV1 Comdty"), _u("RX1 Comdty")]

    def test_none_whitelist_passes_universe_through(self) -> None:
        filtered, unmatched = _incr._filter_universe_by_include_tickers(
            self._UNIVERSE, None,
        )
        assert filtered == self._UNIVERSE
        assert unmatched == []

    def test_empty_whitelist_is_treated_as_no_whitelist(self) -> None:
        """An empty list never reaches this helper (validator rejects it),
        but the helper itself defaults defensively."""
        filtered, unmatched = _incr._filter_universe_by_include_tickers(
            self._UNIVERSE, [],
        )
        assert filtered == self._UNIVERSE
        assert unmatched == []

    def test_matched_whitelist_filters_universe(self) -> None:
        filtered, unmatched = _incr._filter_universe_by_include_tickers(
            self._UNIVERSE, ["TY1 Comdty", "FV1 Comdty"],
        )
        assert {it["ticker"] for it in filtered} == {"TY1 Comdty", "FV1 Comdty"}
        assert unmatched == []

    def test_unmatched_entry_is_surfaced(self) -> None:
        """The headline behaviour: a typo or out-of-scope reference is
        reported, NEVER silently dropped (v2 bug)."""
        filtered, unmatched = _incr._filter_universe_by_include_tickers(
            self._UNIVERSE, ["TY1 Comdty", "TYPO1 Comdty"],
        )
        assert {it["ticker"] for it in filtered} == {"TY1 Comdty"}
        assert unmatched == ["TYPO1 Comdty"]

    def test_all_unmatched_entries_are_reported(self) -> None:
        """If every entry is a typo the helper still reports them all — the
        caller must abort, never silently process an empty universe."""
        filtered, unmatched = _incr._filter_universe_by_include_tickers(
            self._UNIVERSE, ["A1 Comdty", "B1 Comdty"],
        )
        assert filtered == []
        assert unmatched == ["A1 Comdty", "B1 Comdty"]

    def test_unmatched_list_is_deterministic_sorted(self) -> None:
        """Stable error reporting — operator gets the same message order
        across runs."""
        filtered, unmatched = _incr._filter_universe_by_include_tickers(
            self._UNIVERSE,
            ["ZZ1 Comdty", "AA1 Comdty", "TY1 Comdty"],
        )
        assert unmatched == ["AA1 Comdty", "ZZ1 Comdty"]


# ============================================================================
# _evaluate_deliverables_coverage — strict generic + contract gates
# ============================================================================


class TestEvaluateDeliverablesCoverage:
    def test_clean_coverage_returns_none(self) -> None:
        err = _incr._evaluate_deliverables_coverage(
            generics_with_data=2,
            expected_generics=2,
            total_contracts_probed=10,
            total_contracts_with_basket=10,
            missed_contracts=[],
        )
        assert err is None

    def test_generic_gap_is_reported(self) -> None:
        """Codex finding 2 / v2: only 1 of 2 configured generics produced a
        basket — abort."""
        err = _incr._evaluate_deliverables_coverage(
            generics_with_data=1,
            expected_generics=2,
            total_contracts_probed=10,
            total_contracts_with_basket=10,
        )
        assert err is not None
        assert "generic coverage gate" in err
        assert "1/2" in err

    def test_contract_gap_is_reported_with_sample(self) -> None:
        """Codex finding 1 / v3: every probed contract MUST emit a basket.
        Even with all generics covered, a single missed contract aborts."""
        err = _incr._evaluate_deliverables_coverage(
            generics_with_data=1,
            expected_generics=1,
            total_contracts_probed=179,
            total_contracts_with_basket=178,
            missed_contracts=["TYZ04 Comdty"],
        )
        assert err is not None
        assert "contract coverage gate" in err
        assert "178/179" in err
        assert "TYZ04 Comdty" in err
        assert "1 short" in err

    def test_one_contract_passing_is_not_enough(self) -> None:
        """v2 bug regression: TY1 had 179 contracts, only TYZ24 emitted.
        v2 accepted this; v3 must reject."""
        err = _incr._evaluate_deliverables_coverage(
            generics_with_data=1,
            expected_generics=1,
            total_contracts_probed=179,
            total_contracts_with_basket=1,
            missed_contracts=[f"TY{i} Comdty" for i in range(178)],
        )
        assert err is not None
        assert "contract coverage gate" in err
        assert "1/179" in err

    def test_missed_contract_list_truncates_at_five(self) -> None:
        """Stable, bounded error messages."""
        missed = [f"M{i} Comdty" for i in range(12)]
        err = _incr._evaluate_deliverables_coverage(
            generics_with_data=1,
            expected_generics=1,
            total_contracts_probed=20,
            total_contracts_with_basket=8,
            missed_contracts=missed,
        )
        assert err is not None
        assert "M0 Comdty" in err
        assert "M4 Comdty" in err
        assert "(+7 more)" in err  # 12 missed, 5 shown, 7 elided

    def test_generic_gate_short_circuits_contract_gate(self) -> None:
        """When both gates would fail, surface the generic miss first — it
        is the more fundamental scope error."""
        err = _incr._evaluate_deliverables_coverage(
            generics_with_data=1,
            expected_generics=3,
            total_contracts_probed=10,
            total_contracts_with_basket=4,
            missed_contracts=["X Comdty"],
        )
        assert err is not None
        assert "generic coverage gate" in err
        assert "contract coverage gate" not in err

    def test_zero_expected_generics_is_handled(self) -> None:
        """Degenerate case (no rolling universe) — not the helper's job to
        diagnose; it just doesn't false-positive."""
        err = _incr._evaluate_deliverables_coverage(
            generics_with_data=0,
            expected_generics=0,
            total_contracts_probed=0,
            total_contracts_with_basket=0,
        )
        assert err is None


# ============================================================================
# _stamp_deliverables_audit_suffix — audit-key isolation (Codex finding 3)
# ============================================================================


class TestStampDeliverablesAuditSuffix:
    def test_suffix_is_appended(self) -> None:
        out = _incr._stamp_deliverables_audit_suffix({
            "playbook_name": "bond_futures",
            "playbook_version": "1.7",
        })
        assert out["playbook_name"] == "bond_futures__deliverables"

    def test_other_lineage_keys_are_preserved(self) -> None:
        out = _incr._stamp_deliverables_audit_suffix({
            "playbook_name": "bond_futures",
            "playbook_version": "1.7",
            "dataset_name": "bond_futures",
            "git_commit_hash": "abc123",
        })
        assert out["playbook_version"] == "1.7"
        assert out["dataset_name"] == "bond_futures"
        assert out["git_commit_hash"] == "abc123"

    def test_input_is_not_mutated(self) -> None:
        original = {"playbook_name": "bond_futures"}
        _ = _incr._stamp_deliverables_audit_suffix(original)
        assert original["playbook_name"] == "bond_futures"

    def test_missing_playbook_name_raises(self) -> None:
        with pytest.raises(ValueError, match="playbook_name"):
            _incr._stamp_deliverables_audit_suffix({"playbook_version": "1.7"})

    def test_blank_playbook_name_raises(self) -> None:
        with pytest.raises(ValueError, match="playbook_name"):
            _incr._stamp_deliverables_audit_suffix({"playbook_name": ""})


# ============================================================================
# SYNC INVARIANT — historical_extractor's copy must match incremental's.
# ============================================================================


class TestExtractorSyncInvariant:
    """The two extractors carry byte-equivalent helpers (single-file invariant
    on the BBG PC). A future edit to one without the other surfaces here."""

    def test_section_validator_behaviour_matches(self) -> None:
        sec = _valid_section()
        assert _incr._resolve_deliverables_section({"deliverables": sec}) is sec
        assert _hist._resolve_deliverables_section({"deliverables": sec}) is sec

    def test_section_validator_raises_on_malformed(self) -> None:
        sec = _valid_section()
        del sec["chain_field"]
        with pytest.raises(ValueError):
            _incr._resolve_deliverables_section({"deliverables": sec})
        with pytest.raises(ValueError):
            _hist._resolve_deliverables_section({"deliverables": sec})

    def test_include_tickers_filter_matches(self) -> None:
        universe = [_u("TY1 Comdty"), _u("FV1 Comdty")]
        a = _incr._filter_universe_by_include_tickers(universe, ["TY1 Comdty", "TYPO1 Comdty"])
        b = _hist._filter_universe_by_include_tickers(universe, ["TY1 Comdty", "TYPO1 Comdty"])
        assert a == b

    def test_coverage_evaluator_matches(self) -> None:
        a = _incr._evaluate_deliverables_coverage(1, 1, 10, 8, ["X Comdty", "Y Comdty"])
        b = _hist._evaluate_deliverables_coverage(1, 1, 10, 8, ["X Comdty", "Y Comdty"])
        assert a == b

    def test_audit_suffix_matches(self) -> None:
        meta = {"playbook_name": "bond_futures", "v": "1"}
        a = _incr._stamp_deliverables_audit_suffix(meta)
        b = _hist._stamp_deliverables_audit_suffix(meta)
        assert a == b


# ============================================================================
# v4 validator amendments — required `basket_factor_column` + strict
# per-entry validation of `static_fields` (ADR 0011 v4, Codex findings 1 & 2,
# third round)
# ============================================================================


class TestResolveDeliverablesSectionV4:
    """v4 adds two new required-when-enabled invariants beyond v3:
       1. ``basket_factor_column`` is required (conversion factor is core).
       2. Each ``static_fields`` entry must be a mapping with ``column_name``
          in the allowed set + a non-blank ``bloomberg_field``.
    """

    def test_missing_basket_factor_column_raises(self) -> None:
        sec = _valid_section()
        del sec["basket_factor_column"]
        with pytest.raises(ValueError, match="basket_factor_column"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_blank_basket_factor_column_raises(self) -> None:
        sec = _valid_section(basket_factor_column="")
        with pytest.raises(ValueError, match="basket_factor_column"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_static_field_entry_must_be_mapping(self) -> None:
        sec = _valid_section(static_fields=["not a dict"])
        with pytest.raises(ValueError, match="static_fields"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_static_field_missing_column_name_raises(self) -> None:
        sec = _valid_section(static_fields=[
            {"bloomberg_field": "FUT_FIRST_DLV_DT"},
        ])
        with pytest.raises(ValueError, match="column_name"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_static_field_blank_column_name_raises(self) -> None:
        sec = _valid_section(static_fields=[
            {"column_name": "  ", "bloomberg_field": "FUT_FIRST_DLV_DT"},
        ])
        with pytest.raises(ValueError, match="column_name"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_static_field_column_name_outside_allowed_set_raises(self) -> None:
        """``column_name`` is restricted to the four typed per-contract date
        columns on macro_data.futures_deliverables. Anything else is a
        configuration error — the table has no other typed date columns."""
        sec = _valid_section(static_fields=[
            {"column_name": "settlement_date", "bloomberg_field": "FUT_SETTLE_DT"},
        ])
        with pytest.raises(ValueError, match="allowed set"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_static_field_duplicate_column_name_raises(self) -> None:
        """Each per-contract date column may be configured only once."""
        sec = _valid_section(static_fields=[
            {"column_name": "first_delivery_date", "bloomberg_field": "FUT_A"},
            {"column_name": "first_delivery_date", "bloomberg_field": "FUT_B"},
        ])
        with pytest.raises(ValueError, match="duplicated"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_static_field_missing_bloomberg_field_raises(self) -> None:
        sec = _valid_section(static_fields=[
            {"column_name": "first_delivery_date"},
        ])
        with pytest.raises(ValueError, match="bloomberg_field"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_static_field_blank_bloomberg_field_raises(self) -> None:
        sec = _valid_section(static_fields=[
            {"column_name": "first_delivery_date", "bloomberg_field": "   "},
        ])
        with pytest.raises(ValueError, match="bloomberg_field"):
            _incr._resolve_deliverables_section({"deliverables": sec})

    def test_all_four_allowed_column_names_pass(self) -> None:
        """Smoke test of the canonical complete static_fields config."""
        sec = _valid_section(static_fields=[
            {"column_name": "first_delivery_date", "bloomberg_field": "FUT_FIRST_DLV_DT"},
            {"column_name": "last_delivery_date",  "bloomberg_field": "FUT_LAST_DLV_DT"},
            {"column_name": "first_notice_date",   "bloomberg_field": "FUT_NOTICE_FIRST"},
            {"column_name": "last_notice_date",    "bloomberg_field": "FUT_NOTICE_LAST"},
        ])
        assert _incr._resolve_deliverables_section({"deliverables": sec}) is sec

    def test_subset_of_allowed_column_names_pass(self) -> None:
        """A playbook may legitimately scope to fewer than four date columns
        (e.g. only delivery dates, no notice dates)."""
        sec = _valid_section(static_fields=[
            {"column_name": "first_delivery_date", "bloomberg_field": "FUT_FIRST_DLV_DT"},
            {"column_name": "last_delivery_date",  "bloomberg_field": "FUT_LAST_DLV_DT"},
        ])
        assert _incr._resolve_deliverables_section({"deliverables": sec}) is sec

    def test_historical_validator_mirrors_incremental(self) -> None:
        """SYNC INVARIANT: every v4 invariant raises identically on the
        historical extractor's copy."""
        sec = _valid_section()
        del sec["basket_factor_column"]
        with pytest.raises(ValueError, match="basket_factor_column"):
            _hist._resolve_deliverables_section({"deliverables": sec})

        sec = _valid_section(static_fields=[
            {"column_name": "bogus", "bloomberg_field": "X"},
        ])
        with pytest.raises(ValueError, match="allowed set"):
            _hist._resolve_deliverables_section({"deliverables": sec})


# ============================================================================
# _stage_contract_rows — the per-contract row-staging pure helper
# (ADR 0011 v4, Codex findings 1 & 2, third round)
# ============================================================================


def _basket_frame(rows):
    """Build a basket DataFrame from a list of dicts. Each dict represents one
    deliverable bond row."""
    return pd.DataFrame(rows)


_VALID_DATES = {
    "FUT_FIRST_DLV_DT": "2024-12-02",
    "FUT_LAST_DLV_DT":  "2024-12-31",
    "FUT_NOTICE_FIRST": "2024-11-29",
    "FUT_NOTICE_LAST":  "2024-12-30",
}

_DATE_FIELD_TO_COLUMN = {
    "FUT_FIRST_DLV_DT": "first_delivery_date",
    "FUT_LAST_DLV_DT":  "last_delivery_date",
    "FUT_NOTICE_FIRST": "first_notice_date",
    "FUT_NOTICE_LAST":  "last_notice_date",
}


def _stage(basket_df, dates_raw=None, **overrides):
    """Call _incr._stage_contract_rows with sensible defaults."""
    kwargs = dict(
        basket_df=basket_df,
        dates_raw=dates_raw if dates_raw is not None else dict(_VALID_DATES),
        generic_ticker="TY1 Comdty",
        contract_code="TYZ24",
        basket_cusip_column="CUSIP",
        basket_factor_column="Conversion Factor",
        basket_isin_column=None,
        date_field_to_column=dict(_DATE_FIELD_TO_COLUMN),
    )
    kwargs.update(overrides)
    return _incr._stage_contract_rows(**kwargs)


class TestStageContractRowsHappyPath:
    def test_well_formed_basket_emits_rows(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
            {"CUSIP": "91282CYY9", "Conversion Factor": 0.7891},
        ])
        rows, miss = _stage(df)
        assert miss is None
        assert len(rows) == 2
        assert rows[0]["deliverable_cusip"] == "91282CAB1"
        assert rows[0]["conversion_factor"] == 0.8234
        assert rows[0]["first_delivery_date"] == "2024-12-02"
        assert rows[0]["last_delivery_date"] == "2024-12-31"
        assert rows[0]["first_notice_date"] == "2024-11-29"
        assert rows[0]["last_notice_date"] == "2024-12-30"

    def test_column_case_is_matched_case_insensitively(self) -> None:
        """Bloomberg returns variable casing on basket-frame columns."""
        df = _basket_frame([
            {"cusip": "91282CAB1", "conversion factor": 0.8234},
        ])
        rows, miss = _stage(df)
        assert miss is None
        assert len(rows) == 1

    def test_blank_cusip_rows_are_padding_and_are_skipped(self) -> None:
        """Bloomberg sometimes pads basket frames with blank-CUSIP rows;
        those are dropped silently — they do NOT fail the contract."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
            {"CUSIP": "", "Conversion Factor": None},
            {"CUSIP": "91282CYY9", "Conversion Factor": 0.7891},
        ])
        rows, miss = _stage(df)
        assert miss is None
        assert len(rows) == 2

    def test_optional_isin_column_is_emitted_when_configured(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234,
             "ISIN": "US91282CAB12"},
        ])
        rows, miss = _stage(df, basket_isin_column="ISIN")
        assert miss is None
        assert rows[0]["deliverable_isin"] == "US91282CAB12"

    def test_optional_isin_column_absent_when_not_configured(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        rows, miss = _stage(df, basket_isin_column=None)
        assert miss is None
        assert rows[0]["deliverable_isin"] is None


class TestStageContractRowsMissReasons:
    """Each miss path returns ``[], <reason>`` and emits no rows. The contract
    is counted as missed by the v3 all-or-nothing coverage gate."""

    def test_missing_cusip_column_returns_miss(self) -> None:
        df = _basket_frame([
            {"Conversion Factor": 0.8234},  # no CUSIP column
        ])
        rows, miss = _stage(df)
        assert rows == []
        assert miss == "missing_basket_column:CUSIP"

    def test_missing_factor_column_returns_miss(self) -> None:
        """ADR 0011 v4 / Codex finding 1, third round: conversion factor
        column absence aborts the contract — never silently NULL."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1"},  # no factor column
        ])
        rows, miss = _stage(df)
        assert rows == []
        assert miss == "missing_basket_column:Conversion Factor"

    def test_missing_static_field_returns_miss(self) -> None:
        """ADR 0011 v4 / Codex finding 2, third round: any configured static
        field absent for the contract aborts the contract."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        dates = dict(_VALID_DATES)
        dates["FUT_LAST_DLV_DT"] = None
        rows, miss = _stage(df, dates_raw=dates)
        assert rows == []
        assert miss == "missing_static_field:last_delivery_date"

    def test_missing_static_field_via_nan_returns_miss(self) -> None:
        """A NaN value (common in pandas / bdp returns) is also treated as
        missing by _clean_scalar."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        dates = dict(_VALID_DATES)
        dates["FUT_FIRST_DLV_DT"] = float("nan")
        rows, miss = _stage(df, dates_raw=dates)
        assert rows == []
        assert miss == "missing_static_field:first_delivery_date"

    def test_missing_static_field_via_blank_string_returns_miss(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        dates = dict(_VALID_DATES)
        dates["FUT_NOTICE_LAST"] = "   "
        rows, miss = _stage(df, dates_raw=dates)
        assert rows == []
        assert miss == "missing_static_field:last_notice_date"

    def test_per_row_missing_factor_returns_miss(self) -> None:
        """A CUSIP-present row with no conversion factor is structurally
        incomplete -- never silently emit ``conversion_factor = NULL``."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
            {"CUSIP": "91282CYY9", "Conversion Factor": None},
        ])
        rows, miss = _stage(df)
        assert rows == []
        assert miss == "missing_factor_for_cusip:91282CYY9"

    def test_per_row_factor_nan_returns_miss(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": float("nan")},
        ])
        rows, miss = _stage(df)
        assert rows == []
        assert miss == "missing_factor_for_cusip:91282CAB1"

    def test_all_blank_cusips_returns_empty_basket_miss(self) -> None:
        """A basket frame that is data-shaped but semantically empty (every
        row has a blank CUSIP) is reported distinctly so the operator can
        diagnose Bloomberg padding-only returns."""
        df = _basket_frame([
            {"CUSIP": "", "Conversion Factor": None},
            {"CUSIP": None, "Conversion Factor": None},
        ])
        rows, miss = _stage(df)
        assert rows == []
        assert miss == "empty_basket"

    def test_partial_static_fields_only_check_configured(self) -> None:
        """If the playbook only configures two of the four date columns, only
        those two are required for the contract. A None for an *unconfigured*
        Bloomberg field is irrelevant."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        date_field_to_column_partial = {
            "FUT_FIRST_DLV_DT": "first_delivery_date",
            "FUT_LAST_DLV_DT":  "last_delivery_date",
        }
        dates = {
            "FUT_FIRST_DLV_DT": "2024-12-02",
            "FUT_LAST_DLV_DT":  "2024-12-31",
            # FUT_NOTICE_* deliberately absent -- not configured, so not required.
        }
        rows, miss = _stage(
            df, dates_raw=dates,
            date_field_to_column=date_field_to_column_partial,
        )
        assert miss is None
        assert len(rows) == 1
        assert rows[0]["first_delivery_date"] == "2024-12-02"
        # Unconfigured columns simply don't appear on the row.
        assert "first_notice_date" not in rows[0]


# ============================================================================
# v5 — type-parseability for factor + static dates (ADR 0011 v5, Codex
# finding, fourth round). v4 caught structural absence (None / NaN / blank);
# v5 additionally rejects values that are present-but-unparseable, because
# the ingester silently coerces those to NULL on the way to the substrate.
# ============================================================================


class TestStageContractRowsParseValidationV5:
    def test_blank_string_factor_returns_missing(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": "   "},
        ])
        rows, miss = _stage(df)
        assert rows == []
        assert miss == "missing_factor_for_cusip:91282CAB1"

    def test_na_string_factor_returns_invalid(self) -> None:
        """The classic Bloomberg sentinel: 'N/A' must NOT silently NULL."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": "N/A"},
        ])
        rows, miss = _stage(df)
        assert rows == []
        assert miss == "invalid_factor_for_cusip:91282CAB1"

    def test_arbitrary_non_numeric_factor_returns_invalid(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": "abc"},
        ])
        rows, miss = _stage(df)
        assert rows == []
        assert miss == "invalid_factor_for_cusip:91282CAB1"

    def test_string_nan_factor_returns_invalid(self) -> None:
        """``float('nan')`` parses successfully — guard against it."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": "NaN"},
        ])
        rows, miss = _stage(df)
        assert rows == []
        assert miss == "invalid_factor_for_cusip:91282CAB1"

    def test_numeric_string_factor_is_parsed(self) -> None:
        """A real factor returned as a stringified number IS valid; we parse
        it to a Python float (the ingester writes that to NUMERIC)."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": "0.8234"},
        ])
        rows, miss = _stage(df)
        assert miss is None
        assert rows[0]["conversion_factor"] == 0.8234
        assert isinstance(rows[0]["conversion_factor"], float)

    def test_factor_emitted_as_float_not_string(self) -> None:
        """Numeric input passes through as a Python float (not numpy scalar,
        not string) — parquet-friendly."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        rows, miss = _stage(df)
        assert miss is None
        assert isinstance(rows[0]["conversion_factor"], float)
        assert rows[0]["conversion_factor"] == 0.8234

    def test_unparseable_static_date_returns_invalid(self) -> None:
        """A configured date field that doesn't parse must NOT silently NULL
        on the way to ``futures_deliverables.first_delivery_date``."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        dates = dict(_VALID_DATES)
        dates["FUT_FIRST_DLV_DT"] = "not-a-date"
        rows, miss = _stage(df, dates_raw=dates)
        assert rows == []
        assert miss == "invalid_static_field:first_delivery_date"

    def test_garbage_string_static_date_returns_invalid(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        dates = dict(_VALID_DATES)
        dates["FUT_LAST_DLV_DT"] = "TBD"
        rows, miss = _stage(df, dates_raw=dates)
        assert rows == []
        assert miss == "invalid_static_field:last_delivery_date"

    def test_pd_timestamp_static_date_is_normalised_to_iso_string(self) -> None:
        """Bloomberg often returns dates as ``pd.Timestamp``. The emitted
        parquet row must carry canonical ``YYYY-MM-DD`` ISO strings."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        dates = {
            "FUT_FIRST_DLV_DT": pd.Timestamp("2024-12-02"),
            "FUT_LAST_DLV_DT":  pd.Timestamp("2024-12-31"),
            "FUT_NOTICE_FIRST": pd.Timestamp("2024-11-29"),
            "FUT_NOTICE_LAST":  pd.Timestamp("2024-12-30"),
        }
        rows, miss = _stage(df, dates_raw=dates)
        assert miss is None
        assert rows[0]["first_delivery_date"] == "2024-12-02"
        assert rows[0]["last_delivery_date"] == "2024-12-31"
        assert isinstance(rows[0]["first_delivery_date"], str)

    def test_datetime_with_time_component_is_stripped_to_iso_date(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        dates = dict(_VALID_DATES)
        dates["FUT_FIRST_DLV_DT"] = "2024-12-02 14:30:00"
        rows, miss = _stage(df, dates_raw=dates)
        assert miss is None
        assert rows[0]["first_delivery_date"] == "2024-12-02"

    def test_static_dates_canonical_across_all_basket_rows(self) -> None:
        """Per-contract dates are denormalised — every row of the basket
        carries the same value, in the same canonical ISO form."""
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
            {"CUSIP": "91282CYY9", "Conversion Factor": 0.7891},
            {"CUSIP": "91282CZZ7", "Conversion Factor": 0.6543},
        ])
        dates = {
            "FUT_FIRST_DLV_DT": pd.Timestamp("2024-12-02"),
            "FUT_LAST_DLV_DT":  pd.Timestamp("2024-12-31"),
            "FUT_NOTICE_FIRST": pd.Timestamp("2024-11-29"),
            "FUT_NOTICE_LAST":  pd.Timestamp("2024-12-30"),
        }
        rows, miss = _stage(df, dates_raw=dates)
        assert miss is None
        # Same value across all 3 rows of the basket.
        assert {r["first_delivery_date"] for r in rows} == {"2024-12-02"}
        assert {r["last_delivery_date"] for r in rows} == {"2024-12-31"}


class TestStageContractRowsHistoricalParity:
    """SYNC INVARIANT: historical_extractor's copy behaves byte-equivalently."""

    def test_happy_path_matches(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        a, ma = _incr._stage_contract_rows(
            basket_df=df, dates_raw=dict(_VALID_DATES),
            generic_ticker="TY1 Comdty", contract_code="TYZ24",
            basket_cusip_column="CUSIP", basket_factor_column="Conversion Factor",
            basket_isin_column=None,
            date_field_to_column=dict(_DATE_FIELD_TO_COLUMN),
        )
        b, mb = _hist._stage_contract_rows(
            basket_df=df, dates_raw=dict(_VALID_DATES),
            generic_ticker="TY1 Comdty", contract_code="TYZ24",
            basket_cusip_column="CUSIP", basket_factor_column="Conversion Factor",
            basket_isin_column=None,
            date_field_to_column=dict(_DATE_FIELD_TO_COLUMN),
        )
        assert a == b and ma == mb

    def test_miss_reason_matches(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": None},
        ])
        common = dict(
            basket_df=df, dates_raw=dict(_VALID_DATES),
            generic_ticker="TY1 Comdty", contract_code="TYZ24",
            basket_cusip_column="CUSIP", basket_factor_column="Conversion Factor",
            basket_isin_column=None,
            date_field_to_column=dict(_DATE_FIELD_TO_COLUMN),
        )
        a, ma = _incr._stage_contract_rows(**common)
        b, mb = _hist._stage_contract_rows(**common)
        assert a == b == []
        assert ma == mb == "missing_factor_for_cusip:91282CAB1"

    def test_v5_invalid_factor_matches(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": "N/A"},
        ])
        common = dict(
            basket_df=df, dates_raw=dict(_VALID_DATES),
            generic_ticker="TY1 Comdty", contract_code="TYZ24",
            basket_cusip_column="CUSIP", basket_factor_column="Conversion Factor",
            basket_isin_column=None,
            date_field_to_column=dict(_DATE_FIELD_TO_COLUMN),
        )
        a, ma = _incr._stage_contract_rows(**common)
        b, mb = _hist._stage_contract_rows(**common)
        assert (a, ma) == (b, mb) == ([], "invalid_factor_for_cusip:91282CAB1")

    def test_v5_invalid_static_date_matches(self) -> None:
        df = _basket_frame([
            {"CUSIP": "91282CAB1", "Conversion Factor": 0.8234},
        ])
        dates = dict(_VALID_DATES)
        dates["FUT_NOTICE_FIRST"] = "not-a-date"
        common = dict(
            basket_df=df, dates_raw=dates,
            generic_ticker="TY1 Comdty", contract_code="TYZ24",
            basket_cusip_column="CUSIP", basket_factor_column="Conversion Factor",
            basket_isin_column=None,
            date_field_to_column=dict(_DATE_FIELD_TO_COLUMN),
        )
        a, ma = _incr._stage_contract_rows(**common)
        b, mb = _hist._stage_contract_rows(**common)
        assert (a, ma) == (b, mb) == ([], "invalid_static_field:first_notice_date")
