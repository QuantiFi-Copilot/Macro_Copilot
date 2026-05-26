"""
_capture.py — Live-DB parity-fixture capture for
              calculate_scan_inflation_linkers_extremes
================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``calculate_scan_inflation_linkers_extremes``
for one or more representative scan invocations.  The recorded
fixture locks in the current behaviour byte-for-byte (modulo 1e-9
float tolerance) so subsequent commits that touch the linker
universe-scan math (directly, via a shared analytics primitive, or
via a YAML edit) can prove the math is unchanged.

Why live data, not synthetic
----------------------------
The point of a parity fixture is to **freeze the current real
tool output before any refactor begins.**  Synthetic data only
freezes the math against artificial inputs; real Bloomberg-shaped
linker universe data exposes idiosyncrasies (per-country linker
holiday patterns, days where one curve family reports
independently, NaN field values from the upstream feed, the
tenor-coverage asymmetry across linker markets) that synthetic
generators don't reproduce.

Usage
-----
From the project root, with TimescaleDB running and DB env vars
set::

    python tests/fixtures/inflation_linkers_scan_inflation_linkers_extremes_v1/_capture.py

Required env vars (defaults match docker-compose.yml so this Just
Works on a typical local setup)::

    DB_HOST=localhost  DB_PORT=5433  DB_USER=quantuser
    DB_PASSWORD=...    DB_NAME=macrodata

The script:

1. Connects to the DB via ``database.database.get_db_engine``.
2. For each case, calls ``fetch_scan_universe`` and
   ``fetch_scan_universe_reference`` to capture the exact long-
   format rows the tool's SQL fetchers return across the scan's
   resolved curve_families.
3. Calls ``calculate_scan_inflation_linkers_extremes`` (against
   the same DB) to record the tool's full output.
4. Computes a SHA-256 hash of the canonical-JSON serialisation of
   the two raw-row sets for tamper detection.
5. Writes a self-contained JSON fixture per case.

The captured ``frozen_today`` tracks the resolved as_of anchor
the tool saw (either the LLM-supplied ``as_of_date`` or, when
omitted, the wall-clock date — but the default fixture pins
``as_of_date`` explicitly so re-captures are deterministic).

Determinism guarantee
---------------------
After the fixtures are written, replaying them via
``test_scan_inflation_linkers_extremes_parity.py`` is fully
offline and deterministic — the DB is never touched again.  Re-
running this capture script will produce slightly different
fixtures (different ``captured_at``, possibly different rows if
the DB has new data), so do NOT regenerate casually.  See
``README.md`` for the regeneration policy.
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
from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (  # noqa: E402
    ScanInflationLinkersExtremesInput,
    calculate_scan_inflation_linkers_extremes,
)
from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes.compute import (  # noqa: E402
    CONFIG_PATH as _SCAN_CONFIG_PATH,
)
from shared.analytics.rates_fetch import (  # noqa: E402
    fetch_scan_universe,
    fetch_scan_universe_reference,
)
from shared.config import load_tool_config  # noqa: E402


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# One representative scan: the full default linker universe with
# min_abs_z=0 and top_n=5 so the fixture exercises the ranking,
# threshold, reference-attach, and disclosure-attach paths.  Add
# more entries here to pin additional curve-family subsets.
#
# ``as_of_date`` is pinned to 2026-04-08 — the latest live DB
# anchor at the time of the round-1 build.  This makes the
# captured ranking DETERMINISTIC across capture runs (re-running
# the capture on a different wall-clock day still produces the
# same numbers given the same DB state).

PINNED_ANCHOR_DATE = date(2026, 4, 8)

_CASES: list[dict] = [
    {
        "fixture_name": "full_universe_top5_zfloor",
        "params": {
            "curve_families": None,
            "top_n": 5,
            "min_abs_z_score": 0.0,
            "as_of_date": PINNED_ANCHOR_DATE.isoformat(),
        },
    },
]


# ===========================================================================
# HELPERS
# ===========================================================================

def _field_row_dict(
    trade_date: Any, curve_family: str, tenor: str,
    contract_code: Any, field_value: float,
) -> dict:
    """Render a single raw_field_rows entry in canonical form."""
    if isinstance(trade_date, str):
        td_str = trade_date
    elif hasattr(trade_date, "isoformat"):
        td_str = trade_date.isoformat()[:10]
    else:
        td_str = str(trade_date)
    return {
        "trade_date": td_str,
        "curve_family": curve_family,
        "tenor": tenor,
        "contract_code": (
            str(contract_code) if contract_code is not None else None
        ),
        "field_value": float(field_value),
    }


def _reference_row_dict(
    curve_family: str, tenor: str, contract_code: Any,
    maturity_date: Any, country: Any, vendor_ticker: Any,
) -> dict:
    """Render a single raw_reference_rows entry in canonical form."""
    if maturity_date is None:
        md = None
    elif isinstance(maturity_date, str):
        md = maturity_date
    elif hasattr(maturity_date, "isoformat"):
        md = maturity_date.isoformat()[:10]
    else:
        md = str(maturity_date)
    return {
        "curve_family": curve_family,
        "tenor": tenor,
        "contract_code": (
            str(contract_code) if contract_code is not None else None
        ),
        "maturity_date": md,
        "country": str(country) if country is not None else None,
        "vendor_ticker": (
            str(vendor_ticker) if vendor_ticker is not None else None
        ),
    }


def _canonicalise(rows: list[dict]) -> str:
    """Canonical JSON encoding for hashing."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_two(
    field_rows: list[dict],
    reference_rows: list[dict],
) -> str:
    """Hash the concatenated canonicalisation of the two row sets.

    The parity test must reproduce this combined hash exactly; a
    label prefix on each list disambiguates the streams so a swap
    of (e.g.) field ↔ reference rows changes the hash.
    """
    payload = (
        "FIELD|" + _canonicalise(field_rows)
        + "|REF|" + _canonicalise(reference_rows)
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
    whitelist_csv = cfg.convention_value("inflation_linker_curve_families")
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

    params = ScanInflationLinkersExtremesInput(**case["params"])

    cfg = load_tool_config(_SCAN_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    ffill_limit = cfg.convention_value("ffill_limit_days")
    field_name = cfg.convention_value("default_field_name")
    fetch_window_days = int(z_window * buffer_mult)

    # Anchor: ``as_of_date`` when supplied (deterministic), wall-clock
    # ``date.today()`` otherwise.  The fixture pins ``as_of_date`` so
    # the fetch_start below is reproducible across capture runs.
    requested_as_of = params.as_of_date
    fetch_anchor = (
        requested_as_of if requested_as_of is not None else date.today()
    )
    frozen_today = fetch_anchor
    fetch_start = fetch_anchor - timedelta(
        days=fetch_window_days + int(ffill_limit)
    )
    curve_families = _resolve_curve_families(params.curve_families)

    # 1. Capture the raw field rows.
    raw_field_df = fetch_scan_universe(
        engine=engine,
        instrument_type="inflation_linker",
        field_name=field_name,
        start_date=fetch_start,
        curve_families=curve_families,
    )
    if raw_field_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: "
            f"fetch_scan_universe returned 0 {field_name} rows. Check "
            "that the linker universe is loaded in the DB."
        )

    # 2. Capture the raw reference rows.
    raw_ref_df = fetch_scan_universe_reference(
        engine=engine,
        instrument_type="inflation_linker",
        curve_families=curve_families,
    )
    if raw_ref_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: "
            f"fetch_scan_universe_reference returned 0 rows. Check "
            "that the linker universe's instrument_master + view "
            "rows exist."
        )

    raw_field_rows = [
        _field_row_dict(
            r["trade_date"], r["curve_family"], r["tenor"],
            r["contract_code"], r["field_value"],
        )
        for _, r in raw_field_df.iterrows()
    ]
    raw_reference_rows = [
        _reference_row_dict(
            r["curve_family"], r["tenor"], r["contract_code"],
            r["maturity_date"], r["country"], r["vendor_ticker"],
        )
        for _, r in raw_ref_df.iterrows()
    ]
    # Sort canonically (by curve_family / tenor / date) so the hash
    # is order-independent across capture runs that re-shuffle row
    # order.
    raw_field_rows.sort(
        key=lambda r: (r["curve_family"], r["tenor"], r["trade_date"]),
    )
    raw_reference_rows.sort(
        key=lambda r: (
            r["curve_family"],
            r["tenor"],
            r["contract_code"] or "",
        ),
    )
    raw_rows_sha256 = _hash_two(raw_field_rows, raw_reference_rows)

    # 3. Run the tool against the same DB so expected_output reflects
    #    production behaviour.
    expected_output = calculate_scan_inflation_linkers_extremes(
        engine=engine, params=params,
    )
    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool returned "
            f"error: {expected_output['error']}"
        )

    fixture = {
        "fixture_name": case["fixture_name"],
        "tool_module": (
            "rates_agent.inflation_indexed_bonds.tools."
            "scan_inflation_linkers_extremes"
        ),
        "tool_function": "calculate_scan_inflation_linkers_extremes",
        "capture": {
            "captured_at": captured_at,
            "database_name": db_name,
            "raw_field_rows_count": len(raw_field_rows),
            "raw_reference_rows_count": len(raw_reference_rows),
            "raw_rows_sha256": raw_rows_sha256,
            "resolved_curve_families": curve_families,
        },
        "input": {
            "params": case["params"],
            "frozen_today": frozen_today.isoformat(),
            "raw_field_rows": raw_field_rows,
            "raw_reference_rows": raw_reference_rows,
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
            f"field_rows={cap['raw_field_rows_count']:5d}  "
            f"ref_rows={cap['raw_reference_rows_count']:3d}  "
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
