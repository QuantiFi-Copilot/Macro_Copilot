"""Targeted compute tests for Phase E2 FX smile tools.

Covers the 3 primitives:
  - get_fx_risk_reversal: delta_anchor knob (25R/10R), vol-pt unit
  - get_fx_butterfly: delta_anchor knob (25B/10B), vol-pt unit
  - get_fx_vol_smile: 5-point aggregate (ATM + 25R + 25B + 10R + 10B)

Mirror of tests/test_fx_vol_atm_compute.py shape — fail-loud Pydantic
checks + sanity assertions on live DB output.
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

    # ============ risk_reversal ============

    def check_rr_eurusd_25_sane():
        out = get_fx_risk_reversal(
            engine, FXRiskReversalInput(pair="EURUSD", delta_anchor=25, tenor="1M")
        )
        m = out["current_metrics"]
        # EURUSD 25R 1M is typically between -2 and +2 vol points
        assert -3.0 < m["current_risk_reversal_vol_pts"] < 3.0, (
            f"EURUSD 25R 1M {m['current_risk_reversal_vol_pts']} out of sane range"
        )
        assert m["smile_point"] == "25R"
        assert m["vendor_ticker"] == "EURUSD25R1M Curncy"
        assert m["observation_count"] >= 100
    check("risk_reversal EURUSD 25R 1M sane + ticker correct", check_rr_eurusd_25_sane)

    def check_rr_eurusd_10_resolves_10r():
        out = get_fx_risk_reversal(
            engine, FXRiskReversalInput(pair="EURUSD", delta_anchor=10, tenor="1M")
        )
        assert out["current_metrics"]["smile_point"] == "10R"
        assert out["current_metrics"]["vendor_ticker"] == "EURUSD10R1M Curncy"
    check("risk_reversal delta_anchor=10 resolves to 10R", check_rr_eurusd_10_resolves_10r)

    def check_rr_12m_bbg_ticker_alias():
        """12M internal tenor should resolve to 1Y BBG ticker suffix."""
        out = get_fx_risk_reversal(
            engine, FXRiskReversalInput(pair="EURUSD", delta_anchor=25, tenor="12M")
        )
        assert out["current_metrics"]["vendor_ticker"] == "EURUSD25R1Y Curncy"
    check("risk_reversal 12M -> 1Y BBG ticker alias", check_rr_12m_bbg_ticker_alias)

    def check_rr_change_in_vol_points():
        """Daily change should be in absolute vol points, not %."""
        out = get_fx_risk_reversal(
            engine, FXRiskReversalInput(pair="EURUSD", delta_anchor=25, tenor="1M")
        )
        m = out["current_metrics"]
        if m["daily_change_vol_pts"] is not None:
            # daily RR move shouldn't exceed +/-1 vol point under normal conditions
            assert abs(m["daily_change_vol_pts"]) < 2.0, (
                f"daily change {m['daily_change_vol_pts']} suggests % not vol pts"
            )
    check("risk_reversal changes in vol points (not pct)", check_rr_change_in_vol_points)

    def check_rr_pair_classes():
        """Works on G10 USD majors + EM USD-leg pairs.

        Note: fx_vol_smile coverage is USD-leg only (no G10 crosses) —
        smile differentials are quoted on the USD-leg curve. G10 crosses
        only have ATM in fx_vol substrate.
        """
        for pair in ("EURUSD", "USDJPY", "USDMXN", "USDCNH", "USDBRL", "USDZAR"):
            out = get_fx_risk_reversal(
                engine, FXRiskReversalInput(pair=pair, delta_anchor=25, tenor="1M")
            )
            assert "current_risk_reversal_vol_pts" in out["current_metrics"]
    check("risk_reversal works on G10+EM USD-leg pair classes", check_rr_pair_classes)

    def check_rr_invalid_delta_pydantic():
        try:
            FXRiskReversalInput(pair="EURUSD", delta_anchor=15, tenor="1M")
        except ValidationError:
            return
        raise AssertionError("delta_anchor=15 should fail Pydantic")
    check("risk_reversal invalid delta_anchor caught by Pydantic", check_rr_invalid_delta_pydantic)

    # ============ butterfly ============

    def check_bf_eurusd_25_sane():
        out = get_fx_butterfly(
            engine, FXButterflyInput(pair="EURUSD", delta_anchor=25, tenor="1M")
        )
        m = out["current_metrics"]
        # BF is typically small positive (wings-rich); 25B 1M usually 0.05-0.5 vol pts
        assert -0.5 < m["current_butterfly_vol_pts"] < 2.0, (
            f"EURUSD 25B 1M {m['current_butterfly_vol_pts']} out of sane range"
        )
        assert m["smile_point"] == "25B"
        assert m["vendor_ticker"] == "EURUSD25B1M Curncy"
        assert m["observation_count"] >= 100
    check("butterfly EURUSD 25B 1M sane + ticker correct", check_bf_eurusd_25_sane)

    def check_bf_eurusd_10_resolves_10b():
        out = get_fx_butterfly(
            engine, FXButterflyInput(pair="EURUSD", delta_anchor=10, tenor="1M")
        )
        assert out["current_metrics"]["smile_point"] == "10B"
        assert out["current_metrics"]["vendor_ticker"] == "EURUSD10B1M Curncy"
    check("butterfly delta_anchor=10 resolves to 10B", check_bf_eurusd_10_resolves_10b)

    def check_bf_10_greater_than_25_typically():
        """10-delta butterfly is typically >= 25-delta butterfly
        (wings get richer as we move further OTM under normal kurtosis).
        Soft check — assert 10B > 0.5 * 25B as a sanity floor."""
        out_25 = get_fx_butterfly(
            engine, FXButterflyInput(pair="EURUSD", delta_anchor=25, tenor="1M")
        )
        out_10 = get_fx_butterfly(
            engine, FXButterflyInput(pair="EURUSD", delta_anchor=10, tenor="1M")
        )
        b25 = out_25["current_metrics"]["current_butterfly_vol_pts"]
        b10 = out_10["current_metrics"]["current_butterfly_vol_pts"]
        # Sanity floor: 10B should be at least half of 25B (typically 2-3x larger)
        assert b10 > 0.5 * b25, (
            f"10B={b10} unexpectedly small vs 25B={b25} — check kurtosis pricing"
        )
    check("butterfly 10B > 0.5 * 25B (kurtosis sanity)", check_bf_10_greater_than_25_typically)

    def check_bf_change_in_vol_points():
        out = get_fx_butterfly(
            engine, FXButterflyInput(pair="EURUSD", delta_anchor=25, tenor="1M")
        )
        m = out["current_metrics"]
        if m["daily_change_vol_pts"] is not None:
            assert abs(m["daily_change_vol_pts"]) < 1.0, (
                f"daily BF change {m['daily_change_vol_pts']} suggests % not vol pts"
            )
    check("butterfly changes in vol points (not pct)", check_bf_change_in_vol_points)

    # ============ vol_smile aggregate ============

    def check_smile_eurusd_5_points():
        out = get_fx_vol_smile(engine, FXVolSmileInput(pair="EURUSD", tenor="1M"))
        m = out["current_metrics"]
        assert m["pair"] == "EURUSD"
        assert m["tenor"] == "1M"
        # All 5 points present
        for key in ("atm", "rr_25", "bf_25", "rr_10", "bf_10"):
            assert key in m, f"missing smile point {key}"
            assert m[key]["current_vol_pts"] is not None
            assert m[key]["observation_count"] > 0
    check("vol_smile EURUSD 1M returns all 5 points", check_smile_eurusd_5_points)

    def check_smile_atm_is_level_rr_is_differential():
        """ATM should be a vol LEVEL (positive, large); RR/BF are DIFFERENTIALS (small)."""
        out = get_fx_vol_smile(engine, FXVolSmileInput(pair="EURUSD", tenor="1M"))
        m = out["current_metrics"]
        # ATM should be 2-30 vol pts for a major pair
        assert 2.0 < m["atm"]["current_vol_pts"] < 30.0, (
            f"ATM {m['atm']['current_vol_pts']} not in 2-30 vol pt level range"
        )
        # RR/BF should be small differentials
        for diff_key in ("rr_25", "bf_25", "rr_10", "bf_10"):
            assert abs(m[diff_key]["current_vol_pts"]) < 5.0, (
                f"{diff_key}={m[diff_key]['current_vol_pts']} too large to be a differential"
            )
    check("vol_smile ATM is level, RR/BF are differentials", check_smile_atm_is_level_rr_is_differential)

    def check_smile_smile_point_labels():
        out = get_fx_vol_smile(engine, FXVolSmileInput(pair="EURUSD", tenor="1M"))
        m = out["current_metrics"]
        assert m["atm"]["smile_point"] == "ATM"
        assert m["rr_25"]["smile_point"] == "25R"
        assert m["bf_25"]["smile_point"] == "25B"
        assert m["rr_10"]["smile_point"] == "10R"
        assert m["bf_10"]["smile_point"] == "10B"
    check("vol_smile each point carries correct smile_point label", check_smile_smile_point_labels)

    def check_smile_as_of_date_is_min():
        """Top-level as_of_date == MIN of per-point latest dates."""
        out = get_fx_vol_smile(engine, FXVolSmileInput(pair="EURUSD", tenor="1M"))
        m = out["current_metrics"]
        point_dates = [
            m["atm"]["as_of_date"],
            m["rr_25"]["as_of_date"],
            m["bf_25"]["as_of_date"],
            m["rr_10"]["as_of_date"],
            m["bf_10"]["as_of_date"],
        ]
        assert m["as_of_date"] == min(point_dates), (
            f"as_of_date {m['as_of_date']} != MIN of points {point_dates}"
        )
    check("vol_smile aggregate as_of_date == MIN of per-point latest", check_smile_as_of_date_is_min)

    def check_smile_consistency_with_rr_bf_singletons():
        """vol_smile RR/BF values must equal the singleton tool outputs (same recipe)."""
        smile = get_fx_vol_smile(engine, FXVolSmileInput(pair="EURUSD", tenor="1M"))["current_metrics"]
        rr25 = get_fx_risk_reversal(
            engine, FXRiskReversalInput(pair="EURUSD", delta_anchor=25, tenor="1M")
        )["current_metrics"]
        bf25 = get_fx_butterfly(
            engine, FXButterflyInput(pair="EURUSD", delta_anchor=25, tenor="1M")
        )["current_metrics"]
        # Same series, same recipe — must match within rounding tolerance
        assert abs(smile["rr_25"]["current_vol_pts"] - rr25["current_risk_reversal_vol_pts"]) < 1e-3
        assert abs(smile["bf_25"]["current_vol_pts"] - bf25["current_butterfly_vol_pts"]) < 1e-3
    check("vol_smile RR/BF match singleton tool outputs", check_smile_consistency_with_rr_bf_singletons)

    def check_smile_fails_loud_unknown_pair():
        try:
            get_fx_vol_smile(engine, FXVolSmileInput(pair="XXXXXX", tenor="1M"))
        except ValueError:
            return
        raise AssertionError("unknown pair should have raised ValueError")
    check("vol_smile unknown pair fails loud", check_smile_fails_loud_unknown_pair)

    # ============ fail-loud Pydantic checks ============

    def check_smile_invalid_tenor_pydantic():
        try:
            FXVolSmileInput(pair="EURUSD", tenor="2M")
        except ValidationError:
            return
        raise AssertionError("invalid tenor should have failed Pydantic")
    check("vol_smile invalid tenor caught by Pydantic", check_smile_invalid_tenor_pydantic)

    def check_bf_invalid_tenor_pydantic():
        try:
            FXButterflyInput(pair="EURUSD", delta_anchor=25, tenor="2M")
        except ValidationError:
            return
        raise AssertionError("invalid tenor should have failed Pydantic")
    check("butterfly invalid tenor caught by Pydantic", check_bf_invalid_tenor_pydantic)

    # ============ report ============
    print("=" * 72)
    print(f"FX SMILE COMPUTE TEST — {sum(1 for r in results if r.status == 'PASS')} PASS / {sum(1 for r in results if r.status == 'FAIL')} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
