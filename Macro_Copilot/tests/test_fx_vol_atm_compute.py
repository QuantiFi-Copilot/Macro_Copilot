"""Targeted tests for Phase E1 FX ATM vol tools.

Covers the 4 primitives:
  - get_fx_atm_vol_level: snapshot semantics + bid/ask field switch
  - get_fx_vol_term_structure: 5/5 standard tenors short-to-long
  - run_fx_vol_scanner: G10 / EM / ALL scopes + rank_by + top_n
  - get_fx_vol_z_score: time-series consistency (last point ==
    snapshot z), emitted-count vs total observations, units label
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
from fx_agent.vol._shared import SUPPORTED_VOL_TENORS  # noqa: E402
from fx_agent.vol.tools.atm_vol_level import (  # noqa: E402
    FXAtmVolLevelInput,
    get_fx_atm_vol_level,
)
from fx_agent.vol.tools.vol_scanner import (  # noqa: E402
    FXVolScannerInput,
    run_fx_vol_scanner,
)
from fx_agent.vol.tools.vol_term_structure import (  # noqa: E402
    FXVolTermStructureInput,
    get_fx_vol_term_structure,
)
from fx_agent.vol.tools.vol_z_score import (  # noqa: E402
    FXVolZScoreInput,
    get_fx_vol_z_score,
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

    # ============ atm_vol_level ============

    def check_atm_vol_level_eurusd_sane():
        out = get_fx_atm_vol_level(engine, FXAtmVolLevelInput(pair="EURUSD", tenor="1M"))
        m = out["current_metrics"]
        assert 2.0 < m["current_atm_vol_pct"] < 30.0, (
            f"EURUSD 1M vol {m['current_atm_vol_pct']} out of sane 2-30% range"
        )
        assert m["observation_count"] >= 200
        assert m["z_score"] is not None
        assert m["vendor_ticker"] == "EURUSDV1M Curncy"
    check("atm_vol_level EURUSD 1M in sane 2-30% range", check_atm_vol_level_eurusd_sane)

    def check_atm_vol_level_12m_bbg_ticker_alias():
        """12M internal tenor should resolve to V1Y BBG ticker."""
        out = get_fx_atm_vol_level(engine, FXAtmVolLevelInput(pair="EURUSD", tenor="12M"))
        assert out["current_metrics"]["vendor_ticker"] == "EURUSDV1Y Curncy"
    check("atm_vol_level 12M -> V1Y BBG ticker alias", check_atm_vol_level_12m_bbg_ticker_alias)

    def check_atm_vol_change_in_vol_points_not_pct():
        """Vol changes should be ABSOLUTE points, not percent of vol level."""
        out = get_fx_atm_vol_level(engine, FXAtmVolLevelInput(pair="EURUSD", tenor="1M"))
        m = out["current_metrics"]
        if m["daily_change_vol_pts"] is not None:
            # daily move on a ~6% vol pair shouldn't exceed +/-2 vol points
            assert abs(m["daily_change_vol_pts"]) < 2.0, (
                f"daily change {m['daily_change_vol_pts']} suggests % not vol pts"
            )
    check("atm_vol_level changes in vol points (not pct)", check_atm_vol_change_in_vol_points_not_pct)

    def check_atm_vol_pair_classes():
        """Works on G10 majors, EM, NDF-currency vol-only, G10 crosses."""
        for pair in ("EURUSD", "USDJPY", "USDMXN", "USDCNH", "EURJPY", "GBPCHF"):
            out = get_fx_atm_vol_level(engine, FXAtmVolLevelInput(pair=pair, tenor="1M"))
            assert out["current_metrics"]["current_atm_vol_pct"] > 0
    check("atm_vol_level works on G10+EM+NDF+crosses pair classes", check_atm_vol_pair_classes)

    def check_atm_vol_bid_ask_field_switch():
        """field_name=PX_BID returns a numeric vol; bid <= ask invariant.

        Note: PX_LAST is the last-trade price (not the current mid), so
        it can fall outside the current [bid, ask] band — we only assert
        the bid <= ask invariant which is structural to the quote.
        """
        bid = get_fx_atm_vol_level(engine, FXAtmVolLevelInput(pair="EURUSD", tenor="1M", field_name="PX_BID"))
        ask = get_fx_atm_vol_level(engine, FXAtmVolLevelInput(pair="EURUSD", tenor="1M", field_name="PX_ASK"))
        b = bid["current_metrics"]["current_atm_vol_pct"]
        a = ask["current_metrics"]["current_atm_vol_pct"]
        assert b > 0 and a > 0, f"bid/ask vols not both positive: {b}/{a}"
        # Allow rounding noise of 0.01 vol points
        assert b - 0.01 <= a, f"bid {b} > ask {a} — quote inversion"
    check("atm_vol_level bid <= ask", check_atm_vol_bid_ask_field_switch)

    # ============ vol_term_structure ============

    def check_term_structure_5_tenors_short_to_long():
        out = get_fx_vol_term_structure(engine, FXVolTermStructureInput(pair="EURUSD"))
        assert out["pair"] == "EURUSD"
        rows = out["rows"]
        assert len(rows) == 5, f"expected 5 tenors, got {len(rows)}: {[r['tenor'] for r in rows]}"
        assert [r["tenor"] for r in rows] == list(SUPPORTED_VOL_TENORS)
    check("term_structure EURUSD returns 5 tenors short-to-long", check_term_structure_5_tenors_short_to_long)

    def check_term_structure_each_tenor_has_z_score():
        out = get_fx_vol_term_structure(engine, FXVolTermStructureInput(pair="EURUSD"))
        for r in out["rows"]:
            assert r["z_score"] is not None, f"{r['tenor']}: z_score unexpectedly None"
            assert r["current_atm_vol_pct"] > 0
    check("term_structure each tenor has z-score + positive vol", check_term_structure_each_tenor_has_z_score)

    def check_term_structure_fails_loud_unknown_pair():
        try:
            get_fx_vol_term_structure(engine, FXVolTermStructureInput(pair="XXXXXX"))
        except ValueError:
            return
        raise AssertionError("unknown pair should have raised ValueError")
    check("term_structure unknown pair fails loud", check_term_structure_fails_loud_unknown_pair)

    # ============ vol_scanner ============

    def check_scanner_g10_returns_6_pairs():
        out = run_fx_vol_scanner(engine, FXVolScannerInput(tenor="1M", market_scope="G10"))
        assert out["market_scope"] == "G10"
        assert len(out["rows"]) == 6, f"expected 6 G10 pairs, got {len(out['rows'])}"
    check("scanner G10 1M returns 6 pairs", check_scanner_g10_returns_6_pairs)

    def check_scanner_em_returns_17_pairs():
        out = run_fx_vol_scanner(engine, FXVolScannerInput(tenor="1M", market_scope="EM"))
        assert len(out["rows"]) == 17, f"expected 17 EM pairs, got {len(out['rows'])}"
    check("scanner EM 1M returns 17 pairs", check_scanner_em_returns_17_pairs)

    def check_scanner_g10_crosses_returns_11_pairs():
        out = run_fx_vol_scanner(engine, FXVolScannerInput(tenor="1M", market_scope="G10_CROSSES"))
        assert len(out["rows"]) == 11, f"expected 11 G10 cross pairs, got {len(out['rows'])}"
    check("scanner G10_CROSSES 1M returns 11 pairs", check_scanner_g10_crosses_returns_11_pairs)

    def check_scanner_all_returns_34_pairs():
        out = run_fx_vol_scanner(engine, FXVolScannerInput(tenor="1M", market_scope="ALL"))
        # 6 G10 + 17 EM + 11 crosses = 34
        assert len(out["rows"]) == 34, f"expected 34 ALL pairs, got {len(out['rows'])}"
    check("scanner ALL 1M returns 34 pairs (6 G10 + 17 EM + 11 crosses)", check_scanner_all_returns_34_pairs)

    def check_scanner_rank_signed_descending():
        out = run_fx_vol_scanner(engine, FXVolScannerInput(tenor="1M", market_scope="G10", rank_by="vol_signed"))
        vols = [r["current_atm_vol_pct"] for r in out["rows"]]
        assert vols == sorted(vols, reverse=True), f"vol_signed not descending: {vols}"
    check("scanner vol_signed ranks descending", check_scanner_rank_signed_descending)

    def check_scanner_top_n_truncates():
        out = run_fx_vol_scanner(engine, FXVolScannerInput(tenor="1M", market_scope="ALL", top_n=5))
        assert len(out["rows"]) == 5
    check("scanner top_n=5 truncates correctly", check_scanner_top_n_truncates)

    def check_scanner_rank_field_one_indexed():
        out = run_fx_vol_scanner(engine, FXVolScannerInput(tenor="1M", market_scope="G10"))
        ranks = [r["rank"] for r in out["rows"]]
        assert ranks == list(range(1, len(ranks) + 1))
    check("scanner rank field 1-indexed contiguous", check_scanner_rank_field_one_indexed)

    # ============ vol_z_score ============

    def check_z_score_series_consistent_with_snapshot():
        """The last emitted z-score in the series MUST equal the
        snapshot z-score from atm_vol_level (same window/min_periods)."""
        zs = get_fx_vol_z_score(engine, FXVolZScoreInput(pair="EURUSD", tenor="1M"))
        snap = get_fx_atm_vol_level(engine, FXAtmVolLevelInput(pair="EURUSD", tenor="1M"))
        assert abs(zs["snapshot"]["current_z_score"] - snap["current_metrics"]["z_score"]) < 1e-3, (
            f"z series last point {zs['snapshot']['current_z_score']} != snapshot "
            f"z {snap['current_metrics']['z_score']}"
        )
    check("z_score series last point == atm_vol_level snapshot z", check_z_score_series_consistent_with_snapshot)

    def check_z_score_units_label():
        out = get_fx_vol_z_score(engine, FXVolZScoreInput(pair="EURUSD", tenor="1M"))
        assert out["units"] == "z_score"
    check("z_score output units == 'z_score'", check_z_score_units_label)

    def check_z_score_emitted_le_total():
        """Emitted observation count must be <= full series count."""
        out = get_fx_vol_z_score(engine, FXVolZScoreInput(pair="EURUSD", tenor="1M"))
        s = out["snapshot"]
        assert s["observation_count_emitted"] <= s["observation_count_full_series"]
        assert s["observation_count_emitted"] == len(out["rows"])
    check("z_score emitted count <= full + == len(rows)", check_z_score_emitted_le_total)

    def check_z_score_rows_monotonic_dates():
        out = get_fx_vol_z_score(engine, FXVolZScoreInput(pair="EURUSD", tenor="1M"))
        dates = [r["trade_date"] for r in out["rows"]]
        assert dates == sorted(dates), "z_score rows not date-sorted ascending"
    check("z_score rows date-sorted ascending", check_z_score_rows_monotonic_dates)

    # ============ fail-loud Pydantic checks ============

    def check_invalid_tenor_pydantic():
        try:
            FXAtmVolLevelInput(pair="EURUSD", tenor="2M")
        except ValidationError:
            return
        raise AssertionError("invalid tenor should have failed Pydantic")
    check("Invalid tenor caught by Pydantic Literal", check_invalid_tenor_pydantic)

    def check_invalid_scanner_scope_pydantic():
        try:
            FXVolScannerInput(market_scope="BOGUS")
        except ValidationError:
            return
        raise AssertionError("invalid market_scope should have failed Pydantic")
    check("Invalid scanner market_scope caught by Pydantic", check_invalid_scanner_scope_pydantic)

    def check_invalid_scanner_rank_pydantic():
        try:
            FXVolScannerInput(rank_by="bogus")
        except ValidationError:
            return
        raise AssertionError("invalid rank_by should have failed Pydantic")
    check("Invalid scanner rank_by caught by Pydantic", check_invalid_scanner_rank_pydantic)

    # ============ report ============
    print("=" * 72)
    print(f"FX ATM VOL COMPUTE TEST — {sum(1 for r in results if r.status == 'PASS')} PASS / {sum(1 for r in results if r.status == 'FAIL')} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
