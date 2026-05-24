"""
test_otr_ofr_spread_parity.py — Snapshot parity tests for otr_ofr_spread
=========================================================================

Locks in the *real production* output of ``calculate_otr_ofr_spread``
for the documented pilot cases (see
``tests/fixtures/otr_ofr_spread_v1/_capture.py``).  Any commit that
changes the math — directly or through compute() / the SQL fetcher
— must keep these tests green or come with a deliberate, reviewed
fixture regeneration.

The fixtures may be captured against a live TimescaleDB by
``tests/fixtures/otr_ofr_spread_v1/_capture.py`` once the resolver
populates otr_history (TD #27a forward-only); the ones shipped at v1
land are SYNTHETIC, locking in regression behaviour against
representative SCD2 + market_data rows.  See the fixture README for
the synthetic-vs-live provenance disclosure (P5).

The test itself runs fully offline by replaying captured
``raw_rows`` through a mocked fetcher.

How it works
------------
For each fixture in ``tests/fixtures/otr_ofr_spread_v1/``:

1. Load the JSON.  Verify the captured ``raw_rows_sha256`` matches a
   freshly-computed hash of ``input.raw_rows`` — guards against fixture
   tampering.
2. Reconstruct the long-format DataFrame the tool's DB fetcher would
   return from ``input.raw_rows``.
3. Patch ``rates_agent.sovereign_bonds.tools.otr_ofr_spread.compute.fetch_otr_ofr_yield_pair``
   to return that DataFrame.  Patch the same module's ``date``
   reference with a subclass whose ``today()`` returns
   ``input.frozen_today`` so ``date.today()`` is deterministic.
4. Build an ``OtrOfrSpreadInput`` from the recorded params and call
   ``calculate_otr_ofr_spread(engine=None, params=...)``.
5. Recursively compare the result against ``expected_output`` —
   strings exact, floats within ``1e-9`` absolute tolerance, dicts
   must have identical keysets, lists identical lengths.

Why a tolerance and not byte-equal JSON?
----------------------------------------
``calculate_otr_ofr_spread`` already rounds every numeric output
(spread_bps to 2 dp, z-score to 4 dp), so on the same inputs the
result *should* be bit-identical between runs.  We still allow a
``1e-9`` absolute tolerance to absorb harmless float-formatting
differences across pandas/numpy versions — anything bigger than that
is real math drift and the test fails.

Regenerating fixtures
---------------------
Only after a deliberate methodology change.  See
``tests/fixtures/otr_ofr_spread_v1/README.md``.
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

from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (
    OtrOfrSpreadInput,
    calculate_otr_ofr_spread,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "otr_ofr_spread_v1"
FLOAT_ABS_TOL = 1e-9


# ---------------------------------------------------------------------------
# Frozen-date helper
# ---------------------------------------------------------------------------

class _FrozenDateForOtrOfr(date):
    """``date`` subclass with ``today()`` returning a fixed value.

    Patched in place of
    ``rates_agent.sovereign_bonds.tools.otr_ofr_spread.compute.date``
    so the tool's ``date.today()`` callsites become deterministic.
    """

    _frozen_value: date = date(2000, 1, 1)  # overridden per test invocation

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------

def _discover_fixtures() -> list[Path]:
    """Return every ``*.json`` file in the fixtures directory, sorted by
    name so test ordering is stable across runs.  Underscore-prefixed
    files are treated as private (``_capture.py`` lives alongside)."""
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
# Fixture replay
# ---------------------------------------------------------------------------

def _canonicalise_rows(rows: list[dict]) -> str:
    """Match the canonical encoding ``_capture.py`` uses for hashing."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _replay_rows_to_df(raw_rows: list[dict]) -> pd.DataFrame:
    """Coerce the JSON-serialised rows back to the DataFrame shape the
    primitive's fetch_otr_ofr_yield_pair returns: ``date`` objects for
    ``trade_date``, ``int`` (nullable) for FK columns, ``float`` for
    the yield columns.  ``None`` preserved for nullable columns.
    """
    coerced = []
    for r in raw_rows:
        coerced.append({
            "trade_date": date.fromisoformat(r["trade_date"]),
            "otr_instrument_id": int(r["otr_instrument_id"]),
            "ofr_instrument_id": (
                int(r["ofr_instrument_id"])
                if r["ofr_instrument_id"] is not None else None
            ),
            "otr_yield": (
                float(r["otr_yield"])
                if r["otr_yield"] is not None else None
            ),
            "ofr_yield": (
                float(r["ofr_yield"])
                if r["ofr_yield"] is not None else None
            ),
        })
    return pd.DataFrame(coerced)


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/otr_ofr_spread_v1/_capture.py` against "
        "the live DB to generate, or ship synthetic v1 fixtures."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_otr_ofr_spread_parity(fixture_path: Path) -> None:
    """Replay captured inputs through ``calculate_otr_ofr_spread`` with
    the fetcher mocked and the date frozen; assert byte-equal output
    against the recorded fixture (within ``1e-9`` float tolerance)."""

    with fixture_path.open() as f:
        fx = json.load(f)

    raw_rows = fx["input"]["raw_rows"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256 — "
        "fixture must be regenerated by _capture.py"
    )
    actual_hash = _sha256_hex(_canonicalise_rows(raw_rows))
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch.\n"
        f"  recorded: {expected_hash}\n"
        f"  computed: {actual_hash}\n"
        "raw_rows have been edited without re-running the capture script."
    )

    df = _replay_rows_to_df(raw_rows)
    params = OtrOfrSpreadInput(**fx["input"]["params"])

    _FrozenDateForOtrOfr._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    module = "rates_agent.sovereign_bonds.tools.otr_ofr_spread.compute"
    with patch(f"{module}.fetch_otr_ofr_yield_pair", return_value=df), patch(
        f"{module}.date", _FrozenDateForOtrOfr,
    ):
        actual = calculate_otr_ofr_spread(engine=None, params=params)

    # Some fixtures pin the error-envelope shape (honest absence on
    # pre-resolver windows); others pin the full happy path.
    expected_output = fx["expected_output"]
    if "error" in expected_output:
        assert "error" in actual, (
            f"{fixture_path.name}: fixture expects error envelope but tool "
            f"returned a happy-path output: {actual!r}"
        )

    _assert_equal(actual, expected_output, path="$")
