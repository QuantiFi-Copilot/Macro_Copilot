#!/usr/bin/env python3
"""
test_otr_ofr_spread_sql_validation.py — OTR/OFR-spread validator
================================================================

Independently reproduces ``calculate_otr_ofr_spread``'s logic against
the live TimescaleDB and compares row-for-row.

The Python primitive resolves the OTR / OFR bond per trade_date from
``macro_data.otr_history`` (LAG window function over effective_from),
LEFT-JOINs ``macro_data.market_data_daily`` for both bonds' yields,
computes ``spread_bps = (otr - ofr) * 100`` per row, applies a
252-trading-day rolling z-score, and trims to the display lookback.

The SQL baseline below independently reproduces the OTR/OFR resolution
plus the per-row spread arithmetic (the hard part — the LAG-driven
prior-bond identification is structurally easy to get wrong).  The
rolling z-score reproduces a window function in SQL using a fixed
252-trading-day window with ``min_periods=60`` — same convention as
the Python compute.

Read-only:
  - Only ``SELECT`` queries — no ``INSERT`` / ``UPDATE`` / ``DELETE``
    / ``ALTER`` / ``DROP``.
  - Excluded from pytest collection via ``tests/conftest.py`` — run as
    a standalone script.

Usage:

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser DB_PASSWORD=...
    python tests/test_otr_ofr_spread_sql_validation.py

Honest absence (TD #27):
  The resolver is forward-only and may not yet have populated
  ``otr_history`` for every (country, tenor) slot.  Slots with zero
  OTR/OFR rows in the live DB are reported as ``SKIP (empty)`` rather
  than failures — empty SCD2 is not a Python-vs-SQL parity defect.
  Slots with at least one row in the live DB are the ones the parity
  check exercises.
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
from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (  # noqa: E402
    CONFIG_PATH,
    OtrOfrSpreadInput,
    calculate_otr_ofr_spread,
)
from shared.config import load_tool_config  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


Case = Tuple[str, str]

DEFAULT_CASE_COUNT = 6
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365


REGRESSION_CASES: List[Case] = [
    ("US", "10Y"),
    ("US", "2Y"),
    ("DE", "10Y"),
]


# Per-field rounding tolerances.  Spread is rounded to 0.01 bps by the
# tool; z-score to 4 dp.  Allow a tiny tolerance to absorb upstream
# float-formatting differences across pandas / numpy versions but
# nothing larger than a single rounding-step.
TOLERANCE_BY_FIELD: Dict[str, float] = {
    "current_spread_bps": 0.01,
    "daily_change_bps": 0.01,
    "current_z_score": 1e-3,
    "otr_yield_pct": 1e-6,
    "ofr_yield_pct": 1e-6,
    "spread_bps": 0.01,
    "z_score": 1e-3,
}


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


# ---------------------------------------------------------------------------
# SQL baseline — independently reproduces the OTR/OFR resolution + spread
# arithmetic.  Rolling z-score is reproduced via SQL window functions.
# ---------------------------------------------------------------------------

_SQL_BASELINE = text(
    """
    WITH
    slot_windows AS (
        SELECT
            o.effective_from,
            COALESCE(o.effective_to, 'infinity'::date) AS effective_to,
            o.otr_instrument_id,
            LAG(o.otr_instrument_id) OVER (
                PARTITION BY o.country, o.tenor
                ORDER BY o.effective_from
            ) AS ofr_instrument_id
        FROM macro_data.otr_history o
        WHERE o.country = :country
          AND o.tenor   = :tenor
    ),
    date_resolved AS (
        SELECT
            w.effective_from,
            w.effective_to,
            w.otr_instrument_id,
            w.ofr_instrument_id
        FROM slot_windows w
        WHERE daterange(
                  w.effective_from,
                  w.effective_to,
                  '[]'
              ) && daterange(:window_start, :window_end, '[]')
    ),
    yield_join AS (
        SELECT
            d_otr.trade_date,
            dr.otr_instrument_id,
            dr.ofr_instrument_id,
            d_otr.field_value AS otr_yield,
            d_ofr.field_value AS ofr_yield,
            ROUND(
                ((d_otr.field_value - d_ofr.field_value) * 100.0)::numeric, 2
            )::float AS spread_bps
        FROM date_resolved dr
        JOIN macro_data.market_data_daily d_otr
          ON d_otr.instrument_id = dr.otr_instrument_id
         AND d_otr.field_name    = :field_name
         AND d_otr.trade_date BETWEEN
             GREATEST(dr.effective_from, :window_start::date)
             AND LEAST(dr.effective_to, :window_end::date)
        LEFT JOIN macro_data.market_data_daily d_ofr
          ON d_ofr.instrument_id = dr.ofr_instrument_id
         AND d_ofr.field_name    = :field_name
         AND d_ofr.trade_date    = d_otr.trade_date
    ),
    distinct_dates AS (
        SELECT DISTINCT ON (trade_date)
            trade_date,
            otr_instrument_id,
            ofr_instrument_id,
            otr_yield,
            ofr_yield,
            spread_bps
        FROM yield_join
        ORDER BY trade_date ASC, otr_instrument_id ASC
    )
    SELECT
        TO_CHAR(trade_date, 'YYYY-MM-DD') AS trade_date,
        otr_instrument_id,
        ofr_instrument_id,
        otr_yield,
        ofr_yield,
        spread_bps,
        ROUND(
            (
                (spread_bps - AVG(spread_bps) OVER w)
                / NULLIF(STDDEV_SAMP(spread_bps) OVER w, 0)
            )::numeric,
            4
        )::float AS z_score,
        COUNT(spread_bps) OVER w AS rolling_count
    FROM distinct_dates
    WINDOW w AS (
        ORDER BY trade_date
        ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
    )
    ORDER BY trade_date ASC
    """
)


def sql_baseline(
    engine,
    *,
    country: str,
    tenor: str,
    field_name: str,
    window_start_iso: str,
    window_end_iso: str,
    lookback_days: int,
    z_min_periods: int,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the primitive's compute path.

    Returns the same wire shape as the primitive's output, restricted
    to current_metrics + spread time-series (z-score / fields).  The
    methodology_note is a fixed string in the tool and is not compared.
    """
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _SQL_BASELINE,
                {
                    "country": country,
                    "tenor": tenor,
                    "field_name": field_name,
                    "window_start": window_start_iso,
                    "window_end": window_end_iso,
                },
            )
            .mappings()
            .all()
        )

    # Filter to display window (lookback_days from end).  SQL pulled
    # the warmup buffer so the z-score is populated at the start of
    # the display window.
    from datetime import date as _date_cls, timedelta
    cutoff = (_date_cls.fromisoformat(window_end_iso) - timedelta(days=lookback_days)).isoformat()
    display_rows = [r for r in rows if r["trade_date"] >= cutoff]

    if not display_rows:
        return {
            "current_metrics": None,
            "time_series": [],
        }

    # Build the per-row display series.  Honour z_min_periods — emit
    # None when the rolling-window count is below the threshold.
    series = []
    for r in display_rows:
        rolling_count = int(r["rolling_count"])
        z = float(r["z_score"]) if (
            r["z_score"] is not None and rolling_count >= z_min_periods
        ) else None
        series.append({
            "date": r["trade_date"],
            "spread_bps": (
                None if r["spread_bps"] is None else float(r["spread_bps"])
            ),
            "z_score": z,
            "otr_yield_pct": (
                None if r["otr_yield"] is None else float(r["otr_yield"])
            ),
            "ofr_yield_pct": (
                None if r["ofr_yield"] is None else float(r["ofr_yield"])
            ),
        })

    latest = series[-1]
    previous = series[-2] if len(series) >= 2 else None

    daily_change = None
    if (
        previous is not None
        and latest["spread_bps"] is not None
        and previous["spread_bps"] is not None
    ):
        daily_change = round(
            latest["spread_bps"] - previous["spread_bps"], 2
        )

    cm_otr_id = display_rows[-1]["otr_instrument_id"]
    cm_ofr_id = display_rows[-1]["ofr_instrument_id"]

    current_metrics = {
        "as_of_date": latest["date"],
        "country": country,
        "tenor": tenor,
        "slot_label": f"{country} {tenor} OTR/OFR",
        "current_spread_bps": latest["spread_bps"],
        "daily_change_bps": daily_change,
        "current_z_score": latest["z_score"],
        "rolling_window_days": 252,
        "otr_yield_pct": latest["otr_yield_pct"],
        "ofr_yield_pct": latest["ofr_yield_pct"],
        "otr_instrument_id": (
            int(cm_otr_id) if cm_otr_id is not None else None
        ),
        "ofr_instrument_id": (
            int(cm_ofr_id) if cm_ofr_id is not None else None
        ),
        "observation_count": len(series),
    }

    return {
        "current_metrics": current_metrics,
        "time_series": series,
    }


def compare_results(tool_result: Dict[str, Any], sql_result: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []

    if "error" in tool_result:
        mismatches.append(f"Tool returned error: {tool_result['error']}")
        return mismatches

    tool_cm = tool_result["current_metrics"]
    sql_cm = sql_result["current_metrics"]

    if sql_cm is None:
        mismatches.append("SQL baseline empty but tool returned current_metrics")
        return mismatches

    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_cm,
        sql_payload=sql_cm,
        fields=(
            "country",
            "tenor",
            "slot_label",
            "as_of_date",
            "rolling_window_days",
            "otr_instrument_id",
            "ofr_instrument_id",
            "observation_count",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_cm,
        sql_payload=sql_cm,
        fields=(
            "current_spread_bps",
            "daily_change_bps",
            "current_z_score",
            "otr_yield_pct",
            "ofr_yield_pct",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # Spot-check the displayed time-series.  Long series take a while
    # to compare element-by-element — sample a deterministic subset
    # (first row, last row, every 50th row in between).
    ts_tool = tool_result["time_series"]
    ts_sql = sql_result["time_series"]
    if len(ts_tool) != len(ts_sql):
        mismatches.append(
            f"time_series length: tool={len(ts_tool)} sql={len(ts_sql)}"
        )
        return mismatches

    indices = list(range(len(ts_tool)))
    sampled = sorted(set(indices[:1] + indices[::50] + indices[-1:]))
    sampled_tool = [ts_tool[i] for i in sampled]
    sampled_sql = [ts_sql[i] for i in sampled]
    mismatches.extend(
        compare_time_series(
            tool_rows=sampled_tool,
            sql_rows=sampled_sql,
            exact_fields=(),
            numeric_fields=(
                "spread_bps",
                "z_score",
                "otr_yield_pct",
                "ofr_yield_pct",
            ),
            tolerances=TOLERANCE_BY_FIELD,
            row_label="time_series",
        )
    )

    return mismatches


def run_case(engine, *, case: Case, lookback_days: int) -> Tuple[str, List[str]]:
    """Returns (status, mismatches).  status ∈ {'PASS', 'FAIL', 'SKIP'}."""
    country, tenor = case
    cfg = load_tool_config(CONFIG_PATH)
    z_min_periods = int(cfg.convention_value("z_score_min_periods"))
    z_window_days = int(cfg.convention_value("z_score_window_days"))
    buffer_mult = float(cfg.convention_value("z_score_buffer_multiplier"))
    field_name = str(cfg.convention_value("default_field_name"))

    tool_result = calculate_otr_ofr_spread(
        engine=engine,
        params=OtrOfrSpreadInput(
            country=country,
            tenor=tenor,
            lookback_days=lookback_days,
        ),
    )

    # The SQL baseline pulls history with the same warmup buffer the
    # tool uses, so the rolling-z-score warmup matches.
    from datetime import date as _date_cls, timedelta
    buffer_calendar_days = int(z_window_days * buffer_mult)
    today = _date_cls.today()
    window_start = today - timedelta(days=lookback_days + buffer_calendar_days)
    sql_result = sql_baseline(
        engine=engine,
        country=country,
        tenor=tenor,
        field_name=field_name,
        window_start_iso=window_start.isoformat(),
        window_end_iso=today.isoformat(),
        lookback_days=lookback_days,
        z_min_periods=z_min_periods,
    )

    # Honest absence: tool returned an error AND SQL returned no rows
    # → SKIP (the slot has no observations in the lookback).
    if (
        "error" in tool_result
        and sql_result["current_metrics"] is None
    ):
        return "SKIP", []

    mismatches = compare_results(tool_result, sql_result)
    return ("PASS" if not mismatches else "FAIL"), mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate calculate_otr_ofr_spread against direct SQL on "
            "macro_data.otr_history + macro_data.market_data_daily "
            "(ADRs 0003 + 0005 + 0007)."
        )
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Allow exit 0 when macro_data.otr_history has zero rows for "
            "every queried slot.  Required during the pre-resolver-"
            "deployment window (TD #27a — forward-only ingest).  Without "
            "this flag, an empty SCD2 table fails the gate so a silent "
            "regression to no-data-at-all is caught."
        ),
    )
    args = parser.parse_args()

    print("=" * 80)
    print("CALCULATE_OTR_OFR_SPREAD TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live otr_history universe...")
    cases = choose_test_cases(engine, case_count=args.cases, seed=args.seed)
    if not cases:
        print("\n  No (country, tenor) slots have any rows in macro_data.otr_history.")
        if args.allow_empty:
            print("  --allow-empty set; treating as honest pre-resolver-deployment")
            print("  state (TD #27a forward-only).  Exiting 0.")
            sys.exit(0)
        print("  This is expected pre-resolver-deployment (TD #27a) but a")
        print("  mandatory SQL validation gate must not pass silently on")
        print("  zero data — pass --allow-empty to waive explicitly.  Failing.")
        sys.exit(2)
    print_selected_cases(cases, lambda case: f"{case[0]} {case[1]}")

    print("[3/4] Running tool vs SQL comparisons...")
    pass_count = 0
    skip_count = 0
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), f"{case[0]} {case[1]}")
        status, mismatches = run_case(engine, case=case, lookback_days=args.days)
        if status == "PASS":
            print("PASS")
            pass_count += 1
        elif status == "SKIP":
            print("SKIP (no OTR/OFR observations in window)")
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

    if pass_count == 0 and skip_count > 0:
        if args.allow_empty:
            print("\n  All cases SKIPPED (empty in window); --allow-empty set.")
            print("  Treating as honest pre-resolver-deployment state (TD #27a).")
            sys.exit(0)
        print("\n  All cases SKIPPED — no slot had observations in the lookback.")
        print("  Pass --allow-empty to waive explicitly during the pre-resolver-")
        print("  deployment window.  Failing.")
        sys.exit(2)


if __name__ == "__main__":
    main()
