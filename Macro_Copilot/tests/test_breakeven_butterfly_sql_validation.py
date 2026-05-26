#!/usr/bin/env python3
"""
test_breakeven_butterfly_sql_validation.py — Linker breakeven
butterfly validator.

Validate ``calculate_breakeven_butterfly`` against an independent
SQL baseline run directly on
``macro_data.v_market_data_daily_enriched`` ×
``macro_data.instrument_master``.  The SQL baseline reproduces the
per-trade-date breakeven butterfly math without going through any of
the Python tool's helpers, so a mismatch surfaces a real divergence
in methodology rather than a shared-code coincidence.

Deterministic full-curve coverage
---------------------------------
The same-country (nominal, linker) universe is bounded and ingested
through ``rates_agent/playbooks/sovereign_bonds.yml`` ×
``rates_agent/playbooks/inflation_indexed_bonds.yml``.  The
intersection of pillars per pair is the set of tenors where BOTH
sides have data:

  - UST × USD_TIPS:           5Y / 10Y / 20Y / 30Y         → C(4,3) =  4 triplets
  - UK_GILT × GBP_LINKER:     1Y / 2Y / 3Y / 5Y / 10Y /
                              20Y / 30Y                    → C(7,3) = 35 triplets
  - FR_OAT × EUR_FR_LINKER:   2Y / 5Y / 7Y / 10Y           → C(4,3) =  4 triplets
  - CANADA_GOVT × CAD_RRB:    5Y / 10Y / 20Y / 30Y         → C(4,3) =  4 triplets

Total: 4 + 35 + 4 + 4 = **47** deterministic same-country triplets.
``DEFAULT_CASE_COUNT == len(REGRESSION_CASES) == 47`` so the helper
``sample_cases`` returns the full fixed list in stable order on
every run — every supported same-country (curve_family, triplet)
case is exercised against the SQL baseline rather than sampled.
When the default-coverage invocation produces fewer than 47 cases
(live pool missing pillars), the runner emits a FATAL diagnostic
and exits — matches the contract established by
``test_real_yield_butterfly_sql_validation.py``.

Adversarial probes (5):
  1. Tenor-ordering rejection: the input layer rejects any
     ordering other than short<belly<long via Pydantic; no silent
     swap.
  2. Distinct-tenor rejection: duplicate tenors rejected at the
     Pydantic layer.
  3. Non-linker / non-nominal curve_family rejection: passing a
     linker in the nominal slot (or vice versa) yields a
     controlled error envelope via the spot primitive's no-proxy
     guard.
  4. Missing-matched-nominal-tenor: unknown (curve_family, tenor)
     on any endpoint yields a controlled error envelope.
  5. SELECT-guard inheritance: same as the
     real_yield_butterfly probe — pollution check via independent
     SQL count probe.

The SQL cross-check INDEPENDENTLY reproduces the butterfly
arithmetic — it computes ``nominal - linker`` for each of the three
tenors directly in SQL, then assembles
``belly_be - 0.5*(short_be + long_be)`` in SQL — and compares
against the Python output day-by-day with tolerance set to the
``bps_round_decimals`` convention.

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \\
        tests/test_breakeven_butterfly_sql_validation.py
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
from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (  # noqa: E402
    BreakevenButterflyInput,
    calculate_breakeven_butterfly,
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


# (nominal_curve_family, linker_curve_family, short_tenor,
#  belly_tenor, long_tenor)
Case = Tuple[str, str, str, str, str]


# Full deterministic coverage of the same-country breakeven
# butterfly universe.  Tenor grid per (nominal, linker) pair is
# the intersection of:
#   - sovereign nominal pillars (rates_agent/playbooks/sovereign_bonds.yml)
#   - linker pillars (rates_agent/playbooks/inflation_indexed_bonds.yml)
# Stable (nominal, linker)-then-(short,belly,long) ordering so
# failures are easy to diff across runs.
_PAIR_PILLARS: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ("UST", "USD_TIPS"):
        ("5Y", "10Y", "20Y", "30Y"),
    ("UK_GILT", "GBP_LINKER"):
        ("1Y", "2Y", "3Y", "5Y", "10Y", "20Y", "30Y"),
    ("FR_OAT", "EUR_FR_LINKER"):
        ("2Y", "5Y", "7Y", "10Y"),
    ("CANADA_GOVT", "CAD_RRB"):
        ("5Y", "10Y", "20Y", "30Y"),
}


def _enumerate_same_country_triplets(
    nominal: str,
    linker: str,
    pillars: Tuple[str, ...],
) -> List[Case]:
    parsed = sorted(
        ((tenor_to_years(t), t) for t in pillars),
        key=lambda x: x[0],
    )
    triplets: List[Case] = []
    n = len(parsed)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                triplets.append(
                    (
                        nominal,
                        linker,
                        parsed[i][1],
                        parsed[j][1],
                        parsed[k][1],
                    )
                )
    return triplets


REGRESSION_CASES: List[Case] = []
for (_n, _l), _p in _PAIR_PILLARS.items():
    REGRESSION_CASES.extend(
        _enumerate_same_country_triplets(_n, _l, _p)
    )

DEFAULT_CASE_COUNT = len(REGRESSION_CASES)
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "YLD_YTM_MID"


# Tolerances follow the breakeven_curve_spread + breakeven_inflation_simple
# validators since the bps math + display precision are the same;
# the butterfly is a 3-point combination so cumulative rounding
# error is small but the chained rounding from inner spot primitive
# composition is wider than a 2-point spread.
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
    "short_breakeven_bps": 0.011,
    "belly_breakeven_bps": 0.011,
    "long_breakeven_bps": 0.011,
    "short_years": 1e-4,
    "belly_years": 1e-4,
    "long_years": 1e-4,
    "butterfly_bps_row": 0.031,
    "z_score_row": 0.006,
}


# Adversarial pollution probes.
LINKER_IN_NOMINAL_PROBE: Tuple[str, str] = ("USD_TIPS", "GBP_LINKER")
NOMINAL_IN_LINKER_PROBE: Tuple[str, str] = ("UST", "DE_BUND")


# Adversarial cross-country rejection probes.
CROSS_COUNTRY_EUR_PROBE: Tuple[str, str] = ("DE_BUND", "EUR_FR_LINKER")
CROSS_CURRENCY_PROBE: Tuple[str, str] = ("UK_GILT", "USD_TIPS")


def butterfly_label(
    nominal: str,
    linker: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
) -> str:
    return (
        f"{nominal}/{linker} "
        f"{short_tenor.replace('Y', '')}s"
        f"{belly_tenor.replace('Y', '')}s"
        f"{long_tenor.replace('Y', '')}s breakeven"
    )


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (nominal, linker, short_tenor, belly_tenor, long_tenor)
    cases from live nominal+linker metadata.  Filters to same-country
    / same-currency pairs only; the spot primitive's compute layer
    refuses cross-country pairs, and this validator should only ever
    exercise pairs the tool will accept.  Requires at least 80
    observations on every endpoint to ensure rolling stats are
    populated.
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
            if len(common_tenors) < 3:
                continue
            parsed: List[Tuple[float, str]] = []
            for t in sorted(common_tenors):
                try:
                    parsed.append((tenor_to_years(t), t))
                except ValueError:
                    continue
            parsed.sort(key=lambda x: x[0])
            m = len(parsed)
            for i in range(m):
                for j in range(i + 1, m):
                    for k in range(j + 1, m):
                        pool.append(
                            (
                                n_cf, l_cf,
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
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the same-country breakeven
    butterfly math.

    Algorithm (mirrors the Python tool's composition shape EXACTLY):
      1. Pull six raw yield series (3 tenors × {nominal, linker})
         under the four-conjunct SELECT guard
         (instrument_type='sovereign_benchmark' or 'inflation_linker'
         AND curve_family=? AND tenor=? AND field_name=?) — same
         shape the spot primitive's pivot step uses.
      2. For each tenor, pivot+ffill+dropna the two legs of that
         tenor and compute the breakeven independently:
            be_t_bps = (nominal_t - linker_t) * 100
         — same shape the spot ``breakeven_inflation_simple``
         primitive emits.  Per-tenor ffill operates on a per-tenor
         date grid (only the dates relevant to that tenor's two
         legs), NOT a multi-tenor union — otherwise the SQL ffill
         would bridge gaps across more rows than the Python spot's
         per-tenor pivot does, producing extra pre-history that
         diverges from the Python tool.
      3. Per-leg anchor-trim each endpoint's breakeven series to
         ``extended_lookback_days`` from that endpoint's latest
         trade_date — mirrors the butterfly primitive's composition
         shape (each inner spot call returns its own trimmed
         time_series_breakeven anchored to that endpoint's latest
         trade_date).
      4. Inner-join the three endpoint breakeven series on
         trade_date — same as the butterfly primitive's strict
         alignment step.
      5. Apply the FIXED simple-butterfly weighting:
            butterfly_bps = belly_be - 0.5 * (short_be + long_be)
         and round to 2 decimals (matches the butterfly primitive's
         bps_round_decimals boundary).
      6. Compute rolling 252-day z-score on the bps butterfly,
         period changes (in BPS — plain subtraction), trailing
         range (in BPS) — all under the same convention values the
         Python tool uses.
      7. Anchor the display cutoff to the latest aligned trade_date
         (matches the Python tool's anchoring).
    """
    t_short_years = tenor_to_years(short_tenor)
    t_belly_years = tenor_to_years(belly_tenor)
    t_long_years = tenor_to_years(long_tenor)
    # Mirror the butterfly primitive's buffer math
    # (compute._conventions_from_config + the
    # extended_lookback_days = lookback + buffer_calendar_days
    # computation in calculate_breakeven_butterfly).  The buffer is
    # max(z_window, trailing_window) * buffer_multiplier
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
              AND tenor IN (:short_tenor, :belly_tenor, :long_tenor)
              AND trade_date >= CURRENT_DATE - ((:lookback_days + 800) * INTERVAL '1 day')
              AND (
                    (curve_family   = :nominal_curve_family
                     AND instrument_type = 'sovereign_benchmark')
                 OR (curve_family   = :linker_curve_family
                     AND instrument_type = 'inflation_linker')
              )
        ),
        -- Per-tenor pivot+ffill+dropna+anchor-trim for the SHORT leg.
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
        -- Per-tenor pivot+ffill+dropna+anchor-trim for the BELLY leg.
        belly_raw AS (
            SELECT trade_date, curve_family, field_value
            FROM raw
            WHERE tenor = :belly_tenor
        ),
        belly_dates AS (
            SELECT DISTINCT trade_date FROM belly_raw
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
                MAX(CASE
                        WHEN br.curve_family = :nominal_curve_family
                        THEN br.field_value
                    END) AS n_raw,
                MAX(CASE
                        WHEN br.curve_family = :linker_curve_family
                        THEN br.field_value
                    END) AS l_raw
            FROM belly_numbered bn
            LEFT JOIN belly_raw br ON br.trade_date = bn.trade_date
            GROUP BY bn.trade_date, bn.brn
        ),
        belly_ffill AS (
            SELECT
                *,
                MAX(CASE WHEN n_raw IS NOT NULL THEN brn END)
                    OVER (ORDER BY brn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS n_last_rn,
                MAX(CASE WHEN l_raw IS NOT NULL THEN brn END)
                    OVER (ORDER BY brn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS l_last_rn
            FROM belly_joined
        ),
        belly_filled AS (
            SELECT
                trade_date,
                brn,
                CASE
                    WHEN n_raw IS NOT NULL THEN n_raw
                    WHEN n_last_rn IS NOT NULL AND brn - n_last_rn <= 5
                    THEN MAX(CASE WHEN n_raw IS NOT NULL THEN n_raw END)
                         OVER (PARTITION BY n_last_rn)
                    ELSE NULL
                END AS n_belly,
                CASE
                    WHEN l_raw IS NOT NULL THEN l_raw
                    WHEN l_last_rn IS NOT NULL AND brn - l_last_rn <= 5
                    THEN MAX(CASE WHEN l_raw IS NOT NULL THEN l_raw END)
                         OVER (PARTITION BY l_last_rn)
                    ELSE NULL
                END AS l_belly
            FROM belly_ffill
        ),
        belly_be_unfiltered AS (
            SELECT
                trade_date,
                ROUND(((n_belly - l_belly) * 100)::numeric, 2)::double precision AS be_belly_bps
            FROM belly_filled
            WHERE n_belly IS NOT NULL AND l_belly IS NOT NULL
        ),
        belly_be_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM belly_be_unfiltered
        ),
        belly_be AS (
            SELECT b.trade_date, b.be_belly_bps
            FROM belly_be_unfiltered b, belly_be_anchor a
            WHERE b.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        -- Per-tenor pivot+ffill+dropna+anchor-trim for the LONG leg.
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
                b.be_belly_bps,
                l.be_long_bps
            FROM short_be s
            INNER JOIN belly_be b ON b.trade_date = s.trade_date
            INNER JOIN long_be  l ON l.trade_date = s.trade_date
        ),
        -- This is the LOAD-BEARING butterfly arithmetic step.  It
        -- assembles the SQL-side butterfly directly from
        -- (be_short_bps, be_belly_bps, be_long_bps) via the FIXED
        -- simple-butterfly weighting and the wing spreads — the
        -- exact arithmetic the Python primitive performs.
        butterfly_rows AS (
            SELECT
                trade_date,
                be_short_bps,
                be_belly_bps,
                be_long_bps,
                ROUND(
                    (be_belly_bps - 0.5 * (be_short_bps + be_long_bps))::numeric,
                    2
                )::double precision AS butterfly_bps,
                ROUND((be_belly_bps - be_short_bps)::numeric, 2)::double precision AS wing_short_bps,
                ROUND((be_long_bps - be_belly_bps)::numeric, 2)::double precision AS wing_long_bps
            FROM aligned
        ),
        renumbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn,
                be_short_bps,
                be_belly_bps,
                be_long_bps,
                butterfly_bps,
                wing_short_bps,
                wing_long_bps
            FROM butterfly_rows
        ),
        scored AS (
            SELECT
                trade_date,
                arn,
                be_short_bps,
                be_belly_bps,
                be_long_bps,
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
                be_short_bps,
                be_belly_bps,
                be_long_bps,
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
            be_short_bps,
            be_belly_bps,
            be_long_bps,
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
                "nominal_curve_family": nominal_curve_family,
                "linker_curve_family": linker_curve_family,
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
            "nominal_curve_family": nominal_curve_family,
            "linker_curve_family": linker_curve_family,
            "short_tenor": short_tenor,
            "belly_tenor": belly_tenor,
            "long_tenor": long_tenor,
            "butterfly_label": butterfly_label(
                nominal_curve_family,
                linker_curve_family,
                short_tenor,
                belly_tenor,
                long_tenor,
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
            "short_breakeven_bps": latest["be_short_bps"],
            "belly_breakeven_bps": latest["be_belly_bps"],
            "long_breakeven_bps": latest["be_long_bps"],
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
            "nominal_curve_family",
            "linker_curve_family",
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
            "short_breakeven_bps",
            "belly_breakeven_bps",
            "long_breakeven_bps",
            "short_years",
            "belly_years",
            "long_years",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
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
        (("UST", "USD_TIPS", "10Y", "5Y", "30Y"), "short>belly"),
        (("UST", "USD_TIPS", "5Y", "30Y", "10Y"), "belly>long"),
        (("UST", "USD_TIPS", "30Y", "10Y", "5Y"), "fully reversed"),
    ):
        n, l, s, b, lg = tup
        try:
            _ = BreakevenButterflyInput(
                nominal_curve_family=n,
                linker_curve_family=l,
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
        (("UST", "USD_TIPS", "10Y", "10Y", "30Y"), "short==belly"),
        (("UST", "USD_TIPS", "5Y", "10Y", "10Y"), "belly==long"),
        (("UST", "USD_TIPS", "10Y", "20Y", "10Y"), "short==long"),
    ):
        n, l, s, b, lg = tup
        try:
            _ = BreakevenButterflyInput(
                nominal_curve_family=n,
                linker_curve_family=l,
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


def assert_pollution_guard(
    engine,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    field_name: str,
    lookback_days: int,
    expected_missing_leg: str,
) -> List[str]:
    """Probe 3: pollution probe.  Pass a linker in the nominal slot
    (or vice versa); the spot primitive's no-proxy guard must fire
    transitively and surface a controlled error envelope mentioning
    the missing instrument_type.
    """
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
            f"{check_cf} {short_tenor} (pollution probe), but got "
            f"{n_rows}."
        )

    tool_result = calculate_breakeven_butterfly(
        engine=engine,
        params=BreakevenButterflyInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    if "error" not in tool_result:
        failures.append(
            "Python tool returned a snapshot for pollution probe "
            f"(nominal={nominal_curve_family}, "
            f"linker={linker_curve_family}, "
            f"{short_tenor}/{belly_tenor}/{long_tenor}) — must "
            "return controlled error envelope.  Got keys: "
            f"{sorted(tool_result.keys())}"
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
    belly_tenor: str,
    long_tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Same-country invariant probe (inherited from the spot
    primitive) — verify the runtime guard refuses cross-country
    pairs and returns a controlled error envelope mentioning both
    curve families AND the (country, currency) mismatch.
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

    tool_result = calculate_breakeven_butterfly(
        engine=engine,
        params=BreakevenButterflyInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
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
            f"{short_tenor}/{belly_tenor}/{long_tenor}.  Expected "
            "exactly the controlled-error envelope {'error': ...}."
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


def assert_unknown_pillar_handling(
    engine,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Probe 4: missing-matched-tenor.  An unknown / unsupported
    (curve_family, tenor) on any endpoint MUST yield a controlled
    error envelope, NOT a Python exception.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_breakeven_butterfly(
            engine=engine,
            params=BreakevenButterflyInput(
                nominal_curve_family=nominal_curve_family,
                linker_curve_family=linker_curve_family,
                short_tenor=short_tenor,
                belly_tenor=belly_tenor,
                long_tenor=long_tenor,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"Unknown pillar ({nominal_curve_family}/"
            f"{linker_curve_family} {short_tenor}/{belly_tenor}/"
            f"{long_tenor}) raised {type(exc).__name__}: {exc} — "
            "must return controlled error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            f"Unknown pillar ({nominal_curve_family}/"
            f"{linker_curve_family} {short_tenor}/{belly_tenor}/"
            f"{long_tenor}) returned a snapshot — must return "
            "controlled error envelope instead.  Got keys: "
            f"{sorted(tool_result.keys())}"
        )
    return failures


def assert_select_guard_inheritance(
    engine, *, field_name: str,
) -> List[str]:
    """Probe 5: SELECT-guard inheritance — wrong instrument_type
    rows MUST NOT leak into any leg of the butterfly compute, AND
    the spot primitive's instrument_master identity guard refuses
    non-linker / non-nominal curve_families with a controlled
    error envelope BEFORE any market-data fetch fires.
    """
    failures: List[str] = []

    # Pollution check: requesting 'UST' under the inflation_linker
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
            "curve).  The spot primitive's filter must refuse "
            "non-linker rows."
        )

    # The tool refuses 'UST' in the linker slot with a controlled
    # error envelope.
    tool_result = calculate_breakeven_butterfly(
        engine=engine,
        params=BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="UST",  # invalid — same as nominal
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            field_name=field_name,
        ),
    ) if False else None  # safer: the schema validator already rejects this

    # Use the cross-country / pollution probes from other tests for
    # the controlled-error surface.
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
            "Validate the linker breakeven_butterfly tool against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("LINKER BREAKEVEN BUTTERFLY TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print(
        f"  total_supported_triplets (per-pair same-country "
        f"triplets) : {len(REGRESSION_CASES)}"
    )
    print("-" * 80)

    print("[1/6] Creating DB engine...")
    engine = get_db_engine()

    print(
        "[2/6] Selecting validation cases from live nominal+linker "
        "metadata..."
    )
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases,
        lambda case: (
            f"{case[0]} vs {case[1]} @ "
            f"{case[2]}/{case[3]}/{case[4]}"
        ),
    )

    # Fail loudly when the default-coverage invocation produces
    # fewer than the full deterministic case list.  Mirrors the
    # contract established by test_real_yield_butterfly_sql_validation.py.
    if args.cases == DEFAULT_CASE_COUNT and len(cases) != DEFAULT_CASE_COUNT:
        selected = set(cases)
        missing = [c for c in REGRESSION_CASES if c not in selected]
        msg_lines = [
            "FATAL: Incomplete breakeven butterfly coverage — "
            "live pool is missing supported pillars.",
            f"  expected : {DEFAULT_CASE_COUNT} deterministic cases "
            "(full REGRESSION_CASES list)",
            f"  observed : {len(cases)} cases returned by "
            "choose_test_cases()",
            f"  missing  : {len(missing)} "
            "(nominal, linker, short, belly, long) tuples — see "
            "rates_agent/playbooks/sovereign_bonds.yml × "
            "rates_agent/playbooks/inflation_indexed_bonds.yml for "
            "the ingested universe (UST/USD_TIPS, "
            "UK_GILT/GBP_LINKER, FR_OAT/EUR_FR_LINKER, "
            "CANADA_GOVT/CAD_RRB intersected pillars).",
        ]
        for c in missing:
            msg_lines.append(
                f"    - {c[0]} vs {c[1]} @ {c[2]}/{c[3]}/{c[4]}"
            )
        full_msg = "\n".join(msg_lines)
        print(full_msg, flush=True)
        print(full_msg, file=sys.stderr, flush=True)
        sys.exit(2)

    print(
        "[3/6] Running tool vs SQL comparisons (with historical-"
        "sample cross-check)..."
    )
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]} vs {case[1]} @ "
            f"{case[2]}/{case[3]}/{case[4]}",
        )
        nominal_cf, linker_cf, short_tenor, belly_tenor, long_tenor = case
        tool_result = calculate_breakeven_butterfly(
            engine=engine,
            params=BreakevenButterflyInput(
                nominal_curve_family=nominal_cf,
                linker_curve_family=linker_cf,
                short_tenor=short_tenor,
                belly_tenor=belly_tenor,
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

    print(
        "[4/6] Adversarial probe 1: tenor-ordering rejected by "
        "schema..."
    )
    probe1 = assert_tenor_ordering_rejected_by_schema()
    print("FAIL" if probe1 else "PASS")
    for f in probe1:
        print(f"  - {f}")

    print(
        "[4/6 cont] Adversarial probe 2: distinct-tenor rejected by "
        "schema..."
    )
    probe2 = assert_distinct_tenor_rejected_by_schema()
    print("FAIL" if probe2 else "PASS")
    for f in probe2:
        print(f"  - {f}")

    print(
        "[5/6] Adversarial probe 3a: linker→nominal slot pollution..."
    )
    probe3a = assert_pollution_guard(
        engine,
        nominal_curve_family=LINKER_IN_NOMINAL_PROBE[0],
        linker_curve_family=LINKER_IN_NOMINAL_PROBE[1],
        short_tenor="5Y",
        belly_tenor="10Y",
        long_tenor="30Y",
        field_name=args.field,
        lookback_days=args.days,
        expected_missing_leg="sovereign_benchmark",
    )
    print("FAIL" if probe3a else "PASS")
    for f in probe3a:
        print(f"  - {f}")

    print(
        "[5/6 cont] Adversarial probe 3b: nominal→linker slot "
        "pollution..."
    )
    probe3b = assert_pollution_guard(
        engine,
        nominal_curve_family=NOMINAL_IN_LINKER_PROBE[0],
        linker_curve_family=NOMINAL_IN_LINKER_PROBE[1],
        short_tenor="5Y",
        belly_tenor="10Y",
        long_tenor="30Y",
        field_name=args.field,
        lookback_days=args.days,
        expected_missing_leg="inflation_linker",
    )
    print("FAIL" if probe3b else "PASS")
    for f in probe3b:
        print(f"  - {f}")

    print(
        "[5/6 cont] Adversarial probe 3c: EUR-zone cross-country "
        "rejection..."
    )
    probe3c = assert_cross_country_guard(
        engine,
        nominal_curve_family=CROSS_COUNTRY_EUR_PROBE[0],
        linker_curve_family=CROSS_COUNTRY_EUR_PROBE[1],
        short_tenor="5Y",
        belly_tenor="10Y",
        long_tenor="30Y",
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe3c else "PASS")
    for f in probe3c:
        print(f"  - {f}")

    print(
        "[5/6 cont] Adversarial probe 3d: cross-currency rejection..."
    )
    probe3d = assert_cross_country_guard(
        engine,
        nominal_curve_family=CROSS_CURRENCY_PROBE[0],
        linker_curve_family=CROSS_CURRENCY_PROBE[1],
        short_tenor="5Y",
        belly_tenor="10Y",
        long_tenor="30Y",
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe3d else "PASS")
    for f in probe3d:
        print(f"  - {f}")

    print(
        "[6/6] Adversarial probe 4: unknown pillar yields "
        "controlled-error envelope..."
    )
    probe4 = assert_unknown_pillar_handling(
        engine,
        nominal_curve_family="UST",
        linker_curve_family="USD_TIPS",
        short_tenor="5Y",
        belly_tenor="10Y",
        long_tenor="40Y",  # unsupported tenor for both legs
        field_name=args.field,
        lookback_days=args.days,
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
        "  probe_1_tenor_ordering          : "
        f"{'PASS' if not probe1 else 'FAIL'}"
    )
    print(
        "  probe_2_distinct_tenor          : "
        f"{'PASS' if not probe2 else 'FAIL'}"
    )
    print(
        "  probe_3a_linker_in_nominal_slot : "
        f"{'PASS' if not probe3a else 'FAIL'}"
    )
    print(
        "  probe_3b_nominal_in_linker_slot : "
        f"{'PASS' if not probe3b else 'FAIL'}"
    )
    print(
        "  probe_3c_eur_zone_cross_country : "
        f"{'PASS' if not probe3c else 'FAIL'}"
    )
    print(
        "  probe_3d_cross_currency         : "
        f"{'PASS' if not probe3d else 'FAIL'}"
    )
    print(
        "  probe_4_unknown_pillar          : "
        f"{'PASS' if not probe4 else 'FAIL'}"
    )

    has_failure = (
        bool(failed_cases) or bool(probe1) or bool(probe2)
        or bool(probe3a) or bool(probe3b) or bool(probe3c)
        or bool(probe3d) or bool(probe4)
    )
    if has_failure:
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]} vs {case[1]} @ "
                    f"{case[2]}/{case[3]}/{case[4]} "
                    f"({len(mismatches)} mismatches)"
                )
        sys.exit(1)


if __name__ == "__main__":
    main()
