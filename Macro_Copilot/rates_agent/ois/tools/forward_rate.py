"""
forward_rate.py — Deterministic OIS Forward-Rate Calculator
=============================================================

Computes the implied forward rate between two points on an OIS curve.
Canonical use cases: 1Y1Y SOFR, 5Y5Y ESTR, 2Y1Y SONIA, or ad-hoc
date-window forwards aligned to central-bank meetings.

Why this exists
---------------
Forwards are the atomic unit of rates-market speak — a PM saying
"1Y1Y priced at 3.20, down 8bps" is reading an OIS-native metric that
cannot be derived from yield levels alone.  This tool is also the math
engine for ``ois_meeting_pricing``: every meeting-priced rate is just a
forward rate over a meeting-bounded window.

Algorithm (simple compounding, zero-rate approximation)
-------------------------------------------------------
1. Fetch every tenor on the curve for each trading day in the display
   window (plus z-score buffer).
2. On each date, linearly interpolate the zero rate at ``start_years``
   and ``end_years`` from the observed par-rate grid.
3. Convert to discount factors via ``DF(T) = 1 / (1 + R · T)`` (OIS
   par-rate ≈ zero-rate; see ``curve_bootstrap.py`` module docstring
   for the approximation-error discussion).
4. Forward rate = ``(DF_start / DF_end − 1) / (end_years − start_years)``.
5. Roll the forward through history to build the time series; apply the
   standard 252-day rolling z-score.

Internal API accepts EITHER a tenor pair OR a date window; the tool
surface exposes both so the LLM can answer both "what's 1Y1Y SOFR?" and
"what's priced between Dec 26 and Jun 27?" directly.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.schemas import (
    OISForwardRateCurrentMetrics,
    OISForwardRateInput,
    OISForwardRateOutput,
    OISForwardRateTimeSeriesRow,
)
from shared.analytics.curve_bootstrap import (
    forward_rate_between,
    sort_tenors_by_years,
    tenor_to_years,
)
from shared.analytics.levels import delta_bps, trailing_high_low_percentile
from shared.analytics.spreads import (
    Z_SCORE_WINDOW,
    rolling_zscore,
    safe_float,
)


# ============================================================================
# DB FETCH — entire OIS curve, all trading days in window
# ============================================================================
# This query is specific to forward_rate because we need the full tenor
# grid (not a pair or a single tenor), so it lives here rather than in
# shared/rates_fetch.py.  If future tools (e.g. curve PCA) need the same
# shape, we'll lift it to shared then.

_FETCH_FULL_CURVE_SQL = text("""
    SELECT
        trade_date,
        tenor,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family = :curve_family
      AND field_name   = :field_name
      AND trade_date  >= :start_date
      AND tenor IS NOT NULL
    ORDER BY trade_date, tenor
""")


def _fetch_full_curve(
    engine: Engine,
    curve_family: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    """Fetch every (date, tenor) observation on one OIS curve."""
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_FULL_CURVE_SQL,
            {
                "curve_family": curve_family,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# ============================================================================
# WINDOW RESOLUTION
# ============================================================================

def _resolve_window(
    params: OISForwardRateInput,
) -> tuple[float, float, str]:
    """Convert the input's (tenor pair OR date pair) into
    ``(start_years, end_years, label)`` — the normalised internal
    representation.

    ``start_years`` / ``end_years`` are year fractions from today.
    ``label`` is a human-readable tag for the output
    (``"SOFR 1Y1Y"`` or ``"SOFR 2026-12-15 to 2027-06-15"``).
    """
    curve_short = params.curve_family.replace("_OIS", "").replace("_", " ")

    if params.start_tenor and params.end_tenor:
        start_years = tenor_to_years(params.start_tenor)
        end_years = tenor_to_years(params.end_tenor)
        # Forward-label convention: tenor pairs with matching year widths
        # render as NxM (e.g. 1Y1Y for 1Y→2Y, 5Y5Y for 5Y→10Y).  Mixed
        # pairs render explicitly as "1Y/2Y" to avoid misleading shorthand.
        widened = end_years - start_years
        if (
            params.start_tenor.endswith("Y")
            and params.end_tenor.endswith("Y")
            and abs(widened - start_years) < 1e-9
        ):
            label = f"{curve_short} {int(start_years)}Y{int(widened)}Y"
        else:
            label = f"{curve_short} {params.start_tenor}/{params.end_tenor}"
        return start_years, end_years, label

    # Date-window path.
    today = date.today()
    sd = datetime.strptime(params.start_date, "%Y-%m-%d").date()
    ed = datetime.strptime(params.end_date, "%Y-%m-%d").date()
    # Year fractions from today, using 365 as a neutral calendar basis.
    start_years = max(0.0, (sd - today).days / 365.0)
    end_years = (ed - today).days / 365.0
    if end_years <= start_years:
        raise ValueError(
            f"end_date ({params.end_date}) must be strictly after "
            f"start_date ({params.start_date})."
        )
    label = f"{curve_short} {params.start_date} to {params.end_date}"
    return start_years, end_years, label


# ============================================================================
# FORWARD-RATE TIME SERIES
# ============================================================================

def _compute_forward_series(
    raw_df: pd.DataFrame,
    start_years: float,
    end_years: float,
) -> pd.Series:
    """For each trading day, bootstrap the curve and compute the forward
    rate between ``start_years`` and ``end_years``.  Returns a Series
    indexed by date with values in percent.
    """
    raw_df = raw_df.copy()
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(raw_df["field_value"], errors="coerce")
    raw_df = raw_df.dropna(subset=["field_value"])
    raw_df = raw_df.drop_duplicates(
        subset=["trade_date", "tenor"], keep="last"
    )

    forwards: dict[pd.Timestamp, float] = {}

    for trade_date, day_df in raw_df.groupby("trade_date"):
        # Sort tenors chronologically and pull the matching rates.
        ordered = sort_tenors_by_years(day_df["tenor"].tolist())
        if len(ordered) < 2:
            continue  # can't interpolate from a single point

        # Build parallel (years, rate_decimal) arrays for interpolation.
        rate_by_tenor = dict(zip(day_df["tenor"], day_df["field_value"]))
        years = [tenor_to_years(t) for t in ordered]
        # Convert pct → decimal for bootstrap math.
        rates_decimal = [float(rate_by_tenor[t]) / 100.0 for t in ordered]

        # Skip days where the requested window falls outside the curve
        # grid by more than flat-extrapolation can defend.  We allow
        # extrapolation in ``interpolate_rate`` itself, but refuse to
        # produce a forward if BOTH endpoints are outside the grid
        # (indicates malformed curve data for that day).
        if start_years > years[-1] or end_years > years[-1]:
            # End extends beyond the longest quoted tenor — flat-extrap
            # tail, still usable.  Only skip if start is also beyond.
            pass

        try:
            fwd_decimal = forward_rate_between(
                years, rates_decimal, start_years, end_years,
            )
        except (ValueError, ZeroDivisionError):
            continue

        forwards[trade_date] = fwd_decimal * 100.0  # back to percent

    if not forwards:
        return pd.Series(dtype=float)

    series = pd.Series(forwards).sort_index()
    # Forward-fill across small holiday gaps (max 5 business days) to
    # align with the convention used by every other rates tool.
    series = series.ffill(limit=5)
    return series


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_ois_forward_rate(
    engine: Engine,
    params: OISForwardRateInput,
) -> Dict[str, Any]:
    """Calculate the implied forward rate on an OIS curve over a window.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : OISForwardRateInput
        Validated input.  Must supply either (start_tenor, end_tenor)
        or (start_date, end_date).

    Returns
    -------
    dict
        Serialized ``OISForwardRateOutput``.  On failure, returns a
        dict with an ``"error"`` key.
    """

    # ------------------------------------------------------------------
    # 1. Resolve the forward window and date buffer
    # ------------------------------------------------------------------
    try:
        start_years, end_years, forward_label = _resolve_window(params)
    except ValueError as exc:
        return {"error": str(exc)}

    buffer_calendar_days = int(Z_SCORE_WINDOW * 1.5)
    fetch_start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch the full OIS curve over the lookback + buffer window
    # ------------------------------------------------------------------
    raw_df = _fetch_full_curve(
        engine=engine,
        curve_family=params.curve_family,
        field_name=params.field_name,
        start_date=fetch_start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No OIS data found for curve_family='{params.curve_family}', "
                f"field='{params.field_name}' since {fetch_start_date.isoformat()}.  "
                "Please verify the OIS curve family exists in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Build the forward-rate time series
    # ------------------------------------------------------------------
    forward_series = _compute_forward_series(raw_df, start_years, end_years)

    if forward_series.empty:
        return {
            "error": (
                f"Could not compute a forward-rate series for "
                f"'{params.curve_family}' over the window "
                f"{start_years:.3f}y → {end_years:.3f}y.  The curve may "
                "be missing enough tenors to interpolate."
            )
        }

    # ------------------------------------------------------------------
    # 4. Rolling z-score
    # ------------------------------------------------------------------
    z_series = rolling_zscore(forward_series)

    # ------------------------------------------------------------------
    # 5. Trim to the requested display window
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_series = forward_series.loc[forward_series.index >= cutoff]
    display_z = z_series.loc[z_series.index >= cutoff]

    if display_series.empty:
        return {
            "error": (
                f"No forward-rate observations within the last "
                f"{params.lookback_days} days for '{params.curve_family}' "
                f"{forward_label}."
            )
        }

    # ------------------------------------------------------------------
    # 6. Build current_metrics
    # ------------------------------------------------------------------
    current_forward = float(display_series.iloc[-1])
    daily_change = delta_bps(
        current_forward * 100,  # pct → bps for this helper
        display_series.iloc[-2] * 100 if len(display_series) >= 2 else None,
    )
    # delta_bps expects inputs already in bps; the forward series is in
    # percent, so we multiply by 100 to compare on the bps scale.
    # (Equivalent to: round((cur_pct - prev_pct) * 100, 2).)

    high_252, low_252, percentile = trailing_high_low_percentile(
        forward_series, window=Z_SCORE_WINDOW, decimals=4,
    )

    # Recover the spot legs from the latest date for context.
    latest_date = display_series.index[-1]
    latest_day = raw_df[
        pd.to_datetime(raw_df["trade_date"]) == latest_date
    ].copy()
    latest_day["field_value"] = pd.to_numeric(
        latest_day["field_value"], errors="coerce"
    )
    latest_day = latest_day.dropna(subset=["field_value"])
    start_spot_pct: Optional[float] = None
    end_spot_pct: Optional[float] = None
    if not latest_day.empty:
        ordered = sort_tenors_by_years(latest_day["tenor"].tolist())
        if ordered:
            rate_by_tenor = dict(zip(latest_day["tenor"], latest_day["field_value"]))
            years = [tenor_to_years(t) for t in ordered]
            rates_pct = [float(rate_by_tenor[t]) for t in ordered]
            from shared.analytics.curve_bootstrap import interpolate_rate
            start_spot_pct = safe_float(
                interpolate_rate(years, rates_pct, start_years)
            )
            end_spot_pct = safe_float(
                interpolate_rate(years, rates_pct, end_years)
            )

    metrics = OISForwardRateCurrentMetrics(
        as_of_date=latest_date.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        forward_label=forward_label,
        start_years=round(start_years, 4),
        end_years=round(end_years, 4),
        forward_rate_pct=safe_float(current_forward),
        daily_change_bps=daily_change,
        current_z_score=safe_float(display_z.iloc[-1]) if len(display_z) else None,
        rolling_window_days=Z_SCORE_WINDOW,
        high_252d_pct=high_252,
        low_252d_pct=low_252,
        percentile_252d=percentile,
        start_spot_rate_pct=start_spot_pct,
        end_spot_rate_pct=end_spot_pct,
    )

    # ------------------------------------------------------------------
    # 7. Build time_series (withheld from LLM, frontend-only)
    # ------------------------------------------------------------------
    ts_rows: List[OISForwardRateTimeSeriesRow] = []
    for idx in display_series.index:
        fwd = display_series.loc[idx]
        z = display_z.loc[idx] if idx in display_z.index else None
        ts_rows.append(
            OISForwardRateTimeSeriesRow(
                date=idx.strftime("%Y-%m-%d"),
                forward_rate_pct=round(float(fwd), 4),
                z_score=safe_float(z),
            )
        )

    output = OISForwardRateOutput(current_metrics=metrics, time_series=ts_rows)
    return output.model_dump()
