"""
test_curve_spread_parity.py — Snapshot parity tests for calculate_curve_spread
==============================================================================

Locks in the *real production* output of ``calculate_curve_spread`` for
the three pilot test cases (UST 2s10s 365d, BUND 5s30s 90d, BTP 2s10s
730d).  Any commit that changes the math — directly or through a
primitive in ``shared/analytics/`` — must keep these tests green or come
with a deliberate, reviewed fixture regeneration.

The fixtures are captured against a live TimescaleDB by
``tests/fixtures/curve_spread_v1/_capture.py``; the test itself runs
fully offline by replaying captured ``raw_rows`` through a mocked
fetcher.

How it works
------------
For each fixture in ``tests/fixtures/curve_spread_v1/``:

1. Load the JSON.  Verify the captured ``raw_rows_sha256`` matches a
   freshly-computed hash of ``input.raw_rows`` — guards against fixture
   tampering.
2. Reconstruct the long-format DataFrame the tool's DB fetcher would
   return from ``input.raw_rows``.
3. Patch ``rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair``
   to return that DataFrame.  Patch the same module's ``date`` reference
   (``...curve_spread.compute.date``) with a subclass whose ``today()``
   returns ``input.frozen_today``, so the two ``date.today()`` callsites
   inside the tool become deterministic.  The patches target
   ``compute.py``'s namespace specifically because the package
   ``__init__.py`` only re-exports ``calculate_curve_spread`` and does
   NOT propagate ``compute.py``'s imports — see the package init's
   docstring "Note for tests" section.
4. Build a ``CurveSpreadInput`` from the recorded params and call
   ``calculate_curve_spread(engine=None, params=...)``.
5. Recursively compare the result against ``expected_output`` — strings
   and ints exact, floats within 1e-9 absolute tolerance, dicts must
   have identical keysets, lists identical lengths.

Why a tolerance and not byte-equal JSON?
----------------------------------------
``calculate_curve_spread`` already rounds every numeric output (z-score
to 4 dp, bps quantities to 2 dp), so on the same inputs the result
*should* be bit-identical between runs.  We still allow a 1e-9 absolute
tolerance to absorb harmless float-formatting differences across
pandas/numpy versions — anything bigger than that is real math drift
and the test fails.

Regenerating fixtures
---------------------
Only after a deliberate methodology change.  See
``tests/fixtures/curve_spread_v1/README.md``.
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

from rates_agent.sovereign_bonds.tools.curve_spread import calculate_curve_spread
from rates_agent.sovereign_bonds.tools.schemas import CurveSpreadInput


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "curve_spread_v1"

# Tight tolerance — outputs are pre-rounded so floats should be
# bit-identical in practice; this only absorbs harmless float-formatting
# noise from upstream library updates.
FLOAT_ABS_TOL = 1e-9


# ---------------------------------------------------------------------------
# Frozen-date helper
# ---------------------------------------------------------------------------

class _FrozenDateForCurveSpread(date):
    """``date`` subclass with ``today()`` returning a fixed value.

    Patched in place of
    ``rates_agent.sovereign_bonds.tools.curve_spread.compute.date``
    so the tool's two ``date.today()`` callsites in ``compute.py``
    become deterministic.  Patching the package init's namespace
    would be a no-op because the ``date`` import lives inside
    ``compute.py``, not on the package's __init__.

    Subtracting a ``timedelta`` from the value returned by ``today()``
    still yields a real ``date`` because ``today()`` returns the
    underlying ``date(...)`` instance, not the subclass.
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
    name so test ordering is stable across runs.

    Files whose name begins with ``_`` are considered private / scratch
    (e.g. ``_sanity_smoke.json`` from interactive debugging) and are
    skipped — only ``_capture.py`` is allowed to write into this
    directory and it always produces non-underscore filenames.
    """
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(
        p for p in FIXTURES_DIR.glob("*.json")
        if not p.name.startswith("_")
    )


_FIXTURE_PATHS = _discover_fixtures()
# Test IDs (e.g. "ust_2s10s_365d") — drives readable pytest output.
_FIXTURE_IDS = [p.stem for p in _FIXTURE_PATHS]


# ---------------------------------------------------------------------------
# Recursive deep comparator
# ---------------------------------------------------------------------------

def _assert_equal(actual: Any, expected: Any, *, path: str = "$") -> None:
    """Assert ``actual == expected`` recursively.

    Floats are compared with ``FLOAT_ABS_TOL`` absolute tolerance.  All
    other scalars must be exactly equal.  Dicts must have identical
    keysets.  Lists must have identical lengths.  Mismatches raise
    ``AssertionError`` with a JSON-ish path so the failure points to the
    exact field that drifted.
    """
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

    # bool is an int subclass in Python — handle it before the float branch.
    if isinstance(expected, bool):
        assert actual is expected, f"{path}: expected {expected!r}, got {actual!r}"
        return

    if isinstance(expected, float):
        assert actual is not None, f"{path}: expected {expected!r}, got None"
        # NaN comparison: treat both-NaN as equal (the tool never emits NaN
        # in practice, but be defensive).
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

    # Fallback for any other scalar type — defensive, shouldn't be reached
    # given the tool only emits dicts/lists/floats/ints/strs/None.
    assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"


# ---------------------------------------------------------------------------
# Fixture-driven test
# ---------------------------------------------------------------------------

def _canonicalise_rows(rows: list[dict]) -> str:
    """Match the canonical encoding ``_capture.py`` uses for hashing.

    Keeping these two implementations identical is critical: a mismatch
    here would make every parity run fail with a hash error.  The two
    encodings agree because both:
      - sort dict keys,
      - use the most compact separators (no whitespace),
      - emit UTF-8.
    """
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/curve_spread_v1/_capture.py` against the "
        "live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_curve_spread_parity(fixture_path: Path) -> None:
    """Run the captured inputs through ``calculate_curve_spread`` with
    the DB fetcher mocked and the date frozen, and assert byte-equal
    output (modulo float tolerance) against the recorded fixture."""

    with fixture_path.open() as f:
        fx = json.load(f)

    # 1. Tamper-detection: verify the recorded raw_rows hash matches a
    #    fresh recompute.  If raw_rows were edited by hand without
    #    re-running the capture script, the hash diverges and we abort
    #    rather than silently accepting altered baseline inputs.
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
        "raw_rows have been edited without re-running the capture script. "
        "Either restore the original rows or regenerate the fixture with "
        "`python tests/fixtures/curve_spread_v1/_capture.py`."
    )

    # 2. Reconstruct the long-format DataFrame the fetcher would return.
    raw_df = pd.DataFrame(raw_rows)
    # Mirror the type contract of the real fetch_tenor_pair output.
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"]).dt.date
    raw_df["tenor"] = raw_df["tenor"].astype(str)
    raw_df["field_value"] = raw_df["field_value"].astype(float)

    # 3. Build the input.
    params = CurveSpreadInput(**fx["input"]["params"])

    # 4. Freeze the date.
    _FrozenDateForCurveSpread._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    # 5. Patch and run.
    #
    # Patches target the tool's COMPUTE module specifically, not the
    # package init.  The package init re-exports calculate_curve_spread
    # but does NOT propagate compute.py's imports (fetch_tenor_pair,
    # date) into its own namespace, so patching the package init would
    # be a no-op.  See rates_agent/sovereign_bonds/tools/curve_spread/
    # __init__.py "Note for tests" docstring section.
    target_module = "rates_agent.sovereign_bonds.tools.curve_spread.compute"
    with patch(f"{target_module}.fetch_tenor_pair", return_value=raw_df), \
         patch(f"{target_module}.date", _FrozenDateForCurveSpread):
        # engine is unused because fetch_tenor_pair is mocked.
        actual = calculate_curve_spread(engine=None, params=params)

    # The tool should never return an error path on these well-formed
    # captured inputs; if it does, surface the message.
    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: {actual.get('error')!r}"
    )

    # 6. Strip additive top-level fields the parity contract is
    # explicitly NOT covering.  ``canonical_time_series`` was added by
    # the legacy-TimeSeries tech-debt cleanup as an intentionally
    # additive field; the parity tests pin the WIRE-FROZEN shape that
    # the frontend reads (``current_metrics`` + bespoke ``time_series``),
    # not every key the tool emits.  When the canonical-TimeSeries
    # contract needs its own parity coverage, that goes in a separate
    # canonical_time_series test rather than rolling into the legacy
    # baselines.
    actual_for_parity = {
        k: v for k, v in actual.items() if k != "canonical_time_series"
    }

    # 7. Compare with recorded expectation.
    _assert_equal(actual_for_parity, fx["expected_output"], path="$")
