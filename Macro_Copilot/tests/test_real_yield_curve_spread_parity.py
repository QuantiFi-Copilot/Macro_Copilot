"""
test_real_yield_curve_spread_parity.py — Snapshot parity tests for
                                         calculate_real_yield_curve_spread
==========================================================================

Locks in the *real production* output of
``calculate_real_yield_curve_spread`` for three representative
linker curve points.  Any commit that changes the math — directly,
through the inner ``get_real_yield_level`` primitive, through a
primitive in ``shared/analytics/``, or via a YAML edit to either
bundled ``config.yaml`` — must keep these tests green or come with
a deliberate, reviewed fixture regeneration.

PR15 backfill — modelled on
``tests/test_curve_spread_parity.py``.

Compose-primitive seam topology
-------------------------------
This primitive composes two inner ``get_real_yield_level`` calls
and runs one identity-guard DB read in its OWN compute module.  The
replay therefore patches THREE seams:

  1. ``real_yield_curve_spread.compute._fetch_curve_family_country_currency``
     — replays the captured (country, currency) tuple per
     ``(curve_family, instrument_type)`` (only 1 call per run).
  2. ``real_yield_level.compute.fetch_single_tenor`` — replays the
     captured raw_rows per ``(curve_family, tenor, field_name,
     instrument_type)`` (one call per endpoint tenor).
  3. ``real_yield_level.compute.date`` — frozen ``date`` subclass
     so the inner level primitive's one ``date.today()`` callsite
     becomes deterministic.

The inner primitive's package init re-exports only public symbols
and does NOT propagate the compute module's imports — see the
``real_yield_level`` package init's "Note for tests" section.  All
three patches target ``compute.py`` namespaces specifically.

Regenerating fixtures
---------------------
Only after a deliberate methodology change.  See
``tests/fixtures/real_yield_curve_spread_v1/README.md``.
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

from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
    RealYieldCurveSpreadInput,
    calculate_real_yield_curve_spread,
)


FIXTURES_DIR = (
    Path(__file__).parent / "fixtures" / "real_yield_curve_spread_v1"
)
FLOAT_ABS_TOL = 1e-9


# ---------------------------------------------------------------------------
# Frozen-date helper for the INNER level primitive
# ---------------------------------------------------------------------------

class _FrozenDateForRealYieldLevel(date):
    """``date`` subclass with ``today()`` returning a fixed value.

    Patched in place of
    ``rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date``
    so the inner level primitive's one ``date.today()`` callsite
    becomes deterministic.  The compose primitive itself does not
    import ``date`` (the fetch start-date logic lives entirely
    inside the inner level primitive).
    """

    _frozen_value: date = date(2000, 1, 1)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------

def _discover_fixtures() -> list[Path]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(
        p for p in FIXTURES_DIR.glob("*.json")
        if not p.name.startswith("_")
    )


_FIXTURE_PATHS = _discover_fixtures()
_FIXTURE_IDS = [p.stem for p in _FIXTURE_PATHS]


# ---------------------------------------------------------------------------
# Recursive deep comparator
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Hash helpers + key constructors — must agree with the capture script
# ---------------------------------------------------------------------------

def _canonicalise(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _series_key(
    *, curve_family: str, tenor: str, field_name: str, instrument_type: str,
) -> str:
    return f"{curve_family}__{tenor}__{field_name}__{instrument_type}"


def _country_key(*, curve_family: str, instrument_type: str) -> str:
    return f"{curve_family}__{instrument_type}"


# ---------------------------------------------------------------------------
# Side-effect builders
# ---------------------------------------------------------------------------

def _build_fetch_side_effect(raw_rows: dict[str, list[dict]]):
    """Side effect for the INNER level primitive's
    ``fetch_single_tenor``.  Routes by (curve_family, tenor,
    field_name, instrument_type).
    """
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
        key = _series_key(
            curve_family=kwargs["curve_family"],
            tenor=kwargs["tenor"],
            field_name=kwargs["field_name"],
            instrument_type=kwargs["instrument_type"],
        )
        return _df_for(key)

    return _side_effect


def _build_identity_side_effect(country_currency: dict[str, dict]):
    """Side effect for this primitive's OWN
    ``_fetch_curve_family_country_currency``.  Called positionally:
    ``(engine, curve_family, instrument_type)``.
    """

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


# ---------------------------------------------------------------------------
# Fixture-driven test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/real_yield_curve_spread_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_real_yield_curve_spread_parity(fixture_path: Path) -> None:
    """Run the captured inputs through
    ``calculate_real_yield_curve_spread`` with the inner DB seams
    mocked and the date frozen, and assert byte-equal output (modulo
    float tolerance) against the recorded fixture."""

    with fixture_path.open() as f:
        fx = json.load(f)

    # 1. Tamper-detection.
    country_currency = fx["input"]["country_currency"]
    raw_rows = fx["input"]["raw_rows"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256 — "
        "fixture must be regenerated by _capture.py"
    )
    payload = (
        "CC|" + _canonicalise(country_currency)
        + "|ROWS|" + _canonicalise(raw_rows)
    )
    actual_hash = _sha256_hex(payload)
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch.\n"
        f"  recorded: {expected_hash}\n"
        f"  computed: {actual_hash}\n"
        "country_currency or raw_rows have been edited without "
        "re-running the capture script.  Either restore the original "
        "payload or regenerate the fixture with "
        "`python tests/fixtures/real_yield_curve_spread_v1/_capture.py`."
    )

    # 2. Build side_effects.
    fetch_se = _build_fetch_side_effect(raw_rows)
    identity_se = _build_identity_side_effect(country_currency)

    # 3. Build the input.
    params = RealYieldCurveSpreadInput(**fx["input"]["params"])

    # 4. Freeze the date (patched into the INNER level primitive).
    _FrozenDateForRealYieldLevel._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    # 5. Patch three seams: identity in THIS primitive's compute
    #    module; fetcher + date in the INNER level primitive's
    #    compute module.
    compose_module = (
        "rates_agent.inflation_indexed_bonds.tools."
        "real_yield_curve_spread.compute"
    )
    level_module = (
        "rates_agent.inflation_indexed_bonds.tools."
        "real_yield_level.compute"
    )
    with patch(
        f"{compose_module}._fetch_curve_family_country_currency",
        side_effect=identity_se,
    ), patch(
        f"{level_module}.fetch_single_tenor", side_effect=fetch_se,
    ), patch(f"{level_module}.date", _FrozenDateForRealYieldLevel):
        actual = calculate_real_yield_curve_spread(
            engine=None, params=params,
        )

    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: "
        f"{actual.get('error')!r}"
    )

    # 6. Compare.
    _assert_equal(actual, fx["expected_output"], path="$")
