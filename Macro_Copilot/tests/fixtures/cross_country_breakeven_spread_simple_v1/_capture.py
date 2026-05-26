"""
_capture.py — Live-DB parity-fixture capture for
              calculate_cross_country_breakeven_spread_simple
==================================================================

Records the *real production* output of
``calculate_cross_country_breakeven_spread_simple`` for three
cross-country breakeven differentials.  PR15 backfill — modelled on
``tests/fixtures/breakeven_curve_spread_v1/_capture.py``; the only
shape difference is that the two inner spot calls use DIFFERENT
(nominal, linker) pairs (one per country) at the SAME tenor, so we
end up with 4 unique country tuples and 4 unique fetches per
scenario (vs 2 unique country tuples and 4 unique fetches for
breakeven_curve_spread).

Compose-primitive seam topology — see
``tests/fixtures/breakeven_curve_spread_v1/README.md``.  All seams
on the INNER spot primitive's compute module:

  - ``breakeven_inflation_simple.compute.fetch_single_tenor``
  - ``breakeven_inflation_simple.compute._fetch_curve_family_country_currency``
  - ``breakeven_inflation_simple.compute.date``

Usage::

    python tests/fixtures/cross_country_breakeven_spread_simple_v1/_capture.py
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
from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (  # noqa: E402
    CrossCountryBreakevenSpreadSimpleInput,
    calculate_cross_country_breakeven_spread_simple,
)
from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.compute import (  # noqa: E402
    CONFIG_PATH as _XCBE_CONFIG_PATH,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute import (  # noqa: E402
    _LINKER_INSTRUMENT_TYPE,
    _NOMINAL_INSTRUMENT_TYPE,
    _fetch_curve_family_country_currency,
)
from shared.analytics.rates_fetch import fetch_single_tenor  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


_CASES: list[dict] = [
    {
        "fixture_name": "us_vs_uk_10y_365d",
        "params": {
            "country_a_nominal_pair": "UST",
            "country_a_linker_pair": "USD_TIPS",
            "country_b_nominal_pair": "UK_GILT",
            "country_b_linker_pair": "GBP_LINKER",
            "tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "us_vs_fr_5y_365d",
        "params": {
            "country_a_nominal_pair": "UST",
            "country_a_linker_pair": "USD_TIPS",
            "country_b_nominal_pair": "FR_OAT",
            "country_b_linker_pair": "EUR_FR_LINKER",
            "tenor": "5Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "uk_vs_fr_10y_730d",
        "params": {
            "country_a_nominal_pair": "UK_GILT",
            "country_a_linker_pair": "GBP_LINKER",
            "country_b_nominal_pair": "FR_OAT",
            "country_b_linker_pair": "EUR_FR_LINKER",
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
    params = CrossCountryBreakevenSpreadSimpleInput(**case["params"])
    frozen_today = date.today()

    cfg = load_tool_config(_XCBE_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    trailing_window = cfg.convention_value("trailing_range_window_days")
    default_field_name = cfg.convention_value("default_field_name")

    outer_buffer = int(max(z_window, trailing_window) * buffer_mult)
    inner_buffer = int(max(z_window, trailing_window) * buffer_mult)
    extended_lookback_days = params.lookback_days + outer_buffer
    fetch_start = frozen_today - timedelta(
        days=extended_lookback_days + inner_buffer
    )
    field_name_resolved = (
        params.field_name if params.field_name is not None
        else default_field_name
    )

    # FOUR identity-guard rows — one per (curve_family, instrument_type)
    # tuple across both country legs.
    legs = [
        (params.country_a_nominal_pair, _NOMINAL_INSTRUMENT_TYPE),
        (params.country_a_linker_pair, _LINKER_INSTRUMENT_TYPE),
        (params.country_b_nominal_pair, _NOMINAL_INSTRUMENT_TYPE),
        (params.country_b_linker_pair, _LINKER_INSTRUMENT_TYPE),
    ]
    country_currency: dict[str, dict] = {}
    for cf, it in legs:
        country, currency, err = _fetch_curve_family_country_currency(
            engine, cf, it,
        )
        if err is not None:
            raise RuntimeError(
                f"Capture failed for {case['fixture_name']}: identity "
                f"lookup for ({cf!r}, {it!r}) errored: {err}"
            )
        country_currency[
            _country_key(curve_family=cf, instrument_type=it)
        ] = {"country": country, "currency": currency}

    # FOUR fetches — same shared tenor across both pairs' (nominal,
    # linker) legs.
    raw_rows: dict[str, list[dict]] = {}
    for cf, it in legs:
        df = fetch_single_tenor(
            engine=engine, curve_family=cf, tenor=params.tenor,
            field_name=field_name_resolved, start_date=fetch_start,
            instrument_type=it,
        )
        if df.empty:
            raise RuntimeError(
                f"Capture failed for {case['fixture_name']}: "
                f"({cf}, {params.tenor}, {it}) returned 0 rows."
            )
        rows = [
            _row_dict(r["trade_date"], r["field_value"])
            for _, r in df.iterrows()
        ]
        rows.sort(key=lambda r: r["trade_date"])
        raw_rows[
            _series_key(
                curve_family=cf, tenor=params.tenor,
                field_name=field_name_resolved, instrument_type=it,
            )
        ] = rows

    payload = (
        "CC|" + _canonicalise(country_currency)
        + "|ROWS|" + _canonicalise(raw_rows)
    )
    raw_rows_sha256 = _sha256_hex(payload)

    expected_output = calculate_cross_country_breakeven_spread_simple(
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
            "cross_country_breakeven_spread_simple"
        ),
        "tool_function": "calculate_cross_country_breakeven_spread_simple",
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
            print(f"  X {case['fixture_name']:30s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        m = fixture["expected_output"]["current_metrics"]
        ts = fixture["expected_output"]["time_series"]
        print(
            f"  OK {case['fixture_name']:30s}  "
            f"rows={sum(cap['raw_rows_counts'].values()):5d}  "
            f"ts_rows={len(ts):4d}  "
            f"as_of={cap['as_of_date']}  "
            f"spread={m['current_spread_bps']:+.2f}bps  "
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
