"""Targeted regression tests for ``fx_agent.forwards.tools.fx_carry``.

These are the assertions that would catch the bugs we fixed in Phase A
steps 4 and 6:

- The 12M tenor must not produce a massively inflated annualised carry
  (the silent fallback bug that returned ~17% instead of ~1.5%).
- ``rank_by="abs_z_score"`` must rank the most-extreme z-score first
  and put rows with a None z-score at the end.
- ``top_n`` must truncate after sorting.
- The "current row" snapshot date must be the LAST common spot+forward
  date for the pair — not a "latest spot" union "latest forward" that
  might mix dates if a tenor goes stale.
- Bad ``rank_by`` / ``tenor`` must fail loudly (Pydantic + the
  defensive compute-side check).
- ``calculate_fx_carry`` and ``get_fx_forward_curve`` must return
  identical ``carry_annualized_pct`` for the same (pair, tenor) pair —
  the two tools share conventions via ``fx_agent.forwards._shared``.

Standalone runner (no pytest dependency, matches
``tests/test_fx_data_readiness.py`` style). Exits non-zero on failure.

Run from the repo root inside the rates-agent-dev container:

    docker compose exec rates-agent-dev micromamba run -n macro-env \
        python tests/test_fx_carry_compute.py
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
    status: str  # "PASS" | "FAIL"
    detail: str = ""


def _pass(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name=name, status="PASS", detail=detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="FAIL", detail=detail)


def _row_for_pair(out: dict, pair: str) -> dict | None:
    return next((r for r in out["rows"] if r["pair"] == pair), None)


# ============================================================================
# Tests
# ============================================================================


def test_12m_not_inflated(engine) -> CheckResult:
    """12M carry must be on the same scale as 1M/3M/6M (it was ~12×
    inflated before the step-4 fix because of a silent fallback to
    21-day tenor_days)."""
    out = get_fx_carry(engine, FXCarryInput(tenor="12M"))
    eur = _row_for_pair(out, "EURUSD")
    if eur is None:
        return _fail("12M_not_inflated", "EURUSD missing from output")
    carry = eur["carry_annualized_pct"]
    if abs(carry) > 10.0:
        return _fail(
            "12M_not_inflated",
            f"EURUSD 12M carry={carry:+.2f}% looks inflated "
            f"(expected near 1.5% on G10 EUR/USD). The step-4 "
            f"silent-fallback regression may have come back.",
        )
    return _pass("12M_not_inflated", f"EURUSD 12M carry={carry:+.2f}% (sane)")


def test_default_rank_by_descending_signed(engine) -> CheckResult:
    """Default ``rank_by='carry_signed'`` must sort rows descending
    by carry_annualized_pct (and pin rank=1 to the highest carry)."""
    out = get_fx_carry(engine, FXCarryInput(tenor="1M"))
    rows = out["rows"]
    if len(rows) < 2:
        return _fail("default_rank_by", f"need ≥2 rows, got {len(rows)}")
    carries = [r["carry_annualized_pct"] for r in rows]
    is_descending = all(a >= b for a, b in zip(carries, carries[1:]))
    if not is_descending:
        return _fail(
            "default_rank_by",
            f"carry not descending: {carries}",
        )
    ranks = [r["rank"] for r in rows]
    if ranks != list(range(1, len(rows) + 1)):
        return _fail("default_rank_by", f"ranks not 1..N: {ranks}")
    return _pass(
        "default_rank_by",
        f"sorted descending, ranks 1..{len(rows)}, top={rows[0]['pair']}",
    )


def test_rank_by_abs_z_score(engine) -> CheckResult:
    """``rank_by='abs_z_score'`` must sort by absolute z-score
    descending; rows with a None z-score must be placed at the end."""
    out = get_fx_carry(engine, FXCarryInput(tenor="1M", rank_by="abs_z_score"))
    rows = out["rows"]
    if len(rows) < 2:
        return _fail("rank_by_abs_z_score", f"need ≥2 rows, got {len(rows)}")

    # Split rows into "has z" / "no z", check ordering inside the
    # has-z group, then check no-z rows are after.
    z_present = [r for r in rows if r["carry_z_score"] is not None]
    z_missing = [r for r in rows if r["carry_z_score"] is None]
    if z_present:
        abs_zs = [abs(r["carry_z_score"]) for r in z_present]
        is_descending = all(a >= b for a, b in zip(abs_zs, abs_zs[1:]))
        if not is_descending:
            return _fail(
                "rank_by_abs_z_score",
                f"|z| not descending across present-z rows: {abs_zs}",
            )

    if z_missing:
        first_missing_rank = z_missing[0]["rank"]
        last_present_rank = z_present[-1]["rank"] if z_present else 0
        if first_missing_rank <= last_present_rank:
            return _fail(
                "rank_by_abs_z_score",
                f"a None-z row landed at rank {first_missing_rank} "
                f"before the last present-z row at rank {last_present_rank}",
            )

    return _pass(
        "rank_by_abs_z_score",
        f"top={rows[0]['pair']} (z={rows[0]['carry_z_score']}), "
        f"{len(z_present)} with z, {len(z_missing)} without",
    )


def test_top_n_truncation(engine) -> CheckResult:
    """``top_n=3`` must return exactly 3 rows, and they must be the
    top 3 of the full unsorted ranking."""
    out_all = get_fx_carry(engine, FXCarryInput(tenor="3M", rank_by="abs_carry"))
    out_top3 = get_fx_carry(
        engine, FXCarryInput(tenor="3M", rank_by="abs_carry", top_n=3)
    )
    if len(out_top3["rows"]) != 3:
        return _fail("top_n", f"top_n=3 returned {len(out_top3['rows'])} rows")
    top3_pairs = [r["pair"] for r in out_top3["rows"]]
    expected_pairs = [r["pair"] for r in out_all["rows"][:3]]
    if top3_pairs != expected_pairs:
        return _fail(
            "top_n",
            f"top-3 pairs differ: full sort gives {expected_pairs} but "
            f"top_n=3 gave {top3_pairs}",
        )
    return _pass("top_n", f"top 3 by |carry| at 3M = {top3_pairs}")


def test_latest_common_date_alignment(engine) -> CheckResult:
    """Each row's spot_date and forward_date must be identical — the
    current snapshot is the LAST common spot+forward date for the pair
    (Codex garde-fou #2 enforced in step 6)."""
    out = get_fx_carry(engine, FXCarryInput(tenor="1M"))
    for row in out["rows"]:
        if row["spot_date"] != row["forward_date"]:
            return _fail(
                "latest_common_date_alignment",
                f"{row['pair']}: spot_date={row['spot_date']} != "
                f"forward_date={row['forward_date']}",
            )
    return _pass(
        "latest_common_date_alignment",
        f"all {len(out['rows'])} rows: spot_date == forward_date",
    )


def test_invalid_rank_by_via_pydantic() -> CheckResult:
    """Pydantic must reject a bad ``rank_by`` value at input time."""
    try:
        FXCarryInput(tenor="1M", rank_by="banana")  # type: ignore[arg-type]
    except ValidationError:
        return _pass("invalid_rank_by", "Pydantic raised ValidationError")
    return _fail("invalid_rank_by", "Pydantic accepted rank_by='banana'")


def test_invalid_tenor_via_pydantic() -> CheckResult:
    """Pydantic must reject a bad ``tenor`` value at input time."""
    try:
        FXCarryInput(tenor="5M")  # type: ignore[arg-type]
    except ValidationError:
        return _pass("invalid_tenor_pydantic", "Pydantic raised ValidationError")
    return _fail("invalid_tenor_pydantic", "Pydantic accepted tenor='5M'")


def test_unsupported_tenor_compute_fail_loud(engine) -> CheckResult:
    """When a caller bypasses Pydantic (``model_construct``) with a
    bad tenor, compute must raise ValueError listing supported tenors —
    not silently fall back to 1M (the pre-step-4 behaviour)."""
    bad = FXCarryInput.model_construct(tenor="5M")
    try:
        get_fx_carry(engine, bad)
    except ValueError as exc:
        msg = str(exc)
        if "5M" in msg and "Supported" in msg:
            return _pass(
                "unsupported_tenor_compute",
                "compute raised informative ValueError",
            )
        return _fail(
            "unsupported_tenor_compute",
            f"raised but message not informative: {msg!r}",
        )
    return _fail(
        "unsupported_tenor_compute",
        "compute did not raise on unsupported tenor",
    )


def test_consistency_with_forward_curve(engine) -> CheckResult:
    """For every (EURUSD, tenor), ``calculate_fx_carry`` and
    ``get_fx_forward_curve`` must return identical
    ``carry_annualized_pct`` (within 0.01% rounding) — the two tools
    share conventions via fx_agent.forwards._shared."""
    curve_out = get_fx_forward_curve(engine, FXForwardCurveInput(pair="EURUSD"))
    curve_by_tenor = {r["tenor"]: r for r in curve_out["rows"]}
    mismatches: list[str] = []
    for tenor in SUPPORTED_FORWARD_TENORS:
        carry_out = get_fx_carry(engine, FXCarryInput(tenor=tenor))
        eur_carry = _row_for_pair(carry_out, "EURUSD")
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
        return _fail("consistency_with_curve", "; ".join(mismatches))
    return _pass(
        "consistency_with_curve",
        f"all {len(SUPPORTED_FORWARD_TENORS)} tenors match across tools",
    )


# ============================================================================
# Runner
# ============================================================================


_EXPECTED_G10_PAIRS = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"}
_EXPECTED_EM_PAIRS = {"USDMXN", "USDZAR", "USDTRY", "USDPLN", "USDHUF", "USDPHP"}


def test_market_scope_default_is_g10(engine) -> CheckResult:
    """Default market_scope must be 'G10' — preserves Phase A behavior
    after the BBG batch 2026-05-25 added EM forwards substrate."""
    out = get_fx_carry(engine, FXCarryInput(tenor="1M"))
    rows = out["rows"]
    pairs = {r["pair"] for r in rows}
    if len(rows) != 6:
        return _fail("market_scope_default_g10", f"expected 6 rows, got {len(rows)}")
    if pairs != _EXPECTED_G10_PAIRS:
        return _fail(
            "market_scope_default_g10",
            f"expected {sorted(_EXPECTED_G10_PAIRS)}, got {sorted(pairs)}",
        )
    return _pass("market_scope_default_g10", "default returns exactly 6 G10 majors")


def test_market_scope_em(engine) -> CheckResult:
    """market_scope='EM' returns exactly the 6 EM deliverable forwards
    (USDMXN, USDZAR, USDTRY, USDPLN, USDHUF, USDPHP). NDFs (BRL/KRW/IDR)
    are NOT included — they have a separate compute path."""
    out = get_fx_carry(engine, FXCarryInput(tenor="1M", market_scope="EM"))
    rows = out["rows"]
    pairs = {r["pair"] for r in rows}
    if len(rows) != 6:
        return _fail("market_scope_em", f"expected 6 rows, got {len(rows)}")
    if pairs != _EXPECTED_EM_PAIRS:
        return _fail(
            "market_scope_em",
            f"expected {sorted(_EXPECTED_EM_PAIRS)}, got {sorted(pairs)}",
        )
    # Defensive: no NDF pairs sneak in.
    if any(p in pairs for p in ("USDBRL", "USDKRW", "USDIDR")):
        return _fail(
            "market_scope_em",
            f"NDF pair leaked into EM scope: {pairs & {'USDBRL', 'USDKRW', 'USDIDR'}}",
        )
    return _pass("market_scope_em", "EM scope returns exactly 6 deliverable EM pairs (no NDFs)")


def test_market_scope_all(engine) -> CheckResult:
    """market_scope='ALL' = G10 + EM deliverable = 12 pairs at any tenor."""
    out = get_fx_carry(engine, FXCarryInput(tenor="1M", market_scope="ALL"))
    rows = out["rows"]
    pairs = {r["pair"] for r in rows}
    expected = _EXPECTED_G10_PAIRS | _EXPECTED_EM_PAIRS
    if len(rows) != 12:
        return _fail("market_scope_all", f"expected 12 rows, got {len(rows)}")
    if pairs != expected:
        return _fail("market_scope_all", f"expected {sorted(expected)}, got {sorted(pairs)}")
    return _pass("market_scope_all", "ALL scope returns 12 pairs (6 G10 + 6 EM deliverable)")


def test_market_scope_invalid_via_pydantic() -> CheckResult:
    """Invalid market_scope is caught by Pydantic Literal."""
    try:
        FXCarryInput(tenor="1M", market_scope="banana")  # type: ignore[arg-type]
    except Exception as exc:
        return _pass("market_scope_invalid_pydantic", f"Pydantic rejected as expected: {type(exc).__name__}")
    return _fail("market_scope_invalid_pydantic", "Pydantic accepted market_scope='banana'")


def main() -> int:
    print("=" * 80)
    print("FX CARRY — TARGETED REGRESSION TESTS")
    print("=" * 80)

    engine = get_db_engine()
    results: list[CheckResult] = []

    # Pure-schema tests do not need the engine.
    results.append(test_invalid_rank_by_via_pydantic())
    results.append(test_invalid_tenor_via_pydantic())
    results.append(test_market_scope_invalid_via_pydantic())

    # DB-touching tests share one engine to avoid teardown overhead.
    for test_fn in (
        test_12m_not_inflated,
        test_default_rank_by_descending_signed,
        test_rank_by_abs_z_score,
        test_top_n_truncation,
        test_latest_common_date_alignment,
        test_unsupported_tenor_compute_fail_loud,
        test_consistency_with_forward_curve,
        test_market_scope_default_is_g10,
        test_market_scope_em,
        test_market_scope_all,
    ):
        try:
            results.append(test_fn(engine))
        except Exception as exc:  # noqa: BLE001 — surface any unexpected throw
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
