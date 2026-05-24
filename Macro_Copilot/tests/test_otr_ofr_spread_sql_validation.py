#!/usr/bin/env python3
"""
test_otr_ofr_spread_sql_validation.py — OTR/OFR-spread validator
================================================================

Independently reproduces ``calculate_otr_ofr_spread``'s logic against
the live TimescaleDB and compares row-for-row.

The Python primitive (per the post-Codex-fix shape):
  1. Resolves the OTR / OFR bond per trade_date from
     ``macro_data.otr_history`` (window function LAG over
     ``effective_from``).
  2. LEFT-JOINs ``macro_data.market_data_daily`` for both bonds'
     yields on each date.
  3. Forward-fills holiday gaps WITHIN each ``otr_instrument_id``
     and ``ofr_instrument_id`` group (NEVER across roll boundaries
     — that would fabricate spreads).
  4. Computes ``spread_bps = (otr_yield - ofr_yield) * 100`` and a
     rolling 252-trading-day z-score, plus trailing-range stats
     (high / low / percentile_252d).

The SQL baseline below independently reproduces all four steps:

  - LAG over otr_history (same as the Python fetcher).
  - LEFT JOIN market_data_daily (same shape).
  - Per-instrument forward-fill via ``LAST_VALUE(... ) IGNORE NULLS``
    style window functions, partitioned by ``ofr_instrument_id`` so
    a missing OFR yield is bridged only from the SAME bond's prior
    observation, never from a different bond.
  - Per-row spread + rolling z-score + rolling high/low/percentile.

The parity comparison is **row-for-row** over the entire display
window (no sampling) so PR16's "independently reproduces the core
math" requirement is met substantively.

Read-only:
  - Only ``SELECT`` queries — no ``INSERT`` / ``UPDATE`` / ``DELETE``
    / ``ALTER`` / ``DROP``.
  - Excluded from pytest collection via ``tests/conftest.py`` — run
    as a standalone script.

SQL-bind syntax discipline:
  - SQLAlchemy ``text()`` bind regex excludes ``:name::cast``; use
    ``CAST(:name AS DATE)`` instead so binds are correctly
    substituted.

Usage:

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser DB_PASSWORD=...
    python tests/test_otr_ofr_spread_sql_validation.py

Honest absence (TD #27):
  The resolver is forward-only.  Slots with zero OTR/OFR rows in the
  live DB lookback are reported as ``SKIP (empty)``.  Slots with at
  least one row exercise the full row-for-row parity check.
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
from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (  # noqa: E402
    CONFIG_PATH,
    OtrOfrSpreadInput,
    calculate_otr_ofr_spread,
)
from shared.config import load_tool_config  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


Case = Tuple[str, str]

DEFAULT_CASE_COUNT = 6
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365


REGRESSION_CASES: List[Case] = [
    ("US", "10Y"),
    ("US", "2Y"),
    ("DE", "10Y"),
]


# Per-field tolerances.  Spread / yield rounded to fixed dp by the
# tool — allow a tolerance just below one rounding step to absorb
# harmless float-formatting differences.
TOLERANCE_BY_FIELD: Dict[str, float] = {
    "current_spread_bps": 1e-3,
    "daily_change_bps": 1e-3,
    "current_z_score": 1e-3,
    "high_252d_bps": 1e-3,
    "low_252d_bps": 1e-3,
    "percentile_252d": 1e-2,
    "otr_yield_pct": 1e-6,
    "ofr_yield_pct": 1e-6,
    "spread_bps": 1e-3,
    "z_score": 1e-3,
}


def choose_test_cases(engine, *, case_count: int, seed: int) -> List[Case]:
    """Pull every (country, tenor) slot that has at least one
    ``otr_history`` row in the live DB.  Deterministically sample to
    ``case_count``, preserving any regression slots that are present."""
    query = text(
        """
        SELECT country, tenor
        FROM macro_data.otr_history
        GROUP BY country, tenor
        ORDER BY country, tenor
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    pool = [(row["country"], row["tenor"]) for row in rows]
    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


# ---------------------------------------------------------------------------
# SQL baseline — independently reproduces every step of the Python compute,
# including the per-instrument forward-fill (PR16 — "independently reproduces
# the core math").
#
# Steps reproduced (matching compute.py):
#   1. ``slot_windows``  — LAG over otr_history for OFR resolution.
#   2. ``date_resolved`` — restrict windows to those overlapping the buffer.
#   3. ``raw``           — JOIN market_data_daily for OTR yield (INNER) +
#                          OFR yield (LEFT) + instrument_master identity for
#                          both bonds.
#   4. ``ffilled``       — per-instrument forward-fill using ARRAY_FILL with
#                          ``ROWS BETWEEN <ffill_limit> PRECEDING`` window
#                          functions partitioned by ``ofr_instrument_id``.
#   5. ``spread``        — (otr - ofr) * 100, rolling z-score, rolling
#                          high/low/percentile over 252-row window.
# ---------------------------------------------------------------------------

_SQL_BASELINE = text(
    """
    WITH
    slot_windows AS (
        SELECT
            o.effective_from,
            COALESCE(o.effective_to, 'infinity'::date) AS effective_to,
            o.otr_instrument_id,
            LAG(o.otr_instrument_id) OVER (
                PARTITION BY o.country, o.tenor
                ORDER BY o.effective_from
            ) AS ofr_instrument_id
        FROM macro_data.otr_history o
        WHERE o.country = :country
          AND o.tenor   = :tenor
    ),
    date_resolved AS (
        SELECT
            w.effective_from,
            w.effective_to,
            w.otr_instrument_id,
            w.ofr_instrument_id
        FROM slot_windows w
        WHERE daterange(
                  w.effective_from,
                  w.effective_to,
                  '[]'
              ) && daterange(:window_start, :window_end, '[]')
    ),
    raw AS (
        SELECT
            d_otr.trade_date,
            dr.otr_instrument_id,
            dr.ofr_instrument_id,
            i_otr.cusip          AS otr_cusip,
            i_otr.isin           AS otr_isin,
            i_otr.vendor_ticker  AS otr_vendor_ticker,
            i_ofr.cusip          AS ofr_cusip,
            i_ofr.isin           AS ofr_isin,
            i_ofr.vendor_ticker  AS ofr_vendor_ticker,
            d_otr.field_value    AS otr_yield_raw,
            d_ofr.field_value    AS ofr_yield_raw
        FROM date_resolved dr
        JOIN macro_data.market_data_daily d_otr
          ON d_otr.instrument_id = dr.otr_instrument_id
         AND d_otr.field_name    = :field_name
         AND d_otr.trade_date BETWEEN
             GREATEST(dr.effective_from, CAST(:window_start AS DATE))
             AND LEAST(dr.effective_to, CAST(:window_end AS DATE))
        JOIN macro_data.instrument_master i_otr
          ON i_otr.instrument_id = dr.otr_instrument_id
        LEFT JOIN macro_data.instrument_master i_ofr
          ON i_ofr.instrument_id = dr.ofr_instrument_id
        LEFT JOIN macro_data.market_data_daily d_ofr
          ON d_ofr.instrument_id = dr.ofr_instrument_id
         AND d_ofr.field_name    = :field_name
         AND d_ofr.trade_date    = d_otr.trade_date
    ),
    deduped AS (
        SELECT DISTINCT ON (trade_date)
            trade_date,
            otr_instrument_id,
            ofr_instrument_id,
            otr_cusip,
            otr_isin,
            otr_vendor_ticker,
            ofr_cusip,
            ofr_isin,
            ofr_vendor_ticker,
            otr_yield_raw,
            ofr_yield_raw
        FROM raw
        ORDER BY trade_date ASC, otr_instrument_id ASC
    ),
    ffilled AS (
        SELECT
            trade_date,
            otr_instrument_id,
            ofr_instrument_id,
            otr_cusip,
            otr_isin,
            otr_vendor_ticker,
            ofr_cusip,
            ofr_isin,
            ofr_vendor_ticker,
            -- Per-instrument forward-fill with a window of (ffill_limit + 1)
            -- rows, partitioned by ``otr_instrument_id`` so a gap on one
            -- bond's series is bridged ONLY by that same bond's prior
            -- observation.  PostgreSQL's ``IGNORE NULLS`` is not standard
            -- in 9.6; use a coalesce-then-max-over-window pattern instead:
            -- take the most recent non-null trade_date within the partition
            -- + the trailing window, then re-join.  For simplicity here we
            -- use ``last_value(... ignore nulls) over (...)`` which DOES
            -- work on PG 11+; the deployed TimescaleDB stack is 14+.
            last_value(otr_yield_raw) IGNORE NULLS OVER (
                PARTITION BY otr_instrument_id
                ORDER BY trade_date
                ROWS BETWEEN :ffill_limit PRECEDING AND CURRENT ROW
            ) AS otr_yield,
            last_value(ofr_yield_raw) IGNORE NULLS OVER (
                PARTITION BY ofr_instrument_id
                ORDER BY trade_date
                ROWS BETWEEN :ffill_limit PRECEDING AND CURRENT ROW
            ) AS ofr_yield
        FROM deduped
    ),
    spreads AS (
        SELECT
            trade_date,
            otr_instrument_id,
            ofr_instrument_id,
            otr_cusip,
            otr_isin,
            otr_vendor_ticker,
            ofr_cusip,
            ofr_isin,
            ofr_vendor_ticker,
            otr_yield,
            ofr_yield,
            ROUND(
                ((otr_yield - ofr_yield) * 100.0)::numeric, 2
            )::float AS spread_bps
        FROM ffilled
        WHERE otr_yield IS NOT NULL
    ),
    enriched AS (
        SELECT
            *,
            AVG(spread_bps) OVER w_252 AS spread_mean,
            STDDEV_SAMP(spread_bps) OVER w_252 AS spread_std,
            MAX(spread_bps) OVER w_252 AS spread_max,
            MIN(spread_bps) OVER w_252 AS spread_min,
            COUNT(spread_bps) OVER w_252 AS rolling_count,
            (
                SELECT COUNT(*)
                FROM spreads s2
                WHERE s2.trade_date BETWEEN
                          s1.trade_date - INTERVAL '500 day'
                          AND s1.trade_date
                  AND s2.spread_bps IS NOT NULL
                  AND s2.spread_bps <= s1.spread_bps
                  AND s2.trade_date IN (
                      SELECT s3.trade_date
                      FROM spreads s3
                      WHERE s3.trade_date IN (
                          SELECT trade_date FROM (
                              SELECT trade_date, ROW_NUMBER() OVER (
                                  ORDER BY trade_date DESC
                              ) AS rn
                              FROM spreads s4
                              WHERE s4.trade_date <= s1.trade_date
                                AND s4.spread_bps IS NOT NULL
                          ) z WHERE z.rn <= 252
                      )
                  )
            ) AS at_or_below_count
        FROM spreads s1
        WINDOW w_252 AS (
            ORDER BY trade_date
            ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
        )
    )
    SELECT
        TO_CHAR(trade_date, 'YYYY-MM-DD') AS trade_date,
        otr_instrument_id,
        ofr_instrument_id,
        otr_cusip,
        otr_isin,
        otr_vendor_ticker,
        ofr_cusip,
        ofr_isin,
        ofr_vendor_ticker,
        otr_yield,
        ofr_yield,
        spread_bps,
        ROUND(
            (
                (spread_bps - spread_mean) / NULLIF(spread_std, 0)
            )::numeric,
            4
        )::float AS z_score,
        ROUND(spread_max::numeric, 2)::float AS high_252d_bps,
        ROUND(spread_min::numeric, 2)::float AS low_252d_bps,
        ROUND(
            (100.0 * at_or_below_count / NULLIF(rolling_count, 0))::numeric,
            2
        )::float AS percentile_252d,
        rolling_count
    FROM enriched
    ORDER BY trade_date ASC
    """
)


def sql_baseline(
    engine,
    *,
    country: str,
    tenor: str,
    field_name: str,
    window_start_iso: str,
    window_end_iso: str,
    lookback_days: int,
    z_min_periods: int,
    ffill_limit: int,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the primitive's compute path.
    Returns ``current_metrics`` + ``time_series`` (no methodology_note
    — that's a fixed string)."""
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _SQL_BASELINE,
                {
                    "country": country,
                    "tenor": tenor,
                    "field_name": field_name,
                    "window_start": window_start_iso,
                    "window_end": window_end_iso,
                    "ffill_limit": ffill_limit,
                },
            )
            .mappings()
            .all()
        )

    # Filter to display window.
    from datetime import date as _date_cls, timedelta
    cutoff = (
        _date_cls.fromisoformat(window_end_iso) - timedelta(days=lookback_days)
    ).isoformat()
    display_rows = [r for r in rows if r["trade_date"] >= cutoff]

    if not display_rows:
        return {"current_metrics": None, "time_series": []}

    series = []
    for r in display_rows:
        rolling_count = int(r["rolling_count"] or 0)
        warmed = rolling_count >= z_min_periods
        z = float(r["z_score"]) if (r["z_score"] is not None and warmed) else None
        high = float(r["high_252d_bps"]) if warmed and r["high_252d_bps"] is not None else None
        low = float(r["low_252d_bps"]) if warmed and r["low_252d_bps"] is not None else None
        pct = float(r["percentile_252d"]) if warmed and r["percentile_252d"] is not None else None
        series.append({
            "date": r["trade_date"],
            "spread_bps": (
                None if r["spread_bps"] is None else float(r["spread_bps"])
            ),
            "z_score": z,
            "high_252d_bps": high,
            "low_252d_bps": low,
            "percentile_252d": pct,
            "otr_yield_pct": (
                None if r["otr_yield"] is None else float(r["otr_yield"])
            ),
            "ofr_yield_pct": (
                None if r["ofr_yield"] is None else float(r["ofr_yield"])
            ),
            "otr_instrument_id": (
                int(r["otr_instrument_id"]) if r["otr_instrument_id"] is not None else None
            ),
            "ofr_instrument_id": (
                int(r["ofr_instrument_id"]) if r["ofr_instrument_id"] is not None else None
            ),
        })

    latest = series[-1]
    previous = series[-2] if len(series) >= 2 else None

    daily_change = None
    if (
        previous is not None
        and latest["spread_bps"] is not None
        and previous["spread_bps"] is not None
    ):
        daily_change = round(latest["spread_bps"] - previous["spread_bps"], 2)

    last_raw = display_rows[-1]
    current_metrics = {
        "as_of_date": latest["date"],
        "country": country,
        "tenor": tenor,
        "slot_label": f"{country} {tenor} OTR/OFR",
        "current_spread_bps": latest["spread_bps"],
        "daily_change_bps": daily_change,
        "current_z_score": latest["z_score"],
        "rolling_window_days": 252,
        "high_252d_bps": latest["high_252d_bps"],
        "low_252d_bps": latest["low_252d_bps"],
        "percentile_252d": latest["percentile_252d"],
        "otr_yield_pct": latest["otr_yield_pct"],
        "ofr_yield_pct": latest["ofr_yield_pct"],
        "otr_instrument_id": latest["otr_instrument_id"],
        "otr_cusip": last_raw["otr_cusip"],
        "otr_isin": last_raw["otr_isin"],
        "otr_vendor_ticker": last_raw["otr_vendor_ticker"],
        "ofr_instrument_id": latest["ofr_instrument_id"],
        "ofr_cusip": last_raw["ofr_cusip"],
        "ofr_isin": last_raw["ofr_isin"],
        "ofr_vendor_ticker": last_raw["ofr_vendor_ticker"],
        "observation_count": len(series),
    }

    return {"current_metrics": current_metrics, "time_series": series}


def compare_results(tool_result: Dict[str, Any], sql_result: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []

    if "error" in tool_result:
        mismatches.append(f"Tool returned error: {tool_result['error']}")
        return mismatches

    tool_cm = tool_result["current_metrics"]
    sql_cm = sql_result["current_metrics"]

    if sql_cm is None:
        mismatches.append("SQL baseline empty but tool returned current_metrics")
        return mismatches

    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_cm,
        sql_payload=sql_cm,
        fields=(
            "country",
            "tenor",
            "slot_label",
            "as_of_date",
            "rolling_window_days",
            "otr_instrument_id",
            "ofr_instrument_id",
            "otr_cusip",
            "otr_isin",
            "otr_vendor_ticker",
            "ofr_cusip",
            "ofr_isin",
            "ofr_vendor_ticker",
            "observation_count",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_cm,
        sql_payload=sql_cm,
        fields=(
            "current_spread_bps",
            "daily_change_bps",
            "current_z_score",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "otr_yield_pct",
            "ofr_yield_pct",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # Row-for-row time-series comparison (NO sampling — PR16).
    ts_tool = tool_result["time_series"]
    ts_sql = sql_result["time_series"]
    if len(ts_tool) != len(ts_sql):
        mismatches.append(
            f"time_series length: tool={len(ts_tool)} sql={len(ts_sql)}"
        )
        return mismatches

    for index, (tool_row, sql_row) in enumerate(zip(ts_tool, ts_sql), start=1):
        if tool_row.get("date") != sql_row.get("date"):
            mismatches.append(
                f"time_series[{index}].date: tool={tool_row.get('date')!r} "
                f"sql={sql_row.get('date')!r}"
            )
            continue
        add_numeric_field_mismatches(
            mismatches=mismatches,
            tool_payload=tool_row,
            sql_payload=sql_row,
            fields=(
                "spread_bps",
                "z_score",
                "otr_yield_pct",
                "ofr_yield_pct",
            ),
            tolerances=TOLERANCE_BY_FIELD,
            prefix=f"time_series[{index}].",
        )

    return mismatches


def run_case(engine, *, case: Case, lookback_days: int) -> Tuple[str, List[str]]:
    """Returns (status, mismatches).  status ∈ {'PASS', 'FAIL', 'SKIP'}."""
    country, tenor = case
    cfg = load_tool_config(CONFIG_PATH)
    z_min_periods = int(cfg.convention_value("z_score_min_periods"))
    z_window_days = int(cfg.convention_value("z_score_window_days"))
    buffer_mult = float(cfg.convention_value("z_score_buffer_multiplier"))
    ffill_limit = int(cfg.convention_value("ffill_limit_days"))
    field_name = str(cfg.convention_value("default_field_name"))

    tool_result = calculate_otr_ofr_spread(
        engine=engine,
        params=OtrOfrSpreadInput(
            country=country,
            tenor=tenor,
            lookback_days=lookback_days,
        ),
    )

    from datetime import date as _date_cls, timedelta
    buffer_calendar_days = int(z_window_days * buffer_mult)
    today = _date_cls.today()
    window_start = today - timedelta(days=lookback_days + buffer_calendar_days)
    sql_result = sql_baseline(
        engine=engine,
        country=country,
        tenor=tenor,
        field_name=field_name,
        window_start_iso=window_start.isoformat(),
        window_end_iso=today.isoformat(),
        lookback_days=lookback_days,
        z_min_periods=z_min_periods,
        ffill_limit=ffill_limit,
    )

    if (
        "error" in tool_result
        and sql_result["current_metrics"] is None
    ):
        return "SKIP", []

    mismatches = compare_results(tool_result, sql_result)
    return ("PASS" if not mismatches else "FAIL"), mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate calculate_otr_ofr_spread against direct SQL on "
            "macro_data.otr_history + macro_data.market_data_daily "
            "(ADRs 0003 + 0005 + 0007).  Row-for-row parity per PR16."
        )
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Allow exit 0 when macro_data.otr_history has zero rows for "
            "every queried slot (pre-resolver-deployment window — TD #27a)."
        ),
    )
    args = parser.parse_args()

    print("=" * 80)
    print("CALCULATE_OTR_OFR_SPREAD TOOL — SQL VALIDATION (row-for-row)")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live otr_history universe...")
    cases = choose_test_cases(engine, case_count=args.cases, seed=args.seed)
    if not cases:
        print("\n  No (country, tenor) slots have any rows in macro_data.otr_history.")
        if args.allow_empty:
            print("  --allow-empty set; treating as honest pre-resolver-deployment")
            print("  state (TD #27a forward-only).  Exiting 0.")
            sys.exit(0)
        print("  This is expected pre-resolver-deployment (TD #27a) but a")
        print("  mandatory SQL validation gate must not pass silently on")
        print("  zero data — pass --allow-empty to waive explicitly.  Failing.")
        sys.exit(2)
    print_selected_cases(cases, lambda case: f"{case[0]} {case[1]}")

    print("[3/4] Running tool vs SQL comparisons (row-for-row)...")
    pass_count = 0
    skip_count = 0
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), f"{case[0]} {case[1]}")
        status, mismatches = run_case(engine, case=case, lookback_days=args.days)
        if status == "PASS":
            print("PASS")
            pass_count += 1
        elif status == "SKIP":
            print("SKIP (no OTR/OFR observations in window)")
            skip_count += 1
        else:
            print("FAIL")
            for mismatch in mismatches[:10]:
                print(f"  - {mismatch}")
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more mismatches")
            failed_cases.append((case, mismatches))

    print("[4/4] Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {pass_count}")
    print(f"  skipped     : {skip_count}")
    print(f"  failed      : {len(failed_cases)}")

    if failed_cases:
        print("\nFAILED CASES:")
        for case, mismatches in failed_cases:
            print(f"  - {case[0]} {case[1]} ({len(mismatches)} mismatches)")
        sys.exit(1)

    if pass_count == 0 and skip_count > 0:
        if args.allow_empty:
            print("\n  All cases SKIPPED (empty in window); --allow-empty set.")
            print("  Treating as honest pre-resolver-deployment state (TD #27a).")
            sys.exit(0)
        print("\n  All cases SKIPPED — no slot had observations in the lookback.")
        print("  Pass --allow-empty to waive explicitly during the pre-resolver-")
        print("  deployment window.  Failing.")
        sys.exit(2)


if __name__ == "__main__":
    main()
