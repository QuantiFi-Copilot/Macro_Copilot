#!/usr/bin/env python3
"""
test_swap_breakeven_basis_simple_sql_validation.py — Same-tenor,
same-currency swap-breakeven basis validator.

Validate ``calculate_swap_breakeven_basis_simple`` against an
independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched`` ×
``macro_data.instrument_master``.  The SQL baseline reproduces the
per-trade-date basis math (zcis_pct - breakeven_pct, *100) without
going through any of the Python tool's helpers, so a mismatch
surfaces a real divergence in methodology rather than a
shared-code coincidence.

Deterministic same-currency coverage
------------------------------------
The basis universe is bounded by the live linker / ZCIS pillar
coverage:

  - USD: USD_ZCIS x UST x USD_TIPS — pillars where USD_TIPS,
    USD_ZCIS, and UST all have data: {5Y, 10Y, 20Y, 30Y} (4).
  - EUR: EUR_ZCIS x FR_OAT x EUR_FR_LINKER (the breakeven
    primitive's canonical EUR pair) — pillar intersection:
    {2Y, 5Y, 10Y} (3).
  - GBP: GBP_ZCIS x UK_GILT x GBP_LINKER — pillar intersection:
    {1Y, 2Y, 3Y, 5Y, 10Y, 20Y, 30Y} (7).

Total deterministic coverage: 4 + 3 + 7 = **14** cases.  Stable
ordering so failures are easy to diff across runs.
``DEFAULT_CASE_COUNT == len(REGRESSION_CASES) == 14`` so the
helper ``sample_cases`` returns the full fixed list in stable
order on every run.

Adversarial probes (6):
  1. ``same_curve``: nominal_curve_family ==
     linker_curve_family is rejected at the Pydantic schema
     layer (no fall-through to compute).
  2. ``unknown_pillar``: an unknown / unsupported tenor on any
     leg surfaces a controlled error envelope, NOT a Python
     exception.
  3. ``unknown_curve``: a non-ZCIS ``zcis_curve_family``
     (e.g. 'UST') yields a controlled error envelope (the inner
     ZCIS level primitive's four-conjunct guard refuses it).
  4. ``four_conjunct_guard``: the basis compute issues NO raw
     market-data SELECTs of its own; both the ZCIS leg's
     four-conjunct guard AND the breakeven leg's instrument_type
     discriminators are inherited transitively from the inner
     primitives.  Verified by source-grep that the basis
     primitive's compute.py contains no raw FROM market_data /
     instrument_master / v_market_data_daily_enriched
     references.
  5. ``sign_convention_inversion``: sanity probe for the
     SUPPORTED sign convention — under
     ``zcis_minus_breakeven``, the basis sign matches
     ``zcis_pct > breakeven_pct``.
  6. ``unsupported_sign_convention_guard``: setting
     ``swap_breakeven_basis_sign_convention`` to anything other
     than ``zcis_minus_breakeven`` raises NotImplementedError, and
     the guard message points callers at
     ``methodology.planned_extensions``, names the supported
     value, AND echoes the offending value.

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \\
        tests/test_swap_breakeven_basis_simple_sql_validation.py
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (  # noqa: E402
    SwapBreakevenBasisSimpleInput,
    calculate_swap_breakeven_basis_simple,
)
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (  # noqa: E402
    compute as basis_compute_module,
)
from shared.analytics.curve_bootstrap import tenor_to_years  # noqa: E402
from shared.config import load_tool_config  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (zcis_curve_family, nominal_curve_family, linker_curve_family, tenor)
Case = Tuple[str, str, str, str]


# Full deterministic coverage of the swap-breakeven basis
# universe — derived from the live universe's per-currency pillar
# intersection (USD_TIPS / EUR_FR_LINKER / GBP_LINKER linker
# coverage, intersected with the corresponding ZCIS and nominal
# pillar grids).
_USD_PILLARS: Tuple[str, ...] = ("5Y", "10Y", "20Y", "30Y")
_EUR_PILLARS: Tuple[str, ...] = ("2Y", "5Y", "10Y")
_GBP_PILLARS: Tuple[str, ...] = (
    "1Y", "2Y", "3Y", "5Y", "10Y", "20Y", "30Y",
)


_BASIS_TRIPLES: Tuple[Tuple[str, str, str, Tuple[str, ...]], ...] = (
    ("USD_ZCIS", "UST",      "USD_TIPS",      _USD_PILLARS),
    ("EUR_ZCIS", "FR_OAT",   "EUR_FR_LINKER", _EUR_PILLARS),
    ("GBP_ZCIS", "UK_GILT",  "GBP_LINKER",    _GBP_PILLARS),
)


def _enumerate_basis_cases() -> List[Case]:
    cases: List[Case] = []
    for zcis_fam, nominal_fam, linker_fam, pillars in _BASIS_TRIPLES:
        # Stable per-pillar ordering by tenor_to_years.
        sorted_pillars = sorted(pillars, key=lambda t: tenor_to_years(t))
        for tenor in sorted_pillars:
            cases.append((zcis_fam, nominal_fam, linker_fam, tenor))
    return cases


REGRESSION_CASES: List[Case] = _enumerate_basis_cases()
DEFAULT_CASE_COUNT = len(REGRESSION_CASES)
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_ZCIS_FIELD_NAME = "PX_MID"
DEFAULT_BOND_FIELD_NAME = "YLD_YTM_MID"


# Tolerances inherited from the cross_market_inflation_swap_spread
# validator — rounding conventions are aligned by config lint.
# The basis flows through one extra arithmetic layer (breakeven
# inner subtraction) plus a pct↔bps round-trip on the breakeven
# series; keep the same display tolerances as a safe upper bound.
TOLERANCE_BY_FIELD = {
    "basis_pct": 0.00021,
    "basis_bps": 0.021,
    "change_1d_bps": 0.021,
    "change_1w_bps": 0.021,
    "change_1m_bps": 0.021,
    "z_score_252d": 0.006,
    "high_252d_bps": 0.021,
    "low_252d_bps": 0.021,
    "percentile_252d": 0.21,
    "zcis_pct": 0.00011,
    "breakeven_pct": 0.00021,
    "breakeven_bps": 0.021,
    "nominal_yield_pct": 0.00011,
    "real_yield_pct": 0.00011,
    "tenor_years": 1e-4,
    "basis_pct_row": 0.00021,
    "basis_bps_row": 0.021,
    "zcis_pct_row": 0.00011,
    "breakeven_pct_row": 0.00021,
}


def choose_test_cases(
    engine, *, zcis_field_name: str, bond_field_name: str,
    case_count: int, seed: int,
) -> List[Case]:
    """Pick (zcis, nominal, linker, tenor) cases from live universe
    metadata.  Filters to the ``instrument_type`` × ZCIS
    ``pricing_type`` discriminators so the SQL baseline operates
    on the same set the Python tool sees.  Requires at least 80
    observations on EVERY leg (ZCIS, nominal, linker) of every
    candidate.
    """
    zcis_pillar_query = text(
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
    bond_pillar_query = text(
        """
        SELECT v.curve_family, v.tenor, v.instrument_type
        FROM macro_data.v_market_data_daily_enriched v
        WHERE v.instrument_type IN ('sovereign_benchmark', 'inflation_linker')
          AND v.field_name = :field_name
        GROUP BY v.curve_family, v.tenor, v.instrument_type
        HAVING COUNT(*) >= 80
        ORDER BY v.curve_family, v.tenor, v.instrument_type
        """
    )
    with engine.connect() as conn:
        zcis_rows = conn.execute(
            zcis_pillar_query, {"field_name": zcis_field_name},
        ).mappings().all()
        bond_rows = conn.execute(
            bond_pillar_query, {"field_name": bond_field_name},
        ).mappings().all()

    zcis_pillars: Dict[str, set] = {}
    for r in zcis_rows:
        zcis_pillars.setdefault(r["curve_family"], set()).add(r["tenor"])

    bond_pillars: Dict[Tuple[str, str], set] = {}
    for r in bond_rows:
        bond_pillars.setdefault(
            (r["curve_family"], r["instrument_type"]), set(),
        ).add(r["tenor"])

    pool: List[Case] = []
    for zcis_fam, nominal_fam, linker_fam, _pillars in _BASIS_TRIPLES:
        zcis_set = zcis_pillars.get(zcis_fam, set())
        nominal_set = bond_pillars.get(
            (nominal_fam, "sovereign_benchmark"), set(),
        )
        linker_set = bond_pillars.get(
            (linker_fam, "inflation_linker"), set(),
        )
        common = zcis_set & nominal_set & linker_set
        parsed: List[Tuple[float, str]] = []
        for t in sorted(common):
            try:
                parsed.append((tenor_to_years(t), t))
            except ValueError:
                continue
        parsed.sort(key=lambda x: x[0])
        for _, tenor in parsed:
            pool.append((zcis_fam, nominal_fam, linker_fam, tenor))

    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    zcis_curve_family: str,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    lookback_days: int,
    zcis_field_name: str,
    bond_field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the same-tenor swap-
    breakeven basis math.

    Algorithm (mirrors the Python tool's composition shape EXACTLY):
      1. Pull the ZCIS leg under the four-conjunct SELECT guard
         (instrument_type='inflation_swap' AND
         pricing_type='zero_coupon_breakeven' AND
         curve_family=? AND tenor=?), ffill within
         ffill_limit_days (5 trading days).
      2. Pull the nominal sovereign leg under
         (instrument_type='sovereign_benchmark' AND
         curve_family=? AND tenor=?).
      3. Pull the linker leg under
         (instrument_type='inflation_linker' AND
         curve_family=? AND tenor=?).
      4. Inner-join nominal and linker on trade_date; round
         (nominal - linker)*100 to 2 decimals = breakeven_bps;
         round breakeven_bps/100 to 4 decimals = breakeven_pct
         (matches the Python tool's pct↔bps round-trip on the
         breakeven series).
      5. Inner-join the ZCIS leg's pct (rounded to 4 decimals) with
         the breakeven_pct on trade_date — same as the basis
         primitive's strict alignment step.
      6. Round (zcis_pct - breakeven_pct) to 4 decimals =
         basis_pct; round basis_pct*100 to 2 decimals = basis_bps.
      7. Compute rolling 252-day z-score (ddof=1, min_periods=60),
         period changes, trailing 252-day high/low/percentile —
         all under the same convention values the Python tool
         uses.
      8. Anchor the display cutoff to the latest aligned
         trade_date.
    """
    t_years = tenor_to_years(tenor)
    extended_lookback_days = lookback_days + 378

    baseline_sql = text(
        """
        WITH zcis_raw AS (
            SELECT
                v.trade_date,
                v.field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched v
            JOIN macro_data.instrument_master i
              ON v.instrument_id = i.instrument_id
            WHERE v.instrument_type = 'inflation_swap'
              AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
              AND v.curve_family   = :zcis_curve_family
              AND v.tenor          = :tenor
              AND v.field_name     = :zcis_field_name
              AND v.field_value IS NOT NULL
              AND v.trade_date >= CURRENT_DATE - ((:lookback_days + 800) * INTERVAL '1 day')
        ),
        zcis_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM zcis_raw
        ),
        zcis_trim AS (
            SELECT z.trade_date, z.field_value
            FROM zcis_raw z, zcis_anchor a
            WHERE z.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        nominal_raw AS (
            SELECT
                v.trade_date,
                v.field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched v
            WHERE v.instrument_type = 'sovereign_benchmark'
              AND v.curve_family   = :nominal_curve_family
              AND v.tenor          = :tenor
              AND v.field_name     = :bond_field_name
              AND v.field_value IS NOT NULL
              AND v.trade_date >= CURRENT_DATE - ((:lookback_days + 800) * INTERVAL '1 day')
        ),
        linker_raw AS (
            SELECT
                v.trade_date,
                v.field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched v
            WHERE v.instrument_type = 'inflation_linker'
              AND v.curve_family   = :linker_curve_family
              AND v.tenor          = :tenor
              AND v.field_name     = :bond_field_name
              AND v.field_value IS NOT NULL
              AND v.trade_date >= CURRENT_DATE - ((:lookback_days + 800) * INTERVAL '1 day')
        ),
        bk_anchor AS (
            -- Breakeven anchoring matches the breakeven primitive's
            -- inner alignment: nominal AND linker must both exist on
            -- the trade_date.
            SELECT MAX(n.trade_date) AS anchor
            FROM nominal_raw n
            INNER JOIN linker_raw l ON l.trade_date = n.trade_date
        ),
        nominal_trim AS (
            SELECT n.trade_date, n.field_value
            FROM nominal_raw n, bk_anchor a
            WHERE n.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        linker_trim AS (
            SELECT l.trade_date, l.field_value
            FROM linker_raw l, bk_anchor a
            WHERE l.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        breakeven_raw AS (
            SELECT
                n.trade_date,
                ROUND(
                    ((n.field_value - l.field_value) * 100)::numeric,
                    2
                )::double precision AS breakeven_bps,
                n.field_value::double precision AS nominal_yield_pct,
                l.field_value::double precision AS real_yield_pct
            FROM nominal_trim n
            INNER JOIN linker_trim l ON l.trade_date = n.trade_date
        ),
        breakeven_with_pct AS (
            SELECT
                trade_date,
                breakeven_bps,
                ROUND((breakeven_bps / 100.0)::numeric, 4)::double precision AS breakeven_pct,
                nominal_yield_pct,
                real_yield_pct
            FROM breakeven_raw
        ),
        aligned AS (
            SELECT
                z.trade_date,
                ROUND((z.field_value)::numeric, 4)::double precision AS zcis_pct,
                b.breakeven_pct,
                b.breakeven_bps,
                b.nominal_yield_pct,
                b.real_yield_pct
            FROM zcis_trim z
            INNER JOIN breakeven_with_pct b ON b.trade_date = z.trade_date
        ),
        basis_rows AS (
            SELECT
                trade_date,
                zcis_pct,
                breakeven_pct,
                breakeven_bps,
                nominal_yield_pct,
                real_yield_pct,
                ROUND(
                    (zcis_pct - breakeven_pct)::numeric,
                    4
                )::double precision AS basis_pct,
                ROUND(
                    ((zcis_pct - breakeven_pct) * 100)::numeric,
                    2
                )::double precision AS basis_bps
            FROM aligned
        ),
        renumbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn,
                zcis_pct,
                breakeven_pct,
                breakeven_bps,
                nominal_yield_pct,
                real_yield_pct,
                basis_pct,
                basis_bps
            FROM basis_rows
        ),
        scored AS (
            SELECT
                trade_date,
                arn,
                zcis_pct,
                breakeven_pct,
                breakeven_bps,
                nominal_yield_pct,
                real_yield_pct,
                basis_pct,
                basis_bps,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(basis_bps) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(basis_bps) OVER zw <> 0
                    THEN ROUND(
                        (
                            (basis_bps - AVG(basis_bps) OVER zw)
                            / NULLIF(STDDEV_SAMP(basis_bps) OVER zw, 0)
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
                zcis_pct,
                breakeven_pct,
                breakeven_bps,
                nominal_yield_pct,
                real_yield_pct,
                basis_pct,
                basis_bps,
                z_score,
                CASE
                    WHEN LAG(basis_bps, 1) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (basis_bps - LAG(basis_bps, 1) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS change_1d_bps,
                CASE
                    WHEN LAG(basis_bps, 5) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (basis_bps - LAG(basis_bps, 5) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS change_1w_bps,
                CASE
                    WHEN LAG(basis_bps, 21) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (basis_bps - LAG(basis_bps, 21) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS change_1m_bps,
                ROUND((MAX(basis_bps) OVER tw)::numeric, 2)::double precision AS high_252d_bps,
                ROUND((MIN(basis_bps) OVER tw)::numeric, 2)::double precision AS low_252d_bps,
                CASE
                    WHEN MAX(basis_bps) OVER tw IS NULL
                      OR MIN(basis_bps) OVER tw IS NULL
                      OR MAX(basis_bps) OVER tw = MIN(basis_bps) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                basis_bps - MIN(basis_bps) OVER tw
                            ) / NULLIF(
                                MAX(basis_bps) OVER tw - MIN(basis_bps) OVER tw,
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
            zcis_pct,
            breakeven_pct,
            breakeven_bps,
            nominal_yield_pct,
            real_yield_pct,
            basis_pct,
            basis_bps,
            z_score,
            change_1d_bps,
            change_1w_bps,
            change_1m_bps,
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
                "zcis_curve_family": zcis_curve_family,
                "nominal_curve_family": nominal_curve_family,
                "linker_curve_family": linker_curve_family,
                "tenor": tenor,
                "zcis_field_name": zcis_field_name,
                "bond_field_name": bond_field_name,
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
            "zcis_curve_family": zcis_curve_family,
            "nominal_curve_family": nominal_curve_family,
            "linker_curve_family": linker_curve_family,
            "tenor": tenor,
            "tenor_years": round(t_years, 4),
            "basis_label": (
                f"{zcis_curve_family} - "
                f"{nominal_curve_family}/{linker_curve_family} "
                f"{tenor} swap-breakeven basis"
            ),
            "basis_pct": latest["basis_pct"],
            "basis_bps": latest["basis_bps"],
            "zcis_pct": latest["zcis_pct"],
            "breakeven_pct": latest["breakeven_pct"],
            "breakeven_bps": latest["breakeven_bps"],
            "nominal_yield_pct": (
                round(float(latest["nominal_yield_pct"]), 4)
                if latest["nominal_yield_pct"] is not None else None
            ),
            "real_yield_pct": (
                round(float(latest["real_yield_pct"]), 4)
                if latest["real_yield_pct"] is not None else None
            ),
            "change_1d_bps": latest["change_1d_bps"],
            "change_1w_bps": latest["change_1w_bps"],
            "change_1m_bps": latest["change_1m_bps"],
            "z_score_252d": latest["z_score"],
            "high_252d_bps": latest["high_252d_bps"],
            "low_252d_bps": latest["low_252d_bps"],
            "percentile_252d": latest["percentile_252d"],
        },
        "time_series": [
            {
                "date": row["date"],
                "basis_pct": row["basis_pct"],
                "basis_bps": row["basis_bps"],
                "zcis_pct": row["zcis_pct"],
                "breakeven_pct": row["breakeven_pct"],
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
            "zcis_curve_family",
            "nominal_curve_family",
            "linker_curve_family",
            "tenor",
            "basis_label",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "basis_pct",
            "basis_bps",
            "zcis_pct",
            "breakeven_pct",
            "breakeven_bps",
            "nominal_yield_pct",
            "real_yield_pct",
            "change_1d_bps",
            "change_1w_bps",
            "change_1m_bps",
            "z_score_252d",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "tenor_years",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # ZCIS-leg reference metadata: every successful run must
    # populate these fields on the ZCIS leg.  An empty value
    # would indicate the per-leg metadata threading regressed.
    for ref_field in (
        "zcis_inflation_index_family",
        "zcis_index_lag",
        "zcis_interpolation",
    ):
        v = tool_metrics.get(ref_field)
        if not v:
            mismatches.append(
                f"current_metrics.{ref_field}: tool returned "
                "empty / None — load-bearing ZCIS-leg reference "
                "metadata must be populated."
            )

    # Derived index-family summary: ``index_families_match`` is a
    # required derived top-level summary field on current_metrics.
    if "index_families_match" not in tool_metrics:
        mismatches.append(
            "current_metrics.index_families_match: tool did not "
            "surface the derived top-level summary field."
        )

    # methodology_label must be non-empty (threaded from YAML) and
    # must surface the load-bearing BASIS CAVEAT.
    label = tool_metrics.get("methodology_label", "") or ""
    if not label:
        mismatches.append(
            "current_metrics.methodology_label: tool returned "
            "empty — must be threaded from YAML's "
            "methodology.what_it_does."
        )
    else:
        lo = label.lower()
        if "not a clean liquidity-premium read" not in lo:
            mismatches.append(
                "current_metrics.methodology_label: missing the "
                "load-bearing BASIS CAVEAT (must mention 'not a "
                "clean liquidity-premium read' so the desk reader "
                "is warned that the basis reflects index-lag / "
                "linker on-the-run / structural ZCIS basis)."
            )
        if "index-lag" not in lo:
            mismatches.append(
                "current_metrics.methodology_label: missing the "
                "'index-lag' driver mention required for honest "
                "interpretation of the basis."
            )

    row_tolerances = dict(TOLERANCE_BY_FIELD)
    row_tolerances["basis_pct"] = TOLERANCE_BY_FIELD["basis_pct_row"]
    row_tolerances["basis_bps"] = TOLERANCE_BY_FIELD["basis_bps_row"]
    row_tolerances["zcis_pct"] = TOLERANCE_BY_FIELD["zcis_pct_row"]
    row_tolerances["breakeven_pct"] = (
        TOLERANCE_BY_FIELD["breakeven_pct_row"]
    )
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=(
                "basis_pct", "basis_bps", "zcis_pct", "breakeven_pct",
            ),
            tolerances=row_tolerances,
        )
    )
    return mismatches


# ============================================================================
# ADVERSARIAL PROBES
# ============================================================================

def assert_same_curve_rejected_by_schema() -> List[str]:
    """Probe 1 (``same_curve``):
    nominal_curve_family == linker_curve_family is rejected at the
    Pydantic schema layer.
    """
    failures: List[str] = []
    try:
        _ = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="UST",
            tenor="10Y",
        )
    except Exception as exc:
        raw_msg = str(exc)
        msg = raw_msg.lower()
        if "must be different" not in msg and "must differ" not in msg:
            failures.append(
                "same_curve probe: rejection message must explain "
                "the cross-leg invariant; got: "
                f"{exc!r}"
            )
        if "cross_market_inflation_swap_spread" not in raw_msg:
            failures.append(
                "same_curve probe: rejection message must include "
                "the sibling-tool re-route hint naming "
                "'cross_market_inflation_swap_spread' so callers "
                "can be redirected to the cross-curve ZCIS "
                f"primitive; got: {exc!r}"
            )
        if (
            "calculate_cross_market_inflation_swap_spread_tool"
            not in raw_msg
        ):
            failures.append(
                "same_curve probe: rejection message must also "
                "include the MCP tool name "
                "'calculate_cross_market_inflation_swap_spread_tool' "
                "so wire-level callers see both forms of the "
                f"re-route hint; got: {exc!r}"
            )
        return failures
    failures.append(
        "same_curve probe: input schema accepted "
        "nominal_curve_family='UST' AND linker_curve_family='UST'.  "
        "The _curve_families_must_differ validator must reject "
        "this — same-issuer breakeven legs collapse to zero."
    )
    return failures


def assert_unknown_pillar_handling(
    engine,
    *,
    zcis_curve_family: str,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    lookback_days: int,
) -> List[str]:
    """Probe 2 (``unknown_pillar``): an unknown / unsupported
    tenor on any leg MUST yield a controlled error envelope, NOT a
    Python exception.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_swap_breakeven_basis_simple(
            engine=engine,
            params=SwapBreakevenBasisSimpleInput(
                zcis_curve_family=zcis_curve_family,
                nominal_curve_family=nominal_curve_family,
                linker_curve_family=linker_curve_family,
                tenor=tenor,
                lookback_days=lookback_days,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"unknown_pillar probe ({zcis_curve_family} - "
            f"{nominal_curve_family}/{linker_curve_family} {tenor})"
            f" raised {type(exc).__name__}: {exc} — must return "
            "controlled error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            f"unknown_pillar probe ({zcis_curve_family} - "
            f"{nominal_curve_family}/{linker_curve_family} {tenor}) "
            "returned a snapshot — must return controlled error "
            f"envelope instead.  Got keys: "
            f"{sorted(tool_result.keys())}"
        )
        return failures

    err = tool_result["error"]
    # Either the ZCIS leg or the breakeven leg should fail with a
    # leg-attributed prefix.  Both legs' inner-error rationales are
    # acceptable.
    if not (
        "inflation_swap" in err
        or "inflation_linker" in err
        or "sovereign_benchmark" in err
    ):
        failures.append(
            "unknown_pillar probe: controlled error envelope "
            "missing instrument_type rationale (composition guard "
            f"inheritance broke); got: {err!r}"
        )
    return failures


def assert_unknown_curve_handling(
    engine, *, lookback_days: int,
) -> List[str]:
    """Probe 3 (``unknown_curve``): a non-ZCIS
    ``zcis_curve_family`` (e.g. 'UST') yields zero rows through
    the four-conjunct SELECT guard; the inner ZCIS level
    primitive surfaces the controlled error and we propagate it
    with zcis-leg attribution.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_swap_breakeven_basis_simple(
            engine=engine,
            params=SwapBreakevenBasisSimpleInput(
                zcis_curve_family="UST",
                nominal_curve_family="UST",
                # nominal == linker would be rejected at schema, so
                # use a real linker on the breakeven leg — the
                # ZCIS leg is the load-bearing failure here.
                linker_curve_family="USD_TIPS",
                tenor="10Y",
                lookback_days=lookback_days,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            "unknown_curve probe (UST as zcis_curve_family) raised "
            f"{type(exc).__name__}: {exc} — must return controlled "
            "error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            "unknown_curve probe (UST as zcis_curve_family) "
            "returned a snapshot — must return controlled error "
            f"envelope instead.  Got keys: "
            f"{sorted(tool_result.keys())}"
        )
        return failures

    err = tool_result["error"]
    if "inflation_swap" not in err:
        failures.append(
            "unknown_curve probe: controlled error envelope "
            "missing 'inflation_swap' rationale (the inner ZCIS "
            f"level primitive's four-conjunct guard); got: {err!r}"
        )
    if "zero_coupon_breakeven" not in err:
        failures.append(
            "unknown_curve probe: controlled error envelope "
            "missing 'zero_coupon_breakeven' rationale; got: "
            f"{err!r}"
        )
    return failures


def assert_four_conjunct_select_guard_inherited(engine) -> List[str]:
    """Probe 4 (``four_conjunct_guard``): the basis primitive's
    compute MUST NOT issue any raw market-data SELECTs of its own
    — both the ZCIS leg's four-conjunct guard AND the breakeven
    leg's instrument_type discriminators live inside the inner
    primitives.  Verified by source-grep.
    """
    failures: List[str] = []
    import re
    source = inspect.getsource(basis_compute_module)
    pattern = re.compile(
        r"FROM\s+(market_data|instrument_master|"
        r"v_market_data_daily_enriched|"
        r"macro_data\.market_data|"
        r"macro_data\.instrument_master|"
        r"macro_data\.v_market_data_daily_enriched)",
        re.IGNORECASE,
    )
    matches = pattern.findall(source)
    if matches:
        failures.append(
            "four_conjunct_guard probe: basis compute.py contains "
            f"raw market-data FROM-clauses ({matches!r}) — both "
            "the ZCIS four-conjunct guard AND the breakeven "
            "instrument_type discriminators must live inside the "
            "inner primitives, NOT here."
        )
    return failures


def _probe_sign_inversion_sanity(
    engine, *, lookback_days: int,
) -> List[str]:
    """Probe 5 (``sign_convention_inversion``): under the
    SUPPORTED sign convention (``zcis_minus_breakeven``), the basis
    sign must agree with ``zcis_pct > breakeven_pct`` on the
    snapshot row.  A regression to a normalised direction is
    caught here.
    """
    failures: List[str] = []
    zcis, nominal, linker, tenor = (
        "USD_ZCIS", "UST", "USD_TIPS", "10Y",
    )

    out = calculate_swap_breakeven_basis_simple(
        engine=engine,
        params=SwapBreakevenBasisSimpleInput(
            zcis_curve_family=zcis,
            nominal_curve_family=nominal,
            linker_curve_family=linker,
            tenor=tenor,
            lookback_days=lookback_days,
        ),
    )
    if "error" in out:
        failures.append(
            "sign_convention_inversion probe: tool returned an "
            f"error envelope.  err={out.get('error')!r}"
        )
        return failures

    cm = out["current_metrics"]
    basis_bps = cm.get("basis_bps")
    zcis_pct = cm.get("zcis_pct")
    breakeven_pct = cm.get("breakeven_pct")
    if basis_bps is None or zcis_pct is None or breakeven_pct is None:
        failures.append(
            "sign_convention_inversion probe: snapshot fields "
            f"contain None.  basis_bps={basis_bps!r} "
            f"zcis_pct={zcis_pct!r} breakeven_pct={breakeven_pct!r}"
        )
        return failures

    expected_sign = (
        1 if zcis_pct > breakeven_pct
        else -1 if zcis_pct < breakeven_pct
        else 0
    )
    actual_sign = (
        1 if basis_bps > 0 else -1 if basis_bps < 0 else 0
    )
    # Allow the no-rounding-edge-case sanity:  if zcis ≈ breakeven
    # by less than one bps_round_decimals, the basis_bps sign can
    # legitimately be 0.
    if expected_sign != actual_sign and abs(basis_bps) > 0.05:
        failures.append(
            "sign_convention_inversion probe: basis sign does NOT "
            "agree with zcis_pct vs breakeven_pct under the "
            "supported sign convention (zcis_minus_breakeven).  "
            f"zcis_pct={zcis_pct} breakeven_pct={breakeven_pct} "
            f"basis_bps={basis_bps} (expected_sign={expected_sign}, "
            f"actual_sign={actual_sign})."
        )

    ts_basis = out.get("time_series_basis") or {}
    description = ts_basis.get("description") or ""
    if "zcis_minus_breakeven" not in description:
        failures.append(
            "sign_convention_inversion probe: time_series_basis."
            "description must surface the literal sign-convention "
            "token 'zcis_minus_breakeven' so a downstream caller "
            "can grep the rendered TimeSeries description and "
            "verify which sign convention produced the BPS series. "
            f"observed description={description!r}"
        )
    return failures


def _probe_unsupported_sign_convention_guard(
    engine, *, lookback_days: int,
) -> List[str]:
    """Probe 6 (``unsupported_sign_convention_guard``): the
    compute-layer guard for ``swap_breakeven_basis_sign_convention``
    MUST raise ``NotImplementedError`` when the convention is
    anything other than ``zcis_minus_breakeven`` AND the exception
    message MUST point callers at ``methodology.planned_extensions``,
    name the supported value, AND echo the offending value.

    Mutates the loaded ToolConfig in-memory (does NOT touch the
    on-disk YAML) so the validator can probe the guard without
    affecting any other run.
    """
    failures: List[str] = []
    zcis, nominal, linker, tenor = (
        "USD_ZCIS", "UST", "USD_TIPS", "10Y",
    )

    base_cfg = load_tool_config(
        Path(__file__).resolve().parent.parent
        / "rates_agent" / "inflation_swaps" / "tools"
        / "swap_breakeven_basis_simple" / "config.yaml"
    )
    base_conv = base_cfg.conventions["swap_breakeven_basis_sign_convention"]
    overridden_conv = base_conv.model_copy(
        update={"value": "breakeven_minus_zcis"},
    )
    new_conventions = dict(base_cfg.conventions)
    new_conventions["swap_breakeven_basis_sign_convention"] = overridden_conv
    mutated_cfg = base_cfg.model_copy(
        update={"conventions": new_conventions},
    )

    raised: Exception | None = None
    try:
        _ = calculate_swap_breakeven_basis_simple(
            engine=engine,
            params=SwapBreakevenBasisSimpleInput(
                zcis_curve_family=zcis,
                nominal_curve_family=nominal,
                linker_curve_family=linker,
                tenor=tenor,
                lookback_days=lookback_days,
            ),
            config=mutated_cfg,
        )
    except NotImplementedError as exc:
        raised = exc
    except Exception as exc:  # noqa: BLE001
        failures.append(
            "unsupported_sign_convention_guard probe: expected "
            f"NotImplementedError, got {type(exc).__name__}: {exc}."
        )
        return failures

    if raised is None:
        failures.append(
            "unsupported_sign_convention_guard probe: compute did "
            "NOT raise NotImplementedError when "
            "swap_breakeven_basis_sign_convention="
            "'breakeven_minus_zcis'.  V1 must refuse anything "
            "other than 'zcis_minus_breakeven' per the catalog's "
            "methodology_guardrails."
        )
        return failures

    msg = str(raised)
    if "methodology.planned_extensions" not in msg:
        failures.append(
            "unsupported_sign_convention_guard probe: guard "
            "message MUST contain the literal "
            "'methodology.planned_extensions' so a ripgrep-style "
            f"audit can confirm the pointer.  Got: {msg!r}"
        )
    if "zcis_minus_breakeven" not in msg:
        failures.append(
            "unsupported_sign_convention_guard probe: guard "
            "message MUST name the only supported value "
            f"('zcis_minus_breakeven').  Got: {msg!r}"
        )
    if "breakeven_minus_zcis" not in msg:
        failures.append(
            "unsupported_sign_convention_guard probe: guard "
            "message MUST echo the offending value "
            f"('breakeven_minus_zcis') so the caller can "
            f"self-diagnose.  Got: {msg!r}"
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
            f"Historical-sample length mismatch: "
            f"tool={len(tool_rows)} sql={len(sql_rows)}"
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
        if t["basis_bps"] is None and s["basis_bps"] is None:
            continue
        if t["basis_bps"] is None or s["basis_bps"] is None:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['basis_bps']} sql={s['basis_bps']}"
            )
            continue
        delta = abs(
            float(t["basis_bps"]) - float(s["basis_bps"])
        )
        if delta > TOLERANCE_BY_FIELD["basis_bps_row"] + 1e-12:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['basis_bps']} sql={s['basis_bps']} "
                f"(delta={delta:.6f})"
            )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the swap_breakeven_basis_simple tool against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--zcis-field", default=DEFAULT_ZCIS_FIELD_NAME,
    )
    parser.add_argument(
        "--bond-field", default=DEFAULT_BOND_FIELD_NAME,
    )
    args = parser.parse_args()

    print("=" * 80)
    print("SWAP_BREAKEVEN_BASIS_SIMPLE TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases               : {args.cases}")
    print(f"  random_seed         : {args.seed}")
    print(f"  lookback_days       : {args.days}")
    print(f"  zcis_field_name     : {args.zcis_field}")
    print(f"  bond_field_name     : {args.bond_field}")
    print(
        f"  total_supported     : {len(REGRESSION_CASES)} "
        "(USD x 4 + EUR x 3 + GBP x 7 = 14)"
    )
    print("-" * 80)

    print("[1/7] Creating DB engine...")
    engine = get_db_engine()

    print(
        "[2/7] Selecting validation cases from live universe "
        "metadata..."
    )
    cases = choose_test_cases(
        engine,
        zcis_field_name=args.zcis_field,
        bond_field_name=args.bond_field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases,
        lambda case: f"{case[0]} - {case[1]}/{case[2]} {case[3]}",
    )

    # Fail loudly when the default-coverage invocation produces fewer
    # than DEFAULT_CASE_COUNT cases, instead of silently passing on
    # an incomplete pool.  Inherited contract from the curve_spread
    # round-2 fix and the cross_market validator: FATAL stdout
    # block + FATAL stderr block + sys.exit(2) on coverage shortfall.
    if args.cases == DEFAULT_CASE_COUNT and len(cases) != DEFAULT_CASE_COUNT:
        selected = set(cases)
        missing = [c for c in REGRESSION_CASES if c not in selected]
        msg_lines = [
            "FATAL: Incomplete swap-breakeven basis coverage — "
            "live pool is missing supported (zcis, nominal, "
            "linker, tenor) combinations.",
            f"  expected : {DEFAULT_CASE_COUNT} deterministic "
            "cases (full REGRESSION_CASES list)",
            f"  observed : {len(cases)} cases returned by "
            "choose_test_cases()",
            f"  missing  : {len(missing)} "
            "(zcis_curve_family, nominal_curve_family, "
            "linker_curve_family, tenor) tuples — see the "
            "inflation_swaps + sovereign_bonds + "
            "inflation_indexed_bonds playbooks for the ingested "
            "universe.",
        ]
        for c in missing:
            msg_lines.append(
                f"    - {c[0]} - {c[1]}/{c[2]} {c[3]}"
            )
        full_msg = "\n".join(msg_lines)
        print(full_msg, flush=True)
        print(full_msg, file=sys.stderr, flush=True)
        sys.exit(2)

    print("[3/7] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]} - {case[1]}/{case[2]} {case[3]}",
        )
        zcis_fam, nominal_fam, linker_fam, tenor = case
        tool_result = calculate_swap_breakeven_basis_simple(
            engine=engine,
            params=SwapBreakevenBasisSimpleInput(
                zcis_curve_family=zcis_fam,
                nominal_curve_family=nominal_fam,
                linker_curve_family=linker_fam,
                tenor=tenor,
                lookback_days=args.days,
            ),
        )
        sql_result = sql_baseline(
            engine=engine,
            zcis_curve_family=zcis_fam,
            nominal_curve_family=nominal_fam,
            linker_curve_family=linker_fam,
            tenor=tenor,
            lookback_days=args.days,
            zcis_field_name=args.zcis_field,
            bond_field_name=args.bond_field,
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
                print(
                    f"  - ... plus {len(mismatches) - 10} "
                    "more mismatches"
                )
            failed_cases.append((case, mismatches))
        else:
            print("PASS")

    print(
        "[4/7] Adversarial probe 1 (``same_curve``): rejected by "
        "schema..."
    )
    probe1 = assert_same_curve_rejected_by_schema()
    print("FAIL" if probe1 else "PASS")
    for f in probe1:
        print(f"  - {f}")

    print(
        "[4/7 cont] Adversarial probe 2 (``unknown_pillar``): "
        "controlled-error envelope..."
    )
    probe2 = assert_unknown_pillar_handling(
        engine,
        zcis_curve_family="USD_ZCIS",
        nominal_curve_family="UST",
        linker_curve_family="USD_TIPS",
        tenor="11Y",  # unsupported pillar
        lookback_days=args.days,
    )
    print("FAIL" if probe2 else "PASS")
    for f in probe2:
        print(f"  - {f}")

    print(
        "[5/7] Adversarial probe 3 (``unknown_curve``): non-ZCIS "
        "zcis_curve_family rejected via four-conjunct guard..."
    )
    probe3 = assert_unknown_curve_handling(
        engine, lookback_days=args.days,
    )
    print("FAIL" if probe3 else "PASS")
    for f in probe3:
        print(f"  - {f}")

    print(
        "[5/7 cont] Adversarial probe 4 (``four_conjunct_guard``): "
        "transitive guard inheritance + no raw SELECTs in basis "
        "compute..."
    )
    probe4 = assert_four_conjunct_select_guard_inherited(engine)
    print("FAIL" if probe4 else "PASS")
    for f in probe4:
        print(f"  - {f}")

    print(
        "[6/7] Adversarial probe 5 (``sign_convention_inversion``): "
        "supported sign convention preserves zcis_pct vs "
        "breakeven_pct ordering..."
    )
    probe5 = _probe_sign_inversion_sanity(
        engine, lookback_days=args.days,
    )
    print("FAIL" if probe5 else "PASS")
    for f in probe5:
        print(f"  - {f}")

    print(
        "[7/7] Adversarial probe 6 "
        "(``unsupported_sign_convention_guard``): compute raises "
        "NotImplementedError + message points at "
        "methodology.planned_extensions..."
    )
    probe6 = _probe_unsupported_sign_convention_guard(
        engine, lookback_days=args.days,
    )
    print("FAIL" if probe6 else "PASS")
    for f in probe6:
        print(f"  - {f}")

    print("Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")
    print(
        "  probe_1_same_curve                       : "
        f"{'PASS' if not probe1 else 'FAIL'}"
    )
    print(
        "  probe_2_unknown_pillar                   : "
        f"{'PASS' if not probe2 else 'FAIL'}"
    )
    print(
        "  probe_3_unknown_curve                    : "
        f"{'PASS' if not probe3 else 'FAIL'}"
    )
    print(
        "  probe_4_four_conjunct_guard              : "
        f"{'PASS' if not probe4 else 'FAIL'}"
    )
    print(
        "  probe_5_sign_convention_inversion        : "
        f"{'PASS' if not probe5 else 'FAIL'}"
    )
    print(
        "  probe_6_unsupported_sign_convention_guard: "
        f"{'PASS' if not probe6 else 'FAIL'}"
    )

    has_failure = (
        bool(failed_cases) or bool(probe1) or bool(probe2)
        or bool(probe3) or bool(probe4) or bool(probe5)
        or bool(probe6)
    )
    if has_failure:
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]} - {case[1]}/{case[2]} "
                    f"{case[3]} ({len(mismatches)} mismatches)"
                )
        sys.exit(1)


if __name__ == "__main__":
    main()
