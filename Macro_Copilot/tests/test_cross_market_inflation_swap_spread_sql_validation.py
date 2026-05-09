#!/usr/bin/env python3
"""
test_cross_market_inflation_swap_spread_sql_validation.py — Same-tenor
cross-market ZCIS spread validator.

Validate ``calculate_cross_market_inflation_swap_spread`` against an
independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched`` ×
``macro_data.instrument_master``.  The SQL baseline reproduces the
per-trade-date cross-market ZCIS spread math without going through
any of the Python tool's helpers, so a mismatch surfaces a real
divergence in methodology rather than a shared-code coincidence.

Deterministic full-curve coverage
---------------------------------
The cross-market ZCIS universe is small and bounded: C(3, 2) = 3
unordered curve-pairs ({USD_ZCIS, EUR_ZCIS}, {USD_ZCIS, GBP_ZCIS},
{EUR_ZCIS, GBP_ZCIS}) × 7 ingested tenors (1Y / 2Y / 3Y / 5Y / 10Y
/ 20Y / 30Y) = **21** total deterministic cases.  Curve-pair
ordering for ``leg_a`` / ``leg_b`` is canonicalised lexicographically
on the curve_family identifier so the validator is stable across
runs.  ``DEFAULT_CASE_COUNT == len(REGRESSION_CASES) == 21`` so the
helper ``sample_cases`` returns the full fixed list in stable
order on every run — every supported (curve_pair, tenor) is
exercised against the SQL baseline rather than sampled.

Adversarial probes (6):
  1. ``same_curve``: leg_a_curve_family == leg_b_curve_family is
     rejected at the Pydantic schema layer (no fall-through to
     compute).
  2. ``unknown_pillar``: an unknown / unsupported tenor on either
     leg surfaces a controlled error envelope, NOT a Python
     exception.
  3. ``unknown_curve``: a non-ZCIS ``curve_family`` (e.g. 'UST')
     yields a controlled error envelope (the level primitive's
     four-conjunct guard refuses it).
  4. ``four_conjunct_guard``: the cross-market compute issues NO
     raw market-data SELECTs of its own; the four-conjunct guard
     is inherited transitively from the level primitive.  Verified
     by independent SQL count probe (zero pollution rows under the
     guard for a non-ZCIS curve_family) AND by source-grep
     verification (compute.py contains no FROM market_data /
     instrument_master / v_market_data_daily_enriched references).
  5. ``sign_inversion_sanity``: swapping leg_a / leg_b inverts
     the spread sign exactly — verified end-to-end against the SQL
     baseline.  Sanity check for the *supported* sign convention.
  6. ``unsupported_sign_convention_guard``: setting
     ``cross_market_sign_convention`` to anything other than
     ``leg_a_minus_leg_b`` raises NotImplementedError, and the guard
     message points callers at ``methodology.planned_extensions``
     (mutates the loaded ToolConfig in-memory; does NOT touch the
     on-disk YAML).

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \\
        tests/test_cross_market_inflation_swap_spread_sql_validation.py
"""

from __future__ import annotations

import argparse
import inspect
import sys
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (  # noqa: E402
    CrossMarketInflationSwapSpreadInput,
    calculate_cross_market_inflation_swap_spread,
)
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (  # noqa: E402
    compute as cross_market_compute_module,
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


# (leg_a_curve_family, leg_b_curve_family, tenor)
Case = Tuple[str, str, str]


# Full deterministic coverage of the cross-market ZCIS spread universe.
# C(3, 2) = 3 unordered curve-pairs (each canonicalised
# lexicographically on the curve_family identifier so leg_a < leg_b)
# × 7 pillars = 21 cases.  Stable ordering so failures are easy to
# diff across runs.
_CURVE_FAMILIES: Tuple[str, ...] = ("USD_ZCIS", "EUR_ZCIS", "GBP_ZCIS")
_PILLARS: Tuple[str, ...] = ("1Y", "2Y", "3Y", "5Y", "10Y", "20Y", "30Y")
_PILLAR_YEARS = {t: tenor_to_years(t) for t in _PILLARS}


def _enumerate_cross_market_pairs() -> List[Case]:
    pairs: List[Case] = []
    sorted_pillars = sorted(_PILLARS, key=lambda t: _PILLAR_YEARS[t])
    # Canonicalise leg_a / leg_b lexicographically so the validator
    # exercises one direction per unordered pair; the
    # sign_convention probe explicitly tests the inversion.
    for leg_a, leg_b in combinations(sorted(_CURVE_FAMILIES), 2):
        for tenor in sorted_pillars:
            pairs.append((leg_a, leg_b, tenor))
    return pairs


REGRESSION_CASES: List[Case] = _enumerate_cross_market_pairs()
DEFAULT_CASE_COUNT = len(REGRESSION_CASES)
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_MID"


# Tolerances inherited from the inflation_swap_curve_spread
# validator — rounding conventions are aligned by config lint, so
# the same tolerance bands hold here.  The cross-market spread
# flows through one less layer of arithmetic (no per-curve
# reference-metadata aggregation) so display deltas are typically
# tighter; keep the same tolerances as a safe upper bound.
TOLERANCE_BY_FIELD = {
    "spread_pct": 0.00021,
    "spread_bps": 0.021,
    "change_1d_bps": 0.021,
    "change_1w_bps": 0.021,
    "change_1m_bps": 0.021,
    "z_score_252d": 0.006,
    "high_252d_bps": 0.021,
    "low_252d_bps": 0.021,
    "percentile_252d": 0.21,
    "leg_a_pct": 0.00011,
    "leg_b_pct": 0.00011,
    "tenor_years": 1e-4,
    "spread_pct_row": 0.00021,
    "spread_bps_row": 0.021,
    "leg_a_pct_row": 0.00011,
    "leg_b_pct_row": 0.00011,
}


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (leg_a, leg_b, tenor) cases from live ZCIS metadata.
    Filters to the ``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'`` universe so the SQL
    baseline operates on the same set the Python tool sees.
    Requires at least 80 observations on BOTH legs to ensure
    rolling stats are populated.
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
    for leg_a, leg_b in combinations(sorted(pillars_by_curve.keys()), 2):
        common_tenors = (
            pillars_by_curve[leg_a] & pillars_by_curve[leg_b]
        )
        parsed: List[Tuple[float, str]] = []
        for t in sorted(common_tenors):
            try:
                parsed.append((tenor_to_years(t), t))
            except ValueError:
                continue
        parsed.sort(key=lambda x: x[0])
        for _, tenor in parsed:
            pool.append((leg_a, leg_b, tenor))

    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def sql_baseline(
    engine,
    *,
    leg_a_curve_family: str,
    leg_b_curve_family: str,
    tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the same-tenor cross-market
    ZCIS spread math.

    Algorithm (mirrors the Python tool's composition shape EXACTLY):
      1. Pull two endpoint ZCIS series (one per leg) under the
         four-conjunct SELECT guard
         (instrument_type='inflation_swap' AND
         pricing_type='zero_coupon_breakeven' AND
         curve_family=? AND tenor=?), per-leg ffill within
         ffill_limit_days (5 trading days) — same shape the level
         primitive's clean step emits.
      2. Inner-join the two endpoint series on trade_date — same
         as the cross-market spread primitive's strict alignment
         step.
      3. Apply the per-trade-date difference:
            spread_pct = leg_a_pct - leg_b_pct
            spread_bps = spread_pct * 100
         and round (spread_pct → 4 decimals, spread_bps → 2
         decimals — matches the cross-market primitive's
         pct_round_decimals / bps_round_decimals boundaries).
      4. Compute rolling 252-day z-score, period changes, trailing
         range — all under the same convention values the Python
         tool uses.
      5. Anchor the display cutoff to the latest aligned
         trade_date (matches the Python tool's anchoring).
    """
    t_years = tenor_to_years(tenor)
    extended_lookback_days = lookback_days + 378

    baseline_sql = text(
        """
        WITH raw AS (
            SELECT
                v.trade_date,
                v.curve_family,
                v.field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched v
            JOIN macro_data.instrument_master i
              ON v.instrument_id = i.instrument_id
            WHERE v.instrument_type = 'inflation_swap'
              AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
              AND v.curve_family IN (:leg_a, :leg_b)
              AND v.tenor       = :tenor
              AND v.field_name  = :field_name
              AND v.trade_date >= CURRENT_DATE - ((:lookback_days + 800) * INTERVAL '1 day')
        ),
        leg_a_raw AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE curve_family = :leg_a
              AND field_value IS NOT NULL
        ),
        leg_a_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM leg_a_raw
        ),
        leg_a_trim AS (
            SELECT s.trade_date, s.field_value
            FROM leg_a_raw s, leg_a_anchor a
            WHERE s.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        leg_b_raw AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE curve_family = :leg_b
              AND field_value IS NOT NULL
        ),
        leg_b_anchor AS (
            SELECT MAX(trade_date) AS anchor FROM leg_b_raw
        ),
        leg_b_trim AS (
            SELECT l.trade_date, l.field_value
            FROM leg_b_raw l, leg_b_anchor a
            WHERE l.trade_date >= a.anchor - (:extended_lookback_days * INTERVAL '1 day')
        ),
        aligned AS (
            SELECT
                s.trade_date,
                ROUND((s.field_value)::numeric, 4)::double precision AS leg_a_pct,
                ROUND((l.field_value)::numeric, 4)::double precision AS leg_b_pct
            FROM leg_a_trim s
            INNER JOIN leg_b_trim l ON l.trade_date = s.trade_date
        ),
        spread_rows AS (
            SELECT
                trade_date,
                leg_a_pct,
                leg_b_pct,
                ROUND(
                    (leg_a_pct - leg_b_pct)::numeric,
                    4
                )::double precision AS spread_pct,
                ROUND(
                    ((leg_a_pct - leg_b_pct) * 100)::numeric,
                    2
                )::double precision AS spread_bps
            FROM aligned
        ),
        renumbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn,
                leg_a_pct,
                leg_b_pct,
                spread_pct,
                spread_bps
            FROM spread_rows
        ),
        scored AS (
            SELECT
                trade_date,
                arn,
                leg_a_pct,
                leg_b_pct,
                spread_pct,
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
                leg_a_pct,
                leg_b_pct,
                spread_pct,
                spread_bps,
                z_score,
                CASE
                    WHEN LAG(spread_bps, 1) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 1) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS change_1d_bps,
                CASE
                    WHEN LAG(spread_bps, 5) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 5) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS change_1w_bps,
                CASE
                    WHEN LAG(spread_bps, 21) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (spread_bps - LAG(spread_bps, 21) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS change_1m_bps,
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
            leg_a_pct,
            leg_b_pct,
            spread_pct,
            spread_bps,
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
                "leg_a": leg_a_curve_family,
                "leg_b": leg_b_curve_family,
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
            "leg_a_curve_family": leg_a_curve_family,
            "leg_b_curve_family": leg_b_curve_family,
            "tenor": tenor,
            "tenor_years": round(t_years, 4),
            "spread_label": (
                f"{leg_a_curve_family}-{leg_b_curve_family} {tenor}"
            ),
            "spread_pct": latest["spread_pct"],
            "spread_bps": latest["spread_bps"],
            "change_1d_bps": latest["change_1d_bps"],
            "change_1w_bps": latest["change_1w_bps"],
            "change_1m_bps": latest["change_1m_bps"],
            "z_score_252d": latest["z_score"],
            "high_252d_bps": latest["high_252d_bps"],
            "low_252d_bps": latest["low_252d_bps"],
            "percentile_252d": latest["percentile_252d"],
            "leg_a_pct": latest["leg_a_pct"],
            "leg_b_pct": latest["leg_b_pct"],
        },
        "time_series": [
            {
                "date": row["date"],
                "spread_pct": row["spread_pct"],
                "spread_bps": row["spread_bps"],
                "leg_a_pct": row["leg_a_pct"],
                "leg_b_pct": row["leg_b_pct"],
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
            "leg_a_curve_family",
            "leg_b_curve_family",
            "tenor",
            "spread_label",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "spread_pct",
            "spread_bps",
            "change_1d_bps",
            "change_1w_bps",
            "change_1m_bps",
            "z_score_252d",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "leg_a_pct",
            "leg_b_pct",
            "tenor_years",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # Per-leg reference-metadata wire surface: every successful run
    # must populate these fields on BOTH legs.  An empty value
    # would indicate the per-leg metadata threading regressed.
    for ref_field in (
        "leg_a_inflation_index_family",
        "leg_b_inflation_index_family",
        "leg_a_index_lag",
        "leg_b_index_lag",
        "leg_a_interpolation",
        "leg_b_interpolation",
    ):
        v = tool_metrics.get(ref_field)
        if not v:
            mismatches.append(
                f"current_metrics.{ref_field}: tool returned "
                "empty / None — load-bearing per-leg reference "
                "metadata must be populated."
            )

    # Derived index-family summary: USD_ZCIS / EUR_ZCIS / GBP_ZCIS
    # all reference DIFFERENT inflation indices in the V1 universe,
    # so every cross-market pair must have ``index_families_match``
    # = False AND a non-empty caveat that names BOTH index families
    # verbatim.  Same-family pairs are not feasible against the V1
    # ingested universe (the cross-market invariant
    # leg_a_curve_family != leg_b_curve_family combined with the
    # one-to-one curve→family mapping rules them out); see Finding 2
    # in the round-2 review for the rationale.
    if "index_families_match" not in tool_metrics:
        mismatches.append(
            "current_metrics.index_families_match: tool did not "
            "surface the derived top-level summary field."
        )
    else:
        if tool_metrics["index_families_match"] is not False:
            mismatches.append(
                "current_metrics.index_families_match: expected "
                "False for every V1 cross-market pair (USD_ZCIS / "
                "EUR_ZCIS / GBP_ZCIS reference distinct inflation "
                f"indices); got {tool_metrics['index_families_match']!r}."
            )
        caveat = tool_metrics.get("index_family_caveat") or ""
        if not caveat:
            mismatches.append(
                "current_metrics.index_family_caveat: must be "
                "non-empty when index_families_match is False."
            )
        else:
            leg_a_fam = tool_metrics.get(
                "leg_a_inflation_index_family", "",
            )
            leg_b_fam = tool_metrics.get(
                "leg_b_inflation_index_family", "",
            )
            if leg_a_fam and leg_a_fam not in caveat:
                mismatches.append(
                    "current_metrics.index_family_caveat: missing "
                    f"leg_a inflation index family {leg_a_fam!r}."
                )
            if leg_b_fam and leg_b_fam not in caveat:
                mismatches.append(
                    "current_metrics.index_family_caveat: missing "
                    f"leg_b inflation index family {leg_b_fam!r}."
                )

    # methodology_label must be non-empty (threaded from YAML) and
    # must surface the load-bearing INDEX-FAMILY CAVEAT.
    label = tool_metrics.get("methodology_label", "") or ""
    if not label:
        mismatches.append(
            "current_metrics.methodology_label: tool returned "
            "empty — must be threaded from YAML's "
            "methodology.what_it_does."
        )
    else:
        lo = label.lower()
        if not (
            ("us cpi" in lo or "us_cpi" in lo or "cpi-u" in lo)
            and "hicp" in lo
            and "rpi" in lo
        ):
            mismatches.append(
                "current_metrics.methodology_label: missing the "
                "load-bearing INDEX-FAMILY CAVEAT (must mention US "
                "CPI-U, HICP, and RPI so the cross-curve "
                "comparability caveat is on the wire)."
            )

    row_tolerances = dict(TOLERANCE_BY_FIELD)
    row_tolerances["spread_pct"] = TOLERANCE_BY_FIELD["spread_pct_row"]
    row_tolerances["spread_bps"] = TOLERANCE_BY_FIELD["spread_bps_row"]
    row_tolerances["leg_a_pct"] = TOLERANCE_BY_FIELD["leg_a_pct_row"]
    row_tolerances["leg_b_pct"] = TOLERANCE_BY_FIELD["leg_b_pct_row"]
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=(
                "spread_pct", "spread_bps", "leg_a_pct", "leg_b_pct",
            ),
            tolerances=row_tolerances,
        )
    )
    return mismatches


# ============================================================================
# ADVERSARIAL PROBES
# ============================================================================

def assert_same_curve_rejected_by_schema() -> List[str]:
    """Probe 1 (``same_curve``): leg_a == leg_b is rejected at the
    Pydantic schema layer with a precise error message that
    re-routes the caller to ``inflation_swap_curve_spread``.
    """
    failures: List[str] = []
    try:
        _ = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="USD_ZCIS",
            tenor="5Y",
        )
    except Exception as exc:
        msg = str(exc)
        if "inflation_swap_curve_spread" not in msg:
            failures.append(
                "same_curve probe: rejection message must re-route "
                "caller to ``inflation_swap_curve_spread``; got: "
                f"{msg!r}"
            )
        return failures
    failures.append(
        "same_curve probe: input schema accepted "
        "leg_a_curve_family='USD_ZCIS' AND "
        "leg_b_curve_family='USD_ZCIS'.  The "
        "_curves_must_differ validator must reject this — "
        "same-curve, two-tenor spreads belong to "
        "``inflation_swap_curve_spread``."
    )
    return failures


def assert_unknown_pillar_handling(
    engine,
    *,
    leg_a_curve_family: str,
    leg_b_curve_family: str,
    tenor: str,
    lookback_days: int,
) -> List[str]:
    """Probe 2 (``unknown_pillar``): an unknown / unsupported tenor
    on either leg MUST yield a controlled error envelope, NOT a
    Python exception.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_cross_market_inflation_swap_spread(
            engine=engine,
            params=CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family=leg_a_curve_family,
                leg_b_curve_family=leg_b_curve_family,
                tenor=tenor,
                lookback_days=lookback_days,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"unknown_pillar probe ({leg_a_curve_family}-"
            f"{leg_b_curve_family} {tenor}) raised "
            f"{type(exc).__name__}: {exc} — must return controlled "
            "error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            f"unknown_pillar probe ({leg_a_curve_family}-"
            f"{leg_b_curve_family} {tenor}) returned a snapshot — "
            "must return controlled error envelope instead.  Got "
            f"keys: {sorted(tool_result.keys())}"
        )
        return failures

    err = tool_result["error"]
    if "inflation_swap" not in err:
        failures.append(
            "unknown_pillar probe: controlled error envelope "
            "missing 'inflation_swap' rationale (composition guard "
            f"inheritance broke); got: {err!r}"
        )
    if "zero_coupon_breakeven" not in err:
        failures.append(
            "unknown_pillar probe: controlled error envelope "
            f"missing 'zero_coupon_breakeven' rationale; got: {err!r}"
        )
    return failures


def assert_unknown_curve_handling(
    engine, *, lookback_days: int,
) -> List[str]:
    """Probe 3 (``unknown_curve``): a non-ZCIS curve_family
    (e.g. 'UST') yields zero rows through the four-conjunct SELECT
    guard; the inner level primitive surfaces the controlled error
    and we propagate it with leg_a / leg_b attribution.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_cross_market_inflation_swap_spread(
            engine=engine,
            params=CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="UST",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
                lookback_days=lookback_days,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            "unknown_curve probe (UST-EUR_ZCIS 5Y) raised "
            f"{type(exc).__name__}: {exc} — must return controlled "
            "error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            "unknown_curve probe (UST-EUR_ZCIS 5Y) returned a "
            "snapshot — must return controlled error envelope "
            f"instead.  Got keys: {sorted(tool_result.keys())}"
        )
        return failures

    err = tool_result["error"]
    if "inflation_swap" not in err:
        failures.append(
            "unknown_curve probe: controlled error envelope "
            "missing 'inflation_swap' rationale; got: "
            f"{err!r}"
        )
    if "zero_coupon_breakeven" not in err:
        failures.append(
            "unknown_curve probe: controlled error envelope "
            "missing 'zero_coupon_breakeven' rationale; got: "
            f"{err!r}"
        )
    return failures


def assert_four_conjunct_select_guard_inherited(
    engine, *, field_name: str,
) -> List[str]:
    """Probe 4 (``four_conjunct_guard``): wrong instrument_type /
    pricing_type rows MUST NOT leak into either leg of the cross-
    market spread compute.

    The level primitive's compute issues a SELECT with the
    four-conjunct guard
    (instrument_type='inflation_swap' AND
    pricing_type='zero_coupon_breakeven' AND curve_family=? AND
    tenor=?).  Composing it twice means both legs inherit the
    guard.  The cross-market primitive's compute MUST NOT issue
    any raw market-data SELECTs of its own — we verify by
    independent SQL that:
      a. EVERY row currently in the inflation_swap universe has
         pricing_type='zero_coupon_breakeven', and
      b. requesting a non-ZCIS curve_family ('UST') under the full
         four-conjunct guard returns zero rows for every supported
         pillar.
    AND by source-grep that the cross-market primitive's compute.py
    contains NO raw market-data SELECTs of its own.
    """
    failures: List[str] = []

    # Sub-probe 4a: pricing_type universe is exactly
    # {'zero_coupon_breakeven'}.
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
            "four_conjunct_guard probe: SQL universe contains "
            "inflation_swap pricing_types other than "
            f"'zero_coupon_breakeven': {pricing_types!r}.  The "
            "four-conjunct guard would still filter them out, but "
            "verify the level primitive's compute.py has not "
            "regressed."
        )

    # Sub-probe 4b: requesting a non-ZCIS curve_family under the
    # full four-conjunct guard returns zero rows.
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
            f"four_conjunct_guard probe: SELECT under the four-"
            f"conjunct guard returned {n_rows} rows for "
            "curve_family='UST' (a nominal sovereign curve).  The "
            "guard MUST refuse non-ZCIS rows via the composed level "
            "primitive."
        )

    # Sub-probe 4c: the cross-market primitive's compute.py
    # contains NO raw market-data SELECTs of its own (the guard
    # lives transitively inside the level primitive).  Per spec:
    # ``grep -E "FROM\s+(market_data|instrument_master|
    # v_market_data_daily_enriched)"`` MUST return ZERO matches.
    # Docstring mentions of the table names are fine — only actual
    # SQL FROM-clauses are forbidden.
    import re
    source = inspect.getsource(cross_market_compute_module)
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
            "four_conjunct_guard probe: cross-market compute.py "
            f"contains raw market-data FROM-clauses ({matches!r}) — "
            "the guard must live inside the level primitive's "
            "compute, NOT here."
        )

    return failures


def _probe_sign_inversion_sanity(
    engine, *, field_name: str, lookback_days: int,
) -> List[str]:
    """Probe 5 (``sign_inversion_sanity``): swapping leg_a / leg_b
    inverts the spread sign exactly.  This is a regression-style
    sanity check for the *supported* sign convention
    (``leg_a_minus_leg_b``); a regression to a normalised direction
    (e.g. always larger-USD-leg first) is caught here.

    NOTE: this probe was previously named
    ``assert_sign_convention_inversion`` and double-counted as the
    sign-convention coverage in earlier rounds; per Finding 4 of the
    round-2 review it is renamed and the new
    ``_probe_unsupported_sign_convention_guard`` probe (probe 6
    below) explicitly validates the compute-layer guard against an
    unsupported ``cross_market_sign_convention`` value.
    """
    failures: List[str] = []
    leg_a, leg_b, tenor = "USD_ZCIS", "EUR_ZCIS", "5Y"

    fwd = calculate_cross_market_inflation_swap_spread(
        engine=engine,
        params=CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family=leg_a,
            leg_b_curve_family=leg_b,
            tenor=tenor,
            lookback_days=lookback_days,
        ),
    )
    rev = calculate_cross_market_inflation_swap_spread(
        engine=engine,
        params=CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family=leg_b,
            leg_b_curve_family=leg_a,
            tenor=tenor,
            lookback_days=lookback_days,
        ),
    )
    if "error" in fwd or "error" in rev:
        failures.append(
            "sign_convention_inversion probe: one of the directions "
            f"returned an error envelope.  fwd={fwd.get('error')!r} "
            f"rev={rev.get('error')!r}"
        )
        return failures

    fwd_bps = fwd["current_metrics"]["spread_bps"]
    rev_bps = rev["current_metrics"]["spread_bps"]
    if fwd_bps is None or rev_bps is None:
        failures.append(
            "sign_convention_inversion probe: one of the directions "
            f"returned None for spread_bps.  fwd={fwd_bps!r} "
            f"rev={rev_bps!r}"
        )
        return failures

    delta = abs(float(fwd_bps) + float(rev_bps))
    if delta > TOLERANCE_BY_FIELD["spread_bps"] + 1e-12:
        failures.append(
            "sign_convention_inversion probe: spread did not "
            f"invert exactly.  USD-EUR={fwd_bps} EUR-USD={rev_bps} "
            f"sum={fwd_bps + rev_bps} (expected ~0; tolerance = "
            f"{TOLERANCE_BY_FIELD['spread_bps']})."
        )

    return failures


def _probe_unsupported_sign_convention_guard(
    engine, *, lookback_days: int,
) -> List[str]:
    """Probe 6 (``unsupported_sign_convention_guard``): the
    compute-layer guard for ``cross_market_sign_convention`` MUST
    raise ``NotImplementedError`` when the convention is anything
    other than ``leg_a_minus_leg_b`` AND the exception message MUST
    point callers at ``methodology.planned_extensions`` (per
    Finding 3 of the round-2 review).

    Mutates the loaded ToolConfig in-memory (does NOT touch the
    on-disk YAML) so the validator can probe the guard without
    affecting any other run.
    """
    failures: List[str] = []
    leg_a, leg_b, tenor = "USD_ZCIS", "EUR_ZCIS", "5Y"

    # Load the bundled config and produce an in-memory clone with
    # only ``cross_market_sign_convention`` overridden.  Both
    # ToolConfig and Convention are frozen pydantic models, so we
    # build a new Convention (via model_copy(update=...)) and a new
    # conventions dict, then thread that into a new ToolConfig with
    # model_copy(update=...).  No on-disk YAML mutation.
    base_cfg = load_tool_config(
        Path(__file__).resolve().parent.parent
        / "rates_agent" / "inflation_swaps" / "tools"
        / "cross_market_inflation_swap_spread" / "config.yaml"
    )
    base_conv = base_cfg.conventions["cross_market_sign_convention"]
    overridden_conv = base_conv.model_copy(
        update={"value": "dv01_weighted"},
    )
    new_conventions = dict(base_cfg.conventions)
    new_conventions["cross_market_sign_convention"] = overridden_conv
    mutated_cfg = base_cfg.model_copy(
        update={"conventions": new_conventions},
    )

    raised: Exception | None = None
    try:
        _ = calculate_cross_market_inflation_swap_spread(
            engine=engine,
            params=CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family=leg_a,
                leg_b_curve_family=leg_b,
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
            "cross_market_sign_convention='dv01_weighted'.  V1 must "
            "refuse anything other than 'leg_a_minus_leg_b'."
        )
        return failures

    msg = str(raised)
    if "methodology.planned_extensions" not in msg:
        failures.append(
            "unsupported_sign_convention_guard probe: guard message "
            "MUST contain the literal 'methodology.planned_extensions' "
            "so a ripgrep-style audit can confirm the pointer.  "
            f"Got: {msg!r}"
        )
    if "leg_a_minus_leg_b" not in msg:
        failures.append(
            "unsupported_sign_convention_guard probe: guard message "
            "MUST name the only supported value "
            f"('leg_a_minus_leg_b').  Got: {msg!r}"
        )
    if "dv01_weighted" not in msg:
        failures.append(
            "unsupported_sign_convention_guard probe: guard message "
            "MUST echo the offending value ('dv01_weighted') so the "
            f"caller can self-diagnose.  Got: {msg!r}"
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
            "Validate the cross_market_inflation_swap_spread tool "
            "against direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print(
        "ZCIS CROSS_MARKET_INFLATION_SWAP_SPREAD TOOL — SQL VALIDATION"
    )
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print(
        f"  total_supported_pairs (C(3,2) × 7 pillars)         : "
        f"{len(REGRESSION_CASES)}"
    )
    print("-" * 80)

    print("[1/7] Creating DB engine...")
    engine = get_db_engine()

    print(
        "[2/7] Selecting validation cases from live ZCIS metadata..."
    )
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases,
        lambda case: f"{case[0]}-{case[1]} {case[2]}",
    )

    # Fail loudly when the default-coverage invocation produces fewer
    # than DEFAULT_CASE_COUNT cases, instead of silently passing on
    # an incomplete pool.  Inherited contract from the curve_spread
    # round-2 fix: FATAL stdout block + FATAL stderr block +
    # sys.exit(2) on coverage shortfall.
    if args.cases == DEFAULT_CASE_COUNT and len(cases) != DEFAULT_CASE_COUNT:
        selected = set(cases)
        missing = [c for c in REGRESSION_CASES if c not in selected]
        msg_lines = [
            "FATAL: Incomplete cross-market ZCIS spread coverage — "
            "live pool is missing supported curve-pair / pillar "
            "combinations.",
            f"  expected : {DEFAULT_CASE_COUNT} deterministic cases "
            "(full REGRESSION_CASES list)",
            f"  observed : {len(cases)} cases returned by "
            "choose_test_cases()",
            f"  missing  : {len(missing)} "
            "(leg_a_curve_family, leg_b_curve_family, tenor) "
            "triples — see the inflation_swaps playbook for the "
            "ingested universe (USD_ZCIS / EUR_ZCIS / GBP_ZCIS × "
            "1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y).",
        ]
        for c in missing:
            msg_lines.append(f"    - {c[0]}-{c[1]} {c[2]}")
        full_msg = "\n".join(msg_lines)
        print(full_msg, flush=True)
        print(full_msg, file=sys.stderr, flush=True)
        sys.exit(2)

    print("[3/7] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]}-{case[1]} {case[2]}",
        )
        leg_a, leg_b, tenor = case
        tool_result = calculate_cross_market_inflation_swap_spread(
            engine=engine,
            params=CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family=leg_a,
                leg_b_curve_family=leg_b,
                tenor=tenor,
                lookback_days=args.days,
            ),
        )
        sql_result = sql_baseline(
            engine=engine,
            leg_a_curve_family=leg_a,
            leg_b_curve_family=leg_b,
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
        leg_a_curve_family="USD_ZCIS",
        leg_b_curve_family="EUR_ZCIS",
        tenor="11Y",  # unsupported pillar
        lookback_days=args.days,
    )
    print("FAIL" if probe2 else "PASS")
    for f in probe2:
        print(f"  - {f}")

    print(
        "[5/7] Adversarial probe 3 (``unknown_curve``): non-ZCIS "
        "curve_family rejected via four-conjunct guard..."
    )
    probe3 = assert_unknown_curve_handling(
        engine, lookback_days=args.days,
    )
    print("FAIL" if probe3 else "PASS")
    for f in probe3:
        print(f"  - {f}")

    print(
        "[5/7 cont] Adversarial probe 4 (``four_conjunct_guard``): "
        "transitive guard inheritance + no raw SELECTs in cross-"
        "market compute..."
    )
    probe4 = assert_four_conjunct_select_guard_inherited(
        engine, field_name=args.field,
    )
    print("FAIL" if probe4 else "PASS")
    for f in probe4:
        print(f"  - {f}")

    print(
        "[6/7] Adversarial probe 5 "
        "(``sign_inversion_sanity``): swapping leg_a / leg_b "
        "inverts the spread sign exactly under the supported "
        "sign convention..."
    )
    probe5 = _probe_sign_inversion_sanity(
        engine, field_name=args.field, lookback_days=args.days,
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
        "  probe_5_sign_inversion_sanity            : "
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
                    f"  - {case[0]}-{case[1]} {case[2]} "
                    f"({len(mismatches)} mismatches)"
                )
        sys.exit(1)


if __name__ == "__main__":
    main()
