#!/usr/bin/env python3
"""
test_scan_inflation_swaps_extremes_sql_validation.py — ZCIS
                                                       universe-scan
                                                       validator

Independently reproduces the ZCIS universe-extremes scan in SQL:
for each ``(curve_family, tenor)`` stem in the ZCIS universe,
compute the 252-day rolling z-score of the cleaned ZCIS quoted-
rate LEVEL series on the enriched view, pick the top-N stems by
|z|, and assert the Python tool's ranking matches the SQL ranking
row-for-row.

Notes on independence
---------------------
The SQL baseline reads
``macro_data.v_market_data_daily_enriched`` directly (same enriched
view ``fetch_scan_universe`` reads, but the SQL is its own
statement so the cross-check is genuinely independent of the
Python fetcher).

The Python tool uses ``clean_single_series`` (ffill 5 days; drop
duplicate dates; sort) before z-scoring; the SQL baseline
reproduces this with: ROW_NUMBER() OVER (PARTITION BY stem,
trade_date) for dedup, plus a forward-fill via LAST_VALUE-with-
IGNORE-NULLS over a rolling 5-day window... but PostgreSQL doesn't
have IGNORE NULLS on LAST_VALUE.  Workaround: since ZCIS daily
series are essentially gap-free on each curve_family's own
calendar (the playbook ingests one row per stem per trading day),
we DEDUP and let the rolling-z window run on the deduped series.
This matches the Python path on the live data; if a future ingest
introduces multi-day gaps within one stem's calendar, the SQL
baseline would need an explicit ffill CTE — flagged in the SQL
comment below.

Z-scores from SQL are rounded to 4 decimals (matches the Python
``z_score_round_decimals`` convention) before comparison.  Rank-
order ties are broken by curve_family asc then tenor asc (mirrors
the Python tie-break) — applied in Python on the SQL output so
both paths use the identical sort.

Future-anchor guard
-------------------
The Python tool returns the controlled-error envelope when
``as_of_date`` is BEYOND the universe's last observed trade_date.
The SQL validator parity-checks this honestly: ZERO rows must
exist between universe_max (exclusive) and the requested anchor
(inclusive), proving the guard's premise.

This script is a standalone CLI runner (matching the existing
``test_*_sql_validation.py`` shape); it MUST be EXCLUDED from
pytest collection via ``tests/conftest.py``'s ``collect_ignore``
list.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (  # noqa: E402
    ScanInflationSwapsExtremesInput,
    calculate_scan_inflation_swaps_extremes,
)
from tests.sql_validation_common import (  # noqa: E402
    floats_match,
    print_case_header,
    print_selected_cases,
)


# (top_n, min_abs_z_score, as_of_date, curve_families)
#
# The fetch window is derived from YAML conventions
# (z_score_window_days × z_score_buffer_multiplier +
# ffill_limit_days, currently 252 × 1.5 + 5 = 383 calendar days)
# anchored on the resolved as-of date.
Case = Tuple[int, float, Optional[date], Tuple[str, ...]]


DEFAULT_FIELD_NAME = "PX_MID"
INSTRUMENT_TYPE = "inflation_swap"

# ZCIS V1 universe — matches the YAML's
# ``inflation_swap_curve_families`` whitelist.
ZCIS_CURVE_FAMILIES: Tuple[str, ...] = (
    "USD_ZCIS", "EUR_ZCIS", "GBP_ZCIS",
)

# Pinned anchor date for the deterministic regression case —
# matches the parity fixture's anchor (2026-04-08) so the SQL
# baseline and the parity test share the same DB scope.
PINNED_ANCHOR_DATE = date(2026, 4, 8)

# Regression cases — guaranteed-coverage scan invocations.
REGRESSION_CASES: List[Case] = [
    # Full universe, low-floor display.
    (5, 0.0, PINNED_ANCHOR_DATE, ZCIS_CURVE_FAMILIES),
    # USD_ZCIS + EUR_ZCIS, default-style threshold.
    (3, 1.0, PINNED_ANCHOR_DATE, ("USD_ZCIS", "EUR_ZCIS")),
    # USD_ZCIS only, high top_n.
    (10, 0.0, PINNED_ANCHOR_DATE, ("USD_ZCIS",)),
    # Full universe, data-max anchor.
    (5, 0.0, None, ZCIS_CURVE_FAMILIES),
]


# Tolerances — z-score is the ranking key so we hold it tight; the
# snapshot fields are emitted by both paths from the same DB rows
# rounded with the same decimals, so 0.01 bps is the right floor
# for change snapshots (rounded to 2 dp upstream).
TOLERANCE_BY_FIELD = {
    "z_score_zcis_rate": 6e-3,
    "zcis_rate_pct": 1e-3,  # 4-decimal rounding; harmless float diff
    "daily_change_zcis_rate_bps": 0.01,
    "monthly_change_zcis_rate_bps": 0.01,
}


def sql_per_stem_z_scores(
    engine,
    *,
    curve_families: Tuple[str, ...],
    as_of_date: Optional[date],
    field_name: str,
) -> List[Dict[str, Any]]:
    """For each (curve_family, tenor) stem in the named curve
    families, compute the rolling 252d z-score of the ZCIS rate
    LEVEL series via SQL window functions, and return the per-stem
    LATEST row (max trade_date) carrying the snapshot values + the
    z-score.

    The fetch window is derived from YAML conventions
    (z_score_window_days × z_score_buffer_multiplier +
    ffill_limit_days, currently 252 × 1.5 + 5 = 383 calendar
    days).

    Returns one row per stem; the Python tool's ranking step is
    reproduced in Python below (sort by |z| desc with
    curve_family / tenor tie-break) on the SQL output.
    """
    FETCH_WINDOW_DAYS = 383

    sql = text(
        """
        WITH params AS (
            SELECT
                COALESCE(CAST(:as_of_date AS DATE), CURRENT_DATE)
                  AS anchor_date
        ),
        raw_rates AS (
            SELECT
                curve_family,
                tenor,
                trade_date,
                field_value::double precision AS zcis_rate
            FROM macro_data.v_market_data_daily_enriched
            CROSS JOIN params p
            WHERE instrument_type = :instrument_type
              AND field_name      = :field_name
              AND curve_family    = ANY(:curve_families)
              AND tenor          IS NOT NULL
              AND field_value    IS NOT NULL
              AND trade_date     >= p.anchor_date
                                     - (:fetch_window_days
                                        * INTERVAL '1 day')
              AND trade_date     <= p.anchor_date
        ),
        dedup AS (
            -- Mirrors clean_single_series's drop_duplicates(keep='last')
            -- inside Python.  The enriched view rarely emits dupes for
            -- ZCIS rows; this is defensive.
            SELECT DISTINCT ON (curve_family, tenor, trade_date)
                curve_family, tenor, trade_date, zcis_rate
            FROM raw_rates
            ORDER BY curve_family, tenor, trade_date, zcis_rate
        ),
        scored AS (
            SELECT
                curve_family,
                tenor,
                trade_date,
                zcis_rate,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(zcis_rate) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(zcis_rate) OVER zw <> 0
                    THEN ROUND(
                        ((zcis_rate - AVG(zcis_rate) OVER zw)
                         / NULLIF(STDDEV_SAMP(zcis_rate) OVER zw, 0))
                            ::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_zcis_rate,
                -- LAG-based change snapshots — multiply by 100 to
                -- convert from PERCENT to BPS, matching the Python
                -- compute layer.
                ROUND(
                    ((zcis_rate - LAG(zcis_rate, 1) OVER (
                        PARTITION BY curve_family, tenor
                        ORDER BY trade_date
                    )) * 100.0)::numeric, 2
                )::double precision AS daily_change_zcis_rate_bps,
                ROUND(
                    ((zcis_rate - LAG(zcis_rate, 21) OVER (
                        PARTITION BY curve_family, tenor
                        ORDER BY trade_date
                    )) * 100.0)::numeric, 2
                )::double precision AS monthly_change_zcis_rate_bps
            FROM dedup
            WINDOW zw AS (
                PARTITION BY curve_family, tenor
                ORDER BY trade_date
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
            )
        ),
        per_stem_latest AS (
            SELECT DISTINCT ON (curve_family, tenor)
                curve_family,
                tenor,
                TO_CHAR(trade_date, 'YYYY-MM-DD') AS as_of_date,
                ROUND(zcis_rate::numeric, 4)::double precision
                    AS zcis_rate_pct,
                daily_change_zcis_rate_bps,
                monthly_change_zcis_rate_bps,
                z_zcis_rate
            FROM scored
            ORDER BY curve_family, tenor, trade_date DESC
        )
        SELECT * FROM per_stem_latest
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "curve_families": list(curve_families),
                "instrument_type": INSTRUMENT_TYPE,
                "field_name": field_name,
                "fetch_window_days": FETCH_WINDOW_DAYS,
                "as_of_date": (
                    as_of_date.isoformat() if as_of_date else None
                ),
            },
        ).mappings().all()
    return [dict(r) for r in rows]


def sql_ranked_rows(
    per_stem_rows: List[Dict[str, Any]],
    *,
    top_n: int,
    min_abs_z: float,
) -> List[Dict[str, Any]]:
    """Reproduce the Python ranking step from the SQL per-stem rows.

    Returns the ranked list, top_n rows, sorted by |z| desc then
    curve_family asc then tenor asc (matches the Python tie-break).
    """
    candidates = []
    for row in per_stem_rows:
        z = row.get("z_zcis_rate")
        if z is None:
            continue
        if abs(z) < min_abs_z:
            continue
        candidates.append((z, row))
    candidates.sort(
        key=lambda zr: (-abs(zr[0]), zr[1]["curve_family"], zr[1]["tenor"]),
    )
    return [
        {**row, "z_score_zcis_rate": z, "rank": idx}
        for idx, (z, row) in enumerate(candidates[:top_n], start=1)
    ]


def compare_results(
    tool_result: Dict[str, Any],
    sql_ranked: List[Dict[str, Any]],
) -> List[str]:
    mismatches: List[str] = []
    if "error" in tool_result:
        mismatches.append(f"Tool returned error: {tool_result['error']}")
        return mismatches

    tool_rows = tool_result["results"]
    if len(tool_rows) != len(sql_ranked):
        mismatches.append(
            f"row count tool={len(tool_rows)} sql={len(sql_ranked)}"
        )

    for rank_idx, (t_row, s_row) in enumerate(
        zip(tool_rows, sql_ranked), start=1,
    ):
        stem = f"{t_row['curve_family']}/{t_row['tenor']}"
        if t_row["curve_family"] != s_row["curve_family"]:
            mismatches.append(
                f"rank {rank_idx}: "
                f"tool={t_row['curve_family']}/{t_row['tenor']} "
                f"sql={s_row['curve_family']}/{s_row['tenor']}"
            )
            continue
        if t_row["tenor"] != s_row["tenor"]:
            mismatches.append(
                f"rank {rank_idx} ({t_row['curve_family']}): "
                f"tool tenor={t_row['tenor']} "
                f"sql tenor={s_row['tenor']}"
            )
            continue
        for field in (
            "z_score_zcis_rate", "zcis_rate_pct",
            "daily_change_zcis_rate_bps",
            "monthly_change_zcis_rate_bps",
        ):
            sql_field = (
                "z_zcis_rate" if field == "z_score_zcis_rate"
                else field
            )
            t_v = t_row.get(field)
            s_v = s_row.get(sql_field)
            if not floats_match(t_v, s_v, field, TOLERANCE_BY_FIELD):
                mismatches.append(
                    f"rank {rank_idx} ({stem}) {field}: "
                    f"tool={t_v!r} sql={s_v!r}"
                )
        if t_row.get("as_of_date") != s_row.get("as_of_date"):
            mismatches.append(
                f"rank {rank_idx} ({stem}) as_of_date: "
                f"tool={t_row.get('as_of_date')!r} "
                f"sql={s_row.get('as_of_date')!r}"
            )
    return mismatches


def run_case(
    engine,
    *,
    case: Case,
    field_name: str,
) -> List[str]:
    top_n, min_abs_z, as_of, curve_families = case
    tool_result = calculate_scan_inflation_swaps_extremes(
        engine=engine,
        params=ScanInflationSwapsExtremesInput(
            curve_families=(
                list(curve_families)
                if curve_families != ZCIS_CURVE_FAMILIES
                else None
            ),
            top_n=top_n,
            min_abs_z_score=min_abs_z,
            as_of_date=as_of,
        ),
    )
    per_stem_rows = sql_per_stem_z_scores(
        engine,
        curve_families=curve_families,
        as_of_date=as_of,
        field_name=field_name,
    )
    if not per_stem_rows:
        return [
            f"SQL baseline returned 0 stems for curve_families="
            f"{curve_families} — DB coverage gap?"
        ]
    sql_ranked = sql_ranked_rows(
        per_stem_rows, top_n=top_n, min_abs_z=min_abs_z,
    )
    return compare_results(tool_result, sql_ranked)


# Future-anchor guard: when the LLM supplies an ``as_of_date``
# BEYOND the ZCIS universe's last observed ``trade_date``, the
# Python tool must return the documented controlled-error envelope.
# The SQL parity for this case is the "zero rows in the window
# between universe_max and the requested anchor" claim.
_FETCH_UNIVERSE_MAX_DATE_SQL = text("""
    SELECT MAX(trade_date) AS max_trade_date
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND field_name      = :field_name
      AND tenor          IS NOT NULL
      AND curve_family    = ANY(:curve_families)
""")


_FETCH_BEYOND_MAX_ROW_COUNT_SQL = text("""
    SELECT COUNT(*) AS row_count
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND field_name      = :field_name
      AND tenor          IS NOT NULL
      AND curve_family    = ANY(:curve_families)
      AND trade_date      > CAST(:universe_max AS DATE)
      AND trade_date     <= CAST(:future_anchor AS DATE)
""")


def run_future_anchor_guard_case(engine, field_name: str) -> List[str]:
    """Validate the future-anchor guard against the live DB.

    1. Pull the ZCIS universe's current MAX(trade_date) in SQL.
    2. Compute the Python tool with ``as_of_date = max + 1 day``.
    3. Assert the Python output is the controlled-error envelope.
    4. Mirror the assertion in SQL: zero rows exist strictly between
       universe_max (exclusive) and the requested anchor (inclusive).

    Read-only; pure SELECT.
    """
    mismatches: List[str] = []
    with engine.connect() as conn:
        max_row = conn.execute(
            _FETCH_UNIVERSE_MAX_DATE_SQL,
            {
                "curve_families": list(ZCIS_CURVE_FAMILIES),
                "instrument_type": INSTRUMENT_TYPE,
                "field_name": field_name,
            },
        ).first()
    if max_row is None or max_row[0] is None:
        return [
            "future-anchor guard: SQL returned NULL for universe "
            "MAX(trade_date) — ZCIS universe appears empty; cannot "
            "validate the guard against a live DB."
        ]
    universe_max: date = (
        max_row[0]
        if isinstance(max_row[0], date)
        else date.fromisoformat(str(max_row[0]))
    )
    future_anchor = universe_max + timedelta(days=1)

    tool_result = calculate_scan_inflation_swaps_extremes(
        engine=engine,
        params=ScanInflationSwapsExtremesInput(
            curve_families=None,
            top_n=5,
            min_abs_z_score=0.0,
            as_of_date=future_anchor,
        ),
    )

    if "error" not in tool_result:
        mismatches.append(
            f"future-anchor guard: Python tool returned a normal "
            f"scan for as_of_date={future_anchor.isoformat()} "
            f"(universe_max={universe_max.isoformat()}) — expected "
            "the controlled-error envelope. Keys present: "
            f"{sorted(tool_result.keys())}"
        )
        return mismatches
    if "results" in tool_result:
        mismatches.append(
            f"future-anchor guard: tool returned BOTH 'error' and "
            f"'results' for as_of_date={future_anchor.isoformat()} — "
            "envelope must be error-only."
        )
    if "scan_summary" in tool_result:
        mismatches.append(
            f"future-anchor guard: tool returned BOTH 'error' and "
            f"'scan_summary' for as_of_date="
            f"{future_anchor.isoformat()} — envelope must be "
            "error-only."
        )
    error_text = tool_result["error"]
    for substring in ("beyond", "last observed", future_anchor.isoformat()):
        if substring not in error_text:
            mismatches.append(
                f"future-anchor guard: error text missing required "
                f"substring {substring!r}; got: {error_text!r}"
            )

    # SQL parity: zero rows must exist between universe_max
    # (exclusive) and future_anchor (inclusive).
    with engine.connect() as conn:
        count_row = conn.execute(
            _FETCH_BEYOND_MAX_ROW_COUNT_SQL,
            {
                "curve_families": list(ZCIS_CURVE_FAMILIES),
                "instrument_type": INSTRUMENT_TYPE,
                "field_name": field_name,
                "universe_max": universe_max.isoformat(),
                "future_anchor": future_anchor.isoformat(),
            },
        ).first()
    sql_beyond_count = int(count_row[0]) if count_row is not None else -1
    if sql_beyond_count != 0:
        mismatches.append(
            f"future-anchor guard: SQL parity violated — "
            f"{sql_beyond_count} rows exist between universe_max="
            f"{universe_max.isoformat()} (excl) and future_anchor="
            f"{future_anchor.isoformat()} (incl). The guard's "
            "premise (no data past universe_max) is false; "
            "reconcile before claiming PASS."
        )
    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the ZCIS universe-extremes scan against "
            "direct SQL."
        ),
    )
    parser.add_argument(
        "--field-name", default=DEFAULT_FIELD_NAME,
    )
    args = parser.parse_args()

    print("=" * 80)
    print("ZCIS UNIVERSE-EXTREMES SCAN — SQL VALIDATION")
    print("=" * 80)
    print(f"  field_name       : {args.field_name}")
    print(f"  instrument_type  : {INSTRUMENT_TYPE}")
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    cases = REGRESSION_CASES

    def _case_label(c: Case) -> str:
        anchor = c[2].isoformat() if c[2] is not None else "data_max"
        return (
            f"top_n={c[0]} min_abs_z={c[1]} as_of={anchor} "
            f"curves={'(all)' if c[3] == ZCIS_CURVE_FAMILIES else list(c[3])}"
        )

    print_selected_cases(cases, _case_label)

    print("[2/3] Running tool vs SQL comparisons...")
    total_case_count = len(cases) + 1
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        label = _case_label(case)
        print_case_header(index, total_case_count, label)
        mismatches = run_case(engine, case=case, field_name=args.field_name)
        if mismatches:
            print("FAIL")
            for mismatch in mismatches[:10]:
                print(f"  - {mismatch}")
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more mismatches")
            failed_cases.append((case, mismatches))
        else:
            print("PASS")

    # Future-anchor guard case (P5 honest-disclosure SQL validation):
    # the controlled-error envelope must fire for as_of = universe_max
    # + 1 day, and the SQL parity (no rows exist past universe_max
    # within the +1d window) must hold.
    guard_label = "future-anchor guard (as_of = universe_max + 1 day)"
    print_case_header(total_case_count, total_case_count, guard_label)
    guard_mismatches = run_future_anchor_guard_case(engine, args.field_name)
    guard_failed: List[Tuple[str, List[str]]] = []
    if guard_mismatches:
        print("FAIL")
        for mismatch in guard_mismatches[:10]:
            print(f"  - {mismatch}")
        if len(guard_mismatches) > 10:
            print(
                f"  - ... plus {len(guard_mismatches) - 10} more mismatches"
            )
        guard_failed.append((guard_label, guard_mismatches))
    else:
        print("PASS")

    print("[3/3] Summary")
    print("-" * 80)
    total_failed = len(failed_cases) + len(guard_failed)
    print(f"  total_cases : {total_case_count}")
    print(f"  passed      : {total_case_count - total_failed}")
    print(f"  failed      : {total_failed}")

    if failed_cases or guard_failed:
        print("\nFAILED CASES:")
        for case, mismatches in failed_cases:
            label = _case_label(case)
            print(f"  - {label} ({len(mismatches)} mismatches)")
        for label, mismatches in guard_failed:
            print(f"  - {label} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
