"""tests/state/test_extractor_dataframe_agnostic.py — the extractor xbbg-output
normalisers must be agnostic to the dataframe shape the installed xbbg returns.

Older xbbg returns classic pandas objects (WIDE: ticker/date index, field
columns). Newer xbbg returns a Narwhals frame in LONG/tidy shape
(``ticker | field | value``). ``_coerce_to_pandas()`` plus the long-format
branches must yield IDENTICAL results for both shapes, and the classic-pandas
path must be byte-identical to the pre-narwhals behaviour.

These are pure-Python transforms — no Bloomberg terminal needed. ``xbbg`` is
mocked before import, per the convention in test_metadata_history_extraction.py.

Both extractors (``historical_extractor`` and ``incremental_extractor``) carry
the same helpers by design, so every test runs against both.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Make sibling packages importable.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# The extractors import ``xbbg`` at module load — patch it BEFORE the import so
# this module collects on a machine without a Bloomberg terminal.
with patch.dict(sys.modules, {"xbbg": MagicMock(), "xbbg.blp": MagicMock()}):
    from utils import historical_extractor as _hist
    from utils import incremental_extractor as _incr

EXTRACTORS = [
    pytest.param(_hist, id="historical"),
    pytest.param(_incr, id="incremental"),
]


def _bdh_rows(df: pd.DataFrame):
    """A ``_normalize_bdh_output`` frame -> a comparable sorted list of tuples."""
    return sorted(
        (r["trade_date"], r["ticker"], r["field_name"], float(r["field_value"]))
        for _, r in df.iterrows()
    )


# ============================================================================
# _coerce_to_pandas — strict no-op for classic pandas; unwraps narwhals.
# ============================================================================
@pytest.mark.parametrize("mod", EXTRACTORS)
def test_coerce_is_identity_for_pandas(mod):
    frame = pd.DataFrame({"a": [1]})
    assert mod._coerce_to_pandas(frame) is frame
    series = pd.Series([1, 2])
    assert mod._coerce_to_pandas(series) is series
    assert mod._coerce_to_pandas(None) is None


@pytest.mark.parametrize("mod", EXTRACTORS)
def test_normalizers_accept_narwhals_frames(mod):
    """A real Narwhals frame (newer xbbg) must normalise correctly."""
    nw = pytest.importorskip("narwhals")
    bdp_long = pd.DataFrame(
        {"ticker": ["T1"], "field": ["SECURITY_DES"], "value": ["Foo"]}
    )
    assert mod._normalize_bdp_output(nw.from_native(bdp_long), "T1") == {
        "SECURITY_DES": "Foo"
    }
    idx = pd.to_datetime(["2024-01-01"])
    bdh_long = pd.DataFrame(
        {"date": idx, "ticker": ["GT10 Govt"], "field": ["PX_LAST"], "value": [1.0]}
    )
    assert _bdh_rows(
        mod._normalize_bdh_output(nw.from_native(bdh_long), "GT10 Govt")
    ) == [("2024-01-01", "GT10 Govt", "PX_LAST", 1.0)]


# ============================================================================
# _normalize_bdp_output — classic WIDE and newer LONG must agree.
# ============================================================================
@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bdp_wide_and_long_agree(mod):
    wide = pd.DataFrame(
        {"SECURITY_DES": ["Foo"], "MATURITY": ["2030-01-01"]}, index=["T1"]
    )
    long = pd.DataFrame(
        {"ticker": ["T1", "T1"], "field": ["SECURITY_DES", "MATURITY"],
         "value": ["Foo", "2030-01-01"]}
    )
    expected = {"SECURITY_DES": "Foo", "MATURITY": "2030-01-01"}
    assert mod._normalize_bdp_output(wide, "T1") == expected
    assert mod._normalize_bdp_output(long, "T1") == expected


@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bdp_long_canonicalised_single_ticker_accepted(mod):
    """No exact match but a SINGLE ticker in the response = Bloomberg
    canonicalising the requested ticker -> accept it."""
    long = pd.DataFrame(
        {"ticker": ["T 4 3/8 05/15/36 Govt", "T 4 3/8 05/15/36 Govt"],
         "field": ["SECURITY_DES", "MATURITY"], "value": ["Foo", "2030-01-01"]}
    )
    assert mod._normalize_bdp_output(long, "/isin/US91282CQQ77") == {
        "SECURITY_DES": "Foo", "MATURITY": "2030-01-01"
    }


@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bdp_long_multi_ticker_no_match_refuses(mod):
    """No exact match AND >1 distinct ticker -> refuse, never mis-attribute."""
    long = pd.DataFrame(
        {"ticker": ["T1", "T2"], "field": ["SECURITY_DES", "SECURITY_DES"],
         "value": ["Foo", "Bar"]}
    )
    assert mod._normalize_bdp_output(long, "T3") == {}


@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bdp_long_multi_ticker_exact_match_isolated(mod):
    """>1 ticker but an exact match exists -> only the matched ticker's fields."""
    long = pd.DataFrame(
        {"ticker": ["T1", "T2"], "field": ["SECURITY_DES", "SECURITY_DES"],
         "value": ["Foo", "Bar"]}
    )
    assert mod._normalize_bdp_output(long, "T2") == {"SECURITY_DES": "Bar"}


# ============================================================================
# _normalize_bdh_output — classic WIDE (flat + MultiIndex) and newer LONG.
# ============================================================================
@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bdh_wide_and_long_agree(mod):
    idx = pd.to_datetime(["2024-01-01", "2024-01-02"])
    wide = pd.DataFrame({"PX_LAST": [1.0, 2.0]}, index=idx)
    long = pd.DataFrame(
        {"date": idx, "ticker": ["GT10 Govt", "GT10 Govt"],
         "field": ["PX_LAST", "PX_LAST"], "value": [1.0, 2.0]}
    )
    expected = [("2024-01-01", "GT10 Govt", "PX_LAST", 1.0),
                ("2024-01-02", "GT10 Govt", "PX_LAST", 2.0)]
    assert _bdh_rows(mod._normalize_bdh_output(wide, "GT10 Govt")) == expected
    assert _bdh_rows(mod._normalize_bdh_output(long, "GT10 Govt")) == expected


@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bdh_classic_multiindex_unchanged(mod):
    """Classic multi-ticker wide bdh: MultiIndex (ticker, field) columns."""
    idx = pd.to_datetime(["2024-01-01"])
    cols = pd.MultiIndex.from_tuples(
        [("GT2 Govt", "PX_LAST"), ("GT10 Govt", "PX_LAST")]
    )
    wide = pd.DataFrame([[1.0, 2.0]], index=idx, columns=cols)
    assert _bdh_rows(mod._normalize_bdh_output(wide, "fallback")) == [
        ("2024-01-01", "GT10 Govt", "PX_LAST", 2.0),
        ("2024-01-01", "GT2 Govt", "PX_LAST", 1.0),
    ]


@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bdh_empty_returns_contract_columns(mod):
    out = mod._normalize_bdh_output(pd.DataFrame(), "T1")
    assert list(out.columns) == [
        "trade_date", "ticker", "field_name", "field_value"
    ]
    assert out.empty


# ============================================================================
# _normalize_bdp_batch_output — classic WIDE and newer LONG must agree.
# ============================================================================
@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bdp_batch_wide_and_long_agree(mod):
    wide = pd.DataFrame({"PX_LAST": [1.0, 2.0]}, index=["T1", "T2"])
    long = pd.DataFrame(
        {"ticker": ["T1", "T2"], "field": ["PX_LAST", "PX_LAST"],
         "value": [1.0, 2.0]}
    )
    expected = {"T1": {"PX_LAST": 1.0}, "T2": {"PX_LAST": 2.0}}
    assert mod._normalize_bdp_batch_output(wide, ["T1", "T2"]) == expected
    assert mod._normalize_bdp_batch_output(long, ["T1", "T2"]) == expected


# ============================================================================
# _extract_underlying_tickers_from_chain — bds bulk output, both shapes.
# ============================================================================
@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bds_chain_classic_frame(mod):
    chain = pd.DataFrame({"value": ["TICK1 Comdty", "TICK2 Comdty"]})
    assert mod._extract_underlying_tickers_from_chain(chain) == [
        "TICK1 Comdty", "TICK2 Comdty"
    ]


@pytest.mark.parametrize("mod", EXTRACTORS)
def test_bds_chain_narwhals_frame(mod):
    nw = pytest.importorskip("narwhals")
    chain = nw.from_native(pd.DataFrame({"value": ["TICK1 Comdty"]}))
    assert mod._extract_underlying_tickers_from_chain(chain) == ["TICK1 Comdty"]
