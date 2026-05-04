#!/usr/bin/env python3
"""
test_ois_forward_rate_sql_validation.py — OIS forward-rate validator
======================================================================

Validate ``calculate_ois_forward_rate`` against an independent SQL
baseline for the latest curve as-of date (snapshot-only).

Per-tool admission gate the OIS plan requires for every primitive
migration (precedent established by
``test_ois_curve_spread_sql_validation.py`` and
``test_ois_cross_market_spread_sql_validation.py``).

Snapshot-only scope
-------------------
Forward-rate computation is non-trivial in SQL: it requires
day-by-day rolling-window resolution, dual-compounding (simple for
T ≤ 1Y, annual for T > 1Y), per-day extrapolation guards, and a
252-row rolling z-score on a series whose membership shifts
day-to-day (some days legitimately drop out under the per-day guard).
A full historical SQL reproduction would be a 3x repaint of the
Python code.

Instead, this validator independently reproduces the **snapshot
forward rate at the curve's as-of date** in SQL, and compares it to
the tool's ``current_metrics.forward_rate_pct``.  That pins:

  - the raw-fetch column contract (correct curve/tenor/field rows
    come back),
  - the dual-compounding bootstrap math (DF construction +
    forward-from-DF formula),
  - the linear interpolation between adjacent par-rate grid points,
  - the as-of-date alignment.

The full historical time-series, z-score, trailing range, and
display-window cutoff are covered by the dedicated unit tests in
``test_ois_forward_rate_compute.py``.  This admission gate is the
extra independent confirmation that the live data + bootstrap math
agree on the snapshot the desk PM cares about most.

Usage
-----
    python -m tests.test_ois_forward_rate_sql_validation

    python -m tests.test_ois_forward_rate_sql_validation \
        --cases 9 --seed 42 --field PX_LAST
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

# ---------------------------------------------------------------------------
# Path setup — ensure project root is on sys.path regardless of cwd
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.ois.tools.forward_rate import (  # noqa: E402
    calculate_ois_forward_rate,
)
from rates_agent.ois.tools.schemas import OISForwardRateInput  # noqa: E402
from shared.analytics.curve_bootstrap import (  # noqa: E402
    discount_factor_from_par,
    sort_tenors_by_years,
    tenor_to_years,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (curve_family, start_tenor, end_tenor)
Case = Tuple[str, str, str]

DEFAULT_CASE_COUNT = 9
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"

# Canonical forward windows that desks quote daily.  Filtered against
# live DB metadata, so a regression case that no longer has data is
# silently dropped.
REGRESSION_CASES: List[Case] = [
    ("USD_SOFR_OIS", "1Y", "2Y"),  # 1Y1Y SOFR
    ("EUR_ESTR_OIS", "1Y", "2Y"),  # 1Y1Y ESTR
    ("GBP_SONIA_OIS", "5Y", "10Y"),  # 5Y5Y SONIA
]

# Snapshot-only tolerances.  Forward-rate math compounds rounding
# differences at the discount-factor stage, so the tolerance is a
# touch wider than the tool's display rounding (4 decimals on the
# percent surface).
TOLERANCE_BY_FIELD = {
    "forward_rate_pct": 0.0011,  # ~0.011 bps
    "start_spot_rate_pct": 0.00011,
    "end_spot_rate_pct": 0.00011,
}


def choose_test_cases(
    engine,
    *,
    field_name: str,
    case_count: int,
    seed: int,
) -> List[Case]:
    """Pick OIS forward-rate test cases from live DB metadata.

    Strategy: every (curve_family, tenor_short, tenor_long) triple
    where both tenors have at least 80 observations and tenor_long
    maps to a strictly larger year fraction than tenor_short.
    """
    query = text(
        """
        SELECT curve_family, tenor
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type IN ('ois', 'ois_swap')
          AND field_name = :field_name
        GROUP BY curve_family, tenor
        HAVING COUNT(*) >= 80
        ORDER BY curve_family, tenor
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            query, {"field_name": field_name},
        ).mappings().all()

    curve_to_tenors: Dict[str, List[str]] = {}
    for row in rows:
        curve_to_tenors.setdefault(row["curve_family"], []).append(row["tenor"])

    pool: List[Case] = []
    for curve_family, tenors in curve_to_tenors.items():
        ordered = sorted(set(tenors), key=tenor_to_years)
        for i, short_t in enumerate(ordered):
            for long_t in ordered[i + 1:]:
                if tenor_to_years(long_t) > tenor_to_years(short_t):
                    pool.append((curve_family, short_t, long_t))

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
    start_tenor: str,
    end_tenor: str,
    field_name: str,
) -> Dict[str, Any]:
    """Compute the snapshot forward rate at the curve's as-of date
    directly from SQL + Python bootstrap.

    SQL fetches the latest-day par-rate grid; Python independently
    reproduces:
      - linear interpolation at start_years / end_years (independent
        from the tool's interpolate_rate),
      - dual-compounding discount factors (via the shared
        ``discount_factor_from_par``),
      - forward = (DF_start / DF_end - 1) / (end_years - start_years).

    NOT a re-call into the tool: the interpolation and forward formula
    are reimplemented here from first principles so the comparison is
    a real cross-check.
    """
    grid_sql = text(
        """
        WITH latest_date AS (
            SELECT MAX(trade_date) AS as_of
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type IN ('ois', 'ois_swap')
              AND curve_family = :curve_family
              AND field_name = :field_name
        )
        SELECT
            TO_CHAR(t.trade_date, 'YYYY-MM-DD') AS as_of_date,
            t.tenor,
            t.field_value::double precision AS rate_pct
        FROM macro_data.v_market_data_daily_enriched t
        CROSS JOIN latest_date l
        WHERE t.instrument_type IN ('ois', 'ois_swap')
          AND t.curve_family = :curve_family
          AND t.field_name = :field_name
          AND t.trade_date = l.as_of
        ORDER BY t.tenor
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            grid_sql,
            {"curve_family": curve_family, "field_name": field_name},
        ).mappings().all()

    if not rows:
        return {"error": "SQL baseline returned no rows."}

    as_of_date = rows[0]["as_of_date"]
    rate_by_tenor = {r["tenor"]: r["rate_pct"] for r in rows if r["rate_pct"] is not None}
    if start_tenor not in rate_by_tenor or end_tenor not in rate_by_tenor:
        return {
            "error": (
                f"SQL baseline missing required tenors {start_tenor}/{end_tenor} "
                f"for {curve_family} on {as_of_date}; "
                f"available: {sorted(rate_by_tenor.keys())}"
            )
        }

    grid_tenors = sort_tenors_by_years(list(rate_by_tenor.keys()))
    grid_years = [tenor_to_years(t) for t in grid_tenors]
    grid_rates_pct = [float(rate_by_tenor[t]) for t in grid_tenors]

    start_years = tenor_to_years(start_tenor)
    end_years = tenor_to_years(end_tenor)

    # Independent linear interpolation (clamps outside the grid).
    def _linear_interp(target: float) -> float:
        if target <= grid_years[0]:
            return grid_rates_pct[0]
        if target >= grid_years[-1]:
            return grid_rates_pct[-1]
        for i in range(1, len(grid_years)):
            if grid_years[i] >= target:
                x0, x1 = grid_years[i - 1], grid_years[i]
                y0, y1 = grid_rates_pct[i - 1], grid_rates_pct[i]
                w = (target - x0) / (x1 - x0)
                return y0 + (y1 - y0) * w
        return grid_rates_pct[-1]

    start_spot_pct = _linear_interp(start_years)
    end_spot_pct = _linear_interp(end_years)

    # Dual-compounding via the shared primitive (independent from
    # the tool's call site — same primitive but invoked here against
    # the SQL-fetched grid).
    df_start = discount_factor_from_par(start_spot_pct / 100.0, start_years)
    df_end = discount_factor_from_par(end_spot_pct / 100.0, end_years)
    forward_decimal = (df_start / df_end - 1.0) / (end_years - start_years)
    forward_pct = forward_decimal * 100.0

    return {
        "current_metrics": {
            "as_of_date": as_of_date,
            "curve_family": curve_family,
            "forward_rate_pct": round(forward_pct, 4),
            "start_spot_rate_pct": round(start_spot_pct, 4),
            "end_spot_rate_pct": round(end_spot_pct, 4),
        },
    }


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

    tool_metrics = tool_result["current_metrics"]
    sql_metrics = sql_result["current_metrics"]

    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=("as_of_date", "curve_family"),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=("forward_rate_pct", "start_spot_rate_pct", "end_spot_rate_pct"),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    return mismatches


def run_case(
    engine,
    *,
    case: Case,
    lookback_days: int,
    field_name: str,
) -> List[str]:
    curve_family, start_tenor, end_tenor = case
    tool_result = calculate_ois_forward_rate(
        engine=engine,
        params=OISForwardRateInput(
            curve_family=curve_family,
            start_tenor=start_tenor,
            end_tenor=end_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        start_tenor=start_tenor,
        end_tenor=end_tenor,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the OIS forward-rate tool against direct SQL.",
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("OIS FORWARD RATE TOOL — SQL VALIDATION (snapshot)")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live OIS metadata...")
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    if not cases:
        print("No OIS forward-rate validation cases found in the database.")
        sys.exit(1)
    print_selected_cases(
        cases, lambda case: f"{case[0]} {case[1]}/{case[2]}",
    )

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases), f"{case[0]} {case[1]}/{case[2]}",
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
            print(
                f"  - {case[0]} {case[1]}/{case[2]} "
                f"({len(mismatches)} mismatches)"
            )
        sys.exit(1)

    print("\nAll OIS forward-rate validation cases passed.")


if __name__ == "__main__":
    main()
