"""
_capture.py — Live-DB parity-fixture capture for
              calculate_cross_country_real_yield_spread_simple
=================================================================

Records the *real production* output of
``calculate_cross_country_real_yield_spread_simple`` for three
representative cross-country linker pairs (US-UK, US-FR, UK-FR).
PR15 backfill — modelled on
``tests/fixtures/real_yield_curve_spread_v1/_capture.py``; the only
shape differences are TWO identity-guard rows per scenario (one per
linker leg in the compose primitive's OWN compute module) and TWO
endpoint fetches in DIFFERENT curve_families instead of two tenors
on a single curve.

Compose-primitive capture pattern
---------------------------------
The primitive composes two calls to ``get_real_yield_level`` for
DIFFERENT linker curves and runs TWO identity-guard DB reads in its
OWN compute module (one per leg).  The capture records:

  - ``country_currency`` — TWO rows per scenario, one per
    ``(curve_family, instrument_type='inflation_linker')`` tuple.
  - ``raw_rows`` — TWO entries per scenario (one per curve_family,
    same shared ``tenor``).
  - ``expected_output`` — full primitive output.

The parity test patches THREE seams:

  - ``cross_country_real_yield_spread_simple.compute._fetch_curve_family_country_currency``
  - ``real_yield_level.compute.fetch_single_tenor``
  - ``real_yield_level.compute.date``

Usage::

    python tests/fixtures/cross_country_real_yield_spread_simple_v1/_capture.py
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
from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (  # noqa: E402
    CrossCountryRealYieldSpreadSimpleInput,
    calculate_cross_country_real_yield_spread_simple,
)
from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.compute import (  # noqa: E402
    CONFIG_PATH as _XCRY_CONFIG_PATH,
    _LINKER_INSTRUMENT_TYPE,
    _fetch_curve_family_country_currency,
)
from shared.analytics.rates_fetch import fetch_single_tenor  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


_CASES: list[dict] = [
    {
        "fixture_name": "usd_tips_gbp_linker_10y_365d",
        "params": {
            "first_curve_family": "USD_TIPS",
            "second_curve_family": "GBP_LINKER",
            "tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "usd_tips_eur_fr_linker_10y_365d",
        "params": {
            "first_curve_family": "USD_TIPS",
            "second_curve_family": "EUR_FR_LINKER",
            "tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "gbp_linker_eur_fr_linker_10y_730d",
        "params": {
            "first_curve_family": "GBP_LINKER",
            "second_curve_family": "EUR_FR_LINKER",
            "tenor": "10Y",
            "lookback_days": 730,
            "field_name": None,
        },
    },
]


def _series_key(*, curve_family, tenor, field_name, instrument_type) -> str:
    return f"{curve_family}__{tenor}__{field_name}__{instrument_type}"


def _country_key(*, curve_family, instrument_type) -> str:
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


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _utc_now_iso_seconds() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0)
        .isoformat().replace("+00:00", "Z")
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


def _capture_one(case: dict, engine, captured_at: str, db_name: str) -> dict:
    params = CrossCountryRealYieldSpreadSimpleInput(**case["params"])
    frozen_today = date.today()

    cfg = load_tool_config(_XCRY_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    trailing_window = cfg.convention_value("trailing_range_window_days")
    default_field_name = cfg.convention_value("default_field_name")

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

    # TWO identity-guard rows — one per linker leg.
    country_currency: dict[str, dict] = {}
    for cf in (params.first_curve_family, params.second_curve_family):
        country, currency, err = _fetch_curve_family_country_currency(
            engine, cf, _LINKER_INSTRUMENT_TYPE,
        )
        if err is not None:
            raise RuntimeError(
                f"Capture failed for {case['fixture_name']}: "
                f"identity lookup for ({cf!r}, "
                f"{_LINKER_INSTRUMENT_TYPE!r}) errored: {err}"
            )
        country_currency[
            _country_key(
                curve_family=cf, instrument_type=_LINKER_INSTRUMENT_TYPE,
            )
        ] = {"country": country, "currency": currency}

    # TWO endpoint fetches — one per linker leg, shared tenor.
    raw_rows: dict[str, list[dict]] = {}
    for cf in (params.first_curve_family, params.second_curve_family):
        df = fetch_single_tenor(
            engine=engine,
            curve_family=cf,
            tenor=params.tenor,
            field_name=field_name_resolved,
            start_date=fetch_start,
            instrument_type=_LINKER_INSTRUMENT_TYPE,
        )
        if df.empty:
            raise RuntimeError(
                f"Capture failed for {case['fixture_name']}: leg "
                f"{cf} returned 0 rows for {params.tenor}/"
                f"{field_name_resolved}."
            )
        rows = [
            _row_dict(r["trade_date"], r["field_value"])
            for _, r in df.iterrows()
        ]
        rows.sort(key=lambda r: r["trade_date"])
        raw_rows[
            _series_key(
                curve_family=cf,
                tenor=params.tenor,
                field_name=field_name_resolved,
                instrument_type=_LINKER_INSTRUMENT_TYPE,
            )
        ] = rows

    payload = (
        "CC|" + _canonicalise(country_currency)
        + "|ROWS|" + _canonicalise(raw_rows)
    )
    raw_rows_sha256 = _sha256_hex(payload)

    expected_output = calculate_cross_country_real_yield_spread_simple(
        engine=engine, params=params,
    )
    if "error" in expected_output:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool "
            f"returned error: {expected_output['error']}"
        )

    return {
        "fixture_name": case["fixture_name"],
        "tool_module": (
            "rates_agent.inflation_indexed_bonds.tools."
            "cross_country_real_yield_spread_simple"
        ),
        "tool_function": "calculate_cross_country_real_yield_spread_simple",
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
            print(f"  X {case['fixture_name']:38s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        m = fixture["expected_output"]["current_metrics"]
        ts = fixture["expected_output"]["time_series"]
        print(
            f"  OK {case['fixture_name']:38s}  "
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
            f"{failures} case(s) FAILED — fix the data issue and re-run."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
