"""
test_swap_breakeven_basis_simple_parity.py — Snapshot parity tests
for calculate_swap_breakeven_basis_simple
==================================================================

PR15 backfill — closes the 15-primitive inflation backfill batch.
This is the only stage-3 primitive whose compose path crosses
domain boundaries: it combines a ZCIS leg
(``calculate_inflation_swap_rate_level``) with a breakeven leg
(``calculate_breakeven_inflation_simple``).

Patches FIVE seams across TWO modules:

  - ``inflation_swap_rate_level.compute.fetch_zcis_single_pillar``
  - ``inflation_swap_rate_level.compute.date``
  - ``breakeven_inflation_simple.compute.fetch_single_tenor``
  - ``breakeven_inflation_simple.compute._fetch_curve_family_country_currency``
  - ``breakeven_inflation_simple.compute.date``

Two distinct ``date`` seams (one per inner primitive's compute
module) because the compose primitive itself does not import
``date``.  Both ``_FrozenDate...`` subclasses share the same
frozen value per scenario.

Regenerating fixtures — see
``tests/fixtures/swap_breakeven_basis_simple_v1/README.md``.
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

from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
    SwapBreakevenBasisSimpleInput,
    calculate_swap_breakeven_basis_simple,
)


FIXTURES_DIR = (
    Path(__file__).parent / "fixtures" / "swap_breakeven_basis_simple_v1"
)
FLOAT_ABS_TOL = 1e-9


class _FrozenDateForZcisLevel(date):
    """Frozen-date subclass for the ZCIS inner primitive."""

    _frozen_value: date = date(2000, 1, 1)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


class _FrozenDateForBreakeven(date):
    """Frozen-date subclass for the breakeven inner primitive."""

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


def _zcis_key(*, curve_family, tenor, field_name) -> str:
    return f"{curve_family}__{tenor}__{field_name}"


def _be_series_key(
    *, curve_family, tenor, field_name, instrument_type,
) -> str:
    return f"{curve_family}__{tenor}__{field_name}__{instrument_type}"


def _country_key(*, curve_family, instrument_type) -> str:
    return f"{curve_family}__{instrument_type}"


def _build_zcis_side_effect(raw_rows_zcis: dict[str, list[dict]]):
    cache: dict[str, pd.DataFrame] = {}

    def _df_for(key: str) -> pd.DataFrame:
        if key in cache:
            return cache[key]
        rows = raw_rows_zcis.get(key)
        if rows is None:
            raise AssertionError(
                f"Parity-test mock: no captured ZCIS raw_rows for "
                f"series key {key!r}.  Captured keys: "
                f"{sorted(raw_rows_zcis.keys())}"
            )
        if not rows:
            df = pd.DataFrame(
                columns=[
                    "trade_date", "field_value", "vendor_ticker",
                    "pricing_type", "inflation_index_family",
                    "index_lag", "interpolation", "underlying_index",
                ]
            )
        else:
            df = pd.DataFrame(rows)
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            df["field_value"] = df["field_value"].astype(float)
        cache[key] = df
        return df

    def _side_effect(**kwargs):
        return _df_for(
            _zcis_key(
                curve_family=kwargs["curve_family"],
                tenor=kwargs["tenor"],
                field_name=kwargs["field_name"],
            )
        )

    return _side_effect


def _build_be_fetch_side_effect(raw_rows_be: dict[str, list[dict]]):
    cache: dict[str, pd.DataFrame] = {}

    def _df_for(key: str) -> pd.DataFrame:
        if key in cache:
            return cache[key]
        rows = raw_rows_be.get(key)
        if rows is None:
            raise AssertionError(
                f"Parity-test mock: no captured breakeven raw_rows for "
                f"series key {key!r}.  Captured keys: "
                f"{sorted(raw_rows_be.keys())}"
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
            _be_series_key(
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
        "`python tests/fixtures/swap_breakeven_basis_simple_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_swap_breakeven_basis_simple_parity(fixture_path: Path) -> None:
    with fixture_path.open() as f:
        fx = json.load(f)

    raw_rows_zcis = fx["input"]["raw_rows_zcis"]
    raw_rows_be = fx["input"]["raw_rows_breakeven"]
    country_currency = fx["input"]["country_currency"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256"
    )
    payload = (
        "ZCIS|" + _canonicalise(raw_rows_zcis)
        + "|BE|" + _canonicalise(raw_rows_be)
        + "|CC|" + _canonicalise(country_currency)
    )
    actual_hash = _sha256_hex(payload)
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch."
    )

    zcis_se = _build_zcis_side_effect(raw_rows_zcis)
    be_fetch_se = _build_be_fetch_side_effect(raw_rows_be)
    identity_se = _build_identity_side_effect(country_currency)
    params = SwapBreakevenBasisSimpleInput(**fx["input"]["params"])
    frozen_value = date.fromisoformat(fx["input"]["frozen_today"])
    _FrozenDateForZcisLevel._frozen_value = frozen_value
    _FrozenDateForBreakeven._frozen_value = frozen_value

    zcis_module = (
        "rates_agent.inflation_swaps.tools."
        "inflation_swap_rate_level.compute"
    )
    be_module = (
        "rates_agent.inflation_indexed_bonds.tools."
        "breakeven_inflation_simple.compute"
    )
    with patch(
        f"{zcis_module}.fetch_zcis_single_pillar", side_effect=zcis_se,
    ), patch(
        f"{zcis_module}.date", _FrozenDateForZcisLevel,
    ), patch(
        f"{be_module}.fetch_single_tenor", side_effect=be_fetch_se,
    ), patch(
        f"{be_module}._fetch_curve_family_country_currency",
        side_effect=identity_se,
    ), patch(f"{be_module}.date", _FrozenDateForBreakeven):
        actual = calculate_swap_breakeven_basis_simple(
            engine=None, params=params,
        )

    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: "
        f"{actual.get('error')!r}"
    )
    _assert_equal(actual, fx["expected_output"], path="$")
