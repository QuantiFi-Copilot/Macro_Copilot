#!/usr/bin/env python3
"""
test_ois_cross_market_spread_sql_validation.py — OIS cross-market spread validator
====================================================================================

Validate ``calculate_ois_cross_market_spread`` against an independent
SQL baseline.  Mirrors ``test_ois_curve_spread_sql_validation.py``
(the prior OIS migration's admission gate) and
``test_cross_market_sql_validation.py`` (the sovereign analog), with
two OIS-specific differences:

  1. Instrument-type filter: ``('ois', 'ois_swap')`` — matches the
     filter the OIS curve_spread validator already uses.
  2. Cutoff anchoring: the display window is anchored to the data's
     latest observation (``MAX(trade_date) - lookback_days``), NOT
     wall-clock (``CURRENT_DATE - lookback_days``).  Sovereign
     anchors to the latter; the OIS tool anchors to the former, and
     the SQL baseline must mirror the tool to be a meaningful gate.

This script:
1. Connects to TimescaleDB using the production database connection.
2. Selects 12 OIS cross-market test cases:
   - 3 fixed regression cases that cover the canonical G4 policy
     differentials (SOFR-ESTR, SOFR-SONIA, ESTR-SONIA at common tenors)
   - 9 reproducibly random OIS curve-pair / tenor cases drawn from
     live DB metadata
3. Runs ``calculate_ois_cross_market_spread`` for each case.
4. Runs an equivalent direct SQL calculation against the database.
5. Compares:
   - current metrics (snapshot)
   - full displayed time-series rows
6. Exits non-zero if any mismatch is found.

This is the per-tool admission gate the OIS plan requires for the
cross_market_spread primitive.  The Codex review of the migration PR
correctly flagged that mocked compute coverage alone does not pin the
live raw-fetch column contract or compare against an independent SQL
baseline; this validator closes that gap.

Usage
-----
    python -m tests.test_ois_cross_market_spread_sql_validation

    python -m tests.test_ois_cross_market_spread_sql_validation \
        --cases 12 --seed 42

    python -m tests.test_ois_cross_market_spread_sql_validation \
        --days 730 --field PX_LAST
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

# ---------------------------------------------------------------------------
# Path setup — ensure project root is on sys.path regardless of cwd
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.ois.tools.cross_market_spread import (  # noqa: E402
    calculate_ois_cross_market_spread,
)
from rates_agent.ois.tools.schemas import OISCrossMarketSpreadInput  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (curve_family_1, curve_family_2, tenor)
Case = Tuple[str, str, str]

DEFAULT_CASE_COUNT = 12
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"

# Canonical G4 policy-differential pairs.  Kept in cf1 - cf2 form so
# the spread direction matches desk convention.  Filtered against the
# live DB metadata pool, so a regression case that no longer has data
# coverage is silently dropped rather than failing CI.
REGRESSION_CASES: List[Case] = [
    ("USD_SOFR_OIS", "EUR_ESTR_OIS", "2Y"),
    ("USD_SOFR_OIS", "GBP_SONIA_OIS", "2Y"),
    ("EUR_ESTR_OIS", "GBP_SONIA_OIS", "5Y"),
]

# Tolerances aligned to the tool's display precision rather than
# unrealistic 1e-6 exactness.  Same shape as the OIS curve_spread
# validator's tolerances — the OIS tool rounds bps to 2dp and z-score
# to 4dp, and a 0.01 bps spread rounding difference can propagate
# through a 252-row rolling mean/std and produce a z-score drift just
# above 0.005 in edge cases.
TOLERANCE_BY_FIELD = {
    "current_spread_bps": 0.011,
    "daily_change_bps": 0.011,
    "weekly_change_bps": 0.011,
    "monthly_change_bps": 0.011,
    "current_z_score": 0.006,
    "high_252d_bps": 0.011,
    "low_252d_bps": 0.011,
    "percentile_252d": 0.11,
    "curve_family_1_rate": 0.00011,
    "curve_family_2_rate": 0.00011,
    "spread_bps": 0.011,
    "z_score": 0.006,
}


def choose_test_cases(
    engine,
    *,
    field_name: str,
    case_count: int,
    seed: int,
) -> List[Case]:
    """Build the test-case list from live OIS DB metadata.

    Strategy:
      - Keep the G4 regression cases when present in live data.
      - Fill the remainder with reproducibly random OIS
        (cf1, cf2, tenor) triples — every available curve pair at
        every shared tenor.
    """
    query = text(
        """
        SELECT tenor, curve_family
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type IN ('ois', 'ois_swap')
          AND field_name = :field_name
        GROUP BY tenor, curve_family
        HAVING COUNT(*) >= 80
        ORDER BY tenor, curve_family
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            query, {"field_name": field_name},
        ).mappings().all()

    tenor_to_curves: Dict[str, List[str]] = {}
    for row in rows:
        tenor_to_curves.setdefault(row["tenor"], []).append(row["curve_family"])

    pool: List[Case] = []
    for tenor, curves in tenor_to_curves.items():
        for cf1, cf2 in itertools.combinations(sorted(set(curves)), 2):
            pool.append((cf1, cf2, tenor))

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
    """Compute the OIS cross-market spread output directly in SQL.

    Independent of the Python tool's helper functions.  Reproduces:
      - fetch one tenor on two OIS curves
      - align on the union of trade dates
      - forward-fill missing leg observations for up to 5 consecutive
        rows
      - compute spread in bps (cf1_rate − cf2_rate) × 100, rounded
        to 2 decimals
      - 252-row rolling z-score with a 60-row minimum, sample std
        (ddof=1), rounded to 4 decimals
      - daily / weekly / monthly bps changes via LAG (1 / 5 / 21
        rows = period_offsets 2 / 6 / 22)
      - trailing 252-row high / low / percentile on the spread
      - **anchor the display cutoff to MAX(trade_date), NOT
        CURRENT_DATE** (the OIS tool's data-anchored convention)
    """
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                curve_family,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type IN ('ois', 'ois_swap')
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
                MAX(CASE WHEN r.curve_family = :curve_family_1 THEN r.field_value END) AS cf1_raw,
                MAX(CASE WHEN r.curve_family = :curve_family_2 THEN r.field_value END) AS cf2_raw
            FROM numbered n
            LEFT JOIN raw r
              ON r.trade_date = n.trade_date
            GROUP BY n.trade_date, n.rn
        ),
        ffill AS (
            SELECT
                *,
                MAX(CASE WHEN cf1_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS cf1_last_rn,
                MAX(CASE WHEN cf2_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS cf2_last_rn
            FROM joined
        ),
        filled AS (
            SELECT
                trade_date,
                rn,
                CASE
                    WHEN cf1_raw IS NOT NULL THEN cf1_raw
                    WHEN cf1_last_rn IS NOT NULL AND rn - cf1_last_rn <= 5
                    THEN MAX(CASE WHEN cf1_raw IS NOT NULL THEN cf1_raw END)
                         OVER (PARTITION BY cf1_last_rn)
                    ELSE NULL
                END AS cf1_rate,
                CASE
                    WHEN cf2_raw IS NOT NULL THEN cf2_raw
                    WHEN cf2_last_rn IS NOT NULL AND rn - cf2_last_rn <= 5
                    THEN MAX(CASE WHEN cf2_raw IS NOT NULL THEN cf2_raw END)
                         OVER (PARTITION BY cf2_last_rn)
                    ELSE NULL
                END AS cf2_rate
            FROM ffill
        ),
        aligned AS (
            SELECT
                trade_date,
                rn,
                cf1_rate,
                cf2_rate,
                ROUND(((cf1_rate - cf2_rate) * 100)::numeric, 2)::double precision AS spread_bps
            FROM filled
            WHERE cf1_rate IS NOT NULL
              AND cf2_rate IS NOT NULL
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                cf1_rate,
                cf2_rate,
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
        latest_as_of AS (
            -- Data-anchored cutoff: matches the OIS tool's
            -- ``wide.index[-1].date()`` (NOT date.today()).  This is
            -- the OIS-specific divergence from sovereign that the
            -- baseline must mirror.
            SELECT MAX(trade_date) AS as_of_date
            FROM scored
        ),
        display_rows AS (
            SELECT
                s.trade_date,
                s.rn,
                s.cf1_rate,
                s.cf2_rate,
                s.spread_bps,
                s.z_score
            FROM scored s
            CROSS JOIN latest_as_of a
            WHERE s.trade_date >= a.as_of_date - (:lookback_days * INTERVAL '1 day')
        ),
        display_metrics AS (
            SELECT
                trade_date,
                rn,
                cf1_rate,
                cf2_rate,
                spread_bps,
                z_score,
                CASE
                    WHEN LAG(spread_bps, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 1) OVER (ORDER BY rn))::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(spread_bps, 5) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 5) OVER (ORDER BY rn))::numeric,
                        2
                    )::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(spread_bps, 21) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 21) OVER (ORDER BY rn))::numeric,
                        2
                    )::double precision
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
            cf1_rate,
            cf2_rate,
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
            "curve_family_1_rate": latest["cf1_rate"],
            "curve_family_2_rate": latest["cf2_rate"],
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


def compare_results(
    tool_result: Dict[str, Any],
    sql_result: Dict[str, Any],
) -> List[str]:
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
            # OIS-specific snapshot field names — "rate" not "yield".
            "curve_family_1_rate",
            "curve_family_2_rate",
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


def run_case(
    engine,
    *,
    case: Case,
    lookback_days: int,
    field_name: str,
) -> List[str]:
    curve_family_1, curve_family_2, tenor = case
    tool_result = calculate_ois_cross_market_spread(
        engine=engine,
        params=OISCrossMarketSpreadInput(
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
        description="Validate the OIS cross-market spread tool against direct SQL."
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
    print("OIS CROSS-MARKET SPREAD TOOL — SQL VALIDATION")
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
        print("No OIS cross-market validation cases found in the database.")
        sys.exit(1)
    print_selected_cases(
        cases, lambda case: f"{case[0]} vs {case[1]} @ {case[2]}",
    )

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases), f"{case[0]} vs {case[1]} @ {case[2]}",
        )
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
            print(
                f"  - {case[0]} vs {case[1]} @ {case[2]} "
                f"({len(mismatches)} mismatches)"
            )
        sys.exit(1)

    print("\nAll OIS cross-market spread validation cases passed.")


if __name__ == "__main__":
    main()
