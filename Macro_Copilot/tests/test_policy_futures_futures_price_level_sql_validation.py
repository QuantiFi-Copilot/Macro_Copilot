#!/usr/bin/env python3
"""
test_policy_futures_futures_price_level_sql_validation.py — policy-
                                                            futures price-
                                                            level validator

Independently reproduces the policy-futures strip-position price-level
computation in SQL (raw_price, implied_rate_pct, 1-day price + rate
deltas, rolling 252-day z-score on the IMPLIED-RATE level series,
trailing 252-day high / low / mid on BOTH unit axes, percentile rank
of the current implied rate, observation_count) and asserts the
Python tool result matches the SQL result row-for-row across a
representative set of (curve_family, strip_position) cases spanning
both the RFR (SOFR / SONIA) and IBOR (Euribor) regimes.

Independence
------------
The SQL baseline filters via ``v_market_data_daily_enriched`` with
the same ``(curve_family, (attributes->>'strip_position')::int,
field_name)`` predicate ``fetch_strip_position`` uses, AND
independently reads the per-strip ``inverse_pricing`` flag from
``instrument_master.attributes`` to drive the implied-rate
conversion. The z-score / range / percentile are computed in SQL
via WINDOW functions; no Python primitive involvement.

Future-anchor guard cross-check
-------------------------------
The runner also calls the Python tool with
``as_of_date = universe_max + 1 day`` for each case and asserts the
controlled-error envelope returns with the documented prefix
("no scoreable strip:"); a separate SQL probe counts rows past the
requested anchor to verify the guard had a real reason to fire.

This script is a standalone CLI runner (matching the existing
``test_*_sql_validation.py`` shape); it must be EXCLUDED from
pytest collection via ``tests/conftest.py``'s ``collect_ignore``
list.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.policy_futures.tools.schemas import (  # noqa: E402
    FuturesPriceLevelInput,
)
from rates_agent.policy_futures.tools.futures_price_level import (  # noqa: E402
    calculate_futures_price_level,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (curve_family, strip_position)
Case = Tuple[str, int]


DEFAULT_CASE_COUNT = 8
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"


# The policy_futures playbook universe per ADR 0013 — strip-position-
# keyed only. Spans both regimes:
#   - RFR: SOFR_FUT (US), SONIA_FUT (UK)
#   - IBOR: EUR_SHORT_RATE_FUT (Euro area)
POLICY_FUTURES_CURVE_FAMILIES: List[str] = [
    "SOFR_FUT",
    "EUR_SHORT_RATE_FUT",
    "SONIA_FUT",
]

# Regression cases — guaranteed to be in the strip universe whenever
# the policy_futures playbook is ingested. Cover all three families
# AND a front (1), mid (4 = end of whites), back (8 = end of reds)
# strip position so the SQL baseline exercises the full strip range.
REGRESSION_CASES: List[Case] = [
    ("SOFR_FUT", 1),
    ("SOFR_FUT", 4),
    ("SONIA_FUT", 1),
    ("SONIA_FUT", 8),
    ("EUR_SHORT_RATE_FUT", 1),
    ("EUR_SHORT_RATE_FUT", 8),
]


TOLERANCE_BY_FIELD = {
    # The Python tool rounds with raw_price_round_decimals = 5; the
    # SQL baseline rounds to 5 to match.
    "raw_price": 1e-4,
    "daily_change_raw_price": 1e-4,
    "high_252d_raw_price": 1e-4,
    "low_252d_raw_price": 1e-4,
    "mid_252d_raw_price": 1e-4,
    # implied_rate_round_decimals = 4
    "implied_rate_pct": 1e-3,
    "daily_change_implied_rate_pct": 1e-3,
    "high_252d_implied_rate_pct": 1e-3,
    "low_252d_implied_rate_pct": 1e-3,
    "mid_252d_implied_rate_pct": 1e-3,
    # z_score_round_decimals = 4
    "z_score_implied_rate": 6e-3,
    # percentile_round_decimals = 1
    "percentile_252d": 0.11,
}


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (curve_family, strip_position) pairs from the live
    universe that have at least 252 trading days of PX_LAST in the
    lookback buffer — keeps the SQL z-score numerically meaningful."""
    query = text(
        """
        SELECT
            i.curve_family,
            (i.attributes->>'strip_position')::int AS strip_position,
            COUNT(*) AS n_rows
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master i
          ON d.instrument_id = i.instrument_id
        WHERE i.is_rolling_contract = TRUE
          AND i.instrument_type     = 'policy_future'
          AND i.curve_family        = ANY(:curve_families)
          AND d.field_name          = :field_name
          AND d.trade_date         >= CURRENT_DATE - INTERVAL '700 days'
        GROUP BY i.curve_family, (i.attributes->>'strip_position')::int
        HAVING COUNT(*) >= 252
        ORDER BY i.curve_family, (i.attributes->>'strip_position')::int
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            query,
            {
                "field_name": field_name,
                "curve_families": POLICY_FUTURES_CURVE_FAMILIES,
            },
        ).mappings().all()
    pool = [(row["curve_family"], int(row["strip_position"])) for row in rows]
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
    strip_position: int,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Reproduce the Python primitive's snapshot using window
    functions over the strip-slot's PX_LAST history. Reads the
    per-strip ``inverse_pricing`` flag from
    ``instrument_master.attributes`` independently so the implied-rate
    conversion is driven off metadata, just like the Python primitive.
    """
    # ------------------------------------------------------------------
    # 1. Read inverse_pricing flag independently (PR8 cross-check).
    # ------------------------------------------------------------------
    flag_sql = text(
        """
        SELECT
            (i.attributes->>'inverse_pricing')::boolean AS inverse_pricing,
            i.contract_code
        FROM macro_data.instrument_master i
        WHERE i.curve_family   = :curve_family
          AND (i.attributes->>'strip_position')::int = :strip_position
          AND i.is_rolling_contract = TRUE
        LIMIT 1
        """
    )
    with engine.connect() as conn:
        flag_row = conn.execute(
            flag_sql,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
            },
        ).mappings().first()
    if flag_row is None:
        return {"error": "SQL baseline: no instrument_master row."}
    inverse_priced = bool(flag_row["inverse_pricing"])
    contract_code = flag_row["contract_code"]

    # ------------------------------------------------------------------
    # 2. Window-function baseline on the cleaned series.
    # ------------------------------------------------------------------
    baseline_sql = text(
        f"""
        WITH raw AS (
            SELECT
                trade_date,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE curve_family = :curve_family
              AND (attributes->>'strip_position')::int = :strip_position
              AND field_name   = :field_name
              AND trade_date  >= CURRENT_DATE - ((:lookback_days + 378) * INTERVAL '1 day')
        ),
        clean AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE field_value IS NOT NULL
        ),
        deduped AS (
            SELECT DISTINCT ON (trade_date)
                trade_date, field_value
            FROM clean
            ORDER BY trade_date, field_value
        ),
        ordered AS (
            SELECT
                trade_date,
                field_value AS raw_price,
                CASE WHEN {('TRUE' if inverse_priced else 'FALSE')}
                     THEN 100.0 - field_value
                     ELSE field_value
                END AS implied_rate,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM deduped
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                raw_price,
                implied_rate,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(implied_rate) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(implied_rate) OVER zw <> 0
                    THEN ROUND(
                        (
                            (implied_rate - AVG(implied_rate) OVER zw)
                            / NULLIF(STDDEV_SAMP(implied_rate) OVER zw, 0)
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score_implied_rate,
                ROUND((MAX(implied_rate) OVER tw)::numeric, 4)::double precision
                    AS high_252d_implied_rate_pct,
                ROUND((MIN(implied_rate) OVER tw)::numeric, 4)::double precision
                    AS low_252d_implied_rate_pct,
                ROUND((MAX(raw_price) OVER tw)::numeric, 5)::double precision
                    AS high_252d_raw_price,
                ROUND((MIN(raw_price) OVER tw)::numeric, 5)::double precision
                    AS low_252d_raw_price,
                CASE
                    WHEN MAX(implied_rate) OVER tw IS NULL
                      OR MIN(implied_rate) OVER tw IS NULL
                      OR MAX(implied_rate) OVER tw = MIN(implied_rate) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (implied_rate - MIN(implied_rate) OVER tw)
                            / NULLIF(
                                MAX(implied_rate) OVER tw
                                - MIN(implied_rate) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d,
                CASE
                    WHEN LAG(raw_price, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (raw_price - LAG(raw_price, 1) OVER (ORDER BY rn))::numeric,
                        5
                    )::double precision
                END AS daily_change_raw_price,
                CASE
                    WHEN LAG(implied_rate, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (implied_rate - LAG(implied_rate, 1) OVER (ORDER BY rn))::numeric,
                        4
                    )::double precision
                END AS daily_change_implied_rate_pct
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
            ROUND(latest.raw_price::numeric, 5)::double precision AS raw_price,
            ROUND(latest.implied_rate::numeric, 4)::double precision AS implied_rate_pct,
            latest.daily_change_raw_price,
            latest.daily_change_implied_rate_pct,
            latest.z_score_implied_rate,
            latest.high_252d_implied_rate_pct,
            latest.low_252d_implied_rate_pct,
            ROUND(
                ((latest.high_252d_implied_rate_pct
                  + latest.low_252d_implied_rate_pct) / 2.0)::numeric,
                4
            )::double precision AS mid_252d_implied_rate_pct,
            latest.high_252d_raw_price,
            latest.low_252d_raw_price,
            ROUND(
                ((latest.high_252d_raw_price + latest.low_252d_raw_price) / 2.0)::numeric,
                5
            )::double precision AS mid_252d_raw_price,
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
                "strip_position": strip_position,
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
            "strip_position": strip_position,
            "contract_code": contract_code,
            "inverse_priced": inverse_priced,
            "raw_price": row["raw_price"],
            "implied_rate_pct": row["implied_rate_pct"],
            "daily_change_raw_price": row["daily_change_raw_price"],
            "daily_change_implied_rate_pct": row["daily_change_implied_rate_pct"],
            "z_score_implied_rate": row["z_score_implied_rate"],
            "high_252d_implied_rate_pct": row["high_252d_implied_rate_pct"],
            "low_252d_implied_rate_pct": row["low_252d_implied_rate_pct"],
            "mid_252d_implied_rate_pct": row["mid_252d_implied_rate_pct"],
            "high_252d_raw_price": row["high_252d_raw_price"],
            "low_252d_raw_price": row["low_252d_raw_price"],
            "mid_252d_raw_price": row["mid_252d_raw_price"],
            "percentile_252d": row["percentile_252d"],
            "observation_count": row["observation_count"],
        }
    }


def sql_universe_max_date(
    engine,
    *,
    curve_family: str,
    strip_position: int,
    field_name: str,
) -> date:
    """SQL-only probe for the strip's MAX(trade_date) — independent of
    the Python tool's ``fetch_strip_position_max_date`` helper.

    Used by the future-anchor guard cross-check below.
    """
    sql = text(
        """
        SELECT MAX(trade_date) AS max_trade_date
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND (attributes->>'strip_position')::int = :strip_position
          AND field_name   = :field_name
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
                "field_name": field_name,
            },
        ).first()
    if row is None or row[0] is None:
        raise RuntimeError(
            f"sql_universe_max_date: no rows for {curve_family} "
            f"strip_position={strip_position}, field={field_name}"
        )
    value = row[0]
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def sql_count_rows_past_anchor(
    engine,
    *,
    curve_family: str,
    strip_position: int,
    field_name: str,
    anchor: date,
) -> int:
    """Count rows in the strip's series with trade_date > anchor —
    used to assert the future-anchor guard had a real reason to fire
    (or, in the within-data case, that no rows would have leaked past
    the requested anchor)."""
    sql = text(
        """
        SELECT COUNT(*) AS n_rows_past
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND (attributes->>'strip_position')::int = :strip_position
          AND field_name   = :field_name
          AND trade_date   > :anchor
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
                "field_name": field_name,
                "anchor": anchor.isoformat(),
            },
        ).first()
    return int(row[0]) if row is not None else 0


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
            "as_of_date", "curve_family", "strip_position",
            "contract_code", "inverse_priced", "observation_count",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "raw_price",
            "implied_rate_pct",
            "daily_change_raw_price",
            "daily_change_implied_rate_pct",
            "z_score_implied_rate",
            "high_252d_implied_rate_pct",
            "low_252d_implied_rate_pct",
            "mid_252d_implied_rate_pct",
            "high_252d_raw_price",
            "low_252d_raw_price",
            "mid_252d_raw_price",
            "percentile_252d",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    return mismatches


def run_case(
    engine, *, case: Case, lookback_days: int, field_name: str,
) -> List[str]:
    curve_family, strip_position = case
    tool_result = calculate_futures_price_level(
        engine=engine,
        params=FuturesPriceLevelInput(
            curve_family=curve_family,
            strip_position=strip_position,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        strip_position=strip_position,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    mismatches = compare_results(tool_result, sql_result)

    # ------------------------------------------------------------------
    # Future-anchor guard cross-check — call the tool with
    # as_of_date = universe_max + 1 day and assert the controlled-
    # error envelope. Independently SQL-probe rows past the requested
    # anchor (must be 0 — proving the guard had a real reason to
    # fire).
    # ------------------------------------------------------------------
    try:
        universe_max = sql_universe_max_date(
            engine=engine,
            curve_family=curve_family,
            strip_position=strip_position,
            field_name=field_name,
        )
    except RuntimeError as exc:
        mismatches.append(f"Future-anchor probe failed: {exc}")
        return mismatches
    future_anchor = universe_max + timedelta(days=1)
    rows_past = sql_count_rows_past_anchor(
        engine=engine,
        curve_family=curve_family,
        strip_position=strip_position,
        field_name=field_name,
        anchor=future_anchor,
    )
    if rows_past != 0:
        mismatches.append(
            f"Future-anchor SQL probe found {rows_past} row(s) past "
            f"{future_anchor.isoformat()} — the universe_max probe "
            "is out of sync with the row data."
        )

    guard_result = calculate_futures_price_level(
        engine=engine,
        params=FuturesPriceLevelInput(
            curve_family=curve_family,
            strip_position=strip_position,
            lookback_days=lookback_days,
            field_name=field_name,
            as_of_date=future_anchor,
        ),
    )
    if "error" not in guard_result:
        mismatches.append(
            f"Future-anchor guard did NOT fire for "
            f"as_of_date={future_anchor.isoformat()}; tool returned "
            "a snapshot instead of the controlled-error envelope."
        )
    elif "no scoreable strip" not in guard_result["error"]:
        mismatches.append(
            f"Future-anchor guard fired with the WRONG envelope "
            f"prefix: {guard_result['error']!r}"
        )

    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the policy-futures price-level monitor against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("POLICY-FUTURES PRICE-LEVEL MONITOR — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live strip universe...")
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases, lambda case: f"{case[0]} strip_position={case[1]}"
    )

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases), f"{case[0]} strip_position={case[1]}"
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
            print(f"  - {case[0]} strip_position={case[1]} "
                  f"({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
