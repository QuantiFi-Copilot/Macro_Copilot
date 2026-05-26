#!/usr/bin/env python3
"""
test_cpi_surprise_sql_validation.py — CPI-surprise validator
=============================================================

Independently reproduces ``calculate_cpi_surprise``'s logic against
the live TimescaleDB and compares row-for-row (no sampling — PR16).

The Python primitive:
  1. Resolves the ``event_type`` via the YAML
     ``cpi_event_type_for_<country>`` conventions
     (US/UK/JP → cpi_yoy; EU → hicp_yoy).
  2. Fetches event_calendar rows for (event_type, country) over a
     calendar buffer window via
     ``shared.analytics.events_fetch.fetch_economic_release_surprises``.
  3. Drops scheduled-placeholder rows (actual IS NULL).
  4. Computes per-row ``surprise = actual − consensus_median`` (the
     exact identity ADR 0008 §2 designates as the primitive layer's
     P12-disclosed computation).
  5. Applies a rolling z-score over the trailing ``release_z_window``
     realised releases with ``release_z_min_periods`` warmup gate.

The SQL baseline below reproduces every step using SQL window
functions over the realised-only subset.  Parity check is row-for-row
over the full display window.

Read-only:
  - Only ``SELECT`` queries.
  - Excluded from pytest collection via ``tests/conftest.py``.

SQL-bind syntax discipline:
  - Per the Codex review of PR #186, SQLAlchemy's ``text()`` bind
    regex excludes ``:name::cast``; this file uses
    ``CAST(:name AS DATE)`` instead.

Usage:

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser DB_PASSWORD=...
    python tests/test_cpi_surprise_sql_validation.py

Honest absence (ADR 0008 §6 / TD #28b):
  The event extractor's release-date coverage is bounded by
  Bloomberg's ECO_RELEASE_DT_LIST window (~1.5 years history +
  forward scheduled).  Slots with zero realised releases in the live
  DB lookback are reported as ``SKIP (empty)`` rather than failures.
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
from rates_agent.inflation_swaps.tools.cpi_surprise import (  # noqa: E402
    CONFIG_PATH,
    CpiSurpriseInput,
    calculate_cpi_surprise,
)
from shared.config import load_tool_config  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


Case = Tuple[str]  # one-element tuple of (country,)

DEFAULT_CASE_COUNT = 4
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_RELEASES = 24

REGRESSION_CASES: List[Case] = [
    ("US",),
    ("EU",),
    ("UK",),
    ("JP",),
]

TOLERANCE_BY_FIELD: Dict[str, float] = {
    "current_surprise_pct": 1e-3,
    "current_z_score": 1e-3,
    "current_actual_pct": 1e-6,
    "current_consensus_median_pct": 1e-6,
    "current_prior_pct": 1e-6,
    "surprise_pct": 1e-3,
    "z_score": 1e-3,
    "actual_pct": 1e-6,
    "consensus_median_pct": 1e-6,
}


def choose_test_cases(engine, *, case_count: int, seed: int) -> List[Case]:
    """Pull every (country,) slot with at least one realised CPI/HICP
    release in the live DB."""
    query = text(
        """
        SELECT DISTINCT country
        FROM macro_data.event_calendar
        WHERE event_type IN ('cpi_yoy', 'hicp_yoy')
          AND actual IS NOT NULL
        ORDER BY country
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    pool: List[Case] = [(row["country"],) for row in rows]
    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


# ---------------------------------------------------------------------------
# SQL baseline — reproduces every step of the Python compute.
# ---------------------------------------------------------------------------

_SQL_BASELINE = text(
    """
    WITH realised AS (
        SELECT
            release_date,
            period,
            actual,
            consensus_median,
            prior
        FROM macro_data.event_calendar
        WHERE event_type   = :event_type
          AND country      = :country
          AND release_date BETWEEN CAST(:window_start AS DATE)
                                AND CAST(:window_end AS DATE)
          AND actual IS NOT NULL
        ORDER BY release_date ASC
    ),
    surprises AS (
        SELECT
            release_date,
            period,
            actual::float AS actual,
            consensus_median::float AS consensus_median,
            prior::float AS prior,
            ROUND(
                (actual - consensus_median)::numeric, 4
            )::float AS surprise_pct
        FROM realised
    ),
    enriched AS (
        SELECT
            release_date,
            period,
            actual,
            consensus_median,
            prior,
            surprise_pct,
            AVG(surprise_pct) OVER w  AS rolling_mean,
            STDDEV_SAMP(surprise_pct) OVER w AS rolling_std,
            COUNT(surprise_pct) OVER w AS rolling_count
        FROM surprises
        WINDOW w AS (
            ORDER BY release_date
            ROWS BETWEEN :z_window_minus_1 PRECEDING AND CURRENT ROW
        )
    )
    SELECT
        TO_CHAR(release_date, 'YYYY-MM-DD') AS release_date,
        period,
        actual,
        consensus_median,
        prior,
        surprise_pct,
        ROUND(
            ((surprise_pct - rolling_mean) / NULLIF(rolling_std, 0))::numeric,
            4
        )::float AS z_score,
        rolling_count
    FROM enriched
    ORDER BY release_date ASC
    """
)


def sql_baseline(
    engine,
    *,
    event_type: str,
    country: str,
    window_start_iso: str,
    window_end_iso: str,
    lookback_releases: int,
    z_window: int,
    z_min_periods: int,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the primitive's compute path.
    Returns ``current_metrics`` + ``time_series`` (no methodology_note
    — that's a fixed string)."""
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _SQL_BASELINE,
                {
                    "event_type": event_type,
                    "country": country,
                    "window_start": window_start_iso,
                    "window_end": window_end_iso,
                    "z_window_minus_1": z_window - 1,
                },
            )
            .mappings()
            .all()
        )

    if not rows:
        return {"current_metrics": None, "time_series": []}

    # Tail to lookback_releases.
    display_rows = list(rows[-lookback_releases:])

    series = []
    for r in display_rows:
        rolling_count = int(r["rolling_count"] or 0)
        warmed = rolling_count >= z_min_periods
        z = float(r["z_score"]) if (r["z_score"] is not None and warmed) else None
        series.append({
            "date": r["release_date"],
            "period": r["period"],
            "surprise_pct": (
                None if r["surprise_pct"] is None else float(r["surprise_pct"])
            ),
            "z_score": z,
            "actual_pct": (
                None if r["actual"] is None else float(r["actual"])
            ),
            "consensus_median_pct": (
                None if r["consensus_median"] is None else float(r["consensus_median"])
            ),
        })

    latest = series[-1]
    last_raw = display_rows[-1]
    surprise_label = (
        f"{country} CPI YoY surprise"
        if event_type == "cpi_yoy"
        else f"{country} HICP YoY surprise"
    )

    current_metrics = {
        "release_date": latest["date"],
        "country": country,
        "event_type": event_type,
        "period": latest["period"],
        "surprise_label": surprise_label,
        "current_surprise_pct": latest["surprise_pct"],
        "current_z_score": latest["z_score"],
        "release_z_window_releases": z_window,
        "current_actual_pct": latest["actual_pct"],
        "current_consensus_median_pct": latest["consensus_median_pct"],
        "current_prior_pct": (
            None if last_raw["prior"] is None else float(last_raw["prior"])
        ),
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
            "event_type",
            "surprise_label",
            "release_date",
            "period",
            "release_z_window_releases",
            "observation_count",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_cm,
        sql_payload=sql_cm,
        fields=(
            "current_surprise_pct",
            "current_z_score",
            "current_actual_pct",
            "current_consensus_median_pct",
            "current_prior_pct",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # Row-for-row time-series comparison — NO sampling (PR16).
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
        add_exact_field_mismatches(
            mismatches=mismatches,
            tool_payload=tool_row,
            sql_payload=sql_row,
            fields=("period",),
            prefix=f"time_series[{index}].",
        )
        add_numeric_field_mismatches(
            mismatches=mismatches,
            tool_payload=tool_row,
            sql_payload=sql_row,
            fields=(
                "surprise_pct",
                "z_score",
                "actual_pct",
                "consensus_median_pct",
            ),
            tolerances=TOLERANCE_BY_FIELD,
            prefix=f"time_series[{index}].",
        )

    return mismatches


def run_case(engine, *, country: str, lookback_releases: int) -> Tuple[str, List[str]]:
    """Returns (status, mismatches).  status ∈ {'PASS','FAIL','SKIP'}."""
    cfg = load_tool_config(CONFIG_PATH)
    z_window = int(cfg.convention_value("release_z_window"))
    z_min_periods = int(cfg.convention_value("release_z_min_periods"))
    buffer_days = int(cfg.convention_value("release_fetch_buffer_days_per_release"))
    event_type = str(cfg.convention_value(f"cpi_event_type_for_{country.lower()}"))

    tool_result = calculate_cpi_surprise(
        engine=engine,
        params=CpiSurpriseInput(
            country=country, lookback_releases=lookback_releases,
        ),
    )

    from datetime import date as _date_cls, timedelta
    fetch_releases = lookback_releases + z_window
    buffer_calendar_days = fetch_releases * buffer_days
    today = _date_cls.today()
    window_start = today - timedelta(days=buffer_calendar_days)
    sql_result = sql_baseline(
        engine=engine,
        event_type=event_type,
        country=country,
        window_start_iso=window_start.isoformat(),
        window_end_iso=today.isoformat(),
        lookback_releases=lookback_releases,
        z_window=z_window,
        z_min_periods=z_min_periods,
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
            "Validate calculate_cpi_surprise against direct SQL on "
            "macro_data.event_calendar (ADRs 0004 + 0008).  Row-for-"
            "row parity per PR16."
        )
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--lookback", type=int, default=DEFAULT_LOOKBACK_RELEASES,
        help="Number of realised releases in the display window."
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Allow exit 0 when macro_data.event_calendar has zero "
            "realised cpi_yoy / hicp_yoy rows.  Required pre-event-"
            "extractor deployment (TD #28b)."
        ),
    )
    args = parser.parse_args()

    print("=" * 80)
    print("CALCULATE_CPI_SURPRISE TOOL — SQL VALIDATION (row-for-row)")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback      : {args.lookback} releases")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live event_calendar...")
    cases = choose_test_cases(engine, case_count=args.cases, seed=args.seed)
    if not cases:
        print("\n  No (country,) slots have realised cpi_yoy / hicp_yoy rows.")
        if args.allow_empty:
            print("  --allow-empty set; treating as honest pre-extractor-")
            print("  deployment state (ADR 0008 §6 / TD #28b).  Exiting 0.")
            sys.exit(0)
        print("  Expected pre-extractor-deployment but the mandatory SQL")
        print("  validation gate must not pass silently — pass --allow-empty.")
        sys.exit(2)
    print_selected_cases(cases, lambda case: f"{case[0]}")

    print("[3/4] Running tool vs SQL comparisons (row-for-row)...")
    pass_count = 0
    skip_count = 0
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        country, = case
        print_case_header(index, len(cases), country)
        status, mismatches = run_case(
            engine, country=country, lookback_releases=args.lookback,
        )
        if status == "PASS":
            print("PASS")
            pass_count += 1
        elif status == "SKIP":
            print("SKIP (no realised cpi_yoy / hicp_yoy releases in window)")
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
            print(f"  - {case[0]} ({len(mismatches)} mismatches)")
        sys.exit(1)

    if pass_count == 0 and skip_count > 0:
        if args.allow_empty:
            print("\n  All cases SKIPPED; --allow-empty set.")
            sys.exit(0)
        print("\n  All cases SKIPPED — pass --allow-empty to waive.")
        sys.exit(2)


if __name__ == "__main__":
    main()
