"""Targeted tests for ``fx_agent.vol.tools.fx_vol_panel``.

Phase F1 (2026-05-27).

Mirror of ``test_fx_panel_compute.py`` for the fx_vol substrate.

Pins:
- G10 1M ATM scope returns a non-empty panel with valid pairs.
- ATM smile_point routes to instrument_type='fx_vol' substrate.
- 25R smile_point routes to instrument_type='fx_vol_smile' substrate.
- Columns are pair names (no spaces, no 'Curncy', length 6).
- Invalid market_scope/tenor/smile_point fails loud at Pydantic.
- Empty window (future start_date) raises ValueError at primitive boundary.
- Typed Panel artifact round-trips via FXVolPanelOutput.model_validate.
- units_by_column is 'percent' for every column.
- Lineage step name is 'calculate_fx_vol_panel_tool'.
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
from fx_agent.vol.tools.fx_vol_panel import (  # noqa: E402
    FXVolPanelInput,
    FXVolPanelOutput,
    calculate_fx_vol_panel,
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

    # --- 1. G10 1M ATM happy path
    def check_g10_atm():
        out = calculate_fx_vol_panel(
            engine,
            FXVolPanelInput(
                market_scope="G10", tenor="1M", smile_point="ATM",
                start_date=recent_start,
            ),
        )
        assert out["n_pairs"] >= 5, f"expected >=5 G10 ATM pairs, got {out['n_pairs']}"
        assert out["tenor"] == "1M"
        assert out["smile_point"] == "ATM"
        for col in out["columns"]:
            assert len(col) == 6, f"pair {col!r} not the expected 6-char form"
    check("G10 1M ATM returns non-empty panel with valid pairs", check_g10_atm)

    # --- 2. 25R smile_point routes successfully (smile substrate)
    def check_g10_25r():
        out = calculate_fx_vol_panel(
            engine,
            FXVolPanelInput(
                market_scope="G10", tenor="1M", smile_point="25R",
                start_date=recent_start,
            ),
        )
        assert out["n_pairs"] >= 5, f"expected >=5 G10 25R pairs, got {out['n_pairs']}"
        assert out["smile_point"] == "25R"
    check("G10 1M 25R routes to fx_vol_smile substrate", check_g10_25r)

    # --- 3. Columns are pair names (no spaces, no 'Curncy')
    def check_columns_are_pairs():
        out = calculate_fx_vol_panel(
            engine,
            FXVolPanelInput(
                market_scope="G10", tenor="1M", smile_point="ATM",
                start_date=recent_start,
            ),
        )
        for col in out["columns"]:
            assert " " not in col, f"column {col!r} contains a space (vendor-ticker leak)"
            assert "Curncy" not in col, f"column {col!r} contains 'Curncy'"
            assert len(col) == 6, f"pair {col!r} not 6-char"
    check("Columns are pair names (no spaces, no 'Curncy')", check_columns_are_pairs)

    # --- 4. Invalid market_scope fails loud at Pydantic
    def check_invalid_scope_fail_loud():
        try:
            FXVolPanelInput(market_scope="ZZ", tenor="1M", smile_point="ATM",
                            start_date=recent_start)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError on invalid market_scope")
    check("Invalid market_scope raises ValidationError", check_invalid_scope_fail_loud)

    # --- 5. Invalid tenor fails loud
    def check_invalid_tenor_fail_loud():
        try:
            FXVolPanelInput(market_scope="G10", tenor="2M", smile_point="ATM",
                            start_date=recent_start)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError on invalid tenor")
    check("Invalid tenor raises ValidationError", check_invalid_tenor_fail_loud)

    # --- 6. Invalid smile_point fails loud
    def check_invalid_smile_fail_loud():
        try:
            FXVolPanelInput(market_scope="G10", tenor="1M", smile_point="50R",
                            start_date=recent_start)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError on invalid smile_point")
    check("Invalid smile_point raises ValidationError", check_invalid_smile_fail_loud)

    # --- 7. Empty window (future start_date) fails loud
    def check_empty_window_fail_loud():
        try:
            calculate_fx_vol_panel(
                engine,
                FXVolPanelInput(
                    market_scope="G10", tenor="1M", smile_point="ATM",
                    start_date=date(2099, 1, 1),
                ),
            )
        except ValueError as e:
            msg = str(e)
            assert "no observations found" in msg, f"expected 'no observations found', got: {msg[:120]}"
            return
        raise AssertionError("expected ValueError on empty window")
    check("Empty fetch raises ValueError (fail-loud)", check_empty_window_fail_loud)

    # --- 8. Typed Panel artifact round-trips
    def check_panel_round_trip():
        out = calculate_fx_vol_panel(
            engine,
            FXVolPanelInput(
                market_scope="G10", tenor="1M", smile_point="ATM",
                start_date=recent_start,
            ),
        )
        validated = FXVolPanelOutput.model_validate(out)
        assert isinstance(validated.panel, Panel), (
            f"expected Panel, got {type(validated.panel).__name__}"
        )
        n_rows, n_cols = validated.panel.payload.shape
        assert n_rows == out["n_observations"]
        assert n_cols == out["n_pairs"]
        assert len(validated.panel.lineage.steps) == 1
        assert validated.panel.lineage.steps[0].name == "calculate_fx_vol_panel_tool"
    check("Typed Panel round-trips via FXVolPanelOutput.model_validate", check_panel_round_trip)

    # --- 9. units_by_column is 'percent' for every column
    def check_units_all_percent():
        out = calculate_fx_vol_panel(
            engine,
            FXVolPanelInput(
                market_scope="G10", tenor="1M", smile_point="ATM",
                start_date=recent_start,
            ),
        )
        units = out["units_by_column"]
        assert len(units) == out["n_pairs"], "units count != n_pairs"
        for col, unit in units.items():
            assert unit == "percent", f"{col}: expected 'percent', got {unit!r}"
    check("units_by_column is 'percent' for every column", check_units_all_percent)

    # --- Summary
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX VOL PANEL COMPUTE TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{(': ' + r.detail) if r.detail else ''}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
