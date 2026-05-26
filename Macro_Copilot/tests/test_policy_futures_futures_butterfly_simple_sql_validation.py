#!/usr/bin/env python3
"""
test_policy_futures_futures_butterfly_simple_sql_validation.py —
                                                                policy-
                                                                futures
                                                                simple-
                                                                butterfly
                                                                validator

Independently reproduces the policy-futures simple-butterfly
computation in SQL (butterfly_value_pct, 1-day delta, rolling 252-day
z-score, trailing 252-day high / low / mid / percentile,
observation_count) and asserts the Python tool result matches the SQL
result row-for-row across a representative set of (curve_family,
wing_short, body, wing_long) cases.

Independence
------------
The SQL baseline filters via ``v_market_data_daily_enriched`` with
the same ``(curve_family, (attributes->>'strip_position')::int,
field_name)`` predicate ``fetch_strip_group`` uses, INNER-joins the
three legs' series on trade_date, and independently reads the
per-leg ``inverse_pricing`` flag from
``instrument_master.attributes`` to drive the implied-rate
conversion. The butterfly is computed via the SAME fixed 50-50
weighting (body − 0.5 * (wing_short + wing_long)). The z-score /
range / percentile / 1-day delta are computed in SQL via WINDOW
functions.

Future-anchor guard cross-check
-------------------------------
The runner also calls the Python tool with
``as_of_date = min(universe_max_per_leg) + 1 day`` and asserts the
controlled-error envelope returns with the documented prefix
("no scoreable strip:").

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
    FuturesButterflySimpleInput,
)
from rates_agent.policy_futures.tools.futures_butterfly_simple import (  # noqa: E402
    calculate_futures_butterfly_simple,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (curve_family, wing_short, body, wing_long)
Case = Tuple[str, int, int, int]


DEFAULT_CASE_COUNT = 8
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"


POLICY_FUTURES_CURVE_FAMILIES: List[str] = [
    "SOFR_FUT",
    "EUR_SHORT_RATE_FUT",
    "SONIA_FUT",
]


# Regression cases — cover all three families AND adjacent (1, 2, 4)
# + wider (1, 4, 8) triples so the SQL baseline exercises both
# RFR + IBOR regimes.
REGRESSION_CASES: List[Case] = [
    ("SOFR_FUT", 1, 2, 4),
    ("SOFR_FUT", 1, 4, 8),
    ("SONIA_FUT", 1, 2, 4),
    ("EUR_SHORT_RATE_FUT", 1, 2, 4),
]


TOLERANCE_BY_FIELD = {
    # butterfly_value_round_decimals = 4
    "butterfly_value_pct": 1e-3,
    "daily_change_butterfly_value_pct": 1e-3,
    "high_252d_butterfly_value_pct": 1e-3,
    "low_252d_butterfly_value_pct": 1e-3,
    "mid_252d_butterfly_value_pct": 1e-3,
    # implied_rate_round_decimals = 4
    "implied_rate_pct_wing_short": 1e-3,
    "implied_rate_pct_body": 1e-3,
    "implied_rate_pct_wing_long": 1e-3,
    # z_score_round_decimals = 4
    "z_score_butterfly": 6e-3,
    # percentile_round_decimals = 1
    "percentile_252d": 0.11,
}


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (curve_family, wing_short, body, wing_long) triples from
    the live universe where ALL THREE strip positions have at least
    252 trading days of PX_LAST in the lookback buffer."""
    query = text(
        """
        WITH strips AS (
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
        )
        SELECT
            a.curve_family,
            a.strip_position AS wing_short,
            b.strip_position AS body,
            c.strip_position AS wing_long
        FROM strips a
        JOIN strips b
          ON a.curve_family = b.curve_family
         AND a.strip_position < b.strip_position
        JOIN strips c
          ON a.curve_family = c.curve_family
         AND b.strip_position < c.strip_position
        ORDER BY a.curve_family, a.strip_position,
                 b.strip_position, c.strip_position
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
    pool = [
        (
            row["curve_family"],
            int(row["wing_short"]),
            int(row["body"]),
            int(row["wing_long"]),
        )
        for row in rows
    ]
    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def _read_inverse_pricing_triple(
    engine,
    *,
    curve_family: str,
    wing_short: int,
    body: int,
    wing_long: int,
) -> Dict[str, Any]:
    """Read the per-leg ``inverse_pricing`` flag + ``contract_code``
    stems independently (PR8 cross-check)."""
    flag_sql = text(
        """
        SELECT
            (i.attributes->>'strip_position')::int AS strip_position,
            (i.attributes->>'inverse_pricing')::boolean AS inverse_pricing,
            i.contract_code
        FROM macro_data.instrument_master i
        WHERE i.curve_family   = :curve_family
          AND (i.attributes->>'strip_position')::int IN (
              :wing_short, :body, :wing_long
          )
          AND i.is_rolling_contract = TRUE
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            flag_sql,
            {
                "curve_family": curve_family,
                "wing_short": wing_short,
                "body": body,
                "wing_long": wing_long,
            },
        ).mappings().all()
    by_pos = {int(r["strip_position"]): dict(r) for r in rows}
    for pos in (wing_short, body, wing_long):
        if pos not in by_pos:
            return {
                "error": (
                    "SQL baseline: missing instrument_master row "
                    f"for strip_position={pos}."
                )
            }
    flags = {by_pos[p]["inverse_pricing"] for p in (wing_short, body, wing_long)}
    if len(flags) != 1:
        return {
            "error": (
                "SQL baseline: legs disagree on inverse_pricing — "
                "tool should return the controlled-error envelope."
            )
        }
    inverse_priced = bool(by_pos[wing_short]["inverse_pricing"])
    return {
        "inverse_priced": inverse_priced,
        "contract_code_wing_short": by_pos[wing_short]["contract_code"],
        "contract_code_body": by_pos[body]["contract_code"],
        "contract_code_wing_long": by_pos[wing_long]["contract_code"],
    }


def sql_baseline(
    engine,
    *,
    curve_family: str,
    wing_short: int,
    body: int,
    wing_long: int,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Reproduce the Python primitive's butterfly snapshot using
    window functions over the three strip slots' PX_LAST history."""
    trip = _read_inverse_pricing_triple(
        engine,
        curve_family=curve_family,
        wing_short=wing_short, body=body, wing_long=wing_long,
    )
    if "error" in trip:
        return trip
    inverse_priced = trip["inverse_priced"]
    contract_code_wing_short = trip["contract_code_wing_short"]
    contract_code_body = trip["contract_code_body"]
    contract_code_wing_long = trip["contract_code_wing_long"]

    if inverse_priced:
        rate_short_expr = "(100.0 - price_wing_short)"
        rate_body_expr = "(100.0 - price_body)"
        rate_long_expr = "(100.0 - price_wing_long)"
    else:
        rate_short_expr = "price_wing_short"
        rate_body_expr = "price_body"
        rate_long_expr = "price_wing_long"

    baseline_sql = text(
        f"""
        WITH raw AS (
            SELECT
                trade_date,
                (attributes->>'strip_position')::int AS strip_position,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE curve_family = :curve_family
              AND (attributes->>'strip_position')::int IN (
                  :wing_short, :body, :wing_long
              )
              AND field_name   = :field_name
              AND trade_date  >= CURRENT_DATE - ((:lookback_days + 378) * INTERVAL '1 day')
              AND field_value IS NOT NULL
        ),
        deduped AS (
            SELECT DISTINCT ON (trade_date, strip_position)
                trade_date, strip_position, field_value
            FROM raw
            ORDER BY trade_date, strip_position, field_value
        ),
        wide AS (
            SELECT
                trade_date,
                MAX(field_value) FILTER (
                    WHERE strip_position = :wing_short
                ) AS price_wing_short,
                MAX(field_value) FILTER (
                    WHERE strip_position = :body
                ) AS price_body,
                MAX(field_value) FILTER (
                    WHERE strip_position = :wing_long
                ) AS price_wing_long
            FROM deduped
            GROUP BY trade_date
        ),
        aligned AS (
            SELECT trade_date, price_wing_short, price_body, price_wing_long
            FROM wide
            WHERE price_wing_short IS NOT NULL
              AND price_body IS NOT NULL
              AND price_wing_long IS NOT NULL
            ORDER BY trade_date
        ),
        rated AS (
            SELECT
                trade_date,
                price_wing_short, price_body, price_wing_long,
                {rate_short_expr} AS rate_wing_short,
                {rate_body_expr} AS rate_body,
                {rate_long_expr} AS rate_wing_long,
                (
                    {rate_body_expr}
                    - 0.5 * (
                        {rate_short_expr} + {rate_long_expr}
                    )
                ) AS butterfly_value_pct,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM aligned
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                rate_wing_short,
                rate_body,
                rate_wing_long,
                butterfly_value_pct,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(butterfly_value_pct) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(butterfly_value_pct) OVER zw <> 0
                    THEN ROUND(
                        (
                            (
                                butterfly_value_pct
                                - AVG(butterfly_value_pct) OVER zw
                            )
                            / NULLIF(
                                STDDEV_SAMP(butterfly_value_pct) OVER zw,
                                0
                            )
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score_butterfly,
                ROUND(
                    (MAX(butterfly_value_pct) OVER tw)::numeric, 4
                )::double precision AS high_252d_butterfly_value_pct,
                ROUND(
                    (MIN(butterfly_value_pct) OVER tw)::numeric, 4
                )::double precision AS low_252d_butterfly_value_pct,
                CASE
                    WHEN MAX(butterfly_value_pct) OVER tw IS NULL
                      OR MIN(butterfly_value_pct) OVER tw IS NULL
                      OR MAX(butterfly_value_pct) OVER tw
                         = MIN(butterfly_value_pct) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                butterfly_value_pct
                                - MIN(butterfly_value_pct) OVER tw
                            )
                            / NULLIF(
                                MAX(butterfly_value_pct) OVER tw
                                - MIN(butterfly_value_pct) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d,
                CASE
                    WHEN LAG(butterfly_value_pct, 1)
                         OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (
                            butterfly_value_pct
                            - LAG(butterfly_value_pct, 1)
                              OVER (ORDER BY rn)
                        )::numeric,
                        4
                    )::double precision
                END AS daily_change_butterfly_value_pct
            FROM rated
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
            FROM aligned
            WHERE trade_date >= (
                SELECT MAX(trade_date) - (:lookback_days * INTERVAL '1 day')
                FROM aligned
            )
        )
        SELECT
            TO_CHAR(latest.trade_date, 'YYYY-MM-DD') AS as_of_date,
            ROUND(latest.rate_wing_short::numeric, 4)::double precision
                AS implied_rate_pct_wing_short,
            ROUND(latest.rate_body::numeric, 4)::double precision
                AS implied_rate_pct_body,
            ROUND(latest.rate_wing_long::numeric, 4)::double precision
                AS implied_rate_pct_wing_long,
            ROUND(latest.butterfly_value_pct::numeric, 4)::double precision
                AS butterfly_value_pct,
            latest.daily_change_butterfly_value_pct,
            latest.z_score_butterfly,
            latest.high_252d_butterfly_value_pct,
            latest.low_252d_butterfly_value_pct,
            ROUND(
                ((
                    latest.high_252d_butterfly_value_pct
                    + latest.low_252d_butterfly_value_pct
                ) / 2.0)::numeric,
                4
            )::double precision AS mid_252d_butterfly_value_pct,
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
                "wing_short": wing_short,
                "body": body,
                "wing_long": wing_long,
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
            "strip_position_wing_short": wing_short,
            "strip_position_body": body,
            "strip_position_wing_long": wing_long,
            "contract_code_wing_short": contract_code_wing_short,
            "contract_code_body": contract_code_body,
            "contract_code_wing_long": contract_code_wing_long,
            "inverse_priced": inverse_priced,
            "implied_rate_pct_wing_short": row["implied_rate_pct_wing_short"],
            "implied_rate_pct_body": row["implied_rate_pct_body"],
            "implied_rate_pct_wing_long": row["implied_rate_pct_wing_long"],
            "butterfly_value_pct": row["butterfly_value_pct"],
            "daily_change_butterfly_value_pct": (
                row["daily_change_butterfly_value_pct"]
            ),
            "z_score_butterfly": row["z_score_butterfly"],
            "high_252d_butterfly_value_pct": (
                row["high_252d_butterfly_value_pct"]
            ),
            "low_252d_butterfly_value_pct": (
                row["low_252d_butterfly_value_pct"]
            ),
            "mid_252d_butterfly_value_pct": (
                row["mid_252d_butterfly_value_pct"]
            ),
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
            "as_of_date", "curve_family",
            "strip_position_wing_short", "strip_position_body",
            "strip_position_wing_long",
            "contract_code_wing_short", "contract_code_body",
            "contract_code_wing_long",
            "inverse_priced", "observation_count",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "implied_rate_pct_wing_short",
            "implied_rate_pct_body",
            "implied_rate_pct_wing_long",
            "butterfly_value_pct",
            "daily_change_butterfly_value_pct",
            "z_score_butterfly",
            "high_252d_butterfly_value_pct",
            "low_252d_butterfly_value_pct",
            "mid_252d_butterfly_value_pct",
            "percentile_252d",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    return mismatches


def run_case(
    engine, *, case: Case, lookback_days: int, field_name: str,
) -> List[str]:
    curve_family, wing_short, body, wing_long = case
    tool_result = calculate_futures_butterfly_simple(
        engine=engine,
        params=FuturesButterflySimpleInput(
            curve_family=curve_family,
            strip_position_wing_short=wing_short,
            strip_position_body=body,
            strip_position_wing_long=wing_long,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        wing_short=wing_short, body=body, wing_long=wing_long,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    mismatches = compare_results(tool_result, sql_result)

    # ------------------------------------------------------------------
    # Future-anchor guard cross-check
    # ------------------------------------------------------------------
    try:
        maxes = {
            pos: sql_universe_max_date(
                engine=engine,
                curve_family=curve_family,
                strip_position=pos,
                field_name=field_name,
            )
            for pos in (wing_short, body, wing_long)
        }
    except RuntimeError as exc:
        mismatches.append(f"Future-anchor probe failed: {exc}")
        return mismatches
    binding_max = min(maxes.values())
    binding_leg_position = min(maxes.items(), key=lambda kv: kv[1])[0]
    future_anchor = binding_max + timedelta(days=1)
    rows_past = sql_count_rows_past_anchor(
        engine=engine,
        curve_family=curve_family,
        strip_position=binding_leg_position,
        field_name=field_name,
        anchor=future_anchor,
    )
    if rows_past != 0:
        mismatches.append(
            f"Future-anchor SQL probe found {rows_past} row(s) past "
            f"{future_anchor.isoformat()} on the binding leg — the "
            "universe_max probe is out of sync with the row data."
        )

    guard_result = calculate_futures_butterfly_simple(
        engine=engine,
        params=FuturesButterflySimpleInput(
            curve_family=curve_family,
            strip_position_wing_short=wing_short,
            strip_position_body=body,
            strip_position_wing_long=wing_long,
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
            "Validate the policy-futures simple-butterfly monitor "
            "against direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("POLICY-FUTURES SIMPLE-BUTTERFLY MONITOR — SQL VALIDATION")
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
        cases,
        lambda case: (
            f"{case[0]} ({case[1]}, {case[2]}, {case[3]})"
        ),
    )

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases), f"{case[0]} ({case[1]}, {case[2]}, {case[3]})",
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
            print(f"  - {case[0]} ({case[1]}, {case[2]}, {case[3]}) "
                  f"({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
