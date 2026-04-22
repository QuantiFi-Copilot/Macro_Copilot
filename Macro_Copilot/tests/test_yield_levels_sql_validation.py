#!/usr/bin/env python3
"""
test_yield_levels_sql_validation.py — Sovereign yield-level validator
========================================================================

Validate ``get_yield_levels`` against an independent SQL baseline.
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
from rates_agent.sovereign_bonds.tools.schemas import YieldLevelInput  # noqa: E402
from rates_agent.sovereign_bonds.tools.yield_levels import get_yield_levels  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


Case = Tuple[str, str]

DEFAULT_CASE_COUNT = 12
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"

REGRESSION_CASES: List[Case] = [
    ("UST", "10Y"),
    ("DE_BUND", "10Y"),
    ("JGB", "10Y"),
]

TOLERANCE_BY_FIELD = {
    "current_yield_pct": 0.00011,
    "daily_change_bps": 0.011,
    "weekly_change_bps": 0.011,
    "monthly_change_bps": 0.011,
    "z_score": 0.006,
    "high_252d_pct": 0.00011,
    "low_252d_pct": 0.00011,
    "percentile_252d": 0.11,
}


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

    pool = [(row["curve_family"], row["tenor"]) for row in rows]
    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    curve_family: str,
    tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'sovereign_benchmark'
              AND curve_family = :curve_family
              AND tenor = :tenor
              AND field_name = :field_name
              AND trade_date >= CURRENT_DATE - ((:lookback_days + 378) * INTERVAL '1 day')
        ),
        clean AS (
            SELECT
                trade_date,
                field_value
            FROM raw
            WHERE field_value IS NOT NULL
        ),
        ordered AS (
            SELECT
                trade_date,
                field_value,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM clean
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                field_value,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(field_value) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(field_value) OVER zw <> 0
                    THEN ROUND(
                        (
                            (field_value - AVG(field_value) OVER zw)
                            / NULLIF(STDDEV_SAMP(field_value) OVER zw, 0)
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score,
                ROUND((MAX(field_value) OVER tw)::numeric, 4)::double precision AS high_252d_pct,
                ROUND((MIN(field_value) OVER tw)::numeric, 4)::double precision AS low_252d_pct,
                CASE
                    WHEN MAX(field_value) OVER tw IS NULL
                      OR MIN(field_value) OVER tw IS NULL
                      OR MAX(field_value) OVER tw = MIN(field_value) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                field_value - MIN(field_value) OVER tw
                            ) / NULLIF(
                                MAX(field_value) OVER tw - MIN(field_value) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d,
                CASE
                    WHEN LAG(field_value, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        ((field_value - LAG(field_value, 1) OVER (ORDER BY rn)) * 100)::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(field_value, 5) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        ((field_value - LAG(field_value, 5) OVER (ORDER BY rn)) * 100)::numeric,
                        2
                    )::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(field_value, 21) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        ((field_value - LAG(field_value, 21) OVER (ORDER BY rn)) * 100)::numeric,
                        2
                    )::double precision
                END AS monthly_change_bps
            FROM ordered
            WINDOW
                zw AS (ORDER BY rn ROWS BETWEEN 251 PRECEDING AND CURRENT ROW),
                tw AS (ORDER BY rn ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
        ),
        latest AS (
            SELECT *
            FROM scored
            ORDER BY trade_date DESC
            LIMIT 1
        ),
        obs AS (
            SELECT COUNT(*) AS observation_count
            FROM ordered
            WHERE trade_date >= CURRENT_DATE - (:lookback_days * INTERVAL '1 day')
        )
        SELECT
            TO_CHAR(latest.trade_date, 'YYYY-MM-DD') AS as_of_date,
            latest.field_value AS current_yield_pct,
            latest.daily_change_bps,
            latest.weekly_change_bps,
            latest.monthly_change_bps,
            latest.z_score,
            latest.high_252d_pct,
            latest.low_252d_pct,
            latest.percentile_252d,
            obs.observation_count
        FROM latest
        CROSS JOIN obs
        """
    )

    with engine.connect() as conn:
        row = conn.execute(
            baseline_sql,
            {
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
                "lookback_days": lookback_days,
            },
        ).mappings().first()

    if row is None:
        return {"error": "SQL baseline returned no rows."}

    return {
        "current_metrics": {
            "as_of_date": row["as_of_date"],
            "curve_family": curve_family,
            "tenor": tenor,
            "current_yield_pct": row["current_yield_pct"],
            "daily_change_bps": row["daily_change_bps"],
            "weekly_change_bps": row["weekly_change_bps"],
            "monthly_change_bps": row["monthly_change_bps"],
            "z_score": row["z_score"],
            "high_252d_pct": row["high_252d_pct"],
            "low_252d_pct": row["low_252d_pct"],
            "percentile_252d": row["percentile_252d"],
            "observation_count": row["observation_count"],
        }
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
        fields=("as_of_date", "curve_family", "tenor", "observation_count"),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_yield_pct",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "z_score",
            "high_252d_pct",
            "low_252d_pct",
            "percentile_252d",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    return mismatches


def run_case(engine, *, case: Case, lookback_days: int, field_name: str) -> List[str]:
    curve_family, tenor = case
    tool_result = get_yield_levels(
        engine=engine,
        params=YieldLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        tenor=tenor,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the sovereign yield-level tool against direct SQL."
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("SOVEREIGN YIELD LEVEL TOOL — SQL VALIDATION")
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
    print_selected_cases(cases, lambda case: f"{case[0]} {case[1]}")

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), f"{case[0]} {case[1]}")
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
            print(f"  - {case[0]} {case[1]} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
