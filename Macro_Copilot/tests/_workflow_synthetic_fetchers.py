"""tests/_workflow_synthetic_fetchers.py — shared synthetic DB-fetcher
patches for the workflow-template tests + CLI.

The two workflow-template tests
(``test_workflow_event_study.py``,
``test_workflow_regime_conditioned_relationship.py``) build their own
synthetic DataFrames inline and patch every primitive's fetcher via
``unittest.mock.patch``.  PR 9 also wants:

  - the new MCP-server / runner tests to exercise the canonical Q1
    and Q2 bindings end-to-end without a live database
  - the CLI's ``run`` subcommand to default to synthetic data so a
    developer typing prompts doesn't need a TimescaleDB up

This module centralizes the fetcher builders + patch-context helpers
so all of those callers reuse one source of truth.  Mirrors the
``shared/operators/`` "build once, reuse everywhere" discipline at
the test-fixture layer.

Public surface
--------------
- ``patch_q1_canonical_fetchers()``   — returns a list of
  ``unittest.mock.patch`` context managers covering swap_spread +
  yield_levels for the canonical Q1 binding.  Use as
  ``with contextlib.ExitStack() as stack: [stack.enter_context(p) for p in patches]``.
- ``patch_q2_canonical_fetchers()``   — same idea for the Q2 binding
  (yield_levels + ois_rate_level + ois_curve_spread).

Frozen "today"
--------------
``FROZEN_TODAY`` is the simulated DB cutoff for every fetcher.  The
patches install a ``date`` shim with ``today() == FROZEN_TODAY`` on
each primitive's compute module so the lookback windows resolve
deterministically.  Any test that uses these patches sees a
deterministic time-axis — matching the existing test files'
discipline.
"""

from __future__ import annotations

import contextlib
from datetime import date, timedelta
from typing import Iterator, List
from unittest.mock import patch as _patch

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Frozen "today" (simulated DB cutoff)
# ---------------------------------------------------------------------------
FROZEN_TODAY: date = date(2026, 4, 30)


class _FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return FROZEN_TODAY


# ===========================================================================
# Q1 — synthetic DataFrames for swap_spread + yield_levels
# ===========================================================================


def synthetic_swap_spread_df(
    *,
    days: int = 600,
    sov_init: float = 4.30,
    ois_init: float = 4.10,
    sov_drift: float = +0.20,
    ois_drift: float = -0.10,
    seed: int = 13,
) -> pd.DataFrame:
    """Long-format frame for ``fetch_cross_domain_pair`` (the swap_spread
    fetcher).  Two curve_family rows per date: UST + USD_SOFR_OIS.

    Spread (sovereign − OIS) drifts across the window so |z|>1.5
    events fire on a healthy fraction of days for the Q1 event-study
    binding."""
    bdays = pd.bdate_range(
        FROZEN_TODAY - timedelta(days=days * 2), FROZEN_TODAY,
    )[-days:]
    n = len(bdays)
    rs = np.random.RandomState(seed)
    rows = []
    for cf, base, drift in (
        ("UST", sov_init, sov_drift),
        ("USD_SOFR_OIS", ois_init, ois_drift),
    ):
        v = np.linspace(base, base + drift, n) + rs.randn(n) * 0.012
        for d, val in zip(bdays, v):
            rows.append({
                "trade_date": d.date(),
                "curve_family": cf,
                "field_value": float(val),
            })
    return pd.DataFrame(rows)


def synthetic_yield_single_tenor_df(
    *,
    days: int = 600,
    base: float = 4.30,
    drift: float = 0.40,
    noise: float = 0.012,
    seed: int = 17,
) -> pd.DataFrame:
    """Long-format frame for ``fetch_single_tenor`` — single curve,
    single tenor.  Used by yield_levels (sovereign) and rate_level
    (OIS) fetcher patches.
    """
    bdays = pd.bdate_range(
        FROZEN_TODAY - timedelta(days=days * 2), FROZEN_TODAY,
    )[-days:]
    n = len(bdays)
    rs = np.random.RandomState(seed)
    vals = np.linspace(base, base + drift, n) + rs.randn(n) * noise
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": vals,
    })


def synthetic_curve_pair_df(
    *,
    days: int = 800,
    seed: int = 13,
) -> pd.DataFrame:
    """Long-format frame for ``fetch_tenor_pair`` — two tenors on a
    single OIS curve (USD_SOFR_OIS 2Y + 10Y for the canonical Q2
    regime-classifier binding).

    Spread (long − short) oscillates between ~+150bps and ~-100bps
    so both regimes (steepening / flattening daily moves above the
    canonical thresholds) fire enough days for the rolling-β
    summaries to be well-defined."""
    bdays = pd.bdate_range(
        FROZEN_TODAY - timedelta(days=days * 2), FROZEN_TODAY,
    )[-days:]
    n = len(bdays)
    rs = np.random.RandomState(seed)
    short = np.linspace(3.50, 4.80, n) + rs.randn(n) * 0.02
    long_drift = np.sin(np.linspace(0, 6 * np.pi, n)) * 1.0
    long = short + long_drift + rs.randn(n) * 0.015
    rows = []
    for tenor, vals in (("2Y", short), ("10Y", long)):
        for d, v in zip(bdays, vals):
            rows.append({
                "trade_date": d.date(),
                "tenor": tenor,
                "field_value": float(v),
            })
    return pd.DataFrame(rows)


# ===========================================================================
# Q1 patch context (event_study canonical binding)
# ===========================================================================


def patch_q1_canonical_fetchers() -> List:
    """Return a list of ``unittest.mock.patch`` context managers that
    install the synthetic Q1 fetchers + frozen ``date`` shim on the
    swap_spread + yield_levels compute modules.

    Use via ``contextlib.ExitStack``::

        with contextlib.ExitStack() as stack:
            for p in patch_q1_canonical_fetchers():
                stack.enter_context(p)
            # ... run workflow ...

    or via the ``q1_canonical_fetchers_context`` helper below.
    """
    return [
        _patch(
            "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
            return_value=synthetic_swap_spread_df(),
        ),
        _patch(
            "rates_agent.ois.tools.swap_spread.compute.date",
            _FrozenDate,
        ),
        _patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=synthetic_yield_single_tenor_df(),
        ),
        _patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ),
    ]


@contextlib.contextmanager
def q1_canonical_fetchers_context() -> Iterator[None]:
    """Convenience wrapper: enter all Q1 patches via ExitStack,
    yield, and unwind on exit."""
    with contextlib.ExitStack() as stack:
        for p in patch_q1_canonical_fetchers():
            stack.enter_context(p)
        yield


# ===========================================================================
# Q2 patch context (regime_conditioned_relationship canonical binding)
# ===========================================================================


def patch_q2_canonical_fetchers() -> List:
    """Patches for the canonical Q2 binding (UST 10Y yield + 2Y SOFR
    OIS rate + 2s10s curve_spread regime classifier)."""
    return [
        # LHS: UST 10Y via fetch_single_tenor (sovereign yield_levels).
        _patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=synthetic_yield_single_tenor_df(
                base=4.30, drift=0.40, seed=17,
            ),
        ),
        _patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ),
        # RHS: 2Y SOFR OIS via fetch_single_tenor (ois rate_level).
        _patch(
            "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
            return_value=synthetic_yield_single_tenor_df(
                base=4.05, drift=0.30, seed=29,
            ),
        ),
        _patch(
            "rates_agent.ois.tools.rate_level.compute.date",
            _FrozenDate,
        ),
        # Regime signal: 2s10s curve spread via fetch_tenor_pair.
        _patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=synthetic_curve_pair_df(),
        ),
        _patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ),
    ]


@contextlib.contextmanager
def q2_canonical_fetchers_context() -> Iterator[None]:
    """Convenience wrapper: enter all Q2 patches via ExitStack."""
    with contextlib.ExitStack() as stack:
        for p in patch_q2_canonical_fetchers():
            stack.enter_context(p)
        yield


# ===========================================================================
# Canonical slot bindings (used by tests + CLI to drive the
# corresponding workflow with the synthetic fetchers)
# ===========================================================================


CANONICAL_Q1_SLOT_VALUES = {
    "signal_tool_name": "calculate_swap_spread_tool",
    "signal_params": {
        "sovereign_curve_family": "UST",
        "ois_curve_family": "USD_SOFR_OIS",
        "tenor": "2Y",
        "lookback_days": 1825,
    },
    "signal_output_field": "time_series_change_zscore",
    "target_tool_name": "get_yield_levels_tool",
    "target_params": {
        "curve_family": "UST",
        "tenor": "10Y",
        "lookback_days": 1825,
    },
    "target_output_field": "time_series",
    "threshold": 1.5,
    "post_window": 5,
}


CANONICAL_Q2_SLOT_VALUES = {
    "lhs_tool_name": "get_yield_levels_tool",
    "lhs_params": {
        "curve_family": "UST", "tenor": "10Y", "lookback_days": 1500,
    },
    "lhs_output_field": "time_series",
    "rhs_tool_name": "get_ois_rate_level_tool",
    "rhs_params": {
        "curve_family": "USD_SOFR_OIS", "tenor": "2Y", "lookback_days": 1500,
    },
    "rhs_output_field": "time_series",
    "regime_signal_tool_name": "calculate_ois_curve_spread_tool",
    "regime_signal_params": {
        "curve_family": "USD_SOFR_OIS",
        "short_tenor": "2Y", "long_tenor": "10Y",
        "lookback_days": 1500,
    },
    "regime_signal_output_field": "time_series_spread",
    "regression_window": 60,
    "regression_min_periods": 30,
    "high_threshold": 3.0,
    "low_threshold": -3.0,
}


__all__ = [
    "FROZEN_TODAY",
    "synthetic_swap_spread_df",
    "synthetic_yield_single_tenor_df",
    "synthetic_curve_pair_df",
    "patch_q1_canonical_fetchers",
    "q1_canonical_fetchers_context",
    "patch_q2_canonical_fetchers",
    "q2_canonical_fetchers_context",
    "CANONICAL_Q1_SLOT_VALUES",
    "CANONICAL_Q2_SLOT_VALUES",
]
