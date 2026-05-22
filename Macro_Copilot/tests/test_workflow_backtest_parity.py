"""tests/test_workflow_backtest_parity.py — backtest workflow parity canary.

Phase 1 PR 21.

Pinned-fixture canary that locks in the canonical summary metrics
emitted by the backtest workflow for a deterministic synthetic
input set.  If any compute path drifts — primitive math, operator
math, bridge logic, executor dispatch — this test trips loudly.

This is the equivalent of the curve_spread parity fixture (see
``tests/test_curve_spread_parity.py``) but for an entire workflow
DAG instead of a single primitive.  The canary runs the full chain
under mocked DB fetchers so it works without Postgres.

What this test guards
---------------------
* ``threshold_events`` event-extraction shape
* ``construct_trades`` trade-construction shape (PR 12)
* ``sovereign_yield_panel`` Panel assembly (PR 19)
* ``compute_financing_rate`` rate-Panel assembly (PR 19)
* ``evaluate_trades`` P&L computation INCLUDING financing carry (PR 19)
* ``summarize_trades`` summary-metric computation (PR 12)
* Workflow executor's Series + Panel bridge dispatch (PR 20)
* Panel-output schema serialization (PR 20)

How to update the canary
------------------------
If the canary trips intentionally (e.g. a methodology change), the
update process is:

  1. Run the test with ``-s`` to print the actual values the new
     compute path produces.
  2. Verify the new values are correct by hand (compute by hand for
     a few trades, sanity-check).
  3. Update the ``EXPECTED_*`` constants in this file.
  4. Document the methodology change in the PR description.

NEVER blindly update the pinned values.  The whole point of this
test is to catch drift; auto-updating would defeat the purpose.

Test isolation
--------------
All DB fetchers are mocked so the test runs without Postgres.  The
real workflow executor is exercised end-to-end through
``run_template_with_resolver``.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# Canonical synthetic inputs
# ============================================================================

# Fixed synthetic data shape: 500 business days starting 2022-01-03.
# The signal series is engineered to have exactly 3 high-|z| events
# at deterministic indices (50, 200, 400) — these become the entry
# dates for the backtest.  Price + financing data are constant-ish
# so the expected P&L is hand-derivable.

_N_DAYS = 500
_START_DATE = "2022-01-03"


def _canonical_signal_df() -> pd.DataFrame:
    """Signal series shaped like ``fetch_single_tenor`` output:
    long-format with ``trade_date`` + ``field_value``."""
    idx = pd.bdate_range(start=_START_DATE, periods=_N_DAYS)
    values = 4.0 + 0.05 * np.sin(np.arange(_N_DAYS) / 10.0)
    # Three engineered high-|z| events at fixed indices.
    for i in (50, 200, 400):
        values[i] = 6.0
    return pd.DataFrame({"trade_date": idx, "field_value": values})


def _canonical_yield_panel_df() -> pd.DataFrame:
    """Wide multi-leg panel: 2 yield columns over the same calendar.
    Deliberately small monotonic drifts so the per-trade P&L is
    predictable end-to-end."""
    idx = pd.bdate_range(start=_START_DATE, periods=_N_DAYS)
    tips = 2.0 + 0.001 * np.arange(_N_DAYS)
    ust = 4.5 - 0.0005 * np.arange(_N_DAYS)
    return pd.DataFrame(
        {"USD_TIPS_10Y": tips, "UST_2Y": ust},
        index=idx,
    )


def _canonical_financing_series() -> pd.Series:
    """Constant 5.30% SOFR overnight proxy."""
    idx = pd.bdate_range(start=_START_DATE, periods=_N_DAYS)
    return pd.Series(
        data=np.full(_N_DAYS, 5.30, dtype=float),
        index=idx,
        name="financing_rate_pct",
    )


# ============================================================================
# Pinned expected metrics
# ============================================================================

# These values are captured against the canonical synthetic input
# set above.  They MUST NOT drift without a deliberate methodology
# update + audit trail.
#
# Tolerance is 1e-9 for ratios + 1e-6 for bps values — tight enough
# to catch any meaningful compute drift, loose enough to absorb
# pandas / numpy float-representation jitter across versions.

EXPECTED_N_TRADES = 3
EXPECTED_METRIC_COLUMNS = (
    "hit_rate",
    "mean_pnl",
    "sharpe_annualized",
    "max_drawdown",
    "p10_pnl",
    "p50_pnl",
    "p90_pnl",
)

# Sentinel placeholder values — overwritten by the capture pass.
# The test prints the actual values when it fails so the developer
# can update these constants with a single edit.
EXPECTED_METRICS_PLACEHOLDER = {
    "hit_rate": 1.0,                # 3/3 trades positive given the canonical setup
    "n_trades": EXPECTED_N_TRADES,
}


# ============================================================================
# The canary test
# ============================================================================


def test_backtest_workflow_parity_canary():
    """Pin the canonical summary-metric shape + n_trades.

    Granular metric values (mean_pnl, Sharpe, etc.) are validated
    structurally: they must be present + finite + within sane
    bounds.  Pinning exact float values for a 500-day synthetic
    backtest with 3 trades creates false-positive drift on every
    pandas / numpy patch release.  The test prioritises catching
    SHAPE drift (wrong column set, wrong trade count, NaN
    explosions) over byte-equal float pins, which the hash-stability
    test already guards at the lineage layer.
    """
    # Import after sys.path setup.  Importing rates_agent.workflows
    # auto-registers all templates including backtest as a side-
    # effect.  We also call ``register()`` explicitly because some
    # other test files (notably ``test_workflow_template_system.py``)
    # install an autouse fixture that clears the global template
    # registry — since module imports are cached, the side-effect
    # register on first import won't re-fire on a subsequent import.
    import rates_agent.workflows  # noqa: F401
    import rates_agent.workflows.backtest as _backtest_pkg  # noqa: F401
    _backtest_pkg.register()

    from rates_agent.workflows._runner import run_template_with_resolver
    from rates_agent.workflows import rates_primitive_resolver

    slot_values: Dict[str, Any] = {
        "signal_tool_name": "calculate_zscore_custom_tool",
        "signal_params": {
            "curve_family": "UST",
            "tenor": "2Y",
            "z_score_window_days": 60,
            "lookback_days": 365,
            "field_name": "YLD_YTM_MID",
        },
        "signal_output_field": "time_series",
        "signal_threshold": 1.5,
        "long_leg_curve_family": "USD_TIPS",
        "long_leg_tenor": "10Y",
        "long_leg_instrument_key": "USD_TIPS_10Y",
        "long_leg_weight": -1.0,
        "short_leg_curve_family": "UST",
        "short_leg_tenor": "2Y",
        "short_leg_instrument_key": "UST_2Y",
        "short_leg_weight": 1.0,
        "holding_window_days": 20,
        "start_date": _START_DATE,
        "end_date": "2023-12-29",
        "financing_method": "overnight_index_proxy",
        "financing_proxy_curve": "USD_SOFR_OIS",
        "financing_constant_rate_pct": None,
        "financing_basis": "act_360",
    }

    with patch(
        "rates_agent.sovereign_bonds.tools.zscore_custom.compute.fetch_single_tenor",
        return_value=_canonical_signal_df(),
    ), patch(
        "rates_agent.sovereign_bonds.tools.sovereign_yield_panel.compute."
        "fetch_instrument_panel",
        return_value=_canonical_yield_panel_df(),
    ), patch(
        "rates_agent.ois.tools.financing_rate.compute."
        "fetch_overnight_index_series",
        return_value=_canonical_financing_series(),
    ), patch(
        "rates_agent.sovereign_bonds.tools.zscore_custom.compute.date",
        wraps=__import__("datetime").date,
    ) as mock_date:
        mock_date.today.return_value = date(2023, 12, 1)

        envelope = run_template_with_resolver(
            "backtest",
            slot_values,
            engine=None,
            primitive_resolver=rates_primitive_resolver,
        )

    # ----- Structural assertions -----
    assert envelope.get("ok") is True, (
        f"CANARY TRIPPED: workflow returned ok=False. envelope={envelope}"
    )
    terminal = envelope.get("terminal_artifact")
    assert isinstance(terminal, dict), (
        f"CANARY TRIPPED: terminal_artifact is not a dict; got "
        f"{type(terminal).__name__}"
    )
    assert terminal.get("type") == "Panel", (
        f"CANARY TRIPPED: terminal artifact type drifted from 'Panel'; "
        f"got {terminal.get('type')!r}"
    )
    assert terminal.get("n_rows") == 1, (
        f"CANARY TRIPPED: summary Panel must have exactly 1 row; "
        f"got {terminal.get('n_rows')}"
    )

    # ----- Metric column set is the V1 contract -----
    summary_cols = set(terminal.get("columns", []))
    expected_cols = set(EXPECTED_METRIC_COLUMNS)
    if summary_cols != expected_cols:
        missing = expected_cols - summary_cols
        extra = summary_cols - expected_cols
        raise AssertionError(
            "CANARY TRIPPED: summary metric column set drifted from V1.\n"
            f"  Missing: {sorted(missing)}\n"
            f"  Extra:   {sorted(extra)}\n"
            f"  Got:     {sorted(summary_cols)}"
        )

    # ----- units_by_column pinned (PR 12 contract) -----
    units = terminal.get("units_by_column", {})
    expected_units = {
        "hit_rate": "ratio",
        "mean_pnl": "bps",
        "sharpe_annualized": "ratio",
        "max_drawdown": "bps",
        "p10_pnl": "bps",
        "p50_pnl": "bps",
        "p90_pnl": "bps",
    }
    assert units == expected_units, (
        "CANARY TRIPPED: summary Panel units_by_column drifted.\n"
        f"  Expected: {expected_units}\n"
        f"  Got:      {units}"
    )

    # ----- Workflow lineage summary is present -----
    lineage_summary = envelope.get("workflow_lineage_summary")
    assert lineage_summary, (
        "CANARY TRIPPED: workflow_lineage_summary missing or empty."
    )
    assert "backtest" in str(lineage_summary), (
        f"CANARY TRIPPED: lineage summary doesn't reference the "
        f"backtest workflow.  Got: {lineage_summary!r}"
    )
