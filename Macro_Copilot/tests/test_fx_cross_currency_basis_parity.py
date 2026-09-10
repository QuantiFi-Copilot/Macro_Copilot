"""PR15 parity test for Phase C cross_currency_basis.

Reloads the captured fixture (EURUSD 1M), monkey-patches:
  - pd.read_sql (for FX joined spot+forward query)
  - shared.analytics.rates_fetch.fetch_cross_market_pair (for OIS leg)
  - pd.Timestamp.today() (for the in-compute start_date filter)

Then runs get_fx_cross_currency_basis and asserts byte-identical
output vs the captured expected_output. SHA-verified tamper detection.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fx_agent.forwards.tools.cross_currency_basis import (  # noqa: E402
    FXCrossCurrencyBasisInput, get_fx_cross_currency_basis,
)
import shared.analytics.rates_fetch as rates_fetch_module  # noqa: E402
import fx_agent.forwards.tools.cross_currency_basis.compute as ccb_compute  # noqa: E402


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
def _replay_ccb(fixture: Dict[str, Any]):
    """Patch FX read_sql + OIS fetch_cross_market_pair + Timestamp.today()."""
    queries = fixture["input"]["queries"]
    fx_rows = next(q["rows"] for q in queries if q["substrate"] == "fx_joined_spot_forward")
    ois_query = next(q for q in queries if q["substrate"] == "ois_cross_market_pair")
    ois_rows = ois_query["rows"]
    ois_curves = ois_query["curves"]

    captured_at = fixture["capture"].get("captured_at")
    frozen = datetime.strptime(captured_at[:10], "%Y-%m-%d").date() if captured_at else None

    original_read_sql = pd.read_sql
    original_fetch = rates_fetch_module.fetch_cross_market_pair
    original_timestamp = pd.Timestamp

    def patched_read_sql(sql, conn, params=None, **kwargs):
        return pd.DataFrame(fx_rows)

    def patched_fetch_cross_market_pair(engine, curve_family_1, curve_family_2, tenor, field_name, start_date, **kw):
        # Filter captured rows by curve_family
        return pd.DataFrame([
            r for r in ois_rows
            if r["curve_family"] in (curve_family_1, curve_family_2)
        ])

    class _FrozenTimestamp(pd.Timestamp):
        @classmethod
        def today(cls):
            return original_timestamp(frozen)

    pd.read_sql = patched_read_sql
    rates_fetch_module.fetch_cross_market_pair = patched_fetch_cross_market_pair
    ccb_compute.fetch_cross_market_pair = patched_fetch_cross_market_pair
    pd.Timestamp = _FrozenTimestamp
    try:
        yield
    finally:
        pd.read_sql = original_read_sql
        rates_fetch_module.fetch_cross_market_pair = original_fetch
        ccb_compute.fetch_cross_market_pair = original_fetch
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

    def ccb_parity():
        path = FIXTURES_DIR / "cross_currency_basis_v1" / "eurusd_1m.json"
        fixture = json.loads(path.read_text())
        _verify_sha(fixture)
        params = FXCrossCurrencyBasisInput(**fixture["input"]["params"])
        engine = _MockEngine()
        with _replay_ccb(fixture):
            out = get_fx_cross_currency_basis(engine, params)
        mismatches = _deep_compare(out, fixture["expected_output"])
        if mismatches:
            msg = "\n  ".join(mismatches[:10])
            raise AssertionError(f"Parity mismatch in {path.name}:\n  {msg}")
    check("cross_currency_basis eurusd_1m parity", ccb_parity)

    print("=" * 72)
    print(f"FX CROSS-CURRENCY BASIS PARITY TEST — {sum(1 for r in results if r.status == 'PASS')} PASS / {sum(1 for r in results if r.status == 'FAIL')} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
