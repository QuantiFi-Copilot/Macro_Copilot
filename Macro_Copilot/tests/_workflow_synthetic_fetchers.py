"""tests/_workflow_synthetic_fetchers.py — shared synthetic DB-fetcher
patches for the workflow-template tests + CLI.

Goal
----
Let the workflow CLI's ``run`` subcommand and the runner / router
unit tests execute every reachable rates-primitive call WITHOUT a
live TimescaleDB, and WITHOUT pinning a specific curve_family /
tenor binding.  The LLM router emits whatever curve_family the user
asked about (UST, DE_BUND, UK_GILT, IT_BTP, JGB, USD_SOFR_OIS,
EUR_ESTR_OIS, GBP_SONIA_OIS, ...) — the synthetic fetchers MUST
honour the caller's kwargs.

Param-aware fetchers
--------------------
Earlier revisions returned a fixed DataFrame regardless of caller
kwargs (``return_value=df``).  That broke any non-canonical binding:
the compute layer's "leg data present" check failed because the
synthetic frame's ``curve_family`` column held UST while the caller
asked for UK_GILT, and the resulting ``{"error": ...}`` envelope
then failed the downstream Pydantic *Output validation in the bridge.

This revision uses ``side_effect=callable(**kwargs)`` for every
patch.  The callable inspects the caller's kwargs (the curve_family /
tenor / short_tenor / long_tenor / curve_family_1 / curve_family_2
that compute layers pass) and returns a DataFrame labeled with those
exact values.  Any LLM binding that picks a registered primitive +
valid input shape now produces a synthetic frame the compute layer
can process end-to-end.

Coverage
--------
- ``fetch_single_tenor``        (yield_levels, ois rate_level)
- ``fetch_tenor_pair``          (sovereign curve_spread, ois curve_spread)
- ``fetch_cross_market_pair``   (sovereign cross_market_spread, ois cross_market_spread)
- ``fetch_cross_domain_pair``   (swap_spread)

Plus a ``date`` shim on every compute module so ``date.today()``
returns a frozen value, making the lookback windows deterministic.

OIS forward_rate uses a per-tool local ``_fetch_full_curve`` (NOT
the shared rates_fetch family), so it gets its own dedicated patch
with a small full-curve synthetic frame.

Public surface (backward-compatible with the prior contexts)
------------------------------------------------------------
- ``patch_q1_canonical_fetchers()``        legacy alias kept for
                                           callers that still want a
                                           "Q1-only" patch list
- ``patch_q2_canonical_fetchers()``        legacy alias for Q2-only
- ``patch_all_synthetic_fetchers()``       NEW: comprehensive patch
                                           list covering every
                                           registered rates primitive
- ``q1_canonical_fetchers_context()``      ExitStack helpers (same)
- ``q2_canonical_fetchers_context()``
- ``all_synthetic_fetchers_context()``     NEW
- ``CANONICAL_Q1_SLOT_VALUES`` / ``CANONICAL_Q2_SLOT_VALUES``
"""

from __future__ import annotations

import contextlib
from datetime import date, timedelta
from typing import Any, Iterator, List, Optional
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


# ---------------------------------------------------------------------------
# Shared synthetic-data builder (deterministic per (curve_family, tenor))
# ---------------------------------------------------------------------------
#
# Each (curve_family, tenor) pair seeds a deterministic random walk so
# repeated calls yield identical data — important because the bridge
# hashes lineage and a non-deterministic series breaks that.
# Different (curve_family, tenor) pairs get different seeds so the
# synthetic series LOOK distinct (positive sovereign-OIS spreads,
# distinguishable yields per tenor, etc.).


def _seed_for(*parts: str) -> int:
    """Stable integer seed derived from the input string parts.
    Same parts → same seed; different parts → different seed.
    """
    s = "|".join(parts)
    # Python's hash() is process-randomized; use a stable hash instead.
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return h


def _synthetic_yield_pct_series(
    *, days: int, curve_family: str, tenor: str,
) -> np.ndarray:
    """Deterministic synthetic yield-level series in PERCENT for a
    given (curve_family, tenor).  Used by the single-tenor fetchers
    (yield_levels, ois rate_level) AND as the building block for the
    pair / cross-market / cross-domain fetchers."""
    seed = _seed_for(curve_family, tenor)
    rs = np.random.RandomState(seed)
    # Base level varies by tenor (longer end is higher) and curve
    # family (sovereign typically slightly above OIS).  Just enough
    # spread between the two so the swap_spread / curve_spread
    # signals have a non-trivial scale.
    tenor_bump = {
        "1M": 0.0, "2M": 0.05, "3M": 0.10, "6M": 0.15, "9M": 0.20,
        "1Y": 0.25, "2Y": 0.40, "3Y": 0.50, "5Y": 0.65, "10Y": 0.85,
        "20Y": 0.95, "30Y": 1.00, "1W": -0.05,
    }.get(tenor, 0.30)
    base = 4.10 + tenor_bump
    # Sovereign curves trade ~10bps above OIS in this synthetic
    # space; the constant is small but non-zero.
    sovereign_premium = 0.10 if not curve_family.endswith("_OIS") else 0.0
    drift = 0.20 + 0.05 * (seed % 7)
    noise = 0.012
    vals = (
        np.linspace(base + sovereign_premium,
                    base + sovereign_premium + drift, days)
        + rs.randn(days) * noise
    )
    return vals


def _bdays_for(days: int) -> pd.DatetimeIndex:
    """Business-day index of length ``days`` ending at FROZEN_TODAY.
    Same for every fetcher so the per-curve series share a calendar."""
    return pd.bdate_range(
        FROZEN_TODAY - timedelta(days=days * 2), FROZEN_TODAY,
    )[-days:]


# ---------------------------------------------------------------------------
# Param-aware fetcher side_effects
# ---------------------------------------------------------------------------
# Each function below MATCHES the signature of the shared
# ``rates_fetch.*`` fetcher it stands in for.  ``unittest.mock.patch``
# is invoked with ``side_effect=<function>`` so the patch invokes the
# function on each call with the same kwargs the production fetcher
# would receive.  Each function reads those kwargs and returns a
# DataFrame labeled with the requested curve_family / tenor.


def _ssfx_fetch_single_tenor(
    *,
    engine: Any = None,  # ignored
    curve_family: str,
    tenor: str,
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,  # accepted (as-of cap); ignored by synthetic
) -> pd.DataFrame:
    """Synthetic ``shared.analytics.rates_fetch.fetch_single_tenor``.

    Returns columns ``['trade_date', 'field_value']``."""
    days = 600
    bdays = _bdays_for(days)
    vals = _synthetic_yield_pct_series(
        days=days, curve_family=curve_family, tenor=tenor,
    )
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": vals,
    })


def _ssfx_fetch_tenor_pair(
    *,
    engine: Any = None,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,  # accepted (as-of cap); ignored by synthetic
) -> pd.DataFrame:
    """Synthetic ``shared.analytics.rates_fetch.fetch_tenor_pair``.

    Returns columns ``['trade_date', 'tenor', 'field_value']`` for
    BOTH tenors on the same curve.  Long-tenor yield > short-tenor
    yield by ~10–60 bps so the spread series has a non-trivial range.
    """
    days = 800
    bdays = _bdays_for(days)
    rows = []
    for tenor in (short_tenor, long_tenor):
        vals = _synthetic_yield_pct_series(
            days=days, curve_family=curve_family, tenor=tenor,
        )
        for d, v in zip(bdays, vals):
            rows.append({
                "trade_date": d.date(),
                "tenor": tenor,
                "field_value": float(v),
            })
    return pd.DataFrame(rows)


def _ssfx_fetch_cross_market_pair(
    *,
    engine: Any = None,
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,  # accepted (as-of cap); ignored by synthetic
) -> pd.DataFrame:
    """Synthetic ``shared.analytics.rates_fetch.fetch_cross_market_pair``.

    Returns columns ``['trade_date', 'curve_family', 'field_value']``
    for BOTH curve_families at the same tenor."""
    days = 600
    bdays = _bdays_for(days)
    rows = []
    for cf in (curve_family_1, curve_family_2):
        vals = _synthetic_yield_pct_series(
            days=days, curve_family=cf, tenor=tenor,
        )
        for d, v in zip(bdays, vals):
            rows.append({
                "trade_date": d.date(),
                "curve_family": cf,
                "field_value": float(v),
            })
    return pd.DataFrame(rows)


def _ssfx_fetch_cross_domain_pair(
    *,
    engine: Any = None,
    sovereign_curve_family: str,
    sovereign_field_name: str,
    ois_curve_family: str,
    ois_field_name: str,
    tenor: str,
    start_date: date,
    end_date: Optional[date] = None,  # accepted (as-of cap); ignored by synthetic
) -> pd.DataFrame:
    """Synthetic ``shared.analytics.rates_fetch.fetch_cross_domain_pair``.

    Returns columns ``['trade_date', 'curve_family', 'field_value']``
    for BOTH the sovereign and OIS legs at the same tenor.  The
    sovereign series sits ~10bps above the OIS series (per the
    sovereign-premium baked into ``_synthetic_yield_pct_series``)
    so the swap_spread signal has a non-trivial scale."""
    days = 600
    bdays = _bdays_for(days)
    rows = []
    for cf in (sovereign_curve_family, ois_curve_family):
        vals = _synthetic_yield_pct_series(
            days=days, curve_family=cf, tenor=tenor,
        )
        for d, v in zip(bdays, vals):
            rows.append({
                "trade_date": d.date(),
                "curve_family": cf,
                "field_value": float(v),
            })
    return pd.DataFrame(rows)


def _ssfx_fetch_full_curve(
    *,
    engine: Any = None,
    curve_family: str,
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,  # accepted (as-of cap); ignored by synthetic
) -> pd.DataFrame:
    """Synthetic ``rates_agent.ois.tools.forward_rate.compute._fetch_full_curve``.

    Returns columns ``['trade_date', 'tenor', 'field_value']`` for a
    spread of tenors (1M..30Y) on one OIS curve."""
    days = 600
    bdays = _bdays_for(days)
    tenors = ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "10Y", "20Y", "30Y")
    rows = []
    for tenor in tenors:
        vals = _synthetic_yield_pct_series(
            days=days, curve_family=curve_family, tenor=tenor,
        )
        for d, v in zip(bdays, vals):
            rows.append({
                "trade_date": d.date(),
                "tenor": tenor,
                "field_value": float(v),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# COMPREHENSIVE patch list — every fetcher every V1 rates primitive uses
# ---------------------------------------------------------------------------
# Adding a new rates primitive = add one (or more) patches here.
#
# Each entry patches a specific compute module's import-binding of the
# fetcher function so the production code path resolves to our
# synthetic side_effect.  The ``date`` shim alongside each patch
# freezes ``date.today()`` per compute module so lookback windows are
# deterministic across runs.


_PATCH_TARGETS_FETCHERS: List[tuple] = [
    # (module_dotted_path, side_effect_callable)
    # ---- yield_levels (sovereign) ----
    (
        "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
        _ssfx_fetch_single_tenor,
    ),
    # ---- ois rate_level ----
    (
        "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
        _ssfx_fetch_single_tenor,
    ),
    # ---- sovereign curve_spread ----
    (
        "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
        _ssfx_fetch_tenor_pair,
    ),
    # ---- ois curve_spread ----
    (
        "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
        _ssfx_fetch_tenor_pair,
    ),
    # ---- sovereign cross_market_spread ----
    (
        "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.fetch_cross_market_pair",
        _ssfx_fetch_cross_market_pair,
    ),
    # ---- ois cross_market_spread ----
    (
        "rates_agent.ois.tools.cross_market_spread.compute.fetch_cross_market_pair",
        _ssfx_fetch_cross_market_pair,
    ),
    # ---- swap_spread (cross-domain) ----
    (
        "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
        _ssfx_fetch_cross_domain_pair,
    ),
    # ---- ois forward_rate (uses module-local _fetch_full_curve) ----
    (
        "rates_agent.ois.tools.forward_rate.compute._fetch_full_curve",
        _ssfx_fetch_full_curve,
    ),
]


# Each compute module that uses ``date.today()`` for lookback resolution
# also needs the ``date`` shim so the synthetic time-axis is
# deterministic across runs.
_PATCH_TARGETS_DATE: List[str] = [
    "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
    "rates_agent.ois.tools.rate_level.compute.date",
    "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
    "rates_agent.ois.tools.curve_spread.compute.date",
    "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.date",
    "rates_agent.ois.tools.cross_market_spread.compute.date",
    "rates_agent.ois.tools.swap_spread.compute.date",
    "rates_agent.ois.tools.forward_rate.compute.date",
]


def patch_all_synthetic_fetchers() -> List:
    """Return a list of ``unittest.mock.patch`` context managers that
    collectively cover every fetcher every V1 rates primitive uses.

    Use via ``contextlib.ExitStack`` or the
    ``all_synthetic_fetchers_context`` helper below.  The fetchers are
    PARAM-AWARE — they read the caller's curve_family / tenor kwargs
    and return synthetic data labeled accordingly, so any LLM-routable
    binding works.
    """
    patches: List = []
    for target, side_effect in _PATCH_TARGETS_FETCHERS:
        patches.append(_patch(target, side_effect=side_effect))
    for target in _PATCH_TARGETS_DATE:
        patches.append(_patch(target, _FrozenDate))
    return patches


@contextlib.contextmanager
def all_synthetic_fetchers_context() -> Iterator[None]:
    """ExitStack helper for ``patch_all_synthetic_fetchers``."""
    with contextlib.ExitStack() as stack:
        for p in patch_all_synthetic_fetchers():
            stack.enter_context(p)
        yield


# ===========================================================================
# Backward-compatible scoped contexts (Q1-only / Q2-only)
# ===========================================================================
# The earlier interface had two named contexts so individual unit
# tests could install only the patches they needed.  Backward-
# compatible aliases so existing callers don't break; both now
# delegate to ``patch_all_synthetic_fetchers`` because (a) the
# unified context is a strict superset and (b) the param-aware
# fetchers handle every binding the LLM might emit.


def patch_q1_canonical_fetchers() -> List:
    return patch_all_synthetic_fetchers()


def patch_q2_canonical_fetchers() -> List:
    return patch_all_synthetic_fetchers()


@contextlib.contextmanager
def q1_canonical_fetchers_context() -> Iterator[None]:
    with all_synthetic_fetchers_context():
        yield


@contextlib.contextmanager
def q2_canonical_fetchers_context() -> Iterator[None]:
    with all_synthetic_fetchers_context():
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
    "patch_all_synthetic_fetchers",
    "all_synthetic_fetchers_context",
    "patch_q1_canonical_fetchers",
    "q1_canonical_fetchers_context",
    "patch_q2_canonical_fetchers",
    "q2_canonical_fetchers_context",
    "CANONICAL_Q1_SLOT_VALUES",
    "CANONICAL_Q2_SLOT_VALUES",
]
