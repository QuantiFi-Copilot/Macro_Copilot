"""
_capture.py — Live-DB parity-fixture capture for
              calculate_breakeven_inflation_simple
==================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``calculate_breakeven_inflation_simple`` for
three representative same-country nominal/linker pairs (US, UK,
France).  The recorded fixtures lock in the current behaviour
byte-for-byte (modulo 1e-9 float tolerance) so subsequent commits
that touch the spot breakeven math can prove the math is unchanged.

PR15 backfill — this primitive shipped before parity-fixture
discipline was load-bearing.  Modelled on
``tests/fixtures/curve_spread_v1/_capture.py``.

Why live data, not synthetic
----------------------------
Synthetic inputs only freeze the math against artificial data; real
Bloomberg-shaped nominal + linker pairs expose idiosyncrasies (per-
country holiday patterns, NaN field values, the linker-vs-nominal
date alignment that ``pivot_and_align_tenors`` has to handle, the
real-world (country, currency) tuples in ``instrument_master``) that
synthetic generators do not reproduce.

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/breakeven_inflation_simple_v1/_capture.py

Required env vars (defaults match docker-compose.yml so this Just
Works on a typical local setup)::

    DB_HOST=localhost  DB_PORT=5433  DB_USER=quantuser
    DB_PASSWORD=...    DB_NAME=macrodata

Per-scenario captures
---------------------
For each scenario the script records:

  - The ``(country, currency)`` row that
    ``_fetch_curve_family_country_currency`` returns for EACH of the
    two ``(curve_family, instrument_type)`` pairs the same-country
    invariant guard consults (nominal + linker).
  - The long-format rows that ``fetch_single_tenor`` returns for
    EACH of the two legs (nominal at the requested tenor under
    ``instrument_type='sovereign_benchmark'``; linker at the same
    tenor under ``instrument_type='inflation_linker'``).
  - The full ``calculate_breakeven_inflation_simple`` output.

The parity test replays these by patching the SAME three seams in
``breakeven_inflation_simple.compute`` (``fetch_single_tenor``,
``_fetch_curve_family_country_currency``, ``date``).

Determinism guarantee
---------------------
After the fixtures are written, replaying them via
``test_breakeven_inflation_simple_parity.py`` is fully offline and
deterministic — the DB is never touched again.  Re-running this
capture script will produce slightly different fixtures (different
``captured_at``, possibly different ``as_of_date`` if the DB has new
data), so do NOT regenerate casually.  See ``README.md`` for the
regeneration policy.
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
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (  # noqa: E402
    BreakevenInflationSimpleInput,
    calculate_breakeven_inflation_simple,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute import (  # noqa: E402
    CONFIG_PATH as _BREAKEVEN_CONFIG_PATH,
    _LINKER_INSTRUMENT_TYPE,
    _NOMINAL_INSTRUMENT_TYPE,
    _fetch_curve_family_country_currency,
)
from shared.analytics.rates_fetch import fetch_single_tenor  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# Three same-country pairs that exercise three distinct
# (country, currency) tuples and the three currency-distinct linker
# universes the playbook ingests.

_CASES: list[dict] = [
    {
        "fixture_name": "ust_usd_tips_10y_365d",
        "params": {
            "nominal_curve_family": "UST",
            "linker_curve_family": "USD_TIPS",
            "tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "uk_gilt_gbp_linker_10y_365d",
        "params": {
            "nominal_curve_family": "UK_GILT",
            "linker_curve_family": "GBP_LINKER",
            "tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "fr_oat_eur_fr_linker_5y_365d",
        "params": {
            "nominal_curve_family": "FR_OAT",
            "linker_curve_family": "EUR_FR_LINKER",
            "tenor": "5Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
]


# ===========================================================================
# HELPERS
# ===========================================================================

def _series_key(
    *, curve_family: str, tenor: str, field_name: str, instrument_type: str,
) -> str:
    """Canonical string key used in the captured ``raw_rows`` dict.

    Tuple keys would not JSON-serialise; this string mirrors the
    fetcher's call signature so the parity test's mock side_effect
    can route lookups deterministically by reconstructing the same
    key from kwargs.
    """
    return f"{curve_family}__{tenor}__{field_name}__{instrument_type}"


def _country_key(*, curve_family: str, instrument_type: str) -> str:
    return f"{curve_family}__{instrument_type}"


def _row_dict(trade_date: Any, field_value: Any) -> dict:
    """Render a single raw_rows entry in canonical form.

    ``fetch_single_tenor`` returns only ``[trade_date, field_value]``
    on this code path (the breakeven primitive passes neither
    ``contract_code`` nor any other disambiguator), so the canonical
    row shape is the same narrow two-column shape as the linker
    real_yield_level capture.
    """
    if isinstance(trade_date, str):
        td_str = trade_date
    elif hasattr(trade_date, "isoformat"):
        td_str = trade_date.isoformat()[:10]
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
    return {"trade_date": td_str, "field_value": fv}


def _canonicalise(obj: Any) -> str:
    """Canonical JSON encoding for hashing.  Sorted keys, compact
    separators, UTF-8 — stable across pandas / Python minor versions.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now_iso_seconds() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _latest_trade_date_across(raw_rows: dict[str, list[dict]]) -> str | None:
    latest = None
    for rows in raw_rows.values():
        if not rows:
            continue
        local = max(r["trade_date"] for r in rows)
        if latest is None or local > latest:
            latest = local
    return latest


# ===========================================================================
# CAPTURE
# ===========================================================================

def _capture_one(case: dict, engine, captured_at: str, db_name: str) -> dict:
    """Run one case against the live DB and assemble the fixture dict."""
    params = BreakevenInflationSimpleInput(**case["params"])
    frozen_today = date.today()

    cfg = load_tool_config(_BREAKEVEN_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    trailing_window = cfg.convention_value("trailing_range_window_days")
    default_field_name = cfg.convention_value("default_field_name")
    buffer_days = int(max(z_window, trailing_window) * buffer_mult)
    fetch_start = frozen_today - timedelta(
        days=params.lookback_days + buffer_days
    )
    field_name_resolved = (
        params.field_name if params.field_name is not None
        else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Capture the two country/currency rows the same-country guard
    #    will request.  Same order the production code consults them
    #    (nominal first, then linker — see
    #    breakeven_inflation_simple._enforce_same_country_invariant).
    # ------------------------------------------------------------------
    country_currency: dict[str, dict] = {}
    for cf, it in [
        (params.nominal_curve_family, _NOMINAL_INSTRUMENT_TYPE),
        (params.linker_curve_family, _LINKER_INSTRUMENT_TYPE),
    ]:
        country, currency, err = _fetch_curve_family_country_currency(
            engine, cf, it,
        )
        if err is not None:
            raise RuntimeError(
                f"Capture failed for {case['fixture_name']}: "
                f"identity lookup for ({cf!r}, {it!r}) errored: {err}"
            )
        country_currency[_country_key(curve_family=cf, instrument_type=it)] = {
            "country": country,
            "currency": currency,
        }

    # ------------------------------------------------------------------
    # 2. Capture both legs' raw_rows.  Order matches production: the
    #    linker leg is fetched FIRST (line 399 of compute.py), then
    #    the nominal leg (line 423).  The mock side_effect in the
    #    parity test routes by (curve_family, tenor, field_name,
    #    instrument_type) so the in-test ordering does not matter,
    #    but the capture preserves the production order for clarity.
    # ------------------------------------------------------------------
    raw_rows: dict[str, list[dict]] = {}

    linker_df = fetch_single_tenor(
        engine=engine,
        curve_family=params.linker_curve_family,
        tenor=params.tenor,
        field_name=field_name_resolved,
        start_date=fetch_start,
        instrument_type=_LINKER_INSTRUMENT_TYPE,
    )
    if linker_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: linker leg "
            f"({params.linker_curve_family} {params.tenor} "
            f"{field_name_resolved}) returned 0 rows."
        )
    linker_rows = [
        _row_dict(r["trade_date"], r["field_value"])
        for _, r in linker_df.iterrows()
    ]
    linker_rows.sort(key=lambda r: r["trade_date"])
    raw_rows[
        _series_key(
            curve_family=params.linker_curve_family,
            tenor=params.tenor,
            field_name=field_name_resolved,
            instrument_type=_LINKER_INSTRUMENT_TYPE,
        )
    ] = linker_rows

    nominal_df = fetch_single_tenor(
        engine=engine,
        curve_family=params.nominal_curve_family,
        tenor=params.tenor,
        field_name=field_name_resolved,
        start_date=fetch_start,
        instrument_type=_NOMINAL_INSTRUMENT_TYPE,
    )
    if nominal_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: nominal leg "
            f"({params.nominal_curve_family} {params.tenor} "
            f"{field_name_resolved}) returned 0 rows."
        )
    nominal_rows = [
        _row_dict(r["trade_date"], r["field_value"])
        for _, r in nominal_df.iterrows()
    ]
    nominal_rows.sort(key=lambda r: r["trade_date"])
    raw_rows[
        _series_key(
            curve_family=params.nominal_curve_family,
            tenor=params.tenor,
            field_name=field_name_resolved,
            instrument_type=_NOMINAL_INSTRUMENT_TYPE,
        )
    ] = nominal_rows

    # Tamper-detection hash covers BOTH the country_currency dict and
    # the raw_rows dict so the parity test catches any hand edit to
    # either provenance stream.
    payload = (
        "CC|" + _canonicalise(country_currency)
        + "|ROWS|" + _canonicalise(raw_rows)
    )
    raw_rows_sha256 = _sha256_hex(payload)

    # ------------------------------------------------------------------
    # 3. Run the actual tool against the same DB so expected_output
    #    reflects production behaviour, not a replay.
    # ------------------------------------------------------------------
    expected_output = calculate_breakeven_inflation_simple(
        engine=engine, params=params,
    )
    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool "
            f"returned error: {expected_output['error']}"
        )

    fixture = {
        "fixture_name": case["fixture_name"],
        "tool_module": (
            "rates_agent.inflation_indexed_bonds.tools."
            "breakeven_inflation_simple"
        ),
        "tool_function": "calculate_breakeven_inflation_simple",
        "capture": {
            "captured_at": captured_at,
            "database_name": db_name,
            "as_of_date": _latest_trade_date_across(raw_rows),
            "raw_rows_counts": {k: len(v) for k, v in raw_rows.items()},
            "raw_rows_sha256": raw_rows_sha256,
        },
        "input": {
            "params": case["params"],
            "frozen_today": frozen_today.isoformat(),
            "country_currency": country_currency,
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
            print(f"  X {case['fixture_name']:36s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        m = fixture["expected_output"]["current_metrics"]
        ts = fixture["expected_output"]["time_series"]
        print(
            f"  OK {case['fixture_name']:36s}  "
            f"rows={sum(cap['raw_rows_counts'].values()):5d}  "
            f"ts_rows={len(ts):4d}  "
            f"as_of={cap['as_of_date']}  "
            f"breakeven={m['breakeven_bps']:+.2f}bps  "
            f"z={m.get('current_z_score')}"
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
