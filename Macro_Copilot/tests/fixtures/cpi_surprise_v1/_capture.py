"""
_capture.py — Live-DB parity-fixture capture for cpi_surprise
==============================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``calculate_cpi_surprise`` for the documented
test cases.  The recorded fixtures lock in current behaviour byte-
for-byte; the parity test then replays them offline.

Why live data, not synthetic
----------------------------
The shipped v1 fixtures are synthetic (see README.md) because the
event extractor is forward-only and the
``rates_agent/playbooks/economic_releases.yml`` playbook is rolling
out — captured fixtures at v1-land time would either be empty for
some countries or carry incomplete rolling z-score warmup.

Once `event_calendar` has full ``cpi_yoy`` / ``hicp_yoy`` coverage
for the canonical regression slots (US / EU / UK / JP), run this
script to replace the synthetic v1 fixtures with live-DB captures.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/cpi_surprise_v1/_capture.py

Required env vars (defaults match docker-compose.yml)::

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser
    DB_PASSWORD=...   DB_NAME=macrodata

The script:

1. Connects via ``database.database.get_db_engine``.
2. For each test case, runs the same fetcher
   (``shared.analytics.events_fetch.fetch_economic_release_surprises``)
   the primitive issues to record the exact rows the tool will see.
3. Calls ``calculate_cpi_surprise`` against the same DB to record
   the tool's full output.
4. Computes a SHA-256 hash of the canonical-JSON serialisation of
   ``raw_rows`` for tamper detection.
5. Writes a self-contained JSON fixture per test case.

Honest absence (ADR 0008 §6 / TD #28b) is captured faithfully — slots
with zero realised releases write an error-envelope fixture rather
than an empty happy-path one.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Path setup so this script can be invoked directly.
_FIXTURES_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FIXTURES_DIR.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.inflation_swaps.tools.cpi_surprise import (  # noqa: E402
    CONFIG_PATH,
    CpiSurpriseInput,
    calculate_cpi_surprise,
)
from shared.analytics.events_fetch import fetch_economic_release_surprises  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


_CASES: list[dict] = [
    {
        "fixture_name": "us_cpi_24releases",
        "params": {"country": "US", "lookback_releases": 24},
    },
    {
        "fixture_name": "eu_hicp_18releases",
        "params": {"country": "EU", "lookback_releases": 18},
    },
    {
        "fixture_name": "uk_cpi_24releases",
        "params": {"country": "UK", "lookback_releases": 24},
    },
    {
        "fixture_name": "jp_cpi_18releases",
        "params": {"country": "JP", "lookback_releases": 18},
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
    """Canonical row dict for hashing / serialisation."""
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
    params = CpiSurpriseInput(**case["params"])
    cfg = load_tool_config(CONFIG_PATH)
    z_window = int(cfg.convention_value("release_z_window"))
    buffer_days = int(cfg.convention_value("release_fetch_buffer_days_per_release"))
    event_type = str(cfg.convention_value(f"cpi_event_type_for_{params.country.lower()}"))

    frozen_today = date.today()
    fetch_releases = params.lookback_releases + z_window
    buffer_calendar_days = fetch_releases * buffer_days
    window_start = frozen_today - timedelta(days=buffer_calendar_days)

    df = fetch_economic_release_surprises(
        engine=engine,
        event_type=event_type,
        country=params.country,
        window_start=window_start,
        window_end=frozen_today,
    )
    raw_rows = [_row_dict(record) for record in df.to_dict(orient="records")]

    expected_output = calculate_cpi_surprise(engine=engine, params=params)

    return {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.inflation_swaps.tools.cpi_surprise",
        "tool_function": "calculate_cpi_surprise",
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
                f"surprise={cm.get('current_surprise_pct')} "
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
