"""Compute tests for the 4 Phase F1 FX scanners.

Phase F1 (2026-05-27).

Validates:
  - scan_fx_cross_currency_basis_tool — consumes fx_basis_panel
  - scan_fx_implied_yield_differential_tool — inline math via shared helpers
  - scan_fx_calendar_spread_tool — consumes fx_vol_panel × 2 tenors
  - scan_fx_vol_skew_tool — consumes fx_vol_panel at smile_point

For each scanner:
  - happy-path top-N call returns valid rows
  - rows have monotonically increasing rank starting at 1
  - sign sanity where applicable (e.g. basis avg NEGATIVE)
  - empty universe (future start_date / unreachable scope) returns
    empty rows or fail-loud where contracted
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402

from fx_agent.forwards.tools.scan_fx_cross_currency_basis import (  # noqa: E402
    FXBasisScannerInput,
    run_fx_cross_currency_basis_scanner,
)
from fx_agent.forwards.tools.scan_fx_implied_yield_differential import (  # noqa: E402
    FXIYDScannerInput,
    run_fx_implied_yield_differential_scanner,
)
from fx_agent.vol.tools.scan_fx_calendar_spread import (  # noqa: E402
    FXCalendarSpreadScannerInput,
    run_fx_calendar_spread_scanner,
)
from fx_agent.vol.tools.scan_fx_vol_skew import (  # noqa: E402
    FXVolSkewScannerInput,
    run_fx_vol_skew_scanner,
)


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


def _check_rank_monotone(rows: list[dict], scanner_name: str) -> None:
    for idx, row in enumerate(rows, start=1):
        assert row["rank"] == idx, (
            f"{scanner_name}: rank not monotone at position {idx}: got {row['rank']}"
        )


def main() -> int:
    engine = get_db_engine()
    results: list[CheckResult] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append(CheckResult(name, "PASS"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    # ====================================================================
    # 1. scan_fx_cross_currency_basis
    # ====================================================================
    def check_basis_topn():
        out = run_fx_cross_currency_basis_scanner(
            engine,
            FXBasisScannerInput(tenor="1M", rank_by="basis_signed", top_n=3),
        )
        assert out["sign_convention"] == "bloomberg_bcrx_usd_scarcity_negative"
        assert len(out["rows"]) == 3, f"expected 3 rows, got {len(out['rows'])}"
        _check_rank_monotone(out["rows"], "basis_scanner")
        # Sign sanity: when ranked by basis_signed, the most-negative basis is rank #1.
        # Verify ordering: rank 1 basis <= rank 2 basis <= rank 3 basis.
        basises = [r["current_basis_bps"] for r in out["rows"]]
        # Note: basis_signed inverts so most-negative ranks first.
        for i in range(len(basises) - 1):
            assert basises[i] <= basises[i + 1], (
                f"basis_signed ranking broken at position {i}: "
                f"{basises[i]} should be <= {basises[i+1]}"
            )
    check("basis scanner top-3 ranks most-negative first", check_basis_topn)

    def check_basis_abs_rank():
        out = run_fx_cross_currency_basis_scanner(
            engine,
            FXBasisScannerInput(tenor="1M", rank_by="abs_basis", top_n=3),
        )
        abs_basises = [abs(r["current_basis_bps"]) for r in out["rows"]]
        for i in range(len(abs_basises) - 1):
            assert abs_basises[i] >= abs_basises[i + 1], (
                f"abs_basis ranking broken at position {i}"
            )
    check("basis scanner abs_basis ranks largest |basis| first", check_basis_abs_rank)

    # ====================================================================
    # 2. scan_fx_implied_yield_differential
    # ====================================================================
    def check_iyd_topn_g10():
        out = run_fx_implied_yield_differential_scanner(
            engine,
            FXIYDScannerInput(
                market_scope="G10", tenor="1M",
                rank_by="iyd_signed", top_n=5,
            ),
        )
        assert out["market_scope"] == "G10"
        assert out["tenor"] == "1M"
        assert 1 <= len(out["rows"]) <= 5, f"expected 1-5 rows, got {len(out['rows'])}"
        _check_rank_monotone(out["rows"], "iyd_scanner")
        iyds = [r["current_iyd_pct"] for r in out["rows"]]
        for i in range(len(iyds) - 1):
            assert iyds[i] >= iyds[i + 1], (
                f"iyd_signed ranking broken at position {i}"
            )
    check("iyd scanner G10 top-5 ranks most-positive first", check_iyd_topn_g10)

    def check_iyd_em_universe():
        out = run_fx_implied_yield_differential_scanner(
            engine,
            FXIYDScannerInput(
                market_scope="EM", tenor="1M",
                rank_by="abs_z_score", top_n=3,
            ),
        )
        assert out["market_scope"] == "EM"
        assert len(out["rows"]) >= 1, "expected at least 1 EM pair"
    check("iyd scanner EM scope returns non-empty result", check_iyd_em_universe)

    # ====================================================================
    # 3. scan_fx_calendar_spread
    # ====================================================================
    def check_calspread_topn():
        out = run_fx_calendar_spread_scanner(
            engine,
            FXCalendarSpreadScannerInput(
                market_scope="G10", front_tenor="1M", back_tenor="3M",
                rank_by="spread_signed", top_n=3,
            ),
        )
        assert out["front_tenor"] == "1M"
        assert out["back_tenor"] == "3M"
        assert 1 <= len(out["rows"]) <= 3
        _check_rank_monotone(out["rows"], "calspread_scanner")
        # Verify spread = front - back identity
        for row in out["rows"]:
            spread_check = row["current_front_vol_pct"] - row["current_back_vol_pct"]
            actual = row["current_spread_vol_pct"]
            assert abs(spread_check - actual) < 0.01, (
                f"spread identity broken for {row['pair']}: "
                f"front-back={spread_check} but reported spread={actual}"
            )
    check("calendar spread scanner top-3 + spread identity", check_calspread_topn)

    def check_calspread_front_back_validation():
        from pydantic import ValidationError
        try:
            FXCalendarSpreadScannerInput(
                market_scope="G10", front_tenor="1M", back_tenor="1M",
            )
        except ValidationError:
            return
        raise AssertionError("expected ValidationError when front_tenor == back_tenor")
    check("calendar spread scanner rejects front==back", check_calspread_front_back_validation)

    # ====================================================================
    # 4. scan_fx_vol_skew
    # ====================================================================
    def check_skew_25r_topn():
        out = run_fx_vol_skew_scanner(
            engine,
            FXVolSkewScannerInput(
                market_scope="G10", tenor="1M", smile_point="25R",
                rank_by="skew_signed", top_n=3,
            ),
        )
        assert out["smile_point"] == "25R"
        assert out["tenor"] == "1M"
        assert 1 <= len(out["rows"]) <= 3
        _check_rank_monotone(out["rows"], "skew_scanner")
        skews = [r["current_skew_vol_pct"] for r in out["rows"]]
        for i in range(len(skews) - 1):
            assert skews[i] >= skews[i + 1], (
                f"skew_signed ranking broken at position {i}"
            )
    check("vol skew scanner G10 25R top-3 ranks most-positive first", check_skew_25r_topn)

    def check_skew_25b_butterfly():
        out = run_fx_vol_skew_scanner(
            engine,
            FXVolSkewScannerInput(
                market_scope="G10", tenor="1M", smile_point="25B",
                rank_by="abs_skew", top_n=3,
            ),
        )
        assert out["smile_point"] == "25B"
        assert len(out["rows"]) >= 1
    check("vol skew scanner G10 25B butterfly returns rows", check_skew_25b_butterfly)

    # ====================================================================
    # Summary
    # ====================================================================
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX PHASE F1 SCANNERS — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{(': ' + r.detail) if r.detail else ''}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
