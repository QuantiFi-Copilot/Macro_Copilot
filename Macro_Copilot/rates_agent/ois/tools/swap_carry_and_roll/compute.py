"""compute.py — OIS swap carry + roll-down (§7-C, Bucket-1B analytic).

Decomposes the carry + roll-down of a par OIS swap held over a horizon h,
from the OIS par curve.  RECEIVER, annualized bps, curve-unchanged
convention (all disclosed):

    roll  = s(T) − s(T−h)    the curve slide-down (reference tenor shortens)
    carry = s(T) − s(h)      coupon received minus the horizon OIS financing
    total = roll + carry

where s(·) is the interpolated par OIS rate and s(h) approximates the OIS
floating-leg financing by the h-tenor par rate.  Pure deterministic curve
interpolation — no model, no fit, no forward approximation.

Test seam: ``_fetch_curve`` is imported at module level for monkeypatching.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.swap_carry_and_roll.schemas import (
    SwapCarryAndRollCurrentMetrics,
    SwapCarryAndRollInput,
    SwapCarryAndRollOutput,
    SwapCarryAndRollTimeSeriesRow,
)
from shared.analytics.curve_bootstrap import (
    interpolate_rate,
    sort_tenors_by_years,
    tenor_to_years,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"
_BPS_PER_DECIMAL = 10000.0  # decimal rate → basis points

_FETCH_SQL = text("""
    SELECT trade_date, tenor, field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family = :curve_family
      AND field_name   = :field_name
      AND trade_date  >= :start_date
      AND tenor IS NOT NULL
    ORDER BY trade_date, tenor
""")


def _fetch_curve(engine: Engine, curve_family: str, field_name: str,
                 start_date: date) -> pd.DataFrame:
    """Fetch every (date, tenor) observation on one OIS curve."""
    with engine.connect() as conn:
        result = conn.execute(_FETCH_SQL, {
            "curve_family": curve_family, "field_name": field_name,
            "start_date": start_date.isoformat(),
        })
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), decimals)


def calculate_swap_carry_and_roll(
    engine: Engine,
    params: SwapCarryAndRollInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Decompose the carry + roll-down of an OIS swap.

    Returns ``output.model_dump()`` on success or ``{"error": "..."}`` on a
    recoverable failure — never raises.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field = config.convention_value("default_swap_rate_field")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    default_horizon = config.convention_value("default_horizon")
    round_dec = int(config.convention_value("carry_roll_round_decimals"))

    field = params.field_name or default_field
    horizon = params.horizon or default_horizon

    try:
        t_years = tenor_to_years(params.tenor)
        h_years = tenor_to_years(horizon)
    except (ValueError, KeyError) as exc:
        return {"error": f"swap_carry_and_roll: bad tenor/horizon — {exc}"}
    if not (t_years > h_years > 0):
        return {
            "error": (
                f"swap_carry_and_roll: the swap tenor ({params.tenor} = "
                f"{t_years}y) must exceed the horizon ({horizon} = {h_years}y)."
            )
        }
    tmh_years = t_years - h_years

    # ------------------------------------------------------------------
    # 1. Fetch the OIS curve.
    # ------------------------------------------------------------------
    start_date = date.today() - timedelta(days=int(params.lookback_days) + 30)
    raw = _fetch_curve(engine, params.curve_family, field, start_date)
    if raw.empty:
        return {
            "error": (
                f"No OIS data for curve_family={params.curve_family!r} "
                f"(field {field}) since {start_date.isoformat()}."
            )
        }
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    raw["field_value"] = pd.to_numeric(raw["field_value"], errors="coerce")
    raw = raw.dropna(subset=["field_value"]).drop_duplicates(
        subset=["trade_date", "tenor"], keep="last",
    )

    # ------------------------------------------------------------------
    # 2. Per-day carry/roll decomposition from the curve.
    # ------------------------------------------------------------------
    rows: Dict[pd.Timestamp, Dict[str, float]] = {}
    for ts, day in raw.groupby("trade_date"):
        ordered = sort_tenors_by_years(day["tenor"].tolist())
        if len(ordered) < 2:
            continue
        rate_by_tenor = dict(zip(day["tenor"], day["field_value"]))
        years = [tenor_to_years(t) for t in ordered]
        rates_dec = [float(rate_by_tenor[t]) / 100.0 for t in ordered]
        # Per-day guard: skip days where the swap tenor T is past the quoted
        # grid (s(T) would be pure flat extrapolation — noise).  The horizon
        # rate s(h) and the rolled rate s(T-h) interpolate within the grid
        # (the short front flat-extrapolates per the documented convention).
        if t_years > years[-1]:
            continue
        try:
            s_t = interpolate_rate(years, rates_dec, t_years)       # s(T)
            s_tmh = interpolate_rate(years, rates_dec, tmh_years)   # s(T-h)
            s_h = interpolate_rate(years, rates_dec, h_years)       # s(h)
        except (ValueError, ZeroDivisionError):
            continue
        # roll  = slide down the curve (reference tenor shortens T -> T-h)
        # carry = coupon received minus the horizon financing (s(T) - s(h))
        rows[ts] = {
            "roll": (s_t - s_tmh) * _BPS_PER_DECIMAL,
            "carry": (s_t - s_h) * _BPS_PER_DECIMAL,
            "total": ((s_t - s_tmh) + (s_t - s_h)) * _BPS_PER_DECIMAL,
            "s_t": s_t * 100.0, "s_tmh": s_tmh * 100.0, "s_h": s_h * 100.0,
        }
    if not rows:
        return {
            "error": (
                f"swap_carry_and_roll: could not decompose carry/roll for "
                f"{params.curve_family} {params.tenor} (curve may lack enough "
                "tenors to bracket the tenor / rolled tenor)."
            )
        }

    frame = pd.DataFrame(rows).T.sort_index()
    frame = frame.ffill(limit=ffill_limit)

    # Trim to the display window (anchored to the curve's as-of date).
    as_of_ts = frame.index[-1]
    cutoff = as_of_ts - pd.Timedelta(days=int(params.lookback_days))
    display = frame.loc[frame.index >= cutoff]
    if display.empty:
        return {"error": "swap_carry_and_roll: no rows within the lookback."}

    last = display.iloc[-1]
    as_of_iso = as_of_ts.strftime("%Y-%m-%d")

    current_metrics = SwapCarryAndRollCurrentMetrics(
        as_of_date=as_of_iso,
        curve_family=params.curve_family,
        tenor=params.tenor,
        horizon=horizon,
        spot_rate_pct=_round(last["s_t"], round_dec),
        rolled_spot_rate_pct=_round(last["s_tmh"], round_dec),
        financing_rate_pct=_round(last["s_h"], round_dec),
        roll_bps=_round(last["roll"], round_dec),
        carry_bps=_round(last["carry"], round_dec),
        total_carry_roll_bps=_round(last["total"], round_dec),
    )

    ts_rows: List[SwapCarryAndRollTimeSeriesRow] = []
    canonical_rows: List[TimeSeriesRow] = []
    for idx, r in display.iterrows():
        ds = idx.strftime("%Y-%m-%d")
        ts_rows.append(SwapCarryAndRollTimeSeriesRow(
            date=ds, roll_bps=_round(r["roll"], round_dec),
            carry_bps=_round(r["carry"], round_dec),
            total_bps=_round(r["total"], round_dec),
        ))
        canonical_rows.append(TimeSeriesRow(date=ds, value=_round(r["total"], round_dec)))

    time_series_total = TimeSeries(
        series_name=f"{params.curve_family}_{params.tenor}_{horizon}_carry_roll",
        units=TimeSeriesUnits.BPS,
        description=(
            f"Total carry + roll (bps) of the {params.curve_family} "
            f"{params.tenor} OIS swap over a {horizon} horizon "
            "(receiver, curve-unchanged; carry = s(T)−s(h), roll = "
            "s(T)−s(T−h))."
        ),
        rows=canonical_rows,
    )

    disclosures = [
        "RECEIVER convention (annualized bps): roll = s(T) − s(T−h) (the "
        "curve slide-down); carry = s(T) − s(h) (coupon received minus the "
        "horizon OIS financing); total = roll + carry.",
        "CURVE-UNCHANGED carry/roll over the horizon — the standard static "
        "assumption, NOT a forecast.  s(h) approximates the OIS floating-leg "
        "financing by the h-tenor par rate.",
    ]

    output = SwapCarryAndRollOutput(
        current_metrics=current_metrics,
        time_series=ts_rows,
        time_series_total_carry_roll=time_series_total,
        methodology_disclosures=disclosures,
    )
    return output.model_dump()


__all__ = ["CONFIG_PATH", "calculate_swap_carry_and_roll"]
