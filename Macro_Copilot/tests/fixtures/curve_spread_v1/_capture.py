"""
_capture.py — Live-DB parity-fixture capture for calculate_curve_spread
========================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``calculate_curve_spread`` for the three pilot
test cases.  The recorded fixtures lock in the current behaviour
byte-for-byte (modulo 1e-9 float tolerance) so subsequent commits in
the tool-config refactor pilot can prove the math is unchanged.

Why live data, not synthetic
----------------------------
The point of commit 0 is to **freeze the current real tool output before
the refactor begins.**  Synthetic data would only freeze the math against
artificial inputs that the refactor is unlikely to break in interesting
ways; real Bloomberg-shaped data exposes idiosyncrasies (NaN field
values, holiday patterns, occasional duplicate rows) that synthetic
generators don't reproduce.  See the agreed plan in the PR thread.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/curve_spread_v1/_capture.py

Required env vars (defaults match docker-compose.yml so this Just Works
on a typical local setup)::

    DB_HOST=localhost  DB_PORT=5433  DB_USER=quantuser
    DB_PASSWORD=...    DB_NAME=macrodata

The script:

1. Connects to the DB via ``database.database.get_db_engine``.
2. For each test case, calls ``fetch_tenor_pair`` directly to record the
   exact long-format rows the tool's SQL fetcher returns.
3. Calls ``calculate_curve_spread`` (against the same DB) to record the
   tool's full output.
4. Computes a SHA-256 hash of the canonical-JSON serialisation of
   ``raw_rows`` for tamper detection.
5. Writes a self-contained JSON fixture per test case.

The captured ``frozen_today`` is the wall-clock date at capture time,
which is what ``calculate_curve_spread`` saw via ``date.today()``.  The
parity test patches ``date.today()`` back to that value during replay.

Determinism guarantee
---------------------
After the fixtures are written, replaying them via
``test_curve_spread_parity.py`` is fully offline and deterministic — the
DB is never touched again.  Re-running this capture script will produce
slightly different fixtures (different ``captured_at``, possibly
different ``as_of_date`` if the DB has new data), so do NOT regenerate
casually.  See ``README.md`` for the regeneration policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Path setup so this script can be invoked directly without pytest's conftest.
# ---------------------------------------------------------------------------
_FIXTURES_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FIXTURES_DIR.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.sovereign_bonds.tools.curve_spread import calculate_curve_spread  # noqa: E402
from rates_agent.sovereign_bonds.tools.curve_spread.compute import (  # noqa: E402
    CONFIG_PATH as _CURVE_SPREAD_CONFIG_PATH,
)
from rates_agent.sovereign_bonds.tools.schemas import CurveSpreadInput  # noqa: E402
from shared.analytics.rates_fetch import fetch_tenor_pair  # noqa: E402
from shared.config import load_tool_config  # noqa: E402

# Note: as of commit 3 of the tool-config pilot, calculate_curve_spread
# lives at .curve_spread.compute (the package init re-exports it for
# backward compat).  The capture script does not currently patch
# anything (it runs against the live DB), but if you ever add mocks
# here, target the COMPUTE module directly:
#
#   patch("rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair", ...)
#   patch("rates_agent.sovereign_bonds.tools.curve_spread.compute.date", ...)
#
# Patching the package init's namespace would be a no-op because the
# imports we want to mock live inside compute.py.


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# Each case is one (curve_family, short_tenor, long_tenor, lookback_days)
# pair.  ``lookback_days`` is the user-input window for the displayed
# series; the tool internally fetches lookback + ~378 calendar days of
# z-score buffer.

_CASES: list[dict] = [
    {
        "fixture_name": "ust_2s10s_365d",
        "params": {
            "curve_family": "UST",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 365,
            "field_name": "YLD_YTM_MID",
        },
    },
    {
        "fixture_name": "bund_5s30s_90d",
        "params": {
            "curve_family": "DE_BUND",
            "short_tenor": "5Y",
            "long_tenor": "30Y",
            "lookback_days": 90,
            "field_name": "YLD_YTM_MID",
        },
    },
    {
        "fixture_name": "btp_2s10s_730d",
        "params": {
            "curve_family": "IT_BTP",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 730,
            "field_name": "YLD_YTM_MID",
        },
    },
]


# ===========================================================================
# HELPERS
# ===========================================================================

def _row_dict(trade_date: Any, tenor: str, field_value: float) -> dict:
    """Render a single raw_rows entry in canonical form.

    ``trade_date`` may arrive from the DB as ``datetime.date``,
    ``pd.Timestamp``, or a string — coerce to ``YYYY-MM-DD`` for stable
    serialisation and stable hashing.
    """
    if isinstance(trade_date, str):
        td_str = trade_date
    elif hasattr(trade_date, "isoformat"):
        # date or pd.Timestamp — both have isoformat returning YYYY-MM-DD prefix.
        iso = trade_date.isoformat()
        td_str = iso[:10]
    else:
        td_str = str(trade_date)
    return {
        "trade_date": td_str,
        "tenor": str(tenor),
        "field_value": float(field_value),
    }


def _canonicalise(rows: list[dict]) -> str:
    """Canonical JSON encoding for hashing.

    Sorted keys, no whitespace, UTF-8.  Two semantically-identical
    row lists produce identical bytes regardless of insertion order
    of dict keys, which keeps the hash stable across pandas / Python
    minor versions.
    """
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _latest_trade_date(rows: list[dict]) -> str | None:
    """Latest ``trade_date`` (string YYYY-MM-DD) across raw_rows.  Stable
    because we sort the row list on date before serialising."""
    if not rows:
        return None
    return max(r["trade_date"] for r in rows)


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
    """Run one case against the live DB and assemble the fixture dict."""
    params = CurveSpreadInput(**case["params"])
    frozen_today = date.today()  # wall-clock date the tool will see

    # 1. Capture the exact long-format rows the tool's fetcher returns
    #    for the same (start_date, end_date) window the tool will use.
    #    Read the window/buffer values from the SAME config.yaml the
    #    tool will load — single source of truth.  A bump to
    #    ``z_score_window_days`` or ``z_score_buffer_multiplier`` in
    #    YAML now propagates to fixture regeneration automatically;
    #    the previous version of this script hardcoded
    #    ``Z_SCORE_WINDOW * 1.5`` and would silently fall out of sync.
    from datetime import timedelta
    cs_config = load_tool_config(_CURVE_SPREAD_CONFIG_PATH)
    z_window = cs_config.convention_value("z_score_window_days")
    buffer_mult = cs_config.convention_value("z_score_buffer_multiplier")
    buffer_days = int(z_window * buffer_mult)
    fetch_start = frozen_today - timedelta(
        days=params.lookback_days + buffer_days
    )
    raw_df = fetch_tenor_pair(
        engine=engine,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        field_name=params.field_name,
        start_date=fetch_start,
    )

    if raw_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: fetch_tenor_pair "
            f"returned 0 rows.  Check that {params.curve_family} "
            f"{params.short_tenor}/{params.long_tenor} {params.field_name} "
            f"is loaded in the DB."
        )

    raw_rows = [
        _row_dict(r["trade_date"], r["tenor"], r["field_value"])
        for _, r in raw_df.iterrows()
    ]
    # Sort for deterministic hashing and clean diffs.
    raw_rows.sort(key=lambda r: (r["trade_date"], r["tenor"]))

    raw_rows_sha256 = _sha256_hex(_canonicalise(raw_rows))

    # 2. Run the actual tool against the same DB so the captured
    #    expected_output reflects production behaviour, not a replay.
    expected_output = calculate_curve_spread(engine=engine, params=params)

    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool returned "
            f"error: {expected_output['error']}"
        )

    fixture = {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.sovereign_bonds.tools.curve_spread",
        "tool_function": "calculate_curve_spread",
        # Provenance — ignored by the parity test's deep comparator, but
        # the test DOES verify that ``raw_rows_sha256`` matches a recompute
        # of the loaded ``raw_rows`` (tamper detection).
        "capture": {
            "captured_at": captured_at,
            "database_name": db_name,
            "as_of_date": _latest_trade_date(raw_rows),
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
            print(f"  ✗ {case['fixture_name']:24s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        m = fixture["expected_output"]["current_metrics"]
        ts = fixture["expected_output"]["time_series"]
        print(
            f"  ✓ {case['fixture_name']:24s}  "
            f"raw_rows={cap['raw_rows_count']:5d}  ts_rows={len(ts):4d}  "
            f"as_of={cap['as_of_date']}  "
            f"spread={m['current_spread_bps']:+.2f}bps  "
            f"z={m.get('current_z_score')}"
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
