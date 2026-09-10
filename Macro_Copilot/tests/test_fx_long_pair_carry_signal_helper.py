"""Tests for fx_agent.forwards._shared.long_pair_carry_signal helper.

The helper encapsulates the load-bearing sign convention for
"carry of going LONG the pair" = r_base - r_quote. Tests are
offline-only (no DB), using synthetic but realistic numbers
derived from current rate regimes.

Asserts:
  1. USDJPY-style (USD base, low-yield quote): long pair = long USD
     against JPY low rate → POSITIVE long_pair_carry
  2. EURUSD-style (USD quote, low-yield base): long pair = long EUR
     against USD high rate → NEGATIVE long_pair_carry
  3. USDMXN-style (USD base, high-yield EM quote): long pair = long
     USD against MXN high rate → NEGATIVE long_pair_carry (giving
     up MXN's high rate)
  4. Sign-flip invariant: long_pair_carry(F, S) = -((F/S - 1) * (annual/tenor) * 100)
  5. JPY divisor handling (forward_points / 100 not 10000)
  6. Identity vs fx_carry.carry_annualized_pct: long_pair_carry == -carry_annualized_pct
     for the same (pair, F, S, tenor) inputs.
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fx_agent.forwards._shared import (  # noqa: E402
    long_pair_carry_signal,
    points_to_spot_units,
)


_JPY_DIVISOR = 100.0
_DEFAULT_DIVISOR = 10000.0
_ANNUAL = 252


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


def main() -> int:
    results: list[CheckResult] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append(CheckResult(name, "PASS"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    # ================= Sign convention per pair direction =================

    def check_usdjpy_long_pair_positive():
        """USDJPY 1M with USD rate ~5%, JPY ~0.5%: USD is the high-yielder,
        so per CIP the forward USDJPY trades BELOW spot (USD discount in
        JPY-forward space). Bloomberg forward points are therefore
        NEGATIVE.
        Long pair USDJPY = long USD short JPY = positive carry of ~+4.5%."""
        # Realistic scenario: spot=148.0, F=147.445 → fp_raw = -55.5
        #   (Bloomberg convention; ÷100 divisor for JPY → fp_spot_units = -0.555)
        # (F/S - 1) = -0.555/148 ≈ -0.00375 (raw CIP differential = r_JPY - r_USD)
        # ann = -0.00375 × 12 × 100 = -4.5% (JPY rate is 4.5% below USD)
        # long_pair_carry = -(raw) = +4.5% ✓ (long USD captures the spread)
        carry = long_pair_carry_signal(
            forward_points=-55.0,  # negative: USD-high-rate regime
            spot=148.0,
            pair="USDJPY",
            tenor_days=21,
            jpy_divisor=_JPY_DIVISOR,
            default_divisor=_DEFAULT_DIVISOR,
            annualization_days=_ANNUAL,
        )
        assert 3.0 < carry < 6.0, (
            f"USDJPY long pair carry {carry} out of expected ~+4.5% range"
        )
    check("USDJPY long pair = positive (USD>JPY rates)", check_usdjpy_long_pair_positive)

    def check_eurusd_long_pair_negative():
        """EURUSD 1M with USD ~5.2%, EUR ~3.5%: USD pays more, so per
        CIP forward EURUSD trades ABOVE spot (EUR at premium = USD at
        discount forward). Bloomberg forward points POSITIVE but SMALL
        (~14 raw for 1M, representing ~1.5% annualized spread).
        Long pair EURUSD = long EUR short USD = NEGATIVE carry."""
        # spot=1.085, F=1.0864 → fp_raw = +14 (÷10000 → fp_spot_units = 0.0014)
        # (F/S - 1) = 0.0014/1.085 ≈ 0.00129 (raw CIP = r_USD - r_EUR)
        # ann = 0.00129 × 12 × 100 ≈ 1.55% (USD pays 1.55% more than EUR)
        # long_pair_carry = -(raw) ≈ -1.55% ✓ (long EUR gives up USD rate)
        carry = long_pair_carry_signal(
            forward_points=14.0,  # small positive: realistic 1M EURUSD
            spot=1.085,
            pair="EURUSD",
            tenor_days=21,
            jpy_divisor=_JPY_DIVISOR,
            default_divisor=_DEFAULT_DIVISOR,
            annualization_days=_ANNUAL,
        )
        assert -3.0 < carry < 0.0, (
            f"EURUSD long pair carry {carry} should be small negative (~-1.5%)"
        )
    check("EURUSD long pair = negative (EUR<USD rates)", check_eurusd_long_pair_negative)

    def check_usdmxn_long_pair_negative_em():
        """USDMXN 3M with MXN ~10%, USD ~5%: forward points POSITIVE
        and large (MXN trades at large discount forward = MXN pays more).
        Long pair USDMXN = long USD short MXN = NEGATIVE carry (giving
        up MXN's ~5% rate spread = canonical EM carry trade backwards)."""
        # spot=18.0, F=18.225 → fp_raw = +2250 (3-month, large forward points)
        # F/S - 1 = 0.225/18.0 ≈ 0.0125 → ann (× 252/63 = 4) ≈ +5% (MXN - USD)
        # long_pair = r_USD - r_MXN = -(MXN-USD) ≈ -5% ✓
        carry = long_pair_carry_signal(
            forward_points=2250.0,
            spot=18.0,
            pair="USDMXN",
            tenor_days=63,
            jpy_divisor=_JPY_DIVISOR,
            default_divisor=_DEFAULT_DIVISOR,
            annualization_days=_ANNUAL,
        )
        assert -8.0 < carry < -3.0, (
            f"USDMXN long pair carry {carry} should be ~-5% (long USD vs high-yield MXN)"
        )
    check("USDMXN long pair = negative (long USD vs high-yield MXN)", check_usdmxn_long_pair_negative_em)

    # ================= Sign-flip invariant =================

    def check_sign_flip_invariant():
        """long_pair_carry must equal -((F/S - 1) * (annual/tenor) * 100)
        for any (F, S, pair, tenor)."""
        cases = [
            (55.0, 148.0, "USDJPY", 21),
            (150.0, 1.085, "EURUSD", 21),
            (2250.0, 18.0, "USDMXN", 63),
            (-30.0, 1.265, "GBPUSD", 21),
            (0.0, 1.50, "AUDUSD", 21),  # Edge: zero forward points → zero carry
        ]
        for fp, spot, pair, tenor_days in cases:
            helper_value = long_pair_carry_signal(
                forward_points=fp, spot=spot, pair=pair,
                tenor_days=tenor_days, jpy_divisor=_JPY_DIVISOR,
                default_divisor=_DEFAULT_DIVISOR, annualization_days=_ANNUAL,
            )
            fp_spot_units = points_to_spot_units(
                pair, fp, jpy_divisor=_JPY_DIVISOR, default_divisor=_DEFAULT_DIVISOR
            )
            expected = -((fp_spot_units / spot) * (_ANNUAL / tenor_days) * 100.0)
            assert abs(helper_value - expected) < 1e-9, (
                f"{pair}: helper={helper_value} expected={expected} diff={abs(helper_value-expected)}"
            )
    check("sign-flip invariant: helper = -(F/S - 1) * (annual/tenor) * 100", check_sign_flip_invariant)

    # ================= Divisor handling =================

    def check_jpy_divisor_handling():
        """USDJPY uses /100; other pairs use /10000. Helper must
        delegate to points_to_spot_units correctly.

        The relative magnitudes are determined by the EFFECTIVE
        denominator (divisor × spot), not just the divisor. With
        same raw fp:
          - USDJPY: fp/100/148 = fp/14800
          - EURUSD: fp/10000/1.085 = fp/10850
        So |USDJPY|/|EURUSD| = 10850/14800 ≈ 0.733 (USDJPY's
        denominator is BIGGER, so its output is SMALLER for same
        raw fp). The intuition "100x divisor = 100x output" is wrong
        because high-magnitude spots (USDJPY ~148) partially offset.
        """
        usdjpy_carry = long_pair_carry_signal(
            forward_points=10.0, spot=148.0, pair="USDJPY",
            tenor_days=21, jpy_divisor=_JPY_DIVISOR,
            default_divisor=_DEFAULT_DIVISOR, annualization_days=_ANNUAL,
        )
        eurusd_carry = long_pair_carry_signal(
            forward_points=10.0, spot=1.085, pair="EURUSD",
            tenor_days=21, jpy_divisor=_JPY_DIVISOR,
            default_divisor=_DEFAULT_DIVISOR, annualization_days=_ANNUAL,
        )
        ratio = abs(usdjpy_carry) / abs(eurusd_carry)
        expected_ratio = (_DEFAULT_DIVISOR * 1.085) / (_JPY_DIVISOR * 148.0)
        assert abs(ratio - expected_ratio) < 0.01, (
            f"JPY divisor handling: ratio |USDJPY|/|EURUSD| = {ratio:.4f}, "
            f"expected {expected_ratio:.4f} (= effective-denominator ratio)"
        )
        # Without JPY divisor handling (i.e. if helper used 10000 for JPY),
        # the ratio would be ~0.0073 (100x smaller). Sanity check the
        # divisor IS being applied:
        assert ratio > 0.1, (
            f"JPY divisor likely NOT applied: ratio {ratio} suggests "
            f"USDJPY used /10000 instead of /100."
        )
    check("JPY divisor handling (100 vs 10000)", check_jpy_divisor_handling)

    # ================= Identity vs fx_carry.carry_annualized_pct =================

    def check_identity_vs_fx_carry_math():
        """long_pair_carry MUST be the exact negation of fx_carry's
        carry_annualized_pct math: carry_annualized = (fp_spot_units / spot)
        * (annual / tenor) * 100. We don't import fx_carry compute (would
        need DB), but we replicate the inline math."""
        cases = [
            (55.0, 148.0, "USDJPY", 21),
            (150.0, 1.085, "EURUSD", 21),
            (2250.0, 18.0, "USDMXN", 63),
        ]
        for fp, spot, pair, tenor_days in cases:
            fp_spot_units = points_to_spot_units(
                pair, fp, jpy_divisor=_JPY_DIVISOR, default_divisor=_DEFAULT_DIVISOR
            )
            fx_carry_value = (
                (fp_spot_units / spot) * (_ANNUAL / tenor_days) * 100.0
            )  # = r_quote - r_base (fx_carry convention)
            long_pair_value = long_pair_carry_signal(
                forward_points=fp, spot=spot, pair=pair,
                tenor_days=tenor_days, jpy_divisor=_JPY_DIVISOR,
                default_divisor=_DEFAULT_DIVISOR, annualization_days=_ANNUAL,
            )  # = r_base - r_quote (long-pair convention)
            assert abs(long_pair_value + fx_carry_value) < 1e-9, (
                f"{pair}: long_pair + fx_carry should be 0 exactly. "
                f"long_pair={long_pair_value} fx_carry={fx_carry_value} "
                f"sum={long_pair_value + fx_carry_value}"
            )
    check("long_pair_carry == -fx_carry.carry_annualized_pct (exact)", check_identity_vs_fx_carry_math)

    # ============ report ============
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX LONG-PAIR CARRY SIGNAL HELPER TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
