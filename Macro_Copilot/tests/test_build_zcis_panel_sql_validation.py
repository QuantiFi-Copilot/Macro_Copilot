#!/usr/bin/env python3
"""
test_build_zcis_panel_sql_validation.py — ZCIS Panel SQL validator
====================================================================

Independently reproduces the ``build_zcis_panel`` primitive's wide
ZCIS panel via a hand-written SQL ``DISTINCT ON`` pivot against the
live ``macro_data.v_market_data_daily_enriched`` view + the
``macro_data.instrument_master`` ``attributes -> pricing_type``
discriminator.

The validator does NOT call back into the Python primitive's
backend (``shared.analytics.panel_assembly.fetch_inflation_swap_panel_by_vendor_ticker``)
— it issues its own SQL and pivots in raw pandas so the
cross-check is a real independent baseline.

Test cases (all PINNED to 2026-04-09, the most recent ingested
trade_date for the ZCIS universe as of this build):

  1. USD-only narrow universe (1Y / 5Y / 10Y) over
     ``2024-01-02 .. 2024-02-28``.
  2. All-three-currencies broad universe over
     ``2024-01-02 .. 2024-02-28``.
  3. Same all-currencies broad universe with
     ``calendar_policy='instrument_native'``.

For every case the validator asserts:

  - row count match
  - column set + order match (Python uses
    (curve_family, tenor_year, vendor_ticker); SQL is sorted the
    same way in pandas after pivot)
  - cell-by-cell PERCENT match within float-precision tolerance
  - the methodology_card field is populated on the Python output

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
from rates_agent.inflation_swaps.tools.build_zcis_panel import (  # noqa: E402
    CONFIG_PATH,
    BuildZcisPanelInput,
    BuildZcisPanelOutput,
    build_zcis_panel,
)
from shared.config import load_tool_config  # noqa: E402


INSTRUMENT_TYPE = "inflation_swap"
PRICING_TYPE = "zero_coupon_breakeven"
DEFAULT_FIELD = "PX_MID"

ZCIS_CURVE_FAMILIES_ALL: Tuple[str, ...] = (
    "USD_ZCIS", "EUR_ZCIS", "GBP_ZCIS",
)


# A pinned anchor inside the ingested ZCIS window (2005 .. 2026-04-09)
# so the validator is deterministic across runs.
_PINNED_START = date(2024, 1, 2)
_PINNED_END = date(2024, 2, 28)


# ---------------------------------------------------------------------------
# Case shape: (label, curve_families, tenors, start, end,
#              calendar_policy, missing_data_policy)
# ---------------------------------------------------------------------------
Case = Tuple[
    str,
    Tuple[str, ...],
    Optional[Tuple[str, ...]],
    date,
    Optional[date],
    Optional[str],
    Optional[str],
]


REGRESSION_CASES: List[Case] = [
    (
        "USD-only narrow universe (1Y, 5Y, 10Y) over 2024-01-02..2024-02-28",
        ("USD_ZCIS",),
        ("1Y", "5Y", "10Y"),
        _PINNED_START,
        _PINNED_END,
        None,  # default calendar policy (business_days)
        None,  # default missingness policy (forward_fill_only)
    ),
    (
        "All-currencies broad universe (full tenor grid) over 2024-01-02..2024-02-28",
        ZCIS_CURVE_FAMILIES_ALL,
        None,
        _PINNED_START,
        _PINNED_END,
        None,
        None,
    ),
    (
        "All-currencies broad universe, instrument_native calendar",
        ZCIS_CURVE_FAMILIES_ALL,
        None,
        _PINNED_START,
        _PINNED_END,
        "instrument_native",
        None,
    ),
]


_BASELINE_SQL = text(
    """
    SELECT DISTINCT ON (v.vendor_ticker, v.trade_date)
        v.trade_date,
        v.curve_family,
        v.tenor,
        v.vendor_ticker,
        v.field_value::double precision AS field_value
    FROM macro_data.v_market_data_daily_enriched AS v
    JOIN macro_data.instrument_master AS i
      ON v.instrument_id = i.instrument_id
    WHERE v.instrument_type = :instrument_type
      AND (i.attributes ->> 'pricing_type') = :pricing_type
      AND v.field_name      = :field_name
      AND v.curve_family    = ANY(:curve_families)
      AND v.tenor          IS NOT NULL
      AND v.field_value    IS NOT NULL
      AND v.vendor_ticker  IS NOT NULL
      AND v.trade_date     >= :start_date
      AND v.trade_date     <= :end_date
    ORDER BY v.vendor_ticker, v.trade_date
    """
)


def _tenor_year_or_inf(tnr: str) -> int:
    if isinstance(tnr, str) and tnr.endswith("Y") and tnr[:-1].isdigit():
        return int(tnr[:-1])
    return 10**9


def sql_baseline(
    engine,
    *,
    curve_families: Sequence[str],
    tenors: Optional[Sequence[str]],
    start_date: date,
    end_date: date,
    calendar_policy: str,
    field_name: str,
    ffill_limit_days: int,
) -> pd.DataFrame:
    """Independent SQL reproduction of the ZCIS panel.

    Issues its own ``DISTINCT ON`` pivot (NOT a call into the
    Python primitive's backend), pivots in raw pandas, applies the
    same business-days calendar filter the primitive applies, and
    forward-fills with the same ``ffill_limit_days``.

    Tenor scoping (when requested) is applied in pandas after the
    raw fetch so the SQL stays simple — equivalent semantics to
    the primitive's ANY(:tenors) filter on a small candidate set.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            _BASELINE_SQL,
            {
                "instrument_type": INSTRUMENT_TYPE,
                "pricing_type": PRICING_TYPE,
                "field_name": field_name,
                "curve_families": list(curve_families),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        ).mappings().all()

    if not rows:
        return pd.DataFrame()

    raw = pd.DataFrame([dict(r) for r in rows])

    if tenors is not None:
        raw = raw[raw["tenor"].isin(set(tenors))]
        if raw.empty:
            return pd.DataFrame()

    # Build the column-order map: (curve_family, tenor_year,
    # vendor_ticker).  Matches the Python helper's sort key.
    meta = (
        raw[["curve_family", "tenor", "vendor_ticker"]]
        .drop_duplicates()
        .copy()
    )
    meta["_tenor_key"] = meta["tenor"].map(_tenor_year_or_inf)
    meta = meta.sort_values(
        by=["curve_family", "_tenor_key", "vendor_ticker"],
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
            # All cells NaN on both sides — that's fine (no
            # mismatch surfaces; ``assert_index_equal`` above
            # caught any structural drift).
            continue
        if max_diff > tolerance:
            mismatches.append(
                f"column {col!r} drifted from SQL baseline; "
                f"max abs diff = {max_diff}"
            )
    return mismatches


def run_case(engine, *, case: Case) -> List[str]:
    (
        label,
        curve_families,
        tenors,
        start_dt,
        end_dt,
        calendar_policy_in,
        missing_policy_in,
    ) = case
    cfg = load_tool_config(CONFIG_PATH)
    ffill_limit_days = int(cfg.convention_value("ffill_limit_days"))
    default_calendar = cfg.convention_value("calendar_policy")
    field_name = cfg.convention_value("default_zcis_rate_field")
    calendar_policy = calendar_policy_in or default_calendar

    params = BuildZcisPanelInput(
        start_date=start_dt,
        end_date=end_dt,
        curve_families=list(curve_families),
        tenors=list(tenors) if tenors is not None else None,
        calendar_policy=calendar_policy_in,
        missing_data_policy=missing_policy_in,
    )
    tool_result = build_zcis_panel(
        engine=engine, params=params, config=cfg,
    )
    if "error" in tool_result:
        return [f"Python tool returned error envelope: {tool_result['error']}"]

    if tool_result.get("methodology_card", {}).get("field_name") is None:
        return [
            "methodology_card.field_name is missing on the Python "
            "output — methodology disclosure not populated."
        ]

    validated = BuildZcisPanelOutput.model_validate(tool_result)
    actual_panel = validated.panel.payload

    expected_wide = sql_baseline(
        engine,
        curve_families=curve_families,
        tenors=tenors,
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
            f"tenors={tenors!r}, start={start_dt.isoformat()}, "
            f"end={end_dt.isoformat()})"
        ]

    return compare_panels(actual_panel, expected_wide, tolerance=1e-9)


def print_header(idx: int, total: int, label: str) -> None:
    print(f"\n[{idx}/{total}] {label}")
    print("-" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the build_zcis_panel primitive against an "
            "independent SQL reproduction."
        ),
    )
    parser.parse_args()

    print("=" * 80)
    print("BUILD_ZCIS_PANEL — SQL VALIDATION")
    print("=" * 80)
    print(f"  field_name      : {DEFAULT_FIELD}")
    print(f"  instrument_type : {INSTRUMENT_TYPE}")
    print(f"  pricing_type    : {PRICING_TYPE}")
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
