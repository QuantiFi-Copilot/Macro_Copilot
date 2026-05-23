#!/usr/bin/env python3
"""
test_futures_strip_snapshot_sql_validation.py — policy-futures
                                                 whole-strip snapshot
                                                 validator

Independently reproduces the policy-futures whole-strip snapshot
computation in SQL (per-leg raw_price, implied_rate_pct, 1-day
delta, rolling 252-day z-score, latest open_interest,
observation_count) and asserts the Python tool result matches the
SQL result row-for-row across at least two policy-futures
curve_families.

Independence
------------
The SQL baseline filters via ``v_market_data_daily_enriched`` with
the same ``(curve_family, (attributes->>'strip_position')::int,
field_name)`` predicate ``fetch_strip_group`` uses, pivots prices
+ open-interest into wide format keyed by ``strip_position``,
aligns on the intersection of trading days (rows where ALL 8
configured positions have a value after dedup), independently reads
the per-strip ``inverse_pricing`` flag from
``instrument_master.attributes`` to drive the implied-rate
conversion, and reproduces the snapshot via window functions:
LAG-based 1-day delta, AVG / STDDEV_SAMP over a 252-row trailing
window for the z-score, MAX(trade_date) for the aligned anchor.

Future-anchor guard cross-check
-------------------------------
The runner also calls the Python tool with ``as_of_date =
universe_max + 1 day`` and asserts the controlled-error envelope
returns with the documented prefix ("no scoreable strip:").

DB-mutation-free
----------------
ZERO INSERT / UPDATE / DELETE / UPSERT / DDL — only SELECT against
``v_market_data_daily_enriched`` and ``instrument_master``.

This script is a standalone CLI runner (matching the existing
``test_*_sql_validation.py`` shape); it is EXCLUDED from pytest
collection via ``tests/conftest.py``'s ``collect_ignore`` list.
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
    FuturesStripSnapshotInput,
)
from rates_agent.policy_futures.tools.futures_strip_snapshot import (  # noqa: E402
    calculate_futures_strip_snapshot,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
)


DEFAULT_LOOKBACK_DAYS = 365  # only used to scope the SQL fetch buffer
DEFAULT_PRICE_FIELD = "PX_LAST"
DEFAULT_OI_FIELD = "OPEN_INT"
DEFAULT_STRIP_POSITIONS: Tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8)


# Two curve_families per the build prompt (both inverse-priced in V1
# per ADR 0011 — TIIE_FUT direct-priced is planned-extension territory).
# SOFR_FUT + EUR_SHORT_RATE_FUT span both RFR and IBOR regimes so the
# methodology disclosure check exercises both regime labels.
REGRESSION_CASES: List[str] = [
    "SOFR_FUT",
    "EUR_SHORT_RATE_FUT",
]


TOLERANCE_BY_FIELD = {
    # implied_rate_round_decimals = 4
    "implied_rate_pct": 1e-3,
    "daily_change_implied_rate_pct": 1e-3,
    # z_score_round_decimals = 4
    "z_score_implied_rate": 6e-3,
    # raw_price_round_decimals = 5
    "raw_price": 1e-4,
    # open_interest_round_decimals = 0
    "open_interest": 0.5,
}


def _read_per_leg_inverse_pricing(
    engine,
    *,
    curve_family: str,
    strip_positions: Tuple[int, ...],
) -> Dict[str, Any]:
    """Read the per-leg ``inverse_pricing`` flag + ``contract_code``
    stems independently (PR8 cross-check). Returns a dict keyed by
    strip_position OR an error envelope on mismatch."""
    sql = text(
        """
        SELECT
            (i.attributes->>'strip_position')::int AS strip_position,
            (i.attributes->>'inverse_pricing')::boolean AS inverse_pricing,
            i.contract_code
        FROM macro_data.instrument_master i
        WHERE i.curve_family   = :curve_family
          AND (i.attributes->>'strip_position')::int = ANY(:strip_positions)
          AND i.is_rolling_contract = TRUE
        ORDER BY (i.attributes->>'strip_position')::int
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "strip_positions": list(strip_positions),
            },
        ).mappings().all()
    by_pos = {int(r["strip_position"]): dict(r) for r in rows}
    for pos in strip_positions:
        if pos not in by_pos:
            return {
                "error": (
                    f"SQL baseline: missing instrument_master row "
                    f"for {curve_family} strip_position={pos}."
                )
            }
    flags = {by_pos[p]["inverse_pricing"] for p in strip_positions}
    if len(flags) != 1:
        return {
            "error": (
                f"SQL baseline: {curve_family} legs disagree on "
                "inverse_pricing — tool should return the controlled-"
                "error envelope."
            )
        }
    inverse_priced = bool(by_pos[strip_positions[0]]["inverse_pricing"])
    return {
        "inverse_priced": inverse_priced,
        "by_position": by_pos,
    }


def sql_baseline(
    engine,
    *,
    curve_family: str,
    strip_positions: Tuple[int, ...],
    lookback_days: int,
    price_field: str,
    oi_field: str,
) -> Dict[str, Any]:
    """Reproduce the Python primitive's strip snapshot using window
    functions over the per-strip PX_LAST + OPEN_INT histories."""
    trip = _read_per_leg_inverse_pricing(
        engine,
        curve_family=curve_family,
        strip_positions=strip_positions,
    )
    if "error" in trip:
        return trip
    inverse_priced: bool = trip["inverse_priced"]
    by_position = trip["by_position"]

    # Build the per-position columns for the wide CTE programmatically
    # (the strip-position count is 8 in V1; we keep the SQL static-ish
    # by templating the per-position price + OI MAX FILTER lines).
    price_cols = ",\n                ".join(
        f"MAX(price_value) FILTER (WHERE strip_position = {p}) AS price_{p}"
        for p in strip_positions
    )
    price_not_null = " AND ".join(
        f"price_{p} IS NOT NULL" for p in strip_positions
    )

    # Per-leg implied-rate expression — driven by inverse_pricing.
    if inverse_priced:
        implied_expr = "(100.0 - price_{p})"
    else:
        implied_expr = "price_{p}"

    # Build per-position rated columns + window-stat expressions.
    rated_cols_parts: List[str] = []
    for p in strip_positions:
        rated_cols_parts.append(
            f"price_{p}, "
            f"{implied_expr.format(p=p)} AS rate_{p}"
        )
    rated_cols = ",\n                ".join(rated_cols_parts)

    scored_cols_parts: List[str] = []
    for p in strip_positions:
        scored_cols_parts.append(
            f"""
            ROUND(rate_{p}::numeric, 4)::double precision AS implied_rate_pct_{p},
            ROUND(price_{p}::numeric, 5)::double precision AS raw_price_{p},
            CASE
                WHEN COUNT(*) OVER zw_{p} >= 60
                 AND STDDEV_SAMP(rate_{p}) OVER zw_{p} IS NOT NULL
                 AND STDDEV_SAMP(rate_{p}) OVER zw_{p} <> 0
                THEN ROUND(
                    ((rate_{p} - AVG(rate_{p}) OVER zw_{p})
                     / NULLIF(STDDEV_SAMP(rate_{p}) OVER zw_{p}, 0))::numeric,
                    4
                )::double precision
                ELSE NULL
            END AS z_score_implied_rate_{p},
            CASE
                WHEN LAG(rate_{p}, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                ELSE ROUND(
                    (rate_{p} - LAG(rate_{p}, 1) OVER (ORDER BY rn))::numeric, 4
                )::double precision
            END AS daily_change_implied_rate_pct_{p}
            """
        )
    scored_cols = ",\n            ".join(scored_cols_parts)

    window_specs = ",\n            ".join(
        f"zw_{p} AS (ORDER BY rn ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)"
        for p in strip_positions
    )

    # Pure SELECT — first get latest aligned trade_date + per-strip
    # rated payload (price / implied-rate / daily-change / z-score),
    # then a separate SELECT for the OI snapshot at that date.
    latest_sql = text(
        f"""
        WITH price_raw AS (
            SELECT
                trade_date,
                (attributes->>'strip_position')::int AS strip_position,
                field_value::double precision AS price_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE curve_family = :curve_family
              AND (attributes->>'strip_position')::int = ANY(:strip_positions)
              AND field_name   = :price_field
              AND trade_date  >= CURRENT_DATE - ((:lookback_days + 700) * INTERVAL '1 day')
              AND field_value IS NOT NULL
        ),
        price_dedup AS (
            SELECT DISTINCT ON (trade_date, strip_position)
                trade_date, strip_position, price_value
            FROM price_raw
            ORDER BY trade_date, strip_position, price_value
        ),
        wide_price AS (
            SELECT
                trade_date,
                {price_cols}
            FROM price_dedup
            GROUP BY trade_date
        ),
        aligned AS (
            SELECT *
            FROM wide_price
            WHERE {price_not_null}
        ),
        rated AS (
            SELECT
                trade_date,
                {rated_cols},
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM aligned
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                {scored_cols}
            FROM rated
            WINDOW
                {window_specs}
        )
        SELECT * FROM scored ORDER BY trade_date DESC LIMIT 1
        """
    )

    with engine.connect() as conn:
        row = conn.execute(
            latest_sql,
            {
                "curve_family": curve_family,
                "strip_positions": list(strip_positions),
                "price_field": price_field,
                "lookback_days": lookback_days,
            },
        ).mappings().first()
    if row is None:
        return {"error": "SQL baseline returned no aligned rows."}

    raw_trade_date = row["trade_date"]
    if isinstance(raw_trade_date, str):
        as_of_date = raw_trade_date
    else:
        as_of_date = raw_trade_date.strftime("%Y-%m-%d")
    # Aligned anchor date — use it to fetch OI at the LATEST trade_date
    # on or before the anchor per strip position. Mirrors the Python
    # tool's behaviour: when raw OI on the exact anchor date is missing
    # (e.g. STIR OI typically publishes T-1 vs T price), the snapshot
    # surfaces the most recent OI within the cleaning window. The
    # DISTINCT ON (strip_position) pattern is the canonical SQL
    # equivalent of "latest OI on or before X per group".
    oi_anchor_sql = text(
        """
        SELECT DISTINCT ON (strip_position)
            (attributes->>'strip_position')::int AS strip_position,
            field_value::double precision AS oi_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND (attributes->>'strip_position')::int = ANY(:strip_positions)
          AND field_name   = :oi_field
          AND trade_date  <= CAST(:anchor_date AS DATE)
        ORDER BY (attributes->>'strip_position')::int, trade_date DESC
        """
    )
    with engine.connect() as conn:
        oi_rows = conn.execute(
            oi_anchor_sql,
            {
                "curve_family": curve_family,
                "strip_positions": list(strip_positions),
                "oi_field": oi_field,
                "anchor_date": as_of_date,
            },
        ).mappings().all()
    oi_by_position = {int(r["strip_position"]): r["oi_value"] for r in oi_rows}

    # Build the per-position snapshot list.
    snapshot_rows: List[Dict[str, Any]] = []
    for p in strip_positions:
        raw_oi = oi_by_position.get(p)
        snapshot_rows.append({
            "strip_position": p,
            "contract_code": by_position[p]["contract_code"],
            "raw_price": row[f"raw_price_{p}"],
            "implied_rate_pct": row[f"implied_rate_pct_{p}"],
            "daily_change_implied_rate_pct": row[
                f"daily_change_implied_rate_pct_{p}"
            ],
            "z_score_implied_rate": row[f"z_score_implied_rate_{p}"],
            "open_interest": (
                None if raw_oi is None else round(float(raw_oi), 0)
            ),
        })

    return {
        "as_of_date": as_of_date,
        "curve_family": curve_family,
        "inverse_priced": inverse_priced,
        "snapshot": snapshot_rows,
    }


def sql_universe_max_date(
    engine,
    *,
    curve_family: str,
    strip_position: int,
    price_field: str,
) -> date:
    sql = text(
        """
        SELECT MAX(trade_date) AS max_trade_date
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND (attributes->>'strip_position')::int = :strip_position
          AND field_name   = :price_field
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
                "price_field": price_field,
            },
        ).first()
    if row is None or row[0] is None:
        raise RuntimeError(
            f"sql_universe_max_date: no rows for {curve_family} "
            f"strip_position={strip_position}, field={price_field}"
        )
    value = row[0]
    return value if isinstance(value, date) else date.fromisoformat(str(value))


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

    # Output-level checks
    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_result,
        sql_payload=sql_result,
        fields=("as_of_date", "curve_family", "inverse_priced"),
        prefix="output.",
    )

    tool_rows = tool_result["snapshot"]
    sql_rows = sql_result["snapshot"]

    if len(tool_rows) != len(sql_rows):
        mismatches.append(
            f"snapshot length: tool={len(tool_rows)} sql={len(sql_rows)}"
        )
        return mismatches

    for idx, (tr, sr) in enumerate(zip(tool_rows, sql_rows), start=1):
        prefix = f"snapshot[{idx}](strip_position={tr.get('strip_position')})."
        add_exact_field_mismatches(
            mismatches=mismatches,
            tool_payload=tr,
            sql_payload=sr,
            fields=("strip_position", "contract_code"),
            prefix=prefix,
        )
        add_numeric_field_mismatches(
            mismatches=mismatches,
            tool_payload=tr,
            sql_payload=sr,
            fields=(
                "raw_price",
                "implied_rate_pct",
                "daily_change_implied_rate_pct",
                "z_score_implied_rate",
                "open_interest",
            ),
            tolerances=TOLERANCE_BY_FIELD,
            prefix=prefix,
        )

    return mismatches


def run_case(
    engine,
    *,
    curve_family: str,
    lookback_days: int,
    price_field: str,
    oi_field: str,
) -> List[str]:
    tool_result = calculate_futures_strip_snapshot(
        engine=engine,
        params=FuturesStripSnapshotInput(
            curve_family=curve_family,
            last_price_field_name=price_field,
            open_interest_field_name=oi_field,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        strip_positions=DEFAULT_STRIP_POSITIONS,
        lookback_days=lookback_days,
        price_field=price_field,
        oi_field=oi_field,
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
                price_field=price_field,
            )
            for pos in DEFAULT_STRIP_POSITIONS
        }
    except RuntimeError as exc:
        mismatches.append(f"Future-anchor probe failed: {exc}")
        return mismatches

    binding_max = min(maxes.values())
    future_anchor = binding_max + timedelta(days=1)

    guard_result = calculate_futures_strip_snapshot(
        engine=engine,
        params=FuturesStripSnapshotInput(
            curve_family=curve_family,
            last_price_field_name=price_field,
            open_interest_field_name=oi_field,
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
            "Validate the policy-futures whole-strip snapshot monitor "
            "against direct SQL."
        ),
    )
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--price-field", default=DEFAULT_PRICE_FIELD)
    parser.add_argument("--oi-field", default=DEFAULT_OI_FIELD)
    args = parser.parse_args()

    print("=" * 80)
    print("POLICY-FUTURES WHOLE-STRIP SNAPSHOT MONITOR — SQL VALIDATION")
    print("=" * 80)
    print(f"  curve_families : {REGRESSION_CASES}")
    print(f"  strip_positions: {list(DEFAULT_STRIP_POSITIONS)}")
    print(f"  lookback_days  : {args.days}")
    print(f"  price_field    : {args.price_field}")
    print(f"  oi_field       : {args.oi_field}")
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[str, List[str]]] = []
    for index, curve_family in enumerate(REGRESSION_CASES, start=1):
        print_case_header(index, len(REGRESSION_CASES), curve_family)
        mismatches = run_case(
            engine,
            curve_family=curve_family,
            lookback_days=args.days,
            price_field=args.price_field,
            oi_field=args.oi_field,
        )
        if mismatches:
            print("FAIL")
            for mismatch in mismatches[:15]:
                print(f"  - {mismatch}")
            if len(mismatches) > 15:
                print(f"  - ... plus {len(mismatches) - 15} more mismatches")
            failed_cases.append((curve_family, mismatches))
        else:
            print("PASS")

    print("[3/3] Summary")
    print("-" * 80)
    print(f"  total_cases : {len(REGRESSION_CASES)}")
    print(f"  passed      : {len(REGRESSION_CASES) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")

    if failed_cases:
        print("\nFAILED CASES:")
        for cf, mismatches in failed_cases:
            print(f"  - {cf} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
