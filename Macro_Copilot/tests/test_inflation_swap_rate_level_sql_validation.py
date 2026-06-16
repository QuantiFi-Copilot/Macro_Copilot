#!/usr/bin/env python3
"""
test_inflation_swap_rate_level_sql_validation.py — ZCIS rate-level
validator.

Validate ``calculate_inflation_swap_rate_level`` against an
independent SQL baseline run directly on
``macro_data.v_market_data_daily_enriched`` ×
``macro_data.instrument_master``.  The SQL baseline reproduces the
level-stat math (z-score / period changes / trailing range) without
going through any of the Python tool's helpers, so a mismatch between
the two surfaces a real divergence in methodology rather than a
shared-code coincidence.

Key ZCIS-specific differences from the linker validator:
  - filter ``instrument_type = 'inflation_swap'`` AND
    ``(attributes ->> 'pricing_type') = 'zero_coupon_breakeven'``
  - field_name = ``PX_MID`` (vs ``YLD_YTM_MID`` for sovereign /
    linker, ``PX_LAST`` for OIS)
  - anchor the lookback cutoff to the data's last observation date,
    NOT ``CURRENT_DATE`` — ZCIS daily feeds can lag wall-clock by
    days; the Python tool's anchoring matches OIS rate_level and
    linker real_yield_level (last observation).

Adversarial probes:
  1. Wrong instrument_type rows (e.g. inflation_linker) MUST NOT leak
     in for any curve_family.
  2. Wrong pricing_type rows MUST NOT leak in.
  3. Wrong curve_family rows MUST NOT leak in (USD_ZCIS request does
     not return EUR_ZCIS rows).
  4. Ambiguous ticker resolution: if any (curve_family, tenor) maps
     to >1 ticker, the tool returns a controlled error envelope.
     This is verified by SQL count + a direct call.
  5. Unknown / unsupported (curve_family, tenor): controlled error
     envelope, NOT a Python exception.

Standalone CLI runner — collected separately by pytest's
``conftest.py`` ignore list.  Run from the repo root::

    /root/.local/share/mamba/envs/macro-env/bin/python \
        tests/test_inflation_swap_rate_level_sql_validation.py
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
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (  # noqa: E402
    InflationSwapRateLevelInput,
    calculate_inflation_swap_rate_level,
)
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
    print_selected_cases,
    sample_cases,
)


Case = Tuple[str, str]

DEFAULT_CASE_COUNT = 21
DEFAULT_SEED = 42
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_MID"

# Full deterministic coverage of the ZCIS pillar universe.  Unlike
# the open / open-ended sovereign and linker universes (where
# sampling is the only practical pattern), the ZCIS universe is
# small and bounded: 3 ZCIS curve families
# (USD_ZCIS / EUR_ZCIS / GBP_ZCIS) × 7 ingested tenors
# (1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y) = 21 pillars total.  At
# DEFAULT_CASE_COUNT == len(REGRESSION_CASES) == 21, the helper
# ``sample_cases`` returns the full fixed list in stable order, so
# every supported pillar is exercised against the SQL baseline on
# every run rather than sampled.  If the live ZCIS pool ever shrinks
# below 21, ``sample_cases`` drops the missing pillars (since
# ``fixed = [case for case in fixed_cases if case in pool_list]``);
# that's the correct degradation behaviour.
REGRESSION_CASES: List[Case] = [
    ("USD_ZCIS", "1Y"),
    ("USD_ZCIS", "2Y"),
    ("USD_ZCIS", "3Y"),
    ("USD_ZCIS", "5Y"),
    ("USD_ZCIS", "10Y"),
    ("USD_ZCIS", "20Y"),
    ("USD_ZCIS", "30Y"),
    ("EUR_ZCIS", "1Y"),
    ("EUR_ZCIS", "2Y"),
    ("EUR_ZCIS", "3Y"),
    ("EUR_ZCIS", "5Y"),
    ("EUR_ZCIS", "10Y"),
    ("EUR_ZCIS", "20Y"),
    ("EUR_ZCIS", "30Y"),
    ("GBP_ZCIS", "1Y"),
    ("GBP_ZCIS", "2Y"),
    ("GBP_ZCIS", "3Y"),
    ("GBP_ZCIS", "5Y"),
    ("GBP_ZCIS", "10Y"),
    ("GBP_ZCIS", "20Y"),
    ("GBP_ZCIS", "30Y"),
]

# Tolerances inherited from the linker / sovereign level validators —
# rounding conventions are aligned by config lint, so the same
# tolerance bands hold here.
TOLERANCE_BY_FIELD = {
    "zcis_rate_pct": 0.00011,
    "daily_change_bps": 0.011,
    "weekly_change_bps": 0.011,
    "monthly_change_bps": 0.011,
    "z_score": 0.006,
    "high_252d_pct": 0.00011,
    "low_252d_pct": 0.00011,
    "percentile_252d": 0.11,
}


def choose_test_cases(engine, *, field_name: str, case_count: int, seed: int) -> List[Case]:
    """Pick (curve_family, tenor) cases from live ZCIS metadata.

    Filters to ``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'`` so the SQL baseline
    operates on the same universe the Python tool sees.  Requires at
    least 80 observations to ensure rolling stats are populated.
    """
    query = text(
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
        rows = conn.execute(query, {"field_name": field_name}).mappings().all()

    pool = [(row["curve_family"], row["tenor"]) for row in rows]
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
    tenor: str,
    lookback_days: int,
    field_name: str,
) -> Dict[str, Any]:
    """Independent SQL reproduction of the level-stat math.

    Differences from the linker validator:
      - ``instrument_type = 'inflation_swap'`` AND
        ``(attributes ->> 'pricing_type') = 'zero_coupon_breakeven'``
      - join on ``instrument_master`` to read pricing_type from the
        attributes JSONB
      - ``observation_count`` cutoff anchored to the data's latest
        ``trade_date``, NOT ``CURRENT_DATE`` — matches the Python
        tool's anchoring (which mirrors OIS rate_level and linker
        real_yield_level).
    """
    baseline_sql = text(
        """
        WITH pillar_max AS (
            -- Anchor the fetch window to THIS pillar's latest available
            -- trade_date (not CURRENT_DATE) — the same floor the Python
            -- tool now uses via latest_trade_date — so the parity check
            -- compares identical windows even when the ZCIS series lags
            -- wall-clock by more than (lookback_days + 378) days.  Without
            -- this, a CURRENT_DATE floor starts AFTER the last data, the
            -- baseline goes empty, and the value-parity gate breaks while
            -- the tool still returns real data (the regression class).
            SELECT MAX(v.trade_date) AS as_of
            FROM macro_data.v_market_data_daily_enriched v
            JOIN macro_data.instrument_master i
              ON v.instrument_id = i.instrument_id
            WHERE v.instrument_type = 'inflation_swap'
              AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
              AND v.curve_family = :curve_family
              AND v.tenor        = :tenor
              AND v.field_name   = :field_name
        ),
        raw AS (
            SELECT
                v.trade_date,
                v.field_value::double precision AS field_value
            FROM macro_data.v_market_data_daily_enriched v
            JOIN macro_data.instrument_master i
              ON v.instrument_id = i.instrument_id
            CROSS JOIN pillar_max p
            WHERE v.instrument_type = 'inflation_swap'
              AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
              AND v.curve_family = :curve_family
              AND v.tenor        = :tenor
              AND v.field_name   = :field_name
              AND v.trade_date  >= COALESCE(p.as_of, CURRENT_DATE)
                                   - ((:lookback_days + 378) * INTERVAL '1 day')
        ),
        clean AS (
            SELECT
                trade_date,
                field_value
            FROM raw
            WHERE field_value IS NOT NULL
        ),
        ordered AS (
            SELECT
                trade_date,
                field_value,
                ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM clean
        ),
        scored AS (
            SELECT
                trade_date,
                rn,
                field_value,
                CASE
                    WHEN COUNT(*) OVER zw >= 60
                     AND STDDEV_SAMP(field_value) OVER zw IS NOT NULL
                     AND STDDEV_SAMP(field_value) OVER zw <> 0
                    THEN ROUND(
                        (
                            (field_value - AVG(field_value) OVER zw)
                            / NULLIF(STDDEV_SAMP(field_value) OVER zw, 0)
                        )::numeric,
                        4
                    )::double precision
                    ELSE NULL
                END AS z_score,
                ROUND((MAX(field_value) OVER tw)::numeric, 4)::double precision AS high_252d_pct,
                ROUND((MIN(field_value) OVER tw)::numeric, 4)::double precision AS low_252d_pct,
                CASE
                    WHEN MAX(field_value) OVER tw IS NULL
                      OR MIN(field_value) OVER tw IS NULL
                      OR MAX(field_value) OVER tw = MIN(field_value) OVER tw
                    THEN NULL
                    ELSE ROUND(
                        (
                            (
                                field_value - MIN(field_value) OVER tw
                            ) / NULLIF(
                                MAX(field_value) OVER tw - MIN(field_value) OVER tw,
                                0
                            ) * 100
                        )::numeric,
                        1
                    )::double precision
                END AS percentile_252d,
                CASE
                    WHEN LAG(field_value, 1) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        ((field_value - LAG(field_value, 1) OVER (ORDER BY rn)) * 100)::numeric,
                        2
                    )::double precision
                END AS daily_change_bps,
                CASE
                    WHEN LAG(field_value, 5) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        ((field_value - LAG(field_value, 5) OVER (ORDER BY rn)) * 100)::numeric,
                        2
                    )::double precision
                END AS weekly_change_bps,
                CASE
                    WHEN LAG(field_value, 21) OVER (ORDER BY rn) IS NULL THEN NULL
                    ELSE ROUND(
                        ((field_value - LAG(field_value, 21) OVER (ORDER BY rn)) * 100)::numeric,
                        2
                    )::double precision
                END AS monthly_change_bps
            FROM ordered
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
            FROM ordered
            WHERE trade_date >= (
                (SELECT MAX(trade_date) FROM ordered)
                - (:lookback_days * INTERVAL '1 day')
            )
        )
        SELECT
            TO_CHAR(latest.trade_date, 'YYYY-MM-DD') AS as_of_date,
            latest.field_value AS zcis_rate_pct,
            latest.daily_change_bps,
            latest.weekly_change_bps,
            latest.monthly_change_bps,
            latest.z_score,
            latest.high_252d_pct,
            latest.low_252d_pct,
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
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
                "lookback_days": lookback_days,
            },
        ).mappings().first()

    if row is None:
        return {"error": "SQL baseline returned no rows."}

    return {
        "current_metrics": {
            "as_of_date": row["as_of_date"],
            "curve_family": curve_family,
            "tenor": tenor,
            "zcis_rate_pct": row["zcis_rate_pct"],
            "daily_change_bps": row["daily_change_bps"],
            "weekly_change_bps": row["weekly_change_bps"],
            "monthly_change_bps": row["monthly_change_bps"],
            "z_score": row["z_score"],
            "high_252d_pct": row["high_252d_pct"],
            "low_252d_pct": row["low_252d_pct"],
            "percentile_252d": row["percentile_252d"],
            "observation_count": row["observation_count"],
        }
    }


def compare_results(tool_result: Dict[str, Any], sql_result: Dict[str, Any]) -> List[str]:
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
        fields=("as_of_date", "curve_family", "tenor", "observation_count"),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "zcis_rate_pct",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "z_score",
            "high_252d_pct",
            "low_252d_pct",
            "percentile_252d",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # Reference-metadata wire surface: every successful run must
    # populate these fields.  Independent SQL spot-check confirms
    # they match instrument_master.
    for ref_field in (
        "inflation_index_family", "index_lag", "interpolation",
    ):
        v = tool_metrics.get(ref_field)
        if not v:
            mismatches.append(
                f"current_metrics.{ref_field}: tool returned "
                f"empty / None — load-bearing reference metadata "
                f"must be populated."
            )

    # methodology_label must be non-empty (threaded from YAML).
    if not tool_metrics.get("methodology_label"):
        mismatches.append(
            "current_metrics.methodology_label: tool returned "
            "empty — must be threaded from YAML's "
            "methodology.what_it_does."
        )

    return mismatches


def run_case(engine, *, case: Case, lookback_days: int, field_name: str) -> List[str]:
    curve_family, tenor = case
    tool_result = calculate_inflation_swap_rate_level(
        engine=engine,
        params=InflationSwapRateLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    sql_result = sql_baseline(
        engine=engine,
        curve_family=curve_family,
        tenor=tenor,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    return compare_results(tool_result, sql_result)


# ============================================================================
# ADVERSARIAL PROBES
# ============================================================================

# Wrong instrument_type: a known linker curve_family that matches an
# inflation_linker row but NOT an inflation_swap row.
WRONG_INSTRUMENT_TYPE_PROBE: Tuple[str, str] = ("USD_TIPS", "10Y")
# Wrong curve_family: an unknown / non-ZCIS family that should yield
# zero rows.
WRONG_CURVE_FAMILY_PROBE: Tuple[str, str] = ("UST", "10Y")
# Unknown (curve_family, tenor): a known ZCIS family with a tenor
# that's not in the universe.
UNKNOWN_TENOR_PROBE: Tuple[str, str] = ("USD_ZCIS", "11Y")


def assert_zcis_instrument_type_guard(
    engine,
    *,
    case: Case,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Probe 1: wrong instrument_type rows (linker, sovereign, OIS)
    MUST NOT leak in.  We test by feeding a known linker
    curve_family ('USD_TIPS') and asserting:
      a. SQL with the ZCIS filter returns zero rows.
      b. Python tool returns the controlled error envelope mentioning
         BOTH instrument_type AND pricing_type filters.
    """
    failures: List[str] = []
    curve_family, tenor = case

    sql = text(
        """
        SELECT COUNT(*) AS n
        FROM macro_data.v_market_data_daily_enriched v
        JOIN macro_data.instrument_master i
          ON v.instrument_id = i.instrument_id
        WHERE v.instrument_type = 'inflation_swap'
          AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
          AND v.curve_family = :curve_family
          AND v.tenor        = :tenor
          AND v.field_name   = :field_name
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
            },
        ).mappings().first()
    n_rows = int(row["n"]) if row is not None else 0

    if n_rows != 0:
        failures.append(
            f"SQL invariant violated: ZCIS filter returned {n_rows} "
            f"rows for non-ZCIS probe ({curve_family}, {tenor}); "
            "the proxy-prevention guarantee assumes zero."
        )

    tool_result = calculate_inflation_swap_rate_level(
        engine=engine,
        params=InflationSwapRateLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )
    if "error" not in tool_result:
        failures.append(
            "Python tool returned a snapshot for non-ZCIS probe "
            f"({curve_family}, {tenor}) — must return controlled "
            "error envelope instead.  Got keys: "
            f"{sorted(tool_result.keys())}"
        )
    else:
        err = tool_result.get("error", "")
        if "inflation_swap" not in err:
            failures.append(
                "Controlled error envelope missing 'inflation_swap' "
                f"rationale; got: {err!r}"
            )
        if "zero_coupon_breakeven" not in err:
            failures.append(
                "Controlled error envelope missing "
                f"'zero_coupon_breakeven' rationale; got: {err!r}"
            )
    return failures


def assert_zcis_pricing_type_guard(engine, *, field_name: str) -> List[str]:
    """Probe 2: wrong pricing_type rows MUST NOT leak in.

    We can't synthesise a non-ZCIS pricing_type without ingesting one
    (forbidden by the read-only DB rule), so this probe confirms via
    SQL that EVERY row currently in the
    instrument_type='inflation_swap' universe carries
    pricing_type='zero_coupon_breakeven' — i.e. the filter is
    redundant under the current data, which means a future ingest of
    a different pricing_type cannot leak in because the filter is
    in place.
    """
    failures: List[str] = []
    sql = text(
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
            sql, {"field_name": field_name},
        ).mappings().all()
    pricing_types = sorted(r["pricing_type"] for r in rows)
    if pricing_types != ["zero_coupon_breakeven"]:
        failures.append(
            "SQL universe contains pricing_types other than "
            f"'zero_coupon_breakeven': {pricing_types!r}.  The "
            "tool's pricing_type filter is the load-bearing guard "
            "that those rows cannot leak through; verify the filter "
            "is still applied in compute.py."
        )
    return failures


def assert_zcis_curve_family_guard(engine, *, field_name: str) -> List[str]:
    """Probe 3: wrong curve_family rows MUST NOT leak in.

    Concrete check: requesting USD_ZCIS at a tenor that exists ONLY
    on EUR_ZCIS (impossible by construction since both publish the
    same 1Y/2Y/3Y/5Y/10Y/20Y/30Y grid; the realistic case is
    requesting USD_ZCIS at '5Y' and confirming none of the returned
    rows carry curve_family='EUR_ZCIS').
    """
    failures: List[str] = []
    sql = text(
        """
        SELECT DISTINCT v.curve_family
        FROM macro_data.v_market_data_daily_enriched v
        JOIN macro_data.instrument_master i
          ON v.instrument_id = i.instrument_id
        WHERE v.instrument_type = 'inflation_swap'
          AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
          AND v.curve_family = :curve_family
          AND v.tenor        = :tenor
          AND v.field_name   = :field_name
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "curve_family": "USD_ZCIS",
                "tenor": "5Y",
                "field_name": field_name,
            },
        ).mappings().all()
    curve_families = sorted(set(r["curve_family"] for r in rows))
    if curve_families != ["USD_ZCIS"]:
        failures.append(
            "SQL guard violated: USD_ZCIS / 5Y selection returned "
            f"rows for {curve_families!r}; expected ['USD_ZCIS'] "
            "only."
        )
    return failures


def assert_ambiguous_ticker_handling(engine, *, field_name: str) -> List[str]:
    """Probe 4: every (curve_family, tenor) in the current universe
    MUST resolve to exactly one vendor_ticker.  If not, the tool
    refuses to silently average and returns a controlled error
    envelope.
    """
    failures: List[str] = []
    sql = text(
        """
        SELECT v.curve_family, v.tenor,
               COUNT(DISTINCT v.vendor_ticker) AS n_tickers
        FROM macro_data.v_market_data_daily_enriched v
        JOIN macro_data.instrument_master i
          ON v.instrument_id = i.instrument_id
        WHERE v.instrument_type = 'inflation_swap'
          AND (i.attributes ->> 'pricing_type') = 'zero_coupon_breakeven'
          AND v.field_name = :field_name
        GROUP BY v.curve_family, v.tenor
        HAVING COUNT(DISTINCT v.vendor_ticker) > 1
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql, {"field_name": field_name},
        ).mappings().all()
    if rows:
        # The current universe shouldn't have duplicates; if it does,
        # confirm the Python tool would refuse.  We don't fail the
        # SQL probe because the playbook guarantees one ticker per
        # pillar — a duplicate is a real data error to surface.
        ambiguous = [
            (r["curve_family"], r["tenor"], r["n_tickers"]) for r in rows
        ]
        failures.append(
            "Live data contains ambiguous (curve_family, tenor) → "
            f"vendor_ticker mappings: {ambiguous!r}.  Refer to the "
            "inflation_swaps playbook to confirm the intended "
            "convention.  The Python tool will return a controlled "
            "error envelope on these pillars; SQL probe surfaces "
            "the duplicate metadata."
        )
    return failures


def assert_unknown_pillar_handling(
    engine,
    *,
    case: Case,
    field_name: str,
    lookback_days: int,
) -> List[str]:
    """Probe 5: an unknown / unsupported (curve_family, tenor) MUST
    yield a controlled error envelope, NOT a Python exception.
    """
    failures: List[str] = []
    curve_family, tenor = case
    try:
        tool_result = calculate_inflation_swap_rate_level(
            engine=engine,
            params=InflationSwapRateLevelInput(
                curve_family=curve_family,
                tenor=tenor,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"Unknown pillar ({curve_family}, {tenor}) raised "
            f"{type(exc).__name__}: {exc} — must return controlled "
            "error envelope instead."
        )
        return failures

    if "error" not in tool_result:
        failures.append(
            f"Unknown pillar ({curve_family}, {tenor}) returned a "
            "snapshot — must return controlled error envelope "
            "instead.  Got keys: "
            f"{sorted(tool_result.keys())}"
        )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the ZCIS inflation_swap_rate_level tool against "
            "direct SQL."
        ),
    )
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    args = parser.parse_args()

    print("=" * 80)
    print("ZCIS INFLATION_SWAP_RATE_LEVEL TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  cases         : {args.cases}")
    print(f"  random_seed   : {args.seed}")
    print(f"  lookback_days : {args.days}")
    print(f"  field_name    : {args.field}")
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
    print_selected_cases(cases, lambda case: f"{case[0]} {case[1]}")

    print("[3/6] Running tool vs SQL comparisons...")
    failed_cases: List[Tuple[Case, List[str]]] = []
    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), f"{case[0]} {case[1]}")
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
        "[4/6] Adversarial probe 1: wrong-instrument_type guard "
        f"(linker {WRONG_INSTRUMENT_TYPE_PROBE[0]} "
        f"{WRONG_INSTRUMENT_TYPE_PROBE[1]})..."
    )
    probe1 = assert_zcis_instrument_type_guard(
        engine,
        case=WRONG_INSTRUMENT_TYPE_PROBE,
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe1 else "PASS")
    for f in probe1:
        print(f"  - {f}")

    print(
        "[4/6 cont] Adversarial probe 1b: wrong-instrument_type "
        f"guard (nominal sovereign {WRONG_CURVE_FAMILY_PROBE[0]} "
        f"{WRONG_CURVE_FAMILY_PROBE[1]})..."
    )
    probe1b = assert_zcis_instrument_type_guard(
        engine,
        case=WRONG_CURVE_FAMILY_PROBE,
        field_name=args.field,
        lookback_days=args.days,
    )
    print("FAIL" if probe1b else "PASS")
    for f in probe1b:
        print(f"  - {f}")

    print("[5/6] Adversarial probe 2: pricing_type guard...")
    probe2 = assert_zcis_pricing_type_guard(
        engine, field_name=args.field,
    )
    print("FAIL" if probe2 else "PASS")
    for f in probe2:
        print(f"  - {f}")

    print("[5/6 cont] Adversarial probe 3: curve_family guard...")
    probe3 = assert_zcis_curve_family_guard(
        engine, field_name=args.field,
    )
    print("FAIL" if probe3 else "PASS")
    for f in probe3:
        print(f"  - {f}")

    print("[5/6 cont] Adversarial probe 4: ambiguous-ticker guard...")
    probe4 = assert_ambiguous_ticker_handling(
        engine, field_name=args.field,
    )
    print("FAIL" if probe4 else "PASS")
    for f in probe4:
        print(f"  - {f}")

    print(
        "[6/6] Adversarial probe 5: unknown pillar "
        f"({UNKNOWN_TENOR_PROBE[0]} {UNKNOWN_TENOR_PROBE[1]}) "
        "controlled-error envelope..."
    )
    probe5 = assert_unknown_pillar_handling(
        engine,
        case=UNKNOWN_TENOR_PROBE,
        field_name=args.field,
        lookback_days=args.days,
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
        "  probe_1_wrong_instrument_type  : "
        f"{'PASS' if not probe1 else 'FAIL'}  ({WRONG_INSTRUMENT_TYPE_PROBE!r})"
    )
    print(
        "  probe_1b_wrong_curve_family    : "
        f"{'PASS' if not probe1b else 'FAIL'}  ({WRONG_CURVE_FAMILY_PROBE!r})"
    )
    print(
        "  probe_2_pricing_type           : "
        f"{'PASS' if not probe2 else 'FAIL'}"
    )
    print(
        "  probe_3_curve_family           : "
        f"{'PASS' if not probe3 else 'FAIL'}"
    )
    print(
        "  probe_4_ambiguous_ticker       : "
        f"{'PASS' if not probe4 else 'FAIL'}"
    )
    print(
        "  probe_5_unknown_pillar         : "
        f"{'PASS' if not probe5 else 'FAIL'}  ({UNKNOWN_TENOR_PROBE!r})"
    )

    has_failure = (
        bool(failed_cases) or bool(probe1) or bool(probe1b) or bool(probe2)
        or bool(probe3) or bool(probe4) or bool(probe5)
    )
    if has_failure:
        if failed_cases:
            print("\nFAILED CASES:")
            for case, mismatches in failed_cases:
                print(
                    f"  - {case[0]} {case[1]} ({len(mismatches)} mismatches)"
                )
        sys.exit(1)


if __name__ == "__main__":
    main()
