#!/usr/bin/env python3
"""
test_policy_futures_futures_pack_average_simple_sql_validation.py —
policy-futures pack-average validator.

Independently reproduces the policy-futures pack-average
computation in SQL (pack_average_implied_rate_pct, 1-day delta,
rolling 252-day z-score, trailing 252-day high / low / mid /
percentile, observation_count) and asserts the Python tool result
matches the SQL result row-for-row across a representative set of
(curve_family, pack) cases.

Independence
------------
The SQL baseline filters via ``v_market_data_daily_enriched`` with
the same ``(curve_family, (attributes->>'strip_position')::int,
field_name)`` predicate ``fetch_strip_group`` uses, INNER-joins the
four pack legs' series on trade_date, and independently reads the
per-leg ``inverse_pricing`` flag from
``instrument_master.attributes`` to drive the implied-rate
conversion. The pack average is computed as the SIMPLE ARITHMETIC
MEAN of the four per-leg implied rates. The z-score / range /
percentile / 1-day delta are computed in SQL via WINDOW functions
(LAG + STDDEV_SAMP + DISTINCT ON as the strip_snapshot validator
does).

EUR_SHORT_RATE_FUT refusal cross-check (Layer B)
------------------------------------------------
The runner also calls the Python tool with curve_family =
EUR_SHORT_RATE_FUT against the live DB stack and asserts that the
ADR 0013 V1 refusal returns a ``{"error": "..."}`` envelope
mentioning ``delivery_month_type`` + ``ADR 0013``. The MCP layer's
NotImplementedError catch is exercised through the package's
calculate_futures_pack_average_simple call — the runner wraps the
raise into the same envelope shape the wrapper does, so the
assertion is honest against the live stack (not just synthetic).

Future-anchor guard cross-check
-------------------------------
The runner calls the Python tool with
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
    FuturesPackAverageSimpleInput,
)
from rates_agent.policy_futures.tools.futures_pack_average_simple import (  # noqa: E402
    calculate_futures_pack_average_simple,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
)


# (curve_family, pack)
Case = Tuple[str, str]


DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"


PACK_POSITIONS = {
    "whites": (1, 2, 3, 4),
    "reds": (5, 6, 7, 8),
}


# The two clean-built families per ADR 0013 V1. EUR_SHORT_RATE_FUT
# is exercised separately via the refusal probe.
CLEAN_CURVE_FAMILIES: List[str] = [
    "SOFR_FUT",
    "SONIA_FUT",
]


REGRESSION_CASES: List[Case] = [
    ("SOFR_FUT", "whites"),
    ("SOFR_FUT", "reds"),
    ("SONIA_FUT", "whites"),
    ("SONIA_FUT", "reds"),
]


TOLERANCE_BY_FIELD = {
    # pack_average_round_decimals = 4
    "pack_average_implied_rate_pct": 1e-3,
    "daily_change_pack_average_implied_rate_pct": 1e-3,
    "high_252d_pack_average_implied_rate_pct": 1e-3,
    "low_252d_pack_average_implied_rate_pct": 1e-3,
    "mid_252d_pack_average_implied_rate_pct": 1e-3,
    # z_score_round_decimals = 4
    "z_score_pack_average": 6e-3,
    # percentile_round_decimals = 1
    "percentile_252d": 0.11,
}


def _read_pack_inverse_flag(
    engine,
    *,
    curve_family: str,
    positions: Tuple[int, ...],
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
          AND (i.attributes->>'strip_position')::int = ANY(:positions)
          AND i.is_rolling_contract = TRUE
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            flag_sql,
            {
                "curve_family": curve_family,
                "positions": list(positions),
            },
        ).mappings().all()
    by_pos = {int(r["strip_position"]): dict(r) for r in rows}
    for pos in positions:
        if pos not in by_pos:
            return {
                "error": (
                    "SQL baseline: missing instrument_master row "
                    f"for strip_position={pos}."
                )
            }
    flags = {by_pos[p]["inverse_pricing"] for p in positions}
    if len(flags) != 1:
        return {
            "error": (
                "SQL baseline: legs disagree on inverse_pricing — "
                "tool should return the controlled-error envelope."
            )
        }
    inverse_priced = bool(by_pos[positions[0]]["inverse_pricing"])
    return {
        "inverse_priced": inverse_priced,
        "contract_codes": [by_pos[p]["contract_code"] for p in positions],
    }


def sql_baseline(
    engine,
    *,
    curve_family: str,
    pack: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Reproduce the Python primitive's pack-average snapshot using
    window functions over the four pack legs' PX_LAST history."""
    positions = PACK_POSITIONS[pack]
    p1, p2, p3, p4 = positions
    flag = _read_pack_inverse_flag(
        engine, curve_family=curve_family, positions=positions,
    )
    if "error" in flag:
        return flag
    inverse_priced = flag["inverse_priced"]
    contract_codes = flag["contract_codes"]

    if inverse_priced:
        rate_expr_template = "(100.0 - {price})"
    else:
        rate_expr_template = "{price}"

    rate_p1 = rate_expr_template.format(price="price_p1")
    rate_p2 = rate_expr_template.format(price="price_p2")
    rate_p3 = rate_expr_template.format(price="price_p3")
    rate_p4 = rate_expr_template.format(price="price_p4")

    baseline_sql = text(
        f"""
        WITH raw AS (
            SELECT
                trade_date,
                (attributes->>'strip_position')::int AS strip_position,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE curve_family = :curve_family
              AND (attributes->>'strip_position')::int = ANY(:positions)
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
                MAX(field_value) FILTER (WHERE strip_position = :p1) AS price_p1,
                MAX(field_value) FILTER (WHERE strip_position = :p2) AS price_p2,
                MAX(field_value) FILTER (WHERE strip_position = :p3) AS price_p3,
                MAX(field_value) FILTER (WHERE strip_position = :p4) AS price_p4
            FROM deduped
            GROUP BY trade_date
        ),
        aligned AS (
            SELECT trade_date, price_p1, price_p2, price_p3, price_p4
            FROM wide
            WHERE price_p1 IS NOT NULL
              AND price_p2 IS NOT NULL
              AND price_p3 IS NOT NULL
              AND price_p4 IS NOT NULL
            ORDER BY trade_date
        ),
        rated AS (
            SELECT
                trade_date,
                {rate_p1} AS rate_p1,
                {rate_p2} AS rate_p2,
                {rate_p3} AS rate_p3,
                {rate_p4} AS rate_p4,
                (
                    {rate_p1} + {rate_p2}
                    + {rate_p3} + {rate_p4}
                ) / 4.0 AS pack_average_implied_rate_pct,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM aligned
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                rate_p1, rate_p2, rate_p3, rate_p4,
                pack_average_implied_rate_pct,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(pack_average_implied_rate_pct) OVER zw
                         IS NOT NULL
                     AND STDDEV_SAMP(pack_average_implied_rate_pct) OVER zw
                         <> 0
                    THEN ROUND(
                        (
                            (
                                pack_average_implied_rate_pct
                                - AVG(pack_average_implied_rate_pct) OVER zw
                            )
                            / NULLIF(
                                STDDEV_SAMP(pack_average_implied_rate_pct) OVER zw,
                                0
                            )
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score_pack_average,
                ROUND(
                    (MAX(pack_average_implied_rate_pct) OVER tw)::numeric, 4
                )::double precision AS high_252d_pack_average_implied_rate_pct,
                ROUND(
                    (MIN(pack_average_implied_rate_pct) OVER tw)::numeric, 4
                )::double precision AS low_252d_pack_average_implied_rate_pct,
                CASE
                    WHEN MAX(pack_average_implied_rate_pct) OVER tw IS NULL
                      OR MIN(pack_average_implied_rate_pct) OVER tw IS NULL
                      OR MAX(pack_average_implied_rate_pct) OVER tw
                         = MIN(pack_average_implied_rate_pct) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                pack_average_implied_rate_pct
                                - MIN(pack_average_implied_rate_pct) OVER tw
                            )
                            / NULLIF(
                                MAX(pack_average_implied_rate_pct) OVER tw
                                - MIN(pack_average_implied_rate_pct) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d,
                CASE
                    WHEN LAG(pack_average_implied_rate_pct, 1)
                         OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (
                            pack_average_implied_rate_pct
                            - LAG(pack_average_implied_rate_pct, 1)
                              OVER (ORDER BY rn)
                        )::numeric,
                        4
                    )::double precision
                END AS daily_change_pack_average_implied_rate_pct
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
            ROUND(latest.rate_p1::numeric, 4)::double precision AS rate_p1,
            ROUND(latest.rate_p2::numeric, 4)::double precision AS rate_p2,
            ROUND(latest.rate_p3::numeric, 4)::double precision AS rate_p3,
            ROUND(latest.rate_p4::numeric, 4)::double precision AS rate_p4,
            ROUND(latest.pack_average_implied_rate_pct::numeric, 4)::double precision
                AS pack_average_implied_rate_pct,
            latest.daily_change_pack_average_implied_rate_pct,
            latest.z_score_pack_average,
            latest.high_252d_pack_average_implied_rate_pct,
            latest.low_252d_pack_average_implied_rate_pct,
            ROUND(
                ((
                    latest.high_252d_pack_average_implied_rate_pct
                    + latest.low_252d_pack_average_implied_rate_pct
                ) / 2.0)::numeric,
                4
            )::double precision AS mid_252d_pack_average_implied_rate_pct,
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
                "positions": list(positions),
                "p1": p1, "p2": p2, "p3": p3, "p4": p4,
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
            "pack": pack,
            "strip_positions": list(positions),
            "contract_codes": contract_codes,
            "inverse_priced": inverse_priced,
            "implied_rates_pct": [
                row["rate_p1"], row["rate_p2"],
                row["rate_p3"], row["rate_p4"],
            ],
            "pack_average_implied_rate_pct": (
                row["pack_average_implied_rate_pct"]
            ),
            "daily_change_pack_average_implied_rate_pct": (
                row["daily_change_pack_average_implied_rate_pct"]
            ),
            "z_score_pack_average": row["z_score_pack_average"],
            "high_252d_pack_average_implied_rate_pct": (
                row["high_252d_pack_average_implied_rate_pct"]
            ),
            "low_252d_pack_average_implied_rate_pct": (
                row["low_252d_pack_average_implied_rate_pct"]
            ),
            "mid_252d_pack_average_implied_rate_pct": (
                row["mid_252d_pack_average_implied_rate_pct"]
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
            "as_of_date", "curve_family", "pack",
            "strip_positions",
            "contract_codes",
            "inverse_priced", "observation_count",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "pack_average_implied_rate_pct",
            "daily_change_pack_average_implied_rate_pct",
            "z_score_pack_average",
            "high_252d_pack_average_implied_rate_pct",
            "low_252d_pack_average_implied_rate_pct",
            "mid_252d_pack_average_implied_rate_pct",
            "percentile_252d",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    # Per-leg implied rates — list-to-list comparison with the same
    # tolerance as the pack average. The list lengths must match by
    # construction (4 legs each).
    tool_legs = tool_metrics.get("implied_rates_pct") or []
    sql_legs = sql_metrics.get("implied_rates_pct") or []
    if len(tool_legs) != len(sql_legs):
        mismatches.append(
            f"current_metrics.implied_rates_pct length: "
            f"tool={len(tool_legs)} sql={len(sql_legs)}"
        )
    else:
        for idx, (tool_val, sql_val) in enumerate(
            zip(tool_legs, sql_legs)
        ):
            if abs(float(tool_val) - float(sql_val)) > 1e-3:
                mismatches.append(
                    f"current_metrics.implied_rates_pct[{idx}]: "
                    f"tool={tool_val!r} sql={sql_val!r}"
                )
    return mismatches


def run_case(
    engine, *, case: Case, lookback_days: int, field_name: str,
) -> List[str]:
    curve_family, pack = case
    tool_result = calculate_futures_pack_average_simple(
        engine=engine,
        params=FuturesPackAverageSimpleInput(
            curve_family=curve_family,
            pack=pack,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        pack=pack,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    mismatches = compare_results(tool_result, sql_result)

    # ------------------------------------------------------------------
    # Future-anchor guard cross-check
    # ------------------------------------------------------------------
    positions = PACK_POSITIONS[pack]
    try:
        maxes = {
            pos: sql_universe_max_date(
                engine=engine,
                curve_family=curve_family,
                strip_position=pos,
                field_name=field_name,
            )
            for pos in positions
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

    guard_result = calculate_futures_pack_average_simple(
        engine=engine,
        params=FuturesPackAverageSimpleInput(
            curve_family=curve_family,
            pack=pack,
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


def run_eur_refusal_case(engine, *, lookback_days: int, field_name: str) -> List[str]:
    """Layer B refusal probe — confirms the ADR 0013 V1
    NotImplementedError fires against the live DB stack and that the
    error message names both ``delivery_month_type`` and ``ADR 0013``."""
    mismatches: List[str] = []
    params = FuturesPackAverageSimpleInput(
        curve_family="EUR_SHORT_RATE_FUT",
        pack="whites",
        lookback_days=lookback_days,
        field_name=field_name,
    )
    raised: Exception | None = None
    error_envelope: Dict[str, Any] | None = None
    try:
        result = calculate_futures_pack_average_simple(
            engine=engine, params=params,
        )
    except NotImplementedError as exc:
        raised = exc
    else:
        # If compute() returned an envelope instead of raising,
        # accept it as long as the message contains both required
        # citations (the MCP wrapper does this serialisation).
        if isinstance(result, dict) and "error" in result:
            error_envelope = result
        else:
            mismatches.append(
                "EUR_SHORT_RATE_FUT refusal probe — expected "
                "NotImplementedError OR controlled-error envelope; "
                f"got {result!r}"
            )
            return mismatches

    message = str(raised) if raised is not None else error_envelope["error"]
    if "delivery_month_type" not in message:
        mismatches.append(
            "EUR_SHORT_RATE_FUT refusal probe — message missing "
            f"``delivery_month_type``: {message!r}"
        )
    if "ADR 0013" not in message:
        mismatches.append(
            "EUR_SHORT_RATE_FUT refusal probe — message missing "
            f"``ADR 0013``: {message!r}"
        )
    if "EUR_SHORT_RATE_FUT" not in message:
        mismatches.append(
            "EUR_SHORT_RATE_FUT refusal probe — message missing "
            f"``EUR_SHORT_RATE_FUT``: {message!r}"
        )
    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the policy-futures pack-average monitor "
            "against direct SQL."
        ),
    )
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("POLICY-FUTURES PACK-AVERAGE MONITOR — SQL VALIDATION")
    print("=" * 80)
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running tool vs SQL comparisons (clean-built families)...")
    cases: List[Case] = REGRESSION_CASES
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases) + 1, f"{case[0]} pack={case[1]}",
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

    print_case_header(
        len(cases) + 1, len(cases) + 1,
        "EUR_SHORT_RATE_FUT refusal (ADR 0013 V1)",
    )
    refusal_mismatches = run_eur_refusal_case(
        engine,
        lookback_days=args.days,
        field_name=args.field,
    )
    if refusal_mismatches:
        print("FAIL")
        for mismatch in refusal_mismatches[:5]:
            print(f"  - {mismatch}")
        failed_cases.append((("EUR_SHORT_RATE_FUT", "whites"), refusal_mismatches))
    else:
        print("PASS")

    print("[3/3] Summary")
    print("-" * 80)
    total_cases = len(cases) + 1
    print(f"  total_cases : {total_cases}")
    print(f"  passed      : {total_cases - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")

    if failed_cases:
        print("\nFAILED CASES:")
        for case, mismatches in failed_cases:
            print(f"  - {case[0]} pack={case[1]} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
