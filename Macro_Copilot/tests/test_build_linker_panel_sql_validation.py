#!/usr/bin/env python3
"""
test_build_linker_panel_sql_validation.py — Linker Panel SQL validator
=======================================================================

Independently reproduces the ``build_linker_panel`` primitive's wide
inflation-linker panel via a hand-written SQL ``DISTINCT ON`` pivot
against the live ``macro_data.v_market_data_daily_enriched`` view +
``macro_data.instrument_master`` (for the per-bond maturity_date used
in the column-order sort).

The validator does NOT call back into the Python primitive's backend
(``shared.analytics.panel_assembly.fetch_linker_panel_by_vendor_ticker``)
— it issues its own SQL and pivots in raw pandas so the cross-check
is a real independent baseline.

Test cases (all PINNED so the validator is deterministic across
runs):

  1. USD_TIPS-only narrow universe over
     ``2024-01-02 .. 2024-02-28``.
  2. All-four-curve-families broad universe over
     ``2024-01-02 .. 2024-02-28``.
  3. All-four-curve-families broad universe with
     ``calendar_policy='business_days'`` (opt-in override of the
     V1 ``instrument_native`` default).

For every case the validator asserts:

  - row count match
  - column set + order match (Python uses
    (curve_family, maturity_date, vendor_ticker); SQL is sorted
    the same way in pandas after pivot)
  - cell-by-cell PERCENT match within float-precision tolerance
  - the methodology_card field is populated on the Python output
  - the per-curve-family ``inflation_index_family`` reference
    matches the live DB (US_CPI_URBAN / UK_RPI / EU_HICP /
    CAN_CPI)

DB safety: read-only SELECTs only.  Excluded from pytest
collection via ``tests/conftest.py``'s ``collect_ignore`` list
(matches the established sibling pattern).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.inflation_indexed_bonds.tools.build_linker_panel import (  # noqa: E402
    CONFIG_PATH,
    BuildLinkerPanelInput,
    BuildLinkerPanelOutput,
    build_linker_panel,
)
from shared.config import load_tool_config  # noqa: E402


INSTRUMENT_TYPE = "inflation_linker"
DEFAULT_FIELD = "YLD_YTM_MID"

LINKER_CURVE_FAMILIES_ALL: Tuple[str, ...] = (
    "USD_TIPS", "GBP_LINKER", "EUR_FR_LINKER", "CAD_RRB",
)

# Live DB-verified per-curve_family inflation_index_family mapping.
# This is the desk-honest cross-country mapping the methodology
# card surfaces.  Verified at build time (2026-05-24) via
# ``SELECT curve_family, attributes->>'inflation_index_family' FROM
#  instrument_master WHERE instrument_type='inflation_linker'``.
EXPECTED_INDEX_FAMILY: Dict[str, str] = {
    "USD_TIPS": "US_CPI_URBAN",
    "GBP_LINKER": "UK_RPI",
    "EUR_FR_LINKER": "EU_HICP",
    "CAD_RRB": "CAN_CPI",
}


# A pinned anchor inside the ingested linker window
# (2005 .. 2026-04-08) so the validator is deterministic across
# runs.
_PINNED_START = date(2024, 1, 2)
_PINNED_END = date(2024, 2, 28)


# ---------------------------------------------------------------------------
# Case shape: (label, curve_families, start, end, calendar_policy,
#              missing_data_policy)
# ---------------------------------------------------------------------------
Case = Tuple[
    str,
    Tuple[str, ...],
    date,
    Optional[date],
    Optional[str],
    Optional[str],
]


REGRESSION_CASES: List[Case] = [
    (
        "USD_TIPS-only narrow universe over 2024-01-02..2024-02-28",
        ("USD_TIPS",),
        _PINNED_START,
        _PINNED_END,
        None,  # default calendar policy (instrument_native for linkers)
        None,  # default missingness policy (forward_fill_only)
    ),
    (
        "All-curve-families broad universe over 2024-01-02..2024-02-28",
        LINKER_CURVE_FAMILIES_ALL,
        _PINNED_START,
        _PINNED_END,
        None,
        None,
    ),
    (
        "All-curve-families broad universe, business_days calendar opt-in",
        LINKER_CURVE_FAMILIES_ALL,
        _PINNED_START,
        _PINNED_END,
        "business_days",
        None,
    ),
]


_BASELINE_SQL = text(
    """
    SELECT DISTINCT ON (v.vendor_ticker, v.trade_date)
        v.trade_date,
        v.curve_family,
        v.vendor_ticker,
        i.maturity_date,
        v.field_value::double precision AS field_value
    FROM macro_data.v_market_data_daily_enriched AS v
    JOIN macro_data.instrument_master AS i
      ON v.instrument_id = i.instrument_id
    WHERE v.instrument_type = :instrument_type
      AND v.field_name      = :field_name
      AND v.curve_family    = ANY(:curve_families)
      AND v.field_value    IS NOT NULL
      AND v.vendor_ticker  IS NOT NULL
      AND v.trade_date     >= :start_date
      AND v.trade_date     <= :end_date
    ORDER BY v.vendor_ticker, v.trade_date
    """
)


def _maturity_sort_key(md: Any) -> Tuple[int, str]:
    if md is None or (isinstance(md, float) and pd.isna(md)):
        return (10**9, "")
    if hasattr(md, "toordinal"):
        return (int(md.toordinal()), str(md))
    return (10**9, str(md))


def sql_baseline(
    engine,
    *,
    curve_families: Sequence[str],
    start_date: date,
    end_date: date,
    calendar_policy: str,
    field_name: str,
    ffill_limit_days: int,
) -> pd.DataFrame:
    """Independent SQL reproduction of the linker panel.

    Issues its own ``DISTINCT ON`` pivot (NOT a call into the
    Python primitive's backend), pivots in raw pandas, applies the
    same calendar filter the primitive applies, and forward-fills
    with the same ``ffill_limit_days``.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            _BASELINE_SQL,
            {
                "instrument_type": INSTRUMENT_TYPE,
                "field_name": field_name,
                "curve_families": list(curve_families),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        ).mappings().all()

    if not rows:
        return pd.DataFrame()

    raw = pd.DataFrame([dict(r) for r in rows])

    # Build the column-order map: (curve_family, maturity_date,
    # vendor_ticker).  Matches the Python helper's sort key.
    meta = (
        raw[["curve_family", "vendor_ticker", "maturity_date"]]
        .drop_duplicates()
        .copy()
    )
    meta["_mat_key"] = meta["maturity_date"].map(_maturity_sort_key)
    meta = meta.sort_values(
        by=["curve_family", "_mat_key", "vendor_ticker"],
        kind="mergesort",
    )
    ordered_tickers: List[str] = meta["vendor_ticker"].tolist()

    wide = (
        raw.pivot_table(
            index="trade_date",
            columns="vendor_ticker",
            values="field_value",
            aggfunc="first",
        )
        .sort_index()
    )
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))

    # Reorder columns (any missing column becomes fully-NaN).
    for ticker in ordered_tickers:
        if ticker not in wide.columns:
            wide[ticker] = float("nan")
    wide = wide[ordered_tickers]

    if ffill_limit_days and ffill_limit_days > 0:
        wide = wide.ffill(limit=ffill_limit_days)

    if calendar_policy == "business_days":
        wide = wide[wide.index.dayofweek < 5]
    elif calendar_policy == "instrument_native":
        pass
    else:
        raise ValueError(
            f"sql_baseline: unknown calendar_policy "
            f"{calendar_policy!r}."
        )

    return wide.astype(float)


def compare_panels(
    actual: pd.DataFrame,
    expected: pd.DataFrame,
    *,
    tolerance: float = 1e-9,
) -> List[str]:
    mismatches: List[str] = []

    if list(actual.columns) != list(expected.columns):
        mismatches.append(
            "column order drift:\n"
            f"  expected: {list(expected.columns)}\n"
            f"  got:      {list(actual.columns)}"
        )
        return mismatches  # downstream comparisons are pointless

    if len(actual) != len(expected):
        mismatches.append(
            f"row count drift: expected {len(expected)}, "
            f"got {len(actual)}"
        )

    try:
        pd.testing.assert_index_equal(actual.index, expected.index)
    except AssertionError as exc:
        mismatches.append(f"index drift: {exc}")

    common_idx = actual.index.intersection(expected.index)
    for col in expected.columns:
        if col not in actual.columns:
            mismatches.append(f"column {col!r} missing on Python output")
            continue
        diff = (
            actual.loc[common_idx, col] - expected.loc[common_idx, col]
        ).abs()
        max_diff = float(diff.max(skipna=True))
        if pd.isna(max_diff):
            # All cells NaN on both sides — that's fine.
            continue
        if max_diff > tolerance:
            mismatches.append(
                f"column {col!r} drifted from SQL baseline; "
                f"max abs diff = {max_diff}"
            )
    return mismatches


def _assert_methodology_card(
    tool_result: Dict[str, Any],
    curve_families: Sequence[str],
) -> List[str]:
    """Verify the methodology card is populated AND the per-curve
    inflation_index_family matches the live DB."""
    mismatches: List[str] = []
    card = tool_result.get("methodology_card", {})
    if not card:
        return ["methodology_card missing on Python output"]
    for key in (
        "field_name",
        "calendar_policy",
        "missing_data_policy",
        "ffill_limit_days",
        "ffill_source_tag",
        "curve_families",
        "vendor_ticker_column_key",
        "security_name_caveat",
        "index_family_caveat",
        "market_structure_caveat",
        "cross_region_business_days_caveat",
        "curve_family_reference",
        "methodology_label",
    ):
        if key not in card:
            mismatches.append(
                f"methodology_card missing key {key!r}"
            )
    if mismatches:
        return mismatches

    cf_ref = card["curve_family_reference"]
    for cf in curve_families:
        if cf not in cf_ref:
            mismatches.append(
                f"curve_family_reference missing entry for {cf!r}"
            )
            continue
        expected = EXPECTED_INDEX_FAMILY[cf]
        actual_idx = cf_ref[cf].get("inflation_index_family")
        if actual_idx != expected:
            mismatches.append(
                f"curve_family={cf!r} inflation_index_family drift: "
                f"expected {expected!r}, got {actual_idx!r}"
            )
    return mismatches


def run_case(engine, *, case: Case) -> List[str]:
    (
        label,
        curve_families,
        start_dt,
        end_dt,
        calendar_policy_in,
        missing_policy_in,
    ) = case
    cfg = load_tool_config(CONFIG_PATH)
    ffill_limit_days = int(cfg.convention_value("ffill_limit_days"))
    default_calendar = cfg.convention_value("calendar_policy")
    field_name = cfg.convention_value("default_field_name")
    calendar_policy = calendar_policy_in or default_calendar

    params = BuildLinkerPanelInput(
        start_date=start_dt,
        end_date=end_dt,
        curve_families=list(curve_families),
        calendar_policy=calendar_policy_in,
        missing_data_policy=missing_policy_in,
    )
    tool_result = build_linker_panel(
        engine=engine, params=params, config=cfg,
    )
    if "error" in tool_result:
        return [f"Python tool returned error envelope: {tool_result['error']}"]

    card_mismatches = _assert_methodology_card(tool_result, curve_families)
    if card_mismatches:
        return card_mismatches

    validated = BuildLinkerPanelOutput.model_validate(tool_result)
    actual_panel = validated.panel.payload

    expected_wide = sql_baseline(
        engine,
        curve_families=curve_families,
        start_date=start_dt,
        end_date=end_dt,
        calendar_policy=calendar_policy,
        field_name=field_name,
        ffill_limit_days=ffill_limit_days,
    )
    if expected_wide.empty:
        return [
            "SQL baseline returned 0 rows — DB coverage gap?  "
            f"(curve_families={curve_families!r}, "
            f"start={start_dt.isoformat()}, end={end_dt.isoformat()})"
        ]

    return compare_panels(actual_panel, expected_wide, tolerance=1e-9)


def print_header(idx: int, total: int, label: str) -> None:
    print(f"\n[{idx}/{total}] {label}")
    print("-" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the build_linker_panel primitive against an "
            "independent SQL reproduction."
        ),
    )
    parser.parse_args()

    print("=" * 80)
    print("BUILD_LINKER_PANEL — SQL VALIDATION")
    print("=" * 80)
    print(f"  field_name      : {DEFAULT_FIELD}")
    print(f"  instrument_type : {INSTRUMENT_TYPE}")
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    cases = REGRESSION_CASES
    total = len(cases)

    print(f"[2/3] Running {total} tool-vs-SQL comparisons...")
    failed: List[Tuple[str, List[str]]] = []
    for idx, case in enumerate(cases, start=1):
        label = case[0]
        print_header(idx, total, label)
        mismatches = run_case(engine, case=case)
        if mismatches:
            print("FAIL")
            for m in mismatches[:10]:
                print(f"  - {m}")
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more mismatches")
            failed.append((label, mismatches))
        else:
            print("PASS")

    print("\n[3/3] Summary")
    print("-" * 80)
    print(f"  total_cases : {total}")
    print(f"  passed      : {total - len(failed)}")
    print(f"  failed      : {len(failed)}")

    if failed:
        print("\nFAILED CASES:")
        for label, mismatches in failed:
            print(f"  - {label} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
