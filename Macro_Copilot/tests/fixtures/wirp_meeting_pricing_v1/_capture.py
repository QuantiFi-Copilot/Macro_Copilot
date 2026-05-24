"""
_capture.py — Live-DB parity-fixture capture for wirp_meeting_pricing
=====================================================================

Captures the four central-bank cases (FOMC + ECB + BOE + BOJ) at
next_n_meetings=6 from the live DB.  Mirrors
cpi_surprise / nfp_surprise's live-capture pattern.

Usage:

    python tests/fixtures/wirp_meeting_pricing_v1/_capture.py
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
from rates_agent.ois.tools.wirp_meeting_pricing import (  # noqa: E402
    CONFIG_PATH,
    WirpMeetingPricingInput,
    calculate_wirp_meeting_pricing,
)
from shared.analytics.rates_fetch import fetch_wirp_meeting_snapshots  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


_CASES: list[dict] = [
    {
        "fixture_name": "fomc_next_6",
        "params": {
            "central_bank": "FOMC",
            "selection_mode": "next_n_meetings",
            "n_meetings": 6,
        },
    },
    {
        "fixture_name": "ecb_next_6",
        "params": {
            "central_bank": "ECB",
            "selection_mode": "next_n_meetings",
            "n_meetings": 6,
        },
    },
    {
        "fixture_name": "boe_next_6",
        "params": {
            "central_bank": "BOE",
            "selection_mode": "next_n_meetings",
            "n_meetings": 6,
        },
    },
    {
        "fixture_name": "boj_next_4",
        "params": {
            "central_bank": "BOJ",
            "selection_mode": "next_n_meetings",
            "n_meetings": 4,
        },
    },
]


def _row_dict(row: dict) -> dict:
    """Canonical row dict for hashing / serialisation."""
    return {
        "instrument_id": int(row["instrument_id"]),
        "vendor_ticker": str(row["vendor_ticker"]),
        "meeting_date": str(row["meeting_date"]),
        "central_bank": row.get("central_bank"),
        "meeting_token": row.get("meeting_token"),
        "bloomberg_ticker_fr": row.get("bloomberg_ticker_fr"),
        "bloomberg_ticker_pr": row.get("bloomberg_ticker_pr"),
        "bloomberg_ticker_nm": row.get("bloomberg_ticker_nm"),
        "bloomberg_ticker_ch": row.get("bloomberg_ticker_ch"),
        "field_name": str(row["field_name"]),
        "field_value": (
            float(row["field_value"]) if row.get("field_value") is not None else None
        ),
        "as_of_date": str(row["as_of_date"]),
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


def _capture_one(case: dict, engine, captured_at: str, db_name: str) -> dict:
    params = WirpMeetingPricingInput(**case["params"])

    frozen_today = date.today()
    # Mirror the compute layer's forward-window construction.
    earliest = frozen_today
    latest = frozen_today + timedelta(days=1500)

    df = fetch_wirp_meeting_snapshots(
        engine=engine,
        central_bank=params.central_bank,
        earliest_meeting_date=earliest,
        latest_meeting_date=latest,
    )
    raw_rows = [_row_dict(record) for record in df.to_dict(orient="records")]

    expected_output = calculate_wirp_meeting_pricing(
        engine=engine, params=params,
    )

    return {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.ois.tools.wirp_meeting_pricing",
        "tool_function": "calculate_wirp_meeting_pricing",
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
                f"next={cm.get('next_meeting_date')}  "
                f"rate={cm.get('next_implied_policy_rate_pct')}%  "
                f"hike/cut/hold={cm.get('next_hike_prob_pct')}/"
                f"{cm.get('next_cut_prob_pct')}/{cm.get('next_hold_prob_pct')}"
            )
        print(
            f"  ✓ {case['fixture_name']:30s}  "
            f"rows={cap['raw_rows_count']:4d}  {summary}"
        )
        successes += 1

    print(f"\nCaptured {successes}/{len(_CASES)} fixtures.")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
