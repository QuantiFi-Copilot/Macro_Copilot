"""
test_wirp_meeting_pricing_parity.py — Snapshot parity tests for WIRP
====================================================================

Locks in the *real production* output of ``calculate_wirp_meeting_pricing``
for the four central banks (FOMC / ECB / BOE / BOJ).  Live-DB
captures with ``capture_method=live_db_v1`` — strict PR15 compliance
from day one (mirroring cpi_surprise's post-Codex-review approach
+ nfp_surprise's day-one pattern).

Replays captured ``raw_rows`` through a mocked fetcher + frozen
date; asserts byte-equal output within ``1e-9`` float tolerance.
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

from rates_agent.ois.tools.wirp_meeting_pricing import (
    WirpMeetingPricingInput,
    calculate_wirp_meeting_pricing,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "wirp_meeting_pricing_v1"
FLOAT_ABS_TOL = 1e-9


class _FrozenDateForWirp(date):
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


def _replay_rows_to_df(raw_rows: list[dict]) -> pd.DataFrame:
    """Reconstruct the WIRP-snapshot DataFrame the primitive's
    fetcher returns."""
    coerced = []
    for r in raw_rows:
        coerced.append({
            "instrument_id": int(r["instrument_id"]),
            "vendor_ticker": r["vendor_ticker"],
            "meeting_date": date.fromisoformat(r["meeting_date"]),
            "central_bank": r["central_bank"],
            "meeting_token": r.get("meeting_token"),
            "bloomberg_ticker_fr": r.get("bloomberg_ticker_fr"),
            "bloomberg_ticker_pr": r.get("bloomberg_ticker_pr"),
            "bloomberg_ticker_nm": r.get("bloomberg_ticker_nm"),
            "bloomberg_ticker_ch": r.get("bloomberg_ticker_ch"),
            "field_name": r["field_name"],
            "field_value": (
                float(r["field_value"]) if r["field_value"] is not None else None
            ),
            "as_of_date": date.fromisoformat(r["as_of_date"]),
        })
    return pd.DataFrame(coerced)


def _params_from_fixture(input_params: dict) -> WirpMeetingPricingInput:
    """Build the Pydantic input from the fixture's ``params`` dict.

    The fixture's selection_mode determines which dependent field
    to pass — the @model_validator forbids passing the other.
    """
    kwargs = {
        "central_bank": input_params["central_bank"],
        "selection_mode": input_params["selection_mode"],
    }
    if input_params["selection_mode"] == "next_n_meetings":
        if input_params.get("n_meetings") is not None:
            kwargs["n_meetings"] = input_params["n_meetings"]
    elif input_params["selection_mode"] == "specific_meeting_date":
        # ISO string → date
        if input_params.get("meeting_date"):
            kwargs["meeting_date"] = date.fromisoformat(input_params["meeting_date"])
    return WirpMeetingPricingInput(**kwargs)


@pytest.mark.skipif(
    not _FIXTURE_PATHS,
    reason=(
        "No fixtures found — run "
        "`python tests/fixtures/wirp_meeting_pricing_v1/_capture.py` "
        "against the live DB to generate."
    ),
)
@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=_FIXTURE_IDS)
def test_wirp_meeting_pricing_parity(fixture_path: Path) -> None:
    """Replay captured inputs through calculate_wirp_meeting_pricing
    with the fetcher mocked and the date frozen."""
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
        f"  computed: {actual_hash}"
    )

    df = _replay_rows_to_df(raw_rows)
    params = _params_from_fixture(fx["input"]["params"])

    _FrozenDateForWirp._frozen_value = date.fromisoformat(
        fx["input"]["frozen_today"]
    )

    module = "rates_agent.ois.tools.wirp_meeting_pricing.compute"
    with patch(
        f"{module}.fetch_wirp_meeting_snapshots", return_value=df,
    ), patch(f"{module}.date", _FrozenDateForWirp):
        actual = calculate_wirp_meeting_pricing(engine=None, params=params)

    expected_output = fx["expected_output"]
    if "error" in expected_output:
        assert "error" in actual, (
            f"{fixture_path.name}: fixture expects error envelope but "
            f"tool returned a happy-path output: {actual!r}"
        )

    _assert_equal(actual, expected_output, path="$")
