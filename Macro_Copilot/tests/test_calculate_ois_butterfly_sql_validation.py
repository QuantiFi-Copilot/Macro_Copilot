#!/usr/bin/env python3
"""
test_calculate_ois_butterfly_sql_validation.py — OIS butterfly validator
========================================================================

Validate ``calculate_ois_butterfly`` against an independent SQL
baseline.  Read-only — SELECT statements only, no DDL / DML / UPSERT.

Pattern mirrors ``tests/test_butterfly_sql_validation.py`` (the
sovereign butterfly validator), with three OIS-specific differences:

  1. SQL filter is ``instrument_type = 'ois_swap'`` (the OIS swap
     instrument_type in macro_data.v_market_data_daily_enriched).
  2. Default field_name is ``PX_LAST`` (the OIS Bloomberg mid-rate
     field), NOT ``YLD_YTM_MID``.
  3. Regression cases enumerate the OIS playbook's canonical
     curve_families (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS).

Standalone script — not collected by pytest (registered in
``tests/conftest.py::collect_ignore``).  Run from the project root::

    python tests/test_calculate_ois_butterfly_sql_validation.py
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
from rates_agent.ois.tools.calculate_ois_butterfly import (  # noqa: E402
    OISButterflyInput,
    calculate_ois_butterfly,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    print_case_header,
    print_selected_cases,
    sample_cases,
    tenor_sort_key,
)


Case = Tuple[str, str, str, str]

DEFAULT_CASE_COUNT = 9
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"

# Canonical 2s5s10s regression cases on the three main OIS curves.
# These are intentionally pinned so a re-run on the same data produces
# the same coverage (sample_cases preserves any fixed case present in
# the live metadata pool).
REGRESSION_CASES: List[Case] = [
    ("USD_SOFR_OIS",  "2Y", "5Y", "10Y"),
    ("EUR_ESTR_OIS",  "2Y", "5Y", "10Y"),
    ("GBP_SONIA_OIS", "2Y", "5Y", "10Y"),
]

# Tolerance shape mirrors the sovereign butterfly validator with one
# split-tolerance refinement copied from the sibling
# inflation_swap_butterfly validator:
#
#   - ``current_z_score`` (snapshot wire field) is held at 0.006.  The
#     snapshot z-score is the load-bearing answer the desk reads.
#   - ``z_score`` (historical-row z-score in ``time_series``) is
#     loosened to ``z_score_row = 0.025`` to absorb FP-banker-rounding
#     vs PostgreSQL ROUND(numeric, N) half-away-from-zero divergences
#     that propagate through the 252-day rolling window on short-end
#     OIS curves with very small butterfly variance (live observation:
#     JPY_OIS 3M/6M/1Y had two historical rows at deltas 0.0062 and
#     0.0065 — well inside 0.025).  The snapshot value is unaffected
#     because the rolling stats land on the same row at the as-of
#     date; only the historical rows show the cumulative effect.
TOLERANCE_BY_FIELD = {
    "current_butterfly_bps": 0.011,
    "daily_change_bps": 0.011,
    "current_z_score": 0.006,
    "high_252d_bps": 0.011,
    "low_252d_bps": 0.011,
    "percentile_252d": 0.11,
    "wing_short_bps": 0.011,
    "wing_long_bps": 0.011,
    "short_tenor_rate": 0.00011,
    "belly_tenor_rate": 0.00011,
    "long_tenor_rate": 0.00011,
    "butterfly_bps": 0.011,
    # Historical-row z-score — loosened per the rationale above.
    "z_score_row": 0.025,
}


def choose_test_cases(engine, *, field_name: str, case_count: int, seed: int) -> List[Case]:
    """Sample (curve_family, short, belly, long) triplets where all
    three tenors have at least 80 observations under the requested
    field_name — same admission gate the sovereign butterfly validator
    uses."""
    query = text(
        """
        SELECT curve_family, tenor
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type = 'ois_swap'
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
        for short_index, short_tenor in enumerate(ordered):
            for belly_index in range(short_index + 1, len(ordered)):
                for long_index in range(belly_index + 1, len(ordered)):
                    pool.append(
                        (
                            curve_family,
                            short_tenor,
                            ordered[belly_index],
                            ordered[long_index],
                        )
                    )

    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def butterfly_label(short_tenor: str, belly_tenor: str, long_tenor: str) -> str:
    if (
        short_tenor.endswith("Y")
        and belly_tenor.endswith("Y")
        and long_tenor.endswith("Y")
    ):
        return (
            f"{short_tenor.replace('Y', '')}s"
            f"{belly_tenor.replace('Y', '')}s"
            f"{long_tenor.replace('Y', '')}s"
        )
    return f"{short_tenor}/{belly_tenor}/{long_tenor}"


def sql_baseline(
    engine,
    *,
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL re-implementation of the OIS butterfly.

    Mirror of the sovereign butterfly SQL baseline (CTE-based: fetch
    raw → date_grid → numbered → joined → ffill (limit=5) → aligned
    (butterfly = (2*belly - short - long) * 100) → scored (252-row
    rolling z, ddof=1) → display_rows (trim to lookback_days) →
    display_metrics (LAG-based daily change, 252-row trailing high/low/
    percentile)).  Read-only.
    """
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                tenor,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'ois_swap'
              AND curve_family = :curve_family
              AND tenor = ANY(:tenors)
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
                MAX(CASE WHEN r.tenor = :belly_tenor THEN r.field_value END) AS belly_rate_raw,
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
                MAX(CASE WHEN belly_rate_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS belly_last_rn,
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
                    WHEN belly_rate_raw IS NOT NULL THEN belly_rate_raw
                    WHEN belly_last_rn IS NOT NULL AND rn - belly_last_rn <= 5
                    THEN MAX(CASE WHEN belly_rate_raw IS NOT NULL THEN belly_rate_raw END)
                         OVER (PARTITION BY belly_last_rn)
                    ELSE NULL
                END AS belly_rate,
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
                belly_rate,
                long_rate,
                ROUND(((2 * belly_rate - short_rate - long_rate) * 100)::numeric, 2)::double precision AS butterfly_bps,
                ROUND(((belly_rate - short_rate) * 100)::numeric, 2)::double precision AS wing_short_bps,
                ROUND(((long_rate - belly_rate) * 100)::numeric, 2)::double precision AS wing_long_bps
            FROM filled
            WHERE short_rate IS NOT NULL
              AND belly_rate IS NOT NULL
              AND long_rate IS NOT NULL
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                short_rate,
                belly_rate,
                long_rate,
                butterfly_bps,
                wing_short_bps,
                wing_long_bps,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(butterfly_bps) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(butterfly_bps) OVER zw <> 0
                    THEN ROUND(
                        (
                            (butterfly_bps - AVG(butterfly_bps) OVER zw)
                            / NULLIF(STDDEV_SAMP(butterfly_bps) OVER zw, 0)
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
        max_aligned AS (
            SELECT MAX(trade_date) AS as_of_date
            FROM aligned
        ),
        display_rows AS (
            -- Tool anchors the cutoff to the LATEST observation date,
            -- not date.today() — mirror that here.
            SELECT
                trade_date,
                rn,
                short_rate,
                belly_rate,
                long_rate,
                butterfly_bps,
                wing_short_bps,
                wing_long_bps,
                z_score
            FROM scored, max_aligned
            WHERE trade_date >= max_aligned.as_of_date - (:lookback_days * INTERVAL '1 day')
        ),
        display_metrics AS (
            SELECT
                trade_date,
                rn,
                short_rate,
                belly_rate,
                long_rate,
                butterfly_bps,
                wing_short_bps,
                wing_long_bps,
                z_score,
                CASE
                    WHEN LAG(butterfly_bps, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND((butterfly_bps - LAG(butterfly_bps, 1) OVER (ORDER BY rn))::numeric, 2)::double precision
                END AS daily_change_bps,
                ROUND((MAX(butterfly_bps) OVER tw)::numeric, 2)::double precision AS high_252d_bps,
                ROUND((MIN(butterfly_bps) OVER tw)::numeric, 2)::double precision AS low_252d_bps,
                CASE
                    WHEN MAX(butterfly_bps) OVER tw IS NULL
                      OR MIN(butterfly_bps) OVER tw IS NULL
                      OR MAX(butterfly_bps) OVER tw = MIN(butterfly_bps) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                butterfly_bps - MIN(butterfly_bps) OVER tw
                            ) / NULLIF(
                                MAX(butterfly_bps) OVER tw - MIN(butterfly_bps) OVER tw,
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
            short_rate,
            belly_rate,
            long_rate,
            butterfly_bps,
            wing_short_bps,
            wing_long_bps,
            z_score,
            daily_change_bps,
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
                "curve_family": curve_family,
                "tenors": [short_tenor, belly_tenor, long_tenor],
                "short_tenor": short_tenor,
                "belly_tenor": belly_tenor,
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
            "butterfly_label": butterfly_label(short_tenor, belly_tenor, long_tenor),
            "current_butterfly_bps": latest["butterfly_bps"],
            "daily_change_bps": latest["daily_change_bps"],
            "current_z_score": latest["z_score"],
            "rolling_window_days": 252,
            "high_252d_bps": latest["high_252d_bps"],
            "low_252d_bps": latest["low_252d_bps"],
            "percentile_252d": latest["percentile_252d"],
            "wing_short_bps": latest["wing_short_bps"],
            "wing_long_bps": latest["wing_long_bps"],
            "short_tenor_rate": latest["short_rate"],
            "belly_tenor_rate": latest["belly_rate"],
            "long_tenor_rate": latest["long_rate"],
        },
        "time_series": [
            {
                "date": row["date"],
                "butterfly_bps": row["butterfly_bps"],
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
        fields=("as_of_date", "curve_family", "butterfly_label", "rolling_window_days"),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_butterfly_bps",
            "daily_change_bps",
            "current_z_score",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "wing_short_bps",
            "wing_long_bps",
            "short_tenor_rate",
            "belly_tenor_rate",
            "long_tenor_rate",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    # Map z_score → z_score_row tolerance for the historical-row
    # comparison (the snapshot's current_z_score stays tight at 0.006).
    row_tolerances = dict(TOLERANCE_BY_FIELD)
    row_tolerances["z_score"] = TOLERANCE_BY_FIELD["z_score_row"]
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=("butterfly_bps", "z_score"),
            tolerances=row_tolerances,
        )
    )
    return mismatches


def run_case(engine, *, case: Case, lookback_days: int, field_name: str) -> List[str]:
    curve_family, short_tenor, belly_tenor, long_tenor = case
    tool_result = calculate_ois_butterfly(
        engine=engine,
        params=OISButterflyInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        short_tenor=short_tenor,
        belly_tenor=belly_tenor,
        long_tenor=long_tenor,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the OIS butterfly tool against direct SQL."
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("OIS BUTTERFLY TOOL — SQL VALIDATION")
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
    print_selected_cases(cases, lambda case: f"{case[0]} {case[1]}/{case[2]}/{case[3]}")

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), f"{case[0]} {case[1]}/{case[2]}/{case[3]}")
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
            print(f"  - {case[0]} {case[1]}/{case[2]}/{case[3]} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
