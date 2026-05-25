"""
_capture.py — Live-DB parity-fixture capture for
              calculate_swap_breakeven_basis_simple
==================================================================

PR15 backfill — closes the inflation-primitives backfill batch
(15/15 covered after this lands).  This is the only stage-3
primitive whose compose path crosses domain boundaries: it
combines a ZCIS leg (``calculate_inflation_swap_rate_level``)
with a breakeven leg (``calculate_breakeven_inflation_simple``)
to produce the swap-vs-breakeven basis.

Cross-domain capture pattern
----------------------------
The capture records THREE provenance streams:

  - ``raw_rows_zcis`` — keyed by
    ``"<zcis_curve_family>__<tenor>__<field_name>"``, the
    EIGHT-column long-format frame
    ``fetch_zcis_single_pillar`` returns for the ZCIS leg.

  - ``raw_rows_breakeven`` — keyed by
    ``"<curve_family>__<tenor>__<field_name>__<instrument_type>"``,
    the TWO-column long-format frame ``fetch_single_tenor``
    returns for each of the breakeven primitive's nominal + linker
    legs (2 entries per scenario).

  - ``country_currency`` — keyed by
    ``"<curve_family>__<instrument_type>"``, the
    ``(country, currency)`` row the breakeven primitive's
    ``_fetch_curve_family_country_currency`` identity guard
    returns for each leg (2 entries per scenario).

The parity test patches FIVE seams across TWO modules:

  - ``inflation_swap_rate_level.compute.fetch_zcis_single_pillar``
  - ``inflation_swap_rate_level.compute.date``
  - ``breakeven_inflation_simple.compute.fetch_single_tenor``
  - ``breakeven_inflation_simple.compute._fetch_curve_family_country_currency``
  - ``breakeven_inflation_simple.compute.date``

Two distinct ``date`` seams are patched because the compose
primitive itself does not import ``date`` — the fetch-start logic
lives independently inside each inner primitive.

Usage::

    python tests/fixtures/swap_breakeven_basis_simple_v1/_capture.py
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
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute import (  # noqa: E402
    _LINKER_INSTRUMENT_TYPE,
    _NOMINAL_INSTRUMENT_TYPE,
    _fetch_curve_family_country_currency,
)
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute import (  # noqa: E402
    fetch_zcis_single_pillar,
)
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (  # noqa: E402
    SwapBreakevenBasisSimpleInput,
    calculate_swap_breakeven_basis_simple,
)
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.compute import (  # noqa: E402
    BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH,
    CONFIG_PATH as _SBBS_CONFIG_PATH,
)
from shared.analytics.rates_fetch import fetch_single_tenor  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


_CASES: list[dict] = [
    {
        "fixture_name": "usd_zcis_ust_usd_tips_10y_365d",
        "params": {
            "zcis_curve_family": "USD_ZCIS",
            "nominal_curve_family": "UST",
            "linker_curve_family": "USD_TIPS",
            "tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "eur_zcis_fr_oat_eur_fr_linker_5y_365d",
        "params": {
            "zcis_curve_family": "EUR_ZCIS",
            "nominal_curve_family": "FR_OAT",
            "linker_curve_family": "EUR_FR_LINKER",
            "tenor": "5Y",
            "lookback_days": 365,
            "field_name": None,
        },
    },
    {
        "fixture_name": "gbp_zcis_uk_gilt_gbp_linker_10y_730d",
        "params": {
            "zcis_curve_family": "GBP_ZCIS",
            "nominal_curve_family": "UK_GILT",
            "linker_curve_family": "GBP_LINKER",
            "tenor": "10Y",
            "lookback_days": 730,
            "field_name": None,
        },
    },
]


def _zcis_key(*, curve_family, tenor, field_name) -> str:
    return f"{curve_family}__{tenor}__{field_name}"


def _be_series_key(
    *, curve_family, tenor, field_name, instrument_type,
) -> str:
    return f"{curve_family}__{tenor}__{field_name}__{instrument_type}"


def _country_key(*, curve_family, instrument_type) -> str:
    return f"{curve_family}__{instrument_type}"


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


def _be_row_dict(trade_date, field_value) -> dict:
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


def _latest_across_all(
    raw_zcis: dict[str, list[dict]],
    raw_be: dict[str, list[dict]],
) -> str | None:
    latest = None
    for src in (raw_zcis, raw_be):
        for rows in src.values():
            if not rows:
                continue
            local = max(r["trade_date"] for r in rows)
            if latest is None or local > latest:
                latest = local
    return latest


def _capture_one(case: dict, engine, captured_at: str, db_name: str) -> dict:
    params = SwapBreakevenBasisSimpleInput(**case["params"])
    frozen_today = date.today()

    # Read this primitive's config (the ZCIS leg uses it; the
    # breakeven leg uses its OWN bundled config — replicates the
    # production behaviour at line 399 of compute.py).
    cfg = load_tool_config(_SBBS_CONFIG_PATH)
    z_window = cfg.convention_value("z_score_window_days")
    buffer_mult = cfg.convention_value("z_score_buffer_multiplier")
    trailing_window = cfg.convention_value("trailing_range_window_days")
    default_field_name = cfg.convention_value("default_zcis_rate_field")

    # The basis compose primitive computes:
    #   extended_lookback = lookback_days + outer_buffer
    # ... then both inner primitives apply their own buffer when
    # computing fetch_start internally.  ZCIS inner buffer uses
    # z_window * buffer_multiplier; breakeven inner buffer uses
    # max(z_window, trailing_window) * buffer_multiplier.  Since
    # z_window == trailing_window == 252 across all bundled YAMLs
    # the two buffer values are identical, so we use one fetch_start
    # value for both legs.
    outer_buffer = int(max(z_window, trailing_window) * buffer_mult)
    inner_buffer_zcis = int(z_window * buffer_mult)
    inner_buffer_be = int(max(z_window, trailing_window) * buffer_mult)
    extended_lookback_days = params.lookback_days + outer_buffer
    fetch_start_zcis = frozen_today - timedelta(
        days=extended_lookback_days + inner_buffer_zcis
    )
    fetch_start_be = frozen_today - timedelta(
        days=extended_lookback_days + inner_buffer_be
    )

    # ZCIS field_name resolution.  ``params.field_name`` is threaded
    # to both inner calls; for ZCIS, None falls through to
    # default_zcis_rate_field (this primitive's config matches the
    # ZCIS level primitive's default).
    field_name_resolved_zcis = (
        params.field_name if params.field_name is not None
        else default_field_name
    )

    # Breakeven field_name resolution.  None falls through to the
    # BREAKEVEN primitive's own default_field_name (YLD_YTM_MID).
    breakeven_cfg = load_tool_config(BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH)
    breakeven_default_field = breakeven_cfg.convention_value(
        "default_field_name",
    )
    field_name_resolved_be = (
        params.field_name if params.field_name is not None
        else breakeven_default_field
    )

    # ------------------------------------------------------------------
    # 1. ZCIS leg — single 8-column row stream.
    # ------------------------------------------------------------------
    raw_rows_zcis: dict[str, list[dict]] = {}
    zcis_df = fetch_zcis_single_pillar(
        engine=engine,
        curve_family=params.zcis_curve_family,
        tenor=params.tenor,
        field_name=field_name_resolved_zcis,
        start_date=fetch_start_zcis,
    )
    if zcis_df.empty:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: ZCIS leg "
            f"({params.zcis_curve_family} {params.tenor}) returned 0 rows."
        )
    zcis_rows = [
        _zcis_row_dict(
            r["trade_date"], r["field_value"], r["vendor_ticker"],
            r["pricing_type"], r["inflation_index_family"],
            r["index_lag"], r["interpolation"], r["underlying_index"],
        )
        for _, r in zcis_df.iterrows()
    ]
    zcis_rows.sort(key=lambda r: r["trade_date"])
    raw_rows_zcis[
        _zcis_key(
            curve_family=params.zcis_curve_family,
            tenor=params.tenor,
            field_name=field_name_resolved_zcis,
        )
    ] = zcis_rows

    # ------------------------------------------------------------------
    # 2. Breakeven leg — 2 country_currency entries + 2 fetch entries.
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
        country_currency[
            _country_key(curve_family=cf, instrument_type=it)
        ] = {"country": country, "currency": currency}

    raw_rows_breakeven: dict[str, list[dict]] = {}
    for cf, it in [
        (params.linker_curve_family, _LINKER_INSTRUMENT_TYPE),
        (params.nominal_curve_family, _NOMINAL_INSTRUMENT_TYPE),
    ]:
        df = fetch_single_tenor(
            engine=engine, curve_family=cf, tenor=params.tenor,
            field_name=field_name_resolved_be, start_date=fetch_start_be,
            instrument_type=it,
        )
        if df.empty:
            raise RuntimeError(
                f"Capture failed for {case['fixture_name']}: breakeven "
                f"leg ({cf}, {params.tenor}, {it}) returned 0 rows."
            )
        rows = [
            _be_row_dict(r["trade_date"], r["field_value"])
            for _, r in df.iterrows()
        ]
        rows.sort(key=lambda r: r["trade_date"])
        raw_rows_breakeven[
            _be_series_key(
                curve_family=cf, tenor=params.tenor,
                field_name=field_name_resolved_be, instrument_type=it,
            )
        ] = rows

    # Combined tamper-detection hash covers ALL THREE provenance streams.
    payload = (
        "ZCIS|" + _canonicalise(raw_rows_zcis)
        + "|BE|" + _canonicalise(raw_rows_breakeven)
        + "|CC|" + _canonicalise(country_currency)
    )
    raw_rows_sha256 = _sha256_hex(payload)

    expected_output = calculate_swap_breakeven_basis_simple(
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
            "swap_breakeven_basis_simple"
        ),
        "tool_function": "calculate_swap_breakeven_basis_simple",
        "capture": {
            "captured_at": captured_at,
            "database_name": db_name,
            "as_of_date": _latest_across_all(
                raw_rows_zcis, raw_rows_breakeven,
            ),
            "raw_rows_counts": {
                **{f"zcis::{k}": len(v) for k, v in raw_rows_zcis.items()},
                **{
                    f"breakeven::{k}": len(v)
                    for k, v in raw_rows_breakeven.items()
                },
            },
            "raw_rows_sha256": raw_rows_sha256,
        },
        "input": {
            "params": case["params"],
            "frozen_today": frozen_today.isoformat(),
            "country_currency": country_currency,
            "raw_rows_zcis": raw_rows_zcis,
            "raw_rows_breakeven": raw_rows_breakeven,
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
            print(f"  X {case['fixture_name']:40s}  FAILED: {exc}")
            continue

        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)

        cap = fixture["capture"]
        m = fixture["expected_output"]["current_metrics"]
        ts = fixture["expected_output"]["time_series"]
        print(
            f"  OK {case['fixture_name']:40s}  "
            f"rows={sum(cap['raw_rows_counts'].values()):5d}  "
            f"ts_rows={len(ts):4d}  "
            f"as_of={cap['as_of_date']}  "
            f"basis={m['basis_bps']:+.2f}bps  "
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
