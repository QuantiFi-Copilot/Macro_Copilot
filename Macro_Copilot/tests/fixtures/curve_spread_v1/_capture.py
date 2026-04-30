"""
_capture.py — Generate / regenerate curve_spread parity fixtures
==================================================================

Run this script ONLY after a deliberate methodology change.  It rebuilds
every fixture in this directory by:

  1. Generating deterministic synthetic yield series for each test case
     (mean-reverting random walk with a fixed RNG seed).
  2. Mocking ``rates_agent.sovereign_bonds.tools.curve_spread.fetch_tenor_pair``
     to return that synthetic DataFrame.
  3. Freezing ``date.today()`` inside the curve_spread module.
  4. Running ``calculate_curve_spread`` on those inputs.
  5. Writing each fixture as a self-contained JSON file.

Usage::

    python tests/fixtures/curve_spread_v1/_capture.py

After running, REVIEW THE DIFF before committing.  Regenerated fixtures
without a methodology rationale = silent acceptance of math drift, which
is the failure mode this whole system is built to prevent.

Synthetic-data parameters are tuned to exercise every code path:
  - enough history to populate the 252-day z-score from the first
    displayed row,
  - independent ~3% holiday gaps per tenor to exercise ffill,
  - realistic-shaped yields so float rounding paths are realistic.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable
from unittest.mock import patch

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup so this script can be run directly without pytest's conftest.
# ---------------------------------------------------------------------------
_FIXTURES_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FIXTURES_DIR.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from rates_agent.sovereign_bonds.tools.curve_spread import calculate_curve_spread  # noqa: E402
from rates_agent.sovereign_bonds.tools.schemas import CurveSpreadInput  # noqa: E402


# ===========================================================================
# SYNTHETIC DATA GENERATOR
# ===========================================================================

def _generate_tenor_series(
    *,
    tenor: str,
    business_days: pd.DatetimeIndex,
    mean_pct: float,
    daily_vol_pct: float,
    initial_pct: float,
    holiday_rate: float,
    seed: int,
) -> pd.DataFrame:
    """Generate one tenor's daily yield series.

    Mean-reverting random walk:

        x_{t+1} = x_t + 0.005 * (mean - x_t) + daily_vol * randn()

    The 0.005 mean-reversion coefficient gives a ~140-day half-life, which
    keeps the series from drifting unrealistically far from ``mean_pct``
    over multi-year windows.

    A random ~``holiday_rate`` fraction of business days is dropped to
    simulate per-tenor holiday gaps (independent across tenors so the
    pivot+ffill path is exercised).

    Returns long-format rows: ``trade_date``, ``tenor``, ``field_value``.
    """
    rs = np.random.RandomState(seed)

    # Drop ~holiday_rate of business days for THIS tenor only.
    keep_mask = rs.random(len(business_days)) > holiday_rate
    dates = business_days[keep_mask]

    n = len(dates)
    values = np.empty(n, dtype=float)
    values[0] = initial_pct
    kappa = 0.005
    for i in range(1, n):
        drift = kappa * (mean_pct - values[i - 1])
        shock = rs.randn() * daily_vol_pct
        values[i] = values[i - 1] + drift + shock

    return pd.DataFrame({
        "trade_date": [d.date() for d in dates],
        "tenor": tenor,
        "field_value": values,
    })


# ===========================================================================
# CASE DEFINITIONS
# ===========================================================================
# Every case is tuned to span lookback_days + ~378-day z-score buffer +
# generous extra so the rolling z-score is fully populated from the first
# displayed row.

_CASES: list[dict] = [
    {
        "fixture_name": "ust_2s10s_365d",
        "params": {
            "curve_family": "UST",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 365,
            "field_name": "YLD_YTM_MID",
        },
        "frozen_today": "2026-04-30",
        "history_calendar_days": 5 * 365,  # 5y of history
        "tenor_specs": [
            # tenor, mean_pct, daily_vol_pct, initial_pct, holiday_rate, seed
            ("2Y",  4.30, 0.045, 4.20, 0.03, 11),
            ("10Y", 4.55, 0.040, 4.50, 0.03, 12),
        ],
    },
    {
        "fixture_name": "bund_5s30s_90d",
        "params": {
            "curve_family": "DE_BUND",
            "short_tenor": "5Y",
            "long_tenor": "30Y",
            "lookback_days": 90,
            "field_name": "YLD_YTM_MID",
        },
        "frozen_today": "2026-04-30",
        "history_calendar_days": 4 * 365,  # 4y is plenty for a 90d window
        "tenor_specs": [
            ("5Y",  2.55, 0.035, 2.40, 0.03, 21),
            ("30Y", 2.85, 0.030, 2.95, 0.03, 22),
        ],
    },
    {
        "fixture_name": "btp_2s10s_730d",
        "params": {
            "curve_family": "IT_BTP",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 730,
            "field_name": "YLD_YTM_MID",
        },
        "frozen_today": "2026-04-30",
        "history_calendar_days": 6 * 365,  # 6y to cover 730d display + buffer
        "tenor_specs": [
            ("2Y",  3.00, 0.055, 2.90, 0.03, 31),
            ("10Y", 4.10, 0.050, 4.00, 0.03, 32),
        ],
    },
]


# ===========================================================================
# CAPTURE
# ===========================================================================

class _FrozenDateForCurveSpread(date):
    """Subclass of ``date`` whose ``today()`` returns a fixed value.

    We patch ``rates_agent.sovereign_bonds.tools.curve_spread.date`` with
    this class so the two ``date.today()`` callsites inside the tool
    become deterministic.  Subtracting a ``timedelta`` from a value
    returned by ``today()`` still produces a real ``date`` because
    ``today()`` returns the underlying ``date(...)`` instance, not the
    subclass.
    """

    _frozen_value: date = date(2000, 1, 1)  # overridden per-call

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _build_raw_df(case: dict, frozen_today: date) -> pd.DataFrame:
    """Build the synthetic long-format DataFrame for a case."""
    history_start = frozen_today - timedelta(days=case["history_calendar_days"])
    business_days = pd.bdate_range(history_start, frozen_today)

    pieces: list[pd.DataFrame] = []
    for tenor, mean_pct, vol_pct, init_pct, holiday_rate, seed in case["tenor_specs"]:
        pieces.append(_generate_tenor_series(
            tenor=tenor,
            business_days=business_days,
            mean_pct=mean_pct,
            daily_vol_pct=vol_pct,
            initial_pct=init_pct,
            holiday_rate=holiday_rate,
            seed=seed,
        ))

    raw = pd.concat(pieces, ignore_index=True)
    # Sort to match the SQL ORDER BY trade_date, tenor convention used by
    # the real fetch_tenor_pair.  The tool itself doesn't depend on order
    # (it pivots), but mirroring the production order keeps fixtures
    # diff-friendly.
    raw = raw.sort_values(["trade_date", "tenor"]).reset_index(drop=True)
    return raw


def _capture_one_case(case: dict) -> dict:
    """Run the tool against synthetic data and return a fully-formed
    fixture dict ready to be written to JSON."""
    frozen_today = date.fromisoformat(case["frozen_today"])
    raw_df = _build_raw_df(case, frozen_today)

    # Patch the date import inside the tool module.  We replace ``date``
    # with a subclass whose ``today()`` returns the frozen value.
    _FrozenDateForCurveSpread._frozen_value = frozen_today

    params = CurveSpreadInput(**case["params"])

    target_module = "rates_agent.sovereign_bonds.tools.curve_spread"
    with patch(f"{target_module}.fetch_tenor_pair", return_value=raw_df), \
         patch(f"{target_module}.date", _FrozenDateForCurveSpread):
        # engine is not used because fetch_tenor_pair is mocked.
        result = calculate_curve_spread(engine=None, params=params)

    if "error" in result:
        raise RuntimeError(
            f"Capture failed for {case['fixture_name']}: tool returned "
            f"error: {result['error']}"
        )

    # Note: we intentionally do NOT record a `generated_at` timestamp.
    # The capture script is deterministic, so re-running it on the same
    # code produces bit-identical fixtures.  Recording a wall-clock time
    # would make every regeneration look like a content change in git
    # diffs even when the data is unchanged.
    fixture = {
        "fixture_name": case["fixture_name"],
        "tool_module": target_module,
        "tool_function": "calculate_curve_spread",
        "input": {
            "params": case["params"],
            "frozen_today": case["frozen_today"],
            "raw_rows": [
                {
                    "trade_date": row["trade_date"].isoformat(),
                    "tenor": row["tenor"],
                    "field_value": float(row["field_value"]),
                }
                for _, row in raw_df.iterrows()
            ],
        },
        "expected_output": result,
    }
    return fixture


def main() -> None:
    print(f"Capturing {len(_CASES)} fixtures into {_FIXTURES_DIR}")
    for case in _CASES:
        fixture = _capture_one_case(case)
        out_path = _FIXTURES_DIR / f"{case['fixture_name']}.json"
        with out_path.open("w") as f:
            json.dump(fixture, f, indent=2, sort_keys=False, default=str)
        n_rows = len(fixture["input"]["raw_rows"])
        n_ts = len(fixture["expected_output"]["time_series"])
        m = fixture["expected_output"]["current_metrics"]
        print(
            f"  ✓ {case['fixture_name']:24s}  "
            f"raw_rows={n_rows:5d}  ts_rows={n_ts:4d}  "
            f"as_of={m['as_of_date']}  spread={m['current_spread_bps']:+.2f}bps  "
            f"z={m.get('current_z_score')}"
        )
    print("Done.")


if __name__ == "__main__":
    main()
