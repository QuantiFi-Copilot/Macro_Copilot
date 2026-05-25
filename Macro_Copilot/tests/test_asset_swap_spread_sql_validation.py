#!/usr/bin/env python3
"""
test_asset_swap_spread_sql_validation.py — Layer-B DB-backed validator
=======================================================================

Independently reproduces ``get_asset_swap_spread``'s logic against the
live TimescaleDB and asserts ``current_asw_spread_bps`` matches a raw
SQL fetch within 1e-9 (catalog guardrail — INGEST primitives must
preserve the vendor value bit-exact).

This validator is the load-bearing P12 + PR16 cross-check.  Per the
builder prompt:
> Assert the Python asw_spread output EQUALS the raw-SQL field_value
> within 1e-9 (or exact equality — these are ingested values, no
> computation, so they should match bit-for-bit; the 1e-9 tolerance
> is a safety net for any pandas / numeric type conversion).

Independent from the primitive's compute path:
  - Issues its own SQL query (not via fetch_single_bond_series).
  - Reads ``macro_data.market_data_daily`` JOIN
    ``macro_data.instrument_master`` directly.
  - Compares the raw DB ``field_value`` to the primitive's
    ``current_asw_spread_bps`` + every row of ``time_series``.

Read-only:
  - Only SELECT queries — no INSERT / UPDATE / DELETE / ALTER / DROP.
  - Excluded from pytest collection (see tests/conftest.py); run as
    a standalone script.

Usage:

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser DB_PASSWORD=...
    python tests/test_asset_swap_spread_sql_validation.py

Honest absence:
  Some vendor_tickers may have NULL ASW on the requested date (e.g.
  CAD govvies — playbook header note 4).  The validator picks
  (vendor_ticker, date) anchors known to have non-NULL ASW from the
  pre-flight DB scan; CAD-sparse cells are explicitly skipped per
  ``SKIP_VENDOR_TICKERS``.  Without an explicit ``--allow-empty``
  waiver, an empty available-anchors set fails the gate so a silent
  regression (e.g. wholesale ingestion failure) is loud.
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
from rates_agent.ois.tools.asset_swap_spread import (  # noqa: E402
    AssetSwapSpreadInput,
    AssetSwapSpreadUnavailableError,
    get_asset_swap_spread,
)


# Canonical regression bonds — picked from the live-DB inventory
# (see builder prompt §Live DB inventory).  Each bond has non-NULL
# ASW history on the date below per the operator's pre-flight check.
# When the live DB advances and the latest non-NULL row shifts, the
# validator picks the actual latest non-NULL row per bond.
REGRESSION_BONDS: List[str] = [
    "/isin/DE000BU22130",  # Germany 2Y
    "/isin/US91282CQQ77",  # US 10Y
    "/isin/GB00BTXS1K06",  # UK 10Y
    "/isin/FR0014012II5",  # France 10Y
]

# Bonds known to have sparse ASW data (CAD govvies per playbook
# header note 4) — skip from automated selection but accept if the
# operator passes them explicitly.
SKIP_VENDOR_TICKERS: set = {
    "/isin/CA135087T537",  # Canada 10Y — sparse
}

DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_ASW_FIELD = "ASSET_SWAP_SPD_MID"
DEFAULT_TOLERANCE = 1e-9


def fetch_latest_non_null_asw(
    engine, *, vendor_ticker: str, field_name: str
) -> Tuple[str, float] | None:
    """Pull the most recent (trade_date, field_value) pair with
    non-NULL ASW for this bond.  Returns ``None`` when no non-NULL
    row exists."""
    sql = text(
        """
        SELECT
            TO_CHAR(d.trade_date, 'YYYY-MM-DD') AS trade_date,
            d.field_value::double precision    AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master i
          ON d.instrument_id = i.instrument_id
        WHERE i.vendor_ticker = :vendor_ticker
          AND i.instrument_type = 'sovereign_cash_bond'
          AND d.field_name    = :field_name
          AND d.field_value IS NOT NULL
        ORDER BY d.trade_date DESC
        LIMIT 1
        """
    )
    with engine.connect() as conn:
        row = (
            conn.execute(
                sql,
                {
                    "vendor_ticker": vendor_ticker,
                    "field_name": field_name,
                },
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    return row["trade_date"], float(row["field_value"])


def fetch_full_window_via_sql(
    engine,
    *,
    vendor_ticker: str,
    field_name: str,
    as_of_date: str,
    lookback_days: int,
) -> List[Dict[str, Any]]:
    """Pull the full ASW series for the bond over the displayed
    window.  Used to compare against the Python ``time_series``
    payload row-for-row."""
    sql = text(
        """
        SELECT
            TO_CHAR(d.trade_date, 'YYYY-MM-DD') AS trade_date,
            d.field_value::double precision    AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master i
          ON d.instrument_id = i.instrument_id
        WHERE i.vendor_ticker  = :vendor_ticker
          AND i.instrument_type = 'sovereign_cash_bond'
          AND d.field_name     = :field_name
          AND d.trade_date    >= CAST(:as_of_date AS DATE) - (:lookback_days * INTERVAL '1 day')
          AND d.trade_date    <= CAST(:as_of_date AS DATE)
        ORDER BY d.trade_date
        """
    )
    with engine.connect() as conn:
        rows = (
            conn.execute(
                sql,
                {
                    "vendor_ticker": vendor_ticker,
                    "field_name": field_name,
                    "as_of_date": as_of_date,
                    "lookback_days": lookback_days,
                },
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def compare_one_anchor(
    engine,
    *,
    vendor_ticker: str,
    field_name: str,
    lookback_days: int,
    tolerance: float,
) -> Tuple[str, List[str]]:
    """Validate one (vendor_ticker, latest_non_null_date) anchor.

    Returns (status, mismatches).  status ∈ {'PASS', 'FAIL', 'SKIP'}.
    """
    latest = fetch_latest_non_null_asw(
        engine, vendor_ticker=vendor_ticker, field_name=field_name,
    )
    if latest is None:
        return "SKIP", [
            f"{vendor_ticker}: no non-NULL ASW rows in live DB"
        ]
    latest_date, sql_value = latest

    # Run the Python primitive against the same anchor.
    from datetime import date as _date
    params = AssetSwapSpreadInput(
        vendor_ticker=vendor_ticker,
        as_of_date=_date.fromisoformat(latest_date),
        lookback_days=lookback_days,
    )
    try:
        tool_result = get_asset_swap_spread(engine=engine, params=params)
    except AssetSwapSpreadUnavailableError as exc:
        return "FAIL", [
            f"{vendor_ticker} on {latest_date}: "
            f"primitive raised AssetSwapSpreadUnavailableError ({exc.reason}) "
            f"despite raw SQL finding a non-NULL value {sql_value!r}"
        ]

    mismatches: List[str] = []

    # ---- snapshot parity (catalog-load-bearing: 1e-9) -----------------
    py_snapshot = tool_result["current_metrics"]["current_asw_spread_bps"]
    delta = abs(float(py_snapshot) - sql_value)
    if delta > tolerance:
        mismatches.append(
            f"snapshot: tool={py_snapshot!r} sql={sql_value!r} "
            f"|delta|={delta:.3e} > tol={tolerance:.0e}"
        )

    # ---- TimeSeries row-by-row parity ---------------------------------
    sql_rows = fetch_full_window_via_sql(
        engine,
        vendor_ticker=vendor_ticker,
        field_name=field_name,
        as_of_date=latest_date,
        lookback_days=lookback_days,
    )
    sql_by_date = {r["trade_date"]: r["field_value"] for r in sql_rows}
    for ts_row in tool_result["time_series"]["rows"]:
        sql_v = sql_by_date.get(ts_row["date"])
        if ts_row["value"] is None and sql_v is None:
            continue
        if ts_row["value"] is None or sql_v is None:
            mismatches.append(
                f"time_series[{ts_row['date']}]: "
                f"tool={ts_row['value']!r} sql={sql_v!r} "
                f"(None mismatch)"
            )
            continue
        d = abs(float(ts_row["value"]) - float(sql_v))
        if d > tolerance:
            mismatches.append(
                f"time_series[{ts_row['date']}]: "
                f"tool={ts_row['value']!r} sql={sql_v!r} "
                f"|delta|={d:.3e} > tol={tolerance:.0e}"
            )

    return ("PASS" if not mismatches else "FAIL"), mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the asset_swap_spread tool against direct SQL on "
            "macro_data.market_data_daily (catalog guardrail: 1e-9 parity)."
        )
    )
    parser.add_argument(
        "--bonds", nargs="*", default=None,
        help=(
            "Optional override list of vendor_tickers to validate.  "
            "Defaults to REGRESSION_BONDS (US, DE, UK, FR)."
        ),
    )
    parser.add_argument(
        "--field", default=DEFAULT_ASW_FIELD,
        help=f"Bloomberg field (default {DEFAULT_ASW_FIELD}).",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_LOOKBACK_DAYS,
        help=f"Lookback days (default {DEFAULT_LOOKBACK_DAYS}).",
    )
    parser.add_argument(
        "--tolerance", type=float, default=DEFAULT_TOLERANCE,
        help=f"Float-equality tolerance (default {DEFAULT_TOLERANCE:.0e}).",
    )
    parser.add_argument(
        "--allow-empty", action="store_true",
        help=(
            "Allow exit 0 when every anchor SKIPs (no non-NULL ASW in "
            "the live DB).  Required during pre-resolver-deployment.  "
            "Without this flag, all-SKIPs fails the gate."
        ),
    )
    args = parser.parse_args()

    bonds = args.bonds if args.bonds else REGRESSION_BONDS
    bonds = [b for b in bonds if b not in SKIP_VENDOR_TICKERS]

    print("=" * 80)
    print("GET_ASSET_SWAP_SPREAD TOOL — SQL VALIDATION")
    print("=" * 80)
    print(f"  bonds         : {bonds}")
    print(f"  field         : {args.field}")
    print(f"  lookback_days : {args.days}")
    print(f"  tolerance     : {args.tolerance:.0e}")
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running per-bond parity checks...")
    pass_count = 0
    skip_count = 0
    failed: List[Tuple[str, List[str]]] = []
    for i, vt in enumerate(bonds, start=1):
        print(f"[{i}/{len(bonds)}] {vt}")
        status, msgs = compare_one_anchor(
            engine,
            vendor_ticker=vt,
            field_name=args.field,
            lookback_days=args.days,
            tolerance=args.tolerance,
        )
        if status == "PASS":
            print("  PASS")
            pass_count += 1
        elif status == "SKIP":
            print(f"  SKIP ({msgs[0]})")
            skip_count += 1
        else:
            print("  FAIL")
            for m in msgs[:10]:
                print(f"    - {m}")
            failed.append((vt, msgs))

    print("[3/3] Summary")
    print("-" * 80)
    print(f"  total : {len(bonds)}")
    print(f"  pass  : {pass_count}")
    print(f"  skip  : {skip_count}")
    print(f"  fail  : {len(failed)}")

    if failed:
        print("\nFAILED:")
        for vt, msgs in failed:
            print(f"  - {vt} ({len(msgs)} mismatches)")
        sys.exit(1)

    if pass_count == 0 and skip_count > 0:
        if args.allow_empty:
            print("\n--allow-empty: all anchors SKIPped, treating as honest absence.")
            sys.exit(0)
        print(
            "\nAll anchors SKIPped — no non-NULL ASW in the live DB.  "
            "Pass --allow-empty to waive explicitly during pre-resolver "
            "deployment.  Failing."
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
