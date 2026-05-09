#!/usr/bin/env python3
"""
test_real_yield_level_sql_validation.py — Linker real-yield-level
validator.

Validate ``get_real_yield_level`` against an independent SQL baseline
run directly on ``macro_data.v_market_data_daily_enriched``.  The SQL
baseline reproduces the level-stat math (z-score / period changes /
trailing range) without going through any of the Python tool's
helpers, so a mismatch between the two surfaces a real divergence in
methodology rather than a shared-code coincidence.

Key linker-specific differences from the sovereign yield_levels
validator:
  - filter ``instrument_type = 'inflation_linker'`` (vs
    ``'sovereign_benchmark'`` for nominal)
  - anchor the lookback cutoff to the data's last observation date,
    NOT ``CURRENT_DATE`` — linker daily feeds can lag wall-clock by
    days; the Python tool's anchoring matches OIS rate_level (last
    observation), not sovereign yield_levels (today)

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \
        tests/test_real_yield_level_sql_validation.py
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
from rates_agent.inflation_indexed_bonds.tools.schemas import (  # noqa: E402
    RealYieldLevelInput,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (  # noqa: E402
    get_real_yield_level,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


Case = Tuple[str, str]

DEFAULT_CASE_COUNT = 10
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"

# One representative case per linker curve family — keeps regression
# coverage broad without over-sampling any single family.
REGRESSION_CASES: List[Case] = [
    ("USD_TIPS", "10Y"),
    ("GBP_LINKER", "10Y"),
    ("EUR_FR_LINKER", "10Y"),
    ("CAD_RRB", "10Y"),
]

# Linker real-yield series have lower observation density than nominal
# sovereigns at some tenors (e.g. CAD_RRB 5Y starts mid-2016) and the
# rounding-conventions tolerances are inherited from yield_levels.
TOLERANCE_BY_FIELD = {
    "real_yield_pct": 0.00011,
    "daily_change_bps": 0.011,
    "weekly_change_bps": 0.011,
    "monthly_change_bps": 0.011,
    "z_score": 0.006,
    "high_252d_pct": 0.00011,
    "low_252d_pct": 0.00011,
    "percentile_252d": 0.11,
}


def choose_test_cases(engine, *, field_name: str, case_count: int, seed: int) -> List[Case]:
    """Pick (curve_family, tenor) cases from live linker metadata.

    Filters to ``instrument_type='inflation_linker'`` so the SQL
    baseline operates on the same universe the Python tool sees.
    Requires at least 80 observations to ensure rolling stats are
    populated.
    """
    query = text(
        """
        SELECT curve_family, tenor
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type = 'inflation_linker'
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
    """Independent SQL reproduction of the level-stat math.

    Differences from the sovereign yield_levels SQL baseline:
      - ``instrument_type = 'inflation_linker'``
      - ``observation_count`` cutoff anchored to the data's latest
        ``trade_date``, NOT ``CURRENT_DATE`` — matches the Python
        tool's anchoring (which mirrors OIS rate_level).
    """
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'inflation_linker'
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
            -- Anchor lookback cutoff to data's latest trade_date —
            -- matches the Python tool (which matches OIS rate_level).
            -- Sovereign yield_levels anchors to CURRENT_DATE; that
            -- divergence is intentional and documented in both tools'
            -- planned_extensions.
            SELECT COUNT(*) AS observation_count
            FROM ordered
            WHERE trade_date >= (
                (SELECT MAX(trade_date) FROM ordered)
                - (:lookback_days * INTERVAL '1 day')
            )
        )
        SELECT
            TO_CHAR(latest.trade_date, 'YYYY-MM-DD') AS as_of_date,
            latest.field_value AS real_yield_pct,
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
            "real_yield_pct": row["real_yield_pct"],
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
            "real_yield_pct",
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
    tool_result = get_real_yield_level(
        engine=engine,
        params=RealYieldLevelInput(
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


# Adversarial probe pinning the linker instrument_type guard: a known
# nominal sovereign curve_family must yield ZERO rows under the
# linker SQL filter AND the Python tool must return the controlled
# error envelope.  This is the SQL-side companion to the offline
# tests in tests/test_real_yield_level_compute.py — proves the SQL
# filter and the Python guard agree on the same universe.  Lives here
# (instead of in pytest collection) because it requires a live DB
# engine and the rest of this module is already standalone.
NOMINAL_GUARD_PROBE: Case = ("UST", "10Y")


def assert_linker_instrument_type_guard(
    engine,
    *,
    case: Case,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Adversarial probe — verify the instrument_type guard at the
    SQL boundary.

    Two invariants:
      1. SQL-side: zero rows match
         ``(curve_family=case[0], tenor=case[1], field_name=field_name,
            instrument_type='inflation_linker')``.
      2. Python-side: ``get_real_yield_level`` returns the controlled
         ``{"error": "..."}`` envelope (NOT a fabricated snapshot).

    Mismatch on either side breaks the proxy-prevention contract.
    """
    failures: List[str] = []
    curve_family, tenor = case

    sql = text(
        """
        SELECT COUNT(*) AS n
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type = 'inflation_linker'
          AND curve_family    = :curve_family
          AND tenor           = :tenor
          AND field_name      = :field_name
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
            },
        ).mappings().first()
    n_linker_rows = int(row["n"]) if row is not None else 0

    if n_linker_rows != 0:
        failures.append(
            f"SQL invariant violated: linker filter returned "
            f"{n_linker_rows} rows for nominal probe ({curve_family}, "
            f"{tenor}); the proxy-prevention guarantee assumes zero."
        )

    tool_result = get_real_yield_level(
        engine=engine,
        params=RealYieldLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    if "error" not in tool_result:
        failures.append(
            "Python tool returned a snapshot for nominal probe "
            f"({curve_family}, {tenor}) — must return controlled "
            "error envelope instead.  Got keys: "
            f"{sorted(tool_result.keys())}"
        )
    elif "inflation_linker" not in tool_result.get("error", ""):
        failures.append(
            "Controlled error envelope is missing the inflation_linker "
            "rationale required to route nominal questions to the "
            f"sovereign tool.  Got: {tool_result['error']!r}"
        )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the linker real_yield_level tool against direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("LINKER REAL-YIELD LEVEL TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live linker metadata...")
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

    print(
        "[4/4] Adversarial probe: linker instrument_type guard "
        f"(nominal {NOMINAL_GUARD_PROBE[0]} {NOMINAL_GUARD_PROBE[1]})..."
    )
    guard_failures = assert_linker_instrument_type_guard(
        engine,
        case=NOMINAL_GUARD_PROBE,
        field_name=args.field,
        lookback_days=args.days,
    )
    if guard_failures:
        print("FAIL")
        for f in guard_failures:
            print(f"  - {f}")
    else:
        print("PASS")

    print("Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")
    print(
        f"  guard_probe : {'PASS' if not guard_failures else 'FAIL'} "
        f"({NOMINAL_GUARD_PROBE[0]} {NOMINAL_GUARD_PROBE[1]} → "
        "must yield zero linker rows + controlled error envelope)"
    )

    if failed_cases or guard_failures:
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]} {case[1]} ({len(mismatches)} mismatches)"
                )
        if guard_failures:
            print("\nINSTRUMENT_TYPE GUARD FAILURES:")
            for f in guard_failures:
                print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
