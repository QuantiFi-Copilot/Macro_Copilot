"""
test_inflation_swap_rate_level_parity.py — Snapshot parity tests for
                                            calculate_inflation_swap_rate_level
================================================================================

Locks in the *real production* output of
``calculate_inflation_swap_rate_level`` for three representative ZCIS
pillars (USD_ZCIS 5Y 365d, EUR_ZCIS 10Y 730d, GBP_ZCIS 5Y 365d).  Any
commit that changes the math — directly or through a primitive in
``shared/analytics/`` or a YAML edit to the bundled ``config.yaml`` —
must keep these tests green or come with a deliberate, reviewed
fixture regeneration.

PR15 backfill — this primitive shipped before the parity-fixture
discipline was load-bearing.  Modelled on
``tests/test_curve_spread_parity.py``.

The fixtures are captured against a live TimescaleDB by
``tests/fixtures/inflation_swap_rate_level_v1/_capture.py``; the test
itself runs fully offline by replaying captured ``raw_rows`` through a
mocked fetcher.

How it works
------------
For each fixture in ``tests/fixtures/inflation_swap_rate_level_v1/``:

1. Load the JSON.  Verify the captured ``raw_rows_sha256`` matches a
   freshly-computed hash of ``input.raw_rows`` — guards against fixture
   tampering.
2. Reconstruct the long-format DataFrame the tool's DB fetcher would
   return from ``input.raw_rows``.  ``fetch_zcis_single_pillar``
   returns EIGHT columns
   (``trade_date``, ``field_value``, ``vendor_ticker``,
   ``pricing_type``, ``inflation_index_family``, ``index_lag``,
   ``interpolation``, ``underlying_index``), all of which are required
   by ``_resolve_reference_metadata`` downstream.  The replayed frame
   reproduces all eight.
3. Patch ``rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar``
   to return that DataFrame.  Patch the same module's ``date`` reference
   with a subclass whose ``today()`` returns ``input.frozen_today``, so
   the one ``date.today()`` callsite inside the tool becomes
   deterministic.  The patches target ``compute.py``'s namespace
   specifically because the package ``__init__.py`` only re-exports
   ``calculate_inflation_swap_rate_level`` and ``fetch_zcis_single_pillar``
   and does NOT propagate ``compute.py``'s ``date`` import — see the
   package init's "Note for tests" section.
4. Build an ``InflationSwapRateLevelInput`` from the recorded params
   and call ``calculate_inflation_swap_rate_level(engine=None,
   params=...)``.
5. Recursively compare the result against ``expected_output`` — strings
   and ints exact, floats within 1e-9 absolute tolerance, dicts must
   have identical keysets, lists identical lengths.

Why a tolerance and not byte-equal JSON?
----------------------------------------
``calculate_inflation_swap_rate_level`` rounds every numeric output via
the ``yield_round_decimals`` / ``z_score_round_decimals`` /
``high_low_round_decimals`` conventions (currently all 4 dp), so on the
same inputs the result *should* be bit-identical between runs.  The
1e-9 absolute tolerance absorbs harmless float-formatting differences
across pandas / numpy versions — anything bigger than that is real
math drift and the test fails.

Regenerating fixtures
---------------------
Only after a deliberate methodology change.  See
``tests/fixtures/inflation_swap_rate_level_v1/README.md``.
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

from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
    InflationSwapRateLevelInput,
    calculate_inflation_swap_rate_level,
)


FIXTURES_DIR = (
    Path(__file__).parent / "fixtures" / "inflation_swap_rate_level_v1"
)

FLOAT_ABS_TOL = 1e-9


# ---------------------------------------------------------------------------
# Frozen-date helper
# ---------------------------------------------------------------------------

class _FrozenDateForZcisLevel(date):
    """``date`` subclass with ``today()`` returning a fixed value.

    Patched in place of
    ``rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date``
    so the tool's one ``date.today()`` callsite in ``compute.py`` becomes
    deterministic.  Patching the package init's namespace would be a
    no-op because the ``date`` import lives inside ``compute.py``, not
    on the package's ``__init__``.

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
# Fixture-driven test
# ---------------------------------------------------------------------------

def _canonicalise_rows(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/inflation_swap_rate_level_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_inflation_swap_rate_level_parity(fixture_path: Path) -> None:
    """Run the captured inputs through
    ``calculate_inflation_swap_rate_level`` with the DB fetcher mocked
    and the date frozen, and assert byte-equal output (modulo float
    tolerance) against the recorded fixture."""

    with fixture_path.open() as f:
        fx = json.load(f)

    # 1. Tamper-detection.
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
        "`python tests/fixtures/inflation_swap_rate_level_v1/_capture.py`."
    )

    # 2. Reconstruct the long-format DataFrame the fetcher would return.
    #    fetch_zcis_single_pillar returns 8 columns; all must be
    #    present for _resolve_reference_metadata to succeed.
    raw_df = pd.DataFrame(raw_rows)
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"]).dt.date
    raw_df["field_value"] = raw_df["field_value"].astype(float)
    # The remaining six columns are string-or-None; pandas handles
    # them as object dtype which matches the live fetcher's
    # SQLAlchemy-returned row shape.

    # 3. Build the input.
    params = InflationSwapRateLevelInput(**fx["input"]["params"])

    # 4. Freeze the date.
    _FrozenDateForZcisLevel._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    # 5. Patch and run.
    target_module = (
        "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute"
    )
    with patch(
        f"{target_module}.fetch_zcis_single_pillar",
        return_value=raw_df,
    ), patch(f"{target_module}.date", _FrozenDateForZcisLevel):
        actual = calculate_inflation_swap_rate_level(
            engine=None, params=params,
        )

    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: "
        f"{actual.get('error')!r}"
    )

    # 6. Compare with recorded expectation.
    _assert_equal(actual, fx["expected_output"], path="$")
