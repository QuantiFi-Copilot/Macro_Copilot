"""
test_policy_futures_volume_open_interest_snapshot_parity.py — Snapshot
parity tests for the policy-futures strip-position volume + OI snapshot
monitor
=====================================================================

Locks in the *real production* output of
``calculate_volume_open_interest_snapshot`` for one or more
representative strip-slot invocations (default: ``SOFR_FUT``
strip_position=1 anchored at 2026-04-08). Any commit that changes the
math — directly or through a primitive in ``shared/analytics/`` or
via a YAML-convention edit — must keep this test green or come with
a deliberate, reviewed fixture regeneration.

PR15 binds new primitives from day-one —
``policy_futures.volume_open_interest_snapshot`` is a new primitive,
so the parity coverage ships with it rather than being grandfathered
debt.

The fixtures are captured against a live TimescaleDB by
``tests/fixtures/policy_futures_volume_open_interest_snapshot_v1/_capture.py``;
the test itself runs fully offline by replaying captured rows + the
captured reference dict through mocked fetchers.

How it works
------------
For each fixture in
``tests/fixtures/policy_futures_volume_open_interest_snapshot_v1/``:

1. Load the JSON. Verify the captured ``raw_rows_sha256`` matches a
   freshly-computed hash of ``input.raw_volume_rows`` +
   ``input.raw_oi_rows`` + ``input.reference`` — guards against
   fixture tampering.
2. Reconstruct the two long-format DataFrames the tool's DB fetcher
   would return for PX_VOLUME and OPEN_INT.
3. Patch the compute module's three fetcher helpers with side-effects
   that route by argument shape, and patch the same module's ``date``
   reference with a subclass whose ``today()`` returns
   ``input.frozen_today``.
4. Build a ``VolumeOpenInterestSnapshotInput`` from the recorded
   params and call
   ``calculate_volume_open_interest_snapshot(engine=None, params=...)``.
5. Recursively compare the result against ``expected_output`` —
   strings and ints exact, floats within 1e-9 absolute tolerance,
   dicts must have identical keysets, lists identical lengths.
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

from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
    VolumeOpenInterestSnapshotInput,
    calculate_volume_open_interest_snapshot,
)


FIXTURES_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "policy_futures_volume_open_interest_snapshot_v1"
)

# Tight tolerance — outputs are pre-rounded so floats should be
# bit-identical in practice; this only absorbs harmless float-
# formatting noise from upstream library updates.
FLOAT_ABS_TOL = 1e-9


_TARGET = (
    "rates_agent.policy_futures.tools.volume_open_interest_snapshot.compute"
)


# ---------------------------------------------------------------------------
# Frozen-date helper
# ---------------------------------------------------------------------------

class _FrozenDateForMonitor(date):
    """``date`` subclass with ``today()`` returning a fixed value."""

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
        assert actual is expected, (
            f"{path}: expected {expected!r}, got {actual!r}"
        )
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
        assert actual == expected, (
            f"{path}: expected {expected!r}, got {actual!r}"
        )
        return

    if isinstance(expected, str):
        assert actual == expected, (
            f"{path}: expected {expected!r}, got {actual!r}"
        )
        return

    assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"


# ---------------------------------------------------------------------------
# Tamper-detection helpers (must mirror _capture.py byte-for-byte)
# ---------------------------------------------------------------------------

def _canonicalise(rows: list[dict]) -> str:
    """Match the canonical encoding ``_capture.py`` uses for hashing."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonicalise_reference(reference: dict) -> str:
    return json.dumps(reference, sort_keys=True, separators=(",", ":"))


def _hash_payload(
    volume_rows: list[dict],
    oi_rows: list[dict],
    reference: dict,
) -> str:
    """Hash the concatenated canonicalisation of the volume rows + OI
    rows + reference dict so a swap of any stream changes the hash.
    Must match ``_capture.py::_hash_payload`` byte-for-byte.
    """
    payload = (
        "VOL|" + _canonicalise(volume_rows)
        + "|OI|" + _canonicalise(oi_rows)
        + "|REF|" + _canonicalise_reference(reference)
    )
    return _sha256_hex(payload)


# ---------------------------------------------------------------------------
# Fixture-driven test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/policy_futures_volume_open_interest_snapshot_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_policy_futures_volume_open_interest_snapshot_parity(
    fixture_path: Path,
) -> None:
    """Run the captured inputs through
    ``calculate_volume_open_interest_snapshot`` with the DB fetchers
    mocked and the date frozen, and assert byte-equal output (modulo
    float tolerance) against the recorded fixture."""

    with fixture_path.open() as f:
        fx = json.load(f)

    # 1. Tamper-detection: verify the recorded raw_rows hash matches
    #    a fresh recompute.
    raw_volume_rows = fx["input"]["raw_volume_rows"]
    raw_oi_rows = fx["input"]["raw_oi_rows"]
    reference = fx["input"]["reference"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256 — "
        "fixture must be regenerated by _capture.py"
    )
    actual_hash = _hash_payload(raw_volume_rows, raw_oi_rows, reference)
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch.\n"
        f"  recorded: {expected_hash}\n"
        f"  computed: {actual_hash}\n"
        "raw rows or reference dict have been edited without re-"
        "running the capture script. Either restore the original "
        "values or regenerate the fixture with "
        "`python tests/fixtures/policy_futures_volume_open_interest_snapshot_v1/_capture.py`."
    )

    # 2. Reconstruct the long-format DataFrames the fetcher returns.
    def _rows_to_df(rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=["trade_date", "field_value"])
        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["field_value"] = df["field_value"].astype(float)
        return df

    raw_volume_df = _rows_to_df(raw_volume_rows)
    raw_oi_df = _rows_to_df(raw_oi_rows)

    # 3. Build the input. The fixture stores ``as_of_date`` as an ISO
    #    string; Pydantic parses it back to ``date`` on construction.
    params_dict = dict(fx["input"]["params"])
    params = VolumeOpenInterestSnapshotInput(**params_dict)

    # 4. Freeze the date (belt-and-braces — the captured fixture
    #    supplies as_of_date explicitly).
    _FrozenDateForMonitor._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    # 5. Patch and run. Patches target the COMPUTE module specifically.
    def _series_side_effect(
        *, engine, curve_family, strip_position, field_name,
        start_date, end_date=None,
    ):
        if field_name == "PX_VOLUME":
            return raw_volume_df
        if field_name == "OPEN_INT":
            return raw_oi_df
        # If the YAML field-name conventions diverge from the captured
        # fixture's mnemonics, surface that as a clear failure rather
        # than returning an empty frame (which would mask the drift).
        raise AssertionError(
            f"parity replay: fetcher was called with field_name="
            f"{field_name!r}, but the captured fixture only knows "
            f"PX_VOLUME and OPEN_INT. The YAML's default_volume_field "
            "/ default_open_interest_field has diverged from what the "
            "fixture was captured against — regenerate the fixture or "
            "restore the YAML."
        )

    def _reference_side_effect(
        *, engine, curve_family, strip_position, as_of_date,
    ):
        return reference

    def _max_date_side_effect(
        *, engine, curve_family, strip_position, field_name,
    ):
        # Return the frozen-today value — guarantees the future-anchor
        # guard passes (any as_of_date <= frozen_today is within
        # range).
        return _FrozenDateForMonitor._frozen_value

    with patch(
        f"{_TARGET}.fetch_strip_position",
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
        actual = calculate_volume_open_interest_snapshot(
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
def test_policy_futures_volume_open_interest_snapshot_parity_tamper_detected() -> None:
    """Negative control: if any captured row is mutated in memory
    before hashing, the tamper-detection step must reject it.
    Guarantees the parity test is actually checking the hash."""
    fixture_path = _FIXTURE_PATHS[0]
    with fixture_path.open() as f:
        fx = json.load(f)
    rows = list(fx["input"]["raw_volume_rows"])
    if not rows:
        pytest.skip("fixture has no volume rows")
    # Tamper: bump the latest volume value by 1 contract on one row.
    tampered = list(rows)
    tampered[-1] = dict(tampered[-1])
    tampered[-1]["field_value"] = float(tampered[-1]["field_value"]) + 1.0

    recorded_hash = fx["capture"]["raw_rows_sha256"]
    tampered_hash = _hash_payload(
        tampered,
        fx["input"]["raw_oi_rows"],
        fx["input"]["reference"],
    )
    assert tampered_hash != recorded_hash, (
        "tamper-detection negative control failed — hash did not "
        "change when raw_volume_rows was mutated. The parity-test "
        "hash implementation is broken."
    )
