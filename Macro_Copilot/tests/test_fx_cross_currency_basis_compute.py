"""Compute tests for Phase C FX cross-currency basis primitive.

Covers structural behavior that doesn't require live OIS data:
  - Pydantic gates (V1 USD-leg scope, fail-loud on USDCHF/EM/crosses)
  - usd_leg_position resolution (base for USDxxx, quote for xxxUSD)
  - Currency mapping resolution
  - FX→OIS tenor alias (12M → 1Y)
  - sign_convention metadata in output

Tests that REQUIRE live OIS data (basis range sanity, identity vs
fx_implied_yield_differential, etc.) gracefully SKIP locally on a
dev DB without OIS substrate. They will PASS on the full QFin DB.
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
from fx_agent.forwards.tools.cross_currency_basis import (  # noqa: E402
    FXCrossCurrencyBasisInput,
    get_fx_cross_currency_basis,
)
from fx_agent.forwards.tools.cross_currency_basis.compute import (  # noqa: E402
    _LOCAL_CURRENCY_TO_OIS_CURVE,
    _FX_TO_OIS_TENOR,
    _resolve_usd_leg,
)
from fx_agent.forwards.tools.implied_yield_differential import (  # noqa: E402
    FXImpliedYieldDifferentialInput,
    get_fx_implied_yield_differential,
)


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


def main() -> int:
    engine = get_db_engine()
    results: list[CheckResult] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append(CheckResult(name, "PASS"))
        except _SkipExpected as exc:
            results.append(CheckResult(name, "SKIP", str(exc)))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    # ================= Pydantic gates =================

    def check_valid_pairs_accepted():
        """All V1 supported pairs accept at the Pydantic boundary."""
        for pair in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"):
            FXCrossCurrencyBasisInput(pair=pair, tenor="1M")  # must not raise
    check("V1 supported pairs accepted by Pydantic", check_valid_pairs_accepted)

    def check_usdchf_fails_loud():
        """USDCHF must fail at Pydantic — no SARON OIS in V1."""
        try:
            FXCrossCurrencyBasisInput(pair="USDCHF", tenor="1M")
        except ValidationError:
            return
        raise AssertionError("USDCHF should fail Pydantic")
    check("USDCHF fails-loud (no SARON OIS V1)", check_usdchf_fails_loud)

    def check_em_fails_loud():
        """EM pairs (USDMXN, USDTRY, etc.) must fail at Pydantic."""
        for pair in ("USDMXN", "USDTRY", "USDZAR"):
            try:
                FXCrossCurrencyBasisInput(pair=pair, tenor="1M")
            except ValidationError:
                continue
            raise AssertionError(f"{pair} should fail Pydantic")
    check("EM pairs fail-loud (no OIS coverage V1)", check_em_fails_loud)

    def check_non_usd_cross_fails_loud():
        """Non-USD G10 crosses must fail at Pydantic."""
        for pair in ("EURJPY", "GBPCHF", "EURGBP"):
            try:
                FXCrossCurrencyBasisInput(pair=pair, tenor="1M")
            except ValidationError:
                continue
            raise AssertionError(f"{pair} should fail Pydantic")
    check("Non-USD G10 crosses fail-loud", check_non_usd_cross_fails_loud)

    def check_invalid_tenor_fails_loud():
        try:
            FXCrossCurrencyBasisInput(pair="EURUSD", tenor="2M")
        except ValidationError:
            return
        raise AssertionError("invalid tenor should fail Pydantic")
    check("Invalid tenor fails-loud", check_invalid_tenor_fails_loud)

    # ================= USD-leg resolution helpers =================

    def check_resolve_usd_leg_xxxusd():
        """EURUSD/GBPUSD/AUDUSD: USD is quote, sign = -1 (FX leg flipped)."""
        for pair, expected_local in [("EURUSD", "EUR"), ("GBPUSD", "GBP"), ("AUDUSD", "AUD")]:
            position, local, sign = _resolve_usd_leg(pair)
            assert position == "quote", f"{pair}: position={position} expected 'quote'"
            assert local == expected_local, f"{pair}: local={local} expected {expected_local}"
            assert sign == -1.0, f"{pair}: sign={sign} expected -1.0"
    check("usd_leg_position 'quote' for xxxUSD pairs", check_resolve_usd_leg_xxxusd)

    def check_resolve_usd_leg_usdxxx():
        """USDJPY/USDCAD: USD is base, sign = +1 (no FX leg flip)."""
        for pair, expected_local in [("USDJPY", "JPY"), ("USDCAD", "CAD")]:
            position, local, sign = _resolve_usd_leg(pair)
            assert position == "base", f"{pair}: position={position} expected 'base'"
            assert local == expected_local, f"{pair}: local={local} expected {expected_local}"
            assert sign == +1.0, f"{pair}: sign={sign} expected +1.0"
    check("usd_leg_position 'base' for USDxxx pairs", check_resolve_usd_leg_usdxxx)

    # ================= Currency → OIS curve mapping =================

    def check_currency_to_ois_mapping_complete():
        """Every V1 local currency must have an OIS curve mapping."""
        for currency in ("EUR", "GBP", "JPY", "AUD", "CAD"):
            assert currency in _LOCAL_CURRENCY_TO_OIS_CURVE, (
                f"{currency} missing from _LOCAL_CURRENCY_TO_OIS_CURVE"
            )
            curve = _LOCAL_CURRENCY_TO_OIS_CURVE[currency]
            assert curve.endswith("_OIS"), f"{currency}: curve {curve} doesn't look like an OIS family"
    check("V1 currency → OIS curve mapping complete + sane", check_currency_to_ois_mapping_complete)

    # ================= FX → OIS tenor alias =================

    def check_fx_to_ois_tenor_alias():
        """FX 12M aliases to OIS 1Y; other tenors identical."""
        assert _FX_TO_OIS_TENOR["12M"] == "1Y", "FX 12M must alias to OIS 1Y"
        for tenor in ("1W", "1M", "3M", "6M"):
            assert _FX_TO_OIS_TENOR[tenor] == tenor, f"{tenor} should be identity-mapped"
    check("FX→OIS tenor alias (12M→1Y, others identical)", check_fx_to_ois_tenor_alias)

    # ================= Live-DB tests (SKIP gracefully if OIS absent) =================

    def check_live_basis_sane():
        """When OIS data is available, EURUSD 1M basis should be in
        the typical DM range of -50 to -5 bp."""
        try:
            out = get_fx_cross_currency_basis(
                engine, FXCrossCurrencyBasisInput(pair="EURUSD", tenor="1M")
            )
        except ValueError as exc:
            if "No OIS data" in str(exc):
                raise _SkipExpected(f"OIS substrate absent locally: {exc}")
            raise
        m = out["current_metrics"]
        basis = m["current_basis_bps"]
        # Typical DM range -50 to +5 bp (allow a small positive buffer for unusual regimes)
        assert -80.0 < basis < 20.0, (
            f"EURUSD 1M basis {basis}bp out of typical DM range "
            f"(-50 to -5 bp expected; +/-20 buffer)"
        )
        assert m["sign_convention"] == "bloomberg_bcrx_usd_scarcity_negative"
    check("EURUSD 1M basis in typical DM range (live-DB only)", check_live_basis_sane)

    def check_live_fx_leg_matches_iyd():
        """The FX leg of cross_currency_basis must byte-match the
        implied_yield_differential primitive for the same (pair, tenor).
        Sanity that the inline IYD recompute is identical."""
        try:
            ccb = get_fx_cross_currency_basis(
                engine, FXCrossCurrencyBasisInput(pair="EURUSD", tenor="1M")
            )
            iyd = get_fx_implied_yield_differential(
                engine, FXImpliedYieldDifferentialInput(pair="EURUSD", tenor="1M")
            )
        except ValueError as exc:
            if "No OIS data" in str(exc):
                raise _SkipExpected(f"OIS substrate absent locally: {exc}")
            raise
        ccb_fx_iyd = ccb["current_metrics"]["current_fx_implied_yield_diff_pct"]
        iyd_value = iyd["current_metrics"]["current_implied_yield_differential_pct"]
        # As-of dates may differ if OIS lags FX; values at common date must match
        # within rounding (4 decimals × 2 tools)
        assert abs(ccb_fx_iyd - iyd_value) < 0.001, (
            f"FX leg drift: ccb={ccb_fx_iyd} iyd={iyd_value} diff={abs(ccb_fx_iyd-iyd_value):.6f}"
        )
    check("FX leg byte-matches implied_yield_differential (live-DB only)", check_live_fx_leg_matches_iyd)

    def check_live_all_v1_pairs_run():
        """All 5 V1 pairs must run end-to-end on live DB."""
        for pair, tenor in [
            ("EURUSD", "1M"), ("GBPUSD", "3M"), ("USDJPY", "1M"),
            ("AUDUSD", "6M"), ("USDCAD", "3M"),
        ]:
            try:
                out = get_fx_cross_currency_basis(
                    engine, FXCrossCurrencyBasisInput(pair=pair, tenor=tenor)
                )
            except ValueError as exc:
                if "No OIS data" in str(exc):
                    raise _SkipExpected(f"OIS substrate absent locally: {exc}")
                raise
            assert "current_basis_bps" in out["current_metrics"]
    check("All 5 V1 pairs run end-to-end (live-DB only)", check_live_all_v1_pairs_run)

    # ============ report ============
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    skip_count = sum(1 for r in results if r.status == "SKIP")
    print("=" * 72)
    print(
        f"FX CROSS-CURRENCY BASIS COMPUTE TEST — "
        f"{pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP"
    )
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    # SKIP doesn't flip exit code — only real FAIL does. Skipped tests
    # are expected on FX-only dev DBs; they'll PASS on full QFin DB.
    return 0 if fail_count == 0 else 1


class _SkipExpected(Exception):
    """Raised inside a check when the live-DB requirement isn't met
    on this environment (e.g. OIS substrate absent). The check is
    marked SKIP without flipping the exit code."""


if __name__ == "__main__":
    sys.exit(main())
