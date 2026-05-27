"""PR15 parity tests for Phase E3 FX vol-carry tools.

For each of vol_risk_premium and vol_calendar_spread, reloads the
captured fixture, monkey-patches the DB-touching call sites, runs
the tool, and asserts the output matches the recorded
expected_output (numeric fields within 1e-9 tolerance, schema-stable
fields by exact equality).

VRP additionally freezes ``date.today()`` to the captured date so
the in-compute cutoff slicing is reproducible regardless of when
the test runs.

Tamper detection: each fixture's raw_rows_sha256 is recomputed on
load and must match the recorded hash (prevents hand-edits without
re-capturing).
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

from fx_agent.vol.tools.vol_calendar_spread import (  # noqa: E402
    FXVolCalendarSpreadInput,
    get_fx_vol_calendar_spread,
)
from fx_agent.vol.tools.vol_risk_premium import (  # noqa: E402
    FXVolRiskPremiumInput,
    get_fx_vol_risk_premium,
)
from fx_agent.vol.tools.vol_risk_premium import compute as vrp_compute  # noqa: E402
import shared.analytics.fx_fetch as fx_fetch_module  # noqa: E402


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


def _rows_to_df(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["trade_date", "field_value"])
    return pd.DataFrame(rows)


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
def _replay_pd_read_sql_by_tenor(queries: List[Dict[str, Any]]):
    """Replay pd.read_sql by tenor — for calendar spread.

    Routes each query call to the captured rows whose `tenor` matches
    the SQL `tenor` parameter.
    """
    by_tenor = {q["tenor"]: q["rows"] for q in queries if q.get("substrate") == "fx_vol"}
    original = pd.read_sql

    def patched(sql, conn, params=None, **kwargs):  # noqa: ANN001
        params = params or {}
        tenor = params.get("tenor")
        return _rows_to_df(by_tenor.get(tenor, []))

    pd.read_sql = patched
    try:
        yield
    finally:
        pd.read_sql = original


@contextmanager
def _replay_vrp(fixture: Dict[str, Any]):
    """Replay pd.read_sql + fetch_fx_spot_series + freeze date.today()
    for vol_risk_premium parity."""
    queries = fixture["input"]["queries"]
    implied_rows = next(
        (q["rows"] for q in queries if q["substrate"] == "fx_vol"), []
    )
    spot_rows = next(
        (q["rows"] for q in queries if q["substrate"] == "fx_spot"), []
    )

    # Freeze date.today() to the captured fixture date so the in-compute
    # cutoff is reproducible. Use captured_at if present, else fall back
    # to the most recent implied row.
    captured_at = fixture["capture"].get("captured_at")
    if captured_at:
        frozen = datetime.strptime(captured_at[:10], "%Y-%m-%d").date()
    else:
        last_date = implied_rows[-1]["trade_date"]
        frozen = datetime.strptime(last_date, "%Y-%m-%d").date()

    class _FrozenDate(date):
        @classmethod
        def today(cls) -> "date":  # type: ignore[override]
            return frozen

    original_read_sql = pd.read_sql
    original_fetch = fx_fetch_module.fetch_fx_spot_series
    original_date = vrp_compute.date

    def patched_read_sql(sql, conn, params=None, **kwargs):  # noqa: ANN001
        return _rows_to_df(implied_rows)

    def patched_fetch(engine, pair, start_date, end_date=None, field_name="PX_LAST"):  # noqa: ANN001
        df = _rows_to_df(spot_rows)
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
        return df.reset_index(drop=True)

    pd.read_sql = patched_read_sql
    fx_fetch_module.fetch_fx_spot_series = patched_fetch
    vrp_compute.date = _FrozenDate
    # Also patch the symbol where the compute module imports it
    vrp_compute.fetch_fx_spot_series = patched_fetch
    try:
        yield
    finally:
        pd.read_sql = original_read_sql
        fx_fetch_module.fetch_fx_spot_series = original_fetch
        vrp_compute.date = original_date
        vrp_compute.fetch_fx_spot_series = original_fetch


def main() -> int:
    results: list[CheckResult] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append(CheckResult(name, "PASS"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    # ===================== vol_risk_premium =====================
    def vrp_parity():
        path = FIXTURES_DIR / "vol_risk_premium_v1" / "eurusd_1m_tenor_matched.json"
        fixture = json.loads(path.read_text())
        _verify_sha(fixture)
        params = FXVolRiskPremiumInput(**fixture["input"]["params"])
        engine = _MockEngine()
        with _replay_vrp(fixture):
            out = get_fx_vol_risk_premium(engine, params)
        mismatches = _deep_compare(out, fixture["expected_output"])
        if mismatches:
            msg = "\n  ".join(mismatches[:10])
            raise AssertionError(f"Parity mismatch in {path.name}:\n  {msg}")
    check("vol_risk_premium eurusd_1m_tenor_matched parity", vrp_parity)

    # ===================== vol_calendar_spread =====================
    def calspread_parity():
        path = FIXTURES_DIR / "vol_calendar_spread_v1" / "eurusd_1m_3m_long_minus_short.json"
        fixture = json.loads(path.read_text())
        _verify_sha(fixture)
        params = FXVolCalendarSpreadInput(**fixture["input"]["params"])
        engine = _MockEngine()
        with _replay_pd_read_sql_by_tenor(fixture["input"]["queries"]):
            out = get_fx_vol_calendar_spread(engine, params)
        mismatches = _deep_compare(out, fixture["expected_output"])
        if mismatches:
            msg = "\n  ".join(mismatches[:10])
            raise AssertionError(f"Parity mismatch in {path.name}:\n  {msg}")
    check("vol_calendar_spread eurusd_1m_3m_lms parity", calspread_parity)

    print("=" * 72)
    print(f"FX VOL CARRY PARITY TEST — {sum(1 for r in results if r.status == 'PASS')} PASS / {sum(1 for r in results if r.status == 'FAIL')} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
