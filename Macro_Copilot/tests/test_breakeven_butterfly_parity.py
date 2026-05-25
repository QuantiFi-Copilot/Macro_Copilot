"""
test_breakeven_butterfly_parity.py — Snapshot parity tests for
                                     calculate_breakeven_butterfly
==================================================================

PR15 backfill — modelled on
``tests/test_breakeven_curve_spread_parity.py``; the only shape
difference is THREE inner spot calls per scenario (short / belly /
long) instead of two.

Patches THREE seams, ALL in the INNER spot primitive's compute
module:

  1. ``breakeven_inflation_simple.compute.fetch_single_tenor``
  2. ``breakeven_inflation_simple.compute._fetch_curve_family_country_currency``
  3. ``breakeven_inflation_simple.compute.date``

Regenerating fixtures — see
``tests/fixtures/breakeven_butterfly_v1/README.md``.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
    BreakevenButterflyInput,
    calculate_breakeven_butterfly,
)


FIXTURES_DIR = (
    Path(__file__).parent / "fixtures" / "breakeven_butterfly_v1"
)
FLOAT_ABS_TOL = 1e-9


class _FrozenDateForBreakeven(date):
    _frozen_value: date = date(2000, 1, 1)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _discover_fixtures() -> list[Path]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(
        p for p in FIXTURES_DIR.glob("*.json")
        if not p.name.startswith("_")
    )


_FIXTURE_PATHS = _discover_fixtures()
_FIXTURE_IDS = [p.stem for p in _FIXTURE_PATHS]


def _assert_equal(actual: Any, expected: Any, *, path: str = "$") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), (
            f"{path}: expected dict, got {type(actual).__name__}"
        )
        ekeys, akeys = set(expected.keys()), set(actual.keys())
        assert ekeys == akeys, (
            f"{path}: key mismatch.  missing={sorted(ekeys - akeys)} "
            f"extra={sorted(akeys - ekeys)}"
        )
        for k in expected:
            _assert_equal(actual[k], expected[k], path=f"{path}.{k}")
        return
    if isinstance(expected, list):
        assert isinstance(actual, list), (
            f"{path}: expected list, got {type(actual).__name__}"
        )
        assert len(actual) == len(expected), (
            f"{path}: length mismatch.  actual={len(actual)} "
            f"expected={len(expected)}"
        )
        for i, (a_item, e_item) in enumerate(zip(actual, expected)):
            _assert_equal(a_item, e_item, path=f"{path}[{i}]")
        return
    if expected is None:
        assert actual is None, f"{path}: expected None, got {actual!r}"
        return
    if isinstance(expected, bool):
        assert actual is expected, (
            f"{path}: expected {expected!r}, got {actual!r}"
        )
        return
    if isinstance(expected, float):
        assert actual is not None, (
            f"{path}: expected {expected!r}, got None"
        )
        if math.isnan(expected):
            assert isinstance(actual, float) and math.isnan(actual), (
                f"{path}: expected NaN, got {actual!r}"
            )
            return
        diff = abs(float(actual) - expected)
        assert diff <= FLOAT_ABS_TOL, (
            f"{path}: float mismatch.  actual={actual!r} expected={expected!r} "
            f"diff={diff:.3e} tol={FLOAT_ABS_TOL:.0e}"
        )
        return
    if isinstance(expected, int):
        assert actual == expected, (
            f"{path}: expected {expected!r}, got {actual!r}"
        )
        return
    if isinstance(expected, str):
        assert actual == expected, (
            f"{path}: expected {expected!r}, got {actual!r}"
        )
        return
    assert actual == expected, (
        f"{path}: expected {expected!r}, got {actual!r}"
    )


def _canonicalise(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _series_key(*, curve_family, tenor, field_name, instrument_type) -> str:
    return f"{curve_family}__{tenor}__{field_name}__{instrument_type}"


def _country_key(*, curve_family, instrument_type) -> str:
    return f"{curve_family}__{instrument_type}"


def _build_fetch_side_effect(raw_rows: dict[str, list[dict]]):
    cache: dict[str, pd.DataFrame] = {}

    def _df_for(key: str) -> pd.DataFrame:
        if key in cache:
            return cache[key]
        rows = raw_rows.get(key)
        if rows is None:
            raise AssertionError(
                f"Parity-test mock: no captured raw_rows for series "
                f"key {key!r}.  Captured keys: "
                f"{sorted(raw_rows.keys())}"
            )
        if not rows:
            df = pd.DataFrame(columns=["trade_date", "field_value"])
        else:
            df = pd.DataFrame(rows)
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            df["field_value"] = df["field_value"].astype(float)
        cache[key] = df
        return df

    def _side_effect(**kwargs):
        return _df_for(
            _series_key(
                curve_family=kwargs["curve_family"],
                tenor=kwargs["tenor"],
                field_name=kwargs["field_name"],
                instrument_type=kwargs["instrument_type"],
            )
        )

    return _side_effect


def _build_identity_side_effect(country_currency: dict[str, dict]):
    def _side_effect(engine, curve_family: str, instrument_type: str):
        key = _country_key(
            curve_family=curve_family, instrument_type=instrument_type,
        )
        entry = country_currency.get(key)
        if entry is None:
            raise AssertionError(
                f"Parity-test mock: no captured country/currency for "
                f"key {key!r}.  Captured keys: "
                f"{sorted(country_currency.keys())}"
            )
        return entry["country"], entry["currency"], None

    return _side_effect


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/breakeven_butterfly_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_breakeven_butterfly_parity(fixture_path: Path) -> None:
    with fixture_path.open() as f:
        fx = json.load(f)

    country_currency = fx["input"]["country_currency"]
    raw_rows = fx["input"]["raw_rows"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256"
    )
    payload = (
        "CC|" + _canonicalise(country_currency)
        + "|ROWS|" + _canonicalise(raw_rows)
    )
    actual_hash = _sha256_hex(payload)
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch."
    )

    fetch_se = _build_fetch_side_effect(raw_rows)
    identity_se = _build_identity_side_effect(country_currency)
    params = BreakevenButterflyInput(**fx["input"]["params"])
    _FrozenDateForBreakeven._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    inner_module = (
        "rates_agent.inflation_indexed_bonds.tools."
        "breakeven_inflation_simple.compute"
    )
    with patch(
        f"{inner_module}.fetch_single_tenor", side_effect=fetch_se,
    ), patch(
        f"{inner_module}._fetch_curve_family_country_currency",
        side_effect=identity_se,
    ), patch(f"{inner_module}.date", _FrozenDateForBreakeven):
        actual = calculate_breakeven_butterfly(
            engine=None, params=params,
        )

    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: "
        f"{actual.get('error')!r}"
    )
    _assert_equal(actual, fx["expected_output"], path="$")
