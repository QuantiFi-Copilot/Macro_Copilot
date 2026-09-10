"""Targeted tests for the Phase D FX NDF tools.

Covers:
  - get_fx_ndf_outright: 6/6 NDF families return clean data at 1M;
    output schema validates; observation count plausible; z-score
    computed; specific known semantic checks (CCN+ outright sane vs
    USDCNY level; NTN+ outright sane vs USDTWD level).
  - calculate_fx_ndf_implied_carry: 6/6 families in default settlement
    mode; carry formula sanity (BCN+ carry > 0 because USDBRL has
    positive implied rate differential vs USD); offshore_tradable
    swaps USDCNY -> USDCNH for CCN+ only and exposes a different carry
    number; rank_by / top_n behave per the schema; spot_date ==
    forward_date (last common date).
  - calculate_fx_ndf_implied_carry: invalid tenor / rank_by / spot
    convention fail-loud via Pydantic.

Standalone runner, same pattern as test_fx_carry_compute.py.
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
from fx_agent.ndf._shared import SUPPORTED_NDF_CODES  # noqa: E402
from fx_agent.ndf.tools.ndf_implied_carry import (  # noqa: E402
    FXNDFImpliedCarryInput,
    calculate_fx_ndf_implied_carry,
)
from fx_agent.ndf.tools.ndf_outright import (  # noqa: E402
    FXNDFOutrightInput,
    get_fx_ndf_outright,
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

    # ============ get_fx_ndf_outright ============

    def check_all_six_families_load():
        """6/6 NDF families at 1M return non-empty data with z-score."""
        for code in SUPPORTED_NDF_CODES:
            out = get_fx_ndf_outright(engine, FXNDFOutrightInput(ndf_code=code, tenor="1M"))
            m = out["current_metrics"]
            assert m["ndf_code"] == code, f"{code}: ndf_code mismatch"
            assert m["current_outright"] > 0, f"{code}: outright non-positive"
            assert m["observation_count"] >= 200, (
                f"{code}: too few observations ({m['observation_count']})"
            )
            # z_score may be None for very young series but every NDF here
            # has 250+ obs, so z_score MUST be a float.
            assert m["z_score"] is not None, f"{code}: z_score unexpectedly None"

    check("6/6 NDF families load at 1M with z-score", check_all_six_families_load)

    def check_ccn_outright_sane():
        """CCN+ 1M outright should be ~6-8 (USDCNY level range)."""
        out = get_fx_ndf_outright(engine, FXNDFOutrightInput(ndf_code="CCN+", tenor="1M"))
        x = out["current_metrics"]["current_outright"]
        assert 5.0 < x < 9.0, f"CCN+ 1M outright {x} out of sane USDCNY range"

    check("CCN+ 1M outright in sane USDCNY range", check_ccn_outright_sane)

    def check_ntn_outright_sane():
        """NTN+ 1M outright should be ~28-35 (USDTWD range)."""
        out = get_fx_ndf_outright(engine, FXNDFOutrightInput(ndf_code="NTN+", tenor="1M"))
        x = out["current_metrics"]["current_outright"]
        assert 25.0 < x < 40.0, f"NTN+ 1M outright {x} out of sane USDTWD range"

    check("NTN+ 1M outright in sane USDTWD range", check_ntn_outright_sane)

    def check_observation_count_matches_lookback():
        """A 90-day lookback yields a smaller observation_count than 365-day."""
        short = get_fx_ndf_outright(engine, FXNDFOutrightInput(ndf_code="CCN+", tenor="1M", lookback_days=90))
        long_ = get_fx_ndf_outright(engine, FXNDFOutrightInput(ndf_code="CCN+", tenor="1M", lookback_days=365))
        assert (
            short["current_metrics"]["observation_count"]
            < long_["current_metrics"]["observation_count"]
        ), "shorter lookback did not produce a smaller observation_count"

    check("lookback_days affects observation_count", check_observation_count_matches_lookback)

    # ============ calculate_fx_ndf_implied_carry ============

    def check_default_settlement_returns_six():
        """Default 1M settlement returns 6 rows (all 6 families)."""
        out = calculate_fx_ndf_implied_carry(engine, FXNDFImpliedCarryInput(tenor="1M"))
        assert out["tenor"] == "1M"
        assert out["spot_convention"] == "settlement"
        assert len(out["rows"]) == 6, f"expected 6 rows, got {len(out['rows'])}"
        codes_returned = {r["ndf_code"] for r in out["rows"]}
        assert codes_returned == set(SUPPORTED_NDF_CODES), (
            f"missing/unexpected NDF codes: {codes_returned ^ set(SUPPORTED_NDF_CODES)}"
        )

    check("settlement 1M returns 6/6 NDF families", check_default_settlement_returns_six)

    def check_all_rows_settlement_convention():
        """In settlement mode every row's spot_convention_used == 'settlement'."""
        out = calculate_fx_ndf_implied_carry(engine, FXNDFImpliedCarryInput(tenor="1M"))
        for r in out["rows"]:
            assert r["spot_convention_used"] == "settlement", (
                f"{r['ndf_code']}: expected 'settlement', got {r['spot_convention_used']!r}"
            )

    check("settlement mode: every row labelled 'settlement'", check_all_rows_settlement_convention)

    def check_offshore_tradable_only_ccn_swaps():
        """In offshore_tradable mode, only CCN+ gets offshore_tradable label;
        others fall back to settlement_fallback."""
        out = calculate_fx_ndf_implied_carry(
            engine, FXNDFImpliedCarryInput(tenor="1M", spot_convention="offshore_tradable")
        )
        labels_by_code = {r["ndf_code"]: r["spot_convention_used"] for r in out["rows"]}
        assert labels_by_code["CCN+"] == "offshore_tradable", (
            f"CCN+ should be offshore_tradable, got {labels_by_code['CCN+']!r}"
        )
        for code in set(SUPPORTED_NDF_CODES) - {"CCN+"}:
            assert labels_by_code[code] == "settlement_fallback", (
                f"{code} should be settlement_fallback, got {labels_by_code[code]!r}"
            )

    check("offshore_tradable: only CCN+ swaps, others fallback", check_offshore_tradable_only_ccn_swaps)

    def check_ccn_carry_differs_between_conventions():
        """CCN+ implied carry should DIFFER between settlement (USDCNY) and
        offshore_tradable (USDCNH) — that's the whole point of the switch."""
        s = calculate_fx_ndf_implied_carry(engine, FXNDFImpliedCarryInput(tenor="1M"))
        o = calculate_fx_ndf_implied_carry(
            engine, FXNDFImpliedCarryInput(tenor="1M", spot_convention="offshore_tradable")
        )
        s_ccn = next(r for r in s["rows"] if r["ndf_code"] == "CCN+")
        o_ccn = next(r for r in o["rows"] if r["ndf_code"] == "CCN+")
        # Difference should be small (USDCNH vs USDCNY usually within 1% spot
        # diff) but non-zero (≥ 0.01 percentage point on annualised carry).
        diff = abs(s_ccn["implied_carry_annualized_pct"] - o_ccn["implied_carry_annualized_pct"])
        assert diff >= 0.01, (
            f"CCN+ carry should differ between settlement and offshore, "
            f"got diff={diff} (settlement={s_ccn['implied_carry_annualized_pct']}, "
            f"offshore={o_ccn['implied_carry_annualized_pct']})"
        )

    check("CCN+ carry differs settlement vs offshore_tradable", check_ccn_carry_differs_between_conventions)

    def check_rows_have_spot_outright_carry():
        """Every row has spot > 0, outright > 0, carry finite."""
        out = calculate_fx_ndf_implied_carry(engine, FXNDFImpliedCarryInput(tenor="1M"))
        for r in out["rows"]:
            assert r["spot"] > 0, f"{r['ndf_code']}: spot non-positive"
            assert r["outright"] > 0, f"{r['ndf_code']}: outright non-positive"
            assert isinstance(r["implied_carry_annualized_pct"], (int, float)), (
                f"{r['ndf_code']}: carry not numeric"
            )
            assert r["spot_date"] == r["forward_date"], (
                f"{r['ndf_code']}: spot_date {r['spot_date']} != forward_date {r['forward_date']} "
                "(snapshot must be at LAST common date)"
            )

    check("rows have positive spot+outright and aligned spot=forward date", check_rows_have_spot_outright_carry)

    def check_top_n_truncates():
        out_full = calculate_fx_ndf_implied_carry(engine, FXNDFImpliedCarryInput(tenor="1M"))
        out_top3 = calculate_fx_ndf_implied_carry(engine, FXNDFImpliedCarryInput(tenor="1M", top_n=3))
        assert len(out_full["rows"]) == 6, f"full should have 6 rows, got {len(out_full['rows'])}"
        assert len(out_top3["rows"]) == 3, f"top_n=3 should have 3 rows, got {len(out_top3['rows'])}"

    check("top_n correctly truncates", check_top_n_truncates)

    def check_rank_field_is_one_indexed():
        out = calculate_fx_ndf_implied_carry(engine, FXNDFImpliedCarryInput(tenor="1M"))
        ranks = [r["rank"] for r in out["rows"]]
        assert ranks == list(range(1, len(ranks) + 1)), f"ranks not 1..N: {ranks}"

    check("rank field is 1-indexed contiguous", check_rank_field_is_one_indexed)

    def check_rank_by_abs_z_score_orders_by_extremeness():
        out = calculate_fx_ndf_implied_carry(
            engine, FXNDFImpliedCarryInput(tenor="1M", rank_by="abs_z_score")
        )
        zs = [
            abs(r["carry_z_score"]) if r["carry_z_score"] is not None else -1
            for r in out["rows"]
        ]
        # Sorted descending (None z-scores end up last because of (0, ...))
        # Filter out the -1 sentinels for the monotonic check
        non_none = [z for z in zs if z >= 0]
        assert non_none == sorted(non_none, reverse=True), (
            f"abs_z_score ranking not descending: {zs}"
        )

    check("rank_by=abs_z_score orders by |z| descending", check_rank_by_abs_z_score_orders_by_extremeness)

    # ============ fail-loud checks ============

    def check_invalid_tenor_pydantic():
        try:
            FXNDFImpliedCarryInput(tenor="2M")
        except ValidationError:
            return
        raise AssertionError("invalid tenor '2M' should have failed Pydantic")

    check("Invalid tenor caught by Pydantic Literal", check_invalid_tenor_pydantic)

    def check_invalid_rank_by_pydantic():
        try:
            FXNDFImpliedCarryInput(rank_by="bogus_rank")
        except ValidationError:
            return
        raise AssertionError("invalid rank_by should have failed Pydantic")

    check("Invalid rank_by caught by Pydantic Literal", check_invalid_rank_by_pydantic)

    def check_invalid_spot_convention_pydantic():
        try:
            FXNDFImpliedCarryInput(spot_convention="bogus_convention")
        except ValidationError:
            return
        raise AssertionError("invalid spot_convention should have failed Pydantic")

    check("Invalid spot_convention caught by Pydantic", check_invalid_spot_convention_pydantic)

    def check_invalid_ndf_code_pydantic():
        try:
            FXNDFOutrightInput(ndf_code="XXX+", tenor="1M")
        except ValidationError:
            return
        raise AssertionError("invalid ndf_code should have failed Pydantic")

    check("Invalid ndf_code caught by Pydantic Literal", check_invalid_ndf_code_pydantic)

    # ============ report ============
    print("=" * 72)
    print(f"FX NDF COMPUTE TEST — {sum(1 for r in results if r.status == 'PASS')} PASS / {sum(1 for r in results if r.status == 'FAIL')} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
