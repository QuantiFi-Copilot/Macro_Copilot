"""
_capture.py — Live-DB parity-fixture capture for get_real_yield_level
======================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``get_real_yield_level`` for three representative
linker pillars.  The recorded fixtures lock in the current behaviour
byte-for-byte (modulo 1e-9 float tolerance) so subsequent commits that
touch the linker real-yield-level math (directly, via a shared analytics
primitive, or via a YAML edit) can prove the math is unchanged.

PR15 backfill — this primitive shipped before the parity-fixture
discipline was load-bearing.  See ``tests/fixtures/curve_spread_v1/``
for the canonical pattern this script mirrors.

Why live data, not synthetic
----------------------------
The point of a parity fixture is to **freeze the current real tool
output before any refactor begins.**  Synthetic data only freezes the
math against artificial inputs; real Bloomberg-shaped linker data
exposes idiosyncrasies (NaN field values, market-specific holiday
patterns, occasional duplicate rows, negative real yields across
post-2020 history) that synthetic generators don't reproduce.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/real_yield_level_v1/_capture.py

Required env vars (defaults match docker-compose.yml so this Just Works
on a typical local setup)::

    DB_HOST=localhost  DB_PORT=5433  DB_USER=quantuser
    DB_PASSWORD=...    DB_NAME=macrodata

The script:

1. Connects to the DB via ``database.database.get_db_engine``.
2. For each case, calls ``fetch_single_tenor`` directly with
   ``instrument_type='inflation_linker'`` to record the exact long-format
   rows the tool's SQL fetcher returns.
3. Calls ``get_real_yield_level`` (against the same DB) to record the
   tool's full output.
4. Computes a SHA-256 hash of the canonical-JSON serialisation of
   ``raw_rows`` for tamper detection.
5. Writes a self-contained JSON fixture per case.

The captured ``frozen_today`` is the wall-clock date at capture time,
which is what ``get_real_yield_level`` saw via ``date.today()``.  The
parity test patches ``date.today()`` back to that value during replay.

Determinism guarantee
---------------------
After the fixtures are written, replaying them via
``test_real_yield_level_parity.py`` is fully offline and deterministic
— the DB is never touched again.  Re-running this capture script will
produce slightly different fixtures (different ``captured_at``, possibly
different ``as_of_date`` if the DB has new data), so do NOT regenerate
casually.  See ``README.md`` for the regeneration policy.
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
from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (  # noqa: E402
    RealYieldLevelInput,
    get_real_yield_level,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute import (  # noqa: E402
    CONFIG_PATH as _REAL_YIELD_LEVEL_CONFIG_PATH,
    _LINKER_INSTRUMENT_TYPE,
)
from shared.analytics.rates_fetch import fetch_single_tenor  # noqa: E402
from shared.config import load_tool_config  # noqa: E402

# Note: as of the PR15 backfill, get_real_yield_level lives at
# .real_yield_level.compute (the package init re-exports it).  The
# capture script does not currently patch anything (it runs against
# the live DB), but if you ever add mocks here, target the COMPUTE
# module directly:
#
#   patch("rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor", ...)
#   patch("rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date", ...)
#
# Patching the package init's namespace would be a no-op because the
# imports we want to mock live inside compute.py.


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# Each case is one (curve_family, tenor, lookback_days) triple chosen to
# cover the three primary linker markets ingested in the playbook.
# ``lookback_days`` is the user-input window for the displayed series;
# the tool internally fetches lookback + ``z_score_window_days *
# z_score_buffer_multiplier`` (~378) calendar days of z-score buffer.

_CASES: list[dict] = [
    {
        "fixture_name": "usd_tips_10y_365d",
        "params": {
            "curve_family": "USD_TIPS",
            "tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,  # YAML default (YLD_YTM_MID)
        },
    },
    {
        "fixture_name": "gbp_linker_10y_730d",
        "params": {
            "curve_family": "GBP_LINKER",
            "tenor": "10Y",
            "lookback_days": 730,
            "field_name": None,
        },
    },
    {
        "fixture_name": "eur_fr_linker_5y_365d",
        "params": {
            "curve_family": "EUR_FR_LINKER",
            "tenor": "5Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
]


# ===========================================================================
# HELPERS
# ===========================================================================

def _row_dict(trade_date: Any, field_value: Any) -> dict:
    """Render a single raw_rows entry in canonical form.

    ``fetch_single_tenor`` returns only two columns
    (``trade_date``, ``field_value``) — no ``tenor`` column — so the
    canonical row shape is narrower than ``curve_spread``'s.

    ``trade_date`` may arrive from the DB as ``datetime.date``,
    ``pd.Timestamp``, or a string — coerce to ``YYYY-MM-DD`` for stable
    serialisation and stable hashing.

    ``field_value`` may be NaN on some rows (the upstream feed
    occasionally publishes NaN); preserve that as ``None`` in the
    canonical JSON so the replayed DataFrame reconstructs the same
    NaN cells via ``astype(float)``.
    """
    if isinstance(trade_date, str):
        td_str = trade_date
    elif hasattr(trade_date, "isoformat"):
        iso = trade_date.isoformat()
        td_str = iso[:10]
    else:
        td_str = str(trade_date)

    if field_value is None:
        fv: Any = None
    else:
        try:
            fv_float = float(field_value)
        except (TypeError, ValueError):
            fv = None
        else:
            fv = None if fv_float != fv_float else fv_float  # NaN check
    return {
        "trade_date": td_str,
        "field_value": fv,
    }


def _canonicalise(rows: list[dict]) -> str:
    """Canonical JSON encoding for hashing.

    Sorted keys, no whitespace, UTF-8.  Two semantically-identical row
    lists produce identical bytes regardless of insertion order of dict
    keys, which keeps the hash stable across pandas / Python minor
    versions.
    """
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _latest_trade_date(rows: list[dict]) -> str | None:
    if not rows:
        return None
    return max(r["trade_date"] for r in rows)


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
    """Run one case against the live DB and assemble the fixture dict."""
    params = RealYieldLevelInput(**case["params"])
    frozen_today = date.today()

    # 1. Capture the exact long-format rows the tool's fetcher returns
    #    for the same (start_date, instrument_type, field_name) window
    #    the tool will use.  Read the window/buffer values from the SAME
    #    config.yaml the tool will load so a YAML bump propagates to
    #    fixture regeneration automatically.
    cfg = load_tool_config(_REAL_YIELD_LEVEL_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    default_field_name = cfg.convention_value("default_field_name")
    buffer_days = int(z_window * buffer_mult)
    fetch_start = frozen_today - timedelta(
        days=params.lookback_days + buffer_days
    )
    field_name_resolved = (
        params.field_name if params.field_name is not None
        else default_field_name
    )
    raw_df = fetch_single_tenor(
        engine=engine,
        curve_family=params.curve_family,
        tenor=params.tenor,
        field_name=field_name_resolved,
        start_date=fetch_start,
        instrument_type=_LINKER_INSTRUMENT_TYPE,
    )

    if raw_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: fetch_single_tenor "
            f"returned 0 rows.  Check that {params.curve_family} "
            f"{params.tenor} {field_name_resolved} is loaded in the DB "
            f"with instrument_type='{_LINKER_INSTRUMENT_TYPE}'."
        )

    raw_rows = [
        _row_dict(r["trade_date"], r["field_value"])
        for _, r in raw_df.iterrows()
    ]
    # Sort for deterministic hashing and clean diffs.  Sorting by
    # trade_date alone is sufficient because fetch_single_tenor returns
    # exactly one row per trade_date for a single (curve_family, tenor,
    # field_name, instrument_type) tuple.
    raw_rows.sort(key=lambda r: r["trade_date"])

    raw_rows_sha256 = _sha256_hex(_canonicalise(raw_rows))

    # 2. Run the actual tool against the same DB so the captured
    #    expected_output reflects production behaviour, not a replay.
    expected_output = get_real_yield_level(engine=engine, params=params)

    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool returned "
            f"error: {expected_output['error']}"
        )

    fixture = {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.inflation_indexed_bonds.tools.real_yield_level",
        "tool_function": "get_real_yield_level",
        # Provenance — ignored by the parity test's deep comparator, but
        # the test DOES verify that ``raw_rows_sha256`` matches a
        # recompute of the loaded ``raw_rows`` (tamper detection).
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
            print(f"  X {case['fixture_name']:28s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        m = fixture["expected_output"]["current_metrics"]
        ts = fixture["expected_output"]["time_series"]["rows"]
        print(
            f"  OK {case['fixture_name']:28s}  "
            f"raw_rows={cap['raw_rows_count']:5d}  "
            f"ts_rows={len(ts):4d}  "
            f"as_of={cap['as_of_date']}  "
            f"real_yield={m['real_yield_pct']:+.4f}pct  "
            f"z={m.get('z_score')}"
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
