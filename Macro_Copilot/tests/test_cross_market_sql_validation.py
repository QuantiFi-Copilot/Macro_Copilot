#!/usr/bin/env python3
"""
test_cross_market_sql_validation.py — Sovereign cross-market validator
========================================================================

Validate ``calculate_cross_market_spread`` against an independent SQL baseline.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.sovereign_bonds.tools.cross_market_spread import calculate_cross_market_spread  # noqa: E402
from rates_agent.sovereign_bonds.tools.schemas import CrossMarketSpreadInput  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


Case = Tuple[str, str, str]

DEFAULT_CASE_COUNT = 12
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"

REGRESSION_CASES: List[Case] = [
    ("IT_BTP", "DE_BUND", "10Y"),
    ("FR_OAT", "DE_BUND", "10Y"),
    ("UST", "DE_BUND", "10Y"),
]

TOLERANCE_BY_FIELD = {
    "current_spread_bps": 0.011,
    "daily_change_bps": 0.011,
    "weekly_change_bps": 0.011,
    "monthly_change_bps": 0.011,
    "current_z_score": 0.006,
    "high_252d_bps": 0.011,
    "low_252d_bps": 0.011,
    "percentile_252d": 0.11,
    "curve_family_1_yield": 0.00011,
    "curve_family_2_yield": 0.00011,
    "spread_bps": 0.011,
    "z_score": 0.006,
}


def choose_test_cases(engine, *, field_name: str, case_count: int, seed: int) -> List[Case]:
    query = text(
        """
        SELECT tenor, curve_family
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type = 'sovereign_benchmark'
          AND field_name = :field_name
        GROUP BY tenor, curve_family
        HAVING COUNT(*) >= 80
        ORDER BY tenor, curve_family
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(query, {"field_name": field_name}).mappings().all()

    tenor_to_curves: Dict[str, List[str]] = {}
    for row in rows:
        tenor_to_curves.setdefault(row["tenor"], []).append(row["curve_family"])

    pool: List[Case] = []
    for tenor, curves in tenor_to_curves.items():
        for curve_family_1, curve_family_2 in itertools.combinations(sorted(set(curves)), 2):
            pool.append((curve_family_1, curve_family_2, tenor))

    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                curve_family,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'sovereign_benchmark'
              AND curve_family IN (:curve_family_1, :curve_family_2)
              AND tenor = :tenor
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
                MAX(CASE WHEN r.curve_family = :curve_family_1 THEN r.field_value END) AS curve_1_raw,
                MAX(CASE WHEN r.curve_family = :curve_family_2 THEN r.field_value END) AS curve_2_raw
            FROM numbered n
            LEFT JOIN raw r
              ON r.trade_date = n.trade_date
            GROUP BY n.trade_date, n.rn
        ),
        ffill AS (
            SELECT
                *,
                MAX(CASE WHEN curve_1_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS curve_1_last_rn,
                MAX(CASE WHEN curve_2_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS curve_2_last_rn
            FROM joined
        ),
        filled AS (
            SELECT
                trade_date,
                rn,
                CASE
                    WHEN curve_1_raw IS NOT NULL THEN curve_1_raw
                    WHEN curve_1_last_rn IS NOT NULL AND rn - curve_1_last_rn <= 5
                    THEN MAX(CASE WHEN curve_1_raw IS NOT NULL THEN curve_1_raw END)
                         OVER (PARTITION BY curve_1_last_rn)
                    ELSE NULL
                END AS curve_1_yield,
                CASE
                    WHEN curve_2_raw IS NOT NULL THEN curve_2_raw
                    WHEN curve_2_last_rn IS NOT NULL AND rn - curve_2_last_rn <= 5
                    THEN MAX(CASE WHEN curve_2_raw IS NOT NULL THEN curve_2_raw END)
                         OVER (PARTITION BY curve_2_last_rn)
                    ELSE NULL
                END AS curve_2_yield
            FROM ffill
        ),
        aligned AS (
            SELECT
                trade_date,
                rn,
                curve_1_yield,
                curve_2_yield,
                ROUND(((curve_1_yield - curve_2_yield) * 100)::numeric, 2)::double precision AS spread_bps
            FROM filled
            WHERE curve_1_yield IS NOT NULL
              AND curve_2_yield IS NOT NULL
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                curve_1_yield,
                curve_2_yield,
                spread_bps,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(spread_bps) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(spread_bps) OVER zw <> 0
                    THEN ROUND(
                        (
                            (spread_bps - AVG(spread_bps) OVER zw)
                            / NULLIF(STDDEV_SAMP(spread_bps) OVER zw, 0)
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score
            FROM aligned
            WINDOW zw AS (
                ORDER BY rn
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
            )
        ),
        display_rows AS (
            SELECT
                trade_date,
                rn,
                curve_1_yield,
                curve_2_yield,
                spread_bps,
                z_score
            FROM scored
            WHERE trade_date >= CURRENT_DATE - (:lookback_days * INTERVAL '1 day')
        ),
        display_metrics AS (
            SELECT
                trade_date,
                rn,
                curve_1_yield,
                curve_2_yield,
                spread_bps,
                z_score,
                CASE
                    WHEN LAG(spread_bps, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND((spread_bps - LAG(spread_bps, 1) OVER (ORDER BY rn))::numeric, 2)::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(spread_bps, 5) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND((spread_bps - LAG(spread_bps, 5) OVER (ORDER BY rn))::numeric, 2)::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(spread_bps, 21) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND((spread_bps - LAG(spread_bps, 21) OVER (ORDER BY rn))::numeric, 2)::double precision
                END AS monthly_change_bps,
                ROUND((MAX(spread_bps) OVER tw)::numeric, 2)::double precision AS high_252d_bps,
                ROUND((MIN(spread_bps) OVER tw)::numeric, 2)::double precision AS low_252d_bps,
                CASE
                    WHEN MAX(spread_bps) OVER tw IS NULL
                      OR MIN(spread_bps) OVER tw IS NULL
                      OR MAX(spread_bps) OVER tw = MIN(spread_bps) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                spread_bps - MIN(spread_bps) OVER tw
                            ) / NULLIF(
                                MAX(spread_bps) OVER tw - MIN(spread_bps) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d
            FROM display_rows
            WINDOW tw AS (
                ORDER BY rn
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
            )
        )
        SELECT
            TO_CHAR(trade_date, 'YYYY-MM-DD') AS date,
            curve_1_yield,
            curve_2_yield,
            spread_bps,
            z_score,
            daily_change_bps,
            weekly_change_bps,
            monthly_change_bps,
            high_252d_bps,
            low_252d_bps,
            percentile_252d
        FROM display_metrics
        ORDER BY date
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            baseline_sql,
            {
                "curve_family_1": curve_family_1,
                "curve_family_2": curve_family_2,
                "tenor": tenor,
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
            "curve_family_1": curve_family_1,
            "curve_family_2": curve_family_2,
            "tenor": tenor,
            "spread_label": f"{curve_family_1}-{curve_family_2} {tenor}",
            "current_spread_bps": latest["spread_bps"],
            "daily_change_bps": latest["daily_change_bps"],
            "weekly_change_bps": latest["weekly_change_bps"],
            "monthly_change_bps": latest["monthly_change_bps"],
            "current_z_score": latest["z_score"],
            "rolling_window_days": 252,
            "high_252d_bps": latest["high_252d_bps"],
            "low_252d_bps": latest["low_252d_bps"],
            "percentile_252d": latest["percentile_252d"],
            "curve_family_1_yield": latest["curve_1_yield"],
            "curve_family_2_yield": latest["curve_2_yield"],
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
        fields=(
            "as_of_date",
            "curve_family_1",
            "curve_family_2",
            "tenor",
            "spread_label",
            "rolling_window_days",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_spread_bps",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "curve_family_1_yield",
            "curve_family_2_yield",
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
    curve_family_1, curve_family_2, tenor = case
    tool_result = calculate_cross_market_spread(
        engine=engine,
        params=CrossMarketSpreadInput(
            curve_family_1=curve_family_1,
            curve_family_2=curve_family_2,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family_1=curve_family_1,
        curve_family_2=curve_family_2,
        tenor=tenor,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the sovereign cross-market spread tool against direct SQL."
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("SOVEREIGN CROSS-MARKET SPREAD TOOL — SQL VALIDATION")
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
    print_selected_cases(cases, lambda case: f"{case[0]} vs {case[1]} @ {case[2]}")

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), f"{case[0]} vs {case[1]} @ {case[2]}")
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
            print(f"  - {case[0]} vs {case[1]} @ {case[2]} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
