#!/usr/bin/env python3
"""
test_breakeven_curve_spread_sql_validation.py — Linker breakeven
curve-spread validator.

Validate ``calculate_breakeven_curve_spread`` against an
independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched``.  The SQL baseline
reproduces the per-trade-date breakeven-spread math without going
through any of the Python tool's helpers, so a mismatch surfaces
a real divergence in methodology rather than a shared-code
coincidence.

Key breakeven-curve-spread differences from the
forward_breakeven_simple validator (its closest sibling):

  - SQL baseline pulls FOUR underlying yield series (nominal at
    short_tenor, linker at short_tenor, nominal at long_tenor,
    linker at long_tenor), aligns on trade_date per-tenor, builds
    each endpoint breakeven independently, then applies a per-
    trade-date difference (NOT the year-weighted forward formula):

        spread_bps = long_breakeven_bps - short_breakeven_bps

  - Adversarial pollution probes (linker→nominal, nominal→linker)
    AND adversarial cross-country probes (EUR-zone, cross-currency,
    plus a symmetric slot-swap) — same shape as the
    forward_breakeven_simple validator, composed via the curve-
    spread primitive.

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \\
        tests/test_breakeven_curve_spread_sql_validation.py
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
from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread import (  # noqa: E402
    BreakevenCurveSpreadInput,
    calculate_breakeven_curve_spread,
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


# (nominal_curve_family, linker_curve_family, short_tenor, long_tenor)
Case = Tuple[str, str, str, str]

DEFAULT_CASE_COUNT = 10
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"

# Canonical regression cases — anchored to desk-recognised breakeven
# curve spreads (2s10s, 5s30s, 5s10s).  All entries below are
# confirmed same-country / same-currency against
# macro_data.instrument_master:
#   UST          (US/USD)      ↔ USD_TIPS       (US/USD)
#   UK_GILT      (UK/GBP)      ↔ GBP_LINKER     (UK/GBP)
#   FR_OAT       (France/EUR)  ↔ EUR_FR_LINKER  (France/EUR)
#   CANADA_GOVT  (Canada/CAD)  ↔ CAD_RRB        (Canada/CAD)
REGRESSION_CASES: List[Case] = [
    ("UST", "USD_TIPS", "5Y", "10Y"),       # 5s10s — desk read
    ("UK_GILT", "GBP_LINKER", "5Y", "10Y"),
    ("FR_OAT", "EUR_FR_LINKER", "5Y", "10Y"),
    ("CANADA_GOVT", "CAD_RRB", "5Y", "10Y"),
]

# Tolerances follow the breakeven_inflation_simple validator's
# precision since the bps math + display precision are the same;
# the curve spread is a single subtraction so cumulative rounding
# error is small.
TOLERANCE_BY_FIELD = {
    "current_spread_bps": 0.021,
    "daily_change_bps": 0.021,
    "weekly_change_bps": 0.021,
    "monthly_change_bps": 0.021,
    "current_z_score": 0.006,
    "high_252d_bps": 0.021,
    "low_252d_bps": 0.021,
    "percentile_252d": 0.21,
    "short_breakeven_bps": 0.011,
    "long_breakeven_bps": 0.011,
    "short_years": 1e-4,
    "long_years": 1e-4,
    "spread_bps_row": 0.021,
    "z_score_row": 0.006,
}


# Adversarial pollution probes.
LINKER_IN_NOMINAL_PROBE: Tuple[str, str] = ("USD_TIPS", "GBP_LINKER")
NOMINAL_IN_LINKER_PROBE: Tuple[str, str] = ("UST", "DE_BUND")


# Adversarial cross-country rejection probes.
CROSS_COUNTRY_EUR_PROBE: Tuple[str, str] = ("DE_BUND", "EUR_FR_LINKER")
CROSS_CURRENCY_PROBE: Tuple[str, str] = ("UK_GILT", "USD_TIPS")


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (nominal, linker, short_tenor, long_tenor) cases from
    live metadata.  Filters to same-country / same-currency pairs
    only; the spot primitive's compute layer refuses cross-country
    pairs, and this validator should only ever exercise pairs the
    tool will accept.
    """
    nominal_query = text(
        """
        SELECT
            e.curve_family,
            e.tenor,
            i.country,
            i.currency
        FROM macro_data.v_market_data_daily_enriched e
        JOIN (
            SELECT DISTINCT curve_family, instrument_type, country, currency
            FROM macro_data.instrument_master
        ) i
          ON i.curve_family    = e.curve_family
         AND i.instrument_type = e.instrument_type
        WHERE e.instrument_type = 'sovereign_benchmark'
          AND e.field_name      = :field_name
        GROUP BY e.curve_family, e.tenor, i.country, i.currency
        HAVING COUNT(*) >= 80
        """
    )
    linker_query = text(
        """
        SELECT
            e.curve_family,
            e.tenor,
            i.country,
            i.currency
        FROM macro_data.v_market_data_daily_enriched e
        JOIN (
            SELECT DISTINCT curve_family, instrument_type, country, currency
            FROM macro_data.instrument_master
        ) i
          ON i.curve_family    = e.curve_family
         AND i.instrument_type = e.instrument_type
        WHERE e.instrument_type = 'inflation_linker'
          AND e.field_name      = :field_name
        GROUP BY e.curve_family, e.tenor, i.country, i.currency
        HAVING COUNT(*) >= 80
        """
    )
    with engine.connect() as conn:
        nominal_rows = conn.execute(
            nominal_query, {"field_name": field_name},
        ).mappings().all()
        linker_rows = conn.execute(
            linker_query, {"field_name": field_name},
        ).mappings().all()

    nominal_tenors: Dict[Tuple[str, str, str], set] = {}
    for row in nominal_rows:
        key = (row["curve_family"], row["country"], row["currency"])
        nominal_tenors.setdefault(key, set()).add(row["tenor"])
    linker_tenors: Dict[Tuple[str, str, str], set] = {}
    for row in linker_rows:
        key = (row["curve_family"], row["country"], row["currency"])
        linker_tenors.setdefault(key, set()).add(row["tenor"])

    pool: List[Case] = []
    for (n_cf, n_country, n_currency), n_tenors in nominal_tenors.items():
        for (l_cf, l_country, l_currency), l_tenors in linker_tenors.items():
            if n_country != l_country or n_currency != l_currency:
                continue
            common_tenors = n_tenors & l_tenors
            if len(common_tenors) < 2:
                continue
            parsed: List[Tuple[float, str]] = []
            for t in sorted(common_tenors):
                try:
                    parsed.append((tenor_to_years(t), t))
                except ValueError:
                    continue
            parsed.sort(key=lambda x: x[0])
            for i, (ys_short, t_short) in enumerate(parsed):
                for ys_long, t_long in parsed[i + 1:]:
                    pool.append((n_cf, l_cf, t_short, t_long))

    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the breakeven curve spread
    math.

    Algorithm (mirrors the Python tool's composition shape EXACTLY):
      1. Pull two endpoint breakeven series, EACH built per-tenor
         from its own (nominal, linker) pivot+ffill+dropna — same
         shape the spot ``breakeven_inflation_simple`` primitive
         emits.  Per-tenor ffill operates on a per-tenor date grid
         (only the dates relevant to that tenor's two legs), NOT a
         multi-tenor union — otherwise the SQL ffill would bridge
         gaps across more rows than the Python spot's per-tenor
         pivot does, producing extra pre-history that diverges
         from the Python tool.
      2. Inner-join the two endpoint breakeven series on
         trade_date — same as the curve-spread primitive's outer
         alignment step.
      3. Apply the per-trade-date difference:
            spread_bps = long_breakeven_bps - short_breakeven_bps
         and round to 2 decimals (matches the curve-spread
         primitive's bps_round_decimals boundary).
      4. Compute rolling 252-day z-score, period changes, trailing
         range — all under the same convention values the Python
         tool uses.
      5. Anchor the display cutoff to the latest aligned
         trade_date (matches the Python tool's anchoring).
    """
    t_short_years = tenor_to_years(short_tenor)
    t_long_years = tenor_to_years(long_tenor)
    # Mirror the curve-spread primitive's buffer math
    # (compute._conventions_from_config + the
    # extended_lookback_days = lookback + buffer_calendar_days
    # computation in calculate_breakeven_curve_spread).  The
    # buffer is max(z_window, trailing_window) * buffer_multiplier
    # = 252 * 1.5 = 378.
    extended_lookback_days = lookback_days + 378

    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                curve_family,
                instrument_type,
                tenor,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE field_name = :field_name
              AND tenor IN (:short_tenor, :long_tenor)
              AND trade_date >= CURRENT_DATE - ((:lookback_days + 600) * INTERVAL '1 day')
              AND (
                    (curve_family   = :nominal_curve_family
                     AND instrument_type = 'sovereign_benchmark')
                 OR (curve_family   = :linker_curve_family
                     AND instrument_type = 'inflation_linker')
              )
        ),
        -- Per-tenor pivot+ffill+dropna for the SHORT leg.
        short_raw AS (
            SELECT trade_date, curve_family, field_value
            FROM raw
            WHERE tenor = :short_tenor
        ),
        short_dates AS (
            SELECT DISTINCT trade_date FROM short_raw
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
                MAX(CASE
                        WHEN sr.curve_family = :nominal_curve_family
                        THEN sr.field_value
                    END) AS n_raw,
                MAX(CASE
                        WHEN sr.curve_family = :linker_curve_family
                        THEN sr.field_value
                    END) AS l_raw
            FROM short_numbered sn
            LEFT JOIN short_raw sr ON sr.trade_date = sn.trade_date
            GROUP BY sn.trade_date, sn.srn
        ),
        short_ffill AS (
            SELECT
                *,
                MAX(CASE WHEN n_raw IS NOT NULL THEN srn END)
                    OVER (ORDER BY srn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS n_last_rn,
                MAX(CASE WHEN l_raw IS NOT NULL THEN srn END)
                    OVER (ORDER BY srn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS l_last_rn
            FROM short_joined
        ),
        short_filled AS (
            SELECT
                trade_date,
                srn,
                CASE
                    WHEN n_raw IS NOT NULL THEN n_raw
                    WHEN n_last_rn IS NOT NULL AND srn - n_last_rn <= 5
                    THEN MAX(CASE WHEN n_raw IS NOT NULL THEN n_raw END)
                         OVER (PARTITION BY n_last_rn)
                    ELSE NULL
                END AS n_short,
                CASE
                    WHEN l_raw IS NOT NULL THEN l_raw
                    WHEN l_last_rn IS NOT NULL AND srn - l_last_rn <= 5
                    THEN MAX(CASE WHEN l_raw IS NOT NULL THEN l_raw END)
                         OVER (PARTITION BY l_last_rn)
                    ELSE NULL
                END AS l_short
            FROM short_ffill
        ),
        short_be_unfiltered AS (
            SELECT
                trade_date,
                ROUND(((n_short - l_short) * 100)::numeric, 2)::double precision AS be_short_bps
            FROM short_filled
            WHERE n_short IS NOT NULL AND l_short IS NOT NULL
        ),
        short_be_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM short_be_unfiltered
        ),
        short_be AS (
            SELECT s.trade_date, s.be_short_bps
            FROM short_be_unfiltered s, short_be_anchor a
            WHERE s.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        -- Per-tenor pivot+ffill+dropna for the LONG leg.
        long_raw AS (
            SELECT trade_date, curve_family, field_value
            FROM raw
            WHERE tenor = :long_tenor
        ),
        long_dates AS (
            SELECT DISTINCT trade_date FROM long_raw
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
                MAX(CASE
                        WHEN lr.curve_family = :nominal_curve_family
                        THEN lr.field_value
                    END) AS n_raw,
                MAX(CASE
                        WHEN lr.curve_family = :linker_curve_family
                        THEN lr.field_value
                    END) AS l_raw
            FROM long_numbered ln
            LEFT JOIN long_raw lr ON lr.trade_date = ln.trade_date
            GROUP BY ln.trade_date, ln.lrn
        ),
        long_ffill AS (
            SELECT
                *,
                MAX(CASE WHEN n_raw IS NOT NULL THEN lrn END)
                    OVER (ORDER BY lrn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS n_last_rn,
                MAX(CASE WHEN l_raw IS NOT NULL THEN lrn END)
                    OVER (ORDER BY lrn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS l_last_rn
            FROM long_joined
        ),
        long_filled AS (
            SELECT
                trade_date,
                lrn,
                CASE
                    WHEN n_raw IS NOT NULL THEN n_raw
                    WHEN n_last_rn IS NOT NULL AND lrn - n_last_rn <= 5
                    THEN MAX(CASE WHEN n_raw IS NOT NULL THEN n_raw END)
                         OVER (PARTITION BY n_last_rn)
                    ELSE NULL
                END AS n_long,
                CASE
                    WHEN l_raw IS NOT NULL THEN l_raw
                    WHEN l_last_rn IS NOT NULL AND lrn - l_last_rn <= 5
                    THEN MAX(CASE WHEN l_raw IS NOT NULL THEN l_raw END)
                         OVER (PARTITION BY l_last_rn)
                    ELSE NULL
                END AS l_long
            FROM long_ffill
        ),
        long_be_unfiltered AS (
            SELECT
                trade_date,
                ROUND(((n_long - l_long) * 100)::numeric, 2)::double precision AS be_long_bps
            FROM long_filled
            WHERE n_long IS NOT NULL AND l_long IS NOT NULL
        ),
        long_be_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM long_be_unfiltered
        ),
        long_be AS (
            SELECT l.trade_date, l.be_long_bps
            FROM long_be_unfiltered l, long_be_anchor a
            WHERE l.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        aligned AS (
            SELECT
                s.trade_date,
                s.be_short_bps,
                l.be_long_bps
            FROM short_be s
            INNER JOIN long_be l ON l.trade_date = s.trade_date
        ),
        spread_rows AS (
            SELECT
                trade_date,
                be_short_bps,
                be_long_bps,
                ROUND(
                    (be_long_bps - be_short_bps)::numeric,
                    2
                )::double precision AS spread_bps
            FROM aligned
        ),
        renumbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn,
                be_short_bps,
                be_long_bps,
                spread_bps
            FROM spread_rows
        ),
        scored AS (
            SELECT
                trade_date,
                arn,
                be_short_bps,
                be_long_bps,
                spread_bps,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(spread_bps) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(spread_bps) OVER zw <> 0
                    THEN ROUND(
                        (
                            (spread_bps - AVG(spread_bps) OVER zw)
                            / NULLIF(STDDEV_SAMP(spread_bps) OVER zw, 0)
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
                be_short_bps,
                be_long_bps,
                spread_bps,
                z_score,
                CASE
                    WHEN LAG(spread_bps, 1) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 1) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(spread_bps, 5) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 5) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(spread_bps, 21) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 21) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS monthly_change_bps,
                ROUND((MAX(spread_bps) OVER tw)::numeric, 2)::double precision AS high_252d_bps,
                ROUND((MIN(spread_bps) OVER tw)::numeric, 2)::double precision AS low_252d_bps,
                CASE
                    WHEN MAX(spread_bps) OVER tw IS NULL
                      OR MIN(spread_bps) OVER tw IS NULL
                      OR MAX(spread_bps) OVER tw = MIN(spread_bps) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                spread_bps - MIN(spread_bps) OVER tw
                            ) / NULLIF(
                                MAX(spread_bps) OVER tw - MIN(spread_bps) OVER tw,
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
            be_short_bps,
            be_long_bps,
            spread_bps,
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
                "nominal_curve_family": nominal_curve_family,
                "linker_curve_family": linker_curve_family,
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
            "nominal_curve_family": nominal_curve_family,
            "linker_curve_family": linker_curve_family,
            "short_tenor": short_tenor,
            "long_tenor": long_tenor,
            "spread_label": (
                f"{nominal_curve_family}/{linker_curve_family} "
                f"{short_tenor.replace('Y', '')}s"
                f"{long_tenor.replace('Y', '')}s breakeven"
            ),
            "current_spread_bps": latest["spread_bps"],
            "daily_change_bps": latest["daily_change_bps"],
            "weekly_change_bps": latest["weekly_change_bps"],
            "monthly_change_bps": latest["monthly_change_bps"],
            "current_z_score": latest["z_score"],
            "rolling_window_days": 252,
            "high_252d_bps": latest["high_252d_bps"],
            "low_252d_bps": latest["low_252d_bps"],
            "percentile_252d": latest["percentile_252d"],
            "short_breakeven_bps": latest["be_short_bps"],
            "long_breakeven_bps": latest["be_long_bps"],
            "short_years": round(t_short_years, 4),
            "long_years": round(t_long_years, 4),
        },
        "time_series": [
            {
                "date": row["date"],
                "spread_bps": row["spread_bps"],
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
            "nominal_curve_family",
            "linker_curve_family",
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
            "current_spread_bps",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "short_breakeven_bps",
            "long_breakeven_bps",
            "short_years",
            "long_years",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    row_tolerances = dict(TOLERANCE_BY_FIELD)
    row_tolerances["spread_bps"] = TOLERANCE_BY_FIELD["spread_bps_row"]
    row_tolerances["z_score"] = TOLERANCE_BY_FIELD["z_score_row"]
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=("spread_bps", "z_score"),
            tolerances=row_tolerances,
        )
    )
    return mismatches


def assert_pollution_guard(
    engine,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    long_tenor: str,
    field_name: str,
    lookback_days: int,
    expected_missing_leg: str,
) -> List[str]:
    failures: List[str] = []

    if expected_missing_leg == "sovereign_benchmark":
        zero_check_sql = text(
            """
            SELECT COUNT(*) AS n
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'sovereign_benchmark'
              AND curve_family    = :curve_family
              AND tenor           = :tenor
              AND field_name      = :field_name
            """
        )
        check_cf = nominal_curve_family
    else:
        zero_check_sql = text(
            """
            SELECT COUNT(*) AS n
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'inflation_linker'
              AND curve_family    = :curve_family
              AND tenor           = :tenor
              AND field_name      = :field_name
            """
        )
        check_cf = linker_curve_family

    with engine.connect() as conn:
        row = conn.execute(
            zero_check_sql,
            {
                "curve_family": check_cf,
                "tenor": short_tenor,
                "field_name": field_name,
            },
        ).mappings().first()
    n_rows = int(row["n"]) if row is not None else 0
    if n_rows != 0:
        failures.append(
            f"SQL invariant violated: expected zero "
            f"instrument_type='{expected_missing_leg}' rows for "
            f"{check_cf} {short_tenor} (pollution probe), but got {n_rows}."
        )

    tool_result = calculate_breakeven_curve_spread(
        engine=engine,
        params=BreakevenCurveSpreadInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    if "error" not in tool_result:
        failures.append(
            "Python tool returned a snapshot for pollution probe "
            f"(nominal={nominal_curve_family}, linker={linker_curve_family}, "
            f"{short_tenor}/{long_tenor}) — must return controlled "
            f"error envelope.  Got keys: {sorted(tool_result.keys())}"
        )
    elif expected_missing_leg not in tool_result.get("error", ""):
        failures.append(
            f"Controlled error envelope missing the "
            f"{expected_missing_leg!r} rationale required to route "
            "the operator to the correct slot.  Got: "
            f"{tool_result['error']!r}"
        )
    return failures


def assert_cross_country_guard(
    engine,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    long_tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Adversarial cross-country probe — verify the runtime
    same-country invariant (inherited from the spot primitive)
    refuses the pair and returns a controlled error envelope
    mentioning both curve families AND the (country, currency)
    mismatch.
    """
    failures: List[str] = []

    identity_sql = text(
        """
        SELECT DISTINCT country, currency
        FROM macro_data.instrument_master
        WHERE curve_family    = :curve_family
          AND instrument_type = :instrument_type
        """
    )
    with engine.connect() as conn:
        n_rows = conn.execute(
            identity_sql,
            {
                "curve_family": nominal_curve_family,
                "instrument_type": "sovereign_benchmark",
            },
        ).mappings().all()
        l_rows = conn.execute(
            identity_sql,
            {
                "curve_family": linker_curve_family,
                "instrument_type": "inflation_linker",
            },
        ).mappings().all()
    if len(n_rows) != 1 or len(l_rows) != 1:
        failures.append(
            "Cross-country probe pre-condition violated: expected "
            "exactly one (country, currency) row per leg in "
            f"instrument_master.  Got nominal={n_rows!r}, "
            f"linker={l_rows!r}."
        )
        return failures

    n_country, n_currency = n_rows[0]["country"], n_rows[0]["currency"]
    l_country, l_currency = l_rows[0]["country"], l_rows[0]["currency"]
    if n_country == l_country and n_currency == l_currency:
        failures.append(
            "Cross-country probe pre-condition violated: pair "
            f"({nominal_curve_family} vs {linker_curve_family}) is "
            "same-country in instrument_master — choose a different "
            "probe."
        )
        return failures

    tool_result = calculate_breakeven_curve_spread(
        engine=engine,
        params=BreakevenCurveSpreadInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )

    keys = sorted(tool_result.keys())
    if keys != ["error"]:
        failures.append(
            "Cross-country pair was not refused: tool returned keys "
            f"{keys!r} for nominal={nominal_curve_family}, "
            f"linker={linker_curve_family}, "
            f"{short_tenor}/{long_tenor}.  Expected exactly the "
            "controlled-error envelope {'error': ...}."
        )
        return failures

    error_text = tool_result["error"]
    if nominal_curve_family not in error_text:
        failures.append(
            f"Controlled error envelope missing nominal curve_family "
            f"{nominal_curve_family!r}.  Got: {error_text!r}"
        )
    if linker_curve_family not in error_text:
        failures.append(
            f"Controlled error envelope missing linker curve_family "
            f"{linker_curve_family!r}.  Got: {error_text!r}"
        )
    for needle in (n_country, n_currency, l_country, l_currency):
        if needle not in error_text:
            failures.append(
                f"Controlled error envelope missing identity field "
                f"{needle!r}.  Got: {error_text!r}"
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
        if t["spread_bps"] is None and s["spread_bps"] is None:
            continue
        if t["spread_bps"] is None or s["spread_bps"] is None:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['spread_bps']} sql={s['spread_bps']}"
            )
            continue
        delta = abs(
            float(t["spread_bps"]) - float(s["spread_bps"])
        )
        if delta > TOLERANCE_BY_FIELD["spread_bps_row"] + 1e-12:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['spread_bps']} sql={s['spread_bps']} "
                f"(delta={delta:.6f})"
            )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the linker breakeven_curve_spread tool against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("LINKER BREAKEVEN CURVE SPREAD TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/5] Creating DB engine...")
    engine = get_db_engine()

    print("[2/5] Selecting validation cases from live nominal+linker metadata...")
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases,
        lambda case: f"{case[0]} vs {case[1]} @ {case[2]}/{case[3]}",
    )

    print("[3/5] Running tool vs SQL comparisons (with historical-sample cross-check)...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]} vs {case[1]} @ {case[2]}/{case[3]}",
        )
        nominal_cf, linker_cf, short_tenor, long_tenor = case
        tool_result = calculate_breakeven_curve_spread(
            engine=engine,
            params=BreakevenCurveSpreadInput(
                nominal_curve_family=nominal_cf,
                linker_curve_family=linker_cf,
                short_tenor=short_tenor,
                long_tenor=long_tenor,
                lookback_days=args.days,
                field_name=args.field,
            ),
        )
        sql_result = sql_baseline(
            engine=engine,
            nominal_curve_family=nominal_cf,
            linker_curve_family=linker_cf,
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
        "[4/5] Adversarial pollution probes (linker→nominal slot, nominal→linker slot)..."
    )
    linker_in_nominal_failures = assert_pollution_guard(
        engine,
        nominal_curve_family=LINKER_IN_NOMINAL_PROBE[0],   # USD_TIPS in nominal slot
        linker_curve_family=LINKER_IN_NOMINAL_PROBE[1],    # GBP_LINKER in linker slot
        short_tenor="2Y",
        long_tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
        expected_missing_leg="sovereign_benchmark",
    )
    print(
        "  linker→nominal probe ({} as nominal, {} as linker): {}".format(
            LINKER_IN_NOMINAL_PROBE[0],
            LINKER_IN_NOMINAL_PROBE[1],
            "PASS" if not linker_in_nominal_failures else "FAIL",
        )
    )
    for f in linker_in_nominal_failures:
        print(f"    - {f}")

    nominal_in_linker_failures = assert_pollution_guard(
        engine,
        nominal_curve_family=NOMINAL_IN_LINKER_PROBE[0],   # UST in nominal slot
        linker_curve_family=NOMINAL_IN_LINKER_PROBE[1],    # DE_BUND in linker slot
        short_tenor="2Y",
        long_tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
        expected_missing_leg="inflation_linker",
    )
    print(
        "  nominal→linker probe ({} as nominal, {} as linker): {}".format(
            NOMINAL_IN_LINKER_PROBE[0],
            NOMINAL_IN_LINKER_PROBE[1],
            "PASS" if not nominal_in_linker_failures else "FAIL",
        )
    )
    for f in nominal_in_linker_failures:
        print(f"    - {f}")

    print(
        "[5/5] Adversarial cross-country rejection probes "
        "(EUR-zone, cross-currency, both directions)..."
    )
    eur_failures = assert_cross_country_guard(
        engine,
        nominal_curve_family=CROSS_COUNTRY_EUR_PROBE[0],   # DE_BUND (Germany/EUR)
        linker_curve_family=CROSS_COUNTRY_EUR_PROBE[1],    # EUR_FR_LINKER (France/EUR)
        short_tenor="2Y",
        long_tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
    )
    print(
        "  EUR-zone cross-country probe ({} as nominal, {} as linker): {}".format(
            CROSS_COUNTRY_EUR_PROBE[0],
            CROSS_COUNTRY_EUR_PROBE[1],
            "PASS" if not eur_failures else "FAIL",
        )
    )
    for f in eur_failures:
        print(f"    - {f}")

    cross_currency_failures = assert_cross_country_guard(
        engine,
        nominal_curve_family=CROSS_CURRENCY_PROBE[0],      # UK_GILT (UK/GBP)
        linker_curve_family=CROSS_CURRENCY_PROBE[1],       # USD_TIPS (US/USD)
        short_tenor="2Y",
        long_tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
    )
    print(
        "  Cross-currency probe ({} as nominal, {} as linker): {}".format(
            CROSS_CURRENCY_PROBE[0],
            CROSS_CURRENCY_PROBE[1],
            "PASS" if not cross_currency_failures else "FAIL",
        )
    )
    for f in cross_currency_failures:
        print(f"    - {f}")

    # Symmetric direction for the cross-currency probe.
    swap_nominal, swap_linker = (
        CROSS_CURRENCY_PROBE[1],   # USD_TIPS in nominal slot
        CROSS_CURRENCY_PROBE[0],   # UK_GILT in linker slot
    )
    swap_result = calculate_breakeven_curve_spread(
        engine=engine,
        params=BreakevenCurveSpreadInput(
            nominal_curve_family=swap_nominal,
            linker_curve_family=swap_linker,
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=args.days,
            field_name=args.field,
        ),
    )
    sym_failures: List[str] = []
    if sorted(swap_result.keys()) != ["error"]:
        sym_failures.append(
            "Symmetric (slot-swapped) cross-currency probe was not "
            f"refused: tool returned keys {sorted(swap_result.keys())!r} "
            f"for nominal={swap_nominal}, linker={swap_linker}.  "
            "Expected exactly the controlled-error envelope "
            "{'error': ...}."
        )
    else:
        err_text = swap_result["error"]
        if swap_nominal not in err_text:
            sym_failures.append(
                f"Symmetric probe error envelope missing nominal "
                f"curve_family {swap_nominal!r}.  Got: {err_text!r}"
            )
    print(
        "  Cross-currency symmetric probe ({} as nominal, {} as linker): {}".format(
            swap_nominal,
            swap_linker,
            "PASS" if not sym_failures else "FAIL",
        )
    )
    for f in sym_failures:
        print(f"    - {f}")

    print("Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")
    print(
        f"  linker→nominal probe: "
        f"{'PASS' if not linker_in_nominal_failures else 'FAIL'}"
    )
    print(
        f"  nominal→linker probe: "
        f"{'PASS' if not nominal_in_linker_failures else 'FAIL'}"
    )
    print(
        f"  EUR-zone cross-country probe: "
        f"{'PASS' if not eur_failures else 'FAIL'}"
    )
    print(
        f"  cross-currency probe: "
        f"{'PASS' if not cross_currency_failures else 'FAIL'}"
    )
    print(
        f"  cross-currency symmetric probe: "
        f"{'PASS' if not sym_failures else 'FAIL'}"
    )

    if (
        failed_cases
        or linker_in_nominal_failures
        or nominal_in_linker_failures
        or eur_failures
        or cross_currency_failures
        or sym_failures
    ):
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]} vs {case[1]} @ {case[2]}/{case[3]} "
                    f"({len(mismatches)} mismatches)"
                )
        if linker_in_nominal_failures:
            print("\nLINKER→NOMINAL PROBE FAILURES:")
            for f in linker_in_nominal_failures:
                print(f"  - {f}")
        if nominal_in_linker_failures:
            print("\nNOMINAL→LINKER PROBE FAILURES:")
            for f in nominal_in_linker_failures:
                print(f"  - {f}")
        if eur_failures:
            print("\nEUR-ZONE CROSS-COUNTRY PROBE FAILURES:")
            for f in eur_failures:
                print(f"  - {f}")
        if cross_currency_failures:
            print("\nCROSS-CURRENCY PROBE FAILURES:")
            for f in cross_currency_failures:
                print(f"  - {f}")
        if sym_failures:
            print("\nCROSS-CURRENCY SYMMETRIC PROBE FAILURES:")
            for f in sym_failures:
                print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
