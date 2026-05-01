#!/usr/bin/env python3
"""
test_curve_move_classifier_sql_validation.py — Sovereign curve-move classifier validator
======================================================================

Validate ``classify_curve_move_compute`` against an independent SQL baseline.
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
from rates_agent.sovereign_bonds.tools.curve_move_classifier import classify_curve_move_compute  # noqa: E402
from rates_agent.sovereign_bonds.tools.schemas import CurveMoveInput  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
    tenor_sort_key,
)


Case = Tuple[str, str, str, str]

DEFAULT_CASE_COUNT = 12
DEFAULT_SEED = 42
DEFAULT_FIELD_NAME = "YLD_YTM_MID"

LOOKBACK_OFFSETS = {
    "1d": (2, 1),
    "5d": (6, 5),
    "22d": (22, 21),
}

CLASSIFICATION_DESCRIPTIONS = {
    "BULL_STEEPENER": (
        "Yields fell and the curve steepened — the front end rallied "
        "more than the back end.  Typically signals dovish repricing "
        "or flight-to-quality demand concentrated in shorter maturities."
    ),
    "BEAR_STEEPENER": (
        "Yields rose and the curve steepened — the back end sold off "
        "more than the front end.  Typically signals rising term premium, "
        "inflation fears, or increased supply expectations."
    ),
    "BULL_FLATTENER": (
        "Yields fell and the curve flattened — the back end rallied "
        "more than the front end.  Typically signals a flight-to-duration "
        "bid or expectations of prolonged low rates."
    ),
    "BEAR_FLATTENER": (
        "Yields rose and the curve flattened — the front end sold off "
        "more than the back end.  Typically signals hawkish central bank "
        "repricing with rate hikes being pulled forward."
    ),
    "PARALLEL_SHIFT": (
        "Both legs moved roughly together with minimal change in curve "
        "slope.  The shape of the curve was preserved."
    ),
    "TWIST": (
        "The front end and back end moved in opposite directions — a "
        "curve twist.  This often reflects a central bank surprise or "
        "a shift in the relative supply/demand for short vs long duration."
    ),
}

REGRESSION_CASES: List[Case] = [
    ("UST", "2Y", "10Y", "1d"),
    ("UST", "2Y", "10Y", "5d"),
    ("UST", "2Y", "10Y", "22d"),
]

TOLERANCE_BY_FIELD = {
    "front_yield_current": 0.00011,
    "back_yield_current": 0.00011,
    "front_yield_prior": 0.00011,
    "back_yield_prior": 0.00011,
    "front_change_bps": 0.011,
    "back_change_bps": 0.011,
    "spread_current_bps": 0.011,
    "spread_prior_bps": 0.011,
    "spread_change_bps": 0.011,
}


def curve_move_label(front_tenor: str, back_tenor: str) -> str:
    """Mirror the tool's current label formatting exactly."""
    return (
        f"{front_tenor.replace('Y', '')}s"
        f"{back_tenor.replace('Y', '')}s"
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

    pool: List[Case] = []
    for curve_family, tenors in curve_to_tenors.items():
        ordered = sorted(set(tenors), key=tenor_sort_key)
        for i, front_tenor in enumerate(ordered):
            for back_tenor in ordered[i + 1 :]:
                for lookback_period in LOOKBACK_OFFSETS:
                    pool.append((curve_family, front_tenor, back_tenor, lookback_period))

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
    front_tenor: str,
    back_tenor: str,
    lookback_period: str,
    field_name: str,
) -> Dict[str, Any]:
    required_obs, lag_rows = LOOKBACK_OFFSETS[lookback_period]
    buffer_days = max(required_obs * 3, 60)

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
              AND tenor IN (:front_tenor, :back_tenor)
              AND field_name = :field_name
              AND trade_date >= CURRENT_DATE - (:buffer_days * INTERVAL '1 day')
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
                MAX(CASE WHEN r.tenor = :front_tenor THEN r.field_value END) AS front_yield_raw,
                MAX(CASE WHEN r.tenor = :back_tenor THEN r.field_value END) AS back_yield_raw
            FROM numbered n
            LEFT JOIN raw r
              ON r.trade_date = n.trade_date
            GROUP BY n.trade_date, n.rn
        ),
        ffill AS (
            SELECT
                *,
                MAX(CASE WHEN front_yield_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS front_last_rn,
                MAX(CASE WHEN back_yield_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS back_last_rn
            FROM joined
        ),
        filled AS (
            SELECT
                trade_date,
                rn,
                CASE
                    WHEN front_yield_raw IS NOT NULL THEN front_yield_raw
                    WHEN front_last_rn IS NOT NULL AND rn - front_last_rn <= 5
                    THEN MAX(CASE WHEN front_yield_raw IS NOT NULL THEN front_yield_raw END)
                         OVER (PARTITION BY front_last_rn)
                    ELSE NULL
                END AS front_yield,
                CASE
                    WHEN back_yield_raw IS NOT NULL THEN back_yield_raw
                    WHEN back_last_rn IS NOT NULL AND rn - back_last_rn <= 5
                    THEN MAX(CASE WHEN back_yield_raw IS NOT NULL THEN back_yield_raw END)
                         OVER (PARTITION BY back_last_rn)
                    ELSE NULL
                END AS back_yield
            FROM ffill
        ),
        aligned AS (
            SELECT
                trade_date,
                rn,
                front_yield,
                back_yield
            FROM filled
            WHERE front_yield IS NOT NULL
              AND back_yield IS NOT NULL
        ),
        aligned_count AS (
            SELECT COUNT(*) AS observation_count
            FROM aligned
        ),
        ordered_desc AS (
            SELECT
                trade_date,
                rn,
                front_yield,
                back_yield
            FROM aligned
            ORDER BY trade_date DESC
        ),
        latest AS (
            SELECT *
            FROM ordered_desc
            LIMIT 1
        ),
        prior AS (
            SELECT *
            FROM ordered_desc
            OFFSET :lag_rows
            LIMIT 1
        )
        SELECT
            TO_CHAR(latest.trade_date, 'YYYY-MM-DD') AS as_of_date,
            TO_CHAR(prior.trade_date, 'YYYY-MM-DD') AS prior_date,
            latest.front_yield AS front_yield_current,
            latest.back_yield AS back_yield_current,
            prior.front_yield AS front_yield_prior,
            prior.back_yield AS back_yield_prior,
            ROUND(((latest.front_yield - prior.front_yield) * 100)::numeric, 2)::double precision AS front_change_bps,
            ROUND(((latest.back_yield - prior.back_yield) * 100)::numeric, 2)::double precision AS back_change_bps,
            ROUND(((latest.back_yield - latest.front_yield) * 100)::numeric, 2)::double precision AS spread_current_bps,
            ROUND(((prior.back_yield - prior.front_yield) * 100)::numeric, 2)::double precision AS spread_prior_bps,
            aligned_count.observation_count
        FROM latest
        CROSS JOIN prior
        CROSS JOIN aligned_count
        """
    )

    with engine.connect() as conn:
        row = conn.execute(
            baseline_sql,
            {
                "curve_family": curve_family,
                "front_tenor": front_tenor,
                "back_tenor": back_tenor,
                "field_name": field_name,
                "buffer_days": buffer_days,
                "lag_rows": lag_rows,
            },
        ).mappings().first()

    if row is None:
        return {"error": "SQL baseline returned no rows."}

    if row["observation_count"] < required_obs:
        return {
            "error": (
                f"SQL baseline found insufficient data: have {row['observation_count']}, "
                f"need at least {required_obs}."
            )
        }

    front_change_bps = row["front_change_bps"]
    back_change_bps = row["back_change_bps"]
    spread_change_bps = round(back_change_bps - front_change_bps, 2)
    avg_change_bps = round((front_change_bps + back_change_bps) / 2, 2)

    if abs(front_change_bps) < 0.5 and abs(back_change_bps) < 0.5:
        classification = "PARALLEL_SHIFT"
    elif front_change_bps * back_change_bps < 0:
        classification = "TWIST"
    elif abs(spread_change_bps) < 1.0:
        classification = "PARALLEL_SHIFT"
    elif avg_change_bps < 0 and spread_change_bps > 0:
        classification = "BULL_STEEPENER"
    elif avg_change_bps >= 0 and spread_change_bps > 0:
        classification = "BEAR_STEEPENER"
    elif avg_change_bps < 0 and spread_change_bps <= 0:
        classification = "BULL_FLATTENER"
    else:
        classification = "BEAR_FLATTENER"

    return {
        "current_metrics": {
            "as_of_date": row["as_of_date"],
            "prior_date": row["prior_date"],
            "curve_family": curve_family,
            "lookback_period": lookback_period,
            "spread_label": curve_move_label(front_tenor, back_tenor),
            "classification": classification,
            "description": CLASSIFICATION_DESCRIPTIONS[classification],
            "front_tenor": front_tenor,
            "back_tenor": back_tenor,
            "front_yield_current": row["front_yield_current"],
            "back_yield_current": row["back_yield_current"],
            "front_yield_prior": row["front_yield_prior"],
            "back_yield_prior": row["back_yield_prior"],
            "front_change_bps": front_change_bps,
            "back_change_bps": back_change_bps,
            "spread_current_bps": row["spread_current_bps"],
            "spread_prior_bps": row["spread_prior_bps"],
            "spread_change_bps": spread_change_bps,
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
        fields=(
            "as_of_date",
            "prior_date",
            "curve_family",
            "lookback_period",
            "spread_label",
            "classification",
            "description",
            "front_tenor",
            "back_tenor",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "front_yield_current",
            "back_yield_current",
            "front_yield_prior",
            "back_yield_prior",
            "front_change_bps",
            "back_change_bps",
            "spread_current_bps",
            "spread_prior_bps",
            "spread_change_bps",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    return mismatches


def run_case(engine, *, case: Case, field_name: str) -> List[str]:
    curve_family, front_tenor, back_tenor, lookback_period = case
    tool_result = classify_curve_move_compute(
        engine=engine,
        params=CurveMoveInput(
            curve_family=curve_family,
            front_tenor=front_tenor,
            back_tenor=back_tenor,
            lookback_period=lookback_period,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        front_tenor=front_tenor,
        back_tenor=back_tenor,
        lookback_period=lookback_period,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the sovereign curve-move classifier tool against direct SQL."
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("SOVEREIGN CURVE MOVE CLASSIFIER TOOL — SQL VALIDATION")
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
    print_selected_cases(cases, lambda case: f"{case[0]} {case[1]}/{case[2]} {case[3]}")

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), f"{case[0]} {case[1]}/{case[2]} {case[3]}")
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
            print(f"  - {case[0]} {case[1]}/{case[2]} {case[3]} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
