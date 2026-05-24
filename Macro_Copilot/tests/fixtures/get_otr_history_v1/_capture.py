"""
_capture.py — Live-DB parity-fixture capture for get_otr_history
=================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``get_otr_history`` for the documented test cases.
The recorded fixtures lock in current behaviour byte-for-byte; the
parity test then replays them offline.

Why live data, not synthetic
----------------------------
The fixture captures real otr_history rows the resolver has observed
forward-only since deployment (TD #27a).  Synthetic fixtures would
only freeze the primitive's mapping logic against fabricated SCD2
rows; real data exposes resolver-specific quirks (detection-date
effective_from, two-run confirmation bootstrap, etc.) that a
synthetic generator does not reproduce.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/get_otr_history_v1/_capture.py

Required env vars (defaults match docker-compose.yml for a typical
local setup)::

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser
    DB_PASSWORD=...   DB_NAME=macrodata

The script:

1. Connects to the DB via ``database.database.get_db_engine``.
2. For each test case, runs the same SCD2 + instrument_master JOIN
   the primitive issues to record the exact rows the tool will see.
3. Calls ``get_otr_history`` against the same DB to record the tool's
   full output.
4. Computes a SHA-256 hash of the canonical-JSON serialisation of
   ``raw_rows`` for tamper detection.
5. Writes a self-contained JSON fixture per test case.

Honest absence (TD #27a) is captured faithfully: if a slot has no
``otr_history`` rows in the live DB, the fixture is still written —
``raw_rows`` is the empty list, ``expected_output.transitions`` is the
empty list, and ``current_metrics`` identity fields are ``None``.
This matches the primitive's honest-absence shape and keeps the
parity fixture a faithful production snapshot.

Determinism guarantee
---------------------
After fixtures are written, the parity test replays them via a
mocked engine + frozen date.  The DB is not touched again.  Re-running
the capture script produces slightly different fixtures (the
resolver may have observed new rolls); do NOT regenerate casually —
see ``README.md``'s regeneration policy.
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

from sqlalchemy import text  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from rates_agent.sovereign_bonds.tools.get_otr_history import (  # noqa: E402
    OtrHistoryInput,
    get_otr_history,
)


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# Each case is one (country, tenor, lookback_days) tuple.  Cover the
# canonical desk slots — US 10Y, US 2Y, DE 10Y — across a 252-day
# default window plus one wider (730d) and one narrower (90d) window
# so the parity test pins both the boundary semantics and the
# typical-window case.

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
        "fixture_name": "us_10y_730d",
        "params": {"country": "US", "tenor": "10Y", "lookback_days": 730},
    },
    {
        "fixture_name": "us_10y_90d",
        "params": {"country": "US", "tenor": "10Y", "lookback_days": 90},
    },
]


# ===========================================================================
# CAPTURE SQL — independent reproduction of the primitive's fetcher
# ===========================================================================

_CAPTURE_SQL = text(
    """
    SELECT
        TO_CHAR(o.effective_from, 'YYYY-MM-DD') AS effective_from,
        TO_CHAR(o.effective_to,   'YYYY-MM-DD') AS effective_to,
        o.otr_instrument_id,
        i.cusip,
        i.isin,
        i.vendor_ticker,
        TO_CHAR(i.maturity_date,  'YYYY-MM-DD') AS maturity_date
    FROM macro_data.otr_history o
    JOIN macro_data.instrument_master i
      ON i.instrument_id = o.otr_instrument_id
    WHERE o.country = :country
      AND o.tenor   = :tenor
      AND daterange(
              o.effective_from,
              COALESCE(o.effective_to, 'infinity'::date),
              '[]'
          ) && daterange(:window_start, :window_end, '[]')
    ORDER BY o.effective_from ASC
    """
)


# ===========================================================================
# HELPERS
# ===========================================================================

def _row_dict(row: dict) -> dict:
    """Canonical row dict for hashing / serialisation."""
    return {
        "effective_from": str(row["effective_from"]),
        "effective_to": (
            str(row["effective_to"]) if row["effective_to"] is not None else None
        ),
        "otr_instrument_id": int(row["otr_instrument_id"]),
        "cusip": (
            str(row["cusip"]).strip() if row.get("cusip") else None
        ),
        "isin": (
            str(row["isin"]).strip() if row.get("isin") else None
        ),
        "vendor_ticker": (
            str(row["vendor_ticker"]).strip() if row.get("vendor_ticker") else None
        ),
        "maturity_date": (
            str(row["maturity_date"]) if row["maturity_date"] is not None else None
        ),
    }


def _canonicalise(rows: list[dict]) -> str:
    """Canonical JSON for hashing — sorted keys, no whitespace, UTF-8."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now_iso_seconds() -> str:
    """ISO-8601 UTC timestamp with second resolution and a trailing ``Z``."""
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
    params = OtrHistoryInput(**case["params"])
    frozen_today = date.today()
    from datetime import timedelta
    window_start = frozen_today - timedelta(days=params.lookback_days)
    window_end = frozen_today

    with engine.connect() as conn:
        rows = (
            conn.execute(
                _CAPTURE_SQL,
                {
                    "country": params.country,
                    "tenor": params.tenor,
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                },
            )
            .mappings()
            .all()
        )
    raw_rows = [_row_dict(dict(r)) for r in rows]
    raw_rows_sha256 = _sha256_hex(_canonicalise(raw_rows))

    expected_output = get_otr_history(engine=engine, params=params)

    # An ``error`` envelope at this layer would be a primitive bug,
    # not a data issue — raise loudly so the capture script does not
    # silently write a bad fixture.
    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool returned "
            f"error: {expected_output['error']}"
        )

    return {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.sovereign_bonds.tools.get_otr_history",
        "tool_function": "get_otr_history",
        "capture": {
            "captured_at": captured_at,
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
            print(f"  ✗ {case['fixture_name']:20s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        cm = fixture["expected_output"]["current_metrics"]
        current_cusip = cm.get("cusip", "—")
        print(
            f"  ✓ {case['fixture_name']:20s}  "
            f"rows={cap['raw_rows_count']:4d}  "
            f"frozen={cap['frozen_today']}  "
            f"current_otr={current_cusip}"
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
