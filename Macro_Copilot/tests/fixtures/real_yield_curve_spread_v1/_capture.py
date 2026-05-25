"""
_capture.py — Live-DB parity-fixture capture for
              calculate_real_yield_curve_spread
==================================================================

Run this script against the live TimescaleDB to record the *real*
production output of ``calculate_real_yield_curve_spread`` for three
representative linker curve points (USD_TIPS 5s10s, GBP_LINKER
2s10s, USD_TIPS 10s30s).  The recorded fixtures lock in the current
behaviour byte-for-byte (modulo 1e-9 float tolerance) so subsequent
commits that touch the real-yield curve-spread math (directly, via
``get_real_yield_level``, ``compute_level_metrics``, the bundled
``config.yaml``, or any shared primitive) can prove the math is
unchanged.

PR15 backfill — modelled on
``tests/fixtures/curve_spread_v1/_capture.py``.

Compose-primitive capture pattern
---------------------------------
This primitive composes two calls to ``get_real_yield_level`` and
runs one identity-guard DB read in its OWN compute module
(``_fetch_curve_family_country_currency``).  The capture script
therefore records THREE provenance streams:

  - ``country_currency`` — the single ``(country, currency)`` row
    the identity guard returns for
    ``(curve_family, instrument_type='inflation_linker')``.
  - ``raw_rows`` — keyed by
    ``"<curve_family>__<tenor>__<field_name>__<instrument_type>"``,
    one entry per inner endpoint call.  ``fetch_single_tenor``
    returns only ``[trade_date, field_value]`` for this code path
    (the inner level primitive passes ``contract_code=None``).
  - ``expected_output`` — the full primitive output from the live-
    DB run.

The parity test routes these by patching three seams:

  - ``real_yield_curve_spread.compute._fetch_curve_family_country_currency``
    (this primitive's OWN identity guard — 1 call per run).
  - ``real_yield_level.compute.fetch_single_tenor`` (the INNER
    level primitive's fetcher — 1 call per endpoint).
  - ``real_yield_level.compute.date`` (the INNER level primitive's
    only ``date.today()`` callsite).

Usage
-----
From the project root, with TimescaleDB running and DB env vars set::

    python tests/fixtures/real_yield_curve_spread_v1/_capture.py

Required env vars (defaults match docker-compose.yml)::

    DB_HOST=localhost  DB_PORT=5433  DB_USER=quantuser
    DB_PASSWORD=...    DB_NAME=macrodata

Determinism guarantee
---------------------
After the fixtures are written, replaying them via
``test_real_yield_curve_spread_parity.py`` is fully offline and
deterministic.  Re-running this capture script will produce slightly
different fixtures (different ``captured_at``, possibly different
``as_of_date`` if the DB has new data), so do NOT regenerate
casually.  See ``README.md``.
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
from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (  # noqa: E402
    RealYieldCurveSpreadInput,
    calculate_real_yield_curve_spread,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute import (  # noqa: E402
    CONFIG_PATH as _RYCS_CONFIG_PATH,
    _LINKER_INSTRUMENT_TYPE,
    _fetch_curve_family_country_currency,
)
from shared.analytics.rates_fetch import fetch_single_tenor  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# Three same-country real-yield curve-spread cases that exercise
# three distinct linker curves and three distinct tenor-pair shapes.

_CASES: list[dict] = [
    {
        "fixture_name": "usd_tips_5s10s_365d",
        "params": {
            "curve_family": "USD_TIPS",
            "short_tenor": "5Y",
            "long_tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "gbp_linker_2s10s_730d",
        "params": {
            "curve_family": "GBP_LINKER",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 730,
            "field_name": None,
        },
    },
    {
        "fixture_name": "usd_tips_10s30s_365d",
        "params": {
            "curve_family": "USD_TIPS",
            "short_tenor": "10Y",
            "long_tenor": "30Y",
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
    return f"{curve_family}__{tenor}__{field_name}__{instrument_type}"


def _country_key(*, curve_family: str, instrument_type: str) -> str:
    return f"{curve_family}__{instrument_type}"


def _row_dict(trade_date: Any, field_value: Any) -> dict:
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
            fv = None if fv_float != fv_float else fv_float
    return {"trade_date": td_str, "field_value": fv}


def _canonicalise(obj: Any) -> str:
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
    params = RealYieldCurveSpreadInput(**case["params"])
    frozen_today = date.today()

    # Read the SAME conventions the inner level primitive will read
    # via this compose primitive's config (cross-config lint enforces
    # the values match the inner level primitive's bundled YAML).
    cfg = load_tool_config(_RYCS_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    trailing_window = cfg.convention_value("trailing_range_window_days")
    default_field_name = cfg.convention_value("default_field_name")

    # Match the compose primitive's extended-inner-lookback math
    # exactly: it passes ``lookback_days = params.lookback_days +
    # buffer_calendar_days`` to each inner level call, where
    # buffer_calendar_days = int(max(z_window, trailing_window) *
    # buffer_multiplier).  The inner level primitive then ADDS its
    # own buffer (z_window * buffer_multiplier) on top of that
    # extended lookback when computing fetch_start.  So the
    # production fetch_start is:
    #
    #   today - (params.lookback_days + outer_buffer + inner_buffer)
    #
    # We mirror that exactly so the captured raw_rows match the
    # production fetch window byte-for-byte.
    outer_buffer = int(max(z_window, trailing_window) * buffer_mult)
    inner_buffer = int(z_window * buffer_mult)
    extended_lookback_days = params.lookback_days + outer_buffer
    fetch_start = frozen_today - timedelta(
        days=extended_lookback_days + inner_buffer
    )
    field_name_resolved = (
        params.field_name if params.field_name is not None
        else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Identity guard — ONE row per scenario (single curve_family).
    # ------------------------------------------------------------------
    country_currency: dict[str, dict] = {}
    country, currency, err = _fetch_curve_family_country_currency(
        engine, params.curve_family, _LINKER_INSTRUMENT_TYPE,
    )
    if err is not None:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: identity "
            f"lookup for ({params.curve_family!r}, "
            f"{_LINKER_INSTRUMENT_TYPE!r}) errored: {err}"
        )
    country_currency[
        _country_key(
            curve_family=params.curve_family,
            instrument_type=_LINKER_INSTRUMENT_TYPE,
        )
    ] = {"country": country, "currency": currency}

    # ------------------------------------------------------------------
    # 2. Endpoint raw_rows — one entry per (short, long) tenor.
    # ------------------------------------------------------------------
    raw_rows: dict[str, list[dict]] = {}
    for tenor in (params.short_tenor, params.long_tenor):
        df = fetch_single_tenor(
            engine=engine,
            curve_family=params.curve_family,
            tenor=tenor,
            field_name=field_name_resolved,
            start_date=fetch_start,
            instrument_type=_LINKER_INSTRUMENT_TYPE,
        )
        if df.empty:
            raise RuntimeError(
                f"Capture failed for {case['fixture_name']}: endpoint "
                f"{tenor} returned 0 rows for "
                f"{params.curve_family}/{field_name_resolved}."
            )
        rows = [
            _row_dict(r["trade_date"], r["field_value"])
            for _, r in df.iterrows()
        ]
        rows.sort(key=lambda r: r["trade_date"])
        raw_rows[
            _series_key(
                curve_family=params.curve_family,
                tenor=tenor,
                field_name=field_name_resolved,
                instrument_type=_LINKER_INSTRUMENT_TYPE,
            )
        ] = rows

    payload = (
        "CC|" + _canonicalise(country_currency)
        + "|ROWS|" + _canonicalise(raw_rows)
    )
    raw_rows_sha256 = _sha256_hex(payload)

    # ------------------------------------------------------------------
    # 3. Run the actual tool against the same DB.
    # ------------------------------------------------------------------
    expected_output = calculate_real_yield_curve_spread(
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
            "real_yield_curve_spread"
        ),
        "tool_function": "calculate_real_yield_curve_spread",
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
            print(f"  X {case['fixture_name']:28s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        m = fixture["expected_output"]["current_metrics"]
        ts = fixture["expected_output"]["time_series"]
        print(
            f"  OK {case['fixture_name']:28s}  "
            f"rows={sum(cap['raw_rows_counts'].values()):5d}  "
            f"ts_rows={len(ts):4d}  "
            f"as_of={cap['as_of_date']}  "
            f"spread={m['current_spread_pct']:+.4f}pct  "
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
