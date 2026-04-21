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
cannot be derived from yield levels alone.

Algorithm
---------
1. Fetch every tenor on the curve over the lookback + z-score buffer.
2. Validate that the requested window falls at least partially inside
   the quoted curve grid — requests entirely outside the grid (both
   endpoints shorter than the shortest tenor, or both longer than the
   longest) are rejected with a clear error rather than silently
   extrapolated.
3. On each trade date, resolve the forward window for that date:
   - Tenor-based: ``start_years``/``end_years`` are constants from
     ``tenor_to_years`` — same every day.
   - Date-based: ``start_years`` / ``end_years`` are computed from
     that day's trade date using the curve's day-count basis (ACT/360
     or ACT/365), so the window shrinks as history approaches the
     start date.  Days where the start has already passed are skipped.
4. Linearly interpolate the zero rate at ``start_years`` and
   ``end_years`` from the observed par-rate grid.
5. Convert to discount factors via the dual convention in
   ``curve_bootstrap.discount_factor_from_par`` (simple compounding
   for T ≤ 1Y, annual for T > 1Y).
6. Forward rate = ``(DF_start / DF_end − 1) / (end_years − start_years)``.
7. Apply the standard 252-day rolling z-score.

Anchor discipline
-----------------
Date-based forwards are anchored to the curve's as-of date (the latest
trade_date in the fetched data) for validation, and to each trade
date's own day for historical series points.  Display-window cutoffs
also anchor to the curve's as-of date.  Never anchored to wall-clock
``date.today()``, which can drift 1-3 days off the DB on weekends or
holidays.

Extrapolation discipline
------------------------
Two layers of guard:
- Upfront: if the requested window is entirely outside the curve grid,
  the tool returns an error before running the time series.
- Per-day: if ``start_years`` extends past the longest quoted tenor
  on a given trade date, that day is skipped (the forward would be
  computed entirely from flat-extrapolated values — noise, not signal).
  ``end_years`` past the longest tenor is acceptable (flat-extrap tail
  is defensible for near-end windows).
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
    day_count_basis_for_curve,
    forward_rate_between,
    interpolate_rate,
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
# WINDOW RESOLVER — computes (start_years, end_years) per trade date
# ============================================================================

class _WindowResolver:
    """Encapsulates window-resolution logic for both input modes.

    Tenor-based inputs produce constant year fractions.  Date-based
    inputs re-anchor per trade date using the curve's day-count.
    """

    def __init__(self, params: OISForwardRateInput):
        self._params = params
        self._day_count = day_count_basis_for_curve(params.curve_family)
        if params.start_tenor and params.end_tenor:
            self._mode = "tenor"
            self._start_years = tenor_to_years(params.start_tenor)
            self._end_years = tenor_to_years(params.end_tenor)
            if self._end_years <= self._start_years:
                raise ValueError(
                    f"end_tenor ({params.end_tenor}) must map to a larger "
                    f"year fraction than start_tenor ({params.start_tenor})."
                )
            self._sd = None
            self._ed = None
        else:
            self._mode = "date"
            self._start_years = None
            self._end_years = None
            self._sd = datetime.strptime(params.start_date, "%Y-%m-%d").date()
            self._ed = datetime.strptime(params.end_date, "%Y-%m-%d").date()
            if self._ed <= self._sd:
                raise ValueError(
                    f"end_date ({params.end_date}) must be strictly after "
                    f"start_date ({params.start_date})."
                )

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def start_date_obj(self) -> Optional[date]:
        return self._sd

    def resolve(self, trade_date: date) -> Optional[tuple[float, float]]:
        """Return ``(start_years, end_years)`` for a given trade date,
        or None if the window isn't valid from that day's perspective
        (e.g. a date window whose start has already passed).
        """
        if self._mode == "tenor":
            return (self._start_years, self._end_years)
        # Date mode: anchor to this trade_date.
        start_years = (self._sd - trade_date).days / self._day_count
        end_years = (self._ed - trade_date).days / self._day_count
        if start_years < 0:
            return None  # window has already started
        if end_years <= start_years:
            return None  # window has collapsed
        return (start_years, end_years)

    def label(self) -> str:
        """Human-readable label for the output metrics."""
        curve_short = (
            self._params.curve_family.replace("_OIS", "").replace("_", " ")
        )
        if self._mode == "tenor":
            start_tenor = self._params.start_tenor
            end_tenor = self._params.end_tenor
            widened = self._end_years - self._start_years
            if (
                start_tenor.endswith("Y")
                and end_tenor.endswith("Y")
                and abs(widened - self._start_years) < 1e-9
            ):
                return f"{curve_short} {int(self._start_years)}Y{int(widened)}Y"
            return f"{curve_short} {start_tenor}/{end_tenor}"
        return f"{curve_short} {self._params.start_date} to {self._params.end_date}"


# ============================================================================
# FORWARD-RATE TIME SERIES
# ============================================================================

def _compute_forward_series(
    raw_df: pd.DataFrame,
    resolver: _WindowResolver,
) -> pd.Series:
    """For each trading day in the fetched data, resolve the forward
    window from that day's perspective, bootstrap the curve, and
    compute the forward rate.  Returns a Series indexed by date in
    percent.  Skips days where the window is invalid or requires
    bogus extrapolation.
    """
    raw_df = raw_df.copy()
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(raw_df["field_value"], errors="coerce")
    raw_df = raw_df.dropna(subset=["field_value"])
    raw_df = raw_df.drop_duplicates(
        subset=["trade_date", "tenor"], keep="last"
    )

    forwards: dict[pd.Timestamp, float] = {}

    for trade_date_ts, day_df in raw_df.groupby("trade_date"):
        # Resolve the window from THIS day's perspective.
        trade_date_py = (
            trade_date_ts.date() if hasattr(trade_date_ts, "date") else trade_date_ts
        )
        window = resolver.resolve(trade_date_py)
        if window is None:
            continue
        start_years, end_years = window

        ordered = sort_tenors_by_years(day_df["tenor"].tolist())
        if len(ordered) < 2:
            continue  # can't interpolate from a single point

        rate_by_tenor = dict(zip(day_df["tenor"], day_df["field_value"]))
        years = [tenor_to_years(t) for t in ordered]
        rates_decimal = [float(rate_by_tenor[t]) / 100.0 for t in ordered]

        # Per-day extrapolation guard — mirrors the upfront guard in
        # ``calculate_ois_forward_rate`` but applied to each historical
        # trade date, because the set of quoted tenors can shift
        # day-to-day.  Two cases where the window is entirely outside
        # the grid:
        #
        #   - both endpoints past the longest tenor
        #     (start > max_grid, which implies end > max_grid since
        #     start < end)
        #   - both endpoints before the shortest tenor
        #     (end < min_grid, which implies start < min_grid)
        #
        # In either case the forward comes entirely from flat
        # extrapolation — noise, not signal — and the day is skipped.
        # Single-sided extrapolation (e.g. end beyond max but start
        # inside grid) is acceptable; the rate shape just flattens at
        # the grid endpoint, which is defensible for near-end windows.
        if start_years > years[-1] or end_years < years[0]:
            continue

        try:
            fwd_decimal = forward_rate_between(
                years, rates_decimal, start_years, end_years,
            )
        except (ValueError, ZeroDivisionError):
            continue

        forwards[trade_date_ts] = fwd_decimal * 100.0  # back to percent

    if not forwards:
        return pd.Series(dtype=float)

    series = pd.Series(forwards).sort_index()
    # Forward-fill small holiday gaps (max 5 business days) — matches
    # the convention used by every other rates tool.
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
    # 1. Build the window resolver and date buffer
    # ------------------------------------------------------------------
    try:
        resolver = _WindowResolver(params)
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
    # 3. Validate date-window start_date vs curve as-of date
    # ------------------------------------------------------------------
    # For date-mode, the user's start_date must not precede the curve's
    # as-of date.  Without this check the math would silently produce a
    # forward for a different (later) window than the user asked for.
    as_of_ts = pd.to_datetime(raw_df["trade_date"]).max()
    as_of_date: date = as_of_ts.date() if hasattr(as_of_ts, "date") else as_of_ts

    if resolver.mode == "date" and resolver.start_date_obj is not None:
        if resolver.start_date_obj < as_of_date:
            return {
                "error": (
                    f"start_date ({resolver.start_date_obj.isoformat()}) is "
                    f"before the curve's as-of date ({as_of_date.isoformat()}).  "
                    "The forward window must begin on or after the most recent "
                    "market data date.  Please supply a start_date on or after "
                    f"{as_of_date.isoformat()}."
                )
            }

    # ------------------------------------------------------------------
    # 3b. Upfront guard: reject windows entirely outside the curve grid
    # ------------------------------------------------------------------
    # Resolve the window from the latest trade date's perspective, then
    # check against the tenor grid available on that day.  If BOTH
    # endpoints fall outside the grid (both shorter than the shortest
    # quoted tenor, or both longer than the longest), the requested
    # forward has zero legitimate signal — the whole rate would come
    # from flat-extrapolated values.  Fail fast with a clear error
    # rather than producing a number the user would misread as market
    # data.  Per-day skip logic in ``_compute_forward_series`` still
    # handles finer-grained misses on individual historical dates.
    latest_day = raw_df[
        pd.to_datetime(raw_df["trade_date"]) == as_of_ts
    ].copy()
    latest_day["field_value"] = pd.to_numeric(
        latest_day["field_value"], errors="coerce",
    )
    latest_day = latest_day.dropna(subset=["field_value"])
    latest_window = resolver.resolve(as_of_date)
    if latest_window is not None and not latest_day.empty:
        grid_tenors = sort_tenors_by_years(latest_day["tenor"].tolist())
        if len(grid_tenors) >= 2:
            grid_years = [tenor_to_years(t) for t in grid_tenors]
            grid_min, grid_max = grid_years[0], grid_years[-1]
            start_y, end_y = latest_window
            both_above = start_y > grid_max and end_y > grid_max
            both_below = start_y < grid_min and end_y < grid_min
            if both_above or both_below:
                return {
                    "error": (
                        f"Requested forward window ({start_y:.3f}y → "
                        f"{end_y:.3f}y from the as-of date) falls entirely "
                        f"outside the quoted curve grid "
                        f"({grid_min:.3f}y → {grid_max:.3f}y).  No market "
                        "data is available to interpolate from; the result "
                        "would come entirely from flat extrapolation.  "
                        "Choose a narrower or more central window."
                    )
                }

    # ------------------------------------------------------------------
    # 4. Build the forward-rate time series (per-trade-date anchoring)
    # ------------------------------------------------------------------
    forward_series = _compute_forward_series(raw_df, resolver)

    if forward_series.empty:
        return {
            "error": (
                f"Could not compute a forward-rate series for "
                f"'{params.curve_family}' over the requested window.  The "
                "curve may be missing enough tenors to interpolate, or the "
                "window may fall outside the quoted grid."
            )
        }

    # ------------------------------------------------------------------
    # 5. Rolling z-score
    # ------------------------------------------------------------------
    z_series = rolling_zscore(forward_series)

    # ------------------------------------------------------------------
    # 6. Trim to the requested display window
    # ------------------------------------------------------------------
    # Anchor the cutoff to the forward series' latest date (which is
    # the curve's as-of date), NOT date.today() — keeps the display
    # window consistent with the actual data regardless of how stale
    # the DB is vs the wall clock.
    series_as_of = forward_series.index[-1]
    cutoff = series_as_of - pd.Timedelta(days=params.lookback_days)
    display_series = forward_series.loc[forward_series.index >= cutoff]
    display_z = z_series.loc[z_series.index >= cutoff]

    if display_series.empty:
        return {
            "error": (
                f"No forward-rate observations within the last "
                f"{params.lookback_days} days for '{params.curve_family}' "
                f"{resolver.label()}."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics
    # ------------------------------------------------------------------
    current_forward = float(display_series.iloc[-1])
    # delta_bps expects inputs already in bps; the series is in percent,
    # so we multiply by 100 so cur/prev are compared on the bps scale.
    daily_change = delta_bps(
        current_forward * 100,
        display_series.iloc[-2] * 100 if len(display_series) >= 2 else None,
    )

    high_252, low_252, percentile = trailing_high_low_percentile(
        forward_series, window=Z_SCORE_WINDOW, decimals=4,
    )

    # Recover the spot legs from the latest trade date — resolving the
    # window from the same day the forward was computed on.
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
    latest_date_py: date = (
        latest_date.date() if hasattr(latest_date, "date") else latest_date
    )
    latest_window = resolver.resolve(latest_date_py)

    if not latest_day.empty and latest_window is not None:
        start_years_latest, end_years_latest = latest_window
        ordered = sort_tenors_by_years(latest_day["tenor"].tolist())
        if ordered:
            rate_by_tenor = dict(zip(latest_day["tenor"], latest_day["field_value"]))
            years_grid = [tenor_to_years(t) for t in ordered]
            rates_pct = [float(rate_by_tenor[t]) for t in ordered]
            start_spot_pct = safe_float(
                interpolate_rate(years_grid, rates_pct, start_years_latest)
            )
            end_spot_pct = safe_float(
                interpolate_rate(years_grid, rates_pct, end_years_latest)
            )

    # Round start/end years for display — avoids spurious precision
    # from the ACT/360 vs ACT/365 day-count split.
    start_years_display = round(
        latest_window[0] if latest_window is not None else 0.0, 4,
    )
    end_years_display = round(
        latest_window[1] if latest_window is not None else 0.0, 4,
    )

    metrics = OISForwardRateCurrentMetrics(
        as_of_date=latest_date_py.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        forward_label=resolver.label(),
        start_years=start_years_display,
        end_years=end_years_display,
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
    # 8. Build time_series (withheld from LLM, frontend-only)
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
