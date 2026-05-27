"""Targeted compute tests for Phase B+ FX carry-extension tools.

Covers:
  - get_fx_implied_yield_differential: per-pair CIP-implied rate spread
  - get_fx_carry_basket: strategy-index primitive
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
from fx_agent.forwards.tools.carry_basket import (  # noqa: E402
    FXCarryBasketInput,
    get_fx_carry_basket,
)
from fx_agent.forwards.tools.fx_carry import get_fx_carry  # noqa: E402
from fx_agent.forwards.tools.fx_carry.schemas import FXCarryInput  # noqa: E402
from fx_agent.forwards.tools.implied_yield_differential import (  # noqa: E402
    FXImpliedYieldDifferentialInput,
    get_fx_implied_yield_differential,
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

    # ================= implied_yield_differential =================

    def check_iyd_eurusd_negative_local_minus_usd():
        """EURUSD: local=EUR, current regime has EUR rate < USD rate
        → local_minus_usd should be NEGATIVE."""
        out = get_fx_implied_yield_differential(
            engine, FXImpliedYieldDifferentialInput(pair="EURUSD", tenor="1M")
        )
        m = out["current_metrics"]
        assert m["local_currency"] == "EUR"
        assert m["usd_leg_position"] == "quote"
        assert m["current_implied_yield_differential_pct"] < 0, (
            f"EURUSD iyd {m['current_implied_yield_differential_pct']} not <0"
        )
    check("iyd EURUSD local_minus_usd negative (EUR<USD regime)", check_iyd_eurusd_negative_local_minus_usd)

    def check_iyd_usdmxn_positive_local_minus_usd():
        """USDMXN: local=MXN, MXN rate >> USD rate → positive."""
        out = get_fx_implied_yield_differential(
            engine, FXImpliedYieldDifferentialInput(pair="USDMXN", tenor="3M")
        )
        m = out["current_metrics"]
        assert m["local_currency"] == "MXN"
        assert m["usd_leg_position"] == "base"
        assert m["current_implied_yield_differential_pct"] > 0, (
            f"USDMXN iyd {m['current_implied_yield_differential_pct']} not >0"
        )
    check("iyd USDMXN local_minus_usd positive (MXN>USD)", check_iyd_usdmxn_positive_local_minus_usd)

    def check_iyd_usdjpy_negative_local_minus_usd():
        """USDJPY: local=JPY, JPY rate << USD rate → negative."""
        out = get_fx_implied_yield_differential(
            engine, FXImpliedYieldDifferentialInput(pair="USDJPY", tenor="1M")
        )
        m = out["current_metrics"]
        assert m["local_currency"] == "JPY"
        assert m["usd_leg_position"] == "base"
        assert m["current_implied_yield_differential_pct"] < 0, (
            f"USDJPY iyd {m['current_implied_yield_differential_pct']} not <0"
        )
    check("iyd USDJPY local_minus_usd negative (JPY<USD)", check_iyd_usdjpy_negative_local_minus_usd)

    def check_iyd_identity_vs_fx_carry():
        """|iyd| == |fx_carry.carry_annualized_pct| within rounding.

        Both formulas compute (F/S - 1) * (annual/tenor_days) * 100 ;
        iyd applies a sign flip based on usd_leg_position. The ABS
        VALUE must match across both tools.
        """
        tol = 0.05  # 5 bp tolerance (rounding to 2 dp on fx_carry vs 4 dp on iyd)
        for pair, scope, tenor in [
            ("EURUSD", "G10", "1M"),
            ("USDJPY", "G10", "1M"),
            ("AUDUSD", "G10", "1M"),
            ("USDMXN", "EM", "3M"),
        ]:
            iyd = get_fx_implied_yield_differential(
                engine, FXImpliedYieldDifferentialInput(pair=pair, tenor=tenor)
            )["current_metrics"]["current_implied_yield_differential_pct"]
            carry_out = get_fx_carry(
                engine, FXCarryInput(tenor=tenor, market_scope=scope)
            )
            row = next((r for r in carry_out["rows"] if r["pair"] == pair), None)
            assert row is not None, f"{pair}: no fx_carry row in scope={scope}"
            ca = row["carry_annualized_pct"]
            assert abs(abs(iyd) - abs(ca)) < tol, (
                f"{pair} {tenor}: |iyd|={abs(iyd)} |fx_carry.carry|={abs(ca)} "
                f"diff={abs(abs(iyd)-abs(ca)):.4f} > tol={tol}"
            )
    check("iyd identity check vs fx_carry.carry_annualized_pct", check_iyd_identity_vs_fx_carry)

    def check_iyd_fails_loud_on_non_usd_cross():
        try:
            get_fx_implied_yield_differential(
                engine, FXImpliedYieldDifferentialInput(pair="EURJPY", tenor="1M")
            )
        except ValueError as exc:
            assert "USD" in str(exc).upper()
            return
        raise AssertionError("non-USD cross EURJPY should fail loud")
    check("iyd EURJPY (no USD leg) fails loud", check_iyd_fails_loud_on_non_usd_cross)

    def check_iyd_invalid_tenor_pydantic():
        try:
            FXImpliedYieldDifferentialInput(pair="EURUSD", tenor="2M")
        except ValidationError:
            return
        raise AssertionError("invalid tenor should have failed Pydantic")
    check("iyd invalid tenor caught by Pydantic", check_iyd_invalid_tenor_pydantic)

    # ===================== carry_basket =====================

    def check_basket_g10_constituents_canonical():
        """G10 long_short top_n=3: long pairs should be the high-USD-
        carry side (USDxxx with low-yield xxx), short should be xxxUSD
        with low-yield xxx. Sign convention verification."""
        out = get_fx_carry_basket(
            engine, FXCarryBasketInput(market_scope="G10", tenor="1M", top_n=3)
        )
        constituents = out["constituent_pairs"]
        long_pairs = [c.split(" ")[0] for c in constituents if "(long)" in c]
        short_pairs = [c.split(" ")[0] for c in constituents if "(short)" in c]
        assert len(long_pairs) == 3 and len(short_pairs) == 3
        # All long pairs must start with USD (long USD = high carry vs
        # low-yielders) given the current rate regime
        assert all(p.startswith("USD") for p in long_pairs), (
            f"G10 long_short long leg should be USDxxx pairs in current "
            f"regime, got {long_pairs}"
        )
        # All short pairs must end with USD (short low-yielders against USD)
        assert all(p.endswith("USD") for p in short_pairs), (
            f"G10 long_short short leg should be xxxUSD pairs, got {short_pairs}"
        )
    check("basket G10 constituents canonical (long USDxxx / short xxxUSD)", check_basket_g10_constituents_canonical)

    def check_basket_em_constituents_canonical():
        """EM long_short top_n=3: short side should include the
        highest-yielding EM currencies (TRY/MXN/ZAR typically), long
        side should be the lower-yielding EM currencies (PHP/THB/SGD)."""
        out = get_fx_carry_basket(
            engine, FXCarryBasketInput(market_scope="EM", tenor="3M", top_n=3)
        )
        constituents = out["constituent_pairs"]
        short_pairs = [c.split(" ")[0] for c in constituents if "(short)" in c]
        # TRY is currently the highest-yielding EM currency by far
        # (≥40% local rate); it must be in the SHORT leg (since long
        # TRY = short USDTRY captures the carry).
        assert "USDTRY" in short_pairs, (
            f"EM long_short top_n=3 must include USDTRY in short leg "
            f"(canonical highest-yielder), got short={short_pairs}"
        )
    check("basket EM constituents canonical (USDTRY in short leg)", check_basket_em_constituents_canonical)

    def check_basket_long_only_only_long_pairs():
        out = get_fx_carry_basket(
            engine, FXCarryBasketInput(
                market_scope="G10", tenor="1M", top_n=3,
                basket_construction="long_only_top_n",
            ),
        )
        constituents = out["constituent_pairs"]
        long_pairs = [c for c in constituents if "(long)" in c]
        short_pairs = [c for c in constituents if "(short)" in c]
        assert len(long_pairs) == 3
        assert len(short_pairs) == 0
    check("basket long_only_top_n returns only long pairs", check_basket_long_only_only_long_pairs)

    def check_basket_snapshot_sane():
        out = get_fx_carry_basket(
            engine, FXCarryBasketInput(market_scope="G10", tenor="1M", top_n=3)
        )
        s = out["snapshot"]
        assert s["rebalance_frequency_days"] == 21
        assert s["signal_lag_days"] == 1
        assert s["weighting_scheme"] == "equal_weight"
        assert s["transaction_cost_basis"] == "none"
        assert s["observation_count"] > 200  # ~2y of daily returns
        assert s["annualized_volatility_pct"] is not None
        assert s["sharpe_ratio"] is not None
        # Vol of an FX carry basket should be 5-25% annualized
        assert 2.0 < s["annualized_volatility_pct"] < 30.0, (
            f"ann vol {s['annualized_volatility_pct']} out of sane range"
        )
    check("basket snapshot sane (locks + vol + obs)", check_basket_snapshot_sane)

    def check_basket_series_length_matches_obs():
        out = get_fx_carry_basket(
            engine, FXCarryBasketInput(market_scope="G10", tenor="1M", top_n=3)
        )
        s = out["snapshot"]
        rows = out["cumulative_excess_return_series"]["rows"]
        # Series length == observation_count (one cum-return value per
        # day we have a basket return for)
        assert len(rows) == s["observation_count"], (
            f"series len {len(rows)} != observation_count {s['observation_count']}"
        )
    check("basket series length == observation_count", check_basket_series_length_matches_obs)

    def check_basket_series_units_ratio():
        out = get_fx_carry_basket(
            engine, FXCarryBasketInput(market_scope="G10", tenor="1M", top_n=3)
        )
        units = out["cumulative_excess_return_series"]["units"]
        assert units == "ratio", f"expected units=ratio got {units}"
    check("basket series units == 'ratio'", check_basket_series_units_ratio)

    def check_basket_invalid_construction_pydantic():
        try:
            FXCarryBasketInput(market_scope="G10", basket_construction="bogus")
        except ValidationError:
            return
        raise AssertionError("invalid basket_construction should fail Pydantic")
    check("basket invalid basket_construction caught by Pydantic", check_basket_invalid_construction_pydantic)

    def check_basket_invalid_scope_pydantic():
        try:
            FXCarryBasketInput(market_scope="BOGUS")
        except ValidationError:
            return
        raise AssertionError("invalid market_scope should fail Pydantic")
    check("basket invalid market_scope caught by Pydantic", check_basket_invalid_scope_pydantic)

    # ============ report ============
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX CARRY EXTENSIONS COMPUTE TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
