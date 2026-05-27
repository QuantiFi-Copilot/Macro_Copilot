"""PR15 parity tests for Phase B+ FX carry-extension tools.

For each of implied_yield_differential and carry_basket, reloads the
captured fixture, monkey-patches pd.read_sql to replay captured
rows, freezes date.today() to the captured date for reproducibility,
runs the tool, and asserts the output matches expected_output
(numeric fields within 1e-9 tolerance, schema-stable fields by exact
equality).

Tamper detection: each fixture's raw_rows_sha256 is recomputed on
load and must match the recorded hash.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fx_agent.forwards.tools.carry_basket import (  # noqa: E402
    FXCarryBasketInput,
    get_fx_carry_basket,
)
from fx_agent.forwards.tools.implied_yield_differential import (  # noqa: E402
    FXImpliedYieldDifferentialInput,
    get_fx_implied_yield_differential,
)
from fx_agent.forwards.tools.implied_yield_differential import compute as iyd_compute  # noqa: E402
from fx_agent.forwards.tools.carry_basket import compute as cb_compute  # noqa: E402


FLOAT_TOLERANCE = 1e-9
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


class _MockConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _MockEngine:
    def connect(self) -> _MockConn:
        return _MockConn()


def _sha256_rows(rows: List[Dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _verify_sha(fixture: Dict[str, Any]) -> None:
    all_rows = [r for q in fixture["input"]["queries"] for r in q["rows"]]
    recomputed = _sha256_rows(all_rows)
    recorded = fixture["capture"]["raw_rows_sha256"]
    assert recomputed == recorded, (
        f"raw_rows SHA-256 mismatch — fixture has been hand-edited. "
        f"recorded={recorded}, recomputed={recomputed}"
    )


def _deep_compare(actual: Any, expected: Any, path: str = "") -> List[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected dict, got {type(actual).__name__}"]
        mismatches: List[str] = []
        for k in expected:
            if k not in actual:
                mismatches.append(f"{path}.{k}: missing in actual")
            else:
                mismatches.extend(_deep_compare(actual[k], expected[k], f"{path}.{k}"))
        for k in actual:
            if k not in expected:
                mismatches.append(f"{path}.{k}: extra key in actual")
        return mismatches
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: expected list, got {type(actual).__name__}"]
        if len(actual) != len(expected):
            return [f"{path}: list len mismatch actual={len(actual)} expected={len(expected)}"]
        mismatches = []
        for i, (a, e) in enumerate(zip(actual, expected)):
            mismatches.extend(_deep_compare(a, e, f"{path}[{i}]"))
        return mismatches
    if isinstance(expected, float) or isinstance(actual, float):
        if expected is None and actual is None:
            return []
        if expected is None or actual is None:
            return [f"{path}: None mismatch actual={actual} expected={expected}"]
        if math.isnan(float(expected)) and math.isnan(float(actual)):
            return []
        if abs(float(actual) - float(expected)) > FLOAT_TOLERANCE:
            return [f"{path}: actual={actual} expected={expected} diff={abs(float(actual)-float(expected))}"]
        return []
    if actual != expected:
        return [f"{path}: actual={actual!r} expected={expected!r}"]
    return []


@contextmanager
def _replay_iyd(fixture: Dict[str, Any]):
    rows = fixture["input"]["queries"][0]["rows"]
    captured_at = fixture["capture"].get("captured_at")
    frozen = (
        datetime.strptime(captured_at[:10], "%Y-%m-%d").date()
        if captured_at else
        datetime.strptime(rows[-1]["trade_date"], "%Y-%m-%d").date()
    )

    class _FrozenDate(date):
        @classmethod
        def today(cls) -> "date":  # type: ignore[override]
            return frozen

    original_read_sql = pd.read_sql

    def patched(sql, conn, params=None, **kwargs):  # noqa: ANN001
        df = pd.DataFrame(rows)
        return df

    # iyd compute uses pd.Timestamp.today().normalize() (not date.today())
    # — patch pd.Timestamp on the module via timestamp_now monkeypatch.
    # Actually compute uses `pd.Timestamp.today()` which is independent
    # of date.today(). We need to also patch that.
    original_timestamp = pd.Timestamp

    class _FrozenTimestamp(pd.Timestamp):
        @classmethod
        def today(cls):  # type: ignore[override]
            return original_timestamp(frozen)

    pd.read_sql = patched
    pd.Timestamp = _FrozenTimestamp
    try:
        yield
    finally:
        pd.read_sql = original_read_sql
        pd.Timestamp = original_timestamp


@contextmanager
def _replay_carry_basket(fixture: Dict[str, Any]):
    # All queries are joined_spot_forward rows per pair — concatenate
    # back into one wide DataFrame matching what pd.read_sql returned.
    rows: List[Dict[str, Any]] = []
    for q in fixture["input"]["queries"]:
        for r in q["rows"]:
            rec = dict(r)
            rec["pair"] = q["pair"]
            rows.append(rec)

    captured_at = fixture["capture"].get("captured_at")
    if captured_at:
        frozen = datetime.strptime(captured_at[:10], "%Y-%m-%d").date()
    else:
        last_date = max(r["trade_date"] for r in rows)
        frozen = datetime.strptime(last_date, "%Y-%m-%d").date()

    original_read_sql = pd.read_sql
    original_timestamp = pd.Timestamp

    def patched(sql, conn, params=None, **kwargs):  # noqa: ANN001
        df = pd.DataFrame(rows)
        # Preserve column order expected by compute: pair, trade_date, spot, forward_points
        return df[["pair", "trade_date", "spot", "forward_points"]]

    class _FrozenTimestamp(pd.Timestamp):
        @classmethod
        def today(cls):  # type: ignore[override]
            return original_timestamp(frozen)

    pd.read_sql = patched
    pd.Timestamp = _FrozenTimestamp
    try:
        yield
    finally:
        pd.read_sql = original_read_sql
        pd.Timestamp = original_timestamp


def main() -> int:
    results: list[CheckResult] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append(CheckResult(name, "PASS"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    def iyd_parity():
        path = FIXTURES_DIR / "implied_yield_differential_v1" / "eurusd_1m.json"
        fixture = json.loads(path.read_text())
        _verify_sha(fixture)
        params = FXImpliedYieldDifferentialInput(**fixture["input"]["params"])
        engine = _MockEngine()
        with _replay_iyd(fixture):
            out = get_fx_implied_yield_differential(engine, params)
        mismatches = _deep_compare(out, fixture["expected_output"])
        if mismatches:
            msg = "\n  ".join(mismatches[:10])
            raise AssertionError(f"Parity mismatch in {path.name}:\n  {msg}")
    check("implied_yield_differential eurusd_1m parity", iyd_parity)

    def carry_basket_parity():
        path = FIXTURES_DIR / "carry_basket_v1" / "g10_1m_top3_long_short.json"
        fixture = json.loads(path.read_text())
        _verify_sha(fixture)
        params = FXCarryBasketInput(**fixture["input"]["params"])
        engine = _MockEngine()
        with _replay_carry_basket(fixture):
            out = get_fx_carry_basket(engine, params)
        mismatches = _deep_compare(out, fixture["expected_output"])
        if mismatches:
            msg = "\n  ".join(mismatches[:10])
            raise AssertionError(f"Parity mismatch in {path.name}:\n  {msg}")
    check("carry_basket g10_1m_top3_long_short parity", carry_basket_parity)

    print("=" * 72)
    print(f"FX CARRY EXTENSIONS PARITY TEST — {sum(1 for r in results if r.status == 'PASS')} PASS / {sum(1 for r in results if r.status == 'FAIL')} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
