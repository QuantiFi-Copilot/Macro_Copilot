"""Targeted tests for ``fx_agent.forwards.tools.fx_basis_panel``.

Phase F1 (2026-05-27).

Composition primitive — assembles a Panel of CIP basis (bps) for
the V1 closed pair set {EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD}.

Pins:
- G10_BASIS_V1 1M happy path: 5-column basis panel.
- Columns are the exact V1 pair set (no extras, no missing).
- units_by_column is 'bps' for every column.
- sign_convention is 'bloomberg_bcrx_usd_scarcity_negative'.
- Invalid market_scope/tenor fails loud at Pydantic.
- Empty window (future start_date) raises ValueError at primitive boundary.
- Typed Panel artifact round-trips via FXBasisPanelOutput.model_validate.
- Lineage step name is 'calculate_fx_basis_panel_tool'.
- Sign sanity: average DM basis tends NEGATIVE (USD scarcity convention),
  verified over the full window.
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
from fx_agent.forwards.tools.fx_basis_panel import (  # noqa: E402
    FXBasisPanelInput,
    FXBasisPanelOutput,
    calculate_fx_basis_panel,
)
from shared.artifacts.types import Panel  # noqa: E402


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


_EXPECTED_V1_PAIRS = frozenset({
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
})


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

    # --- 1. G10_BASIS_V1 1M happy path: 5 columns
    def check_v1_5pairs():
        out = calculate_fx_basis_panel(
            engine,
            FXBasisPanelInput(
                market_scope="G10_BASIS_V1", tenor="1M",
                start_date=recent_start,
            ),
        )
        assert out["n_pairs"] == 5, f"expected 5 V1 pairs, got {out['n_pairs']}"
        assert set(out["columns"]) == _EXPECTED_V1_PAIRS, (
            f"V1 pair set mismatch: got {sorted(out['columns'])}"
        )
        assert out["tenor"] == "1M"
        assert out["market_scope"] == "G10_BASIS_V1"
    check("G10_BASIS_V1 1M returns exactly 5 V1 pairs", check_v1_5pairs)

    # --- 2. units_by_column is 'bps' for every column
    def check_units_all_bps():
        out = calculate_fx_basis_panel(
            engine,
            FXBasisPanelInput(market_scope="G10_BASIS_V1", tenor="1M",
                              start_date=recent_start),
        )
        units = out["units_by_column"]
        for col, unit in units.items():
            assert unit == "bps", f"{col}: expected 'bps', got {unit!r}"
    check("units_by_column is 'bps' for every column", check_units_all_bps)

    # --- 3. sign_convention echoed
    def check_sign_convention():
        out = calculate_fx_basis_panel(
            engine,
            FXBasisPanelInput(market_scope="G10_BASIS_V1", tenor="1M",
                              start_date=recent_start),
        )
        assert out["sign_convention"] == "bloomberg_bcrx_usd_scarcity_negative", (
            f"expected BCRX sign convention, got {out['sign_convention']!r}"
        )
    check("sign_convention is 'bloomberg_bcrx_usd_scarcity_negative'", check_sign_convention)

    # --- 4. Invalid tenor fails loud
    def check_invalid_tenor():
        try:
            FXBasisPanelInput(market_scope="G10_BASIS_V1", tenor="2M",
                              start_date=recent_start)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError on invalid tenor")
    check("Invalid tenor raises ValidationError", check_invalid_tenor)

    # --- 5. Invalid market_scope fails loud (V1 has only one valid scope)
    def check_invalid_scope():
        try:
            FXBasisPanelInput(market_scope="G10", tenor="1M",
                              start_date=recent_start)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError on invalid scope")
    check("Invalid market_scope raises ValidationError", check_invalid_scope)

    # --- 6. Empty window fails loud
    def check_empty_window():
        try:
            calculate_fx_basis_panel(
                engine,
                FXBasisPanelInput(
                    market_scope="G10_BASIS_V1", tenor="1M",
                    start_date=date(2099, 1, 1),
                ),
            )
        except ValueError as e:
            msg = str(e)
            assert "no basis observations" in msg or "no observations" in msg, (
                f"expected 'no basis observations' or 'no observations': {msg[:120]}"
            )
            return
        raise AssertionError("expected ValueError on empty window")
    check("Empty fetch raises ValueError (fail-loud)", check_empty_window)

    # --- 7. Typed Panel round-trips
    def check_panel_round_trip():
        out = calculate_fx_basis_panel(
            engine,
            FXBasisPanelInput(market_scope="G10_BASIS_V1", tenor="1M",
                              start_date=recent_start),
        )
        validated = FXBasisPanelOutput.model_validate(out)
        assert isinstance(validated.panel, Panel)
        n_rows, n_cols = validated.panel.payload.shape
        assert n_rows == out["n_observations"]
        assert n_cols == out["n_pairs"]
        assert len(validated.panel.lineage.steps) == 1
        assert validated.panel.lineage.steps[0].name == "calculate_fx_basis_panel_tool"
    check("Typed Panel round-trips via FXBasisPanelOutput.model_validate", check_panel_round_trip)

    # --- 8. Sign sanity: DM basis tends NEGATIVE on average
    def check_dm_basis_sign_sanity():
        out = calculate_fx_basis_panel(
            engine,
            FXBasisPanelInput(market_scope="G10_BASIS_V1", tenor="1M",
                              start_date=recent_start),
        )
        validated = FXBasisPanelOutput.model_validate(out)
        # Average across all (date, pair) cells; DM basis is typically -5 to -50 bp.
        mean_basis = float(validated.panel.payload.mean().mean())
        assert mean_basis < 5.0, (
            f"expected DM basis avg < 5bp under BCRX sign convention "
            f"(NEGATIVE = USD scarce); got mean_basis={mean_basis:.2f} bp. "
            "If positive and >+50bp consistently, suspect sign-flip regression."
        )
    check("DM basis avg < 5bp (BCRX sign sanity)", check_dm_basis_sign_sanity)

    # --- Summary
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX BASIS PANEL COMPUTE TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{(': ' + r.detail) if r.detail else ''}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
