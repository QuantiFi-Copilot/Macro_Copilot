"""
_capture.py — Live-DB parity-fixture capture for otr_ofr_spread
================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``calculate_otr_ofr_spread`` for the documented
test cases.  The recorded fixtures lock in current behaviour byte-for-
byte; the parity test then replays them offline.

Why live data, not synthetic
----------------------------
The shipped v1 fixtures are synthetic (see README.md) because the OTR
resolver is forward-only (TD #27a) and the cash-bond playbook
(ADR 0005) is rolling out — captured fixtures at v1-land time would
either be empty or carry incomplete rolling-window warmup.

Once `otr_history` and per-CUSIP `YLD_YTM_MID` rows have full
coverage for the canonical regression slots, run this script to
replace the synthetic v1 fixtures with live-DB captures.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/otr_ofr_spread_v1/_capture.py

Required env vars (defaults match docker-compose.yml for a typical
local setup)::

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser
    DB_PASSWORD=...   DB_NAME=macrodata

The script:

1. Connects to the DB via ``database.database.get_db_engine``.
2. For each test case, runs the same OTR/OFR yield-pair query the
   primitive issues (via ``fetch_otr_ofr_yield_pair``) to record the
   exact rows the tool will see.
3. Calls ``calculate_otr_ofr_spread`` against the same DB to record
   the tool's full output.
4. Computes a SHA-256 hash of the canonical-JSON serialisation of
   ``raw_rows`` for tamper detection.
5. Writes a self-contained JSON fixture per test case.

Honest absence (TD #27a) is captured faithfully: if a slot has no
OTR/OFR rows in the live DB lookback, the fixture records the
primitive's error envelope as ``expected_output`` so the
honest-absence path is pinned by the same mechanism as the happy
path.

Determinism guarantee
---------------------
After fixtures are written, the parity test replays them via a
mocked fetcher + frozen date.  The DB is not touched again.  Re-
running the capture script produces slightly different fixtures
(the resolver may have observed new rolls or new daily yields); do
NOT regenerate casually — see ``README.md``'s regeneration policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
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
from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (  # noqa: E402
    CONFIG_PATH,
    OtrOfrSpreadInput,
    calculate_otr_ofr_spread,
)
from shared.analytics.rates_fetch import fetch_otr_ofr_yield_pair  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================

_CASES: list[dict] = [
    {
        "fixture_name": "us_10y_365d",
        "params": {"country": "US", "tenor": "10Y", "lookback_days": 365},
    },
    {
        "fixture_name": "us_2y_365d",
        "params": {"country": "US", "tenor": "2Y", "lookback_days": 365},
    },
    {
        "fixture_name": "de_10y_365d",
        "params": {"country": "DE", "tenor": "10Y", "lookback_days": 365},
    },
    {
        "fixture_name": "us_10y_180d",
        "params": {"country": "US", "tenor": "10Y", "lookback_days": 180},
    },
]


# ===========================================================================
# HELPERS
# ===========================================================================

def _row_dict(row: dict) -> dict:
    """Canonical row dict for hashing / serialisation."""
    return {
        "trade_date": str(row["trade_date"]),
        "otr_instrument_id": int(row["otr_instrument_id"]),
        "ofr_instrument_id": (
            int(row["ofr_instrument_id"])
            if row.get("ofr_instrument_id") is not None else None
        ),
        "otr_yield": (
            float(row["otr_yield"])
            if row.get("otr_yield") is not None else None
        ),
        "ofr_yield": (
            float(row["ofr_yield"])
            if row.get("ofr_yield") is not None else None
        ),
    }


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


# ===========================================================================
# CAPTURE
# ===========================================================================

def _capture_one(case: dict, engine, captured_at: str, db_name: str) -> dict:
    params = OtrOfrSpreadInput(**case["params"])
    cfg = load_tool_config(CONFIG_PATH)
    z_window_days = int(cfg.convention_value("z_score_window_days"))
    buffer_mult = float(cfg.convention_value("z_score_buffer_multiplier"))
    field_name = str(cfg.convention_value("default_field_name"))

    frozen_today = date.today()
    buffer_calendar_days = int(z_window_days * buffer_mult)
    window_start = frozen_today - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )
    window_end = frozen_today

    df = fetch_otr_ofr_yield_pair(
        engine=engine,
        country=params.country,
        tenor=params.tenor,
        field_name=field_name,
        window_start=window_start,
        window_end=window_end,
    )
    raw_rows = [
        _row_dict(record)
        for record in df.to_dict(orient="records")
    ]
    raw_rows_sha256 = _sha256_hex(_canonicalise(raw_rows))

    expected_output = calculate_otr_ofr_spread(engine=engine, params=params)
    # The primitive's error envelope is a valid honest-absence shape;
    # the parity test handles both branches.

    return {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.sovereign_bonds.tools.otr_ofr_spread",
        "tool_function": "calculate_otr_ofr_spread",
        "capture": {
            "captured_at": captured_at,
            "capture_method": "live_db_v1",
            "database_name": db_name,
            "frozen_today": frozen_today.isoformat(),
            "raw_rows_count": len(raw_rows),
            "raw_rows_sha256": raw_rows_sha256,
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
                f"current_spread_bps={cm.get('current_spread_bps')}  "
                f"current_z={cm.get('current_z_score')}"
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
