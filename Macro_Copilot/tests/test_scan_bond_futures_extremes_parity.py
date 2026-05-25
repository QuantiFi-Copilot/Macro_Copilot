"""
test_scan_bond_futures_extremes_parity.py — Snapshot parity tests for
                                              the bond-futures
                                              universe-wide extremes
                                              scan
=====================================================================

Locks in the *real production* output of
``calculate_scan_bond_futures_extremes`` for one or more
representative scan invocations (default: the full V1 universe with
top_n=3 / min_abs_z_score=0). Any commit that changes the math —
directly or through a primitive in ``shared/analytics/`` or via a
YAML-convention edit — must keep this test green or come with a
deliberate, reviewed fixture regeneration.

PR15 binds new primitives from day-one — ``scan_bond_futures_extremes``
is a new primitive, so the parity coverage ships with it rather than
being grandfathered debt.

The fixtures are captured against a live TimescaleDB by
``tests/fixtures/scan_bond_futures_extremes_v1/_capture.py``; the
test itself runs fully offline by replaying captured row sets
through a mocked fetcher.

How it works
------------
For each fixture in ``tests/fixtures/scan_bond_futures_extremes_v1/``:

1. Load the JSON. Verify the captured ``raw_rows_sha256`` matches a
   freshly-computed hash of ``input.raw_price_rows`` +
   ``input.raw_volume_rows`` + ``input.raw_oi_rows`` — guards
   against fixture tampering.
2. Reconstruct the three long-format DataFrames the tool's DB
   fetcher would return for PX_LAST / PX_VOLUME / OPEN_INT.
3. Patch
   ``rates_agent.bond_futures.tools.scan_bond_futures_extremes.compute.fetch_rolling_generic_universe_series``
   with a side-effect that routes by ``field_name``, and patch the
   same module's ``date`` reference with a subclass whose
   ``today()`` returns ``input.frozen_today``.
4. Build a ``ScanBondFuturesExtremesInput`` from the recorded params
   and call ``calculate_scan_bond_futures_extremes(engine=None,
   params=...)``.
5. Recursively compare the result against ``expected_output`` —
   strings and ints exact, floats within 1e-9 absolute tolerance,
   dicts must have identical keysets, lists identical lengths.

Why a tolerance and not byte-equal JSON?
----------------------------------------
``calculate_scan_bond_futures_extremes`` already rounds every numeric
output (z-score to 4 dp, prices to 6 dp, counts to 0 dp), so on the
same inputs the result *should* be bit-identical between runs. We
still allow a 1e-9 absolute tolerance to absorb harmless float-
formatting differences across pandas / numpy versions — anything
bigger than that is real math drift and the test fails.

Regenerating fixtures
---------------------
Only after a deliberate methodology change. See
``tests/fixtures/scan_bond_futures_extremes_v1/README.md``.
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

from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
    ScanBondFuturesExtremesInput,
    calculate_scan_bond_futures_extremes,
)


FIXTURES_DIR = (
    Path(__file__).parent / "fixtures" / "scan_bond_futures_extremes_v1"
)

# Tight tolerance — outputs are pre-rounded so floats should be
# bit-identical in practice; this only absorbs harmless float-
# formatting noise from upstream library updates.
FLOAT_ABS_TOL = 1e-9


# ---------------------------------------------------------------------------
# Frozen-date helper
# ---------------------------------------------------------------------------

class _FrozenDateForScan(date):
    """``date`` subclass with ``today()`` returning a fixed value.

    Patched in place of
    ``rates_agent.bond_futures.tools.scan_bond_futures_extremes.compute.date``
    so the tool's ``date.today()`` callsite in ``compute.py`` becomes
    deterministic. Round-2 mandatory-fix #2: compute now only calls
    ``date.today()`` when the input's ``as_of_date`` is None — the
    pinned-anchor fixture supplies ``as_of_date`` explicitly, so this
    frozen-date patch becomes a belt-and-braces safety net rather
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
    """Match the canonical encoding ``_capture.py`` uses for hashing."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_three(
    price_rows: list[dict],
    volume_rows: list[dict],
    oi_rows: list[dict],
) -> str:
    """Hash the concatenated canonicalisation of the three row sets.

    Must match ``_capture.py::_hash_three`` byte-for-byte — label
    prefixes on each list disambiguate the streams so a swap of
    (e.g.) price ↔ volume rows changes the hash.
    """
    payload = (
        "PRICE|" + _canonicalise(price_rows)
        + "|VOL|" + _canonicalise(volume_rows)
        + "|OI|" + _canonicalise(oi_rows)
    )
    return _sha256_hex(payload)


# ---------------------------------------------------------------------------
# Fixture-driven test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/scan_bond_futures_extremes_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_scan_bond_futures_extremes_parity(fixture_path: Path) -> None:
    """Run the captured inputs through
    ``calculate_scan_bond_futures_extremes`` with the DB fetcher
    mocked and the date frozen, and assert byte-equal output (modulo
    float tolerance) against the recorded fixture."""

    with fixture_path.open() as f:
        fx = json.load(f)

    # 1. Tamper-detection: verify the recorded raw_rows hash matches
    #    a fresh recompute.
    raw_price_rows = fx["input"]["raw_price_rows"]
    raw_volume_rows = fx["input"]["raw_volume_rows"]
    raw_oi_rows = fx["input"]["raw_oi_rows"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256 — "
        "fixture must be regenerated by _capture.py"
    )
    actual_hash = _hash_three(raw_price_rows, raw_volume_rows, raw_oi_rows)
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch.\n"
        f"  recorded: {expected_hash}\n"
        f"  computed: {actual_hash}\n"
        "raw rows have been edited without re-running the capture "
        "script. Either restore the original rows or regenerate the "
        "fixture with "
        "`python tests/fixtures/scan_bond_futures_extremes_v1/_capture.py`."
    )

    # 2. Reconstruct the long-format DataFrames the fetcher returns.
    def _to_df(rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(
                columns=[
                    "trade_date", "curve_family", "contract_code",
                    "tenor", "field_value",
                ]
            )
        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["field_value"] = df["field_value"].astype(float)
        return df

    raw_price_df = _to_df(raw_price_rows)
    raw_volume_df = _to_df(raw_volume_rows)
    raw_oi_df = _to_df(raw_oi_rows)

    # 3. Build the input.
    params = ScanBondFuturesExtremesInput(**fx["input"]["params"])

    # 4. Freeze the date.
    _FrozenDateForScan._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    # 5. Patch and run. Patches target the COMPUTE module specifically.
    target = "rates_agent.bond_futures.tools.scan_bond_futures_extremes.compute"

    def _universe_side_effect(
        *, engine, curve_families, field_name, start_date, end_date=None,
    ):
        # The tool reads default_price_field / default_volume_field /
        # default_open_interest_field from config.yaml — currently
        # PX_LAST / PX_VOLUME / OPEN_INT. Route by field_name so a
        # future YAML rename gets the right series. ``end_date`` is
        # the round-3 SQL upper bound (anchors the fetch at the
        # requested as-of); the replay accepts and ignores it
        # because the captured rows are already truncated through
        # the in-Python ``cap_at`` step, so the parity expectation
        # carries identical numerics regardless of whether the SQL
        # upper bound is active in the mocked path.
        if field_name == "PX_LAST":
            return raw_price_df
        if field_name == "PX_VOLUME":
            return raw_volume_df
        if field_name == "OPEN_INT":
            return raw_oi_df
        raise AssertionError(
            f"parity replay: fetcher was called with field_name="
            f"{field_name!r}, but the captured fixture only knows "
            "PX_LAST / PX_VOLUME / OPEN_INT. The YAML's "
            "default_price_field / default_volume_field / "
            "default_open_interest_field has diverged from what the "
            "fixture was captured against — regenerate the fixture "
            "or restore the YAML."
        )

    # Round-3 future-anchor guard: when the captured params carry an
    # explicit ``as_of_date`` (the deterministic-anchor fixture
    # does — pinned at 2026-04-08), compute probes the universe's
    # last observed trade_date before fetching. Mock the probe to
    # return the captured frozen-today value so the guard sees the
    # requested anchor as <= the universe max and lets the scan
    # proceed (the captured fixture by construction was always
    # within-data; an out-of-range capture would be a fixture bug
    # that the live DB ``MAX(trade_date)`` query during capture
    # would have surfaced).
    def _max_date_side_effect(*, engine, curve_families):
        return _FrozenDateForScan._frozen_value

    with patch(
        f"{target}.fetch_rolling_generic_universe_series",
        side_effect=_universe_side_effect,
    ), patch(
        f"{target}.fetch_rolling_generic_universe_max_date",
        side_effect=_max_date_side_effect,
    ), patch(
        f"{target}.date", _FrozenDateForScan,
    ):
        actual = calculate_scan_bond_futures_extremes(
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
def test_scan_bond_futures_extremes_parity_tamper_detected() -> None:
    """Negative control: if any of the three raw-row lists is mutated
    in memory before hashing, the tamper-detection step must reject
    it. Guarantees the parity test is actually checking the hash."""
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
    tampered_hash = _hash_three(
        tampered, fx["input"]["raw_volume_rows"], fx["input"]["raw_oi_rows"],
    )
    assert tampered_hash != recorded_hash, (
        "tamper-detection negative control failed — hash did not "
        "change when raw_price_rows was mutated. The parity-test "
        "hash implementation is broken."
    )
