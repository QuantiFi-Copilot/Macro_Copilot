"""
curve_regime.py — Deterministic Curve Move Classifier
======================================================

Classifies the curve move over a lookback period into one of six
deterministic regime tags:

    BULL_STEEPENER   — yields fell, curve steepened (front rallied more)
    BEAR_STEEPENER   — yields rose, curve steepened (back sold off more)
    BULL_FLATTENER   — yields fell, curve flattened (back rallied more)
    BEAR_FLATTENER   — yields rose, curve flattened (front sold off more)
    PARALLEL_SHIFT   — both legs moved together, spread barely changed
    TWIST            — front and back moved in opposite directions

This replaces LLM inference with deterministic classification.  In the
10-prompt gauntlet, the LLM correctly identified a "bull steepener" from
three separate tool calls — but that reasoning was probabilistic.  This
tool makes the classification guaranteed correct.

Data flow
---------
1.  **Fetch** — two-tenor query (front + back legs) against enriched view.
2.  **Pivot** — each tenor becomes a column with trade_date as index.
3.  **Fill** — forward-fill holiday gaps (max 5 days).
4.  **Math** — compute changes over the requested lookback period.
5.  **Classify** — deterministic regime tag from the changes.
6.  **Return** — ``CurveRegimeOutput`` with classification + numbers.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.tools.schemas import (
    CurveRegimeInput,
    CurveRegimeCurrentMetrics,
    CurveRegimeOutput,
)


# ============================================================================
# CONSTANTS
# ============================================================================

# If the spread changed less than this (in bps), classify as PARALLEL_SHIFT.
PARALLEL_THRESHOLD_BPS = 1.0

# If the average yield changed less than this (in bps), classify as NO_MOVE.
MOVE_THRESHOLD_BPS = 0.5

# Map lookback_period labels to iloc offsets.
# After ffill, the series is approximately daily-trading-day frequency.
_PERIOD_OFFSETS = {
    "1d": 2,    # current vs previous (iloc[-1] vs iloc[-2])
    "5d": 6,    # current vs 5 trading days ago
    "22d": 22,  # current vs ~1 month ago
}


# ============================================================================
# PRIVATE HELPERS
# ============================================================================

_FETCH_SQL = text("""
    SELECT
        trade_date,
        tenor,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family  = :curve_family
      AND tenor         IN (:front_tenor, :back_tenor)
      AND field_name    = :field_name
      AND trade_date   >= :start_date
    ORDER BY trade_date
""")


def _fetch_raw(
    engine: Engine,
    curve_family: str,
    front_tenor: str,
    back_tenor: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_SQL,
            {
                "curve_family": curve_family,
                "front_tenor": front_tenor,
                "back_tenor": back_tenor,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


def _safe_float(value: Any, decimals: int = 2) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else round(f, decimals)
    except (TypeError, ValueError):
        return None


def _classify(
    front_change_bps: float,
    back_change_bps: float,
    spread_change_bps: float,
    avg_change_bps: float,
) -> str:
    """
    Deterministic curve-move classification.

    Returns one of:
        BULL_STEEPENER, BEAR_STEEPENER,
        BULL_FLATTENER, BEAR_FLATTENER,
        PARALLEL_SHIFT, TWIST
    """
    # Edge case: nothing moved
    if (abs(front_change_bps) < MOVE_THRESHOLD_BPS
            and abs(back_change_bps) < MOVE_THRESHOLD_BPS):
        return "PARALLEL_SHIFT"

    # Twist: front and back moved in opposite directions
    if front_change_bps * back_change_bps < 0:
        return "TWIST"

    # Parallel: both legs moved together, spread barely changed
    if abs(spread_change_bps) < PARALLEL_THRESHOLD_BPS:
        return "PARALLEL_SHIFT"

    # Standard 4-quadrant classification
    is_bull = avg_change_bps < 0        # yields fell on average
    is_steepener = spread_change_bps > 0  # spread widened

    if is_bull and is_steepener:
        return "BULL_STEEPENER"
    elif not is_bull and is_steepener:
        return "BEAR_STEEPENER"
    elif is_bull and not is_steepener:
        return "BULL_FLATTENER"
    else:
        return "BEAR_FLATTENER"


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

    # Fetch enough history for the offset plus some buffer
    buffer_days = max(offset * 3, 60)
    start_date = date.today() - timedelta(days=buffer_days)

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    raw_df = _fetch_raw(
        engine=engine,
        curve_family=params.curve_family,
        front_tenor=params.front_tenor,
        back_tenor=params.back_tenor,
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
    # 4. Pivot + clean
    # ------------------------------------------------------------------
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(raw_df["field_value"], errors="coerce")
    raw_df = raw_df.drop_duplicates(subset=["trade_date", "tenor"], keep="last")

    wide = raw_df.pivot(index="trade_date", columns="tenor", values="field_value")
    wide = wide.sort_index()
    wide = wide.ffill(limit=5)
    wide = wide.dropna(subset=[params.front_tenor, params.back_tenor])

    if len(wide) < offset:
        return {
            "error": (
                f"Insufficient data for a {params.lookback_period} lookback "
                f"on '{params.curve_family}' {params.front_tenor}/{params.back_tenor}.  "
                f"Only {len(wide)} observations available, need at least {offset}."
            )
        }

    # ------------------------------------------------------------------
    # 5. Compute changes and classify
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

    regime_tag = _classify(
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
        front_yield_current=_safe_float(current_front, 4),
        back_yield_current=_safe_float(current_back, 4),
        front_yield_prior=_safe_float(prior_front, 4),
        back_yield_prior=_safe_float(prior_back, 4),
        front_change_bps=front_change_bps,
        back_change_bps=back_change_bps,
        spread_current_bps=current_spread_bps,
        spread_prior_bps=prior_spread_bps,
        spread_change_bps=spread_change_bps,
    )

    output = CurveRegimeOutput(current_metrics=metrics)
    return output.model_dump()
