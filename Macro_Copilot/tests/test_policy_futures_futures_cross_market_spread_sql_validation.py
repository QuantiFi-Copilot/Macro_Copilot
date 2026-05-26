#!/usr/bin/env python3
"""
test_policy_futures_futures_cross_market_spread_sql_validation.py —
                                                          policy-
                                                          futures
                                                          cross-
                                                          market-
                                                          spread
                                                          validator

Independently reproduces the policy-futures matched-strip cross-
market implied-rate differential in SQL (spread_value_pct, 1-day
delta on the spread axis, rolling 252-day z-score on the SPREAD
series, trailing 252-day high / low / mid / percentile,
observation_count) and asserts the Python tool result matches the
SQL result row-for-row across a representative set of
(curve_family_a, curve_family_b, strip_position) cases.

Independence
------------
The SQL baseline filters via ``v_market_data_daily_enriched`` with
the same ``(curve_family IN (a, b), (attributes->>'strip_position')::int,
field_name)`` predicate ``fetch_cross_market_strip`` uses, INNER-
joins the two legs' series on trade_date (the equivalent of the
Python tool's ffill + pivot + dropna(subset=both legs) alignment
step), and independently reads the per-leg ``inverse_pricing`` flag
from ``instrument_master.attributes`` to drive the implied-rate
conversion. The z-score / range / percentile / 1-day delta are
computed in SQL via WINDOW functions; no Python primitive
involvement.

Future-anchor guard cross-check
-------------------------------
The runner also calls the Python tool with
``as_of_date = min(universe_max_per_leg) + 1 day`` and asserts the
controlled-error envelope returns with the documented prefix
("no scoreable strip:"); a separate SQL probe counts rows past the
requested anchor on the binding leg to verify the guard had a real
reason to fire.

Coverage
--------
Regression cases cover the three pairwise combinations of the V1
universe at strip_position 1, plus a wider strip_position (4) for
the strongest-coverage pair. If a live RFR-vs-IBOR pair is present
in the data (SOFR_FUT vs EUR_SHORT_RATE_FUT, SONIA_FUT vs
EUR_SHORT_RATE_FUT), the runner will exercise the mixed-regime
branch and verify both per-leg regime labels appear in the tool's
methodology disclosure.

This script is a standalone CLI runner (matching the existing
``test_*_sql_validation.py`` shape); it must be EXCLUDED from
pytest collection via ``tests/conftest.py``'s ``collect_ignore``
list.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.policy_futures.tools.schemas import (  # noqa: E402
    FuturesCrossMarketSpreadInput,
)
from rates_agent.policy_futures.tools.futures_cross_market_spread import (  # noqa: E402
    calculate_futures_cross_market_spread,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


# (curve_family_a, curve_family_b, strip_position)
Case = Tuple[str, str, int]


DEFAULT_CASE_COUNT = 8
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"


# The policy_futures playbook universe per ADR 0013 — strip-
# position-keyed only. Spans both regimes:
#   - RFR: SOFR_FUT (US), SONIA_FUT (UK)
#   - IBOR: EUR_SHORT_RATE_FUT (Euro area)
POLICY_FUTURES_CURVE_FAMILIES: List[str] = [
    "SOFR_FUT",
    "EUR_SHORT_RATE_FUT",
    "SONIA_FUT",
]


# Regression cases — guaranteed to be in the strip universe whenever
# the policy_futures playbook is ingested. Cover all three pairwise
# combinations at strip 1 (same-regime SOFR/SONIA pair AND two mixed-
# regime pairs against EUR_SHORT_RATE_FUT) AND a wider strip
# (strip 4) on the SOFR vs SONIA pair so the SQL baseline exercises
# both short-strip and deeper-strip shapes.
REGRESSION_CASES: List[Case] = [
    ("SOFR_FUT", "SONIA_FUT", 1),
    ("SOFR_FUT", "EUR_SHORT_RATE_FUT", 1),
    ("SONIA_FUT", "EUR_SHORT_RATE_FUT", 1),
    ("SOFR_FUT", "SONIA_FUT", 4),
]


TOLERANCE_BY_FIELD = {
    # Python tool rounds spread_value_round_decimals=4; SQL rounds
    # to 4.
    "spread_value_pct": 1e-3,
    "daily_change_spread_value_pct": 1e-3,
    # implied_rate_round_decimals = 4
    "implied_rate_pct_a": 1e-3,
    "implied_rate_pct_b": 1e-3,
    "high_252d_spread_value_pct": 1e-3,
    "low_252d_spread_value_pct": 1e-3,
    "mid_252d_spread_value_pct": 1e-3,
    # z_score_round_decimals = 4
    "z_score_spread": 6e-3,
    # percentile_round_decimals = 1
    "percentile_252d": 0.11,
}


def choose_test_cases(
    engine, *, field_name: str, case_count: int, seed: int,
) -> List[Case]:
    """Pick (curve_family_a, curve_family_b, strip_position) triples
    from the live universe where BOTH strip slots have at least 252
    trading days of PX_LAST in the lookback buffer — keeps the SQL
    z-score numerically meaningful."""
    query = text(
        """
        WITH strips AS (
            SELECT
                i.curve_family,
                (i.attributes->>'strip_position')::int AS strip_position,
                COUNT(*) AS n_rows
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master i
              ON d.instrument_id = i.instrument_id
            WHERE i.is_rolling_contract = TRUE
              AND i.instrument_type     = 'policy_future'
              AND i.curve_family        = ANY(:curve_families)
              AND d.field_name          = :field_name
              AND d.trade_date         >= CURRENT_DATE - INTERVAL '700 days'
            GROUP BY i.curve_family, (i.attributes->>'strip_position')::int
            HAVING COUNT(*) >= 252
        )
        SELECT
            a.curve_family    AS curve_family_a,
            b.curve_family    AS curve_family_b,
            a.strip_position  AS strip_position
        FROM strips a
        JOIN strips b
          ON a.strip_position = b.strip_position
         AND a.curve_family   < b.curve_family
        ORDER BY a.strip_position,
                 a.curve_family,
                 b.curve_family
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            query,
            {
                "field_name": field_name,
                "curve_families": POLICY_FUTURES_CURVE_FAMILIES,
            },
        ).mappings().all()
    pool = [
        (
            row["curve_family_a"],
            row["curve_family_b"],
            int(row["strip_position"]),
        )
        for row in rows
    ]
    return sample_cases(
        pool,
        fixed_cases=REGRESSION_CASES,
        case_count=case_count,
        seed=seed,
    )


def _read_inverse_pricing_pair(
    engine,
    *,
    curve_family_a: str,
    curve_family_b: str,
    strip_position: int,
) -> Dict[str, Any]:
    """Read the per-leg ``inverse_pricing`` flag + ``contract_code``
    stems independently (PR8 cross-check). Each leg's flag is read
    INDEPENDENTLY — unlike the same-curve calendar-spread tool, a
    cross-market spread does NOT require the two legs to agree on
    the flag."""
    flag_sql = text(
        """
        SELECT
            i.curve_family,
            (i.attributes->>'strip_position')::int AS strip_position,
            (i.attributes->>'inverse_pricing')::boolean AS inverse_pricing,
            i.contract_code
        FROM macro_data.instrument_master i
        WHERE i.curve_family IN (:curve_family_a, :curve_family_b)
          AND (i.attributes->>'strip_position')::int = :strip_position
          AND i.is_rolling_contract = TRUE
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            flag_sql,
            {
                "curve_family_a": curve_family_a,
                "curve_family_b": curve_family_b,
                "strip_position": strip_position,
            },
        ).mappings().all()
    by_curve = {r["curve_family"]: dict(r) for r in rows}
    if (
        curve_family_a not in by_curve
        or curve_family_b not in by_curve
    ):
        return {"error": "SQL baseline: missing instrument_master row."}
    return {
        "inverse_priced_a": bool(by_curve[curve_family_a]["inverse_pricing"]),
        "inverse_priced_b": bool(by_curve[curve_family_b]["inverse_pricing"]),
        "contract_code_a": by_curve[curve_family_a]["contract_code"],
        "contract_code_b": by_curve[curve_family_b]["contract_code"],
    }


def sql_baseline(
    engine,
    *,
    curve_family_a: str,
    curve_family_b: str,
    strip_position: int,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Reproduce the Python primitive's cross-market-spread snapshot
    using window functions over the two markets' PX_LAST history.
    Reads the per-strip ``inverse_pricing`` flag from
    ``instrument_master.attributes`` independently per leg so the
    implied-rate conversion is driven off metadata, just like the
    Python primitive."""
    pair = _read_inverse_pricing_pair(
        engine,
        curve_family_a=curve_family_a,
        curve_family_b=curve_family_b,
        strip_position=strip_position,
    )
    if "error" in pair:
        return pair
    inverse_priced_a = pair["inverse_priced_a"]
    inverse_priced_b = pair["inverse_priced_b"]
    contract_code_a = pair["contract_code_a"]
    contract_code_b = pair["contract_code_b"]

    # ------------------------------------------------------------------
    # Window-function baseline on the aligned (intersection) series.
    # Per-leg implied-rate conversion is metadata-driven: SQL emits
    # ``CASE WHEN <flag> THEN (100 - price) ELSE price END`` on each
    # leg independently.
    # ------------------------------------------------------------------
    a_conv = (
        f"CASE WHEN {('TRUE' if inverse_priced_a else 'FALSE')} "
        f"THEN (100.0 - price_a) ELSE price_a END"
    )
    b_conv = (
        f"CASE WHEN {('TRUE' if inverse_priced_b else 'FALSE')} "
        f"THEN (100.0 - price_b) ELSE price_b END"
    )

    # The two markets trade on DIFFERENT calendars (US for SOFR_FUT,
    # UK for SONIA_FUT, Euro for EUR_SHORT_RATE_FUT). The Python tool
    # bridges single-side holidays via ffill (limit=5) before
    # intersecting the two legs; a naive INNER JOIN in SQL would
    # drop those one-side-only dates and yield a different
    # observation_count + slightly different rolling-window
    # members. To match the Python tool's alignment, this baseline
    # reproduces the ffill step in SQL using the same window-
    # function pattern the sovereign cross_market_spread validator
    # uses (see tests/test_cross_market_sql_validation.py:142). The
    # filled-then-intersected series is the honest SQL analogue of
    # ``pivot_and_align_tenors(ffill_limit=5)``.
    baseline_sql = text(
        f"""
        WITH raw AS (
            SELECT
                trade_date,
                curve_family,
                field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE curve_family IN (:curve_family_a, :curve_family_b)
              AND (attributes->>'strip_position')::int = :strip_position
              AND field_name   = :field_name
              AND trade_date  >= CURRENT_DATE - ((:lookback_days + 378) * INTERVAL '1 day')
              AND field_value IS NOT NULL
        ),
        date_grid AS (
            SELECT DISTINCT trade_date
            FROM raw
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
                MAX(CASE WHEN r.curve_family = :curve_family_a
                         THEN r.field_value END) AS price_a_raw,
                MAX(CASE WHEN r.curve_family = :curve_family_b
                         THEN r.field_value END) AS price_b_raw
            FROM numbered n
            LEFT JOIN raw r
              ON r.trade_date = n.trade_date
            GROUP BY n.trade_date, n.rn
        ),
        ffill_marks AS (
            SELECT
                *,
                MAX(CASE WHEN price_a_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS a_last_rn,
                MAX(CASE WHEN price_b_raw IS NOT NULL THEN rn END)
                    OVER (ORDER BY rn
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS b_last_rn
            FROM joined
        ),
        filled AS (
            SELECT
                trade_date,
                rn,
                CASE
                    WHEN price_a_raw IS NOT NULL THEN price_a_raw
                    WHEN a_last_rn IS NOT NULL AND rn - a_last_rn <= 5
                    THEN MAX(CASE WHEN price_a_raw IS NOT NULL
                                  THEN price_a_raw END)
                         OVER (PARTITION BY a_last_rn)
                    ELSE NULL
                END AS price_a,
                CASE
                    WHEN price_b_raw IS NOT NULL THEN price_b_raw
                    WHEN b_last_rn IS NOT NULL AND rn - b_last_rn <= 5
                    THEN MAX(CASE WHEN price_b_raw IS NOT NULL
                                  THEN price_b_raw END)
                         OVER (PARTITION BY b_last_rn)
                    ELSE NULL
                END AS price_b
            FROM ffill_marks
        ),
        aligned AS (
            SELECT
                trade_date,
                price_a,
                price_b
            FROM filled
            WHERE price_a IS NOT NULL AND price_b IS NOT NULL
            ORDER BY trade_date
        ),
        spreads AS (
            SELECT
                trade_date,
                price_a,
                price_b,
                ({a_conv}) AS implied_rate_a,
                ({b_conv}) AS implied_rate_b,
                ({a_conv}) - ({b_conv}) AS spread_value_pct,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM aligned
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                implied_rate_a,
                implied_rate_b,
                spread_value_pct,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(spread_value_pct) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(spread_value_pct) OVER zw <> 0
                    THEN ROUND(
                        (
                            (
                                spread_value_pct
                                - AVG(spread_value_pct) OVER zw
                            )
                            / NULLIF(
                                STDDEV_SAMP(spread_value_pct) OVER zw,
                                0
                            )
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score_spread,
                ROUND(
                    (MAX(spread_value_pct) OVER tw)::numeric, 4
                )::double precision AS high_252d_spread_value_pct,
                ROUND(
                    (MIN(spread_value_pct) OVER tw)::numeric, 4
                )::double precision AS low_252d_spread_value_pct,
                CASE
                    WHEN MAX(spread_value_pct) OVER tw IS NULL
                      OR MIN(spread_value_pct) OVER tw IS NULL
                      OR MAX(spread_value_pct) OVER tw
                         = MIN(spread_value_pct) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                spread_value_pct
                                - MIN(spread_value_pct) OVER tw
                            )
                            / NULLIF(
                                MAX(spread_value_pct) OVER tw
                                - MIN(spread_value_pct) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d,
                CASE
                    WHEN LAG(spread_value_pct, 1)
                         OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        (
                            spread_value_pct
                            - LAG(spread_value_pct, 1)
                              OVER (ORDER BY rn)
                        )::numeric,
                        4
                    )::double precision
                END AS daily_change_spread_value_pct
            FROM spreads
            WINDOW
                zw AS (ORDER BY rn ROWS BETWEEN 251 PRECEDING AND CURRENT ROW),
                tw AS (ORDER BY rn ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
        ),
        latest AS (
            SELECT *
            FROM scored
            ORDER BY trade_date DESC
            LIMIT 1
        ),
        obs AS (
            SELECT COUNT(*) AS observation_count
            FROM aligned
            WHERE trade_date >= (
                SELECT MAX(trade_date) - (:lookback_days * INTERVAL '1 day')
                FROM aligned
            )
        )
        SELECT
            TO_CHAR(latest.trade_date, 'YYYY-MM-DD') AS as_of_date,
            ROUND(latest.implied_rate_a::numeric, 4)::double precision
                AS implied_rate_pct_a,
            ROUND(latest.implied_rate_b::numeric, 4)::double precision
                AS implied_rate_pct_b,
            ROUND(latest.spread_value_pct::numeric, 4)::double precision
                AS spread_value_pct,
            latest.daily_change_spread_value_pct,
            latest.z_score_spread,
            latest.high_252d_spread_value_pct,
            latest.low_252d_spread_value_pct,
            ROUND(
                ((
                    latest.high_252d_spread_value_pct
                    + latest.low_252d_spread_value_pct
                ) / 2.0)::numeric,
                4
            )::double precision AS mid_252d_spread_value_pct,
            latest.percentile_252d,
            obs.observation_count
        FROM latest
        CROSS JOIN obs
        """
    )

    with engine.connect() as conn:
        row = conn.execute(
            baseline_sql,
            {
                "curve_family_a": curve_family_a,
                "curve_family_b": curve_family_b,
                "strip_position": strip_position,
                "field_name": field_name,
                "lookback_days": lookback_days,
            },
        ).mappings().first()

    if row is None:
        return {"error": "SQL baseline returned no rows."}

    return {
        "current_metrics": {
            "as_of_date": row["as_of_date"],
            "curve_family_a": curve_family_a,
            "curve_family_b": curve_family_b,
            "strip_position": strip_position,
            "contract_code_a": contract_code_a,
            "contract_code_b": contract_code_b,
            "inverse_priced_a": inverse_priced_a,
            "inverse_priced_b": inverse_priced_b,
            "implied_rate_pct_a": row["implied_rate_pct_a"],
            "implied_rate_pct_b": row["implied_rate_pct_b"],
            "spread_value_pct": row["spread_value_pct"],
            "daily_change_spread_value_pct": (
                row["daily_change_spread_value_pct"]
            ),
            "z_score_spread": row["z_score_spread"],
            "high_252d_spread_value_pct": (
                row["high_252d_spread_value_pct"]
            ),
            "low_252d_spread_value_pct": (
                row["low_252d_spread_value_pct"]
            ),
            "mid_252d_spread_value_pct": (
                row["mid_252d_spread_value_pct"]
            ),
            "percentile_252d": row["percentile_252d"],
            "observation_count": row["observation_count"],
        }
    }


def sql_universe_max_date(
    engine,
    *,
    curve_family: str,
    strip_position: int,
    field_name: str,
) -> date:
    """SQL-only probe for the strip's MAX(trade_date) — independent
    of the Python tool's ``fetch_strip_position_max_date`` helper."""
    sql = text(
        """
        SELECT MAX(trade_date) AS max_trade_date
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND (attributes->>'strip_position')::int = :strip_position
          AND field_name   = :field_name
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
                "field_name": field_name,
            },
        ).first()
    if row is None or row[0] is None:
        raise RuntimeError(
            f"sql_universe_max_date: no rows for {curve_family} "
            f"strip_position={strip_position}, field={field_name}"
        )
    value = row[0]
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def sql_count_rows_past_anchor(
    engine,
    *,
    curve_family: str,
    strip_position: int,
    field_name: str,
    anchor: date,
) -> int:
    """Count rows in the strip's series with trade_date > anchor —
    used to assert the future-anchor guard had a real reason to
    fire."""
    sql = text(
        """
        SELECT COUNT(*) AS n_rows_past
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND (attributes->>'strip_position')::int = :strip_position
          AND field_name   = :field_name
          AND trade_date   > :anchor
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "strip_position": strip_position,
                "field_name": field_name,
                "anchor": anchor.isoformat(),
            },
        ).first()
    return int(row[0]) if row is not None else 0


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
            "as_of_date", "curve_family_a", "curve_family_b",
            "strip_position",
            "contract_code_a", "contract_code_b",
            "inverse_priced_a", "inverse_priced_b",
            "observation_count",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "implied_rate_pct_a",
            "implied_rate_pct_b",
            "spread_value_pct",
            "daily_change_spread_value_pct",
            "z_score_spread",
            "high_252d_spread_value_pct",
            "low_252d_spread_value_pct",
            "mid_252d_spread_value_pct",
            "percentile_252d",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )
    return mismatches


def run_case(
    engine, *, case: Case, lookback_days: int, field_name: str,
) -> List[str]:
    curve_family_a, curve_family_b, strip_position = case
    tool_result = calculate_futures_cross_market_spread(
        engine=engine,
        params=FuturesCrossMarketSpreadInput(
            curve_family_a=curve_family_a,
            curve_family_b=curve_family_b,
            strip_position=strip_position,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family_a=curve_family_a,
        curve_family_b=curve_family_b,
        strip_position=strip_position,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    mismatches = compare_results(tool_result, sql_result)

    # ------------------------------------------------------------------
    # Mixed-regime label cross-check: if the tool succeeded AND the
    # two legs have different short_rate_regime labels, verify the
    # methodology disclosure carries the MIXED-REGIME marker
    # (catalog guardrail — no pack-average collapse).
    # ------------------------------------------------------------------
    if "error" not in tool_result:
        cm = tool_result["current_metrics"]
        if cm.get("short_rate_regime_a") != cm.get("short_rate_regime_b"):
            disclosure = tool_result.get("methodology_disclosure", "")
            if "MIXED-REGIME" not in disclosure:
                mismatches.append(
                    "Mixed-regime pair detected (a="
                    f"{cm.get('short_rate_regime_a')!r}, b="
                    f"{cm.get('short_rate_regime_b')!r}) but "
                    "methodology_disclosure is missing the "
                    "'MIXED-REGIME' marker required by the catalog "
                    "guardrail."
                )
            if "pack-average" not in disclosure:
                mismatches.append(
                    "Mixed-regime pair detected but "
                    "methodology_disclosure is missing the "
                    "'pack-average' refusal required by the catalog "
                    "guardrail."
                )

    # ------------------------------------------------------------------
    # Future-anchor guard cross-check — call the tool with
    # as_of_date = min(universe_max_per_leg) + 1 day and assert the
    # controlled-error envelope. Independently SQL-probe rows past
    # the requested anchor on the binding leg (must be 0).
    # ------------------------------------------------------------------
    try:
        max_a = sql_universe_max_date(
            engine=engine,
            curve_family=curve_family_a,
            strip_position=strip_position,
            field_name=field_name,
        )
        max_b = sql_universe_max_date(
            engine=engine,
            curve_family=curve_family_b,
            strip_position=strip_position,
            field_name=field_name,
        )
    except RuntimeError as exc:
        mismatches.append(f"Future-anchor probe failed: {exc}")
        return mismatches
    binding_max = min(max_a, max_b)
    future_anchor = binding_max + timedelta(days=1)
    binding_leg_curve = (
        curve_family_a if max_a <= max_b else curve_family_b
    )
    rows_past = sql_count_rows_past_anchor(
        engine=engine,
        curve_family=binding_leg_curve,
        strip_position=strip_position,
        field_name=field_name,
        anchor=future_anchor,
    )
    if rows_past != 0:
        mismatches.append(
            f"Future-anchor SQL probe found {rows_past} row(s) past "
            f"{future_anchor.isoformat()} on the binding leg "
            f"{binding_leg_curve} — the universe_max probe is out of "
            "sync with the row data."
        )

    guard_result = calculate_futures_cross_market_spread(
        engine=engine,
        params=FuturesCrossMarketSpreadInput(
            curve_family_a=curve_family_a,
            curve_family_b=curve_family_b,
            strip_position=strip_position,
            lookback_days=lookback_days,
            field_name=field_name,
            as_of_date=future_anchor,
        ),
    )
    if "error" not in guard_result:
        mismatches.append(
            f"Future-anchor guard did NOT fire for "
            f"as_of_date={future_anchor.isoformat()}; tool returned "
            "a snapshot instead of the controlled-error envelope."
        )
    elif "no scoreable strip" not in guard_result["error"]:
        mismatches.append(
            f"Future-anchor guard fired with the WRONG envelope "
            f"prefix: {guard_result['error']!r}"
        )

    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the policy-futures cross-market-spread "
            "monitor against direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("POLICY-FUTURES CROSS-MARKET-SPREAD MONITOR — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
    print("-" * 80)

    print("[1/4] Creating DB engine...")
    engine = get_db_engine()

    print("[2/4] Selecting validation cases from live strip universe...")
    cases = choose_test_cases(
        engine,
        field_name=args.field,
        case_count=args.cases,
        seed=args.seed,
    )
    print_selected_cases(
        cases,
        lambda case: (
            f"{case[0]} vs {case[1]} (strip={case[2]})"
        ),
    )

    print("[3/4] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(
            index, len(cases),
            f"{case[0]} vs {case[1]} (strip={case[2]})",
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

    print("[4/4] Summary")
    print("-" * 80)
    print(f"  total_cases : {len(cases)}")
    print(f"  passed      : {len(cases) - len(failed_cases)}")
    print(f"  failed      : {len(failed_cases)}")

    if failed_cases:
        print("\nFAILED CASES:")
        for case, mismatches in failed_cases:
            print(f"  - {case[0]} vs {case[1]} (strip={case[2]}) "
                  f"({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
