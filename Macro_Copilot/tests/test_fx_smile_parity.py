"""PR15 parity tests for Phase E2 FX smile tools.

For each of risk_reversal, butterfly, vol_smile, reloads the captured
fixture, monkey-patches ``pandas.read_sql`` to replay the captured
rows for each query, runs the tool, and asserts the output matches
the recorded expected_output (numeric fields within 1e-9 tolerance,
schema-stable fields by exact equality).

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fx_agent.vol.tools.butterfly import (  # noqa: E402
    FXButterflyInput,
    get_fx_butterfly,
)
from fx_agent.vol.tools.risk_reversal import (  # noqa: E402
    FXRiskReversalInput,
    get_fx_risk_reversal,
)
from fx_agent.vol.tools.vol_smile import (  # noqa: E402
    FXVolSmileInput,
    get_fx_vol_smile,
)
from fx_agent.vol.tools import butterfly as butterfly_mod  # noqa: E402
from fx_agent.vol.tools import risk_reversal as rr_mod  # noqa: E402
from fx_agent.vol.tools import vol_smile as smile_mod  # noqa: E402


FLOAT_TOLERANCE = 1e-9
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


class _MockConn:
    """Trivial engine.connect() context manager — pd.read_sql is mocked
    so the conn never actually executes anything."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _MockEngine:
    def connect(self) -> _MockConn:
        return _MockConn()


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


def _sha256_rows(rows: List[Dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rows_to_df(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["trade_date", "field_value"])
    return pd.DataFrame(rows)


class _ReplayReadSql:
    """Monkey-patch context for pd.read_sql that replays captured rows.

    Indexes captured rows by (smile_point) — the discriminator across
    queries within a single tool's compute path. For risk_reversal and
    butterfly there's one query, for vol_smile there are 5.
    """

    def __init__(self, queries: List[Dict[str, Any]]) -> None:
        self._by_smile_point: Dict[str, List[Dict[str, Any]]] = {
            q["smile_point"]: q["rows"] for q in queries
        }
        # ATM doesn't carry smile_point in params — match by substrate.
        self._atm_rows = self._by_smile_point.get("ATM", [])
        self._original = None

    def __enter__(self):
        captured = self  # closure-bind

        def patched(sql, conn, params=None, **kwargs):  # noqa: ANN001
            if params is None:
                params = {}
            sp = params.get("smile_point")
            if sp is not None:
                return _rows_to_df(captured._by_smile_point.get(sp, []))
            # ATM query has no smile_point param — vol_smile substrate
            return _rows_to_df(captured._atm_rows)

        self._original = pd.read_sql
        pd.read_sql = patched
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._original is not None:
            pd.read_sql = self._original


def _verify_sha(fixture: Dict[str, Any]) -> None:
    """Tamper detection — recompute hash over all captured rows."""
    all_rows = [r for q in fixture["input"]["queries"] for r in q["rows"]]
    recomputed = _sha256_rows(all_rows)
    recorded = fixture["capture"]["raw_rows_sha256"]
    assert recomputed == recorded, (
        f"raw_rows SHA-256 mismatch — fixture has been hand-edited. "
        f"recorded={recorded}, recomputed={recomputed}"
    )


def _deep_compare(actual: Any, expected: Any, path: str = "") -> List[str]:
    """Return list of mismatches; empty list = match."""
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


def _run_parity(
    fixture_path: Path,
    *,
    input_cls,
    compute_fn,
) -> None:
    fixture = json.loads(fixture_path.read_text())
    _verify_sha(fixture)
    params = input_cls(**fixture["input"]["params"])
    engine = _MockEngine()
    with _ReplayReadSql(fixture["input"]["queries"]):
        out = compute_fn(engine=engine, params=params)
    mismatches = _deep_compare(out, fixture["expected_output"])
    if mismatches:
        msg = "\n  ".join(mismatches[:10])
        raise AssertionError(f"Parity mismatch in {fixture_path.name}:\n  {msg}")


def main() -> int:
    results: list[CheckResult] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append(CheckResult(name, "PASS"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    # ===================== risk_reversal =====================
    def rr_parity():
        _run_parity(
            FIXTURES_DIR / "risk_reversal_v1" / "eurusd_25r_1m.json",
            input_cls=FXRiskReversalInput,
            compute_fn=lambda engine, params: get_fx_risk_reversal(engine, params),
        )
    check("risk_reversal eurusd_25r_1m parity", rr_parity)

    # ===================== butterfly =====================
    def bf_parity():
        _run_parity(
            FIXTURES_DIR / "butterfly_v1" / "eurusd_25b_1m.json",
            input_cls=FXButterflyInput,
            compute_fn=lambda engine, params: get_fx_butterfly(engine, params),
        )
    check("butterfly eurusd_25b_1m parity", bf_parity)

    # ===================== vol_smile =====================
    def smile_parity():
        _run_parity(
            FIXTURES_DIR / "vol_smile_v1" / "eurusd_1m_5pt_smile.json",
            input_cls=FXVolSmileInput,
            compute_fn=lambda engine, params: get_fx_vol_smile(engine, params),
        )
    check("vol_smile eurusd_1m_5pt_smile parity", smile_parity)

    print("=" * 72)
    print(f"FX SMILE PARITY TEST — {sum(1 for r in results if r.status == 'PASS')} PASS / {sum(1 for r in results if r.status == 'FAIL')} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
