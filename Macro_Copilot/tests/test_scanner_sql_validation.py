#!/usr/bin/env python3
"""
test_scanner_sql_validation.py — Sovereign scanner validator
============================================================

Validate ``scan_extremes`` against an independent SQL baseline.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.sovereign_bonds.tools.scanner import scan_extremes  # noqa: E402
from rates_agent.sovereign_bonds.tools.schemas import ScannerInput  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
)


Case = Tuple[Optional[Tuple[str, ...]], int, float]

DEFAULT_CASE_COUNT = 8
DEFAULT_SEED = 42
DEFAULT_FIELD_NAME = "YLD_YTM_MID"

TOLERANCE_BY_FIELD = {
    "current_yield_pct": 0.00011,
    "daily_change_bps": 0.011,
    "z_score": 0.006,
    "high_252d_pct": 0.00011,
    "low_252d_pct": 0.00011,
    "percentile_252d": 0.11,
}


def choose_test_cases(engine, *, field_name: str, case_count: int, seed: int) -> List[Case]:
    query = text(
        """
        SELECT curve_family
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type = 'sovereign_benchmark'
          AND field_name = :field_name
        GROUP BY curve_family
        HAVING COUNT(*) >= 80
        ORDER BY curve_family
        """
    )

    with engine.connect() as conn:
        curves = [row["curve_family"] for row in conn.execute(query, {"field_name": field_name}).mappings()]

    fixed_cases: List[Case] = [
        (None, 10, 1.5),
        (None, 8, 2.0),
    ]

    preferred_groups = [
        ("DE_BUND", "FR_OAT", "IT_BTP", "ES_BONO"),
        ("UST", "DE_BUND", "UK_GILT"),
    ]
    for group in preferred_groups:
        present = tuple(curve for curve in group if curve in curves)
        if len(present) >= 2:
            fixed_cases.append((present, 8, 1.25))

    rng = random.Random(seed)
    candidate_cases: List[Case] = []
    thresholds = [1.0, 1.25, 1.5, 2.0]
    tops = [5, 8, 10]

    for subset_size in (2, 3, 4):
        if len(curves) < subset_size:
            continue
        for _ in range(12):
            subset = tuple(sorted(rng.sample(curves, k=subset_size)))
            candidate_cases.append((subset, rng.choice(tops), rng.choice(thresholds)))

    selected: List[Case] = []
    for case in fixed_cases:
        if case not in selected:
            selected.append(case)

    for case in candidate_cases:
        if case not in selected:
            selected.append(case)
        if len(selected) >= case_count:
            break

    return selected[:case_count]


def sql_baseline(
    engine,
    *,
    curve_families: Optional[Tuple[str, ...]],
    top_n: int,
    min_abs_z_score: float,
    field_name: str,
) -> Dict[str, Any]:
    filtered_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                curve_family,
                tenor,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'sovereign_benchmark'
              AND field_name = :field_name
              AND trade_date >= CURRENT_DATE - (743 * INTERVAL '1 day')
              AND tenor IS NOT NULL
              AND curve_family = ANY(:curve_families)
        ),
        group_count AS (
            SELECT COUNT(*) AS group_count
            FROM (
                SELECT DISTINCT curve_family, tenor
                FROM raw
                WHERE field_value IS NOT NULL
            ) g
        ),
        ordered AS (
            SELECT
                trade_date,
                curve_family,
                tenor,
                field_value,
                ROW_NUMBER() OVER (
                    PARTITION BY curve_family, tenor
                    ORDER BY trade_date
                ) AS rn
            FROM raw
            WHERE field_value IS NOT NULL
        ),
        scored AS (
            SELECT
                trade_date,
                curve_family,
                tenor,
                rn,
                field_value,
                CASE
                    WHEN LAG(field_value, 1) OVER (
                        PARTITION BY curve_family, tenor
                        ORDER BY rn
                    ) IS NULL THEN NULL
                    ELSE ROUND(
                        (
                            (
                                field_value
                                - LAG(field_value, 1) OVER (
                                    PARTITION BY curve_family, tenor
                                    ORDER BY rn
                                )
                            ) * 100
                        )::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
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
                ROW_NUMBER() OVER (
                    PARTITION BY curve_family, tenor
                    ORDER BY trade_date DESC
                ) AS rev_rn
            FROM ordered
            WINDOW
                zw AS (
                    PARTITION BY curve_family, tenor
                    ORDER BY rn
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                ),
                tw AS (
                    PARTITION BY curve_family, tenor
                    ORDER BY rn
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                )
        ),
        latest AS (
            SELECT
                curve_family,
                tenor,
                TO_CHAR(trade_date, 'YYYY-MM-DD') AS as_of_date,
                field_value AS current_yield_pct,
                daily_change_bps,
                z_score,
                high_252d_pct,
                low_252d_pct,
                percentile_252d
            FROM scored
            WHERE rev_rn = 1
              AND z_score IS NOT NULL
              AND ABS(z_score) >= :min_abs_z_score
        ),
        ranked AS (
            SELECT
                ROW_NUMBER() OVER (
                    ORDER BY ABS(z_score) DESC, curve_family, tenor
                ) AS rank,
                curve_family,
                tenor,
                as_of_date,
                current_yield_pct,
                daily_change_bps,
                z_score,
                high_252d_pct,
                low_252d_pct,
                percentile_252d,
                CASE
                    WHEN z_score > 0 THEN 'EXTREME_HIGH'
                    ELSE 'EXTREME_LOW'
                END AS signal
            FROM latest
        ),
        found_count AS (
            SELECT COUNT(*) AS found_count
            FROM latest
        )
        SELECT
            ranked.rank,
            ranked.curve_family,
            ranked.tenor,
            ranked.as_of_date,
            ranked.current_yield_pct,
            ranked.daily_change_bps,
            ranked.z_score,
            ranked.high_252d_pct,
            ranked.low_252d_pct,
            ranked.percentile_252d,
            ranked.signal,
            group_count.group_count,
            found_count.found_count
        FROM ranked
        CROSS JOIN group_count
        CROSS JOIN found_count
        WHERE ranked.rank <= :top_n
        ORDER BY ranked.rank
        """
    )
    unfiltered_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                curve_family,
                tenor,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'sovereign_benchmark'
              AND field_name = :field_name
              AND trade_date >= CURRENT_DATE - (743 * INTERVAL '1 day')
              AND tenor IS NOT NULL
        ),
        group_count AS (
            SELECT COUNT(*) AS group_count
            FROM (
                SELECT DISTINCT curve_family, tenor
                FROM raw
                WHERE field_value IS NOT NULL
            ) g
        ),
        ordered AS (
            SELECT
                trade_date,
                curve_family,
                tenor,
                field_value,
                ROW_NUMBER() OVER (
                    PARTITION BY curve_family, tenor
                    ORDER BY trade_date
                ) AS rn
            FROM raw
            WHERE field_value IS NOT NULL
        ),
        scored AS (
            SELECT
                trade_date,
                curve_family,
                tenor,
                rn,
                field_value,
                CASE
                    WHEN LAG(field_value, 1) OVER (
                        PARTITION BY curve_family, tenor
                        ORDER BY rn
                    ) IS NULL THEN NULL
                    ELSE ROUND(
                        (
                            (
                                field_value
                                - LAG(field_value, 1) OVER (
                                    PARTITION BY curve_family, tenor
                                    ORDER BY rn
                                )
                            ) * 100
                        )::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
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
                ROW_NUMBER() OVER (
                    PARTITION BY curve_family, tenor
                    ORDER BY trade_date DESC
                ) AS rev_rn
            FROM ordered
            WINDOW
                zw AS (
                    PARTITION BY curve_family, tenor
                    ORDER BY rn
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                ),
                tw AS (
                    PARTITION BY curve_family, tenor
                    ORDER BY rn
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                )
        ),
        latest AS (
            SELECT
                curve_family,
                tenor,
                TO_CHAR(trade_date, 'YYYY-MM-DD') AS as_of_date,
                field_value AS current_yield_pct,
                daily_change_bps,
                z_score,
                high_252d_pct,
                low_252d_pct,
                percentile_252d
            FROM scored
            WHERE rev_rn = 1
              AND z_score IS NOT NULL
              AND ABS(z_score) >= :min_abs_z_score
        ),
        ranked AS (
            SELECT
                ROW_NUMBER() OVER (
                    ORDER BY ABS(z_score) DESC, curve_family, tenor
                ) AS rank,
                curve_family,
                tenor,
                as_of_date,
                current_yield_pct,
                daily_change_bps,
                z_score,
                high_252d_pct,
                low_252d_pct,
                percentile_252d,
                CASE
                    WHEN z_score > 0 THEN 'EXTREME_HIGH'
                    ELSE 'EXTREME_LOW'
                END AS signal
            FROM latest
        ),
        found_count AS (
            SELECT COUNT(*) AS found_count
            FROM latest
        )
        SELECT
            ranked.rank,
            ranked.curve_family,
            ranked.tenor,
            ranked.as_of_date,
            ranked.current_yield_pct,
            ranked.daily_change_bps,
            ranked.z_score,
            ranked.high_252d_pct,
            ranked.low_252d_pct,
            ranked.percentile_252d,
            ranked.signal,
            group_count.group_count,
            found_count.found_count
        FROM ranked
        CROSS JOIN group_count
        CROSS JOIN found_count
        WHERE ranked.rank <= :top_n
        ORDER BY ranked.rank
        """
    )
    sql = filtered_sql if curve_families else unfiltered_sql

    params: Dict[str, Any] = {
        "field_name": field_name,
        "min_abs_z_score": min_abs_z_score,
        "top_n": top_n,
    }
    if curve_families:
        params["curve_families"] = list(curve_families)

    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()

    if not rows:
        if curve_families:
            count_sql = text(
                """
                SELECT COUNT(*) AS group_count
                FROM (
                    SELECT DISTINCT curve_family, tenor
                    FROM macro_data.v_market_data_daily_enriched
                    WHERE instrument_type = 'sovereign_benchmark'
                      AND field_name = :field_name
                      AND trade_date >= CURRENT_DATE - (743 * INTERVAL '1 day')
                      AND tenor IS NOT NULL
                      AND curve_family = ANY(:curve_families)
                ) g
                """
            )
            params_for_count: Dict[str, Any] = {
                "field_name": field_name,
                "curve_families": list(curve_families),
            }
        else:
            count_sql = text(
                """
                SELECT COUNT(*) AS group_count
                FROM (
                    SELECT DISTINCT curve_family, tenor
                    FROM macro_data.v_market_data_daily_enriched
                    WHERE instrument_type = 'sovereign_benchmark'
                      AND field_name = :field_name
                      AND trade_date >= CURRENT_DATE - (743 * INTERVAL '1 day')
                      AND tenor IS NOT NULL
                ) g
                """
            )
            params_for_count = {"field_name": field_name}
        with engine.connect() as conn:
            group_count = conn.execute(
                count_sql,
                params_for_count,
            ).scalar_one()

        filter_desc = f" for curves {list(curve_families)}" if curve_families else ""
        return {
            "error": (
                f"No instruments found with |z-score| >= {min_abs_z_score}"
                f"{filter_desc}.  Try lowering min_abs_z_score."
            ),
            "group_count": group_count,
        }

    group_count = rows[0]["group_count"]
    found_count = rows[0]["found_count"]
    results = [
        {
            "rank": row["rank"],
            "curve_family": row["curve_family"],
            "tenor": row["tenor"],
            "as_of_date": row["as_of_date"],
            "current_yield_pct": row["current_yield_pct"],
            "daily_change_bps": row["daily_change_bps"],
            "z_score": row["z_score"],
            "high_252d_pct": row["high_252d_pct"],
            "low_252d_pct": row["low_252d_pct"],
            "percentile_252d": row["percentile_252d"],
            "signal": row["signal"],
        }
        for row in rows
    ]
    return {
        "scan_summary": (
            f"Scanned {group_count} instruments.  "
            f"Found {found_count} with |z-score| >= {min_abs_z_score}.  "
            f"Showing top {len(results)} by absolute z-score."
        ),
        "results": results,
    }


def compare_results(tool_result: Dict[str, Any], sql_result: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []

    tool_error = tool_result.get("error")
    sql_error = sql_result.get("error")

    if tool_error or sql_error:
        if tool_error != sql_error:
            mismatches.append(f"error: tool={tool_error!r} sql={sql_error!r}")
        return mismatches

    if tool_result.get("scan_summary") != sql_result.get("scan_summary"):
        mismatches.append(
            f"scan_summary: tool={tool_result.get('scan_summary')!r} "
            f"sql={sql_result.get('scan_summary')!r}"
        )

    tool_rows = tool_result["results"]
    sql_rows = sql_result["results"]
    if len(tool_rows) != len(sql_rows):
        mismatches.append(f"results length: tool={len(tool_rows)} sql={len(sql_rows)}")
        return mismatches

    for index, (tool_row, sql_row) in enumerate(zip(tool_rows, sql_rows), start=1):
        row_mismatches: List[str] = []
        add_exact_field_mismatches(
            mismatches=row_mismatches,
            tool_payload=tool_row,
            sql_payload=sql_row,
            fields=("rank", "curve_family", "tenor", "as_of_date", "signal"),
            prefix=f"results[{index}].",
        )
        add_numeric_field_mismatches(
            mismatches=row_mismatches,
            tool_payload=tool_row,
            sql_payload=sql_row,
            fields=(
                "current_yield_pct",
                "daily_change_bps",
                "z_score",
                "high_252d_pct",
                "low_252d_pct",
                "percentile_252d",
            ),
            tolerances=TOLERANCE_BY_FIELD,
            prefix=f"results[{index}].",
        )
        mismatches.extend(row_mismatches)

    return mismatches


def run_case(engine, *, case: Case, field_name: str) -> List[str]:
    curve_families, top_n, min_abs_z_score = case
    tool_result = scan_extremes(
        engine=engine,
        params=ScannerInput(
            curve_families=list(curve_families) if curve_families else None,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_families=curve_families,
        top_n=top_n,
        min_abs_z_score=min_abs_z_score,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def format_case(case: Case) -> str:
    curve_families, top_n, min_abs_z_score = case
    curve_desc = "ALL" if curve_families is None else ",".join(curve_families)
    return f"curves={curve_desc} top_n={top_n} min_abs_z={min_abs_z_score}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the sovereign scanner tool against direct SQL."
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("SOVEREIGN SCANNER TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases       : {args.cases}")
    print(f"  random_seed : {args.seed}")
    print(f"  field_name  : {args.field}")
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
    print_selected_cases(cases, format_case)

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), format_case(case))
        mismatches = run_case(engine, case=case, field_name=args.field)
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
            print(f"  - {format_case(case)} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
