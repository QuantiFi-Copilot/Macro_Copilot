"""
test_asset_swap_spread_parity.py — Snapshot parity tests for
get_asset_swap_spread.
=============================================================

Locks in the *deterministic* output of ``get_asset_swap_spread`` for
the documented test cases (see
``tests/fixtures/asset_swap_spread_v1/README.md``).  Any commit that
changes the math — directly or through compute() — must keep these
tests green or come with a deliberate, reviewed fixture regeneration.

The fixtures are deterministic synthetic captures (v1; see README).
The test runs offline by replaying captured ``raw_rows`` + identity
through a mocked engine + patched fetcher.

How it works
------------
For each fixture in ``tests/fixtures/asset_swap_spread_v1/*.json``:

1. Load the JSON.  Verify ``capture.raw_rows_sha256`` matches a fresh
   hash of ``input.raw_rows`` — guards against fixture tampering.
2. Build a MagicMock engine whose ``.connect().execute(...).mappings().first()``
   returns ``input.identity_row`` (for the instrument_master lookup
   in compute._fetch_bond_identity).
3. Patch ``rates_agent.ois.tools.asset_swap_spread.compute.fetch_single_bond_series``
   to return the captured ``raw_rows`` as a DataFrame.
4. Patch ``rates_agent.ois.tools.asset_swap_spread.compute.date`` with
   a subclass returning ``input.frozen_today`` from ``today()``.
5. Build an ``AssetSwapSpreadInput`` from the recorded params, call
   ``get_asset_swap_spread(engine=mock, params=...)``, and recursively
   compare against ``expected_output``.

Why no rounding tolerance on the RAW ASW value?
-----------------------------------------------
Per the catalog guardrail, the raw ASW value is preserved bit-exact
from the DB (1e-9 SQL parity).  The parity test enforces the same
discipline — float values match within ``1e-9`` (effectively exact
for double-precision floats over the small magnitude range of bps
spreads).
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rates_agent.ois.tools.asset_swap_spread import (
    AssetSwapSpreadInput,
    get_asset_swap_spread,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "asset_swap_spread_v1"
FLOAT_ABS_TOL = 1e-9


# ---------------------------------------------------------------------------
# Frozen-date helper
# ---------------------------------------------------------------------------


class _FrozenDateForAsw(date):
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
        p for p in FIXTURES_DIR.glob("*.json") if not p.name.startswith("_")
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
# Helpers
# ---------------------------------------------------------------------------


def _canonicalise_rows(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_engine_mock(identity_row: dict) -> MagicMock:
    engine = MagicMock()
    conn = MagicMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = identity_row
    conn.execute.return_value = result
    engine.connect.return_value.__enter__.return_value = conn
    return engine


def _raw_rows_to_df(raw_rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(raw_rows)
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["field_value"] = df["field_value"].astype(float)
    return df


# ---------------------------------------------------------------------------
# Fixture-replay test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/asset_swap_spread_v1/_regenerate.py` to "
        "generate the v1 synthetic fixtures."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_asset_swap_spread_parity(fixture_path: Path) -> None:
    with fixture_path.open() as f:
        fx = json.load(f)

    raw_rows = fx["input"]["raw_rows"]
    capture = fx.get("capture", {})
    expected_hash = capture.get("raw_rows_sha256")
    assert expected_hash, (
        f"{fixture_path.name}: missing capture.raw_rows_sha256 — "
        "fixture must be regenerated via _regenerate.py"
    )
    actual_hash = _sha256_hex(_canonicalise_rows(raw_rows))
    assert actual_hash == expected_hash, (
        f"{fixture_path.name}: raw_rows_sha256 mismatch.\n"
        f"  recorded: {expected_hash}\n"
        f"  computed: {actual_hash}\n"
        "raw_rows have been edited without regenerating."
    )

    # Re-hydrate the identity row (dates as date objects).
    identity_row = dict(fx["input"]["identity_row"])
    if identity_row.get("maturity_date") is not None:
        identity_row["maturity_date"] = date.fromisoformat(
            identity_row["maturity_date"]
        )
    engine = _build_engine_mock(identity_row)

    raw_df = _raw_rows_to_df(raw_rows)

    params_dict = dict(fx["input"]["params"])
    if params_dict.get("as_of_date") is not None:
        params_dict["as_of_date"] = date.fromisoformat(params_dict["as_of_date"])
    params = AssetSwapSpreadInput(**params_dict)

    _FrozenDateForAsw._frozen_value = date.fromisoformat(fx["input"]["frozen_today"])

    target = "rates_agent.ois.tools.asset_swap_spread.compute"
    with patch(f"{target}.fetch_single_bond_series", return_value=raw_df), patch(
        f"{target}.date", _FrozenDateForAsw
    ):
        actual = get_asset_swap_spread(engine=engine, params=params)

    assert "error" not in actual, (
        f"Tool returned an error for {fixture_path.name}: {actual.get('error')!r}"
    )

    _assert_equal(actual, fx["expected_output"], path="$")
