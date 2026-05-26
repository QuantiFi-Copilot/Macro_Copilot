"""
_capture.py — Live-DB parity-fixture capture for nfp_surprise
==============================================================

Run against the live TimescaleDB to record the real production
output of ``calculate_nfp_surprise`` for the US slot.  Recorded
fixtures lock in current behaviour byte-for-byte; the parity test
replays them offline.

This script mirrors ``tests/fixtures/cpi_surprise_v1/_capture.py``
but pinned to (event_type='nfp', country='US') — the only case for
this US-only primitive per the brief.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/nfp_surprise_v1/_capture.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_FIXTURES_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FIXTURES_DIR.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.sovereign_bonds.tools.nfp_surprise import (  # noqa: E402
    CONFIG_PATH,
    NfpSurpriseInput,
    calculate_nfp_surprise,
)
from shared.analytics.events_fetch import fetch_economic_release_surprises  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


# lookback_releases values chosen to capture all realised US NFP rows
# present in the live DB at v1-land time (16 realised — see manifest
# validation_status).  Setting lookback=16 produces a 16-row display
# series + the z-score warmup boundary (release_z_min_periods=6 fires
# z after 6 realised observations).
_CASES: list[dict] = [
    {
        "fixture_name": "us_nfp_16releases",
        "params": {"lookback_releases": 16},
    },
]


_NULLABLE_FLOAT_COLS = (
    "actual",
    "consensus_median",
    "consensus_high",
    "consensus_low",
    "prior",
    "revised_prior",
    "surprise_std_dev",
)


def _row_dict(row: dict) -> dict:
    out: dict[str, Any] = {
        "event_id": int(row["event_id"]),
        "event_type": row["event_type"],
        "event_category": row["event_category"],
        "country": row["country"],
        "currency": row.get("currency"),
        "release_date": str(row["release_date"]),
        "release_time": (
            None if row.get("release_time") is None
            else str(row["release_time"])
        ),
        "period": row.get("period"),
    }
    for col in _NULLABLE_FLOAT_COLS:
        v = row.get(col)
        out[col] = None if v is None else float(v)
    return out


def _canonicalise(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now_iso_seconds() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _capture_one(case: dict, engine, captured_at: str, db_name: str) -> dict:
    params = NfpSurpriseInput(**case["params"])
    cfg = load_tool_config(CONFIG_PATH)
    z_window = int(cfg.convention_value("release_z_window"))
    buffer_days = int(cfg.convention_value("release_fetch_buffer_days_per_release"))
    country = str(cfg.convention_value("country"))
    event_type = str(cfg.convention_value("event_type"))

    frozen_today = date.today()
    fetch_releases = params.lookback_releases + z_window
    buffer_calendar_days = fetch_releases * buffer_days
    window_start = frozen_today - timedelta(days=buffer_calendar_days)

    df = fetch_economic_release_surprises(
        engine=engine,
        event_type=event_type,
        country=country,
        window_start=window_start,
        window_end=frozen_today,
    )
    raw_rows = [_row_dict(record) for record in df.to_dict(orient="records")]

    expected_output = calculate_nfp_surprise(engine=engine, params=params)

    return {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.sovereign_bonds.tools.nfp_surprise",
        "tool_function": "calculate_nfp_surprise",
        "capture": {
            "captured_at": captured_at,
            "capture_method": "live_db_v1",
            "database_name": db_name,
            "frozen_today": frozen_today.isoformat(),
            "raw_rows_count": len(raw_rows),
            "raw_rows_sha256": _sha256_hex(_canonicalise(raw_rows)),
        },
        "input": {
            "params": case["params"],
            "frozen_today": frozen_today.isoformat(),
            "raw_rows": raw_rows,
        },
        "expected_output": expected_output,
    }


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
            print(f"  ✗ {case['fixture_name']:30s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        out = fixture["expected_output"]
        if "error" in out:
            summary = f"error envelope ({out['error'][:40]}...)"
        else:
            cm = out["current_metrics"]
            summary = (
                f"surprise={cm.get('current_surprise_k_jobs')}k "
                f"z={cm.get('current_z_score')} "
                f"obs={cm.get('observation_count')}"
            )
        print(
            f"  ✓ {case['fixture_name']:30s}  "
            f"rows={cap['raw_rows_count']:4d}  "
            f"frozen={cap['frozen_today']}  "
            f"{summary}"
        )
        successes += 1

    print(f"\nCaptured {successes}/{len(_CASES)} fixtures.")
    if failures:
        print(
            f"{failures} case(s) FAILED — fix the data issue and re-run.  "
            "Existing fixtures are NOT overwritten on failure."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
