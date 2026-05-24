#!/usr/bin/env python3
"""
test_get_otr_history_sql_validation.py — OTR-history validator
==============================================================

Independently reproduces ``get_otr_history``'s logic against the live
TimescaleDB and compares row-for-row.

The Python primitive reads ``macro_data.otr_history`` JOIN
``macro_data.instrument_master`` for one (country, tenor) slot, sorts
by ``effective_from`` ascending, and surfaces the currently-open
window as ``current_metrics``.

The SQL baseline below is a near-clone of the primitive's query but
intentionally written from scratch (not imported from compute.py) so
the parity check is an independent reproduction, not a self-test.

Read-only:
  - Only ``SELECT`` queries — no ``INSERT`` / ``UPDATE`` / ``DELETE``
    / ``ALTER`` / ``DROP``.
  - The script is excluded from pytest collection (see
    ``tests/conftest.py``) — run as a standalone script.

Usage:

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser DB_PASSWORD=...
    python tests/test_get_otr_history_sql_validation.py

Honest absence:
  The resolver is forward-only (TD #27a) and may not yet have
  populated ``otr_history`` for every (country, tenor) slot.  Cases
  the live DB has zero rows for are reported as ``SKIP (empty)``
  rather than failures — empty SCD2 is not a Python-vs-SQL parity
  defect.  Slots that have at least one row in the live DB are the
  ones the parity check exercises.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.sovereign_bonds.tools.get_otr_history import (  # noqa: E402
    OtrHistoryInput,
    get_otr_history,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


Case = Tuple[str, str]

DEFAULT_CASE_COUNT = 8
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365  # matches YAML default_lookback_days (calendar days, 1Y).

# Slots that should be present in any reasonably-populated dev DB.
# Used as deterministic regression coverage; remaining slots filled
# by random sampling from the live universe.
REGRESSION_CASES: List[Case] = [
    ("US", "10Y"),
    ("US", "2Y"),
    ("DE", "10Y"),
]


def choose_test_cases(engine, *, case_count: int, seed: int) -> List[Case]:
    """Pull every (country, tenor) slot that has at least one
    ``otr_history`` row in the live DB.  Deterministically sample to
    ``case_count``, preserving any regression slots that are actually
    present."""
    query = text(
        """
        SELECT country, tenor
        FROM macro_data.otr_history
        GROUP BY country, tenor
        ORDER BY country, tenor
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    pool = [(row["country"], row["tenor"]) for row in rows]
    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    country: str,
    tenor: str,
    lookback_days: int,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the primitive's compute path.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    country, tenor : str
        Slot to query.
    lookback_days : int
        Display window in calendar days, anchored to ``CURRENT_DATE``.

    Returns
    -------
    dict
        Same wire shape as ``get_otr_history``'s output (
        ``current_metrics`` + ``transitions`` + ``methodology_note``),
        but with the methodology_note set to a fixed sentinel because
        the parity check ignores it (it's a constant string that does
        not depend on DB state).
    """
    history_sql = text(
        """
        SELECT
            TO_CHAR(o.effective_from, 'YYYY-MM-DD') AS effective_from,
            TO_CHAR(o.effective_to,   'YYYY-MM-DD') AS effective_to,
            o.otr_instrument_id,
            i.cusip,
            i.isin,
            i.vendor_ticker,
            TO_CHAR(i.maturity_date,  'YYYY-MM-DD') AS maturity_date
        FROM macro_data.otr_history o
        JOIN macro_data.instrument_master i
          ON i.instrument_id = o.otr_instrument_id
        WHERE o.country = :country
          AND o.tenor   = :tenor
          AND daterange(
                  o.effective_from,
                  COALESCE(o.effective_to, 'infinity'::date),
                  '[]'
              ) && daterange(
                  CURRENT_DATE - (:lookback_days * INTERVAL '1 day'),
                  CURRENT_DATE,
                  '[]'
              )
        ORDER BY o.effective_from ASC
        """
    )
    with engine.connect() as conn:
        rows = (
            conn.execute(
                history_sql,
                {
                    "country": country,
                    "tenor": tenor,
                    "lookback_days": lookback_days,
                },
            )
            .mappings()
            .all()
        )

    transitions = [
        {
            "effective_from": r["effective_from"],
            "effective_to": r["effective_to"],
            "otr_instrument_id": int(r["otr_instrument_id"]),
            "cusip": r["cusip"],
            "isin": r["isin"],
            "vendor_ticker": r["vendor_ticker"],
            "maturity_date": r["maturity_date"],
        }
        for r in rows
    ]

    # Currently-OTR snapshot — last row with effective_to IS NULL.
    open_row = next(
        (t for t in reversed(transitions) if t["effective_to"] is None),
        None,
    )

    # as_of_date is CURRENT_DATE at the live DB.
    as_of_sql = text("SELECT TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD') AS today")
    with engine.connect() as conn:
        as_of = conn.execute(as_of_sql).mappings().first()["today"]

    current_metrics = {
        "country": country,
        "tenor": tenor,
        "as_of_date": as_of,
        "otr_instrument_id": open_row["otr_instrument_id"] if open_row else None,
        "cusip": open_row["cusip"] if open_row else None,
        "isin": open_row["isin"] if open_row else None,
        "vendor_ticker": open_row["vendor_ticker"] if open_row else None,
        "maturity_date": open_row["maturity_date"] if open_row else None,
        "current_effective_from": (
            open_row["effective_from"] if open_row else None
        ),
        "transition_count_in_window": len(transitions),
        "lookback_days": lookback_days,
    }

    return {
        "current_metrics": current_metrics,
        "transitions": transitions,
        # methodology_note is a fixed constant string in the tool — not
        # checked here, the compute test pins it.
        "methodology_note": "<not-checked-by-sql-validation>",
    }


def compare_results(tool_result: Dict[str, Any], sql_result: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []

    if "error" in tool_result:
        mismatches.append(f"Tool returned error: {tool_result['error']}")
        return mismatches

    tool_cm = tool_result["current_metrics"]
    sql_cm = sql_result["current_metrics"]

    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_cm,
        sql_payload=sql_cm,
        fields=(
            "country",
            "tenor",
            "as_of_date",
            "otr_instrument_id",
            "cusip",
            "isin",
            "vendor_ticker",
            "maturity_date",
            "current_effective_from",
            "transition_count_in_window",
            "lookback_days",
        ),
        prefix="current_metrics.",
    )

    tool_tx = tool_result["transitions"]
    sql_tx = sql_result["transitions"]
    if len(tool_tx) != len(sql_tx):
        mismatches.append(
            f"transitions length: tool={len(tool_tx)} sql={len(sql_tx)}"
        )
        return mismatches

    for index, (tool_row, sql_row) in enumerate(zip(tool_tx, sql_tx), start=1):
        add_exact_field_mismatches(
            mismatches=mismatches,
            tool_payload=tool_row,
            sql_payload=sql_row,
            fields=(
                "effective_from",
                "effective_to",
                "otr_instrument_id",
                "cusip",
                "isin",
                "vendor_ticker",
                "maturity_date",
            ),
            prefix=f"transitions[{index}].",
        )

    return mismatches


def run_case(engine, *, case: Case, lookback_days: int) -> Tuple[str, List[str]]:
    """Returns (status, mismatches).  status ∈ {'PASS', 'FAIL', 'SKIP'}."""
    country, tenor = case
    tool_result = get_otr_history(
        engine=engine,
        params=OtrHistoryInput(
            country=country,
            tenor=tenor,
            lookback_days=lookback_days,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        country=country,
        tenor=tenor,
        lookback_days=lookback_days,
    )

    # Honest absence: tool returned no transitions AND SQL returned no
    # transitions → SKIP, not a parity defect.
    if (
        not tool_result["transitions"]
        and not sql_result["transitions"]
    ):
        return "SKIP", []

    mismatches = compare_results(tool_result, sql_result)
    return ("PASS" if not mismatches else "FAIL"), mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the get_otr_history tool against direct SQL on "
            "macro_data.otr_history (ADR 0003)."
        )
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    args = parser.parse_args()

    print("=" * 80)
    print("GET_OTR_HISTORY TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live otr_history universe...")
    cases = choose_test_cases(
        engine,
        case_count=args.cases,
        seed=args.seed,
    )
    if not cases:
        print("\n  No (country, tenor) slots have any rows in macro_data.otr_history.")
        print("  This is expected pre-resolver-deployment (TD #27a).  The")
        print("  parity test has nothing to validate; this is honest absence,")
        print("  not a failure.")
        sys.exit(0)
    print_selected_cases(cases, lambda case: f"{case[0]} {case[1]}")

    print("[3/4] Running tool vs SQL comparisons...")
    pass_count = 0
    skip_count = 0
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), f"{case[0]} {case[1]}")
        status, mismatches = run_case(
            engine, case=case, lookback_days=args.days,
        )
        if status == "PASS":
            print("PASS")
            pass_count += 1
        elif status == "SKIP":
            print("SKIP (empty otr_history for slot in window)")
            skip_count += 1
        else:
            print("FAIL")
            for mismatch in mismatches[:10]:
                print(f"  - {mismatch}")
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more mismatches")
            failed_cases.append((case, mismatches))

    print("[4/4] Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {pass_count}")
    print(f"  skipped     : {skip_count}")
    print(f"  failed      : {len(failed_cases)}")

    if failed_cases:
        print("\nFAILED CASES:")
        for case, mismatches in failed_cases:
            print(f"  - {case[0]} {case[1]} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
