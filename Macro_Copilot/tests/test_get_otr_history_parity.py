"""
test_get_otr_history_parity.py — Snapshot parity tests for get_otr_history
==========================================================================

Locks in the *real production* output of ``get_otr_history`` for the
documented pilot cases (see ``tests/fixtures/get_otr_history_v1/_capture.py``).
Any commit that changes the math — directly or through compute() /
the SCD2 query — must keep these tests green or come with a deliberate,
reviewed fixture regeneration.

The fixtures are captured against a live TimescaleDB by
``tests/fixtures/get_otr_history_v1/_capture.py``; the test itself runs
fully offline by replaying captured ``raw_rows`` through a mocked
engine.

How it works
------------
For each fixture in ``tests/fixtures/get_otr_history_v1/``:

1. Load the JSON.  Verify the captured ``raw_rows_sha256`` matches a
   freshly-computed hash of ``input.raw_rows`` — guards against fixture
   tampering.
2. Build a MagicMock engine whose ``.connect().execute().mappings().all()``
   returns the captured ``raw_rows`` (coerced back to the DB row shape
   the primitive expects — ``date`` for the dates, ``int`` for the FK).
3. Patch ``rates_agent.sovereign_bonds.tools.get_otr_history.compute.date``
   with a subclass whose ``today()`` returns ``input.frozen_today``, so
   ``date.today()`` inside the tool is deterministic.
4. Build an ``OtrHistoryInput`` from the recorded params and call
   ``get_otr_history(engine=mock_engine, params=...)``.
5. Recursively compare the result against ``expected_output`` — strings,
   ints, and None exact; lists must have identical lengths.  No floats
   appear in this primitive's output, so the tolerance branch is
   defensive only.

Why no tolerance?
-----------------
``get_otr_history`` emits no numeric methodology output — only
categorical identity fields and ISO date strings.  Byte-equal
comparison is the right check.

Regenerating fixtures
---------------------
Only after a deliberate methodology change.  See
``tests/fixtures/get_otr_history_v1/README.md``.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.get_otr_history import (
    OtrHistoryInput,
    get_otr_history,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "get_otr_history_v1"
FLOAT_ABS_TOL = 1e-9  # Defensive — this primitive emits no floats.


# ---------------------------------------------------------------------------
# Frozen-date helper
# ---------------------------------------------------------------------------

class _FrozenDateForOtrHistory(date):
    """``date`` subclass with ``today()`` returning a fixed value.

    Patched in place of
    ``rates_agent.sovereign_bonds.tools.get_otr_history.compute.date``
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
# Fixture-replay test
# ---------------------------------------------------------------------------

def _canonicalise_rows(rows: list[dict]) -> str:
    """Match the canonical encoding ``_capture.py`` uses for hashing."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _replay_rows_to_db_shape(raw_rows: list[dict]) -> list[dict]:
    """Coerce the JSON-serialised rows back to the DB row shape the
    primitive expects: ``date`` objects for date columns, ``int`` for
    the FK, ``None`` preserved for nullable columns."""
    coerced = []
    for r in raw_rows:
        coerced.append({
            "effective_from": date.fromisoformat(r["effective_from"]),
            "effective_to": (
                date.fromisoformat(r["effective_to"])
                if r["effective_to"] is not None else None
            ),
            "otr_instrument_id": int(r["otr_instrument_id"]),
            "cusip": r["cusip"],
            "isin": r["isin"],
            "vendor_ticker": r["vendor_ticker"],
            "maturity_date": (
                date.fromisoformat(r["maturity_date"])
                if r["maturity_date"] is not None else None
            ),
        })
    return coerced


def _build_engine_mock(rows: list[dict]) -> MagicMock:
    mock_engine = MagicMock(name="engine")
    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = rows
    mock_conn.execute.return_value = mock_result
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    return mock_engine


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/get_otr_history_v1/_capture.py` against "
        "the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_get_otr_history_parity(fixture_path: Path) -> None:
    """Replay captured inputs through ``get_otr_history`` with the DB
    engine mocked and the date frozen, and assert byte-equal output
    against the recorded fixture."""

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

    # Reconstruct the DB row shape the primitive's SQL fetcher returns
    # and build a MagicMock engine that yields it.
    db_rows = _replay_rows_to_db_shape(raw_rows)
    mock_engine = _build_engine_mock(db_rows)

    params = OtrHistoryInput(**fx["input"]["params"])

    _FrozenDateForOtrHistory._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    target_module = "rates_agent.sovereign_bonds.tools.get_otr_history.compute"
    with patch(f"{target_module}.date", _FrozenDateForOtrHistory):
        actual = get_otr_history(engine=mock_engine, params=params)

    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: {actual.get('error')!r}"
    )

    _assert_equal(actual, fx["expected_output"], path="$")
