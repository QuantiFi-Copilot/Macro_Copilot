"""
_capture.py — Live-DB parity-fixture capture for
              calculate_futures_price_level (policy_futures domain)
====================================================================

Records the *real* production output of
``calculate_futures_price_level`` for one representative policy-
futures strip-slot snapshot against the live TimescaleDB. The
recorded fixture locks the current behaviour byte-for-byte (modulo
1e-9 float tolerance) so subsequent commits that touch the math
(directly, via a shared analytics primitive, or via a YAML edit) can
prove the math is unchanged.

Why live data, not synthetic
----------------------------
The point of a parity fixture is to **freeze the current real tool
output before any refactor begins.** Synthetic data only freezes the
math against artificial inputs; real Bloomberg-shaped policy-futures
data exposes idiosyncrasies (holiday patterns differing across
markets, SCD2 metadata rotation across the rolling chain, ticks at
0.0025 vs 0.005 across the SFR strip) that synthetic generators
don't reproduce.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/policy_futures_futures_price_level_v1/_capture.py

The script:

1. Connects to the DB via ``database.database.get_db_engine``.
2. For the captured case, calls ``fetch_strip_position`` to record
   the exact long-format rows the tool's price fetcher returns AND
   ``fetch_strip_position_reference`` to record the per-strip
   metadata as of the pinned anchor date.
3. Calls ``calculate_futures_price_level`` (against the same DB) to
   record the tool's full output.
4. Computes a SHA-256 hash of the canonical-JSON serialisation of
   the raw rows + the reference dict for tamper detection.
5. Writes a self-contained JSON fixture per case.

Determinism guarantee
---------------------
After the fixtures are written, replaying them via
``test_policy_futures_futures_price_level_parity.py`` is fully
offline and deterministic — the DB is never touched again.
Re-running this capture script may produce slightly different
fixtures (different ``captured_at``, possibly different
``as_of_date`` if the DB has new data); see ``README.md`` for the
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
from rates_agent.policy_futures.tools.futures_price_level import (  # noqa: E402
    FuturesPriceLevelInput,
    calculate_futures_price_level,
)
from rates_agent.policy_futures.tools.futures_price_level.compute import (  # noqa: E402
    CONFIG_PATH as _PRICE_LEVEL_CONFIG_PATH,
)
from shared.analytics.rates_fetch import (  # noqa: E402
    fetch_strip_position,
    fetch_strip_position_reference,
)
from shared.config import load_tool_config  # noqa: E402


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# Pin the as_of_date to the policy_futures ingest as-of (2026-04-08)
# so the captured ranking is DETERMINISTIC across capture runs —
# re-running the capture on a different wall-clock day still produces
# the same numbers given the same DB state.
PINNED_ANCHOR_DATE = date(2026, 4, 8)

_CASES: list[dict] = [
    {
        "fixture_name": "full_universe_sfr1",
        "params": {
            "curve_family": "SOFR_FUT",
            "strip_position": 1,
            "lookback_days": 365,
            "as_of_date": PINNED_ANCHOR_DATE.isoformat(),
            "field_name": None,  # falls through to YAML default
        },
    },
]


# ===========================================================================
# HELPERS
# ===========================================================================

def _row_dict(
    trade_date: Any, field_value: float,
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
        "field_value": float(field_value),
    }


def _canonicalise(rows: list[dict]) -> str:
    """Canonical JSON encoding for hashing."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalise_reference(reference: dict) -> dict:
    """Coerce DB-driver types (Decimal, date) into JSON-safe scalars
    so the reference dict can be canonicalised and serialised. The
    Python primitive coerces these on entry (``float(...)``), so
    capturing them as floats here keeps the fixture's recorded
    reference numerically identical to what compute sees at runtime."""
    from decimal import Decimal
    out: dict[str, Any] = {}
    for k, v in reference.items():
        if isinstance(v, date):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _canonicalise_reference(reference: dict) -> str:
    """Stable canonicalisation of the per-strip reference dict for
    hashing. Dates are encoded as YYYY-MM-DD strings; Decimal coerced
    to float."""
    return json.dumps(
        _normalise_reference(reference),
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_payload(rows: list[dict], reference: dict) -> str:
    """Combined hash of price rows + reference dict so a swap of
    either stream changes the hash. Mirrors the bond_futures
    scan_bond_futures_extremes hash layering."""
    payload = (
        "PRICE|" + _canonicalise(rows)
        + "|REF|" + _canonicalise_reference(reference)
    )
    return _sha256_hex(payload)


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
    from datetime import timedelta

    params = FuturesPriceLevelInput(**case["params"])

    cfg = load_tool_config(_PRICE_LEVEL_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    price_field = cfg.convention_value("default_price_field")

    fetch_window_days = int(z_window * buffer_mult)
    fetch_anchor = params.as_of_date or date.today()
    frozen_today = fetch_anchor
    fetch_start = fetch_anchor - timedelta(
        days=params.lookback_days + fetch_window_days
    )

    # 1. Capture the raw price rows.
    raw_price_df = fetch_strip_position(
        engine=engine,
        curve_family=params.curve_family,
        strip_position=params.strip_position,
        field_name=price_field,
        start_date=fetch_start,
        end_date=fetch_anchor,
    )
    if raw_price_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: "
            f"fetch_strip_position returned 0 {price_field} rows."
        )

    raw_price_rows = [
        _row_dict(r["trade_date"], r["field_value"])
        for _, r in raw_price_df.iterrows()
    ]
    raw_price_rows.sort(key=lambda r: r["trade_date"])

    # 2. Capture the reference dict (per-strip metadata).
    reference = fetch_strip_position_reference(
        engine=engine,
        curve_family=params.curve_family,
        strip_position=params.strip_position,
        as_of_date=fetch_anchor,
    )
    if reference is None:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: "
            f"fetch_strip_position_reference returned None."
        )

    raw_rows_sha256 = _hash_payload(raw_price_rows, reference)

    # 3. Run the tool against the same DB so expected_output reflects
    #    production behaviour.
    expected_output = calculate_futures_price_level(
        engine=engine, params=params,
    )
    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool returned "
            f"error: {expected_output['error']}"
        )

    fixture = {
        "fixture_name": case["fixture_name"],
        "tool_module": "rates_agent.policy_futures.tools.futures_price_level",
        "tool_function": "calculate_futures_price_level",
        "capture": {
            "captured_at": captured_at,
            "database_name": db_name,
            "raw_price_rows_count": len(raw_price_rows),
            "raw_rows_sha256": raw_rows_sha256,
            "resolved_field_name": "PX_LAST",
        },
        "input": {
            "params": case["params"],
            "frozen_today": frozen_today.isoformat(),
            "raw_price_rows": raw_price_rows,
            "reference": _normalise_reference(reference),
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
        cm = fixture["expected_output"].get("current_metrics", {})
        print(
            f"  OK {case['fixture_name']:40s}  "
            f"px_rows={cap['raw_price_rows_count']:5d}  "
            f"as_of={cm.get('as_of_date', '?'):10s}  "
            f"raw_price={cm.get('raw_price', '?')}  "
            f"implied_rate={cm.get('implied_rate_pct', '?')}"
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
