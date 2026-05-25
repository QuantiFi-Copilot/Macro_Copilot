"""
_capture.py — Live-DB parity-fixture capture for
              calculate_cross_market_inflation_swap_spread
==================================================================

PR15 backfill — modelled on
``tests/fixtures/inflation_swap_curve_spread_v1/_capture.py``; the
only shape difference is that the two inner level calls use
DIFFERENT ZCIS ``curve_family`` values at the SAME shared tenor.

Compose-primitive seam topology — see
``tests/fixtures/inflation_swap_curve_spread_v1/README.md``.  All
seams on the INNER level primitive's compute module:

  - ``inflation_swap_rate_level.compute.fetch_zcis_single_pillar``
  - ``inflation_swap_rate_level.compute.date``

Usage::

    python tests/fixtures/cross_market_inflation_swap_spread_v1/_capture.py
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
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (  # noqa: E402
    CrossMarketInflationSwapSpreadInput,
    calculate_cross_market_inflation_swap_spread,
)
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.compute import (  # noqa: E402
    CONFIG_PATH as _XCIS_CONFIG_PATH,
)
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute import (  # noqa: E402
    fetch_zcis_single_pillar,
)
from shared.config import load_tool_config  # noqa: E402


_CASES: list[dict] = [
    {
        "fixture_name": "usd_zcis_eur_zcis_5y_365d",
        "params": {
            "leg_a_curve_family": "USD_ZCIS",
            "leg_b_curve_family": "EUR_ZCIS",
            "tenor": "5Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "usd_zcis_gbp_zcis_10y_365d",
        "params": {
            "leg_a_curve_family": "USD_ZCIS",
            "leg_b_curve_family": "GBP_ZCIS",
            "tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "eur_zcis_gbp_zcis_5y_730d",
        "params": {
            "leg_a_curve_family": "EUR_ZCIS",
            "leg_b_curve_family": "GBP_ZCIS",
            "tenor": "5Y",
            "lookback_days": 730,
            "field_name": None,
        },
    },
]


def _series_key(*, curve_family, tenor, field_name) -> str:
    return f"{curve_family}__{tenor}__{field_name}"


def _zcis_row_dict(
    trade_date, field_value, vendor_ticker, pricing_type,
    inflation_index_family, index_lag, interpolation, underlying_index,
) -> dict:
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

    def _opt_str(v: Any) -> Any:
        return None if v is None else str(v)

    return {
        "trade_date": td_str,
        "field_value": fv,
        "vendor_ticker": _opt_str(vendor_ticker),
        "pricing_type": _opt_str(pricing_type),
        "inflation_index_family": _opt_str(inflation_index_family),
        "index_lag": _opt_str(index_lag),
        "interpolation": _opt_str(interpolation),
        "underlying_index": _opt_str(underlying_index),
    }


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
    params = CrossMarketInflationSwapSpreadInput(**case["params"])
    frozen_today = date.today()

    cfg = load_tool_config(_XCIS_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    trailing_window = cfg.convention_value("trailing_range_window_days")
    default_field_name = cfg.convention_value("default_zcis_rate_field")

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

    raw_rows: dict[str, list[dict]] = {}
    for cf in (params.leg_a_curve_family, params.leg_b_curve_family):
        df = fetch_zcis_single_pillar(
            engine=engine, curve_family=cf, tenor=params.tenor,
            field_name=field_name_resolved, start_date=fetch_start,
        )
        if df.empty:
            raise RuntimeError(
                f"Capture failed for {case['fixture_name']}: leg "
                f"{cf} returned 0 rows for {params.tenor}."
            )
        rows = [
            _zcis_row_dict(
                r["trade_date"], r["field_value"], r["vendor_ticker"],
                r["pricing_type"], r["inflation_index_family"],
                r["index_lag"], r["interpolation"], r["underlying_index"],
            )
            for _, r in df.iterrows()
        ]
        rows.sort(key=lambda r: r["trade_date"])
        raw_rows[
            _series_key(
                curve_family=cf, tenor=params.tenor,
                field_name=field_name_resolved,
            )
        ] = rows

    raw_rows_sha256 = _sha256_hex(_canonicalise(raw_rows))

    expected_output = calculate_cross_market_inflation_swap_spread(
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
            "rates_agent.inflation_swaps.tools."
            "cross_market_inflation_swap_spread"
        ),
        "tool_function": "calculate_cross_market_inflation_swap_spread",
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
            f"spread={m['spread_bps']:+.2f}bps  "
            f"z={m.get('z_score_252d')}"
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
