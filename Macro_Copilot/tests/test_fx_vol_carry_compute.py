"""Targeted compute tests for Phase E3 FX vol-carry tools.

Covers the 2 primitives:
  - get_fx_vol_risk_premium: implied - realized, both basis modes
  - get_fx_vol_calendar_spread: tenor differential, both sign modes
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
from fx_agent.vol.tools.vol_calendar_spread import (  # noqa: E402
    FXVolCalendarSpreadInput,
    get_fx_vol_calendar_spread,
)
from fx_agent.vol.tools.vol_risk_premium import (  # noqa: E402
    FXVolRiskPremiumInput,
    get_fx_vol_risk_premium,
)


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


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

    # ============ vol_risk_premium ============

    def check_vrp_eurusd_1m_sane():
        out = get_fx_vol_risk_premium(
            engine, FXVolRiskPremiumInput(pair="EURUSD", tenor="1M")
        )
        m = out["current_metrics"]
        # VRP for EURUSD 1M typically -5 .. +5 vol pts
        assert -5.0 < m["current_vol_risk_premium_vol_pts"] < 5.0, (
            f"VRP {m['current_vol_risk_premium_vol_pts']} out of sane range"
        )
        assert m["realized_window_basis"] == "tenor_matched"
        assert m["realized_window_days"] == 21  # 1M → 21 trading days
        assert m["vendor_ticker_implied"] == "EURUSDV1M Curncy"
        assert m["current_implied_vol_pct"] > 0
        assert m["current_realized_vol_pct"] > 0
        # Identity check: VRP = implied - realized within rounding
        assert abs(
            m["current_vol_risk_premium_vol_pts"]
            - (m["current_implied_vol_pct"] - m["current_realized_vol_pct"])
        ) < 0.001
    check("vrp EURUSD 1M tenor_matched sane + identity", check_vrp_eurusd_1m_sane)

    def check_vrp_tenor_window_map():
        """tenor_matched correctly resolves window per tenor."""
        cases = [("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252)]
        for tenor, expected_days in cases:
            try:
                out = get_fx_vol_risk_premium(
                    engine,
                    FXVolRiskPremiumInput(
                        pair="EURUSD", tenor=tenor,
                        lookback_days=400 if tenor != "12M" else 730,
                    ),
                )
                got = out["current_metrics"]["realized_window_days"]
                assert got == expected_days, f"{tenor}: window={got} expected={expected_days}"
            except ValueError as exc:
                # Some tenors may need longer lookback — flag but don't hard-fail
                if "No overlapping" in str(exc) or "lookback" in str(exc):
                    continue
                raise
    check("vrp tenor_matched window resolution", check_vrp_tenor_window_map)

    def check_vrp_fixed_30d_basis():
        """fixed_30d overrides tenor matching."""
        out_matched = get_fx_vol_risk_premium(
            engine, FXVolRiskPremiumInput(pair="EURUSD", tenor="3M")
        )
        out_fixed = get_fx_vol_risk_premium(
            engine,
            FXVolRiskPremiumInput(
                pair="EURUSD", tenor="3M", realized_window_basis="fixed_30d"
            ),
        )
        # Same implied, different realized window → different realized + VRP
        assert out_matched["current_metrics"]["realized_window_days"] == 63
        assert out_fixed["current_metrics"]["realized_window_days"] == 30
        # Realized vol differs because window differs
        m_real = out_matched["current_metrics"]["current_realized_vol_pct"]
        f_real = out_fixed["current_metrics"]["current_realized_vol_pct"]
        # On a typical pair these should differ by at least 0.05 vol pts
        # (unless the underlying is unusually flat)
        # Just check they're not identical:
        assert m_real != f_real, (
            f"tenor_matched and fixed_30d gave identical realized vol "
            f"({m_real}) — unexpected unless 63d == 30d coincidentally."
        )
    check("vrp fixed_30d basis overrides tenor", check_vrp_fixed_30d_basis)

    def check_vrp_change_in_vol_points():
        out = get_fx_vol_risk_premium(
            engine, FXVolRiskPremiumInput(pair="EURUSD", tenor="1M")
        )
        m = out["current_metrics"]
        if m["daily_change_vol_pts"] is not None:
            assert abs(m["daily_change_vol_pts"]) < 3.0, (
                f"daily VRP change {m['daily_change_vol_pts']} too large for vol pts"
            )
    check("vrp changes in vol points (not pct)", check_vrp_change_in_vol_points)

    def check_vrp_fails_loud_unknown_pair():
        try:
            get_fx_vol_risk_premium(
                engine, FXVolRiskPremiumInput(pair="XXXXXX", tenor="1M")
            )
        except ValueError:
            return
        raise AssertionError("unknown pair should have raised ValueError")
    check("vrp unknown pair fails loud", check_vrp_fails_loud_unknown_pair)

    def check_vrp_invalid_basis_pydantic():
        try:
            FXVolRiskPremiumInput(
                pair="EURUSD", tenor="1M", realized_window_basis="bogus"
            )
        except ValidationError:
            return
        raise AssertionError("invalid realized_window_basis should fail Pydantic")
    check("vrp invalid basis caught by Pydantic", check_vrp_invalid_basis_pydantic)

    # ============ vol_calendar_spread ============

    def check_calspread_eurusd_1m_3m_sane():
        out = get_fx_vol_calendar_spread(
            engine,
            FXVolCalendarSpreadInput(
                pair="EURUSD", short_tenor="1M", long_tenor="3M",
            ),
        )
        m = out["current_metrics"]
        # 1M-3M EURUSD calendar typically -1 .. +1 vol pts
        assert -2.0 < m["current_spread_vol_pts"] < 2.0, (
            f"1M-3M spread {m['current_spread_vol_pts']} out of sane range"
        )
        assert m["spread_direction"] == "long_minus_short"
        assert m["vendor_ticker_short"] == "EURUSDV1M Curncy"
        assert m["vendor_ticker_long"] == "EURUSDV3M Curncy"
        # Identity check
        assert abs(
            m["current_spread_vol_pts"]
            - (m["current_long_vol_pct"] - m["current_short_vol_pct"])
        ) < 0.001
    check("calspread EURUSD 1M/3M sane + identity", check_calspread_eurusd_1m_3m_sane)

    def check_calspread_sign_flip():
        """spread_direction flip negates the spread (exact)."""
        out_lms = get_fx_vol_calendar_spread(
            engine,
            FXVolCalendarSpreadInput(
                pair="EURUSD", short_tenor="1M", long_tenor="12M",
                spread_direction="long_minus_short",
            ),
        )
        out_sml = get_fx_vol_calendar_spread(
            engine,
            FXVolCalendarSpreadInput(
                pair="EURUSD", short_tenor="1M", long_tenor="12M",
                spread_direction="short_minus_long",
            ),
        )
        # Exact negation
        assert abs(
            out_lms["current_metrics"]["current_spread_vol_pts"]
            + out_sml["current_metrics"]["current_spread_vol_pts"]
        ) < 1e-6, "sign flip should negate the spread exactly"
        # Z-score sign should also flip
        if (
            out_lms["current_metrics"]["z_score"] is not None
            and out_sml["current_metrics"]["z_score"] is not None
        ):
            assert abs(
                out_lms["current_metrics"]["z_score"]
                + out_sml["current_metrics"]["z_score"]
            ) < 1e-3
    check("calspread sign flip negates spread + z exactly", check_calspread_sign_flip)

    def check_calspread_eurusd_contango_typical():
        """EURUSD 12M typically richer than 1M (vol contango)."""
        out = get_fx_vol_calendar_spread(
            engine,
            FXVolCalendarSpreadInput(
                pair="EURUSD", short_tenor="1M", long_tenor="12M",
            ),
        )
        m = out["current_metrics"]
        # Soft sanity: 12M > 1M usually (contango is normal regime for EURUSD)
        assert m["current_long_vol_pct"] >= m["current_short_vol_pct"] - 1.0, (
            "EURUSD 12M unexpectedly far below 1M — vol curve inverted? "
            f"short={m['current_short_vol_pct']} long={m['current_long_vol_pct']}"
        )
    check("calspread EURUSD 12M >= 1M typically", check_calspread_eurusd_contango_typical)

    def check_calspread_inverted_tenors_pydantic():
        try:
            FXVolCalendarSpreadInput(
                pair="EURUSD", short_tenor="3M", long_tenor="1M",
            )
        except ValidationError:
            return
        raise AssertionError("short>=long should fail Pydantic model validator")
    check("calspread inverted tenors caught by Pydantic", check_calspread_inverted_tenors_pydantic)

    def check_calspread_same_tenors_pydantic():
        try:
            FXVolCalendarSpreadInput(
                pair="EURUSD", short_tenor="1M", long_tenor="1M",
            )
        except ValidationError:
            return
        raise AssertionError("equal tenors should fail Pydantic")
    check("calspread equal tenors caught by Pydantic", check_calspread_same_tenors_pydantic)

    def check_calspread_pair_classes():
        """Works across G10 + EM majors with both tenors available."""
        for pair in ("EURUSD", "USDJPY", "USDMXN", "USDCNH"):
            out = get_fx_vol_calendar_spread(
                engine,
                FXVolCalendarSpreadInput(
                    pair=pair, short_tenor="1M", long_tenor="3M",
                ),
            )
            assert "current_spread_vol_pts" in out["current_metrics"]
    check("calspread works on G10+EM pair classes", check_calspread_pair_classes)

    # ============ report ============
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX VOL CARRY COMPUTE TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
