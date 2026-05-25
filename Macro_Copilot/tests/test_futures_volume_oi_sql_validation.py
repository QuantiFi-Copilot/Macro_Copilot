#!/usr/bin/env python3
"""
test_futures_volume_oi_sql_validation.py — bond-futures volume + OI
                                            validator

Independently reproduces the bond-futures volume + open-interest
computation in SQL (current_volume, current_open_interest,
delta_open_interest_1d, rolling 252-day OI z-score, trailing 252-day
OI high / low / percentile, rolling 22-day volume mean / max,
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

The two series (PX_VOLUME + OPEN_INT) are joined ON ``trade_date`` in
SQL — the equivalent of the Python tool's ``index.intersection``
alignment step. The SQL z-score / trailing stats / period delta run
on the INNER-joined date series so both paths see the same aligned
input.

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
from rates_agent.bond_futures.tools.schemas import FuturesVolumeOIInput  # noqa: E402
from rates_agent.bond_futures.tools.futures_volume_oi import (  # noqa: E402
    calculate_futures_volume_oi,
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
DEFAULT_VOLUME_FIELD = "PX_VOLUME"
DEFAULT_OI_FIELD = "OPEN_INT"


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
    # Volume / OI are integer-valued in the source feed but rounded by
    # the Python tool via safe_float; the SQL baseline rounds to 0
    # decimals on the same series, so a 0.5-contract tolerance is the
    # right floor (half-tick on a rounded count).
    "current_volume": 0.5,
    "current_open_interest": 0.5,
    "delta_open_interest_1d": 0.5,
    "oi_z_score": 6e-3,
    "oi_high_252d": 0.5,
    "oi_low_252d": 0.5,
    "oi_percentile_252d": 0.11,
    "volume_rolling_mean_22d": 0.5,
    "volume_rolling_max_22d": 0.5,
}


def choose_test_cases(
    engine,
    *,
    volume_field: str,
    oi_field: str,
    case_count: int,
    seed: int,
) -> List[Case]:
    """Pick rolling-generic stems from the live universe that have at
    least 252 trading days of BOTH PX_VOLUME and OPEN_INT in the
    lookback buffer — keeps the SQL z-score numerically meaningful."""
    query = text(
        """
        SELECT i.curve_family, i.contract_code,
               COUNT(*) FILTER (WHERE d.field_name = :volume_field) AS n_vol,
               COUNT(*) FILTER (WHERE d.field_name = :oi_field) AS n_oi
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master i
          ON d.instrument_id = i.instrument_id
        WHERE i.is_rolling_contract = TRUE
          AND i.curve_family = ANY(:curve_families)
          AND i.tenor IS NOT NULL
          AND d.field_name = ANY(ARRAY[:volume_field, :oi_field])
          AND d.trade_date >= CURRENT_DATE - INTERVAL '500 days'
        GROUP BY i.curve_family, i.contract_code
        HAVING COUNT(*) FILTER (WHERE d.field_name = :volume_field) >= 252
           AND COUNT(*) FILTER (WHERE d.field_name = :oi_field) >= 252
        ORDER BY i.curve_family, i.contract_code
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            query,
            {
                "volume_field": volume_field,
                "oi_field": oi_field,
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
    volume_field: str,
    oi_field: str,
) -> Dict[str, Any]:
    """Reproduce the Python primitive's snapshot using window
    functions over the rolling-generic's volume + OI history.

    Independence:
      - Filters via ``instrument_master.contract_code`` (the master
        stem) joined to ``market_data_daily`` directly — NOT via the
        enriched view's SCD2-overridden contract_code column.
      - INNER-joins the two series on trade_date (mirrors the
        Python tool's ``index.intersection`` alignment step).
      - Computes z-score, ΔOI, trailing range, percentile, and
        volume rolling mean / max in SQL via WINDOW functions; no
        Python primitive involvement.
    """
    baseline_sql = text(
        """
        WITH raw_vol AS (
            SELECT
                d.trade_date,
                d.field_value::double precision AS volume
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master i
              ON d.instrument_id = i.instrument_id
            WHERE i.is_rolling_contract = TRUE
              AND i.curve_family   = :curve_family
              AND i.contract_code  = :contract_code
              AND d.field_name     = :volume_field
              AND d.trade_date    >= CURRENT_DATE - ((:lookback_days + 378) * INTERVAL '1 day')
              AND d.field_value   IS NOT NULL
        ),
        raw_oi AS (
            SELECT
                d.trade_date,
                d.field_value::double precision AS open_interest
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master i
              ON d.instrument_id = i.instrument_id
            WHERE i.is_rolling_contract = TRUE
              AND i.curve_family   = :curve_family
              AND i.contract_code  = :contract_code
              AND d.field_name     = :oi_field
              AND d.trade_date    >= CURRENT_DATE - ((:lookback_days + 378) * INTERVAL '1 day')
              AND d.field_value   IS NOT NULL
        ),
        vol_dedup AS (
            SELECT DISTINCT ON (trade_date)
                trade_date, volume
            FROM raw_vol
            ORDER BY trade_date, volume
        ),
        oi_dedup AS (
            SELECT DISTINCT ON (trade_date)
                trade_date, open_interest
            FROM raw_oi
            ORDER BY trade_date, open_interest
        ),
        joined AS (
            -- INNER JOIN reproduces the Python intersection on date.
            SELECT
                v.trade_date,
                v.volume,
                o.open_interest
            FROM vol_dedup v
            INNER JOIN oi_dedup o USING (trade_date)
        ),
        ordered AS (
            SELECT
                trade_date,
                volume,
                open_interest,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM joined
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                volume,
                open_interest,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(open_interest) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(open_interest) OVER zw <> 0
                    THEN ROUND(
                        (
                            (open_interest - AVG(open_interest) OVER zw)
                            / NULLIF(STDDEV_SAMP(open_interest) OVER zw, 0)
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS oi_z_score,
                ROUND(MAX(open_interest) OVER tw)::double precision AS oi_high_252d,
                ROUND(MIN(open_interest) OVER tw)::double precision AS oi_low_252d,
                CASE
                    WHEN MAX(open_interest) OVER tw IS NULL
                      OR MIN(open_interest) OVER tw IS NULL
                      OR MAX(open_interest) OVER tw = MIN(open_interest) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (open_interest - MIN(open_interest) OVER tw)
                            / NULLIF(
                                MAX(open_interest) OVER tw - MIN(open_interest) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS oi_percentile_252d,
                -- ΔOI is RAW subtraction in CONTRACTS (NOT multiplied
                -- by 100). Mirrors the Python tool's
                -- ``open_interest.iloc[-1] - open_interest.iloc[-2]``
                -- with delta_oi_offset_rows = 2.
                CASE
                    WHEN LAG(open_interest, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (open_interest - LAG(open_interest, 1) OVER (ORDER BY rn))::numeric,
                        0
                    )::double precision
                END AS delta_open_interest_1d,
                -- Volume rolling mean / max over the trailing 22 rows
                -- INCLUSIVE of current. Python uses .rolling(window=22,
                -- min_periods=1) so the first observations also emit a
                -- (smaller-sample) mean / max; SQL needs no min_periods
                -- guard at the WINDOW level — AVG / MAX over a 0-21
                -- preceding window with at least 1 row always emits.
                ROUND(AVG(volume) OVER vw)::double precision AS volume_rolling_mean_22d,
                ROUND(MAX(volume) OVER vw)::double precision AS volume_rolling_max_22d
            FROM ordered
            WINDOW
                zw AS (ORDER BY rn ROWS BETWEEN 251 PRECEDING AND CURRENT ROW),
                tw AS (ORDER BY rn ROWS BETWEEN 251 PRECEDING AND CURRENT ROW),
                vw AS (ORDER BY rn ROWS BETWEEN 21 PRECEDING AND CURRENT ROW)
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
            ROUND(latest.volume)::double precision AS current_volume,
            ROUND(latest.open_interest)::double precision AS current_open_interest,
            latest.delta_open_interest_1d,
            latest.oi_z_score,
            latest.oi_high_252d,
            latest.oi_low_252d,
            latest.oi_percentile_252d,
            latest.volume_rolling_mean_22d,
            latest.volume_rolling_max_22d,
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
                "volume_field": volume_field,
                "oi_field": oi_field,
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
            "current_volume": row["current_volume"],
            "current_open_interest": row["current_open_interest"],
            "delta_open_interest_1d": row["delta_open_interest_1d"],
            "oi_z_score": row["oi_z_score"],
            "oi_high_252d": row["oi_high_252d"],
            "oi_low_252d": row["oi_low_252d"],
            "oi_percentile_252d": row["oi_percentile_252d"],
            "volume_rolling_mean_22d": row["volume_rolling_mean_22d"],
            "volume_rolling_max_22d": row["volume_rolling_max_22d"],
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
            "current_volume",
            "current_open_interest",
            "delta_open_interest_1d",
            "oi_z_score",
            "oi_high_252d",
            "oi_low_252d",
            "oi_percentile_252d",
            "volume_rolling_mean_22d",
            "volume_rolling_max_22d",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    return mismatches


def run_case(
    engine,
    *,
    case: Case,
    lookback_days: int,
    volume_field: str,
    oi_field: str,
) -> List[str]:
    curve_family, contract_code = case
    tool_result = calculate_futures_volume_oi(
        engine=engine,
        params=FuturesVolumeOIInput(
            curve_family=curve_family,
            contract_code=contract_code,
            lookback_days=lookback_days,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        contract_code=contract_code,
        lookback_days=lookback_days,
        volume_field=volume_field,
        oi_field=oi_field,
    )
    return compare_results(tool_result, sql_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the bond-futures volume + OI monitor against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--volume-field", default=DEFAULT_VOLUME_FIELD)
    parser.add_argument("--oi-field", default=DEFAULT_OI_FIELD)
    args = parser.parse_args()

    print("=" * 80)
    print("BOND-FUTURES VOLUME + OI MONITOR — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  volume_field  : {args.volume_field}")
    print(f"  oi_field      : {args.oi_field}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live rolling-generic universe...")
    cases = choose_test_cases(
        engine,
        volume_field=args.volume_field,
        oi_field=args.oi_field,
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
            volume_field=args.volume_field,
            oi_field=args.oi_field,
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
