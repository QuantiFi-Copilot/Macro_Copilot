"""
curve_regime.py — Deterministic Curve Move Classifier
======================================================

Classifies a two-point curve move over a lookback period into one of six
deterministic regime tags (BULL_STEEPENER, BEAR_STEEPENER,
BULL_FLATTENER, BEAR_FLATTENER, PARALLEL_SHIFT, TWIST).

Thin orchestration layer over ``shared/analytics/`` primitives:

- ``fetch_tenor_group``              — DB query (2 tenors)
- ``pivot_and_align_tenors``         — pivot by tenor + ffill
- ``classify_curve_move``            — deterministic tag from bps moves
- ``safe_float``                     — None/NaN-safe numeric coercion

Domain-specific responsibilities that stay in this module: input
validation, domain-aware error messages, the ``_PERIOD_OFFSETS`` and
``_REGIME_DESCRIPTIONS`` lookup tables (narrative copy, tunable per
domain), ``"2s10s"`` label, and output-schema assembly.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict

from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.schemas import (
    CurveRegimeInput,
    CurveRegimeCurrentMetrics,
    CurveRegimeOutput,
)
from shared.analytics.rates_fetch import fetch_tenor_group
from shared.analytics.regime import classify_curve_move
from shared.analytics.spreads import (
    pivot_and_align_tenors,
    safe_float,
)


# ============================================================================
# DOMAIN-LOCAL CONFIG
# ============================================================================
# Map lookback_period labels to iloc offsets.  After ffill the series is
# approximately trading-daily; this table defines how many rows back
# each label reaches.  Kept local because sovereign tool semantics
# ("1d" / "5d" / "22d") are a product choice, not shared analytics.
_PERIOD_OFFSETS = {
    "1d": 2,    # current vs previous (iloc[-1] vs iloc[-2])
    "5d": 6,    # current vs 5 trading days ago
    "22d": 22,  # current vs ~1 month ago
}

# User-facing regime narration.  Kept local because these sentences are
# written for a PM reading a sovereign-curve move — an OIS tool would
# likely want to swap "term premium" / "supply expectations" phrasing
# for "rate-path repricing" / "terminal-rate" phrasing.
_REGIME_DESCRIPTIONS = {
    "BULL_STEEPENER": (
        "Yields fell and the curve steepened — the front end rallied "
        "more than the back end.  Typically signals dovish repricing "
        "or flight-to-quality demand concentrated in shorter maturities."
    ),
    "BEAR_STEEPENER": (
        "Yields rose and the curve steepened — the back end sold off "
        "more than the front end.  Typically signals rising term premium, "
        "inflation fears, or increased supply expectations."
    ),
    "BULL_FLATTENER": (
        "Yields fell and the curve flattened — the back end rallied "
        "more than the front end.  Typically signals a flight-to-duration "
        "bid or expectations of prolonged low rates."
    ),
    "BEAR_FLATTENER": (
        "Yields rose and the curve flattened — the front end sold off "
        "more than the back end.  Typically signals hawkish central bank "
        "repricing with rate hikes being pulled forward."
    ),
    "PARALLEL_SHIFT": (
        "Both legs moved roughly together with minimal change in curve "
        "slope.  The shape of the curve was preserved."
    ),
    "TWIST": (
        "The front end and back end moved in opposite directions — a "
        "curve twist.  This often reflects a central bank surprise or "
        "a shift in the relative supply/demand for short vs long duration."
    ),
}


# ============================================================================
# PUBLIC API
# ============================================================================

def classify_curve_regime(
    engine: Engine,
    params: CurveRegimeInput,
) -> Dict[str, Any]:
    """
    Classify the curve move over a lookback period into a deterministic
    regime tag.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : CurveRegimeInput
        Validated input with curve_family, front_tenor, back_tenor,
        lookback_period, and field_name.

    Returns
    -------
    dict
        Serialized ``CurveRegimeOutput``.  On failure, returns a dict
        with an ``"error"`` key.
    """

    # ------------------------------------------------------------------
    # 1. Resolve lookback
    # ------------------------------------------------------------------
    offset = _PERIOD_OFFSETS.get(params.lookback_period)
    if offset is None:
        return {
            "error": (
                f"Invalid lookback_period '{params.lookback_period}'.  "
                f"Valid values: {list(_PERIOD_OFFSETS.keys())}."
            )
        }

    # Fetch enough history for the offset plus some buffer.  No z-score
    # warm-up needed here (classifier is deterministic, not statistical).
    buffer_days = max(offset * 3, 60)
    start_date = date.today() - timedelta(days=buffer_days)

    # ------------------------------------------------------------------
    # 2. Fetch (2 tenors on one curve)
    # ------------------------------------------------------------------
    raw_df = fetch_tenor_group(
        engine=engine,
        curve_family=params.curve_family,
        tenors=[params.front_tenor, params.back_tenor],
        field_name=params.field_name,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family='{params.curve_family}', "
                f"tenors=['{params.front_tenor}', '{params.back_tenor}'], "
                f"field='{params.field_name}' since {start_date.isoformat()}."
            )
        }

    # ------------------------------------------------------------------
    # 3. Validate both tenors are present
    # ------------------------------------------------------------------
    available_tenors = set(raw_df["tenor"].unique())
    missing = {params.front_tenor, params.back_tenor} - available_tenors
    if missing:
        return {
            "error": (
                f"Missing tenor data for {missing} in '{params.curve_family}'.  "
                f"Available: {sorted(available_tenors)}."
            )
        }

    # ------------------------------------------------------------------
    # 4. Pivot + align holiday gaps
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(params.front_tenor, params.back_tenor),
    )

    if len(wide) < offset:
        return {
            "error": (
                f"Insufficient data for a {params.lookback_period} lookback "
                f"on '{params.curve_family}' {params.front_tenor}/{params.back_tenor}.  "
                f"Only {len(wide)} observations available, need at least {offset}."
            )
        }

    # ------------------------------------------------------------------
    # 5. Compute bps changes and classify
    # ------------------------------------------------------------------
    current_front = float(wide[params.front_tenor].iloc[-1])
    current_back = float(wide[params.back_tenor].iloc[-1])
    prior_front = float(wide[params.front_tenor].iloc[-offset])
    prior_back = float(wide[params.back_tenor].iloc[-offset])

    front_change_bps = round((current_front - prior_front) * 100, 2)
    back_change_bps = round((current_back - prior_back) * 100, 2)
    spread_change_bps = round(back_change_bps - front_change_bps, 2)
    avg_change_bps = round((front_change_bps + back_change_bps) / 2, 2)

    current_spread_bps = round((current_back - current_front) * 100, 2)
    prior_spread_bps = round((prior_back - prior_front) * 100, 2)

    regime_tag = classify_curve_move(
        front_change_bps=front_change_bps,
        back_change_bps=back_change_bps,
        spread_change_bps=spread_change_bps,
        avg_change_bps=avg_change_bps,
    )

    # ------------------------------------------------------------------
    # 6. Build output
    # ------------------------------------------------------------------
    as_of = wide.index[-1].strftime("%Y-%m-%d")
    prior_date = wide.index[-offset].strftime("%Y-%m-%d")

    spread_label = (
        f"{params.front_tenor.replace('Y', '')}s"
        f"{params.back_tenor.replace('Y', '')}s"
    )

    metrics = CurveRegimeCurrentMetrics(
        as_of_date=as_of,
        prior_date=prior_date,
        curve_family=params.curve_family,
        lookback_period=params.lookback_period,
        spread_label=spread_label,
        regime_tag=regime_tag,
        regime_description=_REGIME_DESCRIPTIONS.get(regime_tag, ""),
        front_tenor=params.front_tenor,
        back_tenor=params.back_tenor,
        front_yield_current=safe_float(current_front),
        back_yield_current=safe_float(current_back),
        front_yield_prior=safe_float(prior_front),
        back_yield_prior=safe_float(prior_back),
        front_change_bps=front_change_bps,
        back_change_bps=back_change_bps,
        spread_current_bps=current_spread_bps,
        spread_prior_bps=prior_spread_bps,
        spread_change_bps=spread_change_bps,
    )

    output = CurveRegimeOutput(current_metrics=metrics)
    return output.model_dump()
