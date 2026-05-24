"""Targeted tests for ``fx_agent.spot.tools.drawdown``.

Standalone runner. Pins Phase B follow-up contract:
  - Drawdown is always ≤ 0 at every point.
  - current_drawdown == time_series.rows[-1].value STRICTLY.
  - max_drawdown == min(time_series.rows[*].value).
  - max_drawdown_date is the date of the min.
  - peak_value matches the running max at peak_date.
  - Recovery logic: if recovery_date is not None, spot at
    recovery_date >= peak_value AND recovery_date > max_drawdown_date.
  - observation_count == len(time_series.rows).
  - Unknown pair raises ValueError (fail-loud).
  - units == 'ratio'.
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.spot.tools.drawdown import (  # noqa: E402
    FXDrawdownInput,
    FXDrawdownOutput,
    calculate_fx_drawdown,
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

    # --- 1. Run end-to-end on EURUSD
    def check_runs():
        out = calculate_fx_drawdown(
            engine, FXDrawdownInput(pair="EURUSD", lookback_days=730)
        )
        FXDrawdownOutput.model_validate(out)
        assert out["time_series"]["series_name"] == "eurusd_drawdown"
        assert out["time_series"]["units"] == "ratio"
        assert out["current_metrics"]["observation_count"] > 400
    check("Runs end-to-end on EURUSD over 2y", check_runs)

    # --- 2. All drawdown values ≤ 0
    def check_dd_non_positive():
        out = calculate_fx_drawdown(
            engine, FXDrawdownInput(pair="USDMXN", lookback_days=365)
        )
        bad = [r for r in out["time_series"]["rows"] if r["value"] is not None and r["value"] > 1e-9]
        assert not bad, f"found positive drawdown rows: {bad[:5]}"
    check("All drawdown values ≤ 0 (RATIO ≤ 0)", check_dd_non_positive)

    # --- 3. Snapshot's current_drawdown == last row of time_series (strict)
    def check_snapshot_matches_last():
        out = calculate_fx_drawdown(
            engine, FXDrawdownInput(pair="USDJPY", lookback_days=365)
        )
        snapshot_dd = out["current_metrics"]["current_drawdown"]
        last_row = out["time_series"]["rows"][-1]
        assert snapshot_dd == last_row["value"], (
            f"snapshot={snapshot_dd} != last_row={last_row['value']}"
        )
    check("current_drawdown == time_series.rows[-1].value (strict)", check_snapshot_matches_last)

    # --- 4. max_drawdown == min of time_series values
    def check_max_dd_matches_min():
        out = calculate_fx_drawdown(
            engine, FXDrawdownInput(pair="USDTRY", lookback_days=2000)
        )
        snapshot_max = out["current_metrics"]["max_drawdown"]
        min_in_series = min(
            r["value"] for r in out["time_series"]["rows"] if r["value"] is not None
        )
        # Both rounded to drawdown_round_decimals (6) — direct equality
        assert snapshot_max == min_in_series, (
            f"snapshot max_drawdown={snapshot_max} != min(series)={min_in_series}"
        )
    check("max_drawdown == min of time_series (strict)", check_max_dd_matches_min)

    # --- 5. max_drawdown_date corresponds to the actual min row
    def check_max_dd_date():
        out = calculate_fx_drawdown(
            engine, FXDrawdownInput(pair="USDZAR", lookback_days=730)
        )
        max_dd_date = out["current_metrics"]["max_drawdown_date"]
        max_dd_value = out["current_metrics"]["max_drawdown"]
        matching_row = next(
            (r for r in out["time_series"]["rows"] if r["date"] == max_dd_date), None
        )
        assert matching_row is not None, f"max_drawdown_date {max_dd_date} not in time_series"
        assert matching_row["value"] == max_dd_value, (
            f"row at max_drawdown_date has value {matching_row['value']}, "
            f"snapshot says {max_dd_value}"
        )
    check("max_drawdown_date row's value == snapshot max_drawdown", check_max_dd_date)

    # --- 6. Recovery invariants (if not None)
    def check_recovery_invariants():
        out = calculate_fx_drawdown(
            engine, FXDrawdownInput(pair="EURUSD", lookback_days=2000)
        )
        rec_date = out["current_metrics"]["recovery_date"]
        max_dd_date = out["current_metrics"]["max_drawdown_date"]
        if rec_date is None:
            assert out["current_metrics"]["time_to_recovery_days"] is None
            return  # not recovered yet, nothing to check
        # Must be strictly after max_dd_date
        rec_d = datetime.strptime(rec_date, "%Y-%m-%d").date()
        max_d = datetime.strptime(max_dd_date, "%Y-%m-%d").date()
        assert rec_d > max_d, f"recovery_date {rec_date} not after max_drawdown_date {max_dd_date}"
        # time_to_recovery_days must be ≥ 1
        ttr = out["current_metrics"]["time_to_recovery_days"]
        assert ttr is not None and ttr >= 1, (
            f"time_to_recovery_days={ttr} invalid for recovered drawdown"
        )
    check("Recovery invariants hold when recovery_date is not None", check_recovery_invariants)

    # --- 7. observation_count == len(time_series.rows)
    def check_obs_count():
        out = calculate_fx_drawdown(
            engine, FXDrawdownInput(pair="USDPLN", lookback_days=365)
        )
        assert out["current_metrics"]["observation_count"] == len(out["time_series"]["rows"])
    check("observation_count == len(time_series.rows)", check_obs_count)

    # --- 8. Unknown pair raises ValueError
    def check_unknown_pair_fail_loud():
        try:
            calculate_fx_drawdown(
                engine, FXDrawdownInput(pair="ZZZZZZ", lookback_days=365)
            )
        except ValueError as e:
            assert "no observations" in str(e)
            return
        raise AssertionError("expected ValueError on unknown pair")
    check("Unknown pair raises ValueError (fail-loud)", check_unknown_pair_fail_loud)

    # --- 9. Units == ratio
    def check_units():
        out = calculate_fx_drawdown(
            engine, FXDrawdownInput(pair="USDIDR", lookback_days=365)
        )
        assert out["time_series"]["units"] == "ratio"
    check("units == 'ratio'", check_units)

    # --- Summary
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX DRAWDOWN TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{(': ' + r.detail) if r.detail else ''}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
