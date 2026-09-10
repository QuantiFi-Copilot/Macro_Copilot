"""Targeted tests for ``fx_agent.spot.tools.returns_series``.

Standalone runner, mirrors test_fx_carry_compute / test_fx_panel_compute
style. Pins the Phase B follow-up contract for the first single-pair
derived primitive:

  - Daily / weekly / monthly horizons each return correct row counts
    relative to the lookback window (horizon doesn't truncate the
    series — warmup rows carry value=None).
  - Snapshot's current_return == time_series.rows[-1].value STRICTLY
    (rounded to the same return_round_decimals).
  - Log-return is mathematically correct: round-trip log(P_t) -
    log(P_{t-h}) match on a known sample row.
  - observation_count == number of non-None rows in time_series.
  - Unknown pair raises ValueError (fail-loud).
  - Invalid horizon caught by Pydantic Literal (ValidationError).
  - units == RATIO.
  - series_name follows the documented convention.
"""

from __future__ import annotations

import math
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
from fx_agent.spot.tools.returns_series import (  # noqa: E402
    FXReturnsSeriesInput,
    FXReturnsSeriesOutput,
    get_fx_returns_series,
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

    # --- 1. Daily horizon returns a non-empty series, snapshot exists
    def check_daily_runs():
        out = get_fx_returns_series(
            engine,
            FXReturnsSeriesInput(pair="EURUSD", horizon="daily", lookback_days=365),
        )
        FXReturnsSeriesOutput.model_validate(out)  # schema round-trip
        assert out["current_metrics"]["horizon"] == "daily"
        assert out["current_metrics"]["observation_count"] > 200, (
            f"observation_count={out['current_metrics']['observation_count']} too low for 1y daily"
        )
        assert out["time_series"]["units"] == "ratio"
        assert out["time_series"]["series_name"] == "eurusd_log_return_daily"
    check("Daily horizon runs end-to-end on EURUSD", check_daily_runs)

    # --- 2. Weekly horizon
    def check_weekly_runs():
        out = get_fx_returns_series(
            engine,
            FXReturnsSeriesInput(pair="USDJPY", horizon="weekly", lookback_days=365),
        )
        assert out["current_metrics"]["horizon"] == "weekly"
        assert out["time_series"]["series_name"] == "usdjpy_log_return_weekly"
        # Weekly should have ~5x fewer "interesting" obs than daily over same window
        # but observation_count counts non-NaN rows — so ~262 - 5 warmup ≈ 257
        assert out["current_metrics"]["observation_count"] > 200
    check("Weekly horizon runs end-to-end on USDJPY", check_weekly_runs)

    # --- 3. Monthly horizon
    def check_monthly_runs():
        out = get_fx_returns_series(
            engine,
            FXReturnsSeriesInput(pair="USDMXN", horizon="monthly", lookback_days=730),
        )
        assert out["current_metrics"]["horizon"] == "monthly"
        assert out["time_series"]["series_name"] == "usdmxn_log_return_monthly"
        assert out["current_metrics"]["observation_count"] > 400  # ~2y of trading days minus 22 warmup
    check("Monthly horizon runs end-to-end on USDMXN", check_monthly_runs)

    # --- 4. Snapshot's current_return == last non-None row of time_series
    def check_snapshot_matches_last_row():
        out = get_fx_returns_series(
            engine,
            FXReturnsSeriesInput(pair="EURUSD", horizon="daily", lookback_days=365),
        )
        snapshot = out["current_metrics"]["current_return"]
        rows = out["time_series"]["rows"]
        last_non_none = next(
            (r for r in reversed(rows) if r["value"] is not None), None
        )
        assert last_non_none is not None, "all rows are None"
        assert snapshot == last_non_none["value"], (
            f"snapshot {snapshot} != last_row {last_non_none['value']}"
        )
    check("current_return == time_series.rows[-1].value (strict)", check_snapshot_matches_last_row)

    # --- 5. Log-return is mathematically correct on a known row
    def check_log_return_math():
        out = get_fx_returns_series(
            engine,
            FXReturnsSeriesInput(pair="EURUSD", horizon="daily", lookback_days=30),
        )
        rows = out["time_series"]["rows"]
        # Find two consecutive non-None rows
        for i in range(len(rows) - 1):
            r_curr = rows[i + 1]
            if r_curr["value"] is None:
                continue
            # The daily log-return at r_curr is log(P_curr) - log(P_prev)
            # We can verify by directly querying the underlying spot via
            # fetch_fx_spot_series and recomputing.
            from shared.analytics.fx_fetch import fetch_fx_spot_series
            from datetime import date as _date
            spot_df = fetch_fx_spot_series(
                engine, "EURUSD", _date(2020, 1, 1), _date(2026, 12, 31)
            )
            spot_by_date = {
                ts.strftime("%Y-%m-%d"): float(v)
                for ts, v in zip(spot_df["trade_date"], spot_df["field_value"])
            }
            if r_curr["date"] not in spot_by_date or rows[i]["date"] not in spot_by_date:
                continue
            expected = math.log(spot_by_date[r_curr["date"]]) - math.log(
                spot_by_date[rows[i]["date"]]
            )
            # Both values are rounded to 6 decimals
            assert abs(round(expected, 6) - r_curr["value"]) < 1e-7, (
                f"mismatch at {r_curr['date']}: expected={round(expected, 6)}, got={r_curr['value']}"
            )
            return  # one good sample is enough
        raise AssertionError("could not find two consecutive non-None rows to verify math")
    check("Log-return math is correct: log(P_t) - log(P_t-1)", check_log_return_math)

    # --- 6. observation_count == number of non-None rows
    def check_obs_count():
        out = get_fx_returns_series(
            engine,
            FXReturnsSeriesInput(pair="EURUSD", horizon="weekly", lookback_days=180),
        )
        non_none = sum(1 for r in out["time_series"]["rows"] if r["value"] is not None)
        assert out["current_metrics"]["observation_count"] == non_none, (
            f"observation_count={out['current_metrics']['observation_count']} != non_none rows={non_none}"
        )
    check("observation_count == number of non-None rows", check_obs_count)

    # --- 7. Unknown pair raises ValueError
    def check_unknown_pair_fail_loud():
        try:
            get_fx_returns_series(
                engine,
                FXReturnsSeriesInput(pair="ZZZZZZ", horizon="daily", lookback_days=365),
            )
        except ValueError as e:
            assert "no observations" in str(e)
            return
        raise AssertionError("expected ValueError on unknown pair")
    check("Unknown pair raises ValueError (fail-loud)", check_unknown_pair_fail_loud)

    # --- 8. Invalid horizon caught by Pydantic Literal
    def check_invalid_horizon():
        try:
            FXReturnsSeriesInput(pair="EURUSD", horizon="hourly", lookback_days=365)
        except ValidationError:
            return
        raise AssertionError("expected ValidationError on horizon='hourly'")
    check("Invalid horizon caught by Pydantic Literal", check_invalid_horizon)

    # --- 9. Units in TimeSeries is 'ratio'
    def check_units_ratio():
        out = get_fx_returns_series(
            engine,
            FXReturnsSeriesInput(pair="USDCAD", horizon="daily", lookback_days=180),
        )
        assert out["time_series"]["units"] == "ratio", (
            f"expected 'ratio', got {out['time_series']['units']!r}"
        )
    check("units == 'ratio'", check_units_ratio)

    # --- Summary
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    print("=" * 72)
    print(f"FX RETURNS SERIES TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{(': ' + r.detail) if r.detail else ''}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
