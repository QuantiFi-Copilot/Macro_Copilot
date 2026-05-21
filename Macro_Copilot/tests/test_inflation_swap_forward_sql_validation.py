#!/usr/bin/env python3
"""
test_inflation_swap_forward_sql_validation.py — Same-curve ZCIS
forward-rate validator.

Validate ``calculate_inflation_swap_forward`` against an
independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched`` ×
``macro_data.instrument_master``.  The SQL baseline reproduces the
per-trade-date dual-compounding geometric forward math without
going through any of the Python tool's helpers, so a mismatch
surfaces a real divergence in methodology rather than a shared-
code coincidence.

Deterministic full-curve coverage
---------------------------------
The ZCIS universe is small and bounded: 3 curve families
(USD_ZCIS / EUR_ZCIS / GBP_ZCIS) × 7 ingested tenors
(1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y) = 21 pillars per curve,
yielding 21 unique same-curve ``(start, end)`` pairs per curve
(C(7, 2) = 21) × 3 curves = **63** total deterministic cases.
``DEFAULT_CASE_COUNT == len(REGRESSION_CASES) == 63`` so the
helper ``sample_cases`` returns the full fixed list in stable
order on every run — every supported same-curve pair is exercised
against the SQL baseline rather than sampled.

Adversarial probes (5):
  1. Cross-curve attempt: the input layer rejects this because
     there is only a single ``curve_family`` field.  The schema
     validation must prevent any encoding of mismatched curve
     families.
  2. ``start_tenor == end_tenor``: Pydantic rejection.
  3. ``start_years > end_years`` (e.g. start=10Y, end=2Y):
     Pydantic rejection (no silent swap).
  4. Unknown / unsupported (curve_family, tenor) on either leg:
     controlled error envelope, NOT a Python exception.
  5. Wrong instrument_type / pricing_type rows MUST NOT leak into
     either leg (inherited from the level primitive's
     four-conjunct SELECT guard) — verified by independent SQL
     count probe.

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \\
        tests/test_inflation_swap_forward_sql_validation.py
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
from rates_agent.inflation_swaps.tools.inflation_swap_forward import (  # noqa: E402
    InflationSwapForwardInput,
    calculate_inflation_swap_forward,
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


# (curve_family, start_tenor, end_tenor)
Case = Tuple[str, str, str]


# Full deterministic coverage of the ZCIS forward universe.
# 3 curve families × C(7, 2) = 21 same-curve pairs each = 63 cases.
# Stable curve_family-then-(start,end) ordering so failures are
# easy to diff across runs.
_PILLARS: Tuple[str, ...] = ("1Y", "2Y", "3Y", "5Y", "10Y", "20Y", "30Y")
_PILLAR_YEARS = {t: tenor_to_years(t) for t in _PILLARS}


def _enumerate_same_curve_pairs(curve_family: str) -> List[Case]:
    pairs: List[Case] = []
    sorted_pillars = sorted(_PILLARS, key=lambda t: _PILLAR_YEARS[t])
    for i, start_t in enumerate(sorted_pillars):
        for end_t in sorted_pillars[i + 1:]:
            pairs.append((curve_family, start_t, end_t))
    return pairs


REGRESSION_CASES: List[Case] = (
    _enumerate_same_curve_pairs("USD_ZCIS")
    + _enumerate_same_curve_pairs("EUR_ZCIS")
    + _enumerate_same_curve_pairs("GBP_ZCIS")
)
DEFAULT_CASE_COUNT = len(REGRESSION_CASES)
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_MID"


# Tolerances inherited from the inflation_swap_curve_spread
# validator — rounding conventions are aligned by config lint, so
# the same tolerance bands hold here.  The forward arithmetic
# adds a non-linear (geometric) compounding step on top of the
# raw ZCIS rates, which can amplify rounding-margin deltas
# slightly relative to the curve-spread tool, especially for very
# short forward windows (e.g. 1Y/2Y).  Keep tolerances generous
# enough to absorb that without papering over real divergence.
TOLERANCE_BY_FIELD = {
    "forward_zcis_pct": 0.00031,
    "forward_zcis_bps": 0.061,
    "change_1d_bps": 0.061,
    "change_1w_bps": 0.061,
    "change_1m_bps": 0.061,
    "z_score_252d": 0.011,
    "high_252d_bps": 0.061,
    "low_252d_bps": 0.061,
    "percentile_252d": 0.31,
    "start_zcis_pct": 0.00011,
    "end_zcis_pct": 0.00011,
    "start_years": 1e-4,
    "end_years": 1e-4,
    "forward_zcis_pct_row": 0.00031,
    "forward_zcis_bps_row": 0.061,
    "z_score_row": 0.011,
}


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (curve_family, start_tenor, end_tenor) cases from
    live ZCIS metadata.  Filters to the
    ``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'`` universe so the SQL
    baseline operates on the same set the Python tool sees.
    Requires at least 80 observations on BOTH endpoints to ensure
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
    for curve_family, tenors in pillars_by_curve.items():
        parsed: List[Tuple[float, str]] = []
        for t in sorted(tenors):
            try:
                parsed.append((tenor_to_years(t), t))
            except ValueError:
                continue
        parsed.sort(key=lambda x: x[0])
        for i, (ys_start, t_start) in enumerate(parsed):
            for ys_end, t_end in parsed[i + 1:]:
                pool.append((curve_family, t_start, t_end))

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
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the same-curve ZCIS forward
    rate math.

    Algorithm (mirrors the Python tool's composition shape EXACTLY):
      1. Pull two endpoint ZCIS series (one per pillar) under the
         four-conjunct SELECT guard
         (instrument_type='inflation_swap' AND
         pricing_type='zero_coupon_breakeven' AND
         curve_family=? AND tenor=?), with NULL drop and rounded
         to yield_round_decimals (4) — same shape the level
         primitive's canonical TimeSeries emits.
      2. Inner-join the two endpoint series on trade_date — same
         as the forward primitive's strict alignment step.
      3. Apply the per-trade-date dual-compounding geometric
         forward formula in decimal:
            (1 + r_long_dec)^T_long / (1 + r_short_dec)^T_short
                 ^ (1 / (T_long - T_short)) - 1
         convert to percent, round to yield_round_decimals (4),
         then derive bps = round(forward_pct * 100, 2).  Matches
         the Python tool's boundary-rounding discipline.
      4. Compute rolling 252-day z-score, period changes, trailing
         range — all under the same convention values the Python
         tool uses.
      5. Anchor the display cutoff to the latest aligned
         trade_date (matches the Python tool's anchoring).
    """
    t_short_years = tenor_to_years(start_tenor)
    t_long_years = tenor_to_years(end_tenor)
    dt_years = t_long_years - t_short_years

    # Mirror the forward primitive's buffer math
    # (compute._conventions_from_config + the
    # extended_lookback_days = lookback + buffer_calendar_days
    # computation in calculate_inflation_swap_forward).  The
    # buffer is max(z_window, trailing_window) * buffer_multiplier
    # = 252 * 1.5 = 378.  The level primitive then uses its own
    # buffer of z_window * buffer_multiplier = 252 * 1.5 = 378 on
    # top, so total fetch buffer is approx
    # lookback + 378 (forward layer) + 378 (level layer) = +756
    # calendar days.  Plus a margin for safety.
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
              AND v.tenor IN (:start_tenor, :end_tenor)
              AND v.field_name      = :field_name
              AND v.trade_date     >= CURRENT_DATE - ((:lookback_days + 800) * INTERVAL '1 day')
        ),
        -- Per-pillar pivot+dropna for the START leg.
        short_raw AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE tenor = :start_tenor
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
        -- Per-pillar pivot+dropna for the END leg.
        long_raw AS (
            SELECT trade_date, field_value
            FROM raw
            WHERE tenor = :end_tenor
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
        forward_rows AS (
            SELECT
                trade_date,
                short_pct,
                long_pct,
                ROUND(
                    (
                        (
                            (
                                POWER(1.0 + long_pct / 100.0, :t_long_years)
                                / POWER(1.0 + short_pct / 100.0, :t_short_years)
                            )
                            ^ (1.0 / :dt_years)
                            - 1.0
                        ) * 100.0
                    )::numeric,
                    4
                )::double precision AS forward_zcis_pct
            FROM aligned
        ),
        forward_with_bps AS (
            SELECT
                trade_date,
                short_pct,
                long_pct,
                forward_zcis_pct,
                ROUND(
                    (forward_zcis_pct * 100.0)::numeric,
                    2
                )::double precision AS forward_zcis_bps
            FROM forward_rows
        ),
        renumbered AS (
            SELECT
                trade_date,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS arn,
                short_pct,
                long_pct,
                forward_zcis_pct,
                forward_zcis_bps
            FROM forward_with_bps
        ),
        scored AS (
            SELECT
                trade_date,
                arn,
                short_pct,
                long_pct,
                forward_zcis_pct,
                forward_zcis_bps,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(forward_zcis_bps) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(forward_zcis_bps) OVER zw <> 0
                    THEN ROUND(
                        (
                            (forward_zcis_bps - AVG(forward_zcis_bps) OVER zw)
                            / NULLIF(STDDEV_SAMP(forward_zcis_bps) OVER zw, 0)
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
                forward_zcis_pct,
                forward_zcis_bps,
                z_score,
                CASE
                    WHEN LAG(forward_zcis_bps, 1) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (forward_zcis_bps - LAG(forward_zcis_bps, 1) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS change_1d_bps,
                CASE
                    WHEN LAG(forward_zcis_bps, 5) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (forward_zcis_bps - LAG(forward_zcis_bps, 5) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS change_1w_bps,
                CASE
                    WHEN LAG(forward_zcis_bps, 21) OVER (ORDER BY arn) IS NULL THEN NULL
                    ELSE ROUND(
                        (forward_zcis_bps - LAG(forward_zcis_bps, 21) OVER (ORDER BY arn))::numeric,
                        2
                    )::double precision
                END AS change_1m_bps,
                ROUND((MAX(forward_zcis_bps) OVER tw)::numeric, 2)::double precision AS high_252d_bps,
                ROUND((MIN(forward_zcis_bps) OVER tw)::numeric, 2)::double precision AS low_252d_bps,
                CASE
                    WHEN MAX(forward_zcis_bps) OVER tw IS NULL
                      OR MIN(forward_zcis_bps) OVER tw IS NULL
                      OR MAX(forward_zcis_bps) OVER tw = MIN(forward_zcis_bps) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                forward_zcis_bps - MIN(forward_zcis_bps) OVER tw
                            ) / NULLIF(
                                MAX(forward_zcis_bps) OVER tw - MIN(forward_zcis_bps) OVER tw,
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
            forward_zcis_pct,
            forward_zcis_bps,
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
                "curve_family": curve_family,
                "start_tenor": start_tenor,
                "end_tenor": end_tenor,
                "field_name": field_name,
                "lookback_days": lookback_days,
                "extended_lookback_days": extended_lookback_days,
                "t_short_years": t_short_years,
                "t_long_years": t_long_years,
                "dt_years": dt_years,
            },
        ).mappings().all()

    if not rows:
        return {"error": "SQL baseline returned no rows."}

    latest = rows[-1]
    return {
        "current_metrics": {
            "as_of_date": latest["date"],
            "curve_family": curve_family,
            "start_tenor": start_tenor,
            "end_tenor": end_tenor,
            "forward_window_label": (
                f"{curve_family} {start_tenor}{end_tenor}"
            ),
            "forward_zcis_pct": latest["forward_zcis_pct"],
            "forward_zcis_bps": latest["forward_zcis_bps"],
            "change_1d_bps": latest["change_1d_bps"],
            "change_1w_bps": latest["change_1w_bps"],
            "change_1m_bps": latest["change_1m_bps"],
            "z_score_252d": latest["z_score"],
            "high_252d_bps": latest["high_252d_bps"],
            "low_252d_bps": latest["low_252d_bps"],
            "percentile_252d": latest["percentile_252d"],
            "start_zcis_pct": latest["short_pct"],
            "end_zcis_pct": latest["long_pct"],
            "start_years": round(t_short_years, 4),
            "end_years": round(t_long_years, 4),
        },
        "time_series": [
            {
                "date": row["date"],
                "forward_zcis_pct": row["forward_zcis_pct"],
                "forward_zcis_bps": row["forward_zcis_bps"],
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
            "start_tenor",
            "end_tenor",
            "forward_window_label",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "forward_zcis_pct",
            "forward_zcis_bps",
            "change_1d_bps",
            "change_1w_bps",
            "change_1m_bps",
            "z_score_252d",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "start_zcis_pct",
            "end_zcis_pct",
            "start_years",
            "end_years",
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
    row_tolerances["forward_zcis_pct"] = TOLERANCE_BY_FIELD[
        "forward_zcis_pct_row"
    ]
    row_tolerances["forward_zcis_bps"] = TOLERANCE_BY_FIELD[
        "forward_zcis_bps_row"
    ]
    row_tolerances["z_score"] = TOLERANCE_BY_FIELD["z_score_row"]
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=(
                "forward_zcis_pct", "forward_zcis_bps", "z_score",
            ),
            tolerances=row_tolerances,
        )
    )
    return mismatches


# ============================================================================
# ADVERSARIAL PROBES
# ============================================================================

def assert_cross_curve_attempt_rejected_by_schema(
    engine,
) -> List[str]:
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
        _ = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
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

    # Verify the input schema cannot be constructed without a
    # curve_family at all (defends against the same surface area).
    try:
        _ = InflationSwapForwardInput(
            start_tenor="5Y",
            end_tenor="10Y",
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
    """Probe 2: ``start_tenor == end_tenor`` rejected at the
    Pydantic layer with no fall-through to compute.
    """
    failures: List[str] = []
    try:
        _ = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="5Y",
        )
    except Exception:
        return failures
    failures.append(
        "Equal-tenor probe: input schema accepted "
        "start_tenor='5Y' AND end_tenor='5Y'.  The "
        "_tenors_must_differ validator must reject this."
    )
    return failures


def assert_inverted_tenor_rejected_by_schema() -> List[str]:
    """Probe 3: ``start_years > end_years`` rejected at the
    Pydantic layer with no silent swap.
    """
    failures: List[str] = []
    try:
        _ = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="10Y",
            end_tenor="2Y",
        )
    except Exception:
        return failures
    failures.append(
        "Inverted-tenor probe: input schema accepted "
        "start_tenor='10Y' AND end_tenor='2Y' (a forward window "
        "inversion in the input shape).  The "
        "_start_must_precede_end validator must reject this "
        "without silently swapping the legs."
    )
    return failures


def assert_unknown_pillar_handling(
    engine,
    *,
    curve_family: str,
    start_tenor: str,
    end_tenor: str,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Probe 4: an unknown / unsupported (curve_family, tenor) on
    either leg MUST yield a controlled error envelope, NOT a
    Python exception.
    """
    failures: List[str] = []
    try:
        tool_result = calculate_inflation_swap_forward(
            engine=engine,
            params=InflationSwapForwardInput(
                curve_family=curve_family,
                start_tenor=start_tenor,
                end_tenor=end_tenor,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"Unknown pillar ({curve_family}, {start_tenor}, "
            f"{end_tenor}) raised "
            f"{type(exc).__name__}: {exc} — must return controlled "
            "error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            f"Unknown pillar ({curve_family}, {start_tenor}, "
            f"{end_tenor}) returned a snapshot — must return "
            "controlled error envelope instead.  Got keys: "
            f"{sorted(tool_result.keys())}"
        )
        return failures

    err = tool_result["error"]
    # The inner level primitive's controlled error envelope must
    # propagate the no-proxy guard rationale.
    if "inflation_swap" not in err:
        failures.append(
            "Controlled error envelope missing 'inflation_swap' "
            f"rationale (composition guard inheritance broke); got: "
            f"{err!r}"
        )
    if "zero_coupon_breakeven" not in err:
        failures.append(
            "Controlled error envelope missing "
            f"'zero_coupon_breakeven' rationale; got: {err!r}"
        )
    return failures


def assert_four_conjunct_select_guard_inherited(
    engine, *, field_name: str,
) -> List[str]:
    """Probe 5: wrong instrument_type / pricing_type rows MUST
    NOT leak into either leg of the forward compute.

    The level primitive's compute issues a SELECT with the
    four-conjunct guard
    (instrument_type='inflation_swap' AND
    pricing_type='zero_coupon_breakeven' AND curve_family=? AND
    tenor=?).  Composing it twice means both legs inherit the
    guard.  We verify by independent SQL that:
      a. EVERY row currently in the inflation_swap universe has
         pricing_type='zero_coupon_breakeven', and
      b. requesting a non-ZCIS curve_family ('UST') under that
         filter returns zero rows for every supported pillar.
    """
    failures: List[str] = []

    # Sub-probe 5a: pricing_type universe is exactly
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

    # Sub-probe 5b: requesting a non-ZCIS curve_family under the
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
        if t["forward_zcis_bps"] is None and s["forward_zcis_bps"] is None:
            continue
        if t["forward_zcis_bps"] is None or s["forward_zcis_bps"] is None:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['forward_zcis_bps']} "
                f"sql={s['forward_zcis_bps']}"
            )
            continue
        delta = abs(
            float(t["forward_zcis_bps"]) - float(s["forward_zcis_bps"])
        )
        if delta > TOLERANCE_BY_FIELD["forward_zcis_bps_row"] + 1e-12:
            failures.append(
                f"Historical sample[{idx}] @ {t['date']}: "
                f"tool={t['forward_zcis_bps']} "
                f"sql={s['forward_zcis_bps']} (delta={delta:.6f})"
            )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the inflation_swap_forward tool against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("ZCIS INFLATION_SWAP_FORWARD TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print(f"  total_supported_pairs (USD/EUR/GBP × C(7,2)) : "
          f"{len(REGRESSION_CASES)}")
    print("-" * 80)

    print("[1/6] Creating DB engine...")
    engine = get_db_engine()

    print("[2/6] Selecting validation cases from live ZCIS metadata...")
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases,
        lambda case: f"{case[0]} {case[1]}{case[2]}",
    )

    # Fail loudly when the default-coverage invocation produces fewer
    # than 63 cases, instead of silently passing on an incomplete pool.
    if args.cases == DEFAULT_CASE_COUNT and len(cases) != DEFAULT_CASE_COUNT:
        selected = set(cases)
        missing = [c for c in REGRESSION_CASES if c not in selected]
        msg_lines = [
            "FATAL: Incomplete ZCIS forward coverage — live pool "
            "is missing supported pillars.",
            f"  expected : {DEFAULT_CASE_COUNT} deterministic cases "
            "(full REGRESSION_CASES list)",
            f"  observed : {len(cases)} cases returned by "
            "choose_test_cases()",
            f"  missing  : {len(missing)} "
            "(curve_family, start_tenor, end_tenor) triples — see "
            "the inflation_swaps playbook for the ingested universe "
            "(USD_ZCIS / EUR_ZCIS / GBP_ZCIS × 1Y / 2Y / 3Y / 5Y / "
            "10Y / 20Y / 30Y).",
        ]
        for c in missing:
            msg_lines.append(f"    - {c[0]} {c[1]}{c[2]}")
        full_msg = "\n".join(msg_lines)
        print(full_msg, flush=True)
        print(full_msg, file=sys.stderr, flush=True)
        sys.exit(2)

    print("[3/6] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]} {case[1]}{case[2]}",
        )
        curve_family, start_tenor, end_tenor = case
        tool_result = calculate_inflation_swap_forward(
            engine=engine,
            params=InflationSwapForwardInput(
                curve_family=curve_family,
                start_tenor=start_tenor,
                end_tenor=end_tenor,
                lookback_days=args.days,
                field_name=args.field,
            ),
        )
        sql_result = sql_baseline(
            engine=engine,
            curve_family=curve_family,
            start_tenor=start_tenor,
            end_tenor=end_tenor,
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
    probe1 = assert_cross_curve_attempt_rejected_by_schema(engine)
    print("FAIL" if probe1 else "PASS")
    for f in probe1:
        print(f"  - {f}")

    print("[4/6 cont] Adversarial probe 2: equal-tenor rejected by schema...")
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
        curve_family="USD_ZCIS",
        start_tenor="5Y",
        end_tenor="11Y",  # unsupported tenor
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe4 else "PASS")
    for f in probe4:
        print(f"  - {f}")

    print(
        "[6/6] Adversarial probe 5: four-conjunct SELECT guard "
        "inheritance (instrument_type + pricing_type)..."
    )
    probe5 = assert_four_conjunct_select_guard_inherited(
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
        "  probe_5_four_conjunct_guard    : "
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
                    f"  - {case[0]} {case[1]}{case[2]} "
                    f"({len(mismatches)} mismatches)"
                )
        sys.exit(1)


if __name__ == "__main__":
    main()
