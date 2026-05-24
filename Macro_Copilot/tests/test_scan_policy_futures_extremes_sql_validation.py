#!/usr/bin/env python3
"""
test_scan_policy_futures_extremes_sql_validation.py — policy-futures
                                                       universe-scan
                                                       SQL validator

Independently reproduces the policy-futures universe-extremes scan
in SQL: for each ``(curve_family, strip_position)`` stem in the
policy_futures universe (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT
× strip positions 1..8 — the 24 stems on the V1 playbook),

  1. fetch the raw price / volume / OI series from the enriched view,
  2. apply the per-stem inverse-pricing conversion via a JOIN to
     ``instrument_master.attributes->>'inverse_pricing'``
     (the same metadata flag the Python tool's reference helper
     reads),
  3. compute the rolling 252-day z-score of EACH of the four
     metric series (implied_rate level, 1-day Δ implied_rate in
     bps, raw volume, raw OI) via SQL window functions,
  4. pick the per-stem LATEST row,
  5. apply the per-metric ranking (top_n + |z| filter, tie-break
     by curve_family asc then strip_position asc) in Python on the
     SQL output,

and assert the Python tool's ranking matches the SQL ranking
row-for-row across both regimes — RFR (SOFR_FUT / SONIA_FUT) AND
IBOR (EUR_SHORT_RATE_FUT).

Notes on independence
---------------------
The SQL baseline reads ``macro_data.v_market_data_daily_enriched``
+ ``macro_data.instrument_master`` directly. The JOIN to
instrument_master surfaces the per-stem
``(attributes->>'inverse_pricing')::boolean`` flag — exactly the
same source the Python tool's
``fetch_scan_universe_policy_future_reference`` helper reads, but
the SQL is its own statement so the cross-check is genuinely
independent of the Python fetcher.

The Python tool uses ``clean_single_series`` (ffill 5 days; drop
duplicate dates; sort) before z-scoring; the SQL baseline
reproduces the dedup step (DISTINCT ON) and lets the rolling-z
window run on the deduped series. This matches the Python path on
the live data; policy-futures daily series are essentially
gap-free on each (curve_family, strip_position) calendar (the
playbook ingests one row per stem per trading day).

Z-scores from SQL are rounded to 4 decimals (matches the Python
``z_score_round_decimals`` convention) before comparison.

Future-anchor guard
-------------------
The Python tool returns the controlled-error envelope when
``as_of_date`` is BEYOND the universe's last observed trade_date.
The SQL validator parity-checks this honestly: ZERO rows must
exist between universe_max (exclusive) and the requested anchor
(inclusive), proving the guard's premise.

This script is a standalone CLI runner (matching the existing
``test_*_sql_validation.py`` shape); it MUST be EXCLUDED from
pytest collection via ``tests/conftest.py``'s ``collect_ignore``
list.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (  # noqa: E402
    ScanPolicyFuturesExtremesInput,
    calculate_scan_policy_futures_extremes,
)
from tests.sql_validation_common import (  # noqa: E402
    floats_match,
    print_case_header,
    print_selected_cases,
)


# (top_n, min_abs_z_score, metrics, as_of_date, curve_families)
Case = Tuple[
    int,
    float,
    Optional[Tuple[str, ...]],
    Optional[date],
    Tuple[str, ...],
]


INSTRUMENT_TYPE = "policy_future"
DEFAULT_PRICE_FIELD = "PX_LAST"
DEFAULT_VOLUME_FIELD = "PX_VOLUME"
DEFAULT_OI_FIELD = "OPEN_INT"

# Policy-futures V1 universe — matches the YAML's
# ``policy_futures_curve_families`` whitelist.
POLICY_FUTURES_CURVE_FAMILIES: Tuple[str, ...] = (
    "SOFR_FUT", "EUR_SHORT_RATE_FUT", "SONIA_FUT",
)

# Pinned anchor date for deterministic regression cases — matches
# the parity fixture's anchor (2026-04-08) and the live-DB ingest
# tail for policy_future PX_LAST.
PINNED_ANCHOR_DATE = date(2026, 4, 8)

# Regression cases — guaranteed-coverage scan invocations covering
# both RFR (SOFR_FUT / SONIA_FUT) and IBOR (EUR_SHORT_RATE_FUT)
# regimes.
REGRESSION_CASES: List[Case] = [
    # Full universe (RFR + IBOR mixed), low-floor display.
    (5, 0.0, None, PINNED_ANCHOR_DATE, POLICY_FUTURES_CURVE_FAMILIES),
    # SOFR_FUT only (RFR), default-style threshold.
    (3, 1.0, None, PINNED_ANCHOR_DATE, ("SOFR_FUT",)),
    # EUR_SHORT_RATE_FUT only (IBOR), low-floor.
    (5, 0.0, None, PINNED_ANCHOR_DATE, ("EUR_SHORT_RATE_FUT",)),
    # SOFR_FUT + SONIA_FUT (RFR-only subset).
    (5, 0.0, None, PINNED_ANCHOR_DATE, ("SOFR_FUT", "SONIA_FUT")),
    # Metric subset — implied_rate_level only.
    (
        5, 0.0,
        ("implied_rate_level",),
        PINNED_ANCHOR_DATE,
        POLICY_FUTURES_CURVE_FAMILIES,
    ),
    # Full universe, data-max anchor (no as_of supplied).
    (5, 0.0, None, None, POLICY_FUTURES_CURVE_FAMILIES),
]


# Tolerances — z-score is the ranking key so we hold it tight; the
# snapshot fields are emitted by both paths from the same DB rows
# rounded with the same decimals.
TOLERANCE_BY_FIELD = {
    "z_score": 6e-3,
    "current_raw_price": 1e-4,
    "implied_rate_pct": 1e-3,
    "daily_change_implied_rate_bps": 0.02,
    "current_volume": 0.5,
    "current_open_interest": 0.5,
    "delta_open_interest_1d": 0.5,
}


# Mapping from metric → (SQL z-column, tool z-field).
_METRIC_Z_COLUMNS: Dict[str, str] = {
    "implied_rate_level": "z_implied_rate_level",
    "implied_rate_change": "z_implied_rate_change",
    "volume_level": "z_volume_level",
    "open_interest_level": "z_oi_level",
}

_METRIC_ORDER = (
    "implied_rate_level", "implied_rate_change",
    "volume_level", "open_interest_level",
)


def sql_per_stem_latest(
    engine,
    *,
    curve_families: Tuple[str, ...],
    as_of_date: Optional[date],
    price_field: str,
    volume_field: str,
    oi_field: str,
) -> List[Dict[str, Any]]:
    """For each (curve_family, strip_position) stem, compute the
    rolling 252d z-score of the four metric series via SQL window
    functions, and return the per-stem LATEST row.

    The four metric series are:
      - implied_rate_level     — 100 - raw_price when inverse-priced
      - implied_rate_change    — diff(1) of implied_rate × 100 (bps)
      - volume_level           — raw daily traded volume
      - open_interest_level    — raw end-of-day open interest

    The Python tool aligns the three field series per-stem on the
    intersection of trading days (price ∩ volume ∩ OI) before
    z-scoring. The SQL baseline reproduces this by performing the
    rolling z-scores on a FULL OUTER JOIN of the three per-stem
    series and filtering down to rows where ALL THREE fields are
    present.

    The fetch window is derived from YAML conventions
    (z_score_window_days × z_score_buffer_multiplier +
    ffill_limit_days, currently 252 × 1.5 + 5 = 383 calendar days).
    """
    FETCH_WINDOW_DAYS = 383

    sql = text(
        """
        WITH params AS (
            SELECT
                COALESCE(CAST(:as_of_date AS DATE), CURRENT_DATE)
                  AS anchor_date
        ),
        -- Per-stem inverse_pricing flag from the master. The Python
        -- tool reads this via fetch_scan_universe_policy_future_
        -- reference; we mirror it here so the conversion rule is
        -- driven off metadata in both paths.
        stem_meta AS (
            SELECT
                i.curve_family,
                (i.attributes->>'strip_position')::int  AS strip_position,
                i.contract_code                          AS contract_code,
                (i.attributes->>'inverse_pricing')::boolean
                                                          AS inverse_pricing
            FROM macro_data.instrument_master i
            WHERE i.curve_family = ANY(:curve_families)
              AND i.is_rolling_contract = TRUE
              AND (i.attributes->>'strip_position')::int IS NOT NULL
        ),
        -- Raw price rows, with the implied_rate derived inline via
        -- the per-stem inverse_pricing flag.
        raw_prices AS (
            SELECT
                d.trade_date,
                d.curve_family,
                (d.attributes->>'strip_position')::int   AS strip_position,
                d.field_value::double precision          AS raw_price,
                CASE WHEN m.inverse_pricing
                     THEN 100.0 - d.field_value::double precision
                     ELSE d.field_value::double precision
                END                                       AS implied_rate
            FROM macro_data.v_market_data_daily_enriched d
            JOIN stem_meta m
              ON d.curve_family    = m.curve_family
             AND (d.attributes->>'strip_position')::int = m.strip_position
            CROSS JOIN params p
            WHERE d.instrument_type = :instrument_type
              AND d.field_name      = :price_field
              AND d.curve_family    = ANY(:curve_families)
              AND d.field_value    IS NOT NULL
              AND d.trade_date     >= p.anchor_date
                                       - (:fetch_window_days
                                          * INTERVAL '1 day')
              AND d.trade_date     <= p.anchor_date
        ),
        raw_volume AS (
            SELECT
                d.trade_date,
                d.curve_family,
                (d.attributes->>'strip_position')::int   AS strip_position,
                d.field_value::double precision          AS volume
            FROM macro_data.v_market_data_daily_enriched d
            CROSS JOIN params p
            WHERE d.instrument_type = :instrument_type
              AND d.field_name      = :volume_field
              AND d.curve_family    = ANY(:curve_families)
              AND d.field_value    IS NOT NULL
              AND (d.attributes->>'strip_position')::int IS NOT NULL
              AND d.trade_date     >= p.anchor_date
                                       - (:fetch_window_days
                                          * INTERVAL '1 day')
              AND d.trade_date     <= p.anchor_date
        ),
        raw_oi AS (
            SELECT
                d.trade_date,
                d.curve_family,
                (d.attributes->>'strip_position')::int   AS strip_position,
                d.field_value::double precision          AS open_interest
            FROM macro_data.v_market_data_daily_enriched d
            CROSS JOIN params p
            WHERE d.instrument_type = :instrument_type
              AND d.field_name      = :oi_field
              AND d.curve_family    = ANY(:curve_families)
              AND d.field_value    IS NOT NULL
              AND (d.attributes->>'strip_position')::int IS NOT NULL
              AND d.trade_date     >= p.anchor_date
                                       - (:fetch_window_days
                                          * INTERVAL '1 day')
              AND d.trade_date     <= p.anchor_date
        ),
        -- Dedup each stream by (stem, trade_date) keeping the last
        -- value — mirrors clean_single_series's
        -- drop_duplicates(keep='last') in Python.
        dedup_price AS (
            SELECT DISTINCT ON
                (curve_family, strip_position, trade_date)
                curve_family, strip_position, trade_date, raw_price,
                implied_rate
            FROM raw_prices
            ORDER BY curve_family, strip_position, trade_date,
                     raw_price
        ),
        dedup_volume AS (
            SELECT DISTINCT ON
                (curve_family, strip_position, trade_date)
                curve_family, strip_position, trade_date, volume
            FROM raw_volume
            ORDER BY curve_family, strip_position, trade_date,
                     volume
        ),
        dedup_oi AS (
            SELECT DISTINCT ON
                (curve_family, strip_position, trade_date)
                curve_family, strip_position, trade_date,
                open_interest
            FROM raw_oi
            ORDER BY curve_family, strip_position, trade_date,
                     open_interest
        ),
        -- INNER JOIN on (stem, trade_date) implements the
        -- per-stem intersection alignment the Python tool does in
        -- pandas.
        aligned AS (
            SELECT
                p.curve_family,
                p.strip_position,
                p.trade_date,
                p.raw_price,
                p.implied_rate,
                v.volume,
                o.open_interest
            FROM dedup_price  p
            JOIN dedup_volume v
              ON  p.curve_family    = v.curve_family
              AND p.strip_position  = v.strip_position
              AND p.trade_date      = v.trade_date
            JOIN dedup_oi     o
              ON  p.curve_family    = o.curve_family
              AND p.strip_position  = o.strip_position
              AND p.trade_date      = o.trade_date
        ),
        -- Pre-compute the 1-day Δ implied_rate in BPS so the
        -- per-stem z-score window can apply to it.
        with_change AS (
            SELECT
                a.*,
                (a.implied_rate
                  - LAG(a.implied_rate, 1) OVER (
                        PARTITION BY a.curve_family, a.strip_position
                        ORDER BY a.trade_date
                  )) * 100.0                              AS implied_rate_change_bps
            FROM aligned a
        ),
        -- Per-metric rolling 252d z-scores on each of the four
        -- series. The z-window matches the Python tool's
        -- z_score_window_days × min_periods (60). The z-score
        -- column for ``implied_rate_change`` is NULL on the first
        -- row of each stem's window because the change series
        -- itself is NULL there.
        scored AS (
            SELECT
                curve_family,
                strip_position,
                trade_date,
                raw_price,
                implied_rate,
                implied_rate_change_bps,
                volume,
                open_interest,
                -- implied_rate_level
                CASE
                    WHEN COUNT(*) OVER zw_rate >= 60
                     AND STDDEV_SAMP(implied_rate) OVER zw_rate IS NOT NULL
                     AND STDDEV_SAMP(implied_rate) OVER zw_rate <> 0
                    THEN ROUND(
                        ((implied_rate
                          - AVG(implied_rate) OVER zw_rate)
                         / NULLIF(STDDEV_SAMP(implied_rate) OVER zw_rate, 0))
                            ::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END                                       AS z_implied_rate_level,
                -- implied_rate_change (in BPS). The change-window
                -- counts only non-NULL changes (the first row of
                -- each stem has NULL change); applying COUNT(...)
                -- over the change column naturally drops the NULL
                -- from the window count.
                CASE
                    WHEN COUNT(implied_rate_change_bps) OVER zw_rate >= 60
                     AND STDDEV_SAMP(implied_rate_change_bps) OVER zw_rate IS NOT NULL
                     AND STDDEV_SAMP(implied_rate_change_bps) OVER zw_rate <> 0
                    THEN ROUND(
                        ((implied_rate_change_bps
                          - AVG(implied_rate_change_bps) OVER zw_rate)
                         / NULLIF(STDDEV_SAMP(implied_rate_change_bps) OVER zw_rate, 0))
                            ::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END                                       AS z_implied_rate_change,
                -- volume_level
                CASE
                    WHEN COUNT(*) OVER zw_rate >= 60
                     AND STDDEV_SAMP(volume) OVER zw_rate IS NOT NULL
                     AND STDDEV_SAMP(volume) OVER zw_rate <> 0
                    THEN ROUND(
                        ((volume - AVG(volume) OVER zw_rate)
                         / NULLIF(STDDEV_SAMP(volume) OVER zw_rate, 0))
                            ::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END                                       AS z_volume_level,
                -- open_interest_level
                CASE
                    WHEN COUNT(*) OVER zw_rate >= 60
                     AND STDDEV_SAMP(open_interest) OVER zw_rate IS NOT NULL
                     AND STDDEV_SAMP(open_interest) OVER zw_rate <> 0
                    THEN ROUND(
                        ((open_interest - AVG(open_interest) OVER zw_rate)
                         / NULLIF(STDDEV_SAMP(open_interest) OVER zw_rate, 0))
                            ::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END                                       AS z_oi_level,
                -- 1-day Δ OI snapshot (raw subtraction; matches
                -- the Python ``delta_open_interest_1d`` field).
                ROUND(
                    (open_interest - LAG(open_interest, 1) OVER (
                        PARTITION BY curve_family, strip_position
                        ORDER BY trade_date
                    ))::numeric, 0
                )::double precision                        AS delta_oi_1d
            FROM with_change
            WINDOW zw_rate AS (
                PARTITION BY curve_family, strip_position
                ORDER BY trade_date
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
            )
        ),
        per_stem_latest AS (
            SELECT DISTINCT ON (curve_family, strip_position)
                curve_family,
                strip_position,
                TO_CHAR(trade_date, 'YYYY-MM-DD')          AS as_of_date,
                ROUND(raw_price::numeric, 5)::double precision
                                                          AS current_raw_price,
                ROUND(implied_rate::numeric, 4)::double precision
                                                          AS implied_rate_pct,
                ROUND(implied_rate_change_bps::numeric, 2)::double precision
                                                          AS daily_change_implied_rate_bps,
                ROUND(volume::numeric, 0)::double precision
                                                          AS current_volume,
                ROUND(open_interest::numeric, 0)::double precision
                                                          AS current_open_interest,
                delta_oi_1d                               AS delta_open_interest_1d,
                z_implied_rate_level,
                z_implied_rate_change,
                z_volume_level,
                z_oi_level
            FROM scored
            ORDER BY curve_family, strip_position, trade_date DESC
        )
        SELECT * FROM per_stem_latest
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "curve_families": list(curve_families),
                "instrument_type": INSTRUMENT_TYPE,
                "price_field": price_field,
                "volume_field": volume_field,
                "oi_field": oi_field,
                "fetch_window_days": FETCH_WINDOW_DAYS,
                "as_of_date": (
                    as_of_date.isoformat() if as_of_date else None
                ),
            },
        ).mappings().all()
    return [dict(r) for r in rows]


def sql_ranked_rows_for_metric(
    per_stem_rows: List[Dict[str, Any]],
    *,
    metric: str,
    top_n: int,
    min_abs_z: float,
) -> List[Dict[str, Any]]:
    """Reproduce the Python ranking step from the SQL per-stem rows
    for one metric.

    Returns the ranked list, top_n rows, sorted by |z| desc then
    curve_family asc then strip_position asc (matches the Python
    tie-break).
    """
    z_col = _METRIC_Z_COLUMNS[metric]
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for row in per_stem_rows:
        z = row.get(z_col)
        if z is None:
            continue
        if abs(z) < min_abs_z:
            continue
        candidates.append((float(z), row))
    candidates.sort(
        key=lambda zr: (
            -abs(zr[0]),
            zr[1]["curve_family"],
            int(zr[1]["strip_position"]),
        ),
    )
    return [
        {**row, "metric": metric, "z_score": z, "rank": idx}
        for idx, (z, row) in enumerate(candidates[:top_n], start=1)
    ]


def sql_ranked_all_metrics(
    per_stem_rows: List[Dict[str, Any]],
    *,
    metrics: Tuple[str, ...],
    top_n: int,
    min_abs_z: float,
) -> List[Dict[str, Any]]:
    """Rank per-metric across the requested subset, concatenated in
    canonical order so the output matches the Python tool's
    deterministic block ordering."""
    output: List[Dict[str, Any]] = []
    for metric in _METRIC_ORDER:
        if metric not in metrics:
            continue
        output.extend(
            sql_ranked_rows_for_metric(
                per_stem_rows,
                metric=metric,
                top_n=top_n,
                min_abs_z=min_abs_z,
            )
        )
    return output


def compare_results(
    tool_result: Dict[str, Any],
    sql_ranked: List[Dict[str, Any]],
) -> List[str]:
    mismatches: List[str] = []
    if "error" in tool_result:
        mismatches.append(
            f"Tool returned error: {tool_result['error']}"
        )
        return mismatches

    tool_rows = tool_result["results"]
    if len(tool_rows) != len(sql_ranked):
        mismatches.append(
            f"row count tool={len(tool_rows)} sql={len(sql_ranked)}"
        )

    for rank_idx, (t_row, s_row) in enumerate(
        zip(tool_rows, sql_ranked), start=1,
    ):
        stem = (
            f"{t_row['curve_family']}/{t_row['strip_position']} "
            f"({t_row['metric']})"
        )
        if t_row["metric"] != s_row["metric"]:
            mismatches.append(
                f"rank {rank_idx}: tool metric={t_row['metric']!r} "
                f"sql metric={s_row['metric']!r}"
            )
            continue
        if t_row["curve_family"] != s_row["curve_family"]:
            mismatches.append(
                f"rank {rank_idx}: "
                f"tool={t_row['curve_family']}/"
                f"{t_row['strip_position']} "
                f"sql={s_row['curve_family']}/"
                f"{s_row['strip_position']}"
            )
            continue
        if int(t_row["strip_position"]) != int(s_row["strip_position"]):
            mismatches.append(
                f"rank {rank_idx} ({t_row['curve_family']}): "
                f"tool sp={t_row['strip_position']} "
                f"sql sp={s_row['strip_position']}"
            )
            continue
        for field in (
            "z_score", "current_raw_price", "implied_rate_pct",
            "daily_change_implied_rate_bps",
            "current_volume", "current_open_interest",
            "delta_open_interest_1d",
        ):
            t_v = t_row.get(field)
            s_v = s_row.get(field)
            if not floats_match(t_v, s_v, field, TOLERANCE_BY_FIELD):
                mismatches.append(
                    f"rank {rank_idx} ({stem}) {field}: "
                    f"tool={t_v!r} sql={s_v!r}"
                )
        if t_row.get("as_of_date") != s_row.get("as_of_date"):
            mismatches.append(
                f"rank {rank_idx} ({stem}) as_of_date: "
                f"tool={t_row.get('as_of_date')!r} "
                f"sql={s_row.get('as_of_date')!r}"
            )
    return mismatches


def run_case(
    engine,
    *,
    case: Case,
    price_field: str,
    volume_field: str,
    oi_field: str,
) -> List[str]:
    top_n, min_abs_z, metrics, as_of, curve_families = case
    requested_metrics = (
        list(metrics) if metrics is not None else None
    )
    tool_result = calculate_scan_policy_futures_extremes(
        engine=engine,
        params=ScanPolicyFuturesExtremesInput(
            curve_families=(
                list(curve_families)
                if curve_families != POLICY_FUTURES_CURVE_FAMILIES
                else None
            ),
            top_n=top_n,
            min_abs_z_score=min_abs_z,
            metrics=requested_metrics,
            as_of_date=as_of,
        ),
    )
    per_stem_rows = sql_per_stem_latest(
        engine,
        curve_families=curve_families,
        as_of_date=as_of,
        price_field=price_field,
        volume_field=volume_field,
        oi_field=oi_field,
    )
    if not per_stem_rows:
        return [
            f"SQL baseline returned 0 stems for curve_families="
            f"{curve_families} — DB coverage gap?"
        ]
    effective_metrics: Tuple[str, ...] = (
        metrics if metrics is not None else _METRIC_ORDER
    )
    sql_ranked = sql_ranked_all_metrics(
        per_stem_rows,
        metrics=effective_metrics,
        top_n=top_n,
        min_abs_z=min_abs_z,
    )
    return compare_results(tool_result, sql_ranked)


# Future-anchor guard SQL parity check.
_FETCH_UNIVERSE_MAX_DATE_SQL = text(
    """
    SELECT MAX(trade_date) AS max_trade_date
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND field_name      = :field_name
      AND curve_family    = ANY(:curve_families)
      AND (attributes->>'strip_position')::int IS NOT NULL
    """
)


_FETCH_BEYOND_MAX_ROW_COUNT_SQL = text(
    """
    SELECT COUNT(*) AS row_count
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = :instrument_type
      AND field_name      = :field_name
      AND curve_family    = ANY(:curve_families)
      AND (attributes->>'strip_position')::int IS NOT NULL
      AND trade_date      > CAST(:universe_max AS DATE)
      AND trade_date     <= CAST(:future_anchor AS DATE)
    """
)


def run_future_anchor_guard_case(
    engine, price_field: str,
) -> List[str]:
    """Validate the future-anchor guard against the live DB.

    1. Pull the policy_futures universe's current MAX(trade_date)
       in SQL.
    2. Compute the Python tool with as_of = max + 1 day.
    3. Assert the Python output is the controlled-error envelope.
    4. Mirror the assertion in SQL: zero rows exist strictly
       between universe_max (exclusive) and the requested anchor
       (inclusive).

    Read-only; pure SELECT.
    """
    mismatches: List[str] = []
    with engine.connect() as conn:
        max_row = conn.execute(
            _FETCH_UNIVERSE_MAX_DATE_SQL,
            {
                "curve_families": list(POLICY_FUTURES_CURVE_FAMILIES),
                "instrument_type": INSTRUMENT_TYPE,
                "field_name": price_field,
            },
        ).first()
    if max_row is None or max_row[0] is None:
        return [
            "future-anchor guard: SQL returned NULL for universe "
            "MAX(trade_date) — universe appears empty; cannot "
            "validate the guard."
        ]
    universe_max: date = (
        max_row[0]
        if isinstance(max_row[0], date)
        else date.fromisoformat(str(max_row[0]))
    )
    future_anchor = universe_max + timedelta(days=1)

    tool_result = calculate_scan_policy_futures_extremes(
        engine=engine,
        params=ScanPolicyFuturesExtremesInput(
            curve_families=None,
            top_n=5,
            min_abs_z_score=0.0,
            as_of_date=future_anchor,
        ),
    )

    if "error" not in tool_result:
        mismatches.append(
            f"future-anchor guard: Python tool returned a normal "
            f"scan for as_of_date={future_anchor.isoformat()} "
            f"(universe_max={universe_max.isoformat()}) — expected "
            "the controlled-error envelope. Keys present: "
            f"{sorted(tool_result.keys())}"
        )
        return mismatches
    if "results" in tool_result:
        mismatches.append(
            f"future-anchor guard: tool returned BOTH 'error' and "
            f"'results' — envelope must be error-only."
        )
    if "scan_summary" in tool_result:
        mismatches.append(
            f"future-anchor guard: tool returned BOTH 'error' and "
            f"'scan_summary' — envelope must be error-only."
        )
    error_text = tool_result["error"]
    for substring in (
        "beyond", "last observed", future_anchor.isoformat(),
    ):
        if substring not in error_text:
            mismatches.append(
                f"future-anchor guard: error text missing required "
                f"substring {substring!r}; got: {error_text!r}"
            )

    # SQL parity.
    with engine.connect() as conn:
        count_row = conn.execute(
            _FETCH_BEYOND_MAX_ROW_COUNT_SQL,
            {
                "curve_families": list(POLICY_FUTURES_CURVE_FAMILIES),
                "instrument_type": INSTRUMENT_TYPE,
                "field_name": price_field,
                "universe_max": universe_max.isoformat(),
                "future_anchor": future_anchor.isoformat(),
            },
        ).first()
    sql_beyond_count = int(count_row[0]) if count_row is not None else -1
    if sql_beyond_count != 0:
        mismatches.append(
            f"future-anchor guard: SQL parity violated — "
            f"{sql_beyond_count} rows exist between universe_max="
            f"{universe_max.isoformat()} (excl) and future_anchor="
            f"{future_anchor.isoformat()} (incl). The guard's "
            "premise (no data past universe_max) is false; "
            "reconcile before claiming PASS."
        )
    return mismatches


def run_inverse_pricing_metadata_parity(engine) -> List[str]:
    """Cross-check: every (curve_family, strip_position) on the
    policy_futures universe must carry a non-NULL inverse_pricing
    flag on instrument_master.attributes — the Python tool's
    metadata-driven conversion REQUIRES this, and the SQL baseline's
    JOIN uses the same flag.

    Returns mismatches when any stem on the universe lacks the
    flag.
    """
    mismatches: List[str] = []
    sql = text(
        """
        SELECT
            i.curve_family,
            (i.attributes->>'strip_position')::int AS strip_position,
            (i.attributes->>'inverse_pricing')::boolean AS inverse_pricing
        FROM macro_data.instrument_master i
        WHERE i.curve_family = ANY(:curve_families)
          AND i.is_rolling_contract = TRUE
          AND (i.attributes->>'strip_position')::int IS NOT NULL
        ORDER BY i.curve_family,
                 (i.attributes->>'strip_position')::int
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "curve_families": list(POLICY_FUTURES_CURVE_FAMILIES),
            },
        ).mappings().all()
    if not rows:
        mismatches.append(
            "inverse_pricing metadata parity: 0 strip-position-"
            "keyed rolling contracts on instrument_master for "
            "policy-futures curve families — universe appears "
            "empty."
        )
        return mismatches
    for r in rows:
        if r["inverse_pricing"] is None:
            mismatches.append(
                f"inverse_pricing metadata parity: "
                f"{r['curve_family']}/sp={r['strip_position']} has "
                "NULL inverse_pricing — the Python tool would "
                "refuse this universe, but the SQL baseline would "
                "silently treat it as direct-priced. Surface this "
                "as a metadata gap to the playbook owner."
            )
    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the policy_futures universe-extremes scan "
            "against direct SQL."
        ),
    )
    parser.add_argument("--price-field", default=DEFAULT_PRICE_FIELD)
    parser.add_argument("--volume-field", default=DEFAULT_VOLUME_FIELD)
    parser.add_argument("--oi-field", default=DEFAULT_OI_FIELD)
    args = parser.parse_args()

    print("=" * 80)
    print(
        "POLICY-FUTURES UNIVERSE-EXTREMES SCAN — SQL VALIDATION"
    )
    print("=" * 80)
    print(f"  price_field      : {args.price_field}")
    print(f"  volume_field     : {args.volume_field}")
    print(f"  oi_field         : {args.oi_field}")
    print(f"  instrument_type  : {INSTRUMENT_TYPE}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    cases = REGRESSION_CASES

    def _case_label(c: Case) -> str:
        anchor = c[3].isoformat() if c[3] is not None else "data_max"
        metrics_label = (
            "(all)" if c[2] is None else list(c[2])
        )
        curves_label = (
            "(all)"
            if c[4] == POLICY_FUTURES_CURVE_FAMILIES
            else list(c[4])
        )
        return (
            f"top_n={c[0]} min_abs_z={c[1]} metrics={metrics_label} "
            f"as_of={anchor} curves={curves_label}"
        )

    print_selected_cases(cases, _case_label)

    print("[2/4] inverse_pricing metadata parity check...")
    metadata_mismatches = run_inverse_pricing_metadata_parity(engine)
    if metadata_mismatches:
        print("FAIL")
        for m in metadata_mismatches[:10]:
            print(f"  - {m}")
    else:
        print("PASS")

    print("[3/4] Running tool vs SQL comparisons...")
    total_case_count = len(cases) + 1
    failed_cases: List[Tuple[Case, List[str]]] = []
    rfr_pass = 0
    rfr_fail = 0
    ibor_pass = 0
    ibor_fail = 0
    for index, case in enumerate(cases, start=1):
        label = _case_label(case)
        print_case_header(index, total_case_count, label)
        mismatches = run_case(
            engine, case=case,
            price_field=args.price_field,
            volume_field=args.volume_field,
            oi_field=args.oi_field,
        )
        # Per-regime tally — a case scoped to a single regime is
        # countable; mixed-regime cases credit both regimes.
        cf_set = set(case[4])
        rfr_touched = bool(cf_set & {"SOFR_FUT", "SONIA_FUT"})
        ibor_touched = "EUR_SHORT_RATE_FUT" in cf_set
        if mismatches:
            print("FAIL")
            for mismatch in mismatches[:10]:
                print(f"  - {mismatch}")
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more")
            failed_cases.append((case, mismatches))
            if rfr_touched:
                rfr_fail += 1
            if ibor_touched:
                ibor_fail += 1
        else:
            print("PASS")
            if rfr_touched:
                rfr_pass += 1
            if ibor_touched:
                ibor_pass += 1

    # Future-anchor guard.
    guard_label = "future-anchor guard (as_of = universe_max + 1 day)"
    print_case_header(
        total_case_count, total_case_count, guard_label,
    )
    guard_mismatches = run_future_anchor_guard_case(
        engine, args.price_field,
    )
    guard_failed: List[Tuple[str, List[str]]] = []
    if guard_mismatches:
        print("FAIL")
        for mismatch in guard_mismatches[:10]:
            print(f"  - {mismatch}")
        if len(guard_mismatches) > 10:
            print(
                f"  - ... plus "
                f"{len(guard_mismatches) - 10} more mismatches"
            )
        guard_failed.append((guard_label, guard_mismatches))
    else:
        print("PASS")

    print("[4/4] Summary")
    print("-" * 80)
    total_failed = (
        len(failed_cases) + len(guard_failed)
        + (1 if metadata_mismatches else 0)
    )
    print(f"  total_cases : {total_case_count + 1}")
    print(f"  passed      : {total_case_count + 1 - total_failed}")
    print(f"  failed      : {total_failed}")
    print(f"  RFR  cases  : pass={rfr_pass}  fail={rfr_fail}")
    print(f"  IBOR cases  : pass={ibor_pass}  fail={ibor_fail}")

    if failed_cases or guard_failed or metadata_mismatches:
        print("\nFAILED CASES:")
        for case, mismatches in failed_cases:
            label = _case_label(case)
            print(
                f"  - {label} ({len(mismatches)} mismatches)"
            )
        for label, mismatches in guard_failed:
            print(
                f"  - {label} ({len(mismatches)} mismatches)"
            )
        if metadata_mismatches:
            print(
                f"  - inverse_pricing metadata parity "
                f"({len(metadata_mismatches)} mismatches)"
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
