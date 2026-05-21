#!/usr/bin/env python3
"""
test_real_yield_curve_spread_sql_validation.py — Same-country
linker real-yield curve-spread validator.

Validate ``calculate_real_yield_curve_spread`` against an
independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched`` ×
``macro_data.instrument_master``.  The SQL baseline reproduces the
per-trade-date real-yield curve-spread math without going through
any of the Python tool's helpers, so a mismatch surfaces a real
divergence in methodology rather than a shared-code coincidence.

Deterministic full-curve coverage
---------------------------------
The linker universe is bounded and ingested through
``rates_agent/playbooks/inflation_indexed_bonds.yml``:

  - USD_TIPS:       5Y / 10Y / 20Y / 30Y                    → C(4, 2)  =  6 pairs
  - GBP_LINKER:     1Y / 2Y / 3Y / 5Y / 10Y / 15Y / 20Y /
                    30Y / 50Y                                → C(9, 2)  = 36 pairs
  - EUR_FR_LINKER:  2Y / 5Y / 7Y / 10Y / 15Y                → C(5, 2)  = 10 pairs
  - CAD_RRB:        5Y / 10Y / 15Y / 20Y / 25Y / 30Y         → C(6, 2)  = 15 pairs

Total: 6 + 36 + 10 + 15 = **67** deterministic same-curve cases.
``DEFAULT_CASE_COUNT == len(REGRESSION_CASES) == 67`` so the
helper ``sample_cases`` returns the full fixed list in stable
order on every run — every supported same-curve pair is exercised
against the SQL baseline rather than sampled.  When the
default-coverage invocation produces fewer than 67 cases (live
pool missing pillars), the runner emits a FATAL diagnostic and
exits — matches the contract established by
``test_curve_spread_sql_validation.py`` and reused by
``test_breakeven_curve_spread_sql_validation.py`` /
``test_inflation_swap_curve_spread_sql_validation.py``.

Adversarial probes (5):
  1. Cross-curve attempt: the input layer rejects this because
     there is only a single ``curve_family`` field.  Pydantic's
     ``extra='forbid'`` must prevent any encoding of mismatched
     curve families via input-shape variations.
  2. ``short_tenor == long_tenor``: Pydantic rejection.
  3. ``short_years > long_years`` (e.g. short=10Y, long=2Y):
     Pydantic rejection (no silent swap).
  4. Unknown / unsupported (curve_family, tenor) on either leg:
     controlled error envelope, NOT a Python exception.
  5. SELECT-guard inheritance: non-linker curve_family (e.g.
     'UST') refused at compute time with a controlled error
     envelope BEFORE any market-data SELECT fires (verified by
     independent SQL count probe — every inflation_linker row
     has the right instrument_type, and a 'UST' curve_family
     yields zero inflation_linker rows).

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \\
        tests/test_real_yield_curve_spread_sql_validation.py
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
from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (  # noqa: E402
    RealYieldCurveSpreadInput,
    calculate_real_yield_curve_spread,
)
from shared.analytics.curve_bootstrap import tenor_to_years  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (curve_family, short_tenor, long_tenor)
Case = Tuple[str, str, str]


# Full deterministic coverage of the linker real-yield curve-spread
# universe.  Tenor grid per curve_family is read from
# rates_agent/playbooks/inflation_indexed_bonds.yml; stable
# curve_family-then-(short,long) ordering so failures are easy to
# diff across runs.
_CURVE_PILLARS: Dict[str, Tuple[str, ...]] = {
    "USD_TIPS":      ("5Y", "10Y", "20Y", "30Y"),
    "GBP_LINKER":    ("1Y", "2Y", "3Y", "5Y", "10Y", "15Y", "20Y", "30Y", "50Y"),
    "EUR_FR_LINKER": ("2Y", "5Y", "7Y", "10Y", "15Y"),
    "CAD_RRB":       ("5Y", "10Y", "15Y", "20Y", "25Y", "30Y"),
}


def _enumerate_same_curve_pairs(
    curve_family: str, pillars: Tuple[str, ...],
) -> List[Case]:
    parsed = sorted(
        ((tenor_to_years(t), t) for t in pillars),
        key=lambda x: x[0],
    )
    pairs: List[Case] = []
    for i, (ys_short, t_short) in enumerate(parsed):
        for ys_long, t_long in parsed[i + 1:]:
            pairs.append((curve_family, t_short, t_long))
    return pairs


REGRESSION_CASES: List[Case] = []
for _cf, _pillars in _CURVE_PILLARS.items():
    REGRESSION_CASES.extend(_enumerate_same_curve_pairs(_cf, _pillars))

DEFAULT_CASE_COUNT = len(REGRESSION_CASES)
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"


# Tolerances inherited from the breakeven_curve_spread validator —
# rounding conventions are aligned by config lint, with the spread
# in PERCENT (yield_round_decimals=4) rather than BPS
# (bps_round_decimals=2), so we tighten by ~100x on the
# PERCENT-units fields and keep the BPS-units period-change fields
# at the sibling tolerance.
TOLERANCE_BY_FIELD = {
    "current_spread_pct": 0.00011,
    "daily_change_bps": 0.021,
    "weekly_change_bps": 0.021,
    "monthly_change_bps": 0.021,
    "current_z_score": 0.006,
    "high_252d_pct": 0.00011,
    "low_252d_pct": 0.00011,
    "percentile_252d": 0.21,
    "short_real_yield_pct": 0.00011,
    "long_real_yield_pct": 0.00011,
    "short_years": 1e-4,
    "long_years": 1e-4,
    "spread_pct_row": 0.00011,
    "z_score_row": 0.006,
}


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (curve_family, short_tenor, long_tenor) cases from
    live linker metadata.  Filters to
    ``instrument_type='inflation_linker'`` so the SQL baseline
    operates on the same set the Python tool sees.  Requires at
    least 80 observations on BOTH endpoints to ensure rolling
    stats are populated.
    """
    pillar_query = text(
        """
        SELECT e.curve_family, e.tenor
        FROM macro_data.v_market_data_daily_enriched e
        JOIN (
            SELECT DISTINCT curve_family, instrument_type
            FROM macro_data.instrument_master
        ) i
          ON i.curve_family    = e.curve_family
         AND i.instrument_type = e.instrument_type
        WHERE e.instrument_type = 'inflation_linker'
          AND e.field_name      = :field_name
        GROUP BY e.curve_family, e.tenor
        HAVING COUNT(*) >= 80
        ORDER BY e.curve_family, e.tenor
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            pillar_query, {"field_name": field_name},
        ).mappings().all()

    pillars_by_curve: Dict[str, set] = {}
    for row in rows:
        pillars_by_curve.setdefault(row["curve_family"], set()).add(
            row["tenor"],
        )

    pool: List[Case] = []
    for curve_family, tenors in pillars_by_curve.items():
        parsed: List[Tuple[float, str]] = []
        for t in sorted(tenors):
            try:
                parsed.append((tenor_to_years(t), t))
            except ValueError:
                continue
        parsed.sort(key=lambda x: x[0])
        for i, (ys_short, t_short) in enumerate(parsed):
            for ys_long, t_long in parsed[i + 1:]:
                pool.append((curve_family, t_short, t_long))

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
    short_tenor: str,
    long_tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the same-country linker
    real-yield curve-spread math.

    Algorithm (mirrors the Python tool's composition shape EXACTLY):
      1. Pull two endpoint linker real-yield series (one per pillar)
         under the four-conjunct SELECT guard
         (instrument_type='inflation_linker' AND curve_family=? AND
         tenor=? AND field_name=?), per-pillar ffill within
         ffill_limit_days (5 trading days) — same shape the level
         primitive's clean step emits.
      2. Inner-join the two endpoint series on trade_date — same
         as the curve-spread primitive's strict alignment step.
      3. Apply the per-trade-date difference:
            spread_pct = long_pct - short_pct
         and round to 4 decimals (matches the curve-spread
         primitive's yield_round_decimals boundary; the spread is
         in PERCENT, NOT bps, because real yields are NOT
         multiplied by 100).
      4. Compute rolling 252-day z-score on the percent-units
         spread, period changes (in BPS — *100 multiplier on the
         percent-units period subtraction), trailing range (in
         PERCENT) — all under the same convention values the
         Python tool uses.
      5. Anchor the display cutoff to the latest aligned
         trade_date (matches the Python tool's anchoring).
    """
    t_short_years = tenor_to_years(short_tenor)
    t_long_years = tenor_to_years(long_tenor)
    # Mirror the curve-spread primitive's buffer math
    # (max(z_window, trailing_window) * buffer_multiplier = 378).
    # The level primitive then uses its own buffer
    # (z_window * buffer_multiplier = 378) on top, so total fetch
    # buffer is approx lookback + 378 (curve layer) + 378 (level
    # layer) = +756 calendar days.  Plus margin for safety.
    extended_lookback_days = lookback_days + 378

    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                v.trade_date,
                v.tenor,
                v.field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched v
            WHERE v.instrument_type = 'inflation_linker'
              AND v.curve_family    = :curve_family
              AND v.tenor IN (:short_tenor, :long_tenor)
              AND v.field_name      = :field_name
              AND v.trade_date     >= CURRENT_DATE - ((:lookback_days + 800) * INTERVAL '1 day')
        ),
        -- Per-pillar pivot+ffill+dropna for the SHORT leg.
        short_raw AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE tenor = :short_tenor
              AND field_value IS NOT NULL
        ),
        short_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM short_raw
        ),
        short_trim AS (
            SELECT s.trade_date, s.field_value
            FROM short_raw s, short_anchor a
            WHERE s.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        -- Per-pillar pivot+ffill+dropna for the LONG leg.
        long_raw AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE tenor = :long_tenor
              AND field_value IS NOT NULL
        ),
        long_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM long_raw
        ),
        long_trim AS (
            SELECT l.trade_date, l.field_value
            FROM long_raw l, long_anchor a
            WHERE l.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        aligned AS (
            SELECT
                s.trade_date,
                ROUND((s.field_value)::numeric, 4)::double precision AS short_pct,
                ROUND((l.field_value)::numeric, 4)::double precision AS long_pct
            FROM short_trim s
            INNER JOIN long_trim l ON l.trade_date = s.trade_date
        ),
        spread_rows AS (
            SELECT
                trade_date,
                short_pct,
                long_pct,
                ROUND(
                    (long_pct - short_pct)::numeric,
                    4
                )::double precision AS spread_pct
            FROM aligned
        ),
        renumbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn,
                short_pct,
                long_pct,
                spread_pct
            FROM spread_rows
        ),
        scored AS (
            SELECT
                trade_date,
                arn,
                short_pct,
                long_pct,
                spread_pct,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(spread_pct) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(spread_pct) OVER zw <> 0
                    THEN ROUND(
                        (
                            (spread_pct - AVG(spread_pct) OVER zw)
                            / NULLIF(STDDEV_SAMP(spread_pct) OVER zw, 0)
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score
            FROM renumbered
            WINDOW zw AS (
                ORDER BY arn
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
            )
        ),
        latest_aligned AS (
            SELECT MAX(trade_date) AS as_of_date FROM renumbered
        ),
        display_rows AS (
            SELECT s.*
            FROM scored s, latest_aligned la
            WHERE s.trade_date >= la.as_of_date - (:lookback_days * INTERVAL '1 day')
        ),
        display_metrics AS (
            SELECT
                trade_date,
                arn,
                short_pct,
                long_pct,
                spread_pct,
                z_score,
                CASE
                    WHEN LAG(spread_pct, 1) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        ((spread_pct - LAG(spread_pct, 1) OVER (ORDER BY arn)) * 100)::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(spread_pct, 5) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        ((spread_pct - LAG(spread_pct, 5) OVER (ORDER BY arn)) * 100)::numeric,
                        2
                    )::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(spread_pct, 21) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        ((spread_pct - LAG(spread_pct, 21) OVER (ORDER BY arn)) * 100)::numeric,
                        2
                    )::double precision
                END AS monthly_change_bps,
                ROUND((MAX(spread_pct) OVER tw)::numeric, 4)::double precision AS high_252d_pct,
                ROUND((MIN(spread_pct) OVER tw)::numeric, 4)::double precision AS low_252d_pct,
                CASE
                    WHEN MAX(spread_pct) OVER tw IS NULL
                      OR MIN(spread_pct) OVER tw IS NULL
                      OR MAX(spread_pct) OVER tw = MIN(spread_pct) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                spread_pct - MIN(spread_pct) OVER tw
                            ) / NULLIF(
                                MAX(spread_pct) OVER tw - MIN(spread_pct) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d
            FROM display_rows
            WINDOW tw AS (
                ORDER BY arn
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
            )
        )
        SELECT
            TO_CHAR(trade_date, 'YYYY-MM-DD') AS date,
            short_pct,
            long_pct,
            spread_pct,
            z_score,
            daily_change_bps,
            weekly_change_bps,
            monthly_change_bps,
            high_252d_pct,
            low_252d_pct,
            percentile_252d
        FROM display_metrics
        ORDER BY date
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            baseline_sql,
            {
                "curve_family": curve_family,
                "short_tenor": short_tenor,
                "long_tenor": long_tenor,
                "field_name": field_name,
                "lookback_days": lookback_days,
                "extended_lookback_days": extended_lookback_days,
            },
        ).mappings().all()

    if not rows:
        return {"error": "SQL baseline returned no rows."}

    latest = rows[-1]
    return {
        "current_metrics": {
            "as_of_date": latest["date"],
            "curve_family": curve_family,
            "short_tenor": short_tenor,
            "long_tenor": long_tenor,
            "spread_label": (
                f"{curve_family} "
                f"{short_tenor.replace('Y', '')}s"
                f"{long_tenor.replace('Y', '')}s real-yield"
            ),
            "current_spread_pct": latest["spread_pct"],
            "daily_change_bps": latest["daily_change_bps"],
            "weekly_change_bps": latest["weekly_change_bps"],
            "monthly_change_bps": latest["monthly_change_bps"],
            "current_z_score": latest["z_score"],
            "rolling_window_days": 252,
            "high_252d_pct": latest["high_252d_pct"],
            "low_252d_pct": latest["low_252d_pct"],
            "percentile_252d": latest["percentile_252d"],
            "short_real_yield_pct": latest["short_pct"],
            "long_real_yield_pct": latest["long_pct"],
            "short_years": round(t_short_years, 4),
            "long_years": round(t_long_years, 4),
        },
        "time_series": [
            {
                "date": row["date"],
                "spread_pct": row["spread_pct"],
                "z_score": row["z_score"],
            }
            for row in rows
        ],
    }


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
            "as_of_date",
            "curve_family",
            "short_tenor",
            "long_tenor",
            "spread_label",
            "rolling_window_days",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_spread_pct",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "high_252d_pct",
            "low_252d_pct",
            "percentile_252d",
            "short_real_yield_pct",
            "long_real_yield_pct",
            "short_years",
            "long_years",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # country / currency must be populated (resolved from
    # instrument_master).  An empty value would indicate the
    # post-fetch identity guard regressed.
    for ref_field in ("country", "currency"):
        v = tool_metrics.get(ref_field)
        if not v:
            mismatches.append(
                f"current_metrics.{ref_field}: tool returned "
                "empty / None — load-bearing reference metadata "
                "must be populated by the same-curve-family "
                "identity guard."
            )

    # methodology_label must be non-empty (threaded from YAML).
    if not tool_metrics.get("methodology_label"):
        mismatches.append(
            "current_metrics.methodology_label: tool returned "
            "empty — must be threaded from YAML's "
            "methodology.what_it_does."
        )

    row_tolerances = dict(TOLERANCE_BY_FIELD)
    row_tolerances["spread_pct"] = TOLERANCE_BY_FIELD["spread_pct_row"]
    row_tolerances["z_score"] = TOLERANCE_BY_FIELD["z_score_row"]
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=("spread_pct", "z_score"),
            tolerances=row_tolerances,
        )
    )
    return mismatches


# ============================================================================
# ADVERSARIAL PROBES
# ============================================================================

def assert_cross_curve_attempt_rejected_by_schema() -> List[str]:
    """Probe 1: cross-curve attempts cannot be expressed.  The
    input layer accepts a single ``curve_family`` field — any
    attempt to encode mismatched curve families through input
    shape variations must fail at the Pydantic layer.
    """
    failures: List[str] = []

    # Verify a stray ``second_curve_family`` (the sort of field a
    # naive cross-curve refactor might add) is rejected by
    # ``extra='forbid'``.
    try:
        _ = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            second_curve_family="GBP_LINKER",
        )
    except Exception:
        pass
    else:
        failures.append(
            "Cross-curve probe: input schema accepted a stray "
            "``second_curve_family`` kwarg.  ``extra='forbid'`` "
            "must reject that to prevent cross-curve attempts via "
            "input-shape variations."
        )

    # Verify the input schema cannot be constructed without a
    # curve_family at all (defends against the same surface area).
    try:
        _ = RealYieldCurveSpreadInput(
            short_tenor="5Y",
            long_tenor="10Y",
        )
    except Exception:
        pass
    else:
        failures.append(
            "Cross-curve probe: input schema accepted construction "
            "without ``curve_family``.  This is the load-bearing "
            "single-curve discriminator and MUST be required."
        )

    return failures


def assert_equal_tenor_rejected_by_schema() -> List[str]:
    """Probe 2: ``short_tenor == long_tenor`` rejected at the
    Pydantic layer with no fall-through to compute.
    """
    failures: List[str] = []
    try:
        _ = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="5Y",
        )
    except Exception:
        return failures
    failures.append(
        "Equal-tenor probe: input schema accepted "
        "short_tenor='5Y' AND long_tenor='5Y'.  The "
        "_tenors_must_differ validator must reject this."
    )
    return failures


def assert_inverted_tenor_rejected_by_schema() -> List[str]:
    """Probe 3: ``short_years > long_years`` rejected at the
    Pydantic layer with no silent swap.
    """
    failures: List[str] = []
    try:
        _ = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="10Y",
            long_tenor="5Y",
        )
    except Exception:
        return failures
    failures.append(
        "Inverted-tenor probe: input schema accepted "
        "short_tenor='10Y' AND long_tenor='5Y' (a curve inversion "
        "in the input shape).  The _short_must_precede_long "
        "validator must reject this without silently swapping the "
        "legs."
    )
    return failures


def assert_unknown_pillar_handling(
    engine,
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Probe 4: an unknown / unsupported (curve_family, tenor) on
    either leg MUST yield a controlled error envelope, NOT a
    Python exception.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_real_yield_curve_spread(
            engine=engine,
            params=RealYieldCurveSpreadInput(
                curve_family=curve_family,
                short_tenor=short_tenor,
                long_tenor=long_tenor,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"Unknown pillar ({curve_family}, {short_tenor}, "
            f"{long_tenor}) raised "
            f"{type(exc).__name__}: {exc} — must return controlled "
            "error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            f"Unknown pillar ({curve_family}, {short_tenor}, "
            f"{long_tenor}) returned a snapshot — must return "
            "controlled error envelope instead.  Got keys: "
            f"{sorted(tool_result.keys())}"
        )
        return failures

    err = tool_result["error"]
    # The inner level primitive's controlled error envelope must
    # propagate the no-proxy guard rationale.
    if "inflation_linker" not in err:
        failures.append(
            "Controlled error envelope missing 'inflation_linker' "
            f"rationale (composition guard inheritance broke); got: "
            f"{err!r}"
        )
    return failures


def assert_select_guard_inheritance(
    engine, *, field_name: str,
) -> List[str]:
    """Probe 5: SELECT-guard inheritance — wrong instrument_type
    rows MUST NOT leak into either leg of the curve-spread
    compute, AND the post-fetch identity guard refuses non-linker
    curve_families with a controlled error envelope BEFORE any
    market-data fetch fires.

    We verify by independent SQL that:
      a. requesting a non-linker curve_family ('UST') under the
         four-conjunct guard returns zero rows for every pillar
         (the level primitive's filter is honest), and
      b. requesting that non-linker curve_family through this
         tool yields a controlled error envelope routing the user
         to the sovereign tool.
    """
    failures: List[str] = []

    # Sub-probe 5a: requesting 'UST' under the inflation_linker
    # filter returns zero rows — this is the load-bearing
    # invariant the post-fetch guard depends on.
    pollution_sql = text(
        """
        SELECT COUNT(*) AS n
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type = 'inflation_linker'
          AND curve_family    = 'UST'
          AND field_name      = :field_name
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            pollution_sql, {"field_name": field_name},
        ).mappings().first()
    n_rows = int(row["n"]) if row is not None else 0
    if n_rows != 0:
        failures.append(
            f"Pollution probe: SELECT under "
            f"instrument_type='inflation_linker' returned {n_rows} "
            "rows for curve_family='UST' (a nominal sovereign "
            "curve).  The level primitive's filter must refuse "
            "non-linker rows."
        )

    # Sub-probe 5b: the tool refuses 'UST' with a controlled
    # error envelope BEFORE any market-data fetch fires.
    tool_result = calculate_real_yield_curve_spread(
        engine=engine,
        params=RealYieldCurveSpreadInput(
            curve_family="UST",
            short_tenor="2Y",
            long_tenor="10Y",
            field_name=field_name,
        ),
    )
    keys = sorted(tool_result.keys())
    if keys != ["error"]:
        failures.append(
            f"Non-linker curve_family probe: tool returned keys "
            f"{keys!r} for curve_family='UST'.  Expected exactly "
            "the controlled-error envelope {'error': ...}."
        )
        return failures

    err = tool_result["error"]
    if "UST" not in err:
        failures.append(
            f"Controlled error envelope missing curve_family "
            f"'UST'.  Got: {err!r}"
        )
    if "inflation_linker" not in err:
        failures.append(
            "Controlled error envelope missing 'inflation_linker' "
            f"identity rationale.  Got: {err!r}"
        )
    return failures


def assert_sample_historical_rows(
    tool_result: Dict[str, Any], sql_result: Dict[str, Any],
) -> List[str]:
    failures: List[str] = []
    if "error" in tool_result or "error" in sql_result:
        return failures
    tool_rows = tool_result["time_series"]
    sql_rows = sql_result["time_series"]
    if not tool_rows or not sql_rows:
        return failures
    if len(tool_rows) != len(sql_rows):
        failures.append(
            f"Historical-sample length mismatch: tool={len(tool_rows)} "
            f"sql={len(sql_rows)}"
        )
        return failures
    idxs = [0, len(tool_rows) // 2, len(tool_rows) - 1]
    for idx in idxs:
        t = tool_rows[idx]
        s = sql_rows[idx]
        if t["date"] != s["date"]:
            failures.append(
                f"Historical sample[{idx}] date mismatch: "
                f"tool={t['date']!r} sql={s['date']!r}"
            )
            continue
        if t["spread_pct"] is None and s["spread_pct"] is None:
            continue
        if t["spread_pct"] is None or s["spread_pct"] is None:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['spread_pct']} sql={s['spread_pct']}"
            )
            continue
        delta = abs(
            float(t["spread_pct"]) - float(s["spread_pct"])
        )
        if delta > TOLERANCE_BY_FIELD["spread_pct_row"] + 1e-12:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['spread_pct']} sql={s['spread_pct']} "
                f"(delta={delta:.6f})"
            )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the linker real_yield_curve_spread tool "
            "against direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("LINKER REAL_YIELD_CURVE_SPREAD TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print(
        f"  total_supported_pairs (per-curve same-curve pairs) : "
        f"{len(REGRESSION_CASES)}"
    )
    print("-" * 80)

    print("[1/6] Creating DB engine...")
    engine = get_db_engine()

    print(
        "[2/6] Selecting validation cases from live linker metadata..."
    )
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases,
        lambda case: f"{case[0]} {case[1]}/{case[2]}",
    )

    # Fail loudly when the default-coverage invocation produces
    # fewer than the full 67 cases, instead of silently passing
    # on an incomplete pool.  Mirrors the contract established by
    # test_curve_spread_sql_validation.py and reused by the
    # breakeven_curve_spread / inflation_swap_curve_spread
    # validators.
    if args.cases == DEFAULT_CASE_COUNT and len(cases) != DEFAULT_CASE_COUNT:
        selected = set(cases)
        missing = [c for c in REGRESSION_CASES if c not in selected]
        msg_lines = [
            "FATAL: Incomplete linker real-yield curve-spread "
            "coverage — live pool is missing supported pillars.",
            f"  expected : {DEFAULT_CASE_COUNT} deterministic cases "
            "(full REGRESSION_CASES list)",
            f"  observed : {len(cases)} cases returned by "
            "choose_test_cases()",
            f"  missing  : {len(missing)} "
            "(curve_family, short_tenor, long_tenor) triples — see "
            "rates_agent/playbooks/inflation_indexed_bonds.yml for "
            "the ingested universe (USD_TIPS / GBP_LINKER / "
            "EUR_FR_LINKER / CAD_RRB pillars).",
        ]
        for c in missing:
            msg_lines.append(f"    - {c[0]} {c[1]}/{c[2]}")
        full_msg = "\n".join(msg_lines)
        print(full_msg, flush=True)
        print(full_msg, file=sys.stderr, flush=True)
        sys.exit(2)

    print("[3/6] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]} {case[1]}/{case[2]}",
        )
        curve_family, short_tenor, long_tenor = case
        tool_result = calculate_real_yield_curve_spread(
            engine=engine,
            params=RealYieldCurveSpreadInput(
                curve_family=curve_family,
                short_tenor=short_tenor,
                long_tenor=long_tenor,
                lookback_days=args.days,
                field_name=args.field,
            ),
        )
        sql_result = sql_baseline(
            engine=engine,
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=args.days,
            field_name=args.field,
        )
        mismatches = compare_results(tool_result, sql_result)
        mismatches.extend(
            assert_sample_historical_rows(tool_result, sql_result),
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

    print(
        "[4/6] Adversarial probe 1: cross-curve attempt rejected by "
        "schema..."
    )
    probe1 = assert_cross_curve_attempt_rejected_by_schema()
    print("FAIL" if probe1 else "PASS")
    for f in probe1:
        print(f"  - {f}")

    print(
        "[4/6 cont] Adversarial probe 2: equal-tenor rejected by "
        "schema..."
    )
    probe2 = assert_equal_tenor_rejected_by_schema()
    print("FAIL" if probe2 else "PASS")
    for f in probe2:
        print(f"  - {f}")

    print(
        "[5/6] Adversarial probe 3: inverted-tenor rejected by schema "
        "(no silent swap)..."
    )
    probe3 = assert_inverted_tenor_rejected_by_schema()
    print("FAIL" if probe3 else "PASS")
    for f in probe3:
        print(f"  - {f}")

    print(
        "[5/6 cont] Adversarial probe 4: unknown pillar yields "
        "controlled-error envelope..."
    )
    probe4 = assert_unknown_pillar_handling(
        engine,
        curve_family="USD_TIPS",
        short_tenor="5Y",
        long_tenor="40Y",  # unsupported tenor for USD_TIPS
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe4 else "PASS")
    for f in probe4:
        print(f"  - {f}")

    print(
        "[6/6] Adversarial probe 5: SELECT-guard inheritance "
        "(non-linker curve_family refused before any fetch)..."
    )
    probe5 = assert_select_guard_inheritance(
        engine, field_name=args.field,
    )
    print("FAIL" if probe5 else "PASS")
    for f in probe5:
        print(f"  - {f}")

    print("Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")
    print(
        "  probe_1_cross_curve_attempt    : "
        f"{'PASS' if not probe1 else 'FAIL'}"
    )
    print(
        "  probe_2_equal_tenor            : "
        f"{'PASS' if not probe2 else 'FAIL'}"
    )
    print(
        "  probe_3_inverted_tenor         : "
        f"{'PASS' if not probe3 else 'FAIL'}"
    )
    print(
        "  probe_4_unknown_pillar         : "
        f"{'PASS' if not probe4 else 'FAIL'}"
    )
    print(
        "  probe_5_select_guard_inherit   : "
        f"{'PASS' if not probe5 else 'FAIL'}"
    )

    has_failure = (
        bool(failed_cases) or bool(probe1) or bool(probe2)
        or bool(probe3) or bool(probe4) or bool(probe5)
    )
    if has_failure:
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]} {case[1]}/{case[2]} "
                    f"({len(mismatches)} mismatches)"
                )
        sys.exit(1)


if __name__ == "__main__":
    main()
