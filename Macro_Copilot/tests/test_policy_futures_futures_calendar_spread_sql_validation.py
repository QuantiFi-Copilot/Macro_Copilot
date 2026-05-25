#!/usr/bin/env python3
"""
test_policy_futures_futures_calendar_spread_sql_validation.py — policy-
                                                                 futures
                                                                 calendar-
                                                                 spread
                                                                 validator

Independently reproduces the policy-futures calendar-spread
computation in SQL (raw_price_spread, spread_implied_rate_pct, 1-day
deltas on each axis, rolling 252-day z-score on the IMPLIED-RATE
SPREAD series, trailing 252-day high / low / mid / percentile on the
implied-rate spread axis, observation_count) and asserts the Python
tool result matches the SQL result row-for-row across a representative
set of (curve_family, strip_position_short, strip_position_long)
cases.

Independence
------------
The SQL baseline filters via ``v_market_data_daily_enriched`` with
the same ``(curve_family, (attributes->>'strip_position')::int,
field_name)`` predicate ``fetch_strip_group`` uses, INNER-joins the
two legs' series on trade_date (the equivalent of the Python tool's
ffill + pivot + dropna(subset=both legs) alignment step), and
independently reads the per-leg ``inverse_pricing`` flag from
``instrument_master.attributes`` to drive the implied-rate
conversion. The z-score / range / percentile / 1-day deltas are
computed in SQL via WINDOW functions; no Python primitive
involvement.

Future-anchor guard cross-check
-------------------------------
The runner also calls the Python tool with
``as_of_date = min(universe_max_per_leg) + 1 day`` and asserts the
controlled-error envelope returns with the documented prefix
("no scoreable strip:"); a separate SQL probe counts rows past the
requested anchor on each leg to verify the guard had a real reason
to fire.

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
    FuturesCalendarSpreadInput,
)
from rates_agent.policy_futures.tools.futures_calendar_spread import (  # noqa: E402
    calculate_futures_calendar_spread,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (curve_family, strip_position_short, strip_position_long)
Case = Tuple[str, int, int]


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
# AND adjacent (1, 2) + non-adjacent (1, 4) leg pairs so the SQL
# baseline exercises both the short-tenor and wider-tenor spread
# shapes.
REGRESSION_CASES: List[Case] = [
    ("SOFR_FUT", 1, 2),
    ("SOFR_FUT", 1, 4),
    ("SONIA_FUT", 1, 2),
    ("EUR_SHORT_RATE_FUT", 1, 2),
]


TOLERANCE_BY_FIELD = {
    # Python tool rounds raw_price_round_decimals=5; SQL rounds to 5.
    "raw_price_spread": 1e-4,
    "daily_change_raw_price_spread": 1e-4,
    # implied_rate_round_decimals = 4
    "spread_implied_rate_pct": 1e-3,
    "daily_change_spread_implied_rate_pct": 1e-3,
    "high_252d_spread_implied_rate_pct": 1e-3,
    "low_252d_spread_implied_rate_pct": 1e-3,
    "mid_252d_spread_implied_rate_pct": 1e-3,
    # z_score_round_decimals = 4
    "z_score_spread_implied_rate": 6e-3,
    # percentile_round_decimals = 1
    "percentile_252d": 0.11,
}


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (curve_family, strip_position_short, strip_position_long)
    triples from the live universe where BOTH strip positions have at
    least 252 trading days of PX_LAST in the lookback buffer — keeps
    the SQL z-score numerically meaningful."""
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
            a.strip_position AS strip_position_short,
            b.strip_position AS strip_position_long
        FROM strips a
        JOIN strips b
          ON a.curve_family = b.curve_family
         AND a.strip_position < b.strip_position
        ORDER BY a.curve_family,
                 a.strip_position,
                 b.strip_position
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
            int(row["strip_position_short"]),
            int(row["strip_position_long"]),
        )
        for row in rows
    ]
    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def _read_inverse_pricing_pair(
    engine,
    *,
    curve_family: str,
    strip_position_short: int,
    strip_position_long: int,
) -> Dict[str, Any]:
    """Read the per-leg ``inverse_pricing`` flag + ``contract_code``
    stems independently (PR8 cross-check). Both legs of a same-curve
    calendar spread should share the flag in the V1 universe; the
    Python tool refuses with the controlled-error envelope otherwise."""
    flag_sql = text(
        """
        SELECT
            (i.attributes->>'strip_position')::int AS strip_position,
            (i.attributes->>'inverse_pricing')::boolean AS inverse_pricing,
            i.contract_code
        FROM macro_data.instrument_master i
        WHERE i.curve_family   = :curve_family
          AND (i.attributes->>'strip_position')::int IN (
              :strip_position_short, :strip_position_long
          )
          AND i.is_rolling_contract = TRUE
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            flag_sql,
            {
                "curve_family": curve_family,
                "strip_position_short": strip_position_short,
                "strip_position_long": strip_position_long,
            },
        ).mappings().all()
    by_pos = {int(r["strip_position"]): dict(r) for r in rows}
    if (
        strip_position_short not in by_pos
        or strip_position_long not in by_pos
    ):
        return {"error": "SQL baseline: missing instrument_master row."}
    return {
        "inverse_priced_short": bool(by_pos[strip_position_short]["inverse_pricing"]),
        "inverse_priced_long": bool(by_pos[strip_position_long]["inverse_pricing"]),
        "contract_code_short": by_pos[strip_position_short]["contract_code"],
        "contract_code_long": by_pos[strip_position_long]["contract_code"],
    }


def sql_baseline(
    engine,
    *,
    curve_family: str,
    strip_position_short: int,
    strip_position_long: int,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Reproduce the Python primitive's calendar-spread snapshot using
    window functions over the two strip slots' PX_LAST history. Reads
    the per-strip ``inverse_pricing`` flag from
    ``instrument_master.attributes`` independently so the implied-rate
    conversion is driven off metadata, just like the Python primitive."""
    pair = _read_inverse_pricing_pair(
        engine,
        curve_family=curve_family,
        strip_position_short=strip_position_short,
        strip_position_long=strip_position_long,
    )
    if "error" in pair:
        return pair
    if pair["inverse_priced_short"] != pair["inverse_priced_long"]:
        return {
            "error": (
                "SQL baseline: legs disagree on inverse_pricing — "
                "tool should return the controlled-error envelope."
            )
        }
    inverse_priced = pair["inverse_priced_short"]
    contract_code_short = pair["contract_code_short"]
    contract_code_long = pair["contract_code_long"]

    # ------------------------------------------------------------------
    # Window-function baseline on the aligned (intersection) series.
    # ------------------------------------------------------------------
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
                  :strip_position_short, :strip_position_long
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
                    WHERE strip_position = :strip_position_short
                ) AS price_short,
                MAX(field_value) FILTER (
                    WHERE strip_position = :strip_position_long
                ) AS price_long
            FROM deduped
            GROUP BY trade_date
        ),
        aligned AS (
            SELECT trade_date, price_short, price_long
            FROM wide
            WHERE price_short IS NOT NULL AND price_long IS NOT NULL
            ORDER BY trade_date
        ),
        spreads AS (
            SELECT
                trade_date,
                price_short,
                price_long,
                (price_short - price_long) AS raw_price_spread,
                CASE WHEN {('TRUE' if inverse_priced else 'FALSE')}
                     THEN (100.0 - price_short) - (100.0 - price_long)
                     ELSE price_short - price_long
                END AS spread_implied_rate_pct,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM aligned
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                raw_price_spread,
                spread_implied_rate_pct,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(spread_implied_rate_pct) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(spread_implied_rate_pct) OVER zw <> 0
                    THEN ROUND(
                        (
                            (
                                spread_implied_rate_pct
                                - AVG(spread_implied_rate_pct) OVER zw
                            )
                            / NULLIF(
                                STDDEV_SAMP(spread_implied_rate_pct) OVER zw,
                                0
                            )
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score_spread_implied_rate,
                ROUND(
                    (MAX(spread_implied_rate_pct) OVER tw)::numeric, 4
                )::double precision AS high_252d_spread_implied_rate_pct,
                ROUND(
                    (MIN(spread_implied_rate_pct) OVER tw)::numeric, 4
                )::double precision AS low_252d_spread_implied_rate_pct,
                CASE
                    WHEN MAX(spread_implied_rate_pct) OVER tw IS NULL
                      OR MIN(spread_implied_rate_pct) OVER tw IS NULL
                      OR MAX(spread_implied_rate_pct) OVER tw
                         = MIN(spread_implied_rate_pct) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                spread_implied_rate_pct
                                - MIN(spread_implied_rate_pct) OVER tw
                            )
                            / NULLIF(
                                MAX(spread_implied_rate_pct) OVER tw
                                - MIN(spread_implied_rate_pct) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d,
                CASE
                    WHEN LAG(raw_price_spread, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (
                            raw_price_spread
                            - LAG(raw_price_spread, 1) OVER (ORDER BY rn)
                        )::numeric,
                        5
                    )::double precision
                END AS daily_change_raw_price_spread,
                CASE
                    WHEN LAG(spread_implied_rate_pct, 1)
                         OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (
                            spread_implied_rate_pct
                            - LAG(spread_implied_rate_pct, 1)
                              OVER (ORDER BY rn)
                        )::numeric,
                        4
                    )::double precision
                END AS daily_change_spread_implied_rate_pct
            FROM spreads
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
            ROUND(latest.raw_price_spread::numeric, 5)::double precision AS raw_price_spread,
            ROUND(latest.spread_implied_rate_pct::numeric, 4)::double precision
                AS spread_implied_rate_pct,
            latest.daily_change_raw_price_spread,
            latest.daily_change_spread_implied_rate_pct,
            latest.z_score_spread_implied_rate,
            latest.high_252d_spread_implied_rate_pct,
            latest.low_252d_spread_implied_rate_pct,
            ROUND(
                ((
                    latest.high_252d_spread_implied_rate_pct
                    + latest.low_252d_spread_implied_rate_pct
                ) / 2.0)::numeric,
                4
            )::double precision AS mid_252d_spread_implied_rate_pct,
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
                "strip_position_short": strip_position_short,
                "strip_position_long": strip_position_long,
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
            "strip_position_short": strip_position_short,
            "strip_position_long": strip_position_long,
            "contract_code_short": contract_code_short,
            "contract_code_long": contract_code_long,
            "inverse_priced": inverse_priced,
            "raw_price_spread": row["raw_price_spread"],
            "spread_implied_rate_pct": row["spread_implied_rate_pct"],
            "daily_change_raw_price_spread": (
                row["daily_change_raw_price_spread"]
            ),
            "daily_change_spread_implied_rate_pct": (
                row["daily_change_spread_implied_rate_pct"]
            ),
            "z_score_spread_implied_rate": row["z_score_spread_implied_rate"],
            "high_252d_spread_implied_rate_pct": (
                row["high_252d_spread_implied_rate_pct"]
            ),
            "low_252d_spread_implied_rate_pct": (
                row["low_252d_spread_implied_rate_pct"]
            ),
            "mid_252d_spread_implied_rate_pct": (
                row["mid_252d_spread_implied_rate_pct"]
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
    """SQL-only probe for the strip's MAX(trade_date) — independent of
    the Python tool's ``fetch_strip_position_max_date`` helper."""
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
    used to assert the future-anchor guard had a real reason to fire."""
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
            "strip_position_short", "strip_position_long",
            "contract_code_short", "contract_code_long",
            "inverse_priced", "observation_count",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "raw_price_spread",
            "spread_implied_rate_pct",
            "daily_change_raw_price_spread",
            "daily_change_spread_implied_rate_pct",
            "z_score_spread_implied_rate",
            "high_252d_spread_implied_rate_pct",
            "low_252d_spread_implied_rate_pct",
            "mid_252d_spread_implied_rate_pct",
            "percentile_252d",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    return mismatches


def run_case(
    engine, *, case: Case, lookback_days: int, field_name: str,
) -> List[str]:
    curve_family, strip_position_short, strip_position_long = case
    tool_result = calculate_futures_calendar_spread(
        engine=engine,
        params=FuturesCalendarSpreadInput(
            curve_family=curve_family,
            strip_position_short=strip_position_short,
            strip_position_long=strip_position_long,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        strip_position_short=strip_position_short,
        strip_position_long=strip_position_long,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    mismatches = compare_results(tool_result, sql_result)

    # ------------------------------------------------------------------
    # Future-anchor guard cross-check — call the tool with
    # as_of_date = min(universe_max_per_leg) + 1 day and assert the
    # controlled-error envelope. Independently SQL-probe rows past
    # the requested anchor on the binding leg (must be 0).
    # ------------------------------------------------------------------
    try:
        max_short = sql_universe_max_date(
            engine=engine,
            curve_family=curve_family,
            strip_position=strip_position_short,
            field_name=field_name,
        )
        max_long = sql_universe_max_date(
            engine=engine,
            curve_family=curve_family,
            strip_position=strip_position_long,
            field_name=field_name,
        )
    except RuntimeError as exc:
        mismatches.append(f"Future-anchor probe failed: {exc}")
        return mismatches
    binding_max = min(max_short, max_long)
    future_anchor = binding_max + timedelta(days=1)
    binding_leg_position = (
        strip_position_short if max_short <= max_long else strip_position_long
    )
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

    guard_result = calculate_futures_calendar_spread(
        engine=engine,
        params=FuturesCalendarSpreadInput(
            curve_family=curve_family,
            strip_position_short=strip_position_short,
            strip_position_long=strip_position_long,
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
            "Validate the policy-futures calendar-spread monitor "
            "against direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("POLICY-FUTURES CALENDAR-SPREAD MONITOR — SQL VALIDATION")
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
            f"{case[0]} ({case[1]}, {case[2]})"
        ),
    )

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases), f"{case[0]} ({case[1]}, {case[2]})",
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
            print(f"  - {case[0]} ({case[1]}, {case[2]}) "
                  f"({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
