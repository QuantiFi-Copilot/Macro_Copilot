"""
test_policy_futures_futures_cross_market_spread_parity.py — Snapshot
                                                              parity
                                                              tests
                                                              for the
                                                              policy-
                                                              futures
                                                              cross-
                                                              market-
                                                              spread
                                                              monitor
=====================================================================

Locks in the *real production* output of
``calculate_futures_cross_market_spread`` (policy_futures domain)
for one or more representative pair invocations.

PR15 binds new primitives from day-one — this is a new primitive, so
the parity coverage ships with it.

The fixtures are captured against a live TimescaleDB by
``tests/fixtures/policy_futures_futures_cross_market_spread_v1/_capture.py``;
the test itself runs fully offline by replaying captured rows + the
captured reference dicts through mocked fetchers.
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

from rates_agent.policy_futures.tools.futures_cross_market_spread import (
    FuturesCrossMarketSpreadInput,
    calculate_futures_cross_market_spread,
)


FIXTURES_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "policy_futures_futures_cross_market_spread_v1"
)

# Tight tolerance — outputs are pre-rounded so floats should be
# bit-identical in practice; this only absorbs harmless float-
# formatting noise from upstream library updates.
FLOAT_ABS_TOL = 1e-9


_TARGET = "rates_agent.policy_futures.tools.futures_cross_market_spread.compute"


# ---------------------------------------------------------------------------
# Frozen-date helper
# ---------------------------------------------------------------------------

class _FrozenDateForMonitor(date):
    """``date`` subclass with ``today()`` returning a fixed value.

    Patched in place of
    ``rates_agent.policy_futures.tools.futures_cross_market_spread.compute.date``
    so the tool's ``date.today()`` callsite becomes deterministic.
    The pinned-anchor fixture supplies ``as_of_date`` explicitly, so
    this frozen-date patch is a belt-and-braces safety net rather
    than load-bearing on the new fixture.
    """

    _frozen_value: date = date(2000, 1, 1)  # overridden per test invocation

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
        assert actual is expected, f"{path}: expected {expected!r}, got {actual!r}"
        return

    if isinstance(expected, float):
        assert actual is not None, f"{path}: expected {expected!r}, got None"
        if math.isnan(expected):
            assert isinstance(actual, float) and math.isnan(actual), (
                f"{path}: expected NaN, got {actual!r}"
            )
            return
        diff = abs(float(actual) - expected)
        assert diff <= FLOAT_ABS_TOL, (
            f"{path}: float mismatch.  actual={actual!r} "
            f"expected={expected!r} diff={diff:.3e} "
            f"tol={FLOAT_ABS_TOL:.0e}"
        )
        return

    if isinstance(expected, int):
        assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"
        return

    if isinstance(expected, str):
        assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"
        return

    assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"


# ---------------------------------------------------------------------------
# Tamper-detection helpers (must mirror _capture.py byte-for-byte)
# ---------------------------------------------------------------------------

def _canonicalise(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonicalise_reference(reference: dict) -> str:
    return json.dumps(reference, sort_keys=True, separators=(",", ":"))


def _hash_payload(
    rows: list[dict], reference_a: dict, reference_b: dict,
) -> str:
    """Hash the concatenated canonicalisation of the rows + per-leg
    reference dicts.

    Must match ``_capture.py::_hash_payload`` byte-for-byte — label
    prefixes disambiguate the streams so a swap (e.g. mutating one
    row vs mutating one ref field) still changes the hash.
    """
    payload = (
        "PRICE|" + _canonicalise(rows)
        + "|REF_A|" + _canonicalise_reference(reference_a)
        + "|REF_B|" + _canonicalise_reference(reference_b)
    )
    return _sha256_hex(payload)


# ---------------------------------------------------------------------------
# Fixture-driven test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/policy_futures_futures_cross_market_spread_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_policy_futures_futures_cross_market_spread_parity(
    fixture_path: Path,
) -> None:
    """Run the captured inputs through
    ``calculate_futures_cross_market_spread`` with the DB fetchers
    mocked and the date frozen, and assert byte-equal output (modulo
    float tolerance) against the recorded fixture."""

    with fixture_path.open() as f:
        fx = json.load(f)

    # 1. Tamper-detection: verify the recorded raw_rows hash matches
    #    a fresh recompute.
    raw_price_rows = fx["input"]["raw_price_rows"]
    reference_a = fx["input"]["reference_a"]
    reference_b = fx["input"]["reference_b"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256 — "
        "fixture must be regenerated by _capture.py"
    )
    actual_hash = _hash_payload(
        raw_price_rows, reference_a, reference_b,
    )
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch.\n"
        f"  recorded: {expected_hash}\n"
        f"  computed: {actual_hash}\n"
        "raw rows or reference dicts have been edited without re-"
        "running the capture script. Either restore the original "
        "values or regenerate the fixture with "
        "`python tests/fixtures/policy_futures_futures_cross_market_spread_v1/_capture.py`."
    )

    # 2. Reconstruct the long-format DataFrame the cross-market-strip
    #    fetcher returns (columns: trade_date, curve_family,
    #    field_value).
    if not raw_price_rows:
        raw_price_df = pd.DataFrame(
            columns=["trade_date", "curve_family", "field_value"],
        )
    else:
        raw_price_df = pd.DataFrame(raw_price_rows)
        raw_price_df["trade_date"] = (
            pd.to_datetime(raw_price_df["trade_date"]).dt.date
        )
        raw_price_df["curve_family"] = raw_price_df["curve_family"].astype(str)
        raw_price_df["field_value"] = raw_price_df["field_value"].astype(float)

    # 3. Build the input.
    params_dict = dict(fx["input"]["params"])
    params = FuturesCrossMarketSpreadInput(**params_dict)

    # 4. Freeze the date (belt-and-braces — the captured fixture
    #    supplies as_of_date explicitly).
    _FrozenDateForMonitor._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    # 5. Patch and run. Patches target the COMPUTE module specifically.
    def _series_side_effect(
        *, engine, curve_family_1, curve_family_2, strip_position,
        field_name, start_date,
    ):
        return raw_price_df

    def _reference_side_effect(
        *, engine, curve_family, strip_position, as_of_date,
    ):
        if curve_family == params.curve_family_a:
            return reference_a
        if curve_family == params.curve_family_b:
            return reference_b
        return None

    def _max_date_side_effect(
        *, engine, curve_family, strip_position, field_name,
    ):
        # Return the frozen-today value — guarantees the future-
        # anchor guard passes (any as_of_date <= frozen_today is
        # within range).
        return _FrozenDateForMonitor._frozen_value

    with patch(
        f"{_TARGET}.fetch_cross_market_strip",
        side_effect=_series_side_effect,
    ), patch(
        f"{_TARGET}.fetch_strip_position_reference",
        side_effect=_reference_side_effect,
    ), patch(
        f"{_TARGET}.fetch_strip_position_max_date",
        side_effect=_max_date_side_effect,
    ), patch(
        f"{_TARGET}.date", _FrozenDateForMonitor,
    ):
        actual = calculate_futures_cross_market_spread(
            engine=None, params=params,
        )

    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: "
        f"{actual.get('error')!r}"
    )

    # 6. Compare with recorded expectation.
    _assert_equal(actual, fx["expected_output"], path="$")


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason="No fixtures found — see capture instructions in the README.",
)
def test_policy_futures_futures_cross_market_spread_parity_tamper_detected() -> None:
    """Negative control: if any captured row is mutated in memory
    before hashing, the tamper-detection step must reject it.
    Guarantees the parity test is actually checking the hash."""
    fixture_path = _FIXTURE_PATHS[0]
    with fixture_path.open() as f:
        fx = json.load(f)
    rows = list(fx["input"]["raw_price_rows"])
    if not rows:
        pytest.skip("fixture has no price rows")
    # Tamper: bump the latest price value by 1 tick on one row.
    tampered = list(rows)
    tampered[-1] = dict(tampered[-1])
    tampered[-1]["field_value"] = float(tampered[-1]["field_value"]) + 1.0

    recorded_hash = fx["capture"]["raw_rows_sha256"]
    tampered_hash = _hash_payload(
        tampered,
        fx["input"]["reference_a"],
        fx["input"]["reference_b"],
    )
    assert tampered_hash != recorded_hash, (
        "tamper-detection negative control failed — hash did not "
        "change when raw_price_rows was mutated."
    )
