"""Targeted tests for ``fx_agent.forwards.tools.fx_forwards_panel``.

Phase F1 (2026-05-27).

Mirror of ``test_fx_panel_compute.py`` for the fx_forward substrate.

Pins:
- G10 1M scope returns a non-empty panel with valid pairs.
- EM 1M scope returns a non-empty panel.
- ALL 1M returns G10 + EM combined.
- Columns are pair names (no spaces, no 'Curncy').
- Invalid market_scope/tenor fails loud at Pydantic.
- G10_CROSSES is REJECTED at Pydantic (closed Literal — no fwd substrate).
- Empty window (future start_date) raises ValueError at primitive boundary.
- Typed Panel artifact round-trips via FXForwardsPanelOutput.model_validate.
- units_by_column is 'price' for every column (forward points are pip levels).
- Lineage step name is 'calculate_fx_forwards_panel_tool'.
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
from fx_agent.forwards.tools.fx_forwards_panel import (  # noqa: E402
    FXForwardsPanelInput,
    FXForwardsPanelOutput,
    calculate_fx_forwards_panel,
)
from shared.artifacts.types import Panel  # noqa: E402


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


def main() -> int:
    engine = get_db_engine()
    results: list[CheckResult] = []
    recent_start = date(2024, 1, 1)

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append(CheckResult(name, "PASS"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    # --- 1. G10 1M happy path
    def check_g10():
        out = calculate_fx_forwards_panel(
            engine,
            FXForwardsPanelInput(
                market_scope="G10", tenor="1M", start_date=recent_start,
            ),
        )
        assert out["n_pairs"] >= 5, f"expected >=5 G10 1M pairs, got {out['n_pairs']}"
        assert out["tenor"] == "1M"
        assert out["market_scope"] == "G10"
        for col in out["columns"]:
            assert len(col) == 6, f"pair {col!r} not 6-char"
    check("G10 1M returns non-empty panel with valid pairs", check_g10)

    # --- 2. EM 1M happy path
    def check_em():
        out = calculate_fx_forwards_panel(
            engine,
            FXForwardsPanelInput(
                market_scope="EM", tenor="1M", start_date=recent_start,
            ),
        )
        assert out["n_pairs"] >= 5, f"expected >=5 EM 1M pairs, got {out['n_pairs']}"
        assert out["market_scope"] == "EM"
    check("EM 1M returns non-empty panel", check_em)

    # --- 3. ALL 1M is the union (>= G10 + EM)
    def check_all_union():
        g10 = calculate_fx_forwards_panel(
            engine,
            FXForwardsPanelInput(market_scope="G10", tenor="1M", start_date=recent_start),
        )
        em = calculate_fx_forwards_panel(
            engine,
            FXForwardsPanelInput(market_scope="EM", tenor="1M", start_date=recent_start),
        )
        all_ = calculate_fx_forwards_panel(
            engine,
            FXForwardsPanelInput(market_scope="ALL", tenor="1M", start_date=recent_start),
        )
        # ALL must be at least the union (may differ by orphan pairs but never less)
        assert all_["n_pairs"] >= max(g10["n_pairs"], em["n_pairs"])
    check("ALL 1M is at least max(G10, EM) panel size", check_all_union)

    # --- 4. Columns are pair names
    def check_columns_are_pairs():
        out = calculate_fx_forwards_panel(
            engine,
            FXForwardsPanelInput(market_scope="G10", tenor="1M", start_date=recent_start),
        )
        for col in out["columns"]:
            assert " " not in col, f"column {col!r} contains a space"
            assert "Curncy" not in col, f"column {col!r} contains 'Curncy'"
            assert len(col) == 6
    check("Columns are pair names (no spaces, no 'Curncy')", check_columns_are_pairs)

    # --- 5. Invalid market_scope fails loud
    def check_invalid_scope_fail_loud():
        try:
            FXForwardsPanelInput(market_scope="ZZ", tenor="1M", start_date=recent_start)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError on invalid market_scope")
    check("Invalid market_scope raises ValidationError", check_invalid_scope_fail_loud)

    # --- 6. G10_CROSSES is rejected (no fwd substrate for crosses)
    def check_crosses_rejected():
        try:
            FXForwardsPanelInput(market_scope="G10_CROSSES", tenor="1M", start_date=recent_start)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError — G10_CROSSES not in Literal")
    check("G10_CROSSES rejected at Pydantic boundary", check_crosses_rejected)

    # --- 7. Invalid tenor fails loud
    def check_invalid_tenor_fail_loud():
        try:
            FXForwardsPanelInput(market_scope="G10", tenor="2M", start_date=recent_start)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError on invalid tenor")
    check("Invalid tenor raises ValidationError", check_invalid_tenor_fail_loud)

    # --- 8. Empty window fails loud
    def check_empty_window_fail_loud():
        try:
            calculate_fx_forwards_panel(
                engine,
                FXForwardsPanelInput(
                    market_scope="G10", tenor="1M", start_date=date(2099, 1, 1),
                ),
            )
        except ValueError as e:
            msg = str(e)
            assert "no observations found" in msg, f"expected 'no observations found': {msg[:120]}"
            return
        raise AssertionError("expected ValueError on empty window")
    check("Empty fetch raises ValueError (fail-loud)", check_empty_window_fail_loud)

    # --- 9. Typed Panel round-trips
    def check_panel_round_trip():
        out = calculate_fx_forwards_panel(
            engine,
            FXForwardsPanelInput(market_scope="G10", tenor="1M", start_date=recent_start),
        )
        validated = FXForwardsPanelOutput.model_validate(out)
        assert isinstance(validated.panel, Panel)
        n_rows, n_cols = validated.panel.payload.shape
        assert n_rows == out["n_observations"]
        assert n_cols == out["n_pairs"]
        assert len(validated.panel.lineage.steps) == 1
        assert validated.panel.lineage.steps[0].name == "calculate_fx_forwards_panel_tool"
    check("Typed Panel round-trips via FXForwardsPanelOutput.model_validate", check_panel_round_trip)

    # --- 10. units_by_column is 'price' for every column
    def check_units_all_price():
        out = calculate_fx_forwards_panel(
            engine,
            FXForwardsPanelInput(market_scope="ALL", tenor="1M", start_date=recent_start),
        )
        units = out["units_by_column"]
        assert len(units) == out["n_pairs"]
        for col, unit in units.items():
            assert unit == "price", f"{col}: expected 'price', got {unit!r}"
    check("units_by_column is 'price' for every column", check_units_all_price)

    # --- Summary
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX FORWARDS PANEL COMPUTE TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{(': ' + r.detail) if r.detail else ''}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
