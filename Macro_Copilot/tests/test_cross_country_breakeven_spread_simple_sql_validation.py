#!/usr/bin/env python3
"""
test_cross_country_breakeven_spread_simple_sql_validation.py — Linker
cross-country breakeven spread validator.

Validate ``calculate_cross_country_breakeven_spread_simple`` against
an independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched`` and
``macro_data.instrument_master``.  The SQL baseline reproduces the
per-trade-date cross-country breakeven-spread math without going
through any of the Python tool's helpers, so a mismatch surfaces a
real divergence in methodology rather than a shared-code coincidence.

Key cross-country differences from the breakeven_curve_spread
validator (its closest sibling):

  - SQL baseline pulls FOUR underlying yield series (country_a
    nominal at tenor, country_a linker at tenor, country_b nominal
    at tenor, country_b linker at tenor), aligns each country's two
    legs per-tenor + ffill, builds each country's breakeven
    independently, then applies a per-trade-date inner-join across
    the country pair and difference (NOT a forward / curve formula):

        spread_bps = breakeven_a_bps - breakeven_b_bps

  - Adversarial probes target the cross-country shape of this
    primitive:
      1. Same-country input pollution (both pairs identical) — must
         fail at the schema layer before any market-data SELECT.
      2. Leg-A internal cross-country pollution
         (country_a_nominal=UST + country_a_linker=EUR_FR_LINKER) —
         must fail inside the spot primitive's same-country
         invariant before any market-data SELECT.
      3. Leg-B internal cross-country pollution — symmetric.
      4. Missing leg-A breakeven series — controlled error envelope
         attributed to country_a.
      5. Missing leg-B breakeven series — controlled error envelope
         attributed to country_b.

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \\
        tests/test_cross_country_breakeven_spread_simple_sql_validation.py
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
from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (  # noqa: E402
    CrossCountryBreakevenSpreadSimpleInput,
    calculate_cross_country_breakeven_spread_simple,
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


# (country_a_nominal_pair, country_a_linker_pair,
#  country_b_nominal_pair, country_b_linker_pair, tenor)
Case = Tuple[str, str, str, str, str]

DEFAULT_CASE_COUNT = 10
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"

# Canonical regression cases — anchored to desk-recognised
# cross-country breakeven differentials at multiple tenors and
# countries (USD vs EUR-FR, USD vs GBP, EUR-DE vs EUR-FR where data
# exists).  All entries below are confirmed cross-country pairs in
# instrument_master — the runner filters out same-country pairs
# upstream.
REGRESSION_CASES: List[Case] = [
    # USD vs EUR-FR at multiple tenors
    ("UST", "USD_TIPS", "FR_OAT", "EUR_FR_LINKER", "5Y"),
    ("UST", "USD_TIPS", "FR_OAT", "EUR_FR_LINKER", "10Y"),
    # USD vs GBP at multiple tenors
    ("UST", "USD_TIPS", "UK_GILT", "GBP_LINKER", "5Y"),
    ("UST", "USD_TIPS", "UK_GILT", "GBP_LINKER", "10Y"),
    # EUR-zone cross-country pair at 10Y (Germany vs France) —
    # only landed if EUR_DE_LINKER is in the universe.
    ("DE_BUND", "EUR_DE_LINKER", "FR_OAT", "EUR_FR_LINKER", "10Y"),
]

# Tolerances follow the breakeven_curve_spread validator's precision
# since the bps math + display precision are the same; the cross-
# country spread is a single subtraction of two pre-rounded country
# breakevens so cumulative rounding error is small.
TOLERANCE_BY_FIELD = {
    "current_spread_bps": 0.021,
    "daily_change_bps": 0.021,
    "weekly_change_bps": 0.021,
    "monthly_change_bps": 0.021,
    "current_z_score": 0.006,
    "high_252d_bps": 0.021,
    "low_252d_bps": 0.021,
    "percentile_252d": 0.21,
    "breakeven_a_bps": 0.011,
    "breakeven_b_bps": 0.011,
    "tenor_years": 1e-4,
    "spread_bps_row": 0.021,
    "z_score_row": 0.006,
}


# Adversarial probes.

# (1) Same-country input pollution — both legs identical (must fail
# at the schema layer BEFORE any market-data SELECT, no DB access
# needed).
SAME_COUNTRY_INPUT_PROBE: Tuple[str, str, str, str] = (
    "UST", "USD_TIPS", "UST", "USD_TIPS",
)

# (2) Leg-A internal cross-country pollution — country_a_nominal=UST
# (US/USD) + country_a_linker=EUR_FR_LINKER (France/EUR).  Country_b
# must be a valid same-country pair AND have a different nominal AND
# a different linker than country_a (per the schema validators), so
# we use UK_GILT (UK/GBP) + GBP_LINKER (UK/GBP).  Must fail inside
# the spot primitive's same-country invariant for country_a's leg
# BEFORE any market-data SELECT.
LEG_A_POLLUTION_PROBE: Tuple[str, str, str, str] = (
    "UST", "EUR_FR_LINKER", "UK_GILT", "GBP_LINKER",
)

# (3) Leg-B internal cross-country pollution — country_b_nominal=
# UK_GILT (UK/GBP) + country_b_linker=EUR_FR_LINKER (France/EUR).
# Country_a is valid (UST + USD_TIPS, US/USD), distinct from
# country_b across BOTH nominal (UST vs UK_GILT) and linker
# (USD_TIPS vs EUR_FR_LINKER).  Symmetric to (2).
LEG_B_POLLUTION_PROBE: Tuple[str, str, str, str] = (
    "UST", "USD_TIPS", "UK_GILT", "EUR_FR_LINKER",
)


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (country_a_nominal, country_a_linker, country_b_nominal,
    country_b_linker, tenor) cases from live metadata.  Filters to
    valid SAME-COUNTRY (nominal, linker) pairs per leg, then
    cross-couples DIFFERENT-country pairs — exactly the input shape
    the tool accepts.
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

    # Build same-country (nominal, linker) pairs first — the per-leg
    # same-country invariant only accepts these.
    same_country_pairs: Dict[
        Tuple[str, str], Tuple[str, str, set]
    ] = {}
    for (n_cf, n_country, n_currency), n_tenors in nominal_tenors.items():
        for (l_cf, l_country, l_currency), l_tenors in linker_tenors.items():
            if n_country != l_country or n_currency != l_currency:
                continue
            common_tenors = n_tenors & l_tenors
            if not common_tenors:
                continue
            same_country_pairs[(n_cf, l_cf)] = (
                n_country, n_currency, common_tenors,
            )

    # Cross-couple different-country pairs at common tenors.
    pool: List[Case] = []
    pair_items = sorted(same_country_pairs.items())
    for i, ((an, al), (an_country, an_currency, an_tenors)) in enumerate(
        pair_items,
    ):
        for (bn, bl), (bn_country, bn_currency, bn_tenors) in pair_items[i + 1:]:
            if an_country == bn_country and an_currency == bn_currency:
                continue
            common_xc_tenors = an_tenors & bn_tenors
            for t in sorted(common_xc_tenors):
                try:
                    tenor_to_years(t)
                except ValueError:
                    continue
                pool.append((an, al, bn, bl, t))

    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    country_a_nominal_pair: str,
    country_a_linker_pair: str,
    country_b_nominal_pair: str,
    country_b_linker_pair: str,
    tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the cross-country breakeven
    spread math.

    Algorithm (mirrors the Python tool's composition shape EXACTLY):
      1. Pull two country breakeven series, EACH built per-country
         from its own (nominal, linker) pivot+ffill+dropna — same
         shape the spot ``breakeven_inflation_simple`` primitive
         emits.  Per-country ffill operates on a per-country date
         grid (only the dates relevant to that country's two legs),
         NOT a multi-country union — otherwise the SQL ffill would
         bridge gaps across more rows than the Python spot's per-
         country pivot does.
      2. Inner-join the two country breakeven series on trade_date
         — same as the cross-country tool's outer alignment step.
      3. Apply the per-trade-date difference:
            spread_bps = breakeven_a_bps - breakeven_b_bps
         and round to 2 decimals (matches the cross-country tool's
         bps_round_decimals boundary).
      4. Compute rolling 252-day z-score, period changes, trailing
         range — all under the same convention values the Python
         tool uses.
      5. Anchor the display cutoff to the latest aligned
         trade_date (matches the Python tool's anchoring).
    """
    tenor_years = tenor_to_years(tenor)
    # Mirror the cross-country primitive's buffer math
    # (compute._conventions_from_config + the
    # extended_lookback_days = lookback + buffer_calendar_days
    # computation in calculate_cross_country_breakeven_spread_simple).
    # The buffer is max(z_window, trailing_window) * buffer_multiplier
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
              AND tenor       = :tenor
              AND trade_date >= CURRENT_DATE - ((:lookback_days + 600) * INTERVAL '1 day')
              AND (
                    (curve_family   = :country_a_nominal_pair
                     AND instrument_type = 'sovereign_benchmark')
                 OR (curve_family   = :country_a_linker_pair
                     AND instrument_type = 'inflation_linker')
                 OR (curve_family   = :country_b_nominal_pair
                     AND instrument_type = 'sovereign_benchmark')
                 OR (curve_family   = :country_b_linker_pair
                     AND instrument_type = 'inflation_linker')
              )
        ),
        -- Per-country pivot+ffill+dropna for COUNTRY_A.
        a_raw AS (
            SELECT trade_date, curve_family, field_value
            FROM raw
            WHERE curve_family IN (
                :country_a_nominal_pair, :country_a_linker_pair
            )
        ),
        a_dates AS (
            SELECT DISTINCT trade_date FROM a_raw
        ),
        a_numbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn
            FROM a_dates
        ),
        a_joined AS (
            SELECT
                an.trade_date,
                an.arn,
                MAX(CASE
                        WHEN ar.curve_family = :country_a_nominal_pair
                        THEN ar.field_value
                    END) AS n_raw,
                MAX(CASE
                        WHEN ar.curve_family = :country_a_linker_pair
                        THEN ar.field_value
                    END) AS l_raw
            FROM a_numbered an
            LEFT JOIN a_raw ar ON ar.trade_date = an.trade_date
            GROUP BY an.trade_date, an.arn
        ),
        a_ffill AS (
            SELECT
                *,
                MAX(CASE WHEN n_raw IS NOT NULL THEN arn END)
                    OVER (ORDER BY arn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS n_last_rn,
                MAX(CASE WHEN l_raw IS NOT NULL THEN arn END)
                    OVER (ORDER BY arn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS l_last_rn
            FROM a_joined
        ),
        a_filled AS (
            SELECT
                trade_date,
                arn,
                CASE
                    WHEN n_raw IS NOT NULL THEN n_raw
                    WHEN n_last_rn IS NOT NULL AND arn - n_last_rn <= 5
                    THEN MAX(CASE WHEN n_raw IS NOT NULL THEN n_raw END)
                         OVER (PARTITION BY n_last_rn)
                    ELSE NULL
                END AS n_a,
                CASE
                    WHEN l_raw IS NOT NULL THEN l_raw
                    WHEN l_last_rn IS NOT NULL AND arn - l_last_rn <= 5
                    THEN MAX(CASE WHEN l_raw IS NOT NULL THEN l_raw END)
                         OVER (PARTITION BY l_last_rn)
                    ELSE NULL
                END AS l_a
            FROM a_ffill
        ),
        a_be_unfiltered AS (
            SELECT
                trade_date,
                ROUND(((n_a - l_a) * 100)::numeric, 2)::double precision AS be_a_bps
            FROM a_filled
            WHERE n_a IS NOT NULL AND l_a IS NOT NULL
        ),
        a_be_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM a_be_unfiltered
        ),
        a_be AS (
            SELECT s.trade_date, s.be_a_bps
            FROM a_be_unfiltered s, a_be_anchor an
            WHERE s.trade_date >= an.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        -- Per-country pivot+ffill+dropna for COUNTRY_B.
        b_raw AS (
            SELECT trade_date, curve_family, field_value
            FROM raw
            WHERE curve_family IN (
                :country_b_nominal_pair, :country_b_linker_pair
            )
        ),
        b_dates AS (
            SELECT DISTINCT trade_date FROM b_raw
        ),
        b_numbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS brn
            FROM b_dates
        ),
        b_joined AS (
            SELECT
                bn.trade_date,
                bn.brn,
                MAX(CASE
                        WHEN br.curve_family = :country_b_nominal_pair
                        THEN br.field_value
                    END) AS n_raw,
                MAX(CASE
                        WHEN br.curve_family = :country_b_linker_pair
                        THEN br.field_value
                    END) AS l_raw
            FROM b_numbered bn
            LEFT JOIN b_raw br ON br.trade_date = bn.trade_date
            GROUP BY bn.trade_date, bn.brn
        ),
        b_ffill AS (
            SELECT
                *,
                MAX(CASE WHEN n_raw IS NOT NULL THEN brn END)
                    OVER (ORDER BY brn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS n_last_rn,
                MAX(CASE WHEN l_raw IS NOT NULL THEN brn END)
                    OVER (ORDER BY brn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS l_last_rn
            FROM b_joined
        ),
        b_filled AS (
            SELECT
                trade_date,
                brn,
                CASE
                    WHEN n_raw IS NOT NULL THEN n_raw
                    WHEN n_last_rn IS NOT NULL AND brn - n_last_rn <= 5
                    THEN MAX(CASE WHEN n_raw IS NOT NULL THEN n_raw END)
                         OVER (PARTITION BY n_last_rn)
                    ELSE NULL
                END AS n_b,
                CASE
                    WHEN l_raw IS NOT NULL THEN l_raw
                    WHEN l_last_rn IS NOT NULL AND brn - l_last_rn <= 5
                    THEN MAX(CASE WHEN l_raw IS NOT NULL THEN l_raw END)
                         OVER (PARTITION BY l_last_rn)
                    ELSE NULL
                END AS l_b
            FROM b_ffill
        ),
        b_be_unfiltered AS (
            SELECT
                trade_date,
                ROUND(((n_b - l_b) * 100)::numeric, 2)::double precision AS be_b_bps
            FROM b_filled
            WHERE n_b IS NOT NULL AND l_b IS NOT NULL
        ),
        b_be_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM b_be_unfiltered
        ),
        b_be AS (
            SELECT s.trade_date, s.be_b_bps
            FROM b_be_unfiltered s, b_be_anchor an
            WHERE s.trade_date >= an.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        aligned AS (
            SELECT
                a.trade_date,
                a.be_a_bps,
                b.be_b_bps
            FROM a_be a
            INNER JOIN b_be b ON b.trade_date = a.trade_date
        ),
        spread_rows AS (
            SELECT
                trade_date,
                be_a_bps,
                be_b_bps,
                ROUND(
                    (be_a_bps - be_b_bps)::numeric,
                    2
                )::double precision AS spread_bps
            FROM aligned
        ),
        renumbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS xrn,
                be_a_bps,
                be_b_bps,
                spread_bps
            FROM spread_rows
        ),
        scored AS (
            SELECT
                trade_date,
                xrn,
                be_a_bps,
                be_b_bps,
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
                ORDER BY xrn
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
                xrn,
                be_a_bps,
                be_b_bps,
                spread_bps,
                z_score,
                CASE
                    WHEN LAG(spread_bps, 1) OVER (ORDER BY xrn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 1) OVER (ORDER BY xrn))::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(spread_bps, 5) OVER (ORDER BY xrn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 5) OVER (ORDER BY xrn))::numeric,
                        2
                    )::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(spread_bps, 21) OVER (ORDER BY xrn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 21) OVER (ORDER BY xrn))::numeric,
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
                ORDER BY xrn
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
            )
        )
        SELECT
            TO_CHAR(trade_date, 'YYYY-MM-DD') AS date,
            be_a_bps,
            be_b_bps,
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
                "country_a_nominal_pair": country_a_nominal_pair,
                "country_a_linker_pair": country_a_linker_pair,
                "country_b_nominal_pair": country_b_nominal_pair,
                "country_b_linker_pair": country_b_linker_pair,
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
            "country_a_nominal_pair": country_a_nominal_pair,
            "country_a_linker_pair": country_a_linker_pair,
            "country_b_nominal_pair": country_b_nominal_pair,
            "country_b_linker_pair": country_b_linker_pair,
            "tenor": tenor,
            "spread_label": (
                f"{country_a_nominal_pair}/{country_a_linker_pair} - "
                f"{country_b_nominal_pair}/{country_b_linker_pair} "
                f"{tenor} XC breakeven"
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
            "breakeven_a_bps": latest["be_a_bps"],
            "breakeven_b_bps": latest["be_b_bps"],
            "tenor_years": round(tenor_years, 4),
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
            "country_a_nominal_pair",
            "country_a_linker_pair",
            "country_b_nominal_pair",
            "country_b_linker_pair",
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
            "current_spread_bps",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "breakeven_a_bps",
            "breakeven_b_bps",
            "tenor_years",
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


def assert_same_country_input_refused(
    *,
    country_a_nominal_pair: str,
    country_a_linker_pair: str,
    country_b_nominal_pair: str,
    country_b_linker_pair: str,
    tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Adversarial probe (1) — both legs identical.  Schema-layer
    rejection BEFORE any DB access; we don't even need an engine.
    """
    failures: List[str] = []
    try:
        CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair=country_a_nominal_pair,
            country_a_linker_pair=country_a_linker_pair,
            country_b_nominal_pair=country_b_nominal_pair,
            country_b_linker_pair=country_b_linker_pair,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        msg = str(exc)
        if "country_a_nominal_pair" not in msg:
            failures.append(
                "Same-country input probe error message missing "
                "'country_a_nominal_pair' rationale.  Got: "
                f"{msg!r}"
            )
        return failures
    failures.append(
        "Same-country input probe was NOT refused: "
        "CrossCountryBreakevenSpreadSimpleInput accepted "
        f"({country_a_nominal_pair}/{country_a_linker_pair} vs "
        f"{country_b_nominal_pair}/{country_b_linker_pair}).  "
        "Expected a schema-layer ValidationError before any "
        "compute work fires."
    )
    return failures


def assert_leg_pollution_refused(
    engine,
    *,
    country_a_nominal_pair: str,
    country_a_linker_pair: str,
    country_b_nominal_pair: str,
    country_b_linker_pair: str,
    tenor: str,
    field_name: str,
    lookback_days: int,
    expected_failing_leg: str,  # "country_a" or "country_b"
) -> List[str]:
    """Adversarial probes (2) and (3) — leg-internal cross-country
    pollution.  The spot primitive's same-country invariant must
    refuse the polluted leg BEFORE any market-data SELECT, and the
    cross-country tool must surface that with a leg-attributed
    prefix.
    """
    failures: List[str] = []

    tool_result = calculate_cross_country_breakeven_spread_simple(
        engine=engine,
        params=CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair=country_a_nominal_pair,
            country_a_linker_pair=country_a_linker_pair,
            country_b_nominal_pair=country_b_nominal_pair,
            country_b_linker_pair=country_b_linker_pair,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    keys = sorted(tool_result.keys())
    if keys != ["error"]:
        failures.append(
            "Leg pollution probe was not refused: tool returned "
            f"keys {keys!r} for "
            f"{country_a_nominal_pair}/{country_a_linker_pair} vs "
            f"{country_b_nominal_pair}/{country_b_linker_pair} @ "
            f"{tenor}.  Expected exactly the controlled-error "
            "envelope {'error': ...}."
        )
        return failures

    err = tool_result["error"]
    if f"{expected_failing_leg} leg failed" not in err:
        failures.append(
            f"Leg pollution probe error envelope missing the "
            f"'{expected_failing_leg} leg failed' prefix required to "
            "route the operator to the correct leg.  Got: "
            f"{err!r}"
        )
    if "same-country" not in err.lower():
        failures.append(
            "Leg pollution probe error envelope missing inner "
            "same-country invariant rationale.  Got: "
            f"{err!r}"
        )
    return failures


def assert_missing_leg_a_breakeven_refused(
    engine,
    *,
    country_a_nominal_pair: str,
    country_a_linker_pair: str,
    country_b_nominal_pair: str,
    country_b_linker_pair: str,
    tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Adversarial probe (4) — country_a leg has a valid (nominal,
    linker) pair under the same-country invariant but the linker
    series at the requested tenor is empty.  Spot primitive must
    surface the no-rows error envelope; cross-country tool must
    attribute it to country_a."""
    failures: List[str] = []

    # Pre-condition: confirm the country_a linker has zero rows at
    # the requested tenor and field.
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
    with engine.connect() as conn:
        row = conn.execute(
            zero_check_sql,
            {
                "curve_family": country_a_linker_pair,
                "tenor": tenor,
                "field_name": field_name,
            },
        ).mappings().first()
    if int(row["n"]) != 0:
        failures.append(
            "Missing-leg-a probe pre-condition violated: expected "
            "zero linker rows for "
            f"{country_a_linker_pair}/{tenor}/{field_name}, got "
            f"{int(row['n'])}.  Choose a different probe."
        )
        return failures

    tool_result = calculate_cross_country_breakeven_spread_simple(
        engine=engine,
        params=CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair=country_a_nominal_pair,
            country_a_linker_pair=country_a_linker_pair,
            country_b_nominal_pair=country_b_nominal_pair,
            country_b_linker_pair=country_b_linker_pair,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    keys = sorted(tool_result.keys())
    if keys != ["error"]:
        failures.append(
            "Missing-leg-a probe was not refused: tool returned "
            f"keys {keys!r}.  Expected the controlled-error "
            "envelope {'error': ...}."
        )
        return failures
    if "country_a leg failed" not in tool_result["error"]:
        failures.append(
            "Missing-leg-a probe error envelope missing the "
            "'country_a leg failed' prefix.  Got: "
            f"{tool_result['error']!r}"
        )
    return failures


def assert_missing_leg_b_breakeven_refused(
    engine,
    *,
    country_a_nominal_pair: str,
    country_a_linker_pair: str,
    country_b_nominal_pair: str,
    country_b_linker_pair: str,
    tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Adversarial probe (5) — symmetric to (4) for country_b."""
    failures: List[str] = []

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
    with engine.connect() as conn:
        row = conn.execute(
            zero_check_sql,
            {
                "curve_family": country_b_linker_pair,
                "tenor": tenor,
                "field_name": field_name,
            },
        ).mappings().first()
    if int(row["n"]) != 0:
        failures.append(
            "Missing-leg-b probe pre-condition violated: expected "
            "zero linker rows for "
            f"{country_b_linker_pair}/{tenor}/{field_name}, got "
            f"{int(row['n'])}.  Choose a different probe."
        )
        return failures

    tool_result = calculate_cross_country_breakeven_spread_simple(
        engine=engine,
        params=CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair=country_a_nominal_pair,
            country_a_linker_pair=country_a_linker_pair,
            country_b_nominal_pair=country_b_nominal_pair,
            country_b_linker_pair=country_b_linker_pair,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    keys = sorted(tool_result.keys())
    if keys != ["error"]:
        failures.append(
            "Missing-leg-b probe was not refused: tool returned "
            f"keys {keys!r}.  Expected the controlled-error "
            "envelope {'error': ...}."
        )
        return failures
    if "country_b leg failed" not in tool_result["error"]:
        failures.append(
            "Missing-leg-b probe error envelope missing the "
            "'country_b leg failed' prefix.  Got: "
            f"{tool_result['error']!r}"
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
            "Validate the linker cross_country_breakeven_spread_simple "
            "tool against direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print(
        "LINKER CROSS-COUNTRY BREAKEVEN SPREAD TOOL — SQL VALIDATION"
    )
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/5] Creating DB engine...")
    engine = get_db_engine()

    print(
        "[2/5] Selecting validation cases from live nominal+linker "
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
        lambda case: f"{case[0]}/{case[1]} vs {case[2]}/{case[3]} @ {case[4]}",
    )

    print(
        "[3/5] Running tool vs SQL comparisons (with historical-"
        "sample cross-check)..."
    )
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]}/{case[1]} vs {case[2]}/{case[3]} @ {case[4]}",
        )
        an, al, bn, bl, tenor = case
        tool_result = calculate_cross_country_breakeven_spread_simple(
            engine=engine,
            params=CrossCountryBreakevenSpreadSimpleInput(
                country_a_nominal_pair=an,
                country_a_linker_pair=al,
                country_b_nominal_pair=bn,
                country_b_linker_pair=bl,
                tenor=tenor,
                lookback_days=args.days,
                field_name=args.field,
            ),
        )
        sql_result = sql_baseline(
            engine=engine,
            country_a_nominal_pair=an,
            country_a_linker_pair=al,
            country_b_nominal_pair=bn,
            country_b_linker_pair=bl,
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
        "[4/5] Adversarial probes (same-country input, leg-internal "
        "cross-country pollution, both legs)..."
    )

    same_country_failures = assert_same_country_input_refused(
        country_a_nominal_pair=SAME_COUNTRY_INPUT_PROBE[0],
        country_a_linker_pair=SAME_COUNTRY_INPUT_PROBE[1],
        country_b_nominal_pair=SAME_COUNTRY_INPUT_PROBE[2],
        country_b_linker_pair=SAME_COUNTRY_INPUT_PROBE[3],
        tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
    )
    print(
        "  (1) same-country input probe ({}/{} vs {}/{}): {}".format(
            *SAME_COUNTRY_INPUT_PROBE,
            "PASS" if not same_country_failures else "FAIL",
        )
    )
    for f in same_country_failures:
        print(f"    - {f}")

    leg_a_failures = assert_leg_pollution_refused(
        engine,
        country_a_nominal_pair=LEG_A_POLLUTION_PROBE[0],
        country_a_linker_pair=LEG_A_POLLUTION_PROBE[1],
        country_b_nominal_pair=LEG_A_POLLUTION_PROBE[2],
        country_b_linker_pair=LEG_A_POLLUTION_PROBE[3],
        tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
        expected_failing_leg="country_a",
    )
    print(
        "  (2) leg-A pollution probe ({}/{} vs {}/{}): {}".format(
            *LEG_A_POLLUTION_PROBE,
            "PASS" if not leg_a_failures else "FAIL",
        )
    )
    for f in leg_a_failures:
        print(f"    - {f}")

    leg_b_failures = assert_leg_pollution_refused(
        engine,
        country_a_nominal_pair=LEG_B_POLLUTION_PROBE[0],
        country_a_linker_pair=LEG_B_POLLUTION_PROBE[1],
        country_b_nominal_pair=LEG_B_POLLUTION_PROBE[2],
        country_b_linker_pair=LEG_B_POLLUTION_PROBE[3],
        tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
        expected_failing_leg="country_b",
    )
    print(
        "  (3) leg-B pollution probe ({}/{} vs {}/{}): {}".format(
            *LEG_B_POLLUTION_PROBE,
            "PASS" if not leg_b_failures else "FAIL",
        )
    )
    for f in leg_b_failures:
        print(f"    - {f}")

    print(
        "[5/5] Adversarial missing-data probes (leg-A / leg-B "
        "missing breakeven series)..."
    )

    # Probe (4): use a tenor that exists for FR_OAT but not
    # USD_TIPS so country_a's linker leg fetch returns zero rows.
    # USD_TIPS does NOT publish 2Y in this universe, but FR_OAT does.
    missing_a_failures = assert_missing_leg_a_breakeven_refused(
        engine,
        country_a_nominal_pair="UST",
        country_a_linker_pair="USD_TIPS",
        country_b_nominal_pair="FR_OAT",
        country_b_linker_pair="EUR_FR_LINKER",
        tenor="2Y",
        field_name=args.field,
        lookback_days=args.days,
    )
    print(
        "  (4) missing-leg-A breakeven probe (UST/USD_TIPS vs "
        "FR_OAT/EUR_FR_LINKER @ 2Y): {}".format(
            "PASS" if not missing_a_failures else "FAIL",
        )
    )
    for f in missing_a_failures:
        print(f"    - {f}")

    # Probe (5): symmetric — pick a tenor where country_b's linker
    # leg has zero rows.  Use 2Y with USD_TIPS in the country_b slot.
    missing_b_failures = assert_missing_leg_b_breakeven_refused(
        engine,
        country_a_nominal_pair="FR_OAT",
        country_a_linker_pair="EUR_FR_LINKER",
        country_b_nominal_pair="UST",
        country_b_linker_pair="USD_TIPS",
        tenor="2Y",
        field_name=args.field,
        lookback_days=args.days,
    )
    print(
        "  (5) missing-leg-B breakeven probe (FR_OAT/EUR_FR_LINKER "
        "vs UST/USD_TIPS @ 2Y): {}".format(
            "PASS" if not missing_b_failures else "FAIL",
        )
    )
    for f in missing_b_failures:
        print(f"    - {f}")

    print("Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")
    print(
        f"  same-country input probe: "
        f"{'PASS' if not same_country_failures else 'FAIL'}"
    )
    print(
        f"  leg-A pollution probe:    "
        f"{'PASS' if not leg_a_failures else 'FAIL'}"
    )
    print(
        f"  leg-B pollution probe:    "
        f"{'PASS' if not leg_b_failures else 'FAIL'}"
    )
    print(
        f"  missing-leg-A probe:      "
        f"{'PASS' if not missing_a_failures else 'FAIL'}"
    )
    print(
        f"  missing-leg-B probe:      "
        f"{'PASS' if not missing_b_failures else 'FAIL'}"
    )

    if (
        failed_cases
        or same_country_failures
        or leg_a_failures
        or leg_b_failures
        or missing_a_failures
        or missing_b_failures
    ):
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]}/{case[1]} vs {case[2]}/{case[3]} "
                    f"@ {case[4]} ({len(mismatches)} mismatches)"
                )
        if same_country_failures:
            print("\nSAME-COUNTRY INPUT PROBE FAILURES:")
            for f in same_country_failures:
                print(f"  - {f}")
        if leg_a_failures:
            print("\nLEG-A POLLUTION PROBE FAILURES:")
            for f in leg_a_failures:
                print(f"  - {f}")
        if leg_b_failures:
            print("\nLEG-B POLLUTION PROBE FAILURES:")
            for f in leg_b_failures:
                print(f"  - {f}")
        if missing_a_failures:
            print("\nMISSING-LEG-A PROBE FAILURES:")
            for f in missing_a_failures:
                print(f"  - {f}")
        if missing_b_failures:
            print("\nMISSING-LEG-B PROBE FAILURES:")
            for f in missing_b_failures:
                print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
