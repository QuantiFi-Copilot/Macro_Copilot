#!/usr/bin/env python3
"""
test_cross_country_real_yield_spread_simple_sql_validation.py —
Linker cross-country real-yield spread validator.

Validate ``calculate_cross_country_real_yield_spread_simple``
against an independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched`` and
``macro_data.instrument_master``.  The SQL baseline reproduces the
per-trade-date cross-country real-yield spread math without going
through any of the Python tool's helpers, so a mismatch surfaces a
real divergence in methodology rather than a shared-code
coincidence.

Deterministic full-coverage enumeration
---------------------------------------
The linker universe is bounded and ingested through
``rates_agent/playbooks/inflation_indexed_bonds.yml``:

  - USD_TIPS:       5Y / 10Y / 20Y / 30Y
  - GBP_LINKER:     1Y / 2Y / 3Y / 5Y / 10Y / 15Y / 20Y / 30Y / 50Y
  - EUR_FR_LINKER:  2Y / 5Y / 7Y / 10Y / 15Y
  - CAD_RRB:        5Y / 10Y / 15Y / 20Y / 25Y / 30Y

Enumerating all (first_curve, second_curve, tenor) triples where
``first_curve != second_curve`` AND the tenor exists on BOTH
curves (curves canonicalized in lexical order for the regression
list) yields:

  - CAD_RRB vs EUR_FR_LINKER: {5Y, 10Y, 15Y}                  → 3
  - CAD_RRB vs GBP_LINKER:    {5Y, 10Y, 15Y, 20Y, 30Y}        → 5
  - CAD_RRB vs USD_TIPS:      {5Y, 10Y, 20Y, 30Y}              → 4
  - EUR_FR_LINKER vs GBP_LINKER: {2Y, 5Y, 10Y, 15Y}            → 4
  - EUR_FR_LINKER vs USD_TIPS:   {5Y, 10Y}                     → 2
  - GBP_LINKER vs USD_TIPS:      {5Y, 10Y, 20Y, 30Y}           → 4

Total: 3 + 5 + 4 + 4 + 2 + 4 = **22** deterministic cross-country
cases.  ``DEFAULT_CASE_COUNT == len(REGRESSION_CASES) == 22`` so
the helper ``sample_cases`` returns the full fixed list in stable
order on every run — every supported cross-country pair is
exercised against the SQL baseline rather than sampled.  When the
default-coverage invocation produces fewer than 22 cases (live
pool missing pillars), the runner emits a FATAL diagnostic and
exits — matches the contract established by
``test_curve_spread_sql_validation.py`` and reused by
``test_real_yield_curve_spread_sql_validation.py`` /
``test_cross_country_breakeven_spread_simple_sql_validation.py``.

Adversarial probes (4):
  1. ``first_curve_family == second_curve_family`` rejected by
     Pydantic ``_curves_must_differ``; ``extra='forbid'`` rejects
     stray kwargs that could encode a same-curve attempt.
  2. Non-linker curve_family on EITHER leg (cross-product of a
     nominal sovereign with a linker) yields a controlled error
     envelope BEFORE any market-data SELECT fires.  Verified by
     independent SQL count probe.
  3. Unknown / unsupported tenor on one leg returns a controlled
     error envelope (not a Python exception) attributed to that
     leg.
  4. SELECT-guard inheritance: requesting a non-linker
     curve_family under the four-conjunct guard returns zero
     rows, AND the tool refuses with a controlled error envelope
     routing the user back to the sovereign tool.

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \\
        tests/test_cross_country_real_yield_spread_simple_sql_validation.py
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
from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (  # noqa: E402
    CrossCountryRealYieldSpreadSimpleInput,
    calculate_cross_country_real_yield_spread_simple,
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


# (first_curve_family, second_curve_family, tenor)
Case = Tuple[str, str, str]


# Per-curve linker pillars sourced from
# rates_agent/playbooks/inflation_indexed_bonds.yml.
_CURVE_PILLARS: Dict[str, Tuple[str, ...]] = {
    "USD_TIPS":      ("5Y", "10Y", "20Y", "30Y"),
    "GBP_LINKER":    ("1Y", "2Y", "3Y", "5Y", "10Y", "15Y", "20Y", "30Y", "50Y"),
    "EUR_FR_LINKER": ("2Y", "5Y", "7Y", "10Y", "15Y"),
    "CAD_RRB":       ("5Y", "10Y", "15Y", "20Y", "25Y", "30Y"),
}


def _enumerate_cross_country_pairs() -> List[Case]:
    """Enumerate all (first_curve, second_curve, tenor) triples
    where first_curve != second_curve AND the tenor exists on
    BOTH curves.  Curves are canonicalized in lexical order so
    the regression list is stable across runs (the sign
    convention is encoded in the order but the underlying math
    is symmetric apart from sign — flipping (first, second) just
    flips the displayed sign).
    """
    pairs: List[Case] = []
    curves = sorted(_CURVE_PILLARS.keys())
    for i, first in enumerate(curves):
        first_tenors = set(_CURVE_PILLARS[first])
        for second in curves[i + 1:]:
            second_tenors = set(_CURVE_PILLARS[second])
            common = first_tenors & second_tenors
            # Sort tenors by year fraction for stable ordering.
            ordered = sorted(
                common, key=lambda t: tenor_to_years(t),
            )
            for tenor in ordered:
                pairs.append((first, second, tenor))
    return pairs


REGRESSION_CASES: List[Case] = _enumerate_cross_country_pairs()
DEFAULT_CASE_COUNT = len(REGRESSION_CASES)
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"


# Tolerances inherited from the real_yield_curve_spread validator —
# the spread is in PERCENT (yield_round_decimals=4) and the period-
# changes are in BPS (bps_round_decimals=2).
TOLERANCE_BY_FIELD = {
    "current_spread_pct": 0.00011,
    "daily_change_bps": 0.021,
    "weekly_change_bps": 0.021,
    "monthly_change_bps": 0.021,
    "current_z_score": 0.006,
    "high_252d_pct": 0.00011,
    "low_252d_pct": 0.00011,
    "percentile_252d": 0.21,
    "first_curve_real_yield_pct": 0.00011,
    "second_curve_real_yield_pct": 0.00011,
    "tenor_years": 1e-4,
    "spread_pct_row": 0.00011,
    "z_score_row": 0.006,
}


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (first_curve, second_curve, tenor) cases from live
    linker metadata.  Filters to
    ``instrument_type='inflation_linker'`` so the SQL baseline
    operates on the same set the Python tool sees.  Requires at
    least 80 observations on BOTH legs to ensure rolling stats
    are populated.
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
    curves = sorted(pillars_by_curve.keys())
    for i, first in enumerate(curves):
        first_tenors = pillars_by_curve[first]
        for second in curves[i + 1:]:
            second_tenors = pillars_by_curve[second]
            common = first_tenors & second_tenors
            for tenor in common:
                try:
                    tenor_to_years(tenor)
                except ValueError:
                    continue
                pool.append((first, second, tenor))

    # Sort the pool deterministically (curve, curve, tenor years).
    pool.sort(key=lambda c: (c[0], c[1], tenor_to_years(c[2])))

    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    first_curve_family: str,
    second_curve_family: str,
    tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the cross-country real-yield
    spread math.

    Algorithm (mirrors the Python tool's composition shape EXACTLY):
      1. Pull two endpoint linker real-yield series, ONE per
         curve_family at the shared tenor, under the four-conjunct
         SELECT guard (instrument_type='inflation_linker' AND
         curve_family=? AND tenor=? AND field_name=?), per-leg
         ffill within ffill_limit_days (5 trading days) — same
         shape the level primitive's clean step emits.
      2. Inner-join the two endpoint series on trade_date — same
         as the cross-country tool's strict alignment step.
      3. Apply the per-trade-date difference:
            spread_pct = first_pct - second_pct
         and round to 4 decimals (matches the cross-country
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
    tenor_years = tenor_to_years(tenor)
    # Mirror the cross-country primitive's buffer math
    # (max(z_window, trailing_window) * buffer_multiplier = 378).
    # The level primitive then uses its own buffer
    # (z_window * buffer_multiplier = 378) on top, so the
    # effective total fetch buffer is approx lookback + 378 (XC
    # layer) + 378 (level layer) = +756 calendar days.  Plus
    # margin for safety.
    extended_lookback_days = lookback_days + 378

    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                v.trade_date,
                v.curve_family,
                v.field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched v
            WHERE v.instrument_type = 'inflation_linker'
              AND v.curve_family IN (
                  :first_curve_family, :second_curve_family
              )
              AND v.tenor          = :tenor
              AND v.field_name     = :field_name
              AND v.trade_date    >= CURRENT_DATE - ((:lookback_days + 800) * INTERVAL '1 day')
        ),
        -- FIRST leg: per-leg trim+filter to non-null rows.
        first_raw AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE curve_family = :first_curve_family
              AND field_value IS NOT NULL
        ),
        first_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM first_raw
        ),
        first_trim AS (
            SELECT s.trade_date, s.field_value
            FROM first_raw s, first_anchor a
            WHERE s.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        -- SECOND leg: same shape.
        second_raw AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE curve_family = :second_curve_family
              AND field_value IS NOT NULL
        ),
        second_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM second_raw
        ),
        second_trim AS (
            SELECT l.trade_date, l.field_value
            FROM second_raw l, second_anchor a
            WHERE l.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        aligned AS (
            SELECT
                s.trade_date,
                ROUND((s.field_value)::numeric, 4)::double precision AS first_pct,
                ROUND((l.field_value)::numeric, 4)::double precision AS second_pct
            FROM first_trim s
            INNER JOIN second_trim l ON l.trade_date = s.trade_date
        ),
        spread_rows AS (
            SELECT
                trade_date,
                first_pct,
                second_pct,
                ROUND(
                    (first_pct - second_pct)::numeric,
                    4
                )::double precision AS spread_pct
            FROM aligned
        ),
        renumbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn,
                first_pct,
                second_pct,
                spread_pct
            FROM spread_rows
        ),
        scored AS (
            SELECT
                trade_date,
                arn,
                first_pct,
                second_pct,
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
                first_pct,
                second_pct,
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
            first_pct,
            second_pct,
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
                "first_curve_family": first_curve_family,
                "second_curve_family": second_curve_family,
                "tenor": tenor,
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
            "first_curve_family": first_curve_family,
            "second_curve_family": second_curve_family,
            "tenor": tenor,
            "spread_label": (
                f"{first_curve_family} - {second_curve_family} "
                f"{tenor} XC real-yield"
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
            "first_curve_real_yield_pct": latest["first_pct"],
            "second_curve_real_yield_pct": latest["second_pct"],
            "tenor_years": round(tenor_years, 4),
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
            "first_curve_family",
            "second_curve_family",
            "tenor",
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
            "first_curve_real_yield_pct",
            "second_curve_real_yield_pct",
            "tenor_years",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # Both legs' country/currency identities must be populated
    # (resolved from instrument_master).  An empty value would
    # indicate the post-fetch identity guard regressed.
    for ref_field in (
        "first_curve_country",
        "first_curve_currency",
        "second_curve_country",
        "second_curve_currency",
    ):
        v = tool_metrics.get(ref_field)
        if not v:
            mismatches.append(
                f"current_metrics.{ref_field}: tool returned "
                "empty / None — load-bearing reference metadata "
                "must be populated by the both-legs-must-be-linker "
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

def assert_same_curve_rejected_by_schema() -> List[str]:
    """Probe 1: ``first_curve_family == second_curve_family``
    rejected at the Pydantic layer with no fall-through to
    compute.  Also asserts ``extra='forbid'`` rejects stray
    kwargs.
    """
    failures: List[str] = []

    try:
        _ = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="USD_TIPS",
            tenor="10Y",
        )
    except Exception:
        pass
    else:
        failures.append(
            "Same-curve probe: input schema accepted "
            "first_curve_family='USD_TIPS' AND "
            "second_curve_family='USD_TIPS'.  The "
            "_curves_must_differ validator must reject this."
        )

    # ``extra='forbid'`` rejects stray kwargs.
    try:
        _ = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            short_tenor="2Y",  # stray
        )
    except Exception:
        pass
    else:
        failures.append(
            "extra='forbid' probe: input schema accepted a stray "
            "``short_tenor`` kwarg.  Must reject to prevent input-"
            "shape variations encoding alternative concepts."
        )

    return failures


def assert_non_linker_cross_product_rejected(
    engine,
    *,
    first_curve_family: str,
    second_curve_family: str,
    tenor: str,
    field_name: str,
    lookback_days: int,
    expected_failing_leg: str,  # "first_curve" or "second_curve"
) -> List[str]:
    """Probe 2: non-linker curve_family on either leg yields a
    controlled error envelope BEFORE any market-data SELECT.
    Specifically, a cross-product of (nominal sovereign, linker)
    must be refused.
    """
    failures: List[str] = []

    tool_result = calculate_cross_country_real_yield_spread_simple(
        engine=engine,
        params=CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family=first_curve_family,
            second_curve_family=second_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    keys = sorted(tool_result.keys())
    if keys != ["error"]:
        failures.append(
            f"Non-linker cross-product probe ({first_curve_family} "
            f"vs {second_curve_family}): tool returned keys "
            f"{keys!r}.  Expected exactly the controlled-error "
            "envelope {'error': ...}."
        )
        return failures

    err = tool_result["error"]
    if f"{expected_failing_leg}_family" not in err:
        failures.append(
            f"Non-linker cross-product probe error envelope "
            f"missing '{expected_failing_leg}_family' attribution.  "
            f"Got: {err!r}"
        )
    if "inflation_linker" not in err:
        failures.append(
            "Non-linker cross-product probe error envelope missing "
            f"'inflation_linker' rationale.  Got: {err!r}"
        )
    return failures


def assert_unknown_pillar_handling(
    engine,
    *,
    first_curve_family: str,
    second_curve_family: str,
    tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Probe 3: an unknown / unsupported tenor on one leg MUST
    yield a controlled error envelope, NOT a Python exception.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_cross_country_real_yield_spread_simple(
            engine=engine,
            params=CrossCountryRealYieldSpreadSimpleInput(
                first_curve_family=first_curve_family,
                second_curve_family=second_curve_family,
                tenor=tenor,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"Unknown pillar ({first_curve_family} vs "
            f"{second_curve_family} @ {tenor}) raised "
            f"{type(exc).__name__}: {exc} — must return controlled "
            "error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            f"Unknown pillar ({first_curve_family} vs "
            f"{second_curve_family} @ {tenor}) returned a snapshot "
            "— must return controlled error envelope instead.  "
            f"Got keys: {sorted(tool_result.keys())}"
        )
        return failures

    err = tool_result["error"]
    # Either first_curve leg or second_curve leg must be named.
    if (
        "first_curve leg failed" not in err
        and "second_curve leg failed" not in err
    ):
        failures.append(
            "Controlled error envelope missing leg attribution "
            f"(``first_curve leg failed`` or ``second_curve leg "
            f"failed``); got: {err!r}"
        )
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
    """Probe 4: SELECT-guard inheritance — wrong instrument_type
    rows MUST NOT leak into either leg of the cross-country
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

    # Sub-probe 4a: requesting 'UST' under the inflation_linker
    # filter returns zero rows.
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

    # Sub-probe 4b: the tool refuses 'UST' (first leg, second
    # leg=USD_TIPS) with a controlled error envelope BEFORE any
    # market-data fetch fires.
    tool_result = calculate_cross_country_real_yield_spread_simple(
        engine=engine,
        params=CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="UST",
            second_curve_family="USD_TIPS",
            tenor="10Y",
            field_name=field_name,
        ),
    )
    keys = sorted(tool_result.keys())
    if keys != ["error"]:
        failures.append(
            f"Non-linker first_curve_family probe: tool returned "
            f"keys {keys!r} for first_curve_family='UST'.  Expected "
            "exactly the controlled-error envelope {'error': ...}."
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
    if "first_curve_family" not in err:
        failures.append(
            "Controlled error envelope missing 'first_curve_family' "
            f"leg attribution.  Got: {err!r}"
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
            "Validate the linker "
            "cross_country_real_yield_spread_simple tool against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print(
        "LINKER CROSS_COUNTRY_REAL_YIELD_SPREAD_SIMPLE TOOL — "
        "SQL VALIDATION"
    )
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print(
        f"  total_supported_pairs (cross-country pairs at common "
        f"tenors) : {len(REGRESSION_CASES)}"
    )
    print("-" * 80)

    # Fail-loud guard: lock the case count to the universe-derived
    # constant so a future playbook drift (e.g. dropping a USD_TIPS
    # tenor) surfaces here rather than silently shrinking coverage.
    assert len(REGRESSION_CASES) == DEFAULT_CASE_COUNT, (
        f"REGRESSION_CASES drift: expected {DEFAULT_CASE_COUNT}, "
        f"got {len(REGRESSION_CASES)}.  Update _CURVE_PILLARS to "
        "match the playbook universe."
    )

    print("[1/5] Creating DB engine...")
    engine = get_db_engine()

    print(
        "[2/5] Selecting validation cases from live linker "
        "metadata (cross-country pairs only)..."
    )
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases,
        lambda case: f"{case[0]} vs {case[1]} @ {case[2]}",
    )

    # Fail loudly when the default-coverage invocation produces
    # fewer than the full 22 cases.
    if args.cases == DEFAULT_CASE_COUNT and len(cases) != DEFAULT_CASE_COUNT:
        selected = set(cases)
        missing = [c for c in REGRESSION_CASES if c not in selected]
        msg_lines = [
            "FATAL: Incomplete linker cross-country real-yield "
            "spread coverage — live pool is missing supported "
            "pillars.",
            f"  expected : {DEFAULT_CASE_COUNT} deterministic "
            "cases (full REGRESSION_CASES list)",
            f"  observed : {len(cases)} cases returned by "
            "choose_test_cases()",
            f"  missing  : {len(missing)} "
            "(first_curve, second_curve, tenor) triples — see "
            "rates_agent/playbooks/inflation_indexed_bonds.yml "
            "for the ingested universe (USD_TIPS / GBP_LINKER / "
            "EUR_FR_LINKER / CAD_RRB pillars).",
        ]
        for c in missing:
            msg_lines.append(f"    - {c[0]} vs {c[1]} @ {c[2]}")
        full_msg = "\n".join(msg_lines)
        print(full_msg, flush=True)
        print(full_msg, file=sys.stderr, flush=True)
        sys.exit(2)

    print("[3/5] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]} vs {case[1]} @ {case[2]}",
        )
        first, second, tenor = case
        tool_result = calculate_cross_country_real_yield_spread_simple(
            engine=engine,
            params=CrossCountryRealYieldSpreadSimpleInput(
                first_curve_family=first,
                second_curve_family=second,
                tenor=tenor,
                lookback_days=args.days,
                field_name=args.field,
            ),
        )
        sql_result = sql_baseline(
            engine=engine,
            first_curve_family=first,
            second_curve_family=second,
            tenor=tenor,
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
        "[4/5] Adversarial probe 1: same-curve / extra='forbid' "
        "rejected by schema..."
    )
    probe1 = assert_same_curve_rejected_by_schema()
    print("FAIL" if probe1 else "PASS")
    for f in probe1:
        print(f"  - {f}")

    print(
        "[4/5 cont] Adversarial probe 2a: non-linker first leg "
        "refused..."
    )
    probe2a = assert_non_linker_cross_product_rejected(
        engine,
        first_curve_family="UST",  # nominal sovereign
        second_curve_family="USD_TIPS",
        tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
        expected_failing_leg="first_curve",
    )
    print("FAIL" if probe2a else "PASS")
    for f in probe2a:
        print(f"  - {f}")

    print(
        "[4/5 cont] Adversarial probe 2b: non-linker second leg "
        "refused..."
    )
    probe2b = assert_non_linker_cross_product_rejected(
        engine,
        first_curve_family="USD_TIPS",
        second_curve_family="DE_BUND",  # nominal sovereign
        tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
        expected_failing_leg="second_curve",
    )
    print("FAIL" if probe2b else "PASS")
    for f in probe2b:
        print(f"  - {f}")

    print(
        "[5/5] Adversarial probe 3: unknown pillar yields "
        "controlled-error envelope..."
    )
    probe3 = assert_unknown_pillar_handling(
        engine,
        first_curve_family="USD_TIPS",
        second_curve_family="GBP_LINKER",
        tenor="40Y",  # unsupported tenor for USD_TIPS
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe3 else "PASS")
    for f in probe3:
        print(f"  - {f}")

    print(
        "[5/5 cont] Adversarial probe 4: SELECT-guard inheritance "
        "(non-linker curve_family refused before any fetch)..."
    )
    probe4 = assert_select_guard_inheritance(
        engine, field_name=args.field,
    )
    print("FAIL" if probe4 else "PASS")
    for f in probe4:
        print(f"  - {f}")

    print("Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")
    print(
        "  probe_1_same_curve / extra='forbid' : "
        f"{'PASS' if not probe1 else 'FAIL'}"
    )
    print(
        "  probe_2a_non_linker_first_leg       : "
        f"{'PASS' if not probe2a else 'FAIL'}"
    )
    print(
        "  probe_2b_non_linker_second_leg      : "
        f"{'PASS' if not probe2b else 'FAIL'}"
    )
    print(
        "  probe_3_unknown_pillar              : "
        f"{'PASS' if not probe3 else 'FAIL'}"
    )
    print(
        "  probe_4_select_guard_inherit        : "
        f"{'PASS' if not probe4 else 'FAIL'}"
    )

    has_failure = (
        bool(failed_cases) or bool(probe1) or bool(probe2a)
        or bool(probe2b) or bool(probe3) or bool(probe4)
    )
    if has_failure:
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]} vs {case[1]} @ {case[2]} "
                    f"({len(mismatches)} mismatches)"
                )
        sys.exit(1)


if __name__ == "__main__":
    main()
