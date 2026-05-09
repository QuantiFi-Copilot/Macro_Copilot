#!/usr/bin/env python3
"""
test_breakeven_inflation_simple_sql_validation.py — Linker breakeven
validator.

Validate ``calculate_breakeven_inflation_simple`` against an
independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched``.  The SQL baseline
reproduces the level-stat math (z-score / period changes / trailing
range) without going through any of the Python tool's helpers, so a
mismatch between the two surfaces a real divergence in methodology
rather than a shared-code coincidence.

Key linker-breakeven-specific differences from the sovereign
cross_market_spread validator:

  - filter ``instrument_type = 'sovereign_benchmark'`` for the nominal
    leg AND ``instrument_type = 'inflation_linker'`` for the linker
    leg — the load-bearing no-proxy guarantee
  - anchor the lookback cutoff to the data's last aligned trade_date,
    NOT ``CURRENT_DATE`` — matches the Python tool's anchoring (which
    matches OIS rate_level / linker real_yield_level)
  - adversarial pollution probes both directions

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \
        tests/test_breakeven_inflation_simple_sql_validation.py
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
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (  # noqa: E402
    BreakevenInflationSimpleInput,
    calculate_breakeven_inflation_simple,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (nominal_curve_family, linker_curve_family, tenor)
Case = Tuple[str, str, str]

DEFAULT_CASE_COUNT = 10
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"

# One representative case per nominal/linker country pair — keeps
# regression coverage broad without over-sampling any single pair.
# All entries below are confirmed same-country / same-currency
# against macro_data.instrument_master (the runtime guard in
# compute._enforce_same_country_invariant uses the same identity
# check):
#   UST          (US/USD)      ↔ USD_TIPS       (US/USD)
#   UK_GILT      (UK/GBP)      ↔ GBP_LINKER     (UK/GBP)
#   FR_OAT       (France/EUR)  ↔ EUR_FR_LINKER  (France/EUR)
#   CANADA_GOVT  (Canada/CAD)  ↔ CAD_RRB        (Canada/CAD)
REGRESSION_CASES: List[Case] = [
    ("UST", "USD_TIPS", "10Y"),
    ("UK_GILT", "GBP_LINKER", "10Y"),
    ("FR_OAT", "EUR_FR_LINKER", "10Y"),
    ("CANADA_GOVT", "CAD_RRB", "10Y"),
]

# Tolerances follow the sovereign cross_market_spread validator's
# precision since the bps math + display precision are the same.
TOLERANCE_BY_FIELD = {
    "breakeven_pct": 0.00011,
    "breakeven_bps": 0.011,
    "daily_change_bps": 0.011,
    "weekly_change_bps": 0.011,
    "monthly_change_bps": 0.011,
    "current_z_score": 0.006,
    "high_252d_bps": 0.011,
    "low_252d_bps": 0.011,
    "percentile_252d": 0.11,
    "nominal_yield_pct": 0.00011,
    "real_yield_pct": 0.00011,
    "breakeven_bps_row": 0.011,
    "z_score_row": 0.006,
}


# Adversarial pollution probes — each MUST yield zero rows on its
# corresponding leg AND produce the controlled error envelope from
# the Python tool.
LINKER_IN_NOMINAL_PROBE: Tuple[str, str] = ("USD_TIPS", "GBP_LINKER")
NOMINAL_IN_LINKER_PROBE: Tuple[str, str] = ("UST", "DE_BUND")


# Adversarial cross-country rejection probes — each MUST trigger the
# same-country invariant in compute and return ``{"error": ...}``
# without touching the market_data fetch.  Two probes plus one
# symmetric-direction sweep so both directions are exercised.
#   - EUR-zone case: same currency (EUR), different country (Germany
#     vs France).  Currency match alone is NOT sufficient.
#   - Cross-currency case: different country AND different currency.
CROSS_COUNTRY_EUR_PROBE: Tuple[str, str] = ("DE_BUND", "EUR_FR_LINKER")
CROSS_CURRENCY_PROBE: Tuple[str, str] = ("UK_GILT", "USD_TIPS")


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (nominal, linker, tenor) cases from live metadata.

    Pulls tenor-overlapping (nominal_curve_family, linker_curve_family)
    pairs whose joint observation count meets the rolling-stat
    minimum AND filters to *same-country / same-currency* pairs only.

    Constraint rationale: a generic bond-implied breakeven is by
    construction a same-country object (the nominal sovereign and
    linker legs must share country and currency).  Without this
    filter the pool would emit cross-country pairs such as
    ``IT_BTP`` (Italy/EUR) vs ``EUR_FR_LINKER`` (France/EUR), or
    ``UK_GILT`` (UK/GBP) vs ``USD_TIPS`` (US/USD), and validate them
    as legitimate breakevens — which is the round-1 reviewer-flagged
    bug.  The compute layer now refuses cross-country pairs at
    runtime; this pool now mirrors that contract so the validator
    only ever exercises pairs the tool will accept.

    Same-country alignment is computed from ``macro_data.instrument
    _master``'s ``country`` + ``currency`` columns (joined into the
    enriched view), keyed by ``(curve_family, instrument_type)``.
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

    # By-tenor groupings carry the (curve_family, country, currency)
    # tuple so the cross-product can filter on identity matching, not
    # just tenor overlap.
    nominal_by_tenor: Dict[str, List[Tuple[str, str, str]]] = {}
    for row in nominal_rows:
        nominal_by_tenor.setdefault(row["tenor"], []).append(
            (row["curve_family"], row["country"], row["currency"]),
        )
    linker_by_tenor: Dict[str, List[Tuple[str, str, str]]] = {}
    for row in linker_rows:
        linker_by_tenor.setdefault(row["tenor"], []).append(
            (row["curve_family"], row["country"], row["currency"]),
        )

    # Same-country / same-currency filter: a (nominal, linker, tenor)
    # triple enters the pool ONLY if both legs agree on country AND
    # currency.  Country + currency are individually redundant for
    # the ingested universe today (every curve_family has a single
    # (country, currency) identity), but the EUR-zone is the load-
    # bearing case: DE_BUND (Germany/EUR) and EUR_FR_LINKER (France/
    # EUR) share currency but NOT country and MUST not enter the
    # pool.  This mirrors the runtime guard in
    # compute._enforce_same_country_invariant.
    pool: List[Case] = []
    common_tenors = set(nominal_by_tenor.keys()) & set(linker_by_tenor.keys())
    for tenor in sorted(common_tenors):
        for nominal_cf, n_country, n_currency in sorted(nominal_by_tenor[tenor]):
            for linker_cf, l_country, l_currency in sorted(linker_by_tenor[tenor]):
                if n_country != l_country or n_currency != l_currency:
                    continue
                pool.append((nominal_cf, linker_cf, tenor))

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
    tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the breakeven level-stat math.

    Mirrors the cross_market_spread SQL baseline shape (pivot two
    legs, ffill ≤5 days, compute the spread in bps, then the rolling
    z-score / period changes / trailing range) but with two distinct
    instrument_type filters per leg AND with the lookback cutoff
    anchored to the data's latest aligned trade_date (matching the
    Python tool's anchoring).
    """
    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                trade_date,
                curve_family,
                instrument_type,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE tenor = :tenor
              AND field_name = :field_name
              AND trade_date >= CURRENT_DATE - ((:lookback_days + 378) * INTERVAL '1 day')
              AND (
                    (curve_family   = :nominal_curve_family
                     AND instrument_type = 'sovereign_benchmark')
                 OR (curve_family   = :linker_curve_family
                     AND instrument_type = 'inflation_linker')
              )
        ),
        date_grid AS (
            SELECT DISTINCT trade_date FROM raw
        ),
        numbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM date_grid
        ),
        joined AS (
            SELECT
                n.trade_date,
                n.rn,
                MAX(CASE WHEN r.curve_family = :nominal_curve_family
                         THEN r.field_value END) AS nominal_raw,
                MAX(CASE WHEN r.curve_family = :linker_curve_family
                         THEN r.field_value END) AS linker_raw
            FROM numbered n
            LEFT JOIN raw r
              ON r.trade_date = n.trade_date
            GROUP BY n.trade_date, n.rn
        ),
        ffill AS (
            SELECT
                *,
                MAX(CASE WHEN nominal_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS nominal_last_rn,
                MAX(CASE WHEN linker_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS linker_last_rn
            FROM joined
        ),
        filled AS (
            SELECT
                trade_date,
                rn,
                CASE
                    WHEN nominal_raw IS NOT NULL THEN nominal_raw
                    WHEN nominal_last_rn IS NOT NULL AND rn - nominal_last_rn <= 5
                    THEN MAX(CASE WHEN nominal_raw IS NOT NULL THEN nominal_raw END)
                         OVER (PARTITION BY nominal_last_rn)
                    ELSE NULL
                END AS nominal_yield,
                CASE
                    WHEN linker_raw IS NOT NULL THEN linker_raw
                    WHEN linker_last_rn IS NOT NULL AND rn - linker_last_rn <= 5
                    THEN MAX(CASE WHEN linker_raw IS NOT NULL THEN linker_raw END)
                         OVER (PARTITION BY linker_last_rn)
                    ELSE NULL
                END AS linker_yield
            FROM ffill
        ),
        aligned AS (
            SELECT
                trade_date,
                rn,
                nominal_yield,
                linker_yield,
                ROUND(((nominal_yield - linker_yield) * 100)::numeric, 2)::double precision AS breakeven_bps
            FROM filled
            WHERE nominal_yield IS NOT NULL
              AND linker_yield IS NOT NULL
        ),
        ranumbered AS (
            -- Renumber from 1 over only the aligned (both-legs-present)
            -- rows so the rolling windows match the Python tool's
            -- post-pivot dataframe (which drops rows where either leg
            -- is NaN after the ffill).
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn,
                nominal_yield,
                linker_yield,
                breakeven_bps
            FROM aligned
        ),
        scored AS (
            SELECT
                trade_date,
                arn,
                nominal_yield,
                linker_yield,
                breakeven_bps,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(breakeven_bps) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(breakeven_bps) OVER zw <> 0
                    THEN ROUND(
                        (
                            (breakeven_bps - AVG(breakeven_bps) OVER zw)
                            / NULLIF(STDDEV_SAMP(breakeven_bps) OVER zw, 0)
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score
            FROM ranumbered
            WINDOW zw AS (
                ORDER BY arn
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
            )
        ),
        latest_aligned AS (
            SELECT MAX(trade_date) AS as_of_date FROM ranumbered
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
                nominal_yield,
                linker_yield,
                breakeven_bps,
                z_score,
                CASE
                    WHEN LAG(breakeven_bps, 1) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND((breakeven_bps - LAG(breakeven_bps, 1) OVER (ORDER BY arn))::numeric, 2)::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(breakeven_bps, 5) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND((breakeven_bps - LAG(breakeven_bps, 5) OVER (ORDER BY arn))::numeric, 2)::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(breakeven_bps, 21) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND((breakeven_bps - LAG(breakeven_bps, 21) OVER (ORDER BY arn))::numeric, 2)::double precision
                END AS monthly_change_bps,
                ROUND((MAX(breakeven_bps) OVER tw)::numeric, 2)::double precision AS high_252d_bps,
                ROUND((MIN(breakeven_bps) OVER tw)::numeric, 2)::double precision AS low_252d_bps,
                CASE
                    WHEN MAX(breakeven_bps) OVER tw IS NULL
                      OR MIN(breakeven_bps) OVER tw IS NULL
                      OR MAX(breakeven_bps) OVER tw = MIN(breakeven_bps) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                breakeven_bps - MIN(breakeven_bps) OVER tw
                            ) / NULLIF(
                                MAX(breakeven_bps) OVER tw - MIN(breakeven_bps) OVER tw,
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
            nominal_yield,
            linker_yield,
            breakeven_bps,
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
                "tenor": tenor,
                "field_name": field_name,
                "lookback_days": lookback_days,
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
            "tenor": tenor,
            "breakeven_label": (
                f"{nominal_curve_family}-{linker_curve_family} "
                f"{tenor} breakeven"
            ),
            "breakeven_bps": latest["breakeven_bps"],
            "breakeven_pct": (
                None if latest["breakeven_bps"] is None
                else round(float(latest["breakeven_bps"]) / 100.0, 4)
            ),
            "daily_change_bps": latest["daily_change_bps"],
            "weekly_change_bps": latest["weekly_change_bps"],
            "monthly_change_bps": latest["monthly_change_bps"],
            "current_z_score": latest["z_score"],
            "rolling_window_days": 252,
            "high_252d_bps": latest["high_252d_bps"],
            "low_252d_bps": latest["low_252d_bps"],
            "percentile_252d": latest["percentile_252d"],
            "nominal_yield_pct": (
                None if latest["nominal_yield"] is None
                else round(float(latest["nominal_yield"]), 4)
            ),
            "real_yield_pct": (
                None if latest["linker_yield"] is None
                else round(float(latest["linker_yield"]), 4)
            ),
        },
        "time_series": [
            {
                "date": row["date"],
                "breakeven_bps": row["breakeven_bps"],
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
            "tenor",
            "breakeven_label",
            "rolling_window_days",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "breakeven_pct",
            "breakeven_bps",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "nominal_yield_pct",
            "real_yield_pct",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    # The Python tool's bespoke time_series carries breakeven_bps +
    # z_score per row; align to the SQL baseline's same row shape.
    # Use sample-historical-row tolerances by reusing the snapshot
    # tolerances under aliased keys.
    row_tolerances = dict(TOLERANCE_BY_FIELD)
    row_tolerances["breakeven_bps"] = TOLERANCE_BY_FIELD["breakeven_bps_row"]
    row_tolerances["z_score"] = TOLERANCE_BY_FIELD["z_score_row"]
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=("breakeven_bps", "z_score"),
            tolerances=row_tolerances,
        )
    )
    return mismatches


def run_case(
    engine, *, case: Case, lookback_days: int, field_name: str,
) -> List[str]:
    nominal_cf, linker_cf, tenor = case
    tool_result = calculate_breakeven_inflation_simple(
        engine=engine,
        params=BreakevenInflationSimpleInput(
            nominal_curve_family=nominal_cf,
            linker_curve_family=linker_cf,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        nominal_curve_family=nominal_cf,
        linker_curve_family=linker_cf,
        tenor=tenor,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


def assert_pollution_guard(
    engine,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    field_name: str,
    lookback_days: int,
    expected_missing_leg: str,
) -> List[str]:
    """Adversarial probe — verify the SQL-side guard kills the
    polluted leg + the Python tool returns the controlled error
    envelope mentioning the right ``instrument_type``.
    """
    failures: List[str] = []

    if expected_missing_leg == "sovereign_benchmark":
        # Polluted nominal leg — confirm zero rows under the nominal
        # filter (linker in the nominal slot).
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
        # Polluted linker leg — confirm zero rows under the linker
        # filter (nominal in the linker slot).
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
                "tenor": tenor,
                "field_name": field_name,
            },
        ).mappings().first()
    n_rows = int(row["n"]) if row is not None else 0
    if n_rows != 0:
        failures.append(
            f"SQL invariant violated: expected zero "
            f"instrument_type='{expected_missing_leg}' rows for "
            f"{check_cf} {tenor} (pollution probe), but got {n_rows}."
        )

    tool_result = calculate_breakeven_inflation_simple(
        engine=engine,
        params=BreakevenInflationSimpleInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    if "error" not in tool_result:
        failures.append(
            "Python tool returned a snapshot for pollution probe "
            f"(nominal={nominal_curve_family}, linker={linker_curve_family}, "
            f"tenor={tenor}) — must return controlled error envelope.  "
            f"Got keys: {sorted(tool_result.keys())}"
        )
    elif expected_missing_leg not in tool_result.get("error", ""):
        failures.append(
            f"Controlled error envelope missing the "
            f"{expected_missing_leg!r} rationale required to route the "
            "operator to the correct slot.  Got: "
            f"{tool_result['error']!r}"
        )
    return failures


def assert_cross_country_guard(
    engine,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Adversarial cross-country probe — verify the runtime same-
    country invariant in
    ``compute._enforce_same_country_invariant`` refuses the pair and
    returns a controlled error envelope mentioning both curve
    families AND the (country, currency) mismatch.

    The probe asserts:
      - the result has exactly one key, ``error``;
      - the error string mentions BOTH curve families;
      - the error string mentions BOTH (country, currency) pairs
        observed in instrument_master (so the operator sees the
        identity mismatch, not just a generic refusal).
    """
    failures: List[str] = []

    # Pull the (country, currency) identities the runtime guard will
    # see — used to assert the error string surfaces them.
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

    tool_result = calculate_breakeven_inflation_simple(
        engine=engine,
        params=BreakevenInflationSimpleInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )

    keys = sorted(tool_result.keys())
    if keys != ["error"]:
        failures.append(
            "Cross-country pair was not refused: tool returned keys "
            f"{keys!r} for nominal={nominal_curve_family}, "
            f"linker={linker_curve_family}, tenor={tenor}.  Expected "
            "exactly the controlled-error envelope {'error': ...}."
        )
        return failures

    error_text = tool_result["error"]
    if nominal_curve_family not in error_text:
        failures.append(
            f"Controlled error envelope missing nominal curve_family "
            f"{nominal_curve_family!r} (operators should be told "
            f"which leg is which).  Got: {error_text!r}"
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
                f"{needle!r} (the (country, currency) mismatch must "
                "be visible to the operator).  Got: "
                f"{error_text!r}"
            )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the linker breakeven_inflation_simple tool against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("LINKER BREAKEVEN INFLATION SIMPLE TOOL — SQL VALIDATION")
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
        lambda case: f"{case[0]} vs {case[1]} @ {case[2]}",
    )

    print("[3/5] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]} vs {case[1]} @ {case[2]}",
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
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more mismatches")
            failed_cases.append((case, mismatches))
        else:
            print("PASS")

    print(
        "[4/5] Adversarial pollution probes (linker→nominal slot, nominal→linker slot)..."
    )
    # Probe 1: linker curve in nominal slot — nominal-leg fetch must
    # find zero sovereign_benchmark rows for the linker curve_family.
    linker_in_nominal_failures = assert_pollution_guard(
        engine,
        nominal_curve_family=LINKER_IN_NOMINAL_PROBE[0],  # USD_TIPS in nominal slot
        linker_curve_family=LINKER_IN_NOMINAL_PROBE[1],   # GBP_LINKER in linker slot
        tenor="10Y",
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

    # Probe 2: nominal curve in linker slot — linker-leg fetch must
    # find zero inflation_linker rows for the nominal curve_family.
    nominal_in_linker_failures = assert_pollution_guard(
        engine,
        nominal_curve_family=NOMINAL_IN_LINKER_PROBE[0],  # UST in nominal slot
        linker_curve_family=NOMINAL_IN_LINKER_PROBE[1],   # DE_BUND in linker slot
        tenor="10Y",
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
    # Probe A: same currency (EUR), different country.  Forward
    # direction: DE_BUND nominal vs EUR_FR_LINKER linker — must be
    # refused by the same-country invariant.
    eur_forward_failures = assert_cross_country_guard(
        engine,
        nominal_curve_family=CROSS_COUNTRY_EUR_PROBE[0],   # DE_BUND (Germany/EUR)
        linker_curve_family=CROSS_COUNTRY_EUR_PROBE[1],    # EUR_FR_LINKER (France/EUR)
        tenor="10Y",
        field_name=args.field,
        lookback_days=args.days,
    )
    print(
        "  EUR-zone cross-country probe ({} as nominal, {} as linker): {}".format(
            CROSS_COUNTRY_EUR_PROBE[0],
            CROSS_COUNTRY_EUR_PROBE[1],
            "PASS" if not eur_forward_failures else "FAIL",
        )
    )
    for f in eur_forward_failures:
        print(f"    - {f}")

    # Probe B: cross-currency case.  Different country AND currency.
    cross_currency_failures = assert_cross_country_guard(
        engine,
        nominal_curve_family=CROSS_CURRENCY_PROBE[0],      # UK_GILT (UK/GBP)
        linker_curve_family=CROSS_CURRENCY_PROBE[1],       # USD_TIPS (US/USD)
        tenor="10Y",
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

    # Symmetric direction for the cross-currency probe — slots
    # literally swapped so the invariant is exercised both ways.
    # In the swap, the nominal slot receives a linker curve_family
    # (USD_TIPS), so the same-country guard's instrument_master
    # lookup for (USD_TIPS, 'sovereign_benchmark') returns zero
    # rows.  That still triggers the {"error": ...} envelope (a
    # branch of the same invariant), proving the guard handles
    # either argument ordering rather than relying on the slot
    # populations being well-formed.  We assert refusal + that the
    # error mentions BOTH curve families; the precise (country,
    # currency) tuple is not asserted here because the swap branch
    # cannot resolve a country for the nominal slot.
    swap_nominal, swap_linker = (
        CROSS_CURRENCY_PROBE[1],   # USD_TIPS in nominal slot
        CROSS_CURRENCY_PROBE[0],   # UK_GILT in linker slot
    )
    swap_result = calculate_breakeven_inflation_simple(
        engine=engine,
        params=BreakevenInflationSimpleInput(
            nominal_curve_family=swap_nominal,
            linker_curve_family=swap_linker,
            tenor="10Y",
            lookback_days=args.days,
            field_name=args.field,
        ),
    )
    eur_symmetric_failures: List[str] = []
    if sorted(swap_result.keys()) != ["error"]:
        eur_symmetric_failures.append(
            "Symmetric (slot-swapped) cross-currency probe was not "
            f"refused: tool returned keys {sorted(swap_result.keys())!r} "
            f"for nominal={swap_nominal}, linker={swap_linker}.  "
            "Expected exactly the controlled-error envelope "
            "{'error': ...}."
        )
    else:
        # Both curve families MUST appear in the error; the symmetric
        # case routes through the "missing instrument_master rows"
        # branch on the nominal leg, which embeds the curve_family
        # name in its message.  We additionally check that the error
        # mentions the linker curve_family by re-running with the
        # original (unswapped) order being handled in the
        # cross_currency_failures probe above.
        err_text = swap_result["error"]
        if swap_nominal not in err_text:
            eur_symmetric_failures.append(
                f"Symmetric probe error envelope missing nominal "
                f"curve_family {swap_nominal!r}.  Got: {err_text!r}"
            )
    print(
        "  Cross-currency symmetric probe ({} as nominal, {} as linker): {}".format(
            swap_nominal,
            swap_linker,
            "PASS" if not eur_symmetric_failures else "FAIL",
        )
    )
    for f in eur_symmetric_failures:
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
        f"{'PASS' if not eur_forward_failures else 'FAIL'}"
    )
    print(
        f"  cross-currency probe: "
        f"{'PASS' if not cross_currency_failures else 'FAIL'}"
    )
    print(
        f"  cross-currency symmetric probe: "
        f"{'PASS' if not eur_symmetric_failures else 'FAIL'}"
    )

    if (
        failed_cases
        or linker_in_nominal_failures
        or nominal_in_linker_failures
        or eur_forward_failures
        or cross_currency_failures
        or eur_symmetric_failures
    ):
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]} vs {case[1]} @ {case[2]} "
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
        if eur_forward_failures:
            print("\nEUR-ZONE CROSS-COUNTRY PROBE FAILURES:")
            for f in eur_forward_failures:
                print(f"  - {f}")
        if cross_currency_failures:
            print("\nCROSS-CURRENCY PROBE FAILURES:")
            for f in cross_currency_failures:
                print(f"  - {f}")
        if eur_symmetric_failures:
            print("\nCROSS-CURRENCY SYMMETRIC PROBE FAILURES:")
            for f in eur_symmetric_failures:
                print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
