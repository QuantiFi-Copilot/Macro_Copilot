"""Targeted tests for ``fx_agent.spot.tools.fx_panel``.

Pins Codex's Phase B test coverage contract (validated 2026-05-25):

- G10 scope returns exactly 9 columns (G10 majors only — crosses
  excluded via fx_family filter).
- EM scope returns exactly 9 columns (EM majors).
- G10_CROSSES scope returns exactly 11 columns.
- ALL scope returns 29 columns (9 G10 + 9 EM + 11 G10 crosses) —
  note this is larger than the "18 spot_fx" sanity total because
  the readiness gate's "18" explicitly filtered to {G10_SPOT, EM_SPOT}
  families, while ALL here means every fx_spot row.
- Columns are pair names (e.g. "EURUSD"), NOT vendor tickers
  (e.g. "EURUSD Curncy").
- min/max date are preserved through the pivot + missing-data policy
  for an EM-scope full-history fetch.
- Invalid market_scope raises pydantic ValidationError at the
  schema boundary (fail-loud).
- Empty fetch (start_date in the future) raises ValueError at the
  primitive boundary (fail-loud — substrate primitive cannot return
  a valid empty Panel).
- TypedPanel artifact round-trips cleanly via FXPanelOutput.model_validate
  → panel.payload shape matches the reported (n_observations, n_pairs).
- units_by_column is 'price' for every column.

Standalone runner, same style as ``test_fx_carry_compute.py`` and
``test_fx_forward_curve_compute.py``.
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from fx_agent.spot.tools.fx_panel import (  # noqa: E402
    FXPanelInput,
    FXPanelOutput,
    calculate_fx_panel,
)
from shared.artifacts.types import Panel  # noqa: E402


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


_EXPECTED_G10_PAIRS = frozenset({
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
    "USDCHF", "NZDUSD", "USDNOK", "USDSEK",
})
_EXPECTED_EM_PAIRS = frozenset({
    "USDMXN", "USDBRL", "USDZAR", "USDTRY", "USDPLN",
    "USDHUF", "USDKRW", "USDIDR", "USDPHP",
})


def _assert_pair_set(actual: list[str], expected: frozenset[str], scope_label: str) -> None:
    actual_set = set(actual)
    if actual_set != expected:
        missing = expected - actual_set
        extra = actual_set - expected
        bits = []
        if missing:
            bits.append(f"missing={sorted(missing)}")
        if extra:
            bits.append(f"unexpected={sorted(extra)}")
        raise AssertionError(
            f"{scope_label} pair set mismatch: {' '.join(bits)}"
        )


def main() -> int:
    engine = get_db_engine()
    results: list[CheckResult] = []
    recent_start = date(2025, 5, 22)

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append(CheckResult(name, "PASS"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    # --- 1. G10 scope returns exactly 9 columns (majors only)
    def check_g10_count():
        out = calculate_fx_panel(
            engine, FXPanelInput(market_scope="G10", start_date=recent_start)
        )
        assert out["n_pairs"] == 9, f"expected 9, got {out['n_pairs']}"
        _assert_pair_set(out["columns"], _EXPECTED_G10_PAIRS, "G10")
    check("G10 returns 9 pairs (majors only, crosses excluded)", check_g10_count)

    # --- 2. EM scope returns exactly 9 columns
    def check_em_count():
        out = calculate_fx_panel(
            engine, FXPanelInput(market_scope="EM", start_date=recent_start)
        )
        assert out["n_pairs"] == 9, f"expected 9, got {out['n_pairs']}"
        _assert_pair_set(out["columns"], _EXPECTED_EM_PAIRS, "EM")
    check("EM returns 9 pairs", check_em_count)

    # --- 3. G10_CROSSES scope returns 11 columns
    def check_crosses_count():
        out = calculate_fx_panel(
            engine, FXPanelInput(market_scope="G10_CROSSES", start_date=recent_start)
        )
        assert out["n_pairs"] == 11, f"expected 11, got {out['n_pairs']}"
        # don't pin the exact set (depends on fx_crosses.yml — may grow);
        # but assert all are within the G10 currency set.
        g10_ccys = {"EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "NOK", "SEK", "USD"}
        for pair in out["columns"]:
            assert len(pair) == 6, f"unexpected pair format {pair!r}"
            assert pair[:3] in g10_ccys and pair[3:] in g10_ccys, (
                f"cross pair {pair} contains non-G10 currency"
            )
    check("G10_CROSSES returns 11 pairs in G10 currency set", check_crosses_count)

    # --- 4. ALL scope returns 29 columns
    def check_all_count():
        out = calculate_fx_panel(
            engine, FXPanelInput(market_scope="ALL", start_date=recent_start)
        )
        assert out["n_pairs"] == 29, f"expected 29 (9+9+11), got {out['n_pairs']}"
    check("ALL returns 29 pairs (9 G10 + 9 EM + 11 crosses)", check_all_count)

    # --- 5. Columns are pair names, not vendor tickers
    def check_columns_are_pairs():
        out = calculate_fx_panel(
            engine, FXPanelInput(market_scope="G10", start_date=recent_start)
        )
        for col in out["columns"]:
            assert " " not in col, (
                f"column {col!r} contains a space — looks like a vendor "
                f"ticker (e.g. 'EURUSD Curncy') not a pair name (e.g. 'EURUSD')"
            )
            assert "Curncy" not in col, (
                f"column {col!r} contains 'Curncy' — vendor-ticker leak"
            )
            assert len(col) == 6, f"pair {col!r} not the expected 6-char form"
    check("Columns are pair names (no spaces, no 'Curncy')", check_columns_are_pairs)

    # --- 6. min/max date preserved through pivot + policy for EM full history
    def check_dates_preserved():
        out = calculate_fx_panel(
            engine, FXPanelInput(market_scope="EM", start_date=date(2000, 1, 1))
        )
        assert out["as_of_start"] == "2000-01-03", (
            f"expected as_of_start=2000-01-03, got {out['as_of_start']!r}"
        )
        assert out["as_of_end"] == "2026-05-22", (
            f"expected as_of_end=2026-05-22, got {out['as_of_end']!r}"
        )
    check("EM full-history min_date=2000-01-03 max_date=2026-05-22 preserved", check_dates_preserved)

    # --- 7. Invalid market_scope fails loud at Pydantic boundary
    def check_invalid_scope_fail_loud():
        try:
            FXPanelInput(market_scope="ZZ_INVALID", start_date=recent_start)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError on invalid market_scope")
    check("Invalid market_scope raises ValidationError", check_invalid_scope_fail_loud)

    # --- 8. Empty window fails loud at primitive boundary
    def check_empty_window_fail_loud():
        try:
            calculate_fx_panel(
                engine,
                FXPanelInput(market_scope="EM", start_date=date(2099, 1, 1)),
            )
        except ValueError as e:
            msg = str(e)
            assert "no observations found" in msg, (
                f"expected 'no observations found' in error, got: {msg[:120]}"
            )
            return
        raise AssertionError("expected ValueError on empty window")
    check("Empty fetch raises ValueError (fail-loud, no error envelope)", check_empty_window_fail_loud)

    # --- 9. Typed Panel artifact round-trips via model_validate
    def check_panel_round_trip():
        out = calculate_fx_panel(
            engine, FXPanelInput(market_scope="EM", start_date=date(2000, 1, 1))
        )
        validated = FXPanelOutput.model_validate(out)
        assert isinstance(validated.panel, Panel), (
            f"expected Panel after model_validate, got {type(validated.panel).__name__}"
        )
        # Panel shape matches output metadata
        n_rows, n_cols = validated.panel.payload.shape
        assert n_rows == out["n_observations"], (
            f"panel rows ({n_rows}) != n_observations ({out['n_observations']})"
        )
        assert n_cols == out["n_pairs"], (
            f"panel cols ({n_cols}) != n_pairs ({out['n_pairs']})"
        )
        # Lineage step recorded with the canonical tool name
        assert len(validated.panel.lineage.steps) == 1
        assert validated.panel.lineage.steps[0].name == "calculate_fx_panel_tool"
    check("Typed Panel round-trips via FXPanelOutput.model_validate", check_panel_round_trip)

    # --- 10. units_by_column is 'price' for every column
    def check_units_all_price():
        out = calculate_fx_panel(
            engine, FXPanelInput(market_scope="ALL", start_date=recent_start)
        )
        units = out["units_by_column"]
        assert len(units) == out["n_pairs"], "units count != n_pairs"
        for col, unit in units.items():
            assert unit == "price", f"{col}: expected 'price', got {unit!r}"
    check("units_by_column is 'price' for every FX spot column", check_units_all_price)

    # --- Summary
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX PANEL COMPUTE TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{(': ' + r.detail) if r.detail else ''}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
