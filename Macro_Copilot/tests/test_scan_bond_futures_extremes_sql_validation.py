#!/usr/bin/env python3
"""
test_scan_bond_futures_extremes_sql_validation.py — bond-futures
                                                     universe-scan
                                                     validator

Independently reproduces the bond-futures universe-extremes scan in
SQL: for each rolling-generic stem in the bond_futures universe,
compute the 252-day rolling z-score on price LEVEL / price 1-day
CHANGE / volume LEVEL / open-interest LEVEL (on the per-stem
intersection of trading days for the three fields), pick the top-N
per metric by |z|, and assert the Python tool's ranking matches the
SQL ranking row-for-row.

Notes on independence
---------------------
The SQL baseline filters via ``instrument_master.contract_code``
joined to ``market_data_daily`` directly — NOT via
``v_market_data_daily_enriched`` (whose ``contract_code`` column
COALESCEs the per-day-effective SCD2 history value, e.g. TYH6, not
the stem TY1). This mirrors
``fetch_rolling_generic_universe_series``'s fetcher shape but is its
own SQL statement so the cross-check is genuinely independent of the
Python fetcher.

The three series (PX_LAST + PX_VOLUME + OPEN_INT) are joined ON
``trade_date`` in SQL per-stem — the equivalent of the Python tool's
``index.intersection`` alignment step. The SQL z-scores run on the
INNER-joined date series so both paths see the same aligned input.

The price_change metric's z-score is the rolling z of the 1-day diff
series — SQL computes the diff via ``LAG(price, 1) OVER (PARTITION
BY stem ORDER BY trade_date)`` on the aligned series, then z-scores
the diff. NaN-pad on the first row per stem mirrors the Python
``diff(1).dropna()``.

This script is a standalone CLI runner (matching the existing
``test_*_sql_validation.py`` shape); it must be EXCLUDED from pytest
collection via ``tests/conftest.py``'s ``collect_ignore`` list.
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
from rates_agent.bond_futures.tools.schemas import (  # noqa: E402
    ScanBondFuturesExtremesInput,
)
from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (  # noqa: E402
    calculate_scan_bond_futures_extremes,
)
from tests.sql_validation_common import (  # noqa: E402
    floats_match,
    print_case_header,
    print_selected_cases,
)


# (top_n, min_abs_z_score, as_of_date, curve_families)
#
# Round-2 mandatory-fix #2: ``lookback_days`` has been removed from
# the input schema; the SQL baseline derives its fetch window from
# YAML conventions (z_score_window_days × z_score_buffer_multiplier
# + ffill_limit_days, currently 252 × 1.5 + 5 = 383 calendar days)
# anchored on the resolved as-of date.
#
# ``as_of_date=None`` reproduces the production default (anchor to
# the data's most-recent shared trading day; matches what
# ``calculate_scan_bond_futures_extremes`` does internally when the
# input is omitted). An explicit ``as_of_date=date(...)`` exercises
# the new deterministic-anchor code path.
Case = Tuple[int, float, Optional[date], Tuple[str, ...]]


DEFAULT_PRICE_FIELD = "PX_LAST"
DEFAULT_VOLUME_FIELD = "PX_VOLUME"
DEFAULT_OI_FIELD = "OPEN_INT"

# Bond_futures V1 universe per ADR 0011 — sovereign-bond futures only.
BOND_FUTURES_CURVE_FAMILIES: Tuple[str, ...] = (
    "UST_FUT", "DE_FUT", "UK_FUT", "JP_FUT", "FR_FUT",
    "IT_FUT", "ES_FUT", "CA_FUT", "AU_FUT",
)

# Pinned anchor date for the deterministic regression case.
# 2026-04-08 is the bond_futures ingest as_of date the round-2 brief
# names — it lives within the loaded macro-tsdb range so the SQL
# baseline and the Python tool both see the same per-stem time
# series and produce identical rankings.
PINNED_ANCHOR_DATE = date(2026, 4, 8)

# Regression cases — guaranteed-coverage scan invocations. The mix:
#   - full universe, as_of=None (data-max anchor) — the headline
#     morning-sweep desk read.
#   - UST + DE subset, as_of=None — narrowed scope, default anchor.
#   - UST-only, low threshold, as_of=None — exercises high top_n.
#   - full universe, as_of=PINNED_ANCHOR_DATE — DETERMINISTIC anchor
#     case. Two runs with this case must produce identical rankings
#     regardless of wall-clock day (the round-2 determinism win).
REGRESSION_CASES: List[Case] = [
    (5, 1.5, None, BOND_FUTURES_CURVE_FAMILIES),
    (3, 0.0, None, ("UST_FUT", "DE_FUT")),
    (10, 0.5, None, ("UST_FUT",)),
    (5, 0.0, PINNED_ANCHOR_DATE, BOND_FUTURES_CURVE_FAMILIES),
]


# Tolerances — z-score is the ranking key so we hold it tight; the
# snapshot fields are emitted by both paths from the same DB rows
# rounded with the same decimals, so 0.5 (half-tick on a 0-decimals
# count) is the right floor for volume / OI counts.
TOLERANCE_BY_FIELD = {
    "z_score": 6e-3,
    "current_price": 1e-3,  # 6-decimal rounding; tolerate harmless float diff
    "current_volume": 0.5,
    "current_open_interest": 0.5,
    "daily_price_change": 1e-3,
    "delta_open_interest_1d": 0.5,
}


def sql_per_stem_metric_scores(
    engine,
    *,
    curve_families: Tuple[str, ...],
    as_of_date: Optional[date],
    price_field: str,
    volume_field: str,
    oi_field: str,
) -> List[Dict[str, Any]]:
    """For each rolling-generic stem in the named curve families,
    compute the four metric z-scores (price level, price change,
    volume level, OI level) using SQL window functions on the per-
    stem INNER-joined three-field series.

    The fetch window is derived from YAML conventions (round-2
    mandatory-fix #2: removed the ``lookback_days`` LLM input):
      anchor          = as_of_date if supplied else CURRENT_DATE
      fetch_start     = anchor - (z_window * buffer_mult + ffill_limit
                                  calendar days)
      per-stem cap    = anchor (when supplied; LLM-controlled
                                deterministic ranking)

    Returns one row per stem (the Python ranking step is reproduced
    here in Python; the SQL baseline provides the per-stem per-metric
    z-score that Python should match).
    """
    # YAML-mirrored fetch window (kept in sync with config.yaml's
    # ``z_score_window_days`` / ``z_score_buffer_multiplier`` /
    # ``ffill_limit_days`` — 252 * 1.5 + 5 = 383 calendar days).
    FETCH_WINDOW_DAYS = 383

    sql = text(
        """
        -- ``CAST(:as_of_date AS DATE)`` rather than the shorthand
        -- ``::date`` cast so SQLAlchemy's bind-param parser does
        -- not mistake the double-colon for a PostgreSQL cast op
        -- inside its own colon-prefixed parameter syntax. Inline
        -- bind-style references are deliberately omitted from this
        -- comment so the parser does not interpret them as bindings.
        WITH params AS (
            SELECT
                COALESCE(CAST(:as_of_date AS DATE), CURRENT_DATE) AS anchor_date
        ),
        raw_price AS (
            SELECT
                i.curve_family, i.contract_code, i.tenor,
                d.trade_date, d.field_value::double precision AS price
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master i
              ON d.instrument_id = i.instrument_id
            CROSS JOIN params p
            WHERE i.is_rolling_contract = TRUE
              AND i.curve_family   = ANY(:curve_families)
              AND i.tenor         IS NOT NULL
              AND d.field_name     = :price_field
              AND d.trade_date    >= p.anchor_date
                                     - (:fetch_window_days * INTERVAL '1 day')
              AND d.trade_date    <= p.anchor_date
              AND d.field_value   IS NOT NULL
        ),
        raw_volume AS (
            SELECT
                i.curve_family, i.contract_code,
                d.trade_date, d.field_value::double precision AS volume
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master i
              ON d.instrument_id = i.instrument_id
            CROSS JOIN params p
            WHERE i.is_rolling_contract = TRUE
              AND i.curve_family   = ANY(:curve_families)
              AND i.tenor         IS NOT NULL
              AND d.field_name     = :volume_field
              AND d.trade_date    >= p.anchor_date
                                     - (:fetch_window_days * INTERVAL '1 day')
              AND d.trade_date    <= p.anchor_date
              AND d.field_value   IS NOT NULL
        ),
        raw_oi AS (
            SELECT
                i.curve_family, i.contract_code,
                d.trade_date, d.field_value::double precision AS open_interest
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master i
              ON d.instrument_id = i.instrument_id
            CROSS JOIN params p
            WHERE i.is_rolling_contract = TRUE
              AND i.curve_family   = ANY(:curve_families)
              AND i.tenor         IS NOT NULL
              AND d.field_name     = :oi_field
              AND d.trade_date    >= p.anchor_date
                                     - (:fetch_window_days * INTERVAL '1 day')
              AND d.trade_date    <= p.anchor_date
              AND d.field_value   IS NOT NULL
        ),
        price_dedup AS (
            SELECT DISTINCT ON (curve_family, contract_code, trade_date)
                curve_family, contract_code, tenor, trade_date, price
            FROM raw_price
            ORDER BY curve_family, contract_code, trade_date, price
        ),
        volume_dedup AS (
            SELECT DISTINCT ON (curve_family, contract_code, trade_date)
                curve_family, contract_code, trade_date, volume
            FROM raw_volume
            ORDER BY curve_family, contract_code, trade_date, volume
        ),
        oi_dedup AS (
            SELECT DISTINCT ON (curve_family, contract_code, trade_date)
                curve_family, contract_code, trade_date, open_interest
            FROM raw_oi
            ORDER BY curve_family, contract_code, trade_date, open_interest
        ),
        aligned AS (
            -- Per-stem INNER JOIN reproduces the Python intersection.
            SELECT
                p.curve_family, p.contract_code, p.tenor,
                p.trade_date,
                p.price, v.volume, o.open_interest
            FROM price_dedup p
            INNER JOIN volume_dedup v USING (curve_family, contract_code, trade_date)
            INNER JOIN oi_dedup    o USING (curve_family, contract_code, trade_date)
        ),
        with_diff AS (
            SELECT
                curve_family, contract_code, tenor, trade_date,
                price, volume, open_interest,
                price - LAG(price, 1) OVER (
                    PARTITION BY curve_family, contract_code
                    ORDER BY trade_date
                ) AS price_change
            FROM aligned
        ),
        scored AS (
            SELECT
                curve_family, contract_code, tenor, trade_date,
                price, volume, open_interest, price_change,
                CASE
                    WHEN COUNT(*) OVER zw_price >= 60
                     AND STDDEV_SAMP(price) OVER zw_price IS NOT NULL
                     AND STDDEV_SAMP(price) OVER zw_price <> 0
                    THEN ROUND(
                        ((price - AVG(price) OVER zw_price)
                         / NULLIF(STDDEV_SAMP(price) OVER zw_price, 0))::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_price,
                CASE
                    WHEN price_change IS NULL THEN NULL
                    WHEN COUNT(price_change) OVER zw_change >= 60
                     AND STDDEV_SAMP(price_change) OVER zw_change IS NOT NULL
                     AND STDDEV_SAMP(price_change) OVER zw_change <> 0
                    THEN ROUND(
                        ((price_change - AVG(price_change) OVER zw_change)
                         / NULLIF(STDDEV_SAMP(price_change) OVER zw_change, 0))::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_price_change,
                CASE
                    WHEN COUNT(*) OVER zw_vol >= 60
                     AND STDDEV_SAMP(volume) OVER zw_vol IS NOT NULL
                     AND STDDEV_SAMP(volume) OVER zw_vol <> 0
                    THEN ROUND(
                        ((volume - AVG(volume) OVER zw_vol)
                         / NULLIF(STDDEV_SAMP(volume) OVER zw_vol, 0))::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_volume,
                CASE
                    WHEN COUNT(*) OVER zw_oi >= 60
                     AND STDDEV_SAMP(open_interest) OVER zw_oi IS NOT NULL
                     AND STDDEV_SAMP(open_interest) OVER zw_oi <> 0
                    THEN ROUND(
                        ((open_interest - AVG(open_interest) OVER zw_oi)
                         / NULLIF(STDDEV_SAMP(open_interest) OVER zw_oi, 0))::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_open_interest
            FROM with_diff
            WINDOW
                zw_price AS (
                    PARTITION BY curve_family, contract_code
                    ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                ),
                zw_change AS (
                    PARTITION BY curve_family, contract_code
                    ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                ),
                zw_vol AS (
                    PARTITION BY curve_family, contract_code
                    ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                ),
                zw_oi AS (
                    PARTITION BY curve_family, contract_code
                    ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                )
        ),
        per_stem_latest AS (
            SELECT DISTINCT ON (curve_family, contract_code)
                curve_family, contract_code, tenor,
                TO_CHAR(trade_date, 'YYYY-MM-DD') AS as_of_date,
                ROUND(price::numeric, 6)::double precision AS current_price,
                CASE WHEN price_change IS NULL THEN NULL
                     ELSE ROUND(price_change::numeric, 6)::double precision
                END AS daily_price_change,
                ROUND(volume::numeric)::double precision AS current_volume,
                ROUND(open_interest::numeric)::double precision AS current_open_interest,
                z_price, z_price_change, z_volume, z_open_interest
            FROM scored
            ORDER BY curve_family, contract_code, trade_date DESC
        )
        SELECT * FROM per_stem_latest
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "curve_families": list(curve_families),
                "price_field": price_field,
                "volume_field": volume_field,
                "oi_field": oi_field,
                "fetch_window_days": FETCH_WINDOW_DAYS,
                # NULL → COALESCE to CURRENT_DATE inside the CTE.
                "as_of_date": as_of_date.isoformat() if as_of_date else None,
            },
        ).mappings().all()
    return [dict(r) for r in rows]


def sql_ranked_rows(
    per_stem_rows: List[Dict[str, Any]],
    *,
    top_n: int,
    min_abs_z: float,
) -> Dict[str, List[Dict[str, Any]]]:
    """Reproduce the Python ranking step in Python from the SQL per-
    stem rows.

    Returns one ranked list per metric, top_n rows per metric, sorted
    by |z| desc then contract_code asc (matches the Python tie-break).
    """
    metric_to_z_field = {
        "price": "z_price",
        "price_change": "z_price_change",
        "volume": "z_volume",
        "open_interest": "z_open_interest",
    }
    out: Dict[str, List[Dict[str, Any]]] = {}
    for metric, z_field in metric_to_z_field.items():
        candidates = []
        for row in per_stem_rows:
            z = row.get(z_field)
            if z is None:
                continue
            if abs(z) < min_abs_z:
                continue
            candidates.append((z, row))
        candidates.sort(key=lambda zr: (-abs(zr[0]), zr[1]["contract_code"]))
        out[metric] = [
            {**row, "metric": metric, "z_score": z, "rank": idx}
            for idx, (z, row) in enumerate(candidates[:top_n], start=1)
        ]
    return out


def compare_results(
    tool_result: Dict[str, Any],
    sql_ranked: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    mismatches: List[str] = []
    if "error" in tool_result:
        mismatches.append(f"Tool returned error: {tool_result['error']}")
        return mismatches

    # Group tool results by metric.
    tool_by_metric: Dict[str, List[Dict[str, Any]]] = {}
    for row in tool_result["results"]:
        tool_by_metric.setdefault(row["metric"], []).append(row)

    for metric, sql_rows in sql_ranked.items():
        tool_rows = tool_by_metric.get(metric, [])
        if len(tool_rows) != len(sql_rows):
            mismatches.append(
                f"{metric}: row count tool={len(tool_rows)} "
                f"sql={len(sql_rows)}"
            )
            # Continue per-row to surface any extra info.
        for rank_idx, (t_row, s_row) in enumerate(
            zip(tool_rows, sql_rows), start=1,
        ):
            stem = f"{t_row['curve_family']}/{t_row['contract_code']}"
            # contract_code + curve_family must match exactly at each
            # rank — if they don't, the ranking diverged.
            if t_row["contract_code"] != s_row["contract_code"]:
                mismatches.append(
                    f"{metric} rank {rank_idx}: "
                    f"tool={t_row['curve_family']}/{t_row['contract_code']} "
                    f"sql={s_row['curve_family']}/{s_row['contract_code']}"
                )
                continue
            if t_row["curve_family"] != s_row["curve_family"]:
                mismatches.append(
                    f"{metric} rank {rank_idx} ({stem}): "
                    f"curve_family mismatch tool={t_row['curve_family']} "
                    f"sql={s_row['curve_family']}"
                )
            # Tolerance-aware z + snapshot comparison.
            for field in (
                "z_score", "current_price", "current_volume",
                "current_open_interest", "daily_price_change",
            ):
                t_v = t_row.get(field)
                s_v = s_row.get(field)
                if not floats_match(t_v, s_v, field, TOLERANCE_BY_FIELD):
                    mismatches.append(
                        f"{metric} rank {rank_idx} ({stem}) {field}: "
                        f"tool={t_v!r} sql={s_v!r}"
                    )
            if t_row.get("as_of_date") != s_row.get("as_of_date"):
                mismatches.append(
                    f"{metric} rank {rank_idx} ({stem}) as_of_date: "
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
    top_n, min_abs_z, as_of, curve_families = case
    tool_result = calculate_scan_bond_futures_extremes(
        engine=engine,
        params=ScanBondFuturesExtremesInput(
            curve_families=list(curve_families)
            if curve_families != BOND_FUTURES_CURVE_FAMILIES
            else None,
            top_n=top_n,
            min_abs_z_score=min_abs_z,
            as_of_date=as_of,
        ),
    )
    per_stem_rows = sql_per_stem_metric_scores(
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
    sql_ranked = sql_ranked_rows(
        per_stem_rows, top_n=top_n, min_abs_z=min_abs_z,
    )
    return compare_results(tool_result, sql_ranked)


# Round-3 PR8 / PR16 / P5 honest-disclosure SQL validation: when the
# LLM supplies an ``as_of_date`` BEYOND the bond-futures universe's
# last observed ``trade_date``, the Python tool must return the
# documented controlled-error envelope (no top-N rankings, no
# scan_summary) — NOT a normal scan computed on only the actually-
# available rows. The SQL parity for this case is the "zero rows in
# the window between universe_max and the requested anchor" claim:
# the upper-bound-only filter must select ZERO rows, and the Python
# output must be the error envelope.
_FETCH_UNIVERSE_MAX_DATE_SQL = text("""
    SELECT MAX(d.trade_date) AS max_trade_date
    FROM macro_data.market_data_daily d
    JOIN macro_data.instrument_master i
      ON d.instrument_id = i.instrument_id
    WHERE i.curve_family        = ANY(:curve_families)
      AND i.is_rolling_contract = TRUE
      AND i.tenor              IS NOT NULL
""")


_FETCH_BEYOND_MAX_ROW_COUNT_SQL = text("""
    SELECT COUNT(*) AS row_count
    FROM macro_data.market_data_daily d
    JOIN macro_data.instrument_master i
      ON d.instrument_id = i.instrument_id
    WHERE i.curve_family        = ANY(:curve_families)
      AND i.is_rolling_contract = TRUE
      AND i.tenor              IS NOT NULL
      AND d.trade_date          > CAST(:universe_max AS DATE)
      AND d.trade_date         <= CAST(:future_anchor AS DATE)
""")


def run_future_anchor_guard_case(engine) -> List[str]:
    """Validate the future-anchor guard against the live DB:

    1. Pull the universe's current MAX(trade_date) in SQL.
    2. Compute the Python tool with ``as_of_date = max + 1 day``.
    3. Assert the Python output is the controlled-error envelope —
       ``error`` key present, no ``results`` / ``scan_summary``.
    4. Mirror the assertion in SQL: the universe row count strictly
       between ``universe_max`` (exclusive) and the requested anchor
       (inclusive) must be zero — proving the guard is the right
       semantics (no rows exist past the universe max, so any anchor
       strictly past the max maps to an empty window).

    Read-only; pure SELECT.
    """
    mismatches: List[str] = []
    with engine.connect() as conn:
        max_row = conn.execute(
            _FETCH_UNIVERSE_MAX_DATE_SQL,
            {"curve_families": list(BOND_FUTURES_CURVE_FAMILIES)},
        ).first()
    if max_row is None or max_row[0] is None:
        return [
            "future-anchor guard: SQL returned NULL for universe "
            "MAX(trade_date) — bond-futures universe appears to be "
            "empty; cannot validate the guard against a live DB."
        ]
    universe_max: date = (
        max_row[0]
        if isinstance(max_row[0], date)
        else date.fromisoformat(str(max_row[0]))
    )
    future_anchor = universe_max + timedelta(days=1)

    tool_result = calculate_scan_bond_futures_extremes(
        engine=engine,
        params=ScanBondFuturesExtremesInput(
            curve_families=None,
            top_n=5,
            min_abs_z_score=0.0,
            as_of_date=future_anchor,
        ),
    )

    if "error" not in tool_result:
        mismatches.append(
            f"future-anchor guard: Python tool returned a normal scan "
            f"for as_of_date={future_anchor.isoformat()} "
            f"(universe_max={universe_max.isoformat()}) — expected the "
            "controlled-error envelope. Keys present: "
            f"{sorted(tool_result.keys())}"
        )
        return mismatches
    if "results" in tool_result:
        mismatches.append(
            f"future-anchor guard: Python tool returned BOTH 'error' "
            f"and 'results' for as_of_date={future_anchor.isoformat()} "
            "— the controlled-error envelope must not carry rankings."
        )
    if "scan_summary" in tool_result:
        mismatches.append(
            f"future-anchor guard: Python tool returned BOTH 'error' "
            f"and 'scan_summary' for as_of_date="
            f"{future_anchor.isoformat()} — envelope must be error-only."
        )
    # Error text must honestly cite the off-data anchor (P5 honest
    # disclosure).
    error_text = tool_result["error"]
    for substring in ("beyond", "last observed", future_anchor.isoformat()):
        if substring not in error_text:
            mismatches.append(
                f"future-anchor guard: error text missing required "
                f"substring {substring!r}; got: {error_text!r}"
            )

    # SQL parity: zero rows must exist between universe_max
    # (exclusive) and future_anchor (inclusive).
    with engine.connect() as conn:
        count_row = conn.execute(
            _FETCH_BEYOND_MAX_ROW_COUNT_SQL,
            {
                "curve_families": list(BOND_FUTURES_CURVE_FAMILIES),
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
            "premise (no data past universe_max) is false; reconcile "
            "before claiming PASS."
        )
    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the bond-futures universe-extremes scan against "
            "direct SQL."
        ),
    )
    parser.add_argument(
        "--price-field", default=DEFAULT_PRICE_FIELD,
    )
    parser.add_argument(
        "--volume-field", default=DEFAULT_VOLUME_FIELD,
    )
    parser.add_argument("--oi-field", default=DEFAULT_OI_FIELD)
    args = parser.parse_args()

    print("=" * 80)
    print("BOND-FUTURES UNIVERSE-EXTREMES SCAN — SQL VALIDATION")
    print("=" * 80)
    print(f"  price_field   : {args.price_field}")
    print(f"  volume_field  : {args.volume_field}")
    print(f"  oi_field      : {args.oi_field}")
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    cases = REGRESSION_CASES

    def _case_label(c: Case) -> str:
        anchor = c[2].isoformat() if c[2] is not None else "data_max"
        return (
            f"top_n={c[0]} min_abs_z={c[1]} as_of={anchor} "
            f"curves={'(all)' if c[3] == BOND_FUTURES_CURVE_FAMILIES else list(c[3])}"
        )

    print_selected_cases(cases, _case_label)

    print("[2/3] Running tool vs SQL comparisons...")
    # Plus one synthetic "future-anchor guard" case appended after the
    # regression cases (round-3 PR8 / PR16 / P5 honest-disclosure
    # validation). Total = N regression + 1 guard.
    total_case_count = len(cases) + 1
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        label = _case_label(case)
        print_case_header(index, total_case_count, label)
        mismatches = run_case(
            engine,
            case=case,
            price_field=args.price_field,
            volume_field=args.volume_field,
            oi_field=args.oi_field,
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

    # Future-anchor guard case (round-3): assert the Python tool
    # returns the controlled-error envelope for as_of = universe_max +
    # 1 day, and assert the SQL parity (no rows exist past
    # universe_max within the +1d window). Appended to the run after
    # the regression cases.
    guard_label = "future-anchor guard (as_of = universe_max + 1 day)"
    print_case_header(total_case_count, total_case_count, guard_label)
    guard_mismatches = run_future_anchor_guard_case(engine)
    guard_failed: List[Tuple[str, List[str]]] = []
    if guard_mismatches:
        print("FAIL")
        for mismatch in guard_mismatches[:10]:
            print(f"  - {mismatch}")
        if len(guard_mismatches) > 10:
            print(
                f"  - ... plus {len(guard_mismatches) - 10} more mismatches"
            )
        guard_failed.append((guard_label, guard_mismatches))
    else:
        print("PASS")

    print("[3/3] Summary")
    print("-" * 80)
    total_failed = len(failed_cases) + len(guard_failed)
    print(f"  total_cases : {total_case_count}")
    print(f"  passed      : {total_case_count - total_failed}")
    print(f"  failed      : {total_failed}")

    if failed_cases or guard_failed:
        print("\nFAILED CASES:")
        for case, mismatches in failed_cases:
            label = _case_label(case)
            print(f"  - {label} ({len(mismatches)} mismatches)")
        for label, mismatches in guard_failed:
            print(f"  - {label} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
