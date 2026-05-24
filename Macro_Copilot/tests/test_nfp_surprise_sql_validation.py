#!/usr/bin/env python3
"""
test_nfp_surprise_sql_validation.py — NFP-surprise validator
=============================================================

Independently reproduces ``calculate_nfp_surprise``'s logic against
the live TimescaleDB and compares row-for-row (no sampling — PR16).

Mirrors test_cpi_surprise_sql_validation.py but pinned to
(event_type='nfp', country='US') per the brief's "single-country
single-event" framing.

Read-only:
  - Only ``SELECT`` queries.
  - Excluded from pytest collection via ``tests/conftest.py``.

SQL-bind syntax discipline:
  - SQLAlchemy's ``text()`` bind regex excludes ``:name::cast``; this
    file uses ``CAST(:name AS DATE)`` instead (Codex P0 lesson from
    PR #186 / primitive 2).

Usage:

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser DB_PASSWORD=...
    python tests/test_nfp_surprise_sql_validation.py

Honest absence (ADR 0008 §6 / TD #28b):
  Slot with zero realised NFP rows → ``SKIP (empty)``.
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
from rates_agent.sovereign_bonds.tools.nfp_surprise import (  # noqa: E402
    CONFIG_PATH,
    NfpSurpriseInput,
    calculate_nfp_surprise,
)
from shared.config import load_tool_config  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
)


DEFAULT_LOOKBACK_RELEASES = 24

TOLERANCE_BY_FIELD: Dict[str, float] = {
    "current_surprise_k_jobs": 1e-3,
    "current_z_score": 1e-3,
    "current_actual_k_jobs": 1e-6,
    "current_consensus_median_k_jobs": 1e-6,
    "current_prior_k_jobs": 1e-6,
    "surprise_k_jobs": 1e-3,
    "z_score": 1e-3,
    "actual_k_jobs": 1e-6,
    "consensus_median_k_jobs": 1e-6,
}


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
                (actual - consensus_median)::numeric, 0
            )::float AS surprise_k_jobs
        FROM realised
    ),
    enriched AS (
        SELECT
            release_date,
            period,
            actual,
            consensus_median,
            prior,
            surprise_k_jobs,
            AVG(surprise_k_jobs) OVER w  AS rolling_mean,
            STDDEV_SAMP(surprise_k_jobs) OVER w AS rolling_std,
            COUNT(surprise_k_jobs) OVER w AS rolling_count
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
        surprise_k_jobs,
        ROUND(
            ((surprise_k_jobs - rolling_mean) / NULLIF(rolling_std, 0))::numeric,
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

    display_rows = list(rows[-lookback_releases:])

    series = []
    for r in display_rows:
        rolling_count = int(r["rolling_count"] or 0)
        warmed = rolling_count >= z_min_periods
        z = float(r["z_score"]) if (r["z_score"] is not None and warmed) else None
        series.append({
            "date": r["release_date"],
            "period": r["period"],
            "surprise_k_jobs": (
                None if r["surprise_k_jobs"] is None else float(r["surprise_k_jobs"])
            ),
            "z_score": z,
            "actual_k_jobs": (
                None if r["actual"] is None else float(r["actual"])
            ),
            "consensus_median_k_jobs": (
                None if r["consensus_median"] is None else float(r["consensus_median"])
            ),
        })

    latest = series[-1]
    last_raw = display_rows[-1]
    current_metrics = {
        "release_date": latest["date"],
        "country": country,
        "event_type": event_type,
        "period": latest["period"],
        "surprise_label": f"{country} NFP surprise",
        "current_surprise_k_jobs": latest["surprise_k_jobs"],
        "current_z_score": latest["z_score"],
        "release_z_window_releases": z_window,
        "current_actual_k_jobs": latest["actual_k_jobs"],
        "current_consensus_median_k_jobs": latest["consensus_median_k_jobs"],
        "current_prior_k_jobs": (
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
            "current_surprise_k_jobs",
            "current_z_score",
            "current_actual_k_jobs",
            "current_consensus_median_k_jobs",
            "current_prior_k_jobs",
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
                "surprise_k_jobs",
                "z_score",
                "actual_k_jobs",
                "consensus_median_k_jobs",
            ),
            tolerances=TOLERANCE_BY_FIELD,
            prefix=f"time_series[{index}].",
        )

    return mismatches


def run_us_nfp(engine, *, lookback_releases: int) -> Tuple[str, List[str]]:
    """The only case — US NFP (per the brief's single-country single-
    event framing)."""
    cfg = load_tool_config(CONFIG_PATH)
    z_window = int(cfg.convention_value("release_z_window"))
    z_min_periods = int(cfg.convention_value("release_z_min_periods"))
    buffer_days = int(cfg.convention_value("release_fetch_buffer_days_per_release"))
    country = str(cfg.convention_value("country"))
    event_type = str(cfg.convention_value("event_type"))

    tool_result = calculate_nfp_surprise(
        engine=engine,
        params=NfpSurpriseInput(lookback_releases=lookback_releases),
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
            "Validate calculate_nfp_surprise against direct SQL on "
            "macro_data.event_calendar (ADRs 0004 + 0008).  Row-for-"
            "row parity per PR16."
        )
    )
    parser.add_argument(
        "--lookback", type=int, default=DEFAULT_LOOKBACK_RELEASES,
        help="Number of realised releases in the display window."
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Allow exit 0 when macro_data.event_calendar has zero "
            "realised nfp rows.  Required pre-event-extractor "
            "deployment (TD #28b)."
        ),
    )
    args = parser.parse_args()

    print("=" * 80)
    print("CALCULATE_NFP_SURPRISE TOOL — SQL VALIDATION (row-for-row)")
    print("=" * 80)
    print(f"  lookback : {args.lookback} releases")
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running US NFP tool vs SQL comparison (row-for-row)...")
    print_case_header(1, 1, "US")
    status, mismatches = run_us_nfp(engine, lookback_releases=args.lookback)
    if status == "PASS":
        print("PASS")
        print("\n[3/3] Summary: 1/1 PASSED")
        sys.exit(0)
    if status == "SKIP":
        print("SKIP (no realised nfp releases in window)")
        if args.allow_empty:
            print("\n--allow-empty set; treating as honest pre-extractor")
            print("deployment state (ADR 0008 §6 / TD #28b).  Exiting 0.")
            sys.exit(0)
        print("\nExpected pre-extractor-deployment but the mandatory SQL")
        print("validation gate must not pass silently — pass --allow-empty.")
        sys.exit(2)
    print("FAIL")
    for mismatch in mismatches[:10]:
        print(f"  - {mismatch}")
    if len(mismatches) > 10:
        print(f"  - ... plus {len(mismatches) - 10} more mismatches")
    sys.exit(1)


if __name__ == "__main__":
    main()
