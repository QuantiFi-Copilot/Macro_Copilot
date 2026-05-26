"""
_capture.py — Live-DB parity-fixture capture for
              calculate_scan_policy_futures_extremes
================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``calculate_scan_policy_futures_extremes`` for
one or more representative scan invocations. The recorded fixture
locks in the current behaviour byte-for-byte (modulo 1e-9 float
tolerance) so subsequent commits that touch the policy-futures
universe-scan math (directly, via a shared analytics primitive, or
via a YAML edit) can prove the math is unchanged.

Why live data, not synthetic
----------------------------
The point of a parity fixture is to **freeze the current real tool
output before any refactor begins.** Synthetic data only freezes
the math against artificial inputs; real Bloomberg-shaped universe
data exposes idiosyncrasies (per-strip holiday calendars across
SOFR / Euribor / SONIA, days where one strip's volume / OI reports
independently, NaN field values from the upstream feed, the
quarterly roll that rotates the per-strip underlying contract).

Usage
-----
From the project root, with TimescaleDB running and DB env vars
set::

    python tests/fixtures/scan_policy_futures_extremes_v1/_capture.py

Required env vars (defaults match docker-compose.yml so this Just
Works on a typical local setup)::

    DB_HOST=localhost  DB_PORT=5433  DB_USER=quantuser
    DB_PASSWORD=...    DB_NAME=macrodata

The script:

1. Connects to the DB via ``database.database.get_db_engine``.
2. For each case, calls ``fetch_scan_universe_strip_position``
   three times (PX_LAST, PX_VOLUME, OPEN_INT) to capture the exact
   long-format rows the tool's SQL fetcher returns across the
   scan's resolved curve_families.
3. Calls ``fetch_scan_universe_policy_future_reference`` to
   capture the per-stem reference rows (inverse_pricing + SCD2
   metadata).
4. Calls ``calculate_scan_policy_futures_extremes`` (against the
   same DB) to record the tool's full output.
5. Computes a SHA-256 hash of the canonical-JSON serialisation of
   the three raw-row sets + the reference rows for tamper
   detection.
6. Writes a self-contained JSON fixture per case.

The captured ``frozen_today`` is the wall-clock date at capture
time, which is what ``calculate_scan_policy_futures_extremes`` saw
via ``date.today()``. The parity test patches ``date.today()`` back
to that value during replay.

Determinism guarantee
---------------------
After the fixtures are written, replaying them via
``test_scan_policy_futures_extremes_parity.py`` is fully offline
and deterministic — the DB is never touched again. Re-running this
capture script will produce slightly different fixtures (different
``captured_at``, possibly different ``as_of_date`` if the DB has
new data), so do NOT regenerate casually. See ``README.md`` for
the regeneration policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List

# ---------------------------------------------------------------------------
# Path setup so this script can be invoked directly without pytest's
# conftest.
# ---------------------------------------------------------------------------
_FIXTURES_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FIXTURES_DIR.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (  # noqa: E402
    ScanPolicyFuturesExtremesInput,
    calculate_scan_policy_futures_extremes,
)
from rates_agent.policy_futures.tools.scan_policy_futures_extremes.compute import (  # noqa: E402
    CONFIG_PATH as _SCAN_CONFIG_PATH,
)
from shared.analytics.rates_fetch import (  # noqa: E402
    fetch_scan_universe_policy_future_reference,
    fetch_scan_universe_strip_position,
)
from shared.config import load_tool_config  # noqa: E402


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# One representative scan: full universe pinned at 2026-04-08
# (the bond_futures / policy_futures ingest as-of on the live DB —
# verified via MAX(trade_date) probe before this fixture was
# authored). Pinning the date keeps the captured ranking
# DETERMINISTIC across capture runs.

PINNED_ANCHOR_DATE = date(2026, 4, 8)

_CASES: list[dict] = [
    {
        "fixture_name": "full_universe_top3_zfloor",
        "params": {
            "curve_families": None,
            "top_n": 3,
            "min_abs_z_score": 0.0,
            "metrics": None,
            "as_of_date": PINNED_ANCHOR_DATE.isoformat(),
        },
    },
]


# ===========================================================================
# HELPERS
# ===========================================================================

def _row_dict_universe(
    trade_date: Any,
    curve_family: str,
    strip_position: int,
    contract_code: Any,
    field_value: float,
) -> dict:
    if isinstance(trade_date, str):
        td_str = trade_date
    elif hasattr(trade_date, "isoformat"):
        td_str = trade_date.isoformat()[:10]
    else:
        td_str = str(trade_date)
    return {
        "trade_date": td_str,
        "curve_family": str(curve_family),
        "strip_position": int(strip_position),
        "contract_code": (
            str(contract_code) if contract_code is not None else None
        ),
        "field_value": float(field_value),
    }


def _row_dict_reference(row: Any) -> dict:
    expiry = row.get("expiry_date") if hasattr(row, "get") else row["expiry_date"]
    if expiry is None:
        expiry_str = None
    elif hasattr(expiry, "isoformat"):
        expiry_str = expiry.isoformat()[:10]
    else:
        expiry_str = str(expiry)
    return {
        "curve_family": str(row["curve_family"]),
        "contract_code": (
            str(row["contract_code"])
            if row["contract_code"] is not None else None
        ),
        "strip_position": int(row["strip_position"]),
        "inverse_pricing": (
            bool(row["inverse_pricing"])
            if row["inverse_pricing"] is not None else None
        ),
        "underlying_contract_code": (
            str(row["underlying_contract_code"])
            if row["underlying_contract_code"] is not None else None
        ),
        "expiry_date": expiry_str,
        "security_name": (
            str(row["security_name"])
            if row["security_name"] is not None else None
        ),
        "tick_size": (
            float(row["tick_size"])
            if row["tick_size"] is not None else None
        ),
        "tick_value": (
            float(row["tick_value"])
            if row["tick_value"] is not None else None
        ),
        "contract_size": (
            float(row["contract_size"])
            if row["contract_size"] is not None else None
        ),
    }


def _canonicalise(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_four(
    price_rows: list[dict],
    volume_rows: list[dict],
    oi_rows: list[dict],
    reference_rows: list[dict],
) -> str:
    """Hash the canonical-JSON concatenation of the four row sets.

    The parity test must reproduce this combined hash exactly; a
    label prefix on each list disambiguates the streams so a swap
    of (e.g.) price ↔ volume rows changes the hash.
    """
    payload = (
        "PRICE|" + _canonicalise(price_rows)
        + "|VOL|" + _canonicalise(volume_rows)
        + "|OI|" + _canonicalise(oi_rows)
        + "|REF|" + _canonicalise(reference_rows)
    )
    return _sha256_hex(payload)


def _utc_now_iso_seconds() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_curve_families(
    requested: list[str] | None,
) -> list[str]:
    cfg = load_tool_config(_SCAN_CONFIG_PATH)
    whitelist_csv = cfg.convention_value(
        "policy_futures_curve_families",
    )
    whitelist = [s.strip() for s in whitelist_csv.split(",") if s.strip()]
    if requested is None:
        return whitelist
    return list(requested)


# ===========================================================================
# CAPTURE
# ===========================================================================

def _capture_one(case: dict, engine, captured_at: str, db_name: str) -> dict:
    """Run one case against the live DB and assemble the fixture
    dict."""
    params = ScanPolicyFuturesExtremesInput(**case["params"])

    cfg = load_tool_config(_SCAN_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    ffill_limit = cfg.convention_value("ffill_limit_days")
    price_field = cfg.convention_value("default_price_field")
    volume_field = cfg.convention_value("default_volume_field")
    oi_field = cfg.convention_value("default_open_interest_field")
    fetch_window_days = int(z_window * buffer_mult)

    requested_as_of = params.as_of_date
    fetch_anchor = (
        requested_as_of if requested_as_of is not None else date.today()
    )
    frozen_today = fetch_anchor
    fetch_start = fetch_anchor - timedelta(
        days=fetch_window_days + int(ffill_limit)
    )
    curve_families = _resolve_curve_families(params.curve_families)

    # 1. Capture raw rows for each of the three market-data fields.
    raw_price_df = fetch_scan_universe_strip_position(
        engine=engine,
        instrument_type="policy_future",
        field_name=price_field,
        start_date=fetch_start,
        curve_families=curve_families,
        end_date=requested_as_of,
    )
    raw_volume_df = fetch_scan_universe_strip_position(
        engine=engine,
        instrument_type="policy_future",
        field_name=volume_field,
        start_date=fetch_start,
        curve_families=curve_families,
        end_date=requested_as_of,
    )
    raw_oi_df = fetch_scan_universe_strip_position(
        engine=engine,
        instrument_type="policy_future",
        field_name=oi_field,
        start_date=fetch_start,
        curve_families=curve_families,
        end_date=requested_as_of,
    )
    if raw_price_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: 0 "
            f"{price_field} rows on the live DB. Check that the "
            "policy_futures universe is ingested."
        )
    if raw_volume_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: 0 "
            f"{volume_field} rows."
        )
    if raw_oi_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: 0 "
            f"{oi_field} rows."
        )

    raw_price_rows: List[dict] = [
        _row_dict_universe(
            r["trade_date"], r["curve_family"],
            r["strip_position"], r["contract_code"],
            r["field_value"],
        )
        for _, r in raw_price_df.iterrows()
    ]
    raw_volume_rows: List[dict] = [
        _row_dict_universe(
            r["trade_date"], r["curve_family"],
            r["strip_position"], r["contract_code"],
            r["field_value"],
        )
        for _, r in raw_volume_df.iterrows()
    ]
    raw_oi_rows: List[dict] = [
        _row_dict_universe(
            r["trade_date"], r["curve_family"],
            r["strip_position"], r["contract_code"],
            r["field_value"],
        )
        for _, r in raw_oi_df.iterrows()
    ]
    # Sort canonically (by stem then date) so the hash is order-
    # independent across capture runs that re-shuffle row order.
    sort_key = lambda r: (
        r["curve_family"], r["strip_position"], r["trade_date"],
    )
    raw_price_rows.sort(key=sort_key)
    raw_volume_rows.sort(key=sort_key)
    raw_oi_rows.sort(key=sort_key)

    # 2. Capture reference rows.
    ref_df = fetch_scan_universe_policy_future_reference(
        engine=engine,
        as_of_date=fetch_anchor,
        curve_families=curve_families,
    )
    if ref_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: 0 reference "
            "rows. Check policy_futures playbook ingestion."
        )
    reference_rows: List[dict] = [
        _row_dict_reference(r) for _, r in ref_df.iterrows()
    ]
    reference_rows.sort(
        key=lambda r: (r["curve_family"], r["strip_position"]),
    )

    raw_rows_sha256 = _hash_four(
        raw_price_rows, raw_volume_rows, raw_oi_rows, reference_rows,
    )

    # 3. Run the tool against the same DB so expected_output
    # reflects production behaviour.
    expected_output = calculate_scan_policy_futures_extremes(
        engine=engine, params=params,
    )
    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool "
            f"returned error: {expected_output['error']}"
        )

    fixture = {
        "fixture_name": case["fixture_name"],
        "tool_module": (
            "rates_agent.policy_futures.tools."
            "scan_policy_futures_extremes"
        ),
        "tool_function": "calculate_scan_policy_futures_extremes",
        "capture": {
            "captured_at": captured_at,
            "database_name": db_name,
            "raw_price_rows_count": len(raw_price_rows),
            "raw_volume_rows_count": len(raw_volume_rows),
            "raw_oi_rows_count": len(raw_oi_rows),
            "reference_rows_count": len(reference_rows),
            "raw_rows_sha256": raw_rows_sha256,
            "resolved_curve_families": curve_families,
        },
        "input": {
            "params": case["params"],
            "frozen_today": frozen_today.isoformat(),
            "raw_price_rows": raw_price_rows,
            "raw_volume_rows": raw_volume_rows,
            "raw_oi_rows": raw_oi_rows,
            "reference_rows": reference_rows,
        },
        "expected_output": expected_output,
    }
    return fixture


def main() -> None:
    db_name = os.getenv("DB_NAME", "macrodata")
    print(f"Capturing parity fixtures from live DB '{db_name}'.")
    print(f"Output directory: {_FIXTURES_DIR}\n")

    engine = get_db_engine()
    captured_at = _utc_now_iso_seconds()

    successes = 0
    failures = 0
    for case in _CASES:
        try:
            fixture = _capture_one(
                case, engine, captured_at, db_name,
            )
        except Exception as exc:
            failures += 1
            print(
                f"  X {case['fixture_name']:40s}  FAILED: {exc}"
            )
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(
                fixture, f, indent=2, sort_keys=False, default=str,
            )

        cap = fixture["capture"]
        n_rows = len(
            fixture["expected_output"].get("results", []) or []
        )
        print(
            f"  OK {case['fixture_name']:40s}  "
            f"px_rows={cap['raw_price_rows_count']:5d}  "
            f"vol_rows={cap['raw_volume_rows_count']:5d}  "
            f"oi_rows={cap['raw_oi_rows_count']:5d}  "
            f"ref_rows={cap['reference_rows_count']:3d}  "
            f"out_rows={n_rows:3d}"
        )
        successes += 1

    print(f"\nCaptured {successes}/{len(_CASES)} fixtures.")
    if failures:
        print(
            f"{failures} case(s) FAILED — fix the data issue and "
            "re-run. Existing fixtures are NOT overwritten on "
            "failure."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
