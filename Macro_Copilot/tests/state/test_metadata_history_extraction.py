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


# ============================================================================
# Batched bdp() — the optimisation that turns ~6,500 single-ticker calls
# on a first full backfill into ~30-150 batched calls. The semantic
# contract MUST be byte-identical to the single-ticker path; these tests
# pin that contract.
# ============================================================================


class TestNormalizeBdpBatchOutput:
    """``_normalize_bdp_batch_output`` parses the xbbg multi-ticker
    DataFrame shape (index=ticker, columns=field) into a
    ``{ticker: {field_upper: value}}`` map. Field names are upper-cased
    to match the single-ticker normaliser's contract so downstream code
    is agnostic to which fetch path produced the values."""

    def test_empty_input_returns_empty(self) -> None:
        assert historical_extractor._normalize_bdp_batch_output(None, []) == {}
        assert historical_extractor._normalize_bdp_batch_output(pd.DataFrame(), []) == {}

    def test_two_tickers_two_fields(self) -> None:
        df = pd.DataFrame(
            {
                "LAST_TRADEABLE_DT": ["2023-12-19", "2024-03-19"],
                "FUT_FIRST_TRADE_DT": ["2022-12-21", "2023-03-21"],
            },
            index=["TYZ23 Comdty", "TYH24 Comdty"],
        )
        out = historical_extractor._normalize_bdp_batch_output(
            df, requested_tickers=["TYZ23 Comdty", "TYH24 Comdty"]
        )
        assert sorted(out.keys()) == ["TYH24 Comdty", "TYZ23 Comdty"]
        assert out["TYZ23 Comdty"]["LAST_TRADEABLE_DT"] == "2023-12-19"
        assert out["TYH24 Comdty"]["FUT_FIRST_TRADE_DT"] == "2023-03-21"

    def test_field_names_are_upper_cased(self) -> None:
        """Matches the single-ticker normaliser's convention so downstream
        ``field_to_column.get(fld_upper)`` lookups work identically
        regardless of which fetch path produced the dict."""
        df = pd.DataFrame(
            {"last_tradeable_dt": ["2023-12-19"]},
            index=["TYZ23 Comdty"],
        )
        out = historical_extractor._normalize_bdp_batch_output(
            df, requested_tickers=["TYZ23 Comdty"]
        )
        assert "LAST_TRADEABLE_DT" in out["TYZ23 Comdty"]
        assert "last_tradeable_dt" not in out["TYZ23 Comdty"]

    def test_unresolved_ticker_is_absent_from_output(self) -> None:
        """Bloomberg returns no row for tickers it can't resolve — those
        tickers simply don't appear in the output map. Caller skips
        them downstream (same as the single-call empty-result path)."""
        df = pd.DataFrame(
            {"LAST_TRADEABLE_DT": ["2023-12-19"]},
            index=["TYZ23 Comdty"],
        )
        out = historical_extractor._normalize_bdp_batch_output(
            df, requested_tickers=["TYZ23 Comdty", "FAKE99 Comdty"]
        )
        assert "TYZ23 Comdty" in out
        assert "FAKE99 Comdty" not in out

    def test_pandas_timestamp_normalised_to_iso_string(self) -> None:
        """``_clean_scalar`` runs on every cell — pandas Timestamp values
        become ISO date strings so the parquet round-trips deterministically."""
        df = pd.DataFrame(
            {"LAST_TRADEABLE_DT": [pd.Timestamp("2023-12-19")]},
            index=["TYZ23 Comdty"],
        )
        out = historical_extractor._normalize_bdp_batch_output(
            df, requested_tickers=["TYZ23 Comdty"]
        )
        assert out["TYZ23 Comdty"]["LAST_TRADEABLE_DT"] == "2023-12-19"

    def test_nan_normalised_to_none(self) -> None:
        df = pd.DataFrame(
            {"LAST_TRADEABLE_DT": ["2023-12-19", float("nan")]},
            index=["TYZ23 Comdty", "BAD99 Comdty"],
        )
        out = historical_extractor._normalize_bdp_batch_output(
            df, requested_tickers=["TYZ23 Comdty", "BAD99 Comdty"]
        )
        assert out["TYZ23 Comdty"]["LAST_TRADEABLE_DT"] == "2023-12-19"
        assert out["BAD99 Comdty"]["LAST_TRADEABLE_DT"] is None


class TestFetchUnderlyingContractsStaticBatch:
    """``_fetch_underlying_contracts_static_batch`` dedups + chunks +
    falls back to single-ticker calls when a batched chunk raises."""

    def test_empty_inputs_return_empty(self) -> None:
        assert historical_extractor._fetch_underlying_contracts_static_batch(
            underlying_tickers=[],
            bloomberg_fields=["LAST_TRADEABLE_DT"],
            request_kwargs={},
        ) == {}
        assert historical_extractor._fetch_underlying_contracts_static_batch(
            underlying_tickers=["TYZ23 Comdty"],
            bloomberg_fields=[],
            request_kwargs={},
        ) == {}

    def test_dedups_repeated_tickers_one_call(self) -> None:
        """Passing the same ticker 8 times (typical for policy_futures
        where SFR1-8 share underlyings) results in ONE bdp call for it."""
        call_count = {"n": 0}
        captured_tickers: List[List[str]] = []

        def fake_bdp(tickers, flds, **kwargs):
            call_count["n"] += 1
            captured_tickers.append(list(tickers) if isinstance(tickers, list) else [tickers])
            df = pd.DataFrame(
                {"LAST_TRADEABLE_DT": ["2023-12-19"] * len(tickers)},
                index=list(tickers),
            )
            return df

        with patch.object(historical_extractor.blp, "bdp", side_effect=fake_bdp):
            out = historical_extractor._fetch_underlying_contracts_static_batch(
                underlying_tickers=["TYZ23 Comdty"] * 8,
                bloomberg_fields=["LAST_TRADEABLE_DT"],
                request_kwargs={},
            )
        assert call_count["n"] == 1
        assert captured_tickers[0] == ["TYZ23 Comdty"]  # deduped
        assert out == {"TYZ23 Comdty": {"LAST_TRADEABLE_DT": "2023-12-19"}}

    def test_chunks_by_max_tickers_per_request(self) -> None:
        """120 distinct tickers with chunk size 50 → 3 bdp calls
        (50 + 50 + 20)."""
        captured_chunks: List[List[str]] = []

        def fake_bdp(tickers, flds, **kwargs):
            captured_chunks.append(list(tickers))
            df = pd.DataFrame(
                {"LAST_TRADEABLE_DT": ["2024-01-01"] * len(tickers)},
                index=list(tickers),
            )
            return df

        many_tickers = [f"FAKE{i} Comdty" for i in range(120)]
        with patch.object(historical_extractor.blp, "bdp", side_effect=fake_bdp):
            out = historical_extractor._fetch_underlying_contracts_static_batch(
                underlying_tickers=many_tickers,
                bloomberg_fields=["LAST_TRADEABLE_DT"],
                request_kwargs={},
                ticker_chunk_size=50,
            )
        assert [len(c) for c in captured_chunks] == [50, 50, 20]
        assert len(out) == 120

    def test_batch_failure_falls_back_to_single_calls(self) -> None:
        """When the batched bdp() raises, the chunk falls back to
        single-ticker calls one at a time, preserving the no-data-loss
        contract (a one-bad-contract problem must not lose data for the
        other tickers in the chunk)."""
        single_call_count = {"n": 0}
        single_call_tickers: List[str] = []

        def fake_bdp(tickers, flds, **kwargs):
            if isinstance(tickers, list):
                # Batched call: raise to trigger fallback.
                raise RuntimeError("synthetic terminal hiccup")
            # Single call: return one row.
            single_call_count["n"] += 1
            single_call_tickers.append(tickers)
            df = pd.DataFrame(
                {"LAST_TRADEABLE_DT": ["2023-12-19"]},
                index=[tickers],
            )
            return df

        with patch.object(historical_extractor.blp, "bdp", side_effect=fake_bdp):
            out = historical_extractor._fetch_underlying_contracts_static_batch(
                underlying_tickers=["TYZ23 Comdty", "TYH24 Comdty"],
                bloomberg_fields=["LAST_TRADEABLE_DT"],
                request_kwargs={},
            )
        assert single_call_count["n"] == 2  # one fallback call per ticker
        assert sorted(single_call_tickers) == ["TYH24 Comdty", "TYZ23 Comdty"]
        assert "TYZ23 Comdty" in out
        assert "TYH24 Comdty" in out

    def test_output_drives_same_window_math_as_single_calls(self) -> None:
        """End-to-end semantic equivalence: feed the batch fetcher's
        output through ``_compute_effective_windows_expiry_roll`` and
        confirm the resulting windows pass the overlap gate — the same
        contract the single-call path satisfies."""

        # Synthetic chain with three sequential contracts.
        def fake_bdp(tickers, flds, **kwargs):
            # Batched call — return three rows in the order requested.
            data = {
                "TYZ23 Comdty": ("2022-12-21", "2023-12-19"),
                "TYH24 Comdty": ("2023-03-21", "2024-03-19"),
                "TYM24 Comdty": ("2023-06-21", "2024-06-18"),
            }
            t_list = list(tickers) if isinstance(tickers, list) else [tickers]
            return pd.DataFrame(
                {
                    "FUT_FIRST_TRADE_DT": [data[t][0] for t in t_list],
                    "LAST_TRADEABLE_DT": [data[t][1] for t in t_list],
                },
                index=t_list,
            )

        with patch.object(historical_extractor.blp, "bdp", side_effect=fake_bdp):
            static = historical_extractor._fetch_underlying_contracts_static_batch(
                underlying_tickers=["TYZ23 Comdty", "TYH24 Comdty", "TYM24 Comdty"],
                bloomberg_fields=["FUT_FIRST_TRADE_DT", "LAST_TRADEABLE_DT"],
                request_kwargs={},
            )

        contracts = []
        for ticker, fields in static.items():
            contracts.append({"_underlying": ticker, **fields})

        windows = historical_extractor._compute_effective_windows_expiry_roll(
            contracts=contracts,
            roll_field_column="LAST_TRADEABLE_DT",
            first_trade_field_column="FUT_FIRST_TRADE_DT",
        )
        # Three contracts → three windows; last is open-ended.
        assert len(windows) == 3
        assert windows[-1]["effective_to"] is None
        # And the windows pass the overlap gate (which is the contract
        # both the extractor's pre-write and the DB EXCLUDE enforce).
        conflicts = historical_extractor._validate_no_overlaps({"TY1 Comdty": windows})
        assert conflicts == []
