#!/usr/bin/env python3
"""
test_inflation_swap_butterfly_sql_validation.py — Same-curve ZCIS
butterfly validator.

Validate ``calculate_inflation_swap_butterfly`` against an
independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched`` ×
``macro_data.instrument_master``.  The SQL baseline reproduces the
per-trade-date ZCIS butterfly math without going through any of the
Python tool's helpers, so a mismatch surfaces a real divergence in
methodology rather than a shared-code coincidence.

Deterministic full-curve coverage
---------------------------------
The ZCIS universe is small and bounded: 3 curve families
(USD_ZCIS / EUR_ZCIS / GBP_ZCIS) × 7 ingested tenors (1Y / 2Y / 3Y
/ 5Y / 10Y / 20Y / 30Y) = 21 pillars per curve, yielding
C(7, 3) = 35 unique same-curve ``(short, belly, long)`` triplets
per curve × 3 curves = **105** total deterministic cases.
``DEFAULT_CASE_COUNT == len(REGRESSION_CASES) == 105`` so the
helper ``sample_cases`` returns the full fixed list in stable
order on every run — every supported same-curve triplet is
exercised against the SQL baseline rather than sampled.
When the default-coverage invocation produces fewer than 105
cases (live pool missing pillars), the runner emits a FATAL
diagnostic and exits — matches the contract established by
``test_inflation_swap_curve_spread_sql_validation.py`` /
``test_breakeven_butterfly_sql_validation.py``.

Adversarial probes (7):
  1. Tenor-ordering rejection: the input layer rejects any
     ordering other than short<belly<long via Pydantic; no silent
     swap.
  2. Distinct-tenor rejection: duplicated tenors rejected at the
     Pydantic layer.
  3. Cross-curve attempt rejection: the input layer accepts a
     single ``curve_family`` field — stray ``second_curve_family``
     kwargs must be rejected by ``extra='forbid'``.
  4. Non-ZCIS curve_family: a non-ZCIS curve_family ('UST')
     surfaces a controlled error envelope, NOT a Python exception
     — inherited from the level primitive's four-conjunct SELECT
     guard.
  5. Missing-matched-tenor: unknown / unsupported (curve_family,
     tenor) on any endpoint yields a controlled error envelope.
  6. SELECT-guard inheritance: pollution check via independent SQL
     count probe — wrong instrument_type / pricing_type rows MUST
     NOT leak into any leg.
  7. Cross-config / methodology threading: confirms the bundled
     YAML methodology disclosure (raw-rate-space caveat, weight
     tuple, formula) actually reaches the wire on a live DB call.

The SQL cross-check INDEPENDENTLY reproduces the butterfly
arithmetic — it fetches ZCIS rates for each of the three tenors
directly in SQL, then assembles
``(belly_zcis_pct - 0.5*(short_zcis_pct + long_zcis_pct)) * 100``
in SQL — and compares against the Python output day-by-day with
tolerance set to the ``bps_round_decimals`` /
``yield_round_decimals`` conventions.

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \\
        tests/test_inflation_swap_butterfly_sql_validation.py
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
from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (  # noqa: E402
    InflationSwapButterflyInput,
    calculate_inflation_swap_butterfly,
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


# (curve_family, short_tenor, belly_tenor, long_tenor)
Case = Tuple[str, str, str, str]


# Full deterministic coverage of the ZCIS butterfly universe.
# 3 curve families × C(7, 3) = 35 same-curve triplets each = 105 cases.
# Stable curve_family-then-(short,belly,long) ordering so failures
# are easy to diff across runs.
_PILLARS: Tuple[str, ...] = ("1Y", "2Y", "3Y", "5Y", "10Y", "20Y", "30Y")
_PILLAR_YEARS = {t: tenor_to_years(t) for t in _PILLARS}


def _enumerate_same_curve_triplets(curve_family: str) -> List[Case]:
    triplets: List[Case] = []
    sorted_pillars = sorted(_PILLARS, key=lambda t: _PILLAR_YEARS[t])
    n = len(sorted_pillars)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                triplets.append(
                    (
                        curve_family,
                        sorted_pillars[i],
                        sorted_pillars[j],
                        sorted_pillars[k],
                    )
                )
    return triplets


REGRESSION_CASES: List[Case] = (
    _enumerate_same_curve_triplets("USD_ZCIS")
    + _enumerate_same_curve_triplets("EUR_ZCIS")
    + _enumerate_same_curve_triplets("GBP_ZCIS")
)
DEFAULT_CASE_COUNT = len(REGRESSION_CASES)
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_MID"


# Tolerances inherited from the breakeven_butterfly +
# inflation_swap_curve_spread validators — chained rounding from
# the per-leg ZCIS level primitive composition is wider than a
# 2-point spread but narrower than the bond-breakeven chain.
#
# z_score_row is loosened to 0.025 (vs the sibling 0.006) to
# cover the cumulative effect of FP-rounding boundary
# divergences inside the 252-day rolling window.  The underlying
# divergence is the well-known Python `round()` / numpy
# banker's-rounding vs PostgreSQL `ROUND(numeric, 2)`
# half-away-from-zero behaviour on values whose IEEE-754
# representation differs from the decimal literal by < 1 ulp.
# Live boundary example observed in the audit: 20Y = 3.12985 —
# Python rounds to 3.1298, PostgreSQL numeric rounds to 3.1299
# (the FP storage of the .x...85 boundary literal is 1 ulp
# below the decimal value, so Python's banker rounding and
# SQL's half-away-from-zero land on opposite sides).  The
# resulting per-row butterfly_bps deltas measured against the
# live SQL baseline are ≤ 0.020 bps (max delta 0.020 bps,
# GBP_ZCIS 1Y/20Y/30Y, 2025-05-14) — well inside the 0.031 bps
# row tolerance for butterfly_bps.  The z_score_row delta
# induced by a single such per-row shift propagating through a
# 252-day rolling window reaches ≤ 0.0212 standard deviations
# (max delta 0.0212, USD_ZCIS 10Y/20Y/30Y, 2025-10-17), and
# z_score_row > 0.010 occurred on 50 rows in the audit, so the
# prior 0.010-class tolerance used by sibling primitives would
# be too strict here.  The current_z_score (snapshot date)
# is unaffected by this rolling-window propagation: its max
# delta is 0.0027, well inside the standard 0.006 tolerance —
# so the tool's primary wire output is still pinned tightly to
# the SQL baseline.  The looser z_score_row tolerance only
# relaxes the historical-row z-score comparison.
TOLERANCE_BY_FIELD = {
    "current_butterfly_bps": 0.031,
    "daily_change_bps": 0.031,
    "weekly_change_bps": 0.031,
    "monthly_change_bps": 0.031,
    "current_z_score": 0.006,
    "high_252d_bps": 0.031,
    "low_252d_bps": 0.031,
    "percentile_252d": 0.21,
    "wing_short_bps": 0.021,
    "wing_long_bps": 0.021,
    "short_zcis_rate_pct": 0.00011,
    "belly_zcis_rate_pct": 0.00011,
    "long_zcis_rate_pct": 0.00011,
    "short_years": 1e-4,
    "belly_years": 1e-4,
    "long_years": 1e-4,
    "butterfly_bps_row": 0.031,
    "z_score_row": 0.025,
}


def butterfly_label(
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
) -> str:
    return (
        f"{curve_family} "
        f"{short_tenor.replace('Y', '')}s"
        f"{belly_tenor.replace('Y', '')}s"
        f"{long_tenor.replace('Y', '')}s"
    )


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (curve_family, short_tenor, belly_tenor, long_tenor)
    cases from live ZCIS metadata.  Filters to the
    ``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'`` universe so the SQL
    baseline operates on the same set the Python tool sees.
    Requires at least 80 observations on all three endpoints.
    """
    pillar_query = text(
        """
        SELECT v.curve_family, v.tenor
        FROM macro_data.v_market_data_daily_enriched v
        JOIN macro_data.instrument_master i
          ON v.instrument_id = i.instrument_id
        WHERE v.instrument_type = 'inflation_swap'
          AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
          AND v.field_name = :field_name
        GROUP BY v.curve_family, v.tenor
        HAVING COUNT(*) >= 80
        ORDER BY v.curve_family, v.tenor
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
        n = len(parsed)
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    pool.append(
                        (
                            curve_family,
                            parsed[i][1],
                            parsed[j][1],
                            parsed[k][1],
                        )
                    )

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
    belly_tenor: str,
    long_tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the same-curve ZCIS butterfly
    math.

    Algorithm (mirrors the Python tool's composition shape EXACTLY):
      1. Pull three endpoint ZCIS series (one per pillar) under the
         four-conjunct SELECT guard
         (instrument_type='inflation_swap' AND
         pricing_type='zero_coupon_breakeven' AND
         curve_family=? AND tenor=?), per-pillar ffill within
         ffill_limit_days (5 trading days), anchor-trim each pillar
         to extended_lookback_days from its own latest trade_date —
         same shape the level primitive's clean + anchoring step
         emits.
      2. Inner-join the three endpoint series on trade_date — same
         as the butterfly primitive's strict alignment step.
      3. Apply the FIXED simple-butterfly weighting:
            butterfly_bps = (belly_pct - 0.5*(short_pct + long_pct))
                            * 100
         and round to 2 decimals (matches the butterfly primitive's
         bps_round_decimals boundary).  This is the LOAD-BEARING
         butterfly arithmetic step — assembled directly from the
         three ZCIS rate columns via the FIXED simple-butterfly
         weighting, EXACTLY the arithmetic the Python primitive
         performs.
      4. Compute rolling 252-day z-score on the bps butterfly,
         period changes (in BPS — plain subtraction), trailing
         range (in BPS) — all under the same convention values the
         Python tool uses.
      5. Anchor the display cutoff to the latest aligned trade_date
         (matches the Python tool's anchoring).
    """
    t_short_years = tenor_to_years(short_tenor)
    t_belly_years = tenor_to_years(belly_tenor)
    t_long_years = tenor_to_years(long_tenor)
    # Mirror the butterfly primitive's buffer math
    # (compute._conventions_from_config +
    # extended_lookback_days = lookback + buffer_calendar_days).
    # The buffer is max(z_window, trailing_window) *
    # buffer_multiplier = 252 * 1.5 = 378.  The level primitive
    # then uses its own buffer of z_window * buffer_multiplier =
    # 252 * 1.5 = 378 on top.
    extended_lookback_days = lookback_days + 378

    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                v.trade_date,
                v.tenor,
                v.field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched v
            JOIN macro_data.instrument_master i
              ON v.instrument_id = i.instrument_id
            WHERE v.instrument_type = 'inflation_swap'
              AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
              AND v.curve_family    = :curve_family
              AND v.tenor IN (:short_tenor, :belly_tenor, :long_tenor)
              AND v.field_name      = :field_name
              AND v.trade_date     >= CURRENT_DATE - ((:lookback_days + 800) * INTERVAL '1 day')
        ),
        -- Per-pillar pivot+ffill+dropna+anchor-trim for the SHORT leg.
        -- Mirrors the inner level primitive's clean_single_series ffill
        -- discipline (ffill_limit=5).  Per-tenor numbering so the ffill
        -- LAG window operates on a contiguous trading-day grid, exactly
        -- matching the breakeven_butterfly SQL baseline pattern.
        short_dates AS (
            SELECT DISTINCT trade_date
            FROM raw
            WHERE tenor = :short_tenor
        ),
        short_numbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS srn
            FROM short_dates
        ),
        short_joined AS (
            SELECT
                sn.trade_date,
                sn.srn,
                MAX(sr.field_value) AS raw_value
            FROM short_numbered sn
            LEFT JOIN raw sr
              ON sr.trade_date = sn.trade_date
             AND sr.tenor      = :short_tenor
            GROUP BY sn.trade_date, sn.srn
        ),
        short_ffill_idx AS (
            SELECT
                *,
                MAX(CASE WHEN raw_value IS NOT NULL THEN srn END)
                    OVER (ORDER BY srn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS last_rn
            FROM short_joined
        ),
        short_filled AS (
            SELECT
                trade_date,
                srn,
                CASE
                    WHEN raw_value IS NOT NULL THEN raw_value
                    WHEN last_rn IS NOT NULL AND srn - last_rn <= 5
                    THEN MAX(CASE WHEN raw_value IS NOT NULL THEN raw_value END)
                         OVER (PARTITION BY last_rn)
                    ELSE NULL
                END AS field_value
            FROM short_ffill_idx
        ),
        short_unfiltered AS (
            SELECT trade_date, field_value
            FROM short_filled
            WHERE field_value IS NOT NULL
        ),
        short_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM short_unfiltered
        ),
        short_trim AS (
            SELECT s.trade_date, s.field_value
            FROM short_unfiltered s, short_anchor a
            WHERE s.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        -- Per-pillar pivot+ffill+dropna+anchor-trim for the BELLY leg.
        belly_dates AS (
            SELECT DISTINCT trade_date
            FROM raw
            WHERE tenor = :belly_tenor
        ),
        belly_numbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS brn
            FROM belly_dates
        ),
        belly_joined AS (
            SELECT
                bn.trade_date,
                bn.brn,
                MAX(br.field_value) AS raw_value
            FROM belly_numbered bn
            LEFT JOIN raw br
              ON br.trade_date = bn.trade_date
             AND br.tenor      = :belly_tenor
            GROUP BY bn.trade_date, bn.brn
        ),
        belly_ffill_idx AS (
            SELECT
                *,
                MAX(CASE WHEN raw_value IS NOT NULL THEN brn END)
                    OVER (ORDER BY brn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS last_rn
            FROM belly_joined
        ),
        belly_filled AS (
            SELECT
                trade_date,
                brn,
                CASE
                    WHEN raw_value IS NOT NULL THEN raw_value
                    WHEN last_rn IS NOT NULL AND brn - last_rn <= 5
                    THEN MAX(CASE WHEN raw_value IS NOT NULL THEN raw_value END)
                         OVER (PARTITION BY last_rn)
                    ELSE NULL
                END AS field_value
            FROM belly_ffill_idx
        ),
        belly_unfiltered AS (
            SELECT trade_date, field_value
            FROM belly_filled
            WHERE field_value IS NOT NULL
        ),
        belly_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM belly_unfiltered
        ),
        belly_trim AS (
            SELECT b.trade_date, b.field_value
            FROM belly_unfiltered b, belly_anchor a
            WHERE b.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        -- Per-pillar pivot+ffill+dropna+anchor-trim for the LONG leg.
        long_dates AS (
            SELECT DISTINCT trade_date
            FROM raw
            WHERE tenor = :long_tenor
        ),
        long_numbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS lrn
            FROM long_dates
        ),
        long_joined AS (
            SELECT
                ln.trade_date,
                ln.lrn,
                MAX(lr.field_value) AS raw_value
            FROM long_numbered ln
            LEFT JOIN raw lr
              ON lr.trade_date = ln.trade_date
             AND lr.tenor      = :long_tenor
            GROUP BY ln.trade_date, ln.lrn
        ),
        long_ffill_idx AS (
            SELECT
                *,
                MAX(CASE WHEN raw_value IS NOT NULL THEN lrn END)
                    OVER (ORDER BY lrn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS last_rn
            FROM long_joined
        ),
        long_filled AS (
            SELECT
                trade_date,
                lrn,
                CASE
                    WHEN raw_value IS NOT NULL THEN raw_value
                    WHEN last_rn IS NOT NULL AND lrn - last_rn <= 5
                    THEN MAX(CASE WHEN raw_value IS NOT NULL THEN raw_value END)
                         OVER (PARTITION BY last_rn)
                    ELSE NULL
                END AS field_value
            FROM long_ffill_idx
        ),
        long_unfiltered AS (
            SELECT trade_date, field_value
            FROM long_filled
            WHERE field_value IS NOT NULL
        ),
        long_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM long_unfiltered
        ),
        long_trim AS (
            SELECT l.trade_date, l.field_value
            FROM long_unfiltered l, long_anchor a
            WHERE l.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        aligned AS (
            SELECT
                s.trade_date,
                ROUND((s.field_value)::numeric, 4)::double precision AS short_pct,
                ROUND((b.field_value)::numeric, 4)::double precision AS belly_pct,
                ROUND((l.field_value)::numeric, 4)::double precision AS long_pct
            FROM short_trim s
            INNER JOIN belly_trim b ON b.trade_date = s.trade_date
            INNER JOIN long_trim  l ON l.trade_date = s.trade_date
        ),
        -- This is the LOAD-BEARING butterfly arithmetic step.  It
        -- assembles the SQL-side butterfly directly from
        -- (short_pct, belly_pct, long_pct) via the FIXED simple-
        -- butterfly weighting and the wing spreads — the exact
        -- arithmetic the Python primitive performs.
        butterfly_rows AS (
            SELECT
                trade_date,
                short_pct,
                belly_pct,
                long_pct,
                ROUND(
                    ((belly_pct - 0.5 * (short_pct + long_pct)) * 100)::numeric,
                    2
                )::double precision AS butterfly_bps,
                ROUND(((belly_pct - short_pct) * 100)::numeric, 2)::double precision AS wing_short_bps,
                ROUND(((long_pct - belly_pct) * 100)::numeric, 2)::double precision AS wing_long_bps
            FROM aligned
        ),
        renumbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn,
                short_pct,
                belly_pct,
                long_pct,
                butterfly_bps,
                wing_short_bps,
                wing_long_bps
            FROM butterfly_rows
        ),
        scored AS (
            SELECT
                trade_date,
                arn,
                short_pct,
                belly_pct,
                long_pct,
                butterfly_bps,
                wing_short_bps,
                wing_long_bps,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(butterfly_bps) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(butterfly_bps) OVER zw <> 0
                    THEN ROUND(
                        (
                            (butterfly_bps - AVG(butterfly_bps) OVER zw)
                            / NULLIF(STDDEV_SAMP(butterfly_bps) OVER zw, 0)
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
                belly_pct,
                long_pct,
                butterfly_bps,
                wing_short_bps,
                wing_long_bps,
                z_score,
                CASE
                    WHEN LAG(butterfly_bps, 1) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (butterfly_bps - LAG(butterfly_bps, 1) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(butterfly_bps, 5) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (butterfly_bps - LAG(butterfly_bps, 5) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(butterfly_bps, 21) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (butterfly_bps - LAG(butterfly_bps, 21) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS monthly_change_bps,
                ROUND((MAX(butterfly_bps) OVER tw)::numeric, 2)::double precision AS high_252d_bps,
                ROUND((MIN(butterfly_bps) OVER tw)::numeric, 2)::double precision AS low_252d_bps,
                CASE
                    WHEN MAX(butterfly_bps) OVER tw IS NULL
                      OR MIN(butterfly_bps) OVER tw IS NULL
                      OR MAX(butterfly_bps) OVER tw = MIN(butterfly_bps) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                butterfly_bps - MIN(butterfly_bps) OVER tw
                            ) / NULLIF(
                                MAX(butterfly_bps) OVER tw - MIN(butterfly_bps) OVER tw,
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
            belly_pct,
            long_pct,
            butterfly_bps,
            wing_short_bps,
            wing_long_bps,
            z_score,
            daily_change_bps,
            weekly_change_bps,
            monthly_change_bps,
            high_252d_bps,
            low_252d_bps,
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
                "belly_tenor": belly_tenor,
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
            "belly_tenor": belly_tenor,
            "long_tenor": long_tenor,
            "butterfly_label": butterfly_label(
                curve_family, short_tenor, belly_tenor, long_tenor,
            ),
            "current_butterfly_bps": latest["butterfly_bps"],
            "daily_change_bps": latest["daily_change_bps"],
            "weekly_change_bps": latest["weekly_change_bps"],
            "monthly_change_bps": latest["monthly_change_bps"],
            "current_z_score": latest["z_score"],
            "rolling_window_days": 252,
            "high_252d_bps": latest["high_252d_bps"],
            "low_252d_bps": latest["low_252d_bps"],
            "percentile_252d": latest["percentile_252d"],
            "wing_short_bps": latest["wing_short_bps"],
            "wing_long_bps": latest["wing_long_bps"],
            "short_zcis_rate_pct": latest["short_pct"],
            "belly_zcis_rate_pct": latest["belly_pct"],
            "long_zcis_rate_pct": latest["long_pct"],
            "short_years": round(t_short_years, 4),
            "belly_years": round(t_belly_years, 4),
            "long_years": round(t_long_years, 4),
        },
        "time_series": [
            {
                "date": row["date"],
                "butterfly_bps": row["butterfly_bps"],
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
            "belly_tenor",
            "long_tenor",
            "butterfly_label",
            "rolling_window_days",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_butterfly_bps",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "wing_short_bps",
            "wing_long_bps",
            "short_zcis_rate_pct",
            "belly_zcis_rate_pct",
            "long_zcis_rate_pct",
            "short_years",
            "belly_years",
            "long_years",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # Reference-metadata wire surface: every successful run must
    # populate these fields.  An empty value would indicate the
    # same-curve reference metadata threading regressed.
    for ref_field in (
        "inflation_index_family", "index_lag", "interpolation",
    ):
        v = tool_metrics.get(ref_field)
        if not v:
            mismatches.append(
                f"current_metrics.{ref_field}: tool returned "
                "empty / None — load-bearing reference metadata "
                "must be populated."
            )

    # methodology_label must be non-empty (threaded from YAML).
    if not tool_metrics.get("methodology_label"):
        mismatches.append(
            "current_metrics.methodology_label: tool returned "
            "empty — must be threaded from YAML's "
            "methodology.what_it_does."
        )

    row_tolerances = dict(TOLERANCE_BY_FIELD)
    row_tolerances["butterfly_bps"] = TOLERANCE_BY_FIELD["butterfly_bps_row"]
    row_tolerances["z_score"] = TOLERANCE_BY_FIELD["z_score_row"]
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=("butterfly_bps", "z_score"),
            tolerances=row_tolerances,
        )
    )
    return mismatches


# ============================================================================
# ADVERSARIAL PROBES
# ============================================================================

def assert_tenor_ordering_rejected_by_schema() -> List[str]:
    """Probe 1: any ordering other than short<belly<long is
    rejected at the Pydantic layer with no silent swap.
    """
    failures: List[str] = []

    for tup, label in (
        (("USD_ZCIS", "10Y", "5Y", "30Y"), "short>belly"),
        (("USD_ZCIS", "5Y", "30Y", "10Y"), "belly>long"),
        (("USD_ZCIS", "30Y", "10Y", "5Y"), "fully reversed"),
    ):
        cf, s, b, lg = tup
        try:
            _ = InflationSwapButterflyInput(
                curve_family=cf,
                short_tenor=s,
                belly_tenor=b,
                long_tenor=lg,
            )
        except Exception:
            pass
        else:
            failures.append(
                f"Tenor-ordering probe ({label}): input schema "
                f"accepted ({s}, {b}, {lg}).  Validator must reject "
                "this without silently swapping the legs."
            )
    return failures


def assert_distinct_tenor_rejected_by_schema() -> List[str]:
    """Probe 2: any duplicated tenor in the triplet is rejected at
    the Pydantic layer with no fall-through to compute.
    """
    failures: List[str] = []

    for tup, label in (
        (("USD_ZCIS", "10Y", "10Y", "30Y"), "short==belly"),
        (("USD_ZCIS", "5Y", "10Y", "10Y"), "belly==long"),
        (("USD_ZCIS", "10Y", "20Y", "10Y"), "short==long"),
    ):
        cf, s, b, lg = tup
        try:
            _ = InflationSwapButterflyInput(
                curve_family=cf,
                short_tenor=s,
                belly_tenor=b,
                long_tenor=lg,
            )
        except Exception:
            pass
        else:
            failures.append(
                f"Distinct-tenor probe ({label}): input schema "
                f"accepted ({s}, {b}, {lg}).  Validator must reject "
                "this."
            )
    return failures


def assert_cross_curve_attempt_rejected_by_schema() -> List[str]:
    """Probe 3: cross-curve attempts cannot be expressed.  The
    input layer accepts a single ``curve_family`` field — stray
    ``second_curve_family`` kwargs must be rejected by
    ``extra='forbid'``.
    """
    failures: List[str] = []

    try:
        _ = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            second_curve_family="EUR_ZCIS",
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

    try:
        _ = InflationSwapButterflyInput(
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
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


def assert_non_zcis_curve_family_returns_controlled_error(
    engine,
    *,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Probe 4: a non-ZCIS curve_family ('UST') surfaces a
    controlled error envelope via the level primitive's four-
    conjunct SELECT guard.  NOT a Python exception.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_inflation_swap_butterfly(
            engine=engine,
            params=InflationSwapButterflyInput(
                curve_family="UST",  # nominal sovereign — NOT a ZCIS curve
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"Non-ZCIS curve_family probe raised "
            f"{type(exc).__name__}: {exc} — must return controlled "
            "error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            f"Non-ZCIS curve_family probe returned a snapshot — "
            "must return controlled error envelope instead.  Got "
            f"keys: {sorted(tool_result.keys())}"
        )
        return failures

    err = tool_result["error"]
    if "inflation_swap" not in err:
        failures.append(
            "Controlled error envelope missing 'inflation_swap' "
            f"rationale (four-conjunct guard inheritance broke); "
            f"got: {err!r}"
        )
    if "zero_coupon_breakeven" not in err:
        failures.append(
            "Controlled error envelope missing "
            f"'zero_coupon_breakeven' rationale; got: {err!r}"
        )
    return failures


def assert_unknown_pillar_handling(
    engine,
    *,
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Probe 5: missing-matched-tenor.  An unknown / unsupported
    (curve_family, tenor) on any endpoint MUST yield a controlled
    error envelope, NOT a Python exception.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_inflation_swap_butterfly(
            engine=engine,
            params=InflationSwapButterflyInput(
                curve_family=curve_family,
                short_tenor=short_tenor,
                belly_tenor=belly_tenor,
                long_tenor=long_tenor,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"Unknown pillar ({curve_family} "
            f"{short_tenor}/{belly_tenor}/{long_tenor}) raised "
            f"{type(exc).__name__}: {exc} — must return controlled "
            "error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            f"Unknown pillar ({curve_family} "
            f"{short_tenor}/{belly_tenor}/{long_tenor}) returned a "
            "snapshot — must return controlled error envelope "
            f"instead.  Got keys: {sorted(tool_result.keys())}"
        )
    return failures


def assert_four_conjunct_select_guard_inherited(
    engine, *, field_name: str,
) -> List[str]:
    """Probe 6: wrong instrument_type / pricing_type rows MUST NOT
    leak into any leg of the butterfly compute.

    The level primitive's compute issues a SELECT with the
    four-conjunct guard
    (instrument_type='inflation_swap' AND
    pricing_type='zero_coupon_breakeven' AND curve_family=? AND
    tenor=?).  Composing it three times means all three legs
    inherit the guard.  We verify by independent SQL that:
      a. EVERY row currently in the inflation_swap universe has
         pricing_type='zero_coupon_breakeven', and
      b. requesting a non-ZCIS curve_family ('UST') under that
         filter returns zero rows for every supported pillar.
    """
    failures: List[str] = []

    # Sub-probe 6a: pricing_type universe is exactly
    # {'zero_coupon_breakeven'} — i.e. the guard would refuse
    # anything else if/when it lands.
    pricing_sql = text(
        """
        SELECT DISTINCT i.attributes ->> 'pricing_type' AS pricing_type
        FROM macro_data.v_market_data_daily_enriched v
        JOIN macro_data.instrument_master i
          ON v.instrument_id = i.instrument_id
        WHERE v.instrument_type = 'inflation_swap'
          AND v.field_name      = :field_name
        ORDER BY pricing_type
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            pricing_sql, {"field_name": field_name},
        ).mappings().all()
    pricing_types = sorted(
        r["pricing_type"] for r in rows if r["pricing_type"] is not None
    )
    if pricing_types and pricing_types != ["zero_coupon_breakeven"]:
        failures.append(
            "SQL universe contains inflation_swap pricing_types "
            f"other than 'zero_coupon_breakeven': {pricing_types!r}.  "
            "The four-conjunct guard would still filter them out, "
            "but verify the level primitive's compute.py has not "
            "regressed."
        )

    # Sub-probe 6b: requesting a non-ZCIS curve_family under the
    # full four-conjunct guard returns zero rows.  This pins the
    # transitive inheritance of the guard via composition.
    pollution_sql = text(
        """
        SELECT COUNT(*) AS n
        FROM macro_data.v_market_data_daily_enriched v
        JOIN macro_data.instrument_master i
          ON v.instrument_id = i.instrument_id
        WHERE v.instrument_type = 'inflation_swap'
          AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
          AND v.curve_family    = 'UST'
          AND v.field_name      = :field_name
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            pollution_sql, {"field_name": field_name},
        ).mappings().first()
    n_rows = int(row["n"]) if row is not None else 0
    if n_rows != 0:
        failures.append(
            f"Pollution probe: SELECT under the four-conjunct "
            f"guard returned {n_rows} rows for curve_family='UST' "
            "(a nominal sovereign curve).  The guard MUST refuse "
            "non-ZCIS rows via the composed level primitive."
        )

    return failures


def assert_methodology_label_threaded(
    engine,
    *,
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Probe 7: confirm the bundled YAML methodology disclosure
    (raw-rate-space caveat, weight tuple, formula) actually reaches
    the wire on a live DB call.
    """
    failures: List[str] = []
    tool_result = calculate_inflation_swap_butterfly(
        engine=engine,
        params=InflationSwapButterflyInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    if "error" in tool_result:
        failures.append(
            "Methodology-label probe: tool returned error envelope "
            f"on a live happy-path call: {tool_result['error']!r}"
        )
        return failures
    label = tool_result["current_metrics"].get("methodology_label", "")
    label_lower = label.lower()
    if "(-0.5, +1.0, -0.5)" not in label:
        failures.append(
            "Methodology label missing the explicit weight tuple "
            f"'(-0.5, +1.0, -0.5)'.  Got: {label!r}"
        )
    if "raw inflation-swap-rate space" not in label_lower and "raw inflation" not in label_lower:
        failures.append(
            "Methodology label missing the raw-rate-space caveat.  "
            f"Got: {label!r}"
        )
    if "belly_zcis_pct" not in label:
        failures.append(
            "Methodology label missing the butterfly formula token "
            f"'belly_zcis_pct'.  Got: {label!r}"
        )
    if "positive" not in label_lower:
        failures.append(
            "Methodology label missing the POSITIVE sign-convention "
            f"caveat.  Got: {label!r}"
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
        if t["butterfly_bps"] is None and s["butterfly_bps"] is None:
            continue
        if t["butterfly_bps"] is None or s["butterfly_bps"] is None:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['butterfly_bps']} sql={s['butterfly_bps']}"
            )
            continue
        delta = abs(
            float(t["butterfly_bps"]) - float(s["butterfly_bps"])
        )
        if delta > TOLERANCE_BY_FIELD["butterfly_bps_row"] + 1e-12:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['butterfly_bps']} sql={s['butterfly_bps']} "
                f"(delta={delta:.6f})"
            )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the inflation_swap_butterfly tool against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("ZCIS INFLATION_SWAP_BUTTERFLY TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print(f"  total_supported_triplets (USD/EUR/GBP × C(7,3)) : "
          f"{len(REGRESSION_CASES)}")
    print("-" * 80)

    print("[1/8] Creating DB engine...")
    engine = get_db_engine()

    print("[2/8] Selecting validation cases from live ZCIS metadata...")
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases,
        lambda case: f"{case[0]} {case[1]}/{case[2]}/{case[3]}",
    )

    # Fail loudly when the default-coverage invocation produces
    # fewer than 105 cases, instead of silently passing on an
    # incomplete pool.
    if args.cases == DEFAULT_CASE_COUNT and len(cases) != DEFAULT_CASE_COUNT:
        selected = set(cases)
        missing = [c for c in REGRESSION_CASES if c not in selected]
        msg_lines = [
            "FATAL: Incomplete ZCIS butterfly coverage — live pool "
            "is missing supported pillars.",
            f"  expected : {DEFAULT_CASE_COUNT} deterministic cases "
            "(full REGRESSION_CASES list)",
            f"  observed : {len(cases)} cases returned by "
            "choose_test_cases()",
            f"  missing  : {len(missing)} "
            "(curve_family, short, belly, long) tuples — see the "
            "inflation_swaps playbook for the ingested universe "
            "(USD_ZCIS / EUR_ZCIS / GBP_ZCIS × 1Y / 2Y / 3Y / 5Y / "
            "10Y / 20Y / 30Y).",
        ]
        for c in missing:
            msg_lines.append(f"    - {c[0]} {c[1]}/{c[2]}/{c[3]}")
        full_msg = "\n".join(msg_lines)
        print(full_msg, flush=True)
        print(full_msg, file=sys.stderr, flush=True)
        sys.exit(2)

    print(
        "[3/8] Running tool vs SQL comparisons (with historical-"
        "sample cross-check)..."
    )
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]} {case[1]}/{case[2]}/{case[3]}",
        )
        curve_family, short_tenor, belly_tenor, long_tenor = case
        tool_result = calculate_inflation_swap_butterfly(
            engine=engine,
            params=InflationSwapButterflyInput(
                curve_family=curve_family,
                short_tenor=short_tenor,
                belly_tenor=belly_tenor,
                long_tenor=long_tenor,
                lookback_days=args.days,
                field_name=args.field,
            ),
        )
        sql_result = sql_baseline(
            engine=engine,
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
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

    print("[4/8] Adversarial probe 1: tenor-ordering rejected by schema...")
    probe1 = assert_tenor_ordering_rejected_by_schema()
    print("FAIL" if probe1 else "PASS")
    for f in probe1:
        print(f"  - {f}")

    print("[4/8 cont] Adversarial probe 2: distinct-tenor rejected by schema...")
    probe2 = assert_distinct_tenor_rejected_by_schema()
    print("FAIL" if probe2 else "PASS")
    for f in probe2:
        print(f"  - {f}")

    print(
        "[5/8] Adversarial probe 3: cross-curve attempt rejected by "
        "schema..."
    )
    probe3 = assert_cross_curve_attempt_rejected_by_schema()
    print("FAIL" if probe3 else "PASS")
    for f in probe3:
        print(f"  - {f}")

    print(
        "[5/8 cont] Adversarial probe 4: non-ZCIS curve_family returns "
        "controlled error..."
    )
    probe4 = assert_non_zcis_curve_family_returns_controlled_error(
        engine,
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe4 else "PASS")
    for f in probe4:
        print(f"  - {f}")

    print(
        "[6/8] Adversarial probe 5: unknown pillar yields "
        "controlled-error envelope..."
    )
    probe5 = assert_unknown_pillar_handling(
        engine,
        curve_family="USD_ZCIS",
        short_tenor="5Y",
        belly_tenor="10Y",
        long_tenor="11Y",  # unsupported tenor
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe5 else "PASS")
    for f in probe5:
        print(f"  - {f}")

    print(
        "[7/8] Adversarial probe 6: four-conjunct SELECT guard "
        "inheritance (instrument_type + pricing_type)..."
    )
    probe6 = assert_four_conjunct_select_guard_inherited(
        engine, field_name=args.field,
    )
    print("FAIL" if probe6 else "PASS")
    for f in probe6:
        print(f"  - {f}")

    print(
        "[8/8] Adversarial probe 7: methodology-label threading "
        "(raw-rate-space caveat + weight tuple on the wire)..."
    )
    probe7 = assert_methodology_label_threaded(
        engine,
        curve_family="USD_ZCIS",
        short_tenor="5Y",
        belly_tenor="10Y",
        long_tenor="30Y",
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe7 else "PASS")
    for f in probe7:
        print(f"  - {f}")

    print("Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")
    print(
        "  probe_1_tenor_ordering            : "
        f"{'PASS' if not probe1 else 'FAIL'}"
    )
    print(
        "  probe_2_distinct_tenor            : "
        f"{'PASS' if not probe2 else 'FAIL'}"
    )
    print(
        "  probe_3_cross_curve_attempt       : "
        f"{'PASS' if not probe3 else 'FAIL'}"
    )
    print(
        "  probe_4_non_zcis_curve_family     : "
        f"{'PASS' if not probe4 else 'FAIL'}"
    )
    print(
        "  probe_5_unknown_pillar            : "
        f"{'PASS' if not probe5 else 'FAIL'}"
    )
    print(
        "  probe_6_four_conjunct_guard       : "
        f"{'PASS' if not probe6 else 'FAIL'}"
    )
    print(
        "  probe_7_methodology_label_threaded: "
        f"{'PASS' if not probe7 else 'FAIL'}"
    )

    has_failure = (
        bool(failed_cases) or bool(probe1) or bool(probe2)
        or bool(probe3) or bool(probe4) or bool(probe5)
        or bool(probe6) or bool(probe7)
    )
    if has_failure:
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]} {case[1]}/{case[2]}/{case[3]} "
                    f"({len(mismatches)} mismatches)"
                )
        sys.exit(1)


if __name__ == "__main__":
    main()
