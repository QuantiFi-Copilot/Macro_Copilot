"""
test_futures_volume_oi_parity.py — Snapshot parity tests for the
                                    bond-futures volume + OI monitor
=====================================================================

Locks in the *real production* output of
``calculate_futures_volume_oi`` for one or more representative
rolling-generic contracts (e.g. TY1 on UST_FUT 10Y). Any commit that
changes the math — directly or through a primitive in
``shared/analytics/`` or via a YAML-convention edit — must keep this
test green or come with a deliberate, reviewed fixture regeneration.

PR15 binds new primitives from day-one — ``futures_volume_oi`` is a
new primitive, so the parity coverage ships with it rather than being
grandfathered debt.

The fixtures are captured against a live TimescaleDB by
``tests/fixtures/futures_volume_oi_v1/_capture.py``; the test itself
runs fully offline by replaying captured ``raw_volume_rows`` +
``raw_oi_rows`` through a mocked fetcher.

How it works
------------
For each fixture in ``tests/fixtures/futures_volume_oi_v1/``:

1. Load the JSON. Verify the captured ``raw_rows_sha256`` matches a
   freshly-computed hash of ``input.raw_volume_rows`` +
   ``input.raw_oi_rows`` — guards against fixture tampering.
2. Reconstruct the two long-format DataFrames the tool's DB fetcher
   would return for PX_VOLUME and OPEN_INT.
3. Patch
   ``rates_agent.bond_futures.tools.futures_volume_oi.compute.fetch_rolling_generic_series``
   with a side-effect that routes by ``field_name``, and patch
   ``...compute.fetch_rolling_generic_reference`` to return the
   recorded reference dict. Patch the same module's ``date`` reference
   (``...compute.date``) with a subclass whose ``today()`` returns
   ``input.frozen_today``, so the two ``date.today()`` callsites
   inside the tool become deterministic.
4. Build a ``FuturesVolumeOIInput`` from the recorded params and call
   ``calculate_futures_volume_oi(engine=None, params=...)``.
5. Recursively compare the result against ``expected_output`` —
   strings and ints exact, floats within 1e-9 absolute tolerance,
   dicts must have identical keysets, lists identical lengths.

Why a tolerance and not byte-equal JSON?
----------------------------------------
``calculate_futures_volume_oi`` already rounds every numeric output
(z-score to 4 dp, counts to 0 dp), so on the same inputs the result
*should* be bit-identical between runs. We still allow a 1e-9
absolute tolerance to absorb harmless float-formatting differences
across pandas / numpy versions — anything bigger than that is real
math drift and the test fails.

Regenerating fixtures
---------------------
Only after a deliberate methodology change. See
``tests/fixtures/futures_volume_oi_v1/README.md``.
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

from rates_agent.bond_futures.tools.futures_volume_oi import (
    FuturesVolumeOIInput,
    calculate_futures_volume_oi,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "futures_volume_oi_v1"

# Tight tolerance — outputs are pre-rounded so floats should be
# bit-identical in practice; this only absorbs harmless float-formatting
# noise from upstream library updates.
FLOAT_ABS_TOL = 1e-9


# ---------------------------------------------------------------------------
# Frozen-date helper
# ---------------------------------------------------------------------------

class _FrozenDateForFuturesVolumeOI(date):
    """``date`` subclass with ``today()`` returning a fixed value.

    Patched in place of
    ``rates_agent.bond_futures.tools.futures_volume_oi.compute.date``
    so the tool's ``date.today()`` callsite in ``compute.py`` becomes
    deterministic.
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
        assert isinstance(actual, dict), f"{path}: expected dict, got {type(actual).__name__}"
        ekeys, akeys = set(expected.keys()), set(actual.keys())
        assert ekeys == akeys, (
            f"{path}: key mismatch.  missing={sorted(ekeys - akeys)} "
            f"extra={sorted(akeys - ekeys)}"
        )
        for k in expected:
            _assert_equal(actual[k], expected[k], path=f"{path}.{k}")
        return

    if isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected list, got {type(actual).__name__}"
        assert len(actual) == len(expected), (
            f"{path}: length mismatch.  actual={len(actual)} expected={len(expected)}"
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
            f"{path}: float mismatch.  actual={actual!r} expected={expected!r} "
            f"diff={diff:.3e} tol={FLOAT_ABS_TOL:.0e}"
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

def _canonicalise_rows(rows: list[dict]) -> str:
    """Match the canonical encoding ``_capture.py`` uses for hashing."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_both(
    volume_rows: list[dict], oi_rows: list[dict],
) -> str:
    """Hash the concatenated canonicalisation of the two row sets.

    Must match ``_capture.py::_hash_both`` byte-for-byte — a label
    prefix on each list disambiguates the two streams.
    """
    payload = (
        "VOL|" + _canonicalise(volume_rows)
        + "|OI|" + _canonicalise(oi_rows)
    )
    return _sha256_hex(payload)


def _canonicalise(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Fixture-driven test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/futures_volume_oi_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_futures_volume_oi_parity(fixture_path: Path) -> None:
    """Run the captured inputs through ``calculate_futures_volume_oi``
    with the DB fetchers mocked and the date frozen, and assert byte-
    equal output (modulo float tolerance) against the recorded
    fixture."""

    with fixture_path.open() as f:
        fx = json.load(f)

    # 1. Tamper-detection: verify the recorded raw_rows hash matches a
    #    fresh recompute. If raw_volume_rows / raw_oi_rows were edited by
    #    hand without re-running the capture script, the hash diverges
    #    and we abort rather than silently accepting altered baseline
    #    inputs.
    raw_volume_rows = fx["input"]["raw_volume_rows"]
    raw_oi_rows = fx["input"]["raw_oi_rows"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256 — "
        "fixture must be regenerated by _capture.py"
    )
    actual_hash = _hash_both(raw_volume_rows, raw_oi_rows)
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch.\n"
        f"  recorded: {expected_hash}\n"
        f"  computed: {actual_hash}\n"
        "raw rows have been edited without re-running the capture "
        "script. Either restore the original rows or regenerate the "
        "fixture with "
        "`python tests/fixtures/futures_volume_oi_v1/_capture.py`."
    )

    # 2. Reconstruct the long-format DataFrames the fetcher would return.
    raw_volume_df = pd.DataFrame(raw_volume_rows)
    raw_volume_df["trade_date"] = pd.to_datetime(raw_volume_df["trade_date"]).dt.date
    raw_volume_df["field_value"] = raw_volume_df["field_value"].astype(float)
    raw_oi_df = pd.DataFrame(raw_oi_rows)
    raw_oi_df["trade_date"] = pd.to_datetime(raw_oi_df["trade_date"]).dt.date
    raw_oi_df["field_value"] = raw_oi_df["field_value"].astype(float)

    # 3. Reconstruct the reference dict.
    reference = fx["input"]["reference"]

    # 4. Build the input.
    params = FuturesVolumeOIInput(**fx["input"]["params"])

    # 5. Freeze the date.
    _FrozenDateForFuturesVolumeOI._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    # 6. Patch and run. Patches target the COMPUTE module specifically.
    target = "rates_agent.bond_futures.tools.futures_volume_oi.compute"

    def _series_side_effect(*, engine, curve_family, contract_code, field_name, start_date, end_date=None):
        # The tool reads default_volume_field / default_open_interest_field
        # from config.yaml — currently "PX_VOLUME" / "OPEN_INT". Route
        # by field_name so a future YAML rename still gets the right
        # series.
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

    with patch(f"{target}.fetch_rolling_generic_series", side_effect=_series_side_effect), \
         patch(f"{target}.fetch_rolling_generic_reference", return_value=reference), \
         patch(f"{target}.date", _FrozenDateForFuturesVolumeOI):
        actual = calculate_futures_volume_oi(engine=None, params=params)

    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: {actual.get('error')!r}"
    )

    # 7. Compare with recorded expectation.
    _assert_equal(actual, fx["expected_output"], path="$")


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason="No fixtures found — see capture instructions in the README.",
)
def test_futures_volume_oi_parity_tamper_detected() -> None:
    """Negative control: if raw_volume_rows is mutated in memory before
    hashing, the tamper-detection step must reject it. Guarantees the
    parity test is actually checking the hash."""
    fixture_path = _FIXTURE_PATHS[0]
    with fixture_path.open() as f:
        fx = json.load(f)
    rows = list(fx["input"]["raw_volume_rows"])
    if not rows:
        pytest.skip("fixture has no volume rows")
    # Tamper: bump the latest volume value by 1 contract.
    tampered = list(rows)
    tampered[-1] = dict(tampered[-1])
    tampered[-1]["field_value"] = float(tampered[-1]["field_value"]) + 1.0

    recorded_hash = fx["capture"]["raw_rows_sha256"]
    tampered_hash = _hash_both(tampered, fx["input"]["raw_oi_rows"])
    assert tampered_hash != recorded_hash, (
        "tamper-detection negative control failed — hash did not change "
        "when raw_volume_rows was mutated. The parity-test hash "
        "implementation is broken."
    )
