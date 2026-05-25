"""
_capture.py — Live-DB parity-fixture capture for
              calculate_futures_volume_oi
================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``calculate_futures_volume_oi`` for one
representative rolling-generic contract (TY1 on UST_FUT 10Y by
default). The recorded fixture locks in the current behaviour byte-
for-byte (modulo 1e-9 float tolerance) so subsequent commits that
touch the bond-futures volume + OI math (directly or via a primitive
in ``shared/analytics/``) can prove the math is unchanged.

Why live data, not synthetic
----------------------------
The point of a parity fixture is to **freeze the current real tool
output before any refactor begins.** Synthetic data only freezes the
math against artificial inputs; real Bloomberg-shaped data exposes
idiosyncrasies (NaN field values, holiday patterns, occasional
duplicate rows, days where volume or OI reports independently) that
synthetic generators don't reproduce.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/futures_volume_oi_v1/_capture.py

Required env vars (defaults match docker-compose.yml so this Just
Works on a typical local setup)::

    DB_HOST=localhost  DB_PORT=5433  DB_USER=quantuser
    DB_PASSWORD=...    DB_NAME=macrodata

The script:

1. Connects to the DB via ``database.database.get_db_engine``.
2. For each case, calls ``fetch_rolling_generic_series`` twice (once
   for PX_VOLUME, once for OPEN_INT) and
   ``fetch_rolling_generic_reference`` once to record the exact
   long-format rows + reference dict the tool's SQL fetchers return.
3. Calls ``calculate_futures_volume_oi`` (against the same DB) to
   record the tool's full output.
4. Computes a SHA-256 hash of the canonical-JSON serialisation of
   ``raw_volume_rows`` + ``raw_oi_rows`` for tamper detection.
5. Writes a self-contained JSON fixture per case.

The captured ``frozen_today`` is the wall-clock date at capture time,
which is what ``calculate_futures_volume_oi`` saw via ``date.today()``.
The parity test patches ``date.today()`` back to that value during
replay.

Determinism guarantee
---------------------
After the fixtures are written, replaying them via
``test_futures_volume_oi_parity.py`` is fully offline and
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
from rates_agent.bond_futures.tools.futures_volume_oi import (  # noqa: E402
    FuturesVolumeOIInput,
    calculate_futures_volume_oi,
)
from rates_agent.bond_futures.tools.futures_volume_oi.compute import (  # noqa: E402
    CONFIG_PATH as _FUTURES_VOLUME_OI_CONFIG_PATH,
)
from shared.analytics.rates_fetch import (  # noqa: E402
    fetch_rolling_generic_reference,
    fetch_rolling_generic_series,
)
from shared.config import load_tool_config  # noqa: E402


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# One representative bond-futures rolling-generic, scoped wide enough
# that the 252d OI window + z-score are meaningful. TY1 is the 10Y UST
# bellwether — guaranteed to be in the rolling-generic universe
# whenever the bond_futures playbook is ingested. Add more entries
# below if multiple contracts should be parity-pinned.

_CASES: list[dict] = [
    {
        "fixture_name": "ty1_ust_fut_10y_365d",
        "params": {
            "curve_family": "UST_FUT",
            "contract_code": "TY1",
            "lookback_days": 365,
        },
    },
]


# ===========================================================================
# HELPERS
# ===========================================================================

def _row_dict(trade_date: Any, field_value: float) -> dict:
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
        "field_value": float(field_value),
    }


def _canonicalise(rows: list[dict]) -> str:
    """Canonical JSON encoding for hashing."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_both(
    volume_rows: list[dict], oi_rows: list[dict],
) -> str:
    """Hash the concatenated canonicalisation of the two row sets.

    The parity test must reproduce this combined hash exactly; a label
    prefix on each list disambiguates the two streams so a swap of
    volume / OI rows changes the hash.
    """
    payload = (
        "VOL|" + _canonicalise(volume_rows)
        + "|OI|" + _canonicalise(oi_rows)
    )
    return _sha256_hex(payload)


def _latest_trade_date(rows: list[dict]) -> str | None:
    if not rows:
        return None
    return max(r["trade_date"] for r in rows)


def _common_latest_trade_date(
    volume_rows: list[dict], oi_rows: list[dict],
) -> str | None:
    if not volume_rows or not oi_rows:
        return None
    vol_dates = {r["trade_date"] for r in volume_rows}
    oi_dates = {r["trade_date"] for r in oi_rows}
    common = vol_dates & oi_dates
    return max(common) if common else None


def _utc_now_iso_seconds() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _serialise_reference(ref: dict | None) -> dict | None:
    if ref is None:
        return None
    out = dict(ref)
    expiry = out.get("expiry_date")
    if expiry is not None and not isinstance(expiry, str):
        out["expiry_date"] = expiry.strftime("%Y-%m-%d")
    contract_size = out.get("contract_size")
    if contract_size is not None:
        out["contract_size"] = float(contract_size)
    return out


# ===========================================================================
# CAPTURE
# ===========================================================================

def _capture_one(case: dict, engine, captured_at: str, db_name: str) -> dict:
    """Run one case against the live DB and assemble the fixture dict."""
    from datetime import timedelta

    params = FuturesVolumeOIInput(**case["params"])
    frozen_today = date.today()

    cfg = load_tool_config(_FUTURES_VOLUME_OI_CONFIG_PATH)
    z_window = cfg.convention_value("oi_z_score_window_days")
    buffer_mult = cfg.convention_value("oi_z_score_buffer_multiplier")
    volume_field = cfg.convention_value("default_volume_field")
    oi_field = cfg.convention_value("default_open_interest_field")
    buffer_days = int(z_window * buffer_mult)
    fetch_start = frozen_today - timedelta(
        days=params.lookback_days + buffer_days
    )

    # 1. Capture the raw rows for volume and open interest.
    raw_vol_df = fetch_rolling_generic_series(
        engine=engine,
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        field_name=volume_field,
        start_date=fetch_start,
    )
    raw_oi_df = fetch_rolling_generic_series(
        engine=engine,
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        field_name=oi_field,
        start_date=fetch_start,
    )
    if raw_vol_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: "
            f"fetch_rolling_generic_series returned 0 volume rows. "
            f"Check that {params.curve_family} {params.contract_code} "
            f"{volume_field} is loaded in the DB."
        )
    if raw_oi_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: "
            f"fetch_rolling_generic_series returned 0 OI rows. "
            f"Check that {params.curve_family} {params.contract_code} "
            f"{oi_field} is loaded in the DB."
        )

    raw_volume_rows = [
        _row_dict(r["trade_date"], r["field_value"])
        for _, r in raw_vol_df.iterrows()
    ]
    raw_volume_rows.sort(key=lambda r: r["trade_date"])
    raw_oi_rows = [
        _row_dict(r["trade_date"], r["field_value"])
        for _, r in raw_oi_df.iterrows()
    ]
    raw_oi_rows.sort(key=lambda r: r["trade_date"])
    raw_rows_sha256 = _hash_both(raw_volume_rows, raw_oi_rows)

    # 2. Capture the reference dict.
    reference = fetch_rolling_generic_reference(
        engine=engine,
        curve_family=params.curve_family,
        contract_code=params.contract_code,
    )
    if reference is None:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: "
            f"fetch_rolling_generic_reference returned None. Check that "
            f"{params.curve_family} {params.contract_code} exists on "
            "instrument_master as a rolling-generic stem."
        )

    # 3. Run the actual tool against the same DB so the captured
    #    expected_output reflects production behaviour.
    expected_output = calculate_futures_volume_oi(
        engine=engine, params=params,
    )
    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool returned "
            f"error: {expected_output['error']}"
        )

    fixture = {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.bond_futures.tools.futures_volume_oi",
        "tool_function": "calculate_futures_volume_oi",
        "capture": {
            "captured_at": captured_at,
            "database_name": db_name,
            "as_of_date": _common_latest_trade_date(raw_volume_rows, raw_oi_rows),
            "raw_volume_rows_count": len(raw_volume_rows),
            "raw_oi_rows_count": len(raw_oi_rows),
            "raw_rows_sha256": raw_rows_sha256,
        },
        "input": {
            "params": case["params"],
            "frozen_today": frozen_today.isoformat(),
            "raw_volume_rows": raw_volume_rows,
            "raw_oi_rows": raw_oi_rows,
            "reference": _serialise_reference(reference),
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
            print(f"  X {case['fixture_name']:32s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        m = fixture["expected_output"]["current_metrics"]
        ts = fixture["expected_output"]["time_series"]
        print(
            f"  OK {case['fixture_name']:32s}  "
            f"vol_rows={cap['raw_volume_rows_count']:5d}  "
            f"oi_rows={cap['raw_oi_rows_count']:5d}  "
            f"ts_rows={len(ts):4d}  "
            f"as_of={cap['as_of_date']}  "
            f"vol={m['current_volume']}  oi={m['current_open_interest']}  "
            f"z={m.get('oi_z_score')}"
        )
        successes += 1

    print(f"\nCaptured {successes}/{len(_CASES)} fixtures.")
    if failures:
        print(
            f"{failures} case(s) FAILED — fix the data issue and re-run. "
            "Existing fixtures are NOT overwritten on failure."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
