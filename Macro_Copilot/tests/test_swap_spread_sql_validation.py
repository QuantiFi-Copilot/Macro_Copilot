#!/usr/bin/env python3
"""
test_swap_spread_sql_validation.py — Cross-domain swap-spread SQL admission gate
=================================================================================

Validate ``calculate_swap_spread`` against an independent SQL
baseline.  Mirrors the prior cross-market admission gates
(``test_ois_cross_market_spread_sql_validation.py``,
``test_cross_market_sql_validation.py``), with two cross-domain-
specific differences:

  1. **Two-leg fetch**: each leg uses a different ``field_name``
     (sovereign ``YLD_YTM_MID`` vs OIS ``PX_LAST``).  The SQL
     baseline matches each (curve_family, field_name) pair
     explicitly in the WHERE clause, mirroring the new
     ``fetch_cross_domain_pair`` helper.
  2. **Cutoff anchoring**: data-anchored
     (``MAX(trade_date) - lookback_days``), matching every other
     OIS-rooted tool's anchoring convention.

This is the per-tool admission gate the workflow milestone plan
requires for the ``ois_treasury_spread`` (a.k.a. ``swap_spread``)
prerequisite primitive.

Usage
-----
    python -m tests.test_swap_spread_sql_validation

    python -m tests.test_swap_spread_sql_validation \
        --cases 9 --seed 42

    python -m tests.test_swap_spread_sql_validation \
        --days 730 --sov-field YLD_YTM_MID --ois-field PX_LAST
"""

from __future__ import annotations

import argparse
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
from rates_agent.ois.tools.swap_spread import (  # noqa: E402
    calculate_swap_spread,
)
from rates_agent.ois.tools.schemas import SwapSpreadInput  # noqa: E402
from rates_agent.ois.tools.swap_spread.schemas import (  # noqa: E402
    CURVE_FAMILY_TO_CURRENCY,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (sovereign_curve_family, ois_curve_family, tenor)
Case = Tuple[str, str, str]

DEFAULT_CASE_COUNT = 9
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_SOVEREIGN_FIELD = "YLD_YTM_MID"
DEFAULT_OIS_FIELD = "PX_LAST"

# Canonical desk pairs (sovereign, OIS in matching currency).
# Filtered against live DB metadata; cases that don't have data
# coverage are silently dropped rather than failing CI.
REGRESSION_CASES: List[Case] = [
    ("UST", "USD_SOFR_OIS", "10Y"),     # the canonical 10Y swap spread
    ("DE_BUND", "EUR_ESTR_OIS", "10Y"), # Bund-ESTR
    ("UK_GILT", "GBP_SONIA_OIS", "5Y"), # Gilt-SONIA
]

# Tolerances aligned to display precision (2dp on bps, 4dp on z-score).
TOLERANCE_BY_FIELD = {
    "current_spread_bps": 0.011,
    "daily_change_bps": 0.011,
    "weekly_change_bps": 0.011,
    "monthly_change_bps": 0.011,
    "current_z_score": 0.006,
    "high_252d_bps": 0.011,
    "low_252d_bps": 0.011,
    "percentile_252d": 0.11,
    "sovereign_yield_pct": 0.00011,
    "ois_rate_pct": 0.00011,
    "spread_bps": 0.011,
    "z_score": 0.006,
}


def choose_test_cases(
    engine,
    *,
    sovereign_field_name: str,
    ois_field_name: str,
    case_count: int,
    seed: int,
) -> List[Case]:
    """Build the test-case list from live DB metadata.

    Strategy: enumerate (sovereign_curve, ois_curve, tenor) triples
    that have BOTH legs present (with their respective field_names)
    at the same tenor, then sample.  Only same-currency pairs are
    eligible — the desk convention is that swap spread compares a
    sovereign to the matching-currency OIS curve.
    """
    sov_query = text(
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
    ois_query = text(
        """
        SELECT curve_family, tenor
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type IN ('ois', 'ois_swap')
          AND field_name = :field_name
        GROUP BY curve_family, tenor
        HAVING COUNT(*) >= 80
        ORDER BY curve_family, tenor
        """
    )

    with engine.connect() as conn:
        sov_rows = conn.execute(
            sov_query, {"field_name": sovereign_field_name},
        ).mappings().all()
        ois_rows = conn.execute(
            ois_query, {"field_name": ois_field_name},
        ).mappings().all()

    sov_by_tenor: Dict[str, set] = {}
    for row in sov_rows:
        sov_by_tenor.setdefault(row["tenor"], set()).add(row["curve_family"])
    ois_by_tenor: Dict[str, set] = {}
    for row in ois_rows:
        ois_by_tenor.setdefault(row["tenor"], set()).add(row["curve_family"])

    # Build the sovereign→OIS same-currency mapping by inverting
    # ``CURVE_FAMILY_TO_CURRENCY`` from the schema layer.  Single
    # source of truth: the schema validator and this SQL gate
    # consume the same mapping, so a future addition (e.g. AUD
    # sovereign curve) extends the schema's dict and this gate
    # picks it up automatically.
    sovereign_to_ois: Dict[str, str] = {}
    for sov_cf, sov_ccy in CURVE_FAMILY_TO_CURRENCY.items():
        # Sovereign curves are those whose name does NOT end with
        # "_OIS" — kept structural rather than hardcoding a list,
        # so adding a new sovereign just requires the dict entry.
        if sov_cf.endswith("_OIS"):
            continue
        # Find the OIS curve in the same currency.
        ois_match = next(
            (
                ois_cf for ois_cf, ois_ccy in CURVE_FAMILY_TO_CURRENCY.items()
                if ois_cf.endswith("_OIS") and ois_ccy == sov_ccy
            ),
            None,
        )
        if ois_match is not None:
            sovereign_to_ois[sov_cf] = ois_match

    pool: List[Case] = []
    for tenor, sov_curves in sov_by_tenor.items():
        ois_at_tenor = ois_by_tenor.get(tenor, set())
        for sov in sov_curves:
            ois_match = sovereign_to_ois.get(sov)
            if ois_match and ois_match in ois_at_tenor:
                pool.append((sov, ois_match, tenor))

    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    sovereign_curve_family: str,
    sovereign_field_name: str,
    ois_curve_family: str,
    ois_field_name: str,
    tenor: str,
    lookback_days: int,
) -> Dict[str, Any]:
    """Compute the cross-domain swap spread directly in SQL.

    Independent of the Python tool's helpers.  Reproduces:
      - fetch one tenor on one sovereign curve (with its field_name)
        + one tenor on one OIS curve (with its field_name)
      - align on the union of trade dates
      - forward-fill missing leg observations for up to 5 consecutive
        rows
      - compute spread in bps (sovereign − ois) × 100 rounded to 2
      - 252-row rolling z-score with 60-row min, sample std (ddof=1),
        rounded to 4
      - daily / weekly / monthly bps changes via LAG (1 / 5 / 21
        rows)
      - trailing 252-row high / low / percentile on the spread
      - **anchor the display cutoff to MAX(trade_date), NOT
        CURRENT_DATE** (the OIS-rooted tool's data-anchored
        convention).
    """
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                curve_family,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE tenor = :tenor
              AND trade_date >= CURRENT_DATE
                  - ((:lookback_days + 378) * INTERVAL '1 day')
              AND (
                    (curve_family = :sovereign_curve_family
                     AND field_name = :sovereign_field_name)
                 OR (curve_family = :ois_curve_family
                     AND field_name = :ois_field_name)
              )
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
                MAX(CASE WHEN r.curve_family = :sovereign_curve_family
                         THEN r.field_value END) AS sov_raw,
                MAX(CASE WHEN r.curve_family = :ois_curve_family
                         THEN r.field_value END) AS ois_raw
            FROM numbered n
            LEFT JOIN raw r
              ON r.trade_date = n.trade_date
            GROUP BY n.trade_date, n.rn
        ),
        ffill AS (
            SELECT
                *,
                MAX(CASE WHEN sov_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING
                          AND CURRENT ROW) AS sov_last_rn,
                MAX(CASE WHEN ois_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING
                          AND CURRENT ROW) AS ois_last_rn
            FROM joined
        ),
        filled AS (
            SELECT
                trade_date,
                rn,
                CASE
                    WHEN sov_raw IS NOT NULL THEN sov_raw
                    WHEN sov_last_rn IS NOT NULL
                         AND rn - sov_last_rn <= 5
                    THEN MAX(CASE WHEN sov_raw IS NOT NULL
                                  THEN sov_raw END)
                         OVER (PARTITION BY sov_last_rn)
                    ELSE NULL
                END AS sov_yield,
                CASE
                    WHEN ois_raw IS NOT NULL THEN ois_raw
                    WHEN ois_last_rn IS NOT NULL
                         AND rn - ois_last_rn <= 5
                    THEN MAX(CASE WHEN ois_raw IS NOT NULL
                                  THEN ois_raw END)
                         OVER (PARTITION BY ois_last_rn)
                    ELSE NULL
                END AS ois_rate
            FROM ffill
        ),
        aligned AS (
            SELECT
                trade_date,
                rn,
                sov_yield,
                ois_rate,
                ROUND(((sov_yield - ois_rate) * 100)::numeric, 2)
                    ::double precision AS spread_bps
            FROM filled
            WHERE sov_yield IS NOT NULL
              AND ois_rate IS NOT NULL
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                sov_yield,
                ois_rate,
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
            -- Data-anchored cutoff: matches the tool's
            -- ``wide.index[-1].date()`` convention.
            SELECT MAX(trade_date) AS as_of_date
            FROM scored
        ),
        display_rows AS (
            SELECT
                s.trade_date,
                s.rn,
                s.sov_yield,
                s.ois_rate,
                s.spread_bps,
                s.z_score
            FROM scored s
            CROSS JOIN latest_as_of a
            WHERE s.trade_date >= a.as_of_date
                  - (:lookback_days * INTERVAL '1 day')
        ),
        display_metrics AS (
            SELECT
                trade_date,
                rn,
                sov_yield,
                ois_rate,
                spread_bps,
                z_score,
                CASE
                    WHEN LAG(spread_bps, 1) OVER (ORDER BY rn) IS NULL
                    THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 1)
                            OVER (ORDER BY rn))::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(spread_bps, 5) OVER (ORDER BY rn) IS NULL
                    THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 5)
                            OVER (ORDER BY rn))::numeric,
                        2
                    )::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(spread_bps, 21) OVER (ORDER BY rn) IS NULL
                    THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 21)
                            OVER (ORDER BY rn))::numeric,
                        2
                    )::double precision
                END AS monthly_change_bps,
                ROUND((MAX(spread_bps) OVER tw)::numeric, 2)
                    ::double precision AS high_252d_bps,
                ROUND((MIN(spread_bps) OVER tw)::numeric, 2)
                    ::double precision AS low_252d_bps,
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
                                MAX(spread_bps) OVER tw
                                - MIN(spread_bps) OVER tw,
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
            sov_yield,
            ois_rate,
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
                "sovereign_curve_family": sovereign_curve_family,
                "sovereign_field_name": sovereign_field_name,
                "ois_curve_family": ois_curve_family,
                "ois_field_name": ois_field_name,
                "tenor": tenor,
                "lookback_days": lookback_days,
            },
        ).mappings().all()

    if not rows:
        return {"error": "SQL baseline returned no rows."}

    latest = rows[-1]
    return {
        "current_metrics": {
            "as_of_date": latest["date"],
            "sovereign_curve_family": sovereign_curve_family,
            "ois_curve_family": ois_curve_family,
            "tenor": tenor,
            "spread_label": (
                f"{sovereign_curve_family}-{ois_curve_family} {tenor}"
            ),
            "current_spread_bps": latest["spread_bps"],
            "daily_change_bps": latest["daily_change_bps"],
            "weekly_change_bps": latest["weekly_change_bps"],
            "monthly_change_bps": latest["monthly_change_bps"],
            "current_z_score": latest["z_score"],
            "rolling_window_days": 252,
            "high_252d_bps": latest["high_252d_bps"],
            "low_252d_bps": latest["low_252d_bps"],
            "percentile_252d": latest["percentile_252d"],
            "sovereign_yield_pct": latest["sov_yield"],
            "ois_rate_pct": latest["ois_rate"],
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
            "sovereign_curve_family",
            "ois_curve_family",
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
            "sovereign_yield_pct",
            "ois_rate_pct",
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
    sovereign_field_name: str,
    ois_field_name: str,
) -> List[str]:
    sovereign_curve_family, ois_curve_family, tenor = case
    tool_result = calculate_swap_spread(
        engine=engine,
        params=SwapSpreadInput(
            sovereign_curve_family=sovereign_curve_family,
            ois_curve_family=ois_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            sovereign_field_name=sovereign_field_name,
            ois_field_name=ois_field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        sovereign_curve_family=sovereign_curve_family,
        sovereign_field_name=sovereign_field_name,
        ois_curve_family=ois_curve_family,
        ois_field_name=ois_field_name,
        tenor=tenor,
        lookback_days=lookback_days,
    )
    return compare_results(tool_result, sql_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the cross-domain swap-spread tool against direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--sov-field", default=DEFAULT_SOVEREIGN_FIELD)
    parser.add_argument("--ois-field", default=DEFAULT_OIS_FIELD)
    args = parser.parse_args()

    print("=" * 80)
    print("CROSS-DOMAIN SWAP-SPREAD TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  sovereign_field : {args.sov_field}")
    print(f"  ois_field       : {args.ois_field}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live DB metadata...")
    cases = choose_test_cases(
        engine,
        sovereign_field_name=args.sov_field,
        ois_field_name=args.ois_field,
        case_count=args.cases,
        seed=args.seed,
    )
    if not cases:
        print("No cross-domain swap-spread validation cases found.")
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
            sovereign_field_name=args.sov_field,
            ois_field_name=args.ois_field,
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


if __name__ == "__main__":
    main()
