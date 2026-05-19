"""tests/state/test_metadata_history_extraction.py — contract tests for the
extractor-side helpers introduced under ADR 0002's ``--mode metadata-history``
flow.

We test the pure-Python transforms that are easy to pin without Bloomberg:

  * ``_extract_underlying_tickers_from_chain`` — robustness against the
    several xbbg / Bloomberg column-naming conventions for bulk-data
    output of FUT_CHAIN-style fields.
  * ``_compute_effective_windows_expiry_roll`` — the ``expiry_roll``
    convention's window-pairing arithmetic.
  * ``_resolve_metadata_history_section`` — the playbook section parser
    that decides whether a given playbook participates in metadata-history
    extraction at all.

The Bloomberg-touching helpers (`_fetch_chain_underlyings`,
`_fetch_underlying_contract_static`, `run_metadata_history_extraction`)
are out of scope here — they wrap ``blp.bds``/``blp.bdp`` which require a
live terminal session. The operator's manual smoke test after PR A2
exercises the end-to-end path with real Bloomberg.

ADR 0002: docs_revamped/05_decisions/0002-playbook-metadata-history-section.md
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Make sibling packages importable.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# The extractor imports ``xbbg`` at module load. Patch it BEFORE the import
# so this test module can be collected on a machine without a Bloomberg
# terminal installed. The actual Bloomberg-dependent functions are not
# exercised here.
with patch.dict(sys.modules, {"xbbg": MagicMock(), "xbbg.blp": MagicMock()}):
    from utils import historical_extractor  # noqa: E402


# ============================================================================
# Chain-output robustness
# ============================================================================


class TestExtractUnderlyingTickersFromChain:
    """``_extract_underlying_tickers_from_chain`` walks the candidate column
    names in priority order and falls back to the first string-valued column.
    A playbook can override the lookup via ``metadata_history.chain_column_name``
    (passed through as ``fallback_column``)."""

    def test_empty_input_returns_empty(self) -> None:
        assert historical_extractor._extract_underlying_tickers_from_chain(None) == []
        assert historical_extractor._extract_underlying_tickers_from_chain(pd.DataFrame()) == []
        assert historical_extractor._extract_underlying_tickers_from_chain("not a df") == []  # type: ignore[arg-type]

    def test_canonical_security_description_column(self) -> None:
        df = pd.DataFrame({"Security Description": ["TYZ20 Comdty", "TYH21 Comdty"]})
        out = historical_extractor._extract_underlying_tickers_from_chain(df)
        assert out == ["TYZ20 Comdty", "TYH21 Comdty"]

    def test_alternate_column_naming_is_recognised(self) -> None:
        # Some xbbg vintages use snake_case here.
        df = pd.DataFrame({"security_description": ["TYZ20 Comdty"]})
        out = historical_extractor._extract_underlying_tickers_from_chain(df)
        assert out == ["TYZ20 Comdty"]

    def test_caller_supplied_fallback_takes_priority(self) -> None:
        """When a playbook overrides ``chain_column_name``, that column is
        consulted before any of the built-in candidates."""
        df = pd.DataFrame({
            "Security Description": ["WRONG_ONE"],
            "my_custom_col": ["TYZ20 Comdty"],
        })
        out = historical_extractor._extract_underlying_tickers_from_chain(
            df, fallback_column="my_custom_col"
        )
        assert out == ["TYZ20 Comdty"]

    def test_first_string_column_fallback(self) -> None:
        """If no named candidate matches, the helper falls back to the first
        object-dtype column."""
        df = pd.DataFrame({"completely_unexpected": ["TYZ20 Comdty"]})
        out = historical_extractor._extract_underlying_tickers_from_chain(df)
        assert out == ["TYZ20 Comdty"]

    def test_nan_values_are_filtered_out(self) -> None:
        df = pd.DataFrame({"Security Description": ["TYZ20 Comdty", None, "TYH21 Comdty"]})
        out = historical_extractor._extract_underlying_tickers_from_chain(df)
        assert out == ["TYZ20 Comdty", "TYH21 Comdty"]

    def test_whitespace_is_stripped(self) -> None:
        df = pd.DataFrame({"Security Description": ["  TYZ20 Comdty  "]})
        out = historical_extractor._extract_underlying_tickers_from_chain(df)
        assert out == ["TYZ20 Comdty"]


# ============================================================================
# Expiry-roll window computation — the algorithmic core of A1
# ============================================================================


class TestComputeEffectiveWindowsExpiryRoll:
    """The ``expiry_roll`` convention from ADR 0002:

      * C_1: effective_from = C_1.first_trade_field, effective_to = C_1.roll_field
      * C_i: effective_from = C_{i-1}.roll_field + 1, effective_to = C_i.roll_field
      * C_n: effective_from = C_{n-1}.roll_field + 1, effective_to = NULL
    """

    @staticmethod
    def _make_contract(
        ticker: str,
        first_trade: str,
        last_tradeable: str,
    ) -> Dict[str, Any]:
        return {
            "TICKER": ticker,
            "FUT_FIRST_TRADE_DT": first_trade,
            "LAST_TRADEABLE_DT": last_tradeable,
        }

    def test_empty_input_returns_empty(self) -> None:
        assert historical_extractor._compute_effective_windows_expiry_roll(
            contracts=[],
            roll_field_column="LAST_TRADEABLE_DT",
            first_trade_field_column="FUT_FIRST_TRADE_DT",
        ) == []

    def test_single_contract_open_window(self) -> None:
        """A single contract's window starts at its first-trade-date and
        is open-ended (current front contract with no successor yet)."""
        contracts = [self._make_contract("TYZ24", "2023-12-21", "2024-12-19")]
        out = historical_extractor._compute_effective_windows_expiry_roll(
            contracts=contracts,
            roll_field_column="LAST_TRADEABLE_DT",
            first_trade_field_column="FUT_FIRST_TRADE_DT",
        )
        assert len(out) == 1
        assert out[0]["effective_from"] == "2023-12-21"
        assert out[0]["effective_to"] is None

    def test_three_contracts_chain(self) -> None:
        """Canonical three-contract chain: oldest anchors at its
        first-trade-date; middle and last anchor at prior roll-date + 1;
        only the last has effective_to = None."""
        contracts = [
            self._make_contract("TYZ23", "2022-12-21", "2023-12-19"),
            self._make_contract("TYH24", "2023-03-21", "2024-03-19"),
            self._make_contract("TYM24", "2023-06-21", "2024-06-18"),
        ]
        out = historical_extractor._compute_effective_windows_expiry_roll(
            contracts=contracts,
            roll_field_column="LAST_TRADEABLE_DT",
            first_trade_field_column="FUT_FIRST_TRADE_DT",
        )
        assert len(out) == 3
        # Sorted ascending by roll-date.
        assert out[0]["effective_from"] == "2022-12-21"  # first-trade of TYZ23
        assert out[0]["effective_to"] == "2023-12-19"
        assert out[1]["effective_from"] == "2023-12-20"  # day after TYZ23 expired
        assert out[1]["effective_to"] == "2024-03-19"
        assert out[2]["effective_from"] == "2024-03-20"
        assert out[2]["effective_to"] is None  # currently in effect

    def test_contracts_out_of_order_are_sorted_by_roll_date(self) -> None:
        """Input order should not affect output — the helper sorts internally."""
        contracts = [
            self._make_contract("TYM24", "2023-06-21", "2024-06-18"),
            self._make_contract("TYZ23", "2022-12-21", "2023-12-19"),
            self._make_contract("TYH24", "2023-03-21", "2024-03-19"),
        ]
        out = historical_extractor._compute_effective_windows_expiry_roll(
            contracts=contracts,
            roll_field_column="LAST_TRADEABLE_DT",
            first_trade_field_column="FUT_FIRST_TRADE_DT",
        )
        # The output is the same as the in-order three-chain test.
        assert out[0]["effective_to"] == "2023-12-19"
        assert out[1]["effective_to"] == "2024-03-19"
        assert out[2]["effective_to"] is None

    def test_contracts_missing_either_anchor_are_dropped(self) -> None:
        """A contract missing its roll_field or first_trade_field is
        skipped (defensive against partial bdp output). Coverage of the
        rest of the chain is unaffected."""
        contracts = [
            self._make_contract("TYZ23", "2022-12-21", "2023-12-19"),
            {"TICKER": "TYH24", "LAST_TRADEABLE_DT": None, "FUT_FIRST_TRADE_DT": None},
            self._make_contract("TYM24", "2023-06-21", "2024-06-18"),
        ]
        out = historical_extractor._compute_effective_windows_expiry_roll(
            contracts=contracts,
            roll_field_column="LAST_TRADEABLE_DT",
            first_trade_field_column="FUT_FIRST_TRADE_DT",
        )
        assert len(out) == 2
        # TYM24 is now adjacent to TYZ23 — its window starts the day after TYZ23 expired.
        assert out[0]["effective_to"] == "2023-12-19"
        assert out[1]["effective_from"] == "2023-12-20"
        assert out[1]["effective_to"] is None

    def test_unparseable_dates_are_dropped(self) -> None:
        """A contract whose date fields are non-parseable strings is dropped."""
        contracts = [
            self._make_contract("TYZ23", "2022-12-21", "2023-12-19"),
            self._make_contract("BAD", "not-a-date", "also-not-a-date"),
            self._make_contract("TYM24", "2023-06-21", "2024-06-18"),
        ]
        out = historical_extractor._compute_effective_windows_expiry_roll(
            contracts=contracts,
            roll_field_column="LAST_TRADEABLE_DT",
            first_trade_field_column="FUT_FIRST_TRADE_DT",
        )
        assert len(out) == 2

    def test_static_field_payload_is_carried_through(self) -> None:
        """The extractor adds typed-column values (contract_code,
        security_name, tick_size, …) to each contract dict; the window
        computer must preserve every non-anchor key onto the output row."""
        contracts = [{
            "TICKER": "TYZ23",
            "FUT_FIRST_TRADE_DT": "2022-12-21",
            "LAST_TRADEABLE_DT": "2023-12-19",
            "contract_code": "TYZ23",
            "security_name": "US 10Y T-NOTE FUT Dec 23",
            "tick_size": 0.015625,
        }]
        out = historical_extractor._compute_effective_windows_expiry_roll(
            contracts=contracts,
            roll_field_column="LAST_TRADEABLE_DT",
            first_trade_field_column="FUT_FIRST_TRADE_DT",
        )
        assert out[0]["contract_code"] == "TYZ23"
        assert out[0]["security_name"] == "US 10Y T-NOTE FUT Dec 23"
        assert out[0]["tick_size"] == pytest.approx(0.015625)

    def test_output_windows_satisfy_overlap_gate(self) -> None:
        """End-to-end smoke: the windows produced by ``expiry_roll`` must
        pass the shared overlap validator (since the validator implements
        the same boundary rule as the EXCLUDE constraint)."""
        from ingestion.metadata_history import validate_no_overlaps

        contracts = [
            self._make_contract("TYZ23", "2022-12-21", "2023-12-19"),
            self._make_contract("TYH24", "2023-03-21", "2024-03-19"),
            self._make_contract("TYM24", "2023-06-21", "2024-06-18"),
        ]
        windows = historical_extractor._compute_effective_windows_expiry_roll(
            contracts=contracts,
            roll_field_column="LAST_TRADEABLE_DT",
            first_trade_field_column="FUT_FIRST_TRADE_DT",
        )
        # Group as a single-ticker dict matching the validator's expected input.
        conflicts = validate_no_overlaps({"TY1 Comdty": windows})
        assert conflicts == []


# ============================================================================
# Playbook section parser — gates metadata-history mode per playbook
# ============================================================================


class TestResolveMetadataHistorySection:
    """``_resolve_metadata_history_section`` returns the section dict for
    playbooks that opt in (with the required minimal shape) and ``None``
    for playbooks that don't. Playbooks with a malformed enabled section
    fall back to ``None`` rather than raising — the loop logs the skip,
    keeping the run alive for other playbooks."""

    @staticmethod
    def _well_formed_section() -> Dict[str, Any]:
        return {
            "enabled": True,
            "chain_field": "FUT_CHAIN",
            "roll_convention": {
                "type": "expiry_roll",
                "roll_field": "LAST_TRADEABLE_DT",
                "first_trade_field": "FUT_FIRST_TRADE_DT",
            },
            "static_fields": [
                {"column_name": "contract_code", "bloomberg_field": "TICKER"},
            ],
        }

    def test_no_section_returns_none(self) -> None:
        assert historical_extractor._resolve_metadata_history_section({}) is None

    def test_disabled_returns_none(self) -> None:
        pb = {"metadata_history": {**self._well_formed_section(), "enabled": False}}
        assert historical_extractor._resolve_metadata_history_section(pb) is None

    def test_enabled_but_no_static_fields_returns_none(self) -> None:
        section = self._well_formed_section()
        section["static_fields"] = []
        assert historical_extractor._resolve_metadata_history_section(
            {"metadata_history": section}
        ) is None

    def test_enabled_but_no_chain_field_returns_none(self) -> None:
        section = self._well_formed_section()
        del section["chain_field"]
        assert historical_extractor._resolve_metadata_history_section(
            {"metadata_history": section}
        ) is None

    def test_well_formed_section_returns_section(self) -> None:
        pb = {"metadata_history": self._well_formed_section()}
        out = historical_extractor._resolve_metadata_history_section(pb)
        assert out is not None
        assert out["chain_field"] == "FUT_CHAIN"

    def test_unsupported_roll_convention_type_raises(self) -> None:
        """ADR 0002 ships one roll-convention type (``expiry_roll``).
        Future values require an ADR amendment + extractor change. Until
        then, any other value is a hard refuse."""
        section = self._well_formed_section()
        section["roll_convention"]["type"] = "first_notice_roll"
        with pytest.raises(ValueError, match="not supported"):
            historical_extractor._resolve_metadata_history_section(
                {"metadata_history": section}
            )

    def test_default_roll_convention_type_is_expiry_roll(self) -> None:
        """If ``roll_convention.type`` is absent, the helper treats it as
        ``expiry_roll`` (the only supported type)."""
        section = self._well_formed_section()
        del section["roll_convention"]["type"]
        out = historical_extractor._resolve_metadata_history_section(
            {"metadata_history": section}
        )
        assert out is not None
