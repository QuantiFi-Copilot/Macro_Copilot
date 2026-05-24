"""
test_nfp_surprise_parity.py — Snapshot parity tests for nfp_surprise
=====================================================================

Locks in the *real production* output of ``calculate_nfp_surprise``
for the documented pilot case (US NFP).  Any commit that changes the
math — directly or through compute() / the event-calendar fetcher —
must keep these tests green or come with a deliberate, reviewed
fixture regeneration.

The fixtures shipped at v1 land are LIVE-DB CAPTURES carrying
``capture_method=live_db_v1`` — strict PR15 compliance from day one
(mirroring cpi_surprise's post-Codex-review approach).  The
``empty_pre_extractor.json`` fixture is intentionally synthetic
because the live DB cannot reproduce its precondition (zero realised
NFP rows for US) now that the US slot has populated data.

Replays captured ``raw_rows`` through a mocked fetcher + frozen date;
asserts byte-equal output within ``1e-9`` float tolerance.
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

from rates_agent.sovereign_bonds.tools.nfp_surprise import (
    NfpSurpriseInput,
    calculate_nfp_surprise,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfp_surprise_v1"
FLOAT_ABS_TOL = 1e-9


class _FrozenDateForNfp(date):
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
        for i, (a, e) in enumerate(zip(actual, expected)):
            _assert_equal(a, e, path=f"{path}[{i}]")
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


def _canonicalise_rows(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_NULLABLE_FLOAT_COLS = (
    "actual",
    "consensus_median",
    "consensus_high",
    "consensus_low",
    "prior",
    "revised_prior",
    "surprise_std_dev",
)


def _replay_rows_to_df(raw_rows: list[dict]) -> pd.DataFrame:
    coerced = []
    for r in raw_rows:
        row = {
            "event_id": int(r["event_id"]),
            "event_type": r["event_type"],
            "event_category": r["event_category"],
            "country": r["country"],
            "currency": r.get("currency"),
            "release_date": date.fromisoformat(r["release_date"]),
            "release_time": None,
            "period": r.get("period"),
        }
        for col in _NULLABLE_FLOAT_COLS:
            v = r.get(col)
            row[col] = None if v is None else float(v)
        coerced.append(row)
    return pd.DataFrame(coerced)


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/nfp_surprise_v1/_capture.py` against "
        "the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_nfp_surprise_parity(fixture_path: Path) -> None:
    """Replay captured inputs through calculate_nfp_surprise with the
    fetcher mocked and the date frozen; assert byte-equal output
    against the recorded fixture (within 1e-9 float tolerance)."""

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
    params = NfpSurpriseInput(**fx["input"]["params"])

    _FrozenDateForNfp._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    module = "rates_agent.sovereign_bonds.tools.nfp_surprise.compute"
    with patch(
        f"{module}.fetch_economic_release_surprises", return_value=df,
    ), patch(f"{module}.date", _FrozenDateForNfp):
        actual = calculate_nfp_surprise(engine=None, params=params)

    expected_output = fx["expected_output"]
    if "error" in expected_output:
        assert "error" in actual, (
            f"{fixture_path.name}: fixture expects error envelope but "
            f"tool returned a happy-path output: {actual!r}"
        )

    _assert_equal(actual, expected_output, path="$")
