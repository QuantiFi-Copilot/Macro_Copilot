"""
_capture.py — Live-DB parity-fixture capture for
              calculate_scan_bond_futures_extremes
================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``calculate_scan_bond_futures_extremes`` for one
or more representative scan invocations. The recorded fixture locks
in the current behaviour byte-for-byte (modulo 1e-9 float tolerance)
so subsequent commits that touch the bond-futures universe-scan math
(directly, via a shared analytics primitive, or via a YAML edit) can
prove the math is unchanged.

Why live data, not synthetic
----------------------------
The point of a parity fixture is to **freeze the current real tool
output before any refactor begins.** Synthetic data only freezes the
math against artificial inputs; real Bloomberg-shaped universe data
exposes idiosyncrasies (per-stem NaN field values, holiday patterns
that differ across markets, days where one stem's volume or OI
reports independently, drift in the rolling-generic underlying
contract as the front rolls) that synthetic generators don't
reproduce.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/scan_bond_futures_extremes_v1/_capture.py

Required env vars (defaults match docker-compose.yml so this Just
Works on a typical local setup)::

    DB_HOST=localhost  DB_PORT=5433  DB_USER=quantuser
    DB_PASSWORD=...    DB_NAME=macrodata

The script:

1. Connects to the DB via ``database.database.get_db_engine``.
2. For each case, calls ``fetch_rolling_generic_universe_series``
   three times (PX_LAST, PX_VOLUME, OPEN_INT) to capture the exact
   long-format rows the tool's SQL fetcher returns across the scan's
   resolved curve_families.
3. Calls ``calculate_scan_bond_futures_extremes`` (against the same
   DB) to record the tool's full output.
4. Computes a SHA-256 hash of the canonical-JSON serialisation of
   the three raw-row sets for tamper detection.
5. Writes a self-contained JSON fixture per case.

The captured ``frozen_today`` is the wall-clock date at capture time,
which is what ``calculate_scan_bond_futures_extremes`` saw via
``date.today()``. The parity test patches ``date.today()`` back to
that value during replay.

Determinism guarantee
---------------------
After the fixtures are written, replaying them via
``test_scan_bond_futures_extremes_parity.py`` is fully offline and
deterministic — the DB is never touched again. Re-running this
capture script will produce slightly different fixtures (different
``captured_at``, possibly different ``as_of_date`` if the DB has new
data), so do NOT regenerate casually. See ``README.md`` for the
regeneration policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup so this script can be invoked directly without pytest's conftest.
# ---------------------------------------------------------------------------
_FIXTURES_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FIXTURES_DIR.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (  # noqa: E402
    ScanBondFuturesExtremesInput,
    calculate_scan_bond_futures_extremes,
)
from rates_agent.bond_futures.tools.scan_bond_futures_extremes.compute import (  # noqa: E402
    CONFIG_PATH as _SCAN_CONFIG_PATH,
)
from shared.analytics.rates_fetch import (  # noqa: E402
    fetch_rolling_generic_universe_series,
)
from shared.config import load_tool_config  # noqa: E402


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# One representative scan: the full default universe with min_abs_z=0
# and top_n=3 so the fixture is small but exercises every metric block
# (the threshold could mask blocks on quiet days). Add more entries
# here to pin additional curve-family subsets.
#
# Round-2 mandatory-fix #2: ``lookback_days`` has been removed from
# the input schema. The fixture now pins ``as_of_date`` to the
# bond_futures ingest as-of (``2026-04-08``) so the captured ranking
# is DETERMINISTIC across capture runs — re-running the capture on a
# different wall-clock day still produces the same numbers given the
# same DB state. This is the round-2 win the reviewer required.
#
# The pinned date lives inside the loaded macro-tsdb range and is far
# enough from the current ingest tail that every stem in the universe
# has full coverage on either side.

PINNED_ANCHOR_DATE = date(2026, 4, 8)

_CASES: list[dict] = [
    {
        "fixture_name": "full_universe_top3_zfloor",
        "params": {
            "curve_families": None,
            "top_n": 3,
            "min_abs_z_score": 0.0,
            "as_of_date": PINNED_ANCHOR_DATE.isoformat(),
        },
    },
]


# ===========================================================================
# HELPERS
# ===========================================================================

def _row_dict(
    trade_date: Any, curve_family: str, contract_code: str,
    tenor: str, field_value: float,
) -> dict:
    """Render a single raw_rows entry in canonical form."""
    if isinstance(trade_date, str):
        td_str = trade_date
    elif hasattr(trade_date, "isoformat"):
        iso = trade_date.isoformat()
        td_str = iso[:10]
    else:
        td_str = str(trade_date)
    return {
        "trade_date": td_str,
        "curve_family": curve_family,
        "contract_code": contract_code,
        "tenor": tenor,
        "field_value": float(field_value),
    }


def _canonicalise(rows: list[dict]) -> str:
    """Canonical JSON encoding for hashing."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_three(
    price_rows: list[dict],
    volume_rows: list[dict],
    oi_rows: list[dict],
) -> str:
    """Hash the concatenated canonicalisation of the three row sets.

    The parity test must reproduce this combined hash exactly; a
    label prefix on each list disambiguates the three streams so a
    swap of (e.g.) price ↔ volume rows changes the hash.
    """
    payload = (
        "PRICE|" + _canonicalise(price_rows)
        + "|VOL|" + _canonicalise(volume_rows)
        + "|OI|" + _canonicalise(oi_rows)
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
    """Resolve the case's curve_families against the YAML whitelist."""
    cfg = load_tool_config(_SCAN_CONFIG_PATH)
    whitelist_csv = cfg.convention_value("bond_futures_curve_families")
    whitelist = [s.strip() for s in whitelist_csv.split(",") if s.strip()]
    if requested is None:
        return whitelist
    return list(requested)


# ===========================================================================
# CAPTURE
# ===========================================================================

def _capture_one(case: dict, engine, captured_at: str, db_name: str) -> dict:
    """Run one case against the live DB and assemble the fixture dict."""
    from datetime import timedelta

    params = ScanBondFuturesExtremesInput(**case["params"])

    cfg = load_tool_config(_SCAN_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    ffill_limit = cfg.convention_value("ffill_limit_days")
    price_field = cfg.convention_value("default_price_field")
    volume_field = cfg.convention_value("default_volume_field")
    oi_field = cfg.convention_value("default_open_interest_field")
    fetch_window_days = int(z_window * buffer_mult)

    # Round-2 mandatory-fix #2: the fetch anchor is the explicit
    # ``as_of_date`` when supplied (deterministic) and falls back to
    # wall-clock ``date.today()`` only when the LLM omitted the
    # anchor. The fixture pins ``as_of_date`` so the fetch_start
    # below is reproducible across capture runs. ``frozen_today`` —
    # which the parity replay patches into compute's ``date`` —
    # tracks the same anchor for parity-replay consistency.
    requested_as_of = params.as_of_date
    fetch_anchor = (
        requested_as_of if requested_as_of is not None else date.today()
    )
    frozen_today = fetch_anchor
    fetch_start = fetch_anchor - timedelta(
        days=fetch_window_days + int(ffill_limit)
    )
    curve_families = _resolve_curve_families(params.curve_families)

    # 1. Capture the raw rows for each of the three fields.
    raw_price_df = fetch_rolling_generic_universe_series(
        engine=engine,
        curve_families=curve_families,
        field_name=price_field,
        start_date=fetch_start,
    )
    raw_volume_df = fetch_rolling_generic_universe_series(
        engine=engine,
        curve_families=curve_families,
        field_name=volume_field,
        start_date=fetch_start,
    )
    raw_oi_df = fetch_rolling_generic_universe_series(
        engine=engine,
        curve_families=curve_families,
        field_name=oi_field,
        start_date=fetch_start,
    )
    if raw_price_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: "
            f"fetch_rolling_generic_universe_series returned 0 "
            f"{price_field} rows. Check that the bond_futures "
            "universe is loaded in the DB."
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

    raw_price_rows = [
        _row_dict(
            r["trade_date"], r["curve_family"], r["contract_code"],
            r["tenor"], r["field_value"],
        )
        for _, r in raw_price_df.iterrows()
    ]
    raw_volume_rows = [
        _row_dict(
            r["trade_date"], r["curve_family"], r["contract_code"],
            r["tenor"], r["field_value"],
        )
        for _, r in raw_volume_df.iterrows()
    ]
    raw_oi_rows = [
        _row_dict(
            r["trade_date"], r["curve_family"], r["contract_code"],
            r["tenor"], r["field_value"],
        )
        for _, r in raw_oi_df.iterrows()
    ]
    # Sort canonically (by stem then date) so the hash is order-
    # independent across capture runs that re-shuffle row order.
    sort_key = lambda r: (r["curve_family"], r["contract_code"], r["trade_date"])
    raw_price_rows.sort(key=sort_key)
    raw_volume_rows.sort(key=sort_key)
    raw_oi_rows.sort(key=sort_key)
    raw_rows_sha256 = _hash_three(raw_price_rows, raw_volume_rows, raw_oi_rows)

    # 2. Run the tool against the same DB so expected_output reflects
    #    production behaviour.
    expected_output = calculate_scan_bond_futures_extremes(
        engine=engine, params=params,
    )
    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool returned "
            f"error: {expected_output['error']}"
        )

    fixture = {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.bond_futures.tools.scan_bond_futures_extremes",
        "tool_function": "calculate_scan_bond_futures_extremes",
        "capture": {
            "captured_at": captured_at,
            "database_name": db_name,
            "raw_price_rows_count": len(raw_price_rows),
            "raw_volume_rows_count": len(raw_volume_rows),
            "raw_oi_rows_count": len(raw_oi_rows),
            "raw_rows_sha256": raw_rows_sha256,
            "resolved_curve_families": curve_families,
        },
        "input": {
            "params": case["params"],
            "frozen_today": frozen_today.isoformat(),
            "raw_price_rows": raw_price_rows,
            "raw_volume_rows": raw_volume_rows,
            "raw_oi_rows": raw_oi_rows,
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
            fixture = _capture_one(case, engine, captured_at, db_name)
        except Exception as exc:
            failures += 1
            print(f"  X {case['fixture_name']:40s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        n_rows = len(fixture["expected_output"].get("results", []) or [])
        print(
            f"  OK {case['fixture_name']:40s}  "
            f"px_rows={cap['raw_price_rows_count']:5d}  "
            f"vol_rows={cap['raw_volume_rows_count']:5d}  "
            f"oi_rows={cap['raw_oi_rows_count']:5d}  "
            f"out_rows={n_rows:3d}"
        )
        successes += 1

    print(f"\nCaptured {successes}/{len(_CASES)} fixtures.")
    if failures:
        print(
            f"{failures} case(s) FAILED — fix the data issue and "
            "re-run. Existing fixtures are NOT overwritten on failure."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
