#!/usr/bin/env python3
"""
test_curve_spread_sql_validation.py — Sovereign curve-spread validator
========================================================================

Validate ``calculate_curve_spread`` against an independent SQL baseline.

Usage
-----
    python3.10 -m tests.test_curve_spread_sql_validation
    python3.10 -m tests.test_curve_spread_sql_validation --cases 12 --seed 42
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
from rates_agent.sovereign_bonds.tools.curve_spread import calculate_curve_spread  # noqa: E402
from rates_agent.sovereign_bonds.tools.schemas import CurveSpreadInput  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    print_case_header,
    print_selected_cases,
    sample_cases,
    tenor_sort_key,
)


Case = Tuple[str, str, str]

DEFAULT_CASE_COUNT = 12
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"

REGRESSION_CASES: List[Case] = [
    ("UST", "2Y", "10Y"),
    ("DE_BUND", "2Y", "10Y"),
    ("UK_GILT", "5Y", "30Y"),
]

TOLERANCE_BY_FIELD = {
    "current_spread_bps": 0.011,
    "daily_change_bps": 0.011,
    "current_z_score": 0.006,
    "short_tenor_yield": 0.00011,
    "long_tenor_yield": 0.00011,
    "spread_bps": 0.011,
    "z_score": 0.006,
}


def curve_spread_label(short_tenor: str, long_tenor: str) -> str:
    """Mirror the tool's current label formatting exactly."""
    return (
        f"{short_tenor.replace('Y', '')}s"
        f"{long_tenor.replace('Y', '')}s"
    )


def choose_test_cases(engine, *, field_name: str, case_count: int, seed: int) -> List[Case]:
    query = text(
        """
        SELECT curve_family, tenor
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type = 'sovereign_benchmark'
          AND field_name = :field_name
        GROUP BY curve_family, tenor
        HAVING COUNT(*) >= 80
        ORDER BY curve_family, tenor
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(query, {"field_name": field_name}).mappings().all()

    curve_to_tenors: Dict[str, List[str]] = {}
    for row in rows:
        curve_to_tenors.setdefault(row["curve_family"], []).append(row["tenor"])

    all_pairs: List[Case] = []
    for curve_family, tenors in curve_to_tenors.items():
        ordered = sorted(set(tenors), key=tenor_sort_key)
        for i, short_tenor in enumerate(ordered):
            for long_tenor in ordered[i + 1 :]:
                all_pairs.append((curve_family, short_tenor, long_tenor))

    return sample_cases(
        all_pairs,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                tenor,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'sovereign_benchmark'
              AND curve_family = :curve_family
              AND tenor IN (:short_tenor, :long_tenor)
              AND field_name = :field_name
              AND trade_date >= CURRENT_DATE - ((:lookback_days + 378) * INTERVAL '1 day')
        ),
        date_grid AS (
            SELECT DISTINCT trade_date
            FROM raw
        ),
        numbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM date_grid
        ),
        joined AS (
            SELECT
                n.trade_date,
                n.rn,
                MAX(CASE WHEN r.tenor = :short_tenor THEN r.field_value END) AS short_yield_raw,
                MAX(CASE WHEN r.tenor = :long_tenor THEN r.field_value END) AS long_yield_raw
            FROM numbered n
            LEFT JOIN raw r
              ON r.trade_date = n.trade_date
            GROUP BY n.trade_date, n.rn
        ),
        ffill AS (
            SELECT
                *,
                MAX(CASE WHEN short_yield_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS short_last_rn,
                MAX(CASE WHEN long_yield_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS long_last_rn
            FROM joined
        ),
        filled AS (
            SELECT
                trade_date,
                rn,
                CASE
                    WHEN short_yield_raw IS NOT NULL THEN short_yield_raw
                    WHEN short_last_rn IS NOT NULL AND rn - short_last_rn <= 5
                    THEN MAX(CASE WHEN short_yield_raw IS NOT NULL THEN short_yield_raw END)
                         OVER (PARTITION BY short_last_rn)
                    ELSE NULL
                END AS short_yield,
                CASE
                    WHEN long_yield_raw IS NOT NULL THEN long_yield_raw
                    WHEN long_last_rn IS NOT NULL AND rn - long_last_rn <= 5
                    THEN MAX(CASE WHEN long_yield_raw IS NOT NULL THEN long_yield_raw END)
                         OVER (PARTITION BY long_last_rn)
                    ELSE NULL
                END AS long_yield
            FROM ffill
        ),
        aligned AS (
            SELECT
                trade_date,
                rn,
                short_yield,
                long_yield,
                ROUND(((long_yield - short_yield) * 100)::numeric, 2)::double precision AS spread_bps
            FROM filled
            WHERE short_yield IS NOT NULL
              AND long_yield IS NOT NULL
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                short_yield,
                long_yield,
                spread_bps,
                CASE
                    WHEN COUNT(*) OVER w >= 60
                     AND STDDEV_SAMP(spread_bps) OVER w IS NOT NULL
                     AND STDDEV_SAMP(spread_bps) OVER w <> 0
                    THEN ROUND(
                        (
                            (spread_bps - AVG(spread_bps) OVER w)
                            / NULLIF(STDDEV_SAMP(spread_bps) OVER w, 0)
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score
            FROM aligned
            WINDOW w AS (
                ORDER BY rn
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
            )
        ),
        display_rows AS (
            SELECT
                trade_date,
                rn,
                short_yield,
                long_yield,
                spread_bps,
                z_score,
                CASE
                    WHEN LAG(spread_bps) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps) OVER (ORDER BY rn))::numeric,
                        2
                    )::double precision
                END AS daily_change_bps
            FROM scored
            WHERE trade_date >= CURRENT_DATE - (:lookback_days * INTERVAL '1 day')
        )
        SELECT
            TO_CHAR(trade_date, 'YYYY-MM-DD') AS date,
            short_yield,
            long_yield,
            spread_bps,
            z_score,
            daily_change_bps
        FROM display_rows
        ORDER BY date
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            baseline_sql,
            {
                "curve_family": curve_family,
                "short_tenor": short_tenor,
                "long_tenor": long_tenor,
                "field_name": field_name,
                "lookback_days": lookback_days,
            },
        ).mappings().all()

    if not rows:
        return {"error": "SQL baseline returned no rows."}

    latest = rows[-1]
    return {
        "current_metrics": {
            "as_of_date": latest["date"],
            "curve_family": curve_family,
            "spread_label": curve_spread_label(short_tenor, long_tenor),
            "current_spread_bps": latest["spread_bps"],
            "daily_change_bps": latest["daily_change_bps"],
            "current_z_score": latest["z_score"],
            "rolling_window_days": 252,
            "short_tenor_yield": latest["short_yield"],
            "long_tenor_yield": latest["long_yield"],
        },
        "time_series": [
            {
                "date": row["date"],
                "spread_bps": row["spread_bps"],
                "z_score": row["z_score"],
            }
            for row in rows
        ],
    }


def compare_results(tool_result: Dict[str, Any], sql_result: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []

    if "error" in tool_result:
        mismatches.append(f"Tool returned error: {tool_result['error']}")
        return mismatches
    if "error" in sql_result:
        mismatches.append(f"SQL baseline returned error: {sql_result['error']}")
        return mismatches

    tool_metrics = tool_result["current_metrics"]
    sql_metrics = sql_result["current_metrics"]

    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=("as_of_date", "curve_family", "spread_label", "rolling_window_days"),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_spread_bps",
            "daily_change_bps",
            "current_z_score",
            "short_tenor_yield",
            "long_tenor_yield",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=("spread_bps", "z_score"),
            tolerances=TOLERANCE_BY_FIELD,
        )
    )

    return mismatches


def run_case(engine, *, case: Case, lookback_days: int, field_name: str) -> List[str]:
    curve_family, short_tenor, long_tenor = case

    tool_result = calculate_curve_spread(
        engine=engine,
        params=CurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        short_tenor=short_tenor,
        long_tenor=long_tenor,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the sovereign curve-spread tool against direct SQL."
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("SOVEREIGN CURVE SPREAD TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live sovereign metadata...")
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(cases, lambda case: f"{case[0]} {case[1]}/{case[2]}")

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), f"{case[0]} {case[1]}/{case[2]}")
        mismatches = run_case(
            engine,
            case=case,
            lookback_days=args.days,
            field_name=args.field,
        )
        if mismatches:
            print("FAIL")
            for mismatch in mismatches[:10]:
                print(f"  - {mismatch}")
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more mismatches")
            failed_cases.append((case, mismatches))
        else:
            print("PASS")

    print("[4/4] Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")

    if failed_cases:
        print("\nFAILED CASES:")
        for case, mismatches in failed_cases:
            print(f"  - {case[0]} {case[1]}/{case[2]} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
