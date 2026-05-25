"""
test_policy_futures_futures_butterfly_simple_parity.py — Snapshot
                                                          parity tests
                                                          for the
                                                          policy-futures
                                                          simple-
                                                          butterfly
                                                          monitor
=====================================================================

Locks in the *real production* output of
``calculate_futures_butterfly_simple`` (policy_futures domain) for
one or more representative triple invocations (default: ``SOFR_FUT``
strip_position_wing_short=1, strip_position_body=2,
strip_position_wing_long=3 anchored at 2026-04-08).

PR15 binds new primitives from day-one — this is a new primitive, so
the parity coverage ships with it.

The fixtures are captured against a live TimescaleDB by
``tests/fixtures/policy_futures_futures_butterfly_simple_v1/_capture.py``;
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

from rates_agent.policy_futures.tools.futures_butterfly_simple import (
    FuturesButterflySimpleInput,
    calculate_futures_butterfly_simple,
)


FIXTURES_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "policy_futures_futures_butterfly_simple_v1"
)

FLOAT_ABS_TOL = 1e-9


_TARGET = "rates_agent.policy_futures.tools.futures_butterfly_simple.compute"


class _FrozenDateForMonitor(date):
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


def _canonicalise(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonicalise_reference(reference: dict) -> str:
    return json.dumps(reference, sort_keys=True, separators=(",", ":"))


def _hash_payload(
    rows: list[dict],
    reference_wing_short: dict,
    reference_body: dict,
    reference_wing_long: dict,
) -> str:
    """Hash the concatenated canonicalisation of the rows + per-leg
    reference dicts. Must match ``_capture.py::_hash_payload``
    byte-for-byte."""
    payload = (
        "PRICE|" + _canonicalise(rows)
        + "|REF_WING_SHORT|" + _canonicalise_reference(reference_wing_short)
        + "|REF_BODY|" + _canonicalise_reference(reference_body)
        + "|REF_WING_LONG|" + _canonicalise_reference(reference_wing_long)
    )
    return _sha256_hex(payload)


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/policy_futures_futures_butterfly_simple_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_policy_futures_futures_butterfly_simple_parity(
    fixture_path: Path,
) -> None:
    with fixture_path.open() as f:
        fx = json.load(f)

    raw_price_rows = fx["input"]["raw_price_rows"]
    reference_wing_short = fx["input"]["reference_wing_short"]
    reference_body = fx["input"]["reference_body"]
    reference_wing_long = fx["input"]["reference_wing_long"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256 — "
        "fixture must be regenerated by _capture.py"
    )
    actual_hash = _hash_payload(
        raw_price_rows,
        reference_wing_short, reference_body, reference_wing_long,
    )
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch.\n"
        f"  recorded: {expected_hash}\n"
        f"  computed: {actual_hash}\n"
        "raw rows or reference dicts have been edited without re-"
        "running the capture script."
    )

    if not raw_price_rows:
        raw_price_df = pd.DataFrame(
            columns=["trade_date", "strip_position", "field_value"],
        )
    else:
        raw_price_df = pd.DataFrame(raw_price_rows)
        raw_price_df["trade_date"] = (
            pd.to_datetime(raw_price_df["trade_date"]).dt.date
        )
        raw_price_df["strip_position"] = raw_price_df["strip_position"].astype(int)
        raw_price_df["field_value"] = raw_price_df["field_value"].astype(float)

    params_dict = dict(fx["input"]["params"])
    params = FuturesButterflySimpleInput(**params_dict)

    _FrozenDateForMonitor._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    def _series_side_effect(
        *, engine, curve_family, strip_positions, field_name, start_date,
    ):
        return raw_price_df

    def _reference_side_effect(
        *, engine, curve_family, strip_position, as_of_date,
    ):
        if strip_position == params.strip_position_wing_short:
            return reference_wing_short
        if strip_position == params.strip_position_body:
            return reference_body
        if strip_position == params.strip_position_wing_long:
            return reference_wing_long
        return None

    def _max_date_side_effect(
        *, engine, curve_family, strip_position, field_name,
    ):
        return _FrozenDateForMonitor._frozen_value

    with patch(
        f"{_TARGET}.fetch_strip_group",
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
        actual = calculate_futures_butterfly_simple(
            engine=None, params=params,
        )

    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: "
        f"{actual.get('error')!r}"
    )

    _assert_equal(actual, fx["expected_output"], path="$")


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason="No fixtures found — see capture instructions in the README.",
)
def test_policy_futures_futures_butterfly_simple_parity_tamper_detected() -> None:
    """Negative control: if any captured row is mutated, the
    tamper-detection step must reject it."""
    fixture_path = _FIXTURE_PATHS[0]
    with fixture_path.open() as f:
        fx = json.load(f)
    rows = list(fx["input"]["raw_price_rows"])
    if not rows:
        pytest.skip("fixture has no price rows")
    tampered = list(rows)
    tampered[-1] = dict(tampered[-1])
    tampered[-1]["field_value"] = float(tampered[-1]["field_value"]) + 1.0

    recorded_hash = fx["capture"]["raw_rows_sha256"]
    tampered_hash = _hash_payload(
        tampered,
        fx["input"]["reference_wing_short"],
        fx["input"]["reference_body"],
        fx["input"]["reference_wing_long"],
    )
    assert tampered_hash != recorded_hash, (
        "tamper-detection negative control failed — hash did not "
        "change when raw_price_rows was mutated."
    )
