#!/usr/bin/env python3
"""
test_futures_price_level_sql_validation.py — bond-futures price-level
                                              validator

Independently reproduces the bond-futures front-month price-level
computation in SQL (current_price, daily/weekly/monthly price deltas,
rolling 252-day z-score, trailing 252-day high / low / percentile,
observation_count) and asserts the Python tool result matches the SQL
result row-for-row across a representative set of rolling-generic
contracts.

Notes on independence
---------------------
The SQL baseline filters via ``instrument_master.contract_code`` (the
rolling-generic stem) joined to ``market_data_daily`` directly — NOT
via ``v_market_data_daily_enriched.contract_code`` (whose value is
the per-window underlying contract from the SCD2 history, e.g. TYH6,
not the stem TY1). This mirrors ``fetch_rolling_generic_series``'s
fetcher shape but is its own SQL statement so the cross-check is
genuinely independent of the Python fetcher.

This script is a standalone CLI runner (matching the existing
``test_*_sql_validation.py`` shape); it must be EXCLUDED from pytest
collection via ``tests/conftest.py``'s ``collect_ignore`` list.
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
from rates_agent.bond_futures.tools.schemas import FuturesPriceLevelInput  # noqa: E402
from rates_agent.bond_futures.tools.futures_price_level import (  # noqa: E402
    calculate_futures_price_level,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (curve_family, contract_code)
Case = Tuple[str, str]


DEFAULT_CASE_COUNT = 8
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"


# The bond_futures playbook universe per ADR 0013 — sovereign-bond
# futures curve families only. Excludes the policy-futures families
# (SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT) which are strip-position-
# keyed and routed to a separate domain agent (policy_futures), AND
# whose ``instrument_master.tenor`` is NULL because they index a
# central-bank rate not a sovereign tenor.
BOND_FUTURES_CURVE_FAMILIES: List[str] = [
    "UST_FUT", "DE_FUT", "UK_FUT", "JP_FUT", "FR_FUT", "IT_FUT",
    "ES_FUT", "CA_FUT", "AU_FUT",
]

# Regression cases — guaranteed to be in the rolling-generic universe
# whenever the bond_futures playbook is ingested. TY1 is the 10Y UST
# bellwether; RX1 the 10Y Bund; JB1 the 10Y JGB.
REGRESSION_CASES: List[Case] = [
    ("UST_FUT", "TY1"),
    ("DE_FUT", "RX1"),
    ("JP_FUT", "JB1"),
]


TOLERANCE_BY_FIELD = {
    # The Python tool rounds with price_round_decimals = 6; the SQL
    # baseline rounds to 6 as well so the comparison is at the
    # convention's nominal precision.
    "current_price": 1e-5,
    "daily_change_price": 1e-5,
    "weekly_change_price": 1e-5,
    "monthly_change_price": 1e-5,
    "z_score": 6e-3,
    "high_252d_price": 1e-5,
    "low_252d_price": 1e-5,
    "percentile_252d": 0.11,
}


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick rolling-generic stems from the live universe that have at
    least 252 trading days of PX_LAST in the lookback buffer — keeps
    the SQL z-score numerically meaningful."""
    query = text(
        """
        SELECT i.curve_family, i.contract_code, COUNT(*) AS n_rows
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master i
          ON d.instrument_id = i.instrument_id
        WHERE i.is_rolling_contract = TRUE
          AND i.curve_family = ANY(:curve_families)
          AND i.tenor IS NOT NULL
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - INTERVAL '500 days'
        GROUP BY i.curve_family, i.contract_code
        HAVING COUNT(*) >= 252
        ORDER BY i.curve_family, i.contract_code
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            query,
            {
                "field_name": field_name,
                "curve_families": BOND_FUTURES_CURVE_FAMILIES,
            },
        ).mappings().all()
    pool = [(row["curve_family"], row["contract_code"]) for row in rows]
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
    contract_code: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Reproduce the Python primitive's snapshot using window
    functions over the rolling-generic's PX_LAST history.

    Independence:
      - Filters via ``instrument_master.contract_code`` (the master
        stem) joined to ``market_data_daily`` directly — NOT via the
        enriched view's SCD2-overridden contract_code column.
      - Computes z-score, period deltas, trailing range, and
        percentile in SQL via WINDOW functions; no Python primitive
        involvement.
    """
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                d.trade_date,
                d.field_value::double precision AS field_value
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master i
              ON d.instrument_id = i.instrument_id
            WHERE i.is_rolling_contract = TRUE
              AND i.curve_family   = :curve_family
              AND i.contract_code  = :contract_code
              AND d.field_name     = :field_name
              AND d.trade_date    >= CURRENT_DATE - ((:lookback_days + 378) * INTERVAL '1 day')
        ),
        clean AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE field_value IS NOT NULL
        ),
        deduped AS (
            -- ``clean_single_series`` drops duplicate dates keeping the
            -- last; ``DISTINCT ON`` gives identical semantics.
            SELECT DISTINCT ON (trade_date)
                trade_date, field_value
            FROM clean
            ORDER BY trade_date, field_value
        ),
        ordered AS (
            SELECT
                trade_date,
                field_value,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM deduped
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
                ROUND((MAX(field_value) OVER tw)::numeric, 6)::double precision AS high_252d_price,
                ROUND((MIN(field_value) OVER tw)::numeric, 6)::double precision AS low_252d_price,
                CASE
                    WHEN MAX(field_value) OVER tw IS NULL
                      OR MIN(field_value) OVER tw IS NULL
                      OR MAX(field_value) OVER tw = MIN(field_value) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (field_value - MIN(field_value) OVER tw)
                            / NULLIF(
                                MAX(field_value) OVER tw - MIN(field_value) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d,
                -- Period changes are RAW subtraction (price units), NOT
                -- multiplied by 100. The Python primitive uses
                -- ``period_changes(..., already_bps=True)`` for the
                -- same semantics.
                CASE
                    WHEN LAG(field_value, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (field_value - LAG(field_value, 1) OVER (ORDER BY rn))::numeric,
                        6
                    )::double precision
                END AS daily_change_price,
                CASE
                    WHEN LAG(field_value, 5) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (field_value - LAG(field_value, 5) OVER (ORDER BY rn))::numeric,
                        6
                    )::double precision
                END AS weekly_change_price,
                CASE
                    WHEN LAG(field_value, 21) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (field_value - LAG(field_value, 21) OVER (ORDER BY rn))::numeric,
                        6
                    )::double precision
                END AS monthly_change_price
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
            WHERE trade_date >= (
                SELECT MAX(trade_date) - (:lookback_days * INTERVAL '1 day')
                FROM ordered
            )
        )
        SELECT
            TO_CHAR(latest.trade_date, 'YYYY-MM-DD') AS as_of_date,
            ROUND(latest.field_value::numeric, 6)::double precision AS current_price,
            latest.daily_change_price,
            latest.weekly_change_price,
            latest.monthly_change_price,
            latest.z_score,
            latest.high_252d_price,
            latest.low_252d_price,
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
                "contract_code": contract_code,
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
            "contract_code": contract_code,
            "current_price": row["current_price"],
            "daily_change_price": row["daily_change_price"],
            "weekly_change_price": row["weekly_change_price"],
            "monthly_change_price": row["monthly_change_price"],
            "z_score": row["z_score"],
            "high_252d_price": row["high_252d_price"],
            "low_252d_price": row["low_252d_price"],
            "percentile_252d": row["percentile_252d"],
            "observation_count": row["observation_count"],
        }
    }


def compare_results(
    tool_result: Dict[str, Any], sql_result: Dict[str, Any],
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
            "as_of_date", "curve_family", "contract_code", "observation_count",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_price",
            "daily_change_price",
            "weekly_change_price",
            "monthly_change_price",
            "z_score",
            "high_252d_price",
            "low_252d_price",
            "percentile_252d",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    return mismatches


def run_case(
    engine, *, case: Case, lookback_days: int, field_name: str,
) -> List[str]:
    curve_family, contract_code = case
    tool_result = calculate_futures_price_level(
        engine=engine,
        params=FuturesPriceLevelInput(
            curve_family=curve_family,
            contract_code=contract_code,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        contract_code=contract_code,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the bond-futures price-level monitor against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("BOND-FUTURES PRICE-LEVEL MONITOR — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live rolling-generic universe...")
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
