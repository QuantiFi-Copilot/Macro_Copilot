#!/usr/bin/env python3
"""
test_ois_curve_spread_sql_validation.py — OIS curve-spread tool validator
==========================================================================

Purpose
-------
Validate the OIS curve-spread tool against an independent SQL baseline.

This script:
1. Connects to TimescaleDB using the production database connection.
2. Selects 12 OIS spread test cases:
   - 3 fixed regression cases that explicitly cover the former 1W-family issue
   - 9 reproducibly random OIS tenor-pair cases
3. Runs ``calculate_ois_curve_spread`` for each case.
4. Runs an equivalent direct SQL calculation against the database for each case.
5. Compares:
   - current metrics
   - full displayed time-series rows
6. Exits non-zero if any mismatch is found.

Usage
-----
    python -m tests.test_ois_curve_spread_sql_validation

    python -m tests.test_ois_curve_spread_sql_validation --cases 12 --seed 42

    python -m tests.test_ois_curve_spread_sql_validation --days 730 --field PX_LAST
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from sqlalchemy import text

# ---------------------------------------------------------------------------
# Path setup — ensure project root is on sys.path regardless of cwd
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.ois.tools.curve_spread import calculate_ois_curve_spread  # noqa: E402
from rates_agent.ois.tools.schemas import OISCurveSpreadInput  # noqa: E402


Case = Tuple[str, str, str]

DEFAULT_CASE_COUNT = 12
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"

REGRESSION_CASES: List[Case] = [
    ("JPY_OIS", "1W", "1M"),
    ("AUD_OIS", "1W", "1M"),
    ("CAD_OIS", "1W", "1M"),
]

TENOR_UNIT_TO_YEARS = {
    "W": 1.0 / 52.0,
    "M": 1.0 / 12.0,
    "Y": 1.0,
}

TOLERANCE_BY_FIELD = {
    "current_spread_bps": 1e-6,
    "daily_change_bps": 1e-6,
    "current_z_score": 1e-6,
    "short_tenor_rate": 1e-6,
    "long_tenor_rate": 1e-6,
    "spread_bps": 1e-6,
    "z_score": 1e-6,
}


def tenor_sort_key(tenor: str) -> float:
    """Convert tenor strings like 1W / 3M / 10Y to sortable year fractions."""
    unit = tenor[-1]
    magnitude = int(tenor[:-1])
    return magnitude * TENOR_UNIT_TO_YEARS[unit]


def format_spread_label(short_tenor: str, long_tenor: str) -> str:
    """Independent reproduction of the tool's label convention."""
    if short_tenor.endswith("Y") and long_tenor.endswith("Y"):
        return f"{short_tenor.replace('Y', '')}s{long_tenor.replace('Y', '')}s"
    return f"{short_tenor}/{long_tenor}"


def floats_match(actual: Any, expected: Any, field_name: str) -> bool:
    """Compare nullable numeric values within a field-specific tolerance."""
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    tolerance = TOLERANCE_BY_FIELD.get(field_name, 1e-9)
    return abs(float(actual) - float(expected)) <= tolerance


def choose_test_cases(
    engine,
    *,
    field_name: str,
    case_count: int,
    seed: int,
) -> List[Case]:
    """
    Build the test-case list from live DB metadata.

    Strategy:
    - Keep three fixed regression cases to cover the former 1W issue.
    - Fill the remainder with reproducibly random valid OIS tenor pairs.
    """
    candidate_sql = text(
        """
        SELECT
            curve_family,
            tenor
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type IN ('ois', 'ois_swap')
          AND field_name = :field_name
        GROUP BY curve_family, tenor
        ORDER BY curve_family, tenor
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(candidate_sql, {"field_name": field_name}).mappings().all()

    curve_to_tenors: Dict[str, List[str]] = {}
    for row in rows:
        curve_to_tenors.setdefault(row["curve_family"], []).append(row["tenor"])

    all_pairs: List[Case] = []
    for curve_family, tenors in curve_to_tenors.items():
        ordered = sorted(set(tenors), key=tenor_sort_key)
        for i, short_tenor in enumerate(ordered):
            for long_tenor in ordered[i + 1 :]:
                all_pairs.append((curve_family, short_tenor, long_tenor))

    fixed_cases = [case for case in REGRESSION_CASES if case in all_pairs]
    remaining_pool = [case for case in all_pairs if case not in fixed_cases]

    if len(fixed_cases) > case_count:
        return fixed_cases[:case_count]

    rng = random.Random(seed)
    remaining_needed = case_count - len(fixed_cases)
    sampled_cases = rng.sample(remaining_pool, k=min(remaining_needed, len(remaining_pool)))

    selected = fixed_cases + sampled_cases
    return selected


def sql_baseline(
    engine,
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """
    Compute the OIS spread output directly in SQL.

    This is intentionally independent of the Python tool's helper functions.
    It reproduces the same steps:
    - fetch two tenors on one curve
    - align on the union of trade dates
    - forward-fill missing leg observations for up to 5 consecutive rows
    - compute spread in bps
    - compute 252-row rolling z-score with a 60-row minimum
    - trim to the display lookback
    """
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                tenor,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE curve_family = :curve_family
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
                MAX(CASE WHEN r.tenor = :short_tenor THEN r.field_value END) AS short_rate_raw,
                MAX(CASE WHEN r.tenor = :long_tenor THEN r.field_value END) AS long_rate_raw
            FROM numbered n
            LEFT JOIN raw r
              ON r.trade_date = n.trade_date
            GROUP BY n.trade_date, n.rn
        ),
        ffill AS (
            SELECT
                *,
                MAX(CASE WHEN short_rate_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS short_last_rn,
                MAX(CASE WHEN long_rate_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS long_last_rn
            FROM joined
        ),
        filled AS (
            SELECT
                trade_date,
                rn,
                CASE
                    WHEN short_rate_raw IS NOT NULL THEN short_rate_raw
                    WHEN short_last_rn IS NOT NULL AND rn - short_last_rn <= 5
                    THEN MAX(CASE WHEN short_rate_raw IS NOT NULL THEN short_rate_raw END)
                         OVER (PARTITION BY short_last_rn)
                    ELSE NULL
                END AS short_rate,
                CASE
                    WHEN long_rate_raw IS NOT NULL THEN long_rate_raw
                    WHEN long_last_rn IS NOT NULL AND rn - long_last_rn <= 5
                    THEN MAX(CASE WHEN long_rate_raw IS NOT NULL THEN long_rate_raw END)
                         OVER (PARTITION BY long_last_rn)
                    ELSE NULL
                END AS long_rate
            FROM ffill
        ),
        aligned AS (
            SELECT
                trade_date,
                rn,
                short_rate,
                long_rate,
                ROUND(((long_rate - short_rate) * 100)::numeric, 2)::double precision AS spread_bps
            FROM filled
            WHERE short_rate IS NOT NULL
              AND long_rate IS NOT NULL
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                short_rate,
                long_rate,
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
                short_rate,
                long_rate,
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
            short_rate,
            long_rate,
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
    baseline_time_series = [
        {
            "date": row["date"],
            "spread_bps": row["spread_bps"],
            "z_score": row["z_score"],
        }
        for row in rows
    ]

    return {
        "current_metrics": {
            "as_of_date": latest["date"],
            "curve_family": curve_family,
            "spread_label": format_spread_label(short_tenor, long_tenor),
            "current_spread_bps": latest["spread_bps"],
            "daily_change_bps": latest["daily_change_bps"],
            "current_z_score": latest["z_score"],
            "rolling_window_days": 252,
            "short_tenor_rate": latest["short_rate"],
            "long_tenor_rate": latest["long_rate"],
        },
        "time_series": baseline_time_series,
    }


def compare_results(
    tool_result: Dict[str, Any],
    sql_result: Dict[str, Any],
) -> List[str]:
    """Return a list of mismatch messages, empty if results match."""
    mismatches: List[str] = []

    if "error" in tool_result:
        mismatches.append(f"Tool returned error: {tool_result['error']}")
        return mismatches

    if "error" in sql_result:
        mismatches.append(f"SQL baseline returned error: {sql_result['error']}")
        return mismatches

    tool_metrics = tool_result["current_metrics"]
    sql_metrics = sql_result["current_metrics"]

    for field in (
        "as_of_date",
        "curve_family",
        "spread_label",
        "rolling_window_days",
    ):
        if tool_metrics.get(field) != sql_metrics.get(field):
            mismatches.append(
                f"current_metrics.{field}: tool={tool_metrics.get(field)!r} "
                f"sql={sql_metrics.get(field)!r}"
            )

    for field in (
        "current_spread_bps",
        "daily_change_bps",
        "current_z_score",
        "short_tenor_rate",
        "long_tenor_rate",
    ):
        if not floats_match(tool_metrics.get(field), sql_metrics.get(field), field):
            mismatches.append(
                f"current_metrics.{field}: tool={tool_metrics.get(field)!r} "
                f"sql={sql_metrics.get(field)!r}"
            )

    tool_ts = tool_result["time_series"]
    sql_ts = sql_result["time_series"]

    if len(tool_ts) != len(sql_ts):
        mismatches.append(
            f"time_series length: tool={len(tool_ts)} sql={len(sql_ts)}"
        )
        return mismatches

    for index, (tool_row, sql_row) in enumerate(zip(tool_ts, sql_ts), start=1):
        if tool_row.get("date") != sql_row.get("date"):
            mismatches.append(
                f"time_series[{index}].date: tool={tool_row.get('date')!r} "
                f"sql={sql_row.get('date')!r}"
            )
            continue

        for field in ("spread_bps", "z_score"):
            if not floats_match(tool_row.get(field), sql_row.get(field), field):
                mismatches.append(
                    f"time_series[{index}].{field} @ {tool_row.get('date')}: "
                    f"tool={tool_row.get(field)!r} sql={sql_row.get(field)!r}"
                )
                break

    return mismatches


def run_case(
    engine,
    *,
    case: Case,
    lookback_days: int,
    field_name: str,
) -> List[str]:
    """Execute one tool-vs-SQL comparison case and return mismatches."""
    curve_family, short_tenor, long_tenor = case

    params = OISCurveSpreadInput(
        curve_family=curve_family,
        short_tenor=short_tenor,
        long_tenor=long_tenor,
        lookback_days=lookback_days,
        field_name=field_name,
    )

    tool_result = calculate_ois_curve_spread(engine=engine, params=params)
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        short_tenor=short_tenor,
        long_tenor=long_tenor,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def print_case_header(index: int, total: int, case: Case) -> None:
    curve_family, short_tenor, long_tenor = case
    print("-" * 80)
    print(f"[{index}/{total}] {curve_family} {short_tenor}/{long_tenor}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the OIS curve-spread tool against direct SQL."
    )
    parser.add_argument(
        "--cases",
        type=int,
        default=DEFAULT_CASE_COUNT,
        help=f"Number of total test cases (default: {DEFAULT_CASE_COUNT})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed for reproducible sampling (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Lookback in calendar days (default: {DEFAULT_LOOKBACK_DAYS})",
    )
    parser.add_argument(
        "--field",
        default=DEFAULT_FIELD_NAME,
        help=f"Field name filter (default: {DEFAULT_FIELD_NAME})",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("OIS CURVE SPREAD TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live OIS metadata...")
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )

    if not cases:
        print("No OIS validation cases found in the database.")
        sys.exit(1)

    print(f"Selected {len(cases)} cases:")
    for case in cases:
        print(f"  - {case[0]} {case[1]}/{case[2]}")

    print("[3/4] Running tool vs SQL comparisons...")
    failures: List[Tuple[Case, List[str]]] = []

    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), case)
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
            failures.append((case, mismatches))
        else:
            print("PASS")

    print("[4/4] Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failures)}")
    print(f"  failed      : {len(failures)}")

    if failures:
        print("\nFAILED CASES:")
        for case, mismatches in failures:
            print(f"  - {case[0]} {case[1]}/{case[2]} ({len(mismatches)} mismatches)")
        sys.exit(1)

    print("\nAll OIS curve-spread validation cases passed.")


if __name__ == "__main__":
    main()
