"""Targeted tests for ``fx_agent.forwards.tools.forward_curve``.

These assertions pin the contract Codex's step-5 / step-7 review
required:

- Output has exactly 5 rows in the canonical short → long order
  (1W / 1M / 3M / 6M / 12M).
- z-score is computed on ``forward_points_spot_units`` (the
  curve-stretchedness measure), not on raw forward_points.
- The JPY divisor handling works correctly on USDJPY (raw fwd_points
  in the range -10 to -500 produce an outright forward near the spot).
- For every (EURUSD, tenor), the curve tool's
  ``carry_annualized_pct`` matches ``calculate_fx_carry``'s
  ``carry_annualized_pct`` — proves both tools share conventions
  through ``fx_agent.forwards._shared``.
- An unknown pair raises a helpful ValueError (no silent empty
  output).

Standalone runner, same style as ``test_fx_carry_compute.py``.
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from fx_agent.forwards._shared import SUPPORTED_FORWARD_TENORS  # noqa: E402
from fx_agent.forwards.tools.forward_curve import (  # noqa: E402
    FXForwardCurveInput,
    get_fx_forward_curve,
)
from fx_agent.forwards.tools.fx_carry import FXCarryInput, get_fx_carry  # noqa: E402


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


def _pass(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name=name, status="PASS", detail=detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="FAIL", detail=detail)


# ============================================================================
# Tests
# ============================================================================


def test_returns_all_tenors_in_canonical_order(engine) -> CheckResult:
    out = get_fx_forward_curve(engine, FXForwardCurveInput(pair="EURUSD"))
    actual = [r["tenor"] for r in out["rows"]]
    expected = list(SUPPORTED_FORWARD_TENORS)
    if actual != expected:
        return _fail(
            "canonical_tenor_order",
            f"expected {expected}, got {actual}",
        )
    return _pass(
        "canonical_tenor_order",
        f"5 tenors in order: {actual}",
    )


def test_zscore_present_when_history_sufficient(engine) -> CheckResult:
    """With 365-day fetch + 252-day z window, every tenor should have
    a non-None z-score on G10 majors."""
    out = get_fx_forward_curve(engine, FXForwardCurveInput(pair="EURUSD"))
    missing = [r["tenor"] for r in out["rows"] if r["z_score"] is None]
    if missing:
        return _fail(
            "zscore_present",
            f"None z-score on tenor(s): {missing} (expected all 5 present)",
        )
    return _pass(
        "zscore_present",
        "z-score present on all 5 EURUSD tenors",
    )


def test_zscore_in_plausible_range(engine) -> CheckResult:
    """Rolling z-scores on forward_points_spot_units should be a
    finite scalar (typically |z| < 4 on G10 forwards in calm regimes;
    we allow up to |z|<6 as a sanity bound)."""
    out = get_fx_forward_curve(engine, FXForwardCurveInput(pair="EURUSD"))
    out_of_range: list[str] = []
    for row in out["rows"]:
        z = row["z_score"]
        if z is None:
            continue
        try:
            zf = float(z)
        except (TypeError, ValueError):
            out_of_range.append(f"{row['tenor']} not numeric: {z!r}")
            continue
        if zf != zf or abs(zf) > 6.0:  # NaN check + sanity bound
            out_of_range.append(f"{row['tenor']} z={zf}")
    if out_of_range:
        return _fail("zscore_range", f"implausible: {out_of_range}")
    return _pass("zscore_range", "all z-scores finite and within ±6")


def test_jpy_divisor_via_usdjpy(engine) -> CheckResult:
    """USDJPY uses the JPY divisor (100). Raw fwd_points are large
    (tens to hundreds), but the resulting outright_forward must land
    near the spot (spot ~150, not spot - 9000)."""
    out = get_fx_forward_curve(engine, FXForwardCurveInput(pair="USDJPY"))
    rows = out["rows"]
    if not rows:
        return _fail("jpy_divisor", "no rows for USDJPY")
    bad: list[str] = []
    for row in rows:
        spot = row["spot"]
        outright = row["outright_forward"]
        # Outright must be within 5% of spot for any standard G10 tenor.
        if not (0.95 * spot < outright < 1.05 * spot):
            bad.append(
                f"{row['tenor']}: spot={spot}, outright={outright} "
                f"(divisor handling looks wrong)"
            )
    if bad:
        return _fail("jpy_divisor", "; ".join(bad))
    return _pass(
        "jpy_divisor",
        f"USDJPY outright within ±5% of spot for all {len(rows)} tenors",
    )


def test_outright_consistency(engine) -> CheckResult:
    """For every tenor, ``outright_forward`` must equal
    ``spot + forward_points_spot_units`` within rounding."""
    out = get_fx_forward_curve(engine, FXForwardCurveInput(pair="EURUSD"))
    bad: list[str] = []
    for row in out["rows"]:
        expected = row["spot"] + row["forward_points_spot_units"]
        diff = abs(row["outright_forward"] - expected)
        if diff > 1e-4:
            bad.append(
                f"{row['tenor']}: outright={row['outright_forward']} "
                f"!= spot+pts_units={expected} (|diff|={diff})"
            )
    if bad:
        return _fail("outright_consistency", "; ".join(bad))
    return _pass(
        "outright_consistency",
        "outright = spot + forward_points_spot_units on all 5 tenors",
    )


def test_consistency_with_fx_carry(engine) -> CheckResult:
    """Cross-tool: same (pair, tenor) → same carry_annualized_pct."""
    curve_out = get_fx_forward_curve(engine, FXForwardCurveInput(pair="EURUSD"))
    curve_by_tenor = {r["tenor"]: r for r in curve_out["rows"]}
    mismatches: list[str] = []
    for tenor in SUPPORTED_FORWARD_TENORS:
        carry_out = get_fx_carry(engine, FXCarryInput(tenor=tenor))
        eur_carry = next(
            (r for r in carry_out["rows"] if r["pair"] == "EURUSD"), None
        )
        eur_curve = curve_by_tenor.get(tenor)
        if eur_carry is None or eur_curve is None:
            mismatches.append(f"{tenor}: row missing")
            continue
        diff = abs(
            eur_carry["carry_annualized_pct"]
            - eur_curve["carry_annualized_pct"]
        )
        if diff > 0.01:
            mismatches.append(
                f"{tenor}: carry={eur_carry['carry_annualized_pct']} "
                f"vs curve={eur_curve['carry_annualized_pct']} "
                f"(|diff|={diff:.4f})"
            )
    if mismatches:
        return _fail("consistency_with_fx_carry", "; ".join(mismatches))
    return _pass(
        "consistency_with_fx_carry",
        f"all {len(SUPPORTED_FORWARD_TENORS)} tenors match across tools",
    )


def test_unknown_pair_raises(engine) -> CheckResult:
    try:
        get_fx_forward_curve(engine, FXForwardCurveInput(pair="BURPBURP"))
    except ValueError as exc:
        msg = str(exc)
        if "BURPBURP" in msg:
            return _pass(
                "unknown_pair_raises",
                "ValueError raised with pair name in message",
            )
        return _fail(
            "unknown_pair_raises",
            f"raised but message did not mention the pair: {msg!r}",
        )
    return _fail(
        "unknown_pair_raises",
        "no exception raised for an unknown pair (should fail loud)",
    )


def test_lookback_days_validation_via_pydantic() -> CheckResult:
    """lookback_days must be in [30, 7300]."""
    failures: list[str] = []
    for bad_value in (10, 9999):
        try:
            FXForwardCurveInput(pair="EURUSD", lookback_days=bad_value)
            failures.append(f"Pydantic accepted lookback_days={bad_value}")
        except ValidationError:
            pass
    if failures:
        return _fail("lookback_days_bounds", "; ".join(failures))
    return _pass(
        "lookback_days_bounds",
        "Pydantic rejects both <30 and >7300",
    )


# ============================================================================
# Runner
# ============================================================================


def main() -> int:
    print("=" * 80)
    print("FX FORWARD CURVE — TARGETED TESTS")
    print("=" * 80)

    engine = get_db_engine()
    results: list[CheckResult] = []

    results.append(test_lookback_days_validation_via_pydantic())

    for test_fn in (
        test_returns_all_tenors_in_canonical_order,
        test_zscore_present_when_history_sufficient,
        test_zscore_in_plausible_range,
        test_jpy_divisor_via_usdjpy,
        test_outright_consistency,
        test_consistency_with_fx_carry,
        test_unknown_pair_raises,
    ):
        try:
            results.append(test_fn(engine))
        except Exception as exc:  # noqa: BLE001
            results.append(
                _fail(
                    test_fn.__name__,
                    f"unexpected exception: {type(exc).__name__}: {exc}\n"
                    + traceback.format_exc(),
                )
            )

    print()
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  [{marker}] {r.name}: {r.detail}")

    failures = [r for r in results if r.status == "FAIL"]
    print()
    print(f"Summary: {len(results) - len(failures)} pass, {len(failures)} fail")

    if failures:
        print("\nFAILED")
        return 1
    print("\nPASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
