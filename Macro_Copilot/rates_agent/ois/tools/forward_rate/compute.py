"""
compute.py — Config-driven OIS forward-rate tool
==================================================

Refactor of the legacy single-file
``rates_agent/ois/tools/forward_rate.py`` into the per-tool-folder
pattern.  Every methodology choice (z-score window/min_periods/ddof,
buffer multiplier, ffill limit, percent/bps/zscore rounding, default
field) flows from the bundled ``config.yaml`` rather than from
module-level constants or hardcoded literals.

Fourth OIS tool brought onto the per-tool-folder pattern (after
``rate_level``, ``curve_spread``, ``cross_market_spread``).  Only
``scanner`` remains.

Backward compatibility
----------------------
The default convention values in ``config.yaml`` reproduce the legacy
behaviour bit-for-bit:

    z_score_window_days        = 252      (was Z_SCORE_WINDOW)
    z_score_min_periods        = 60       (was implicit pandas default)
    z_score_ddof               = 1        (was implicit pandas default)
    z_score_buffer_multiplier  = 1.5      (was hardcoded)
    trailing_range_window_days = 252      (was Z_SCORE_WINDOW reused)
    ffill_limit_days           = 5        (was hardcoded ffill(limit=5))
    pct_round_decimals         = 4        (was hardcoded round(.., 4))
    bps_round_decimals         = 2        (was delta_bps default)
    z_score_round_decimals     = 4        (was rolling_zscore default)
    window_years_round_decimals= 4        (was hardcoded round(.., 4))
    default_swap_rate_field    = PX_LAST  (was Pydantic default)

So callers using the bundled config see byte-identical
``current_metrics`` and bespoke ``time_series`` to the legacy tool.
This migration ADDS two canonical ``TimeSeries`` fields —
``time_series_forward`` (PERCENT) and ``time_series_zscore``
(Z_SCORE) — for the upcoming primitive-to-operator bridge.  The
signature gains an optional ``config`` kwarg (auto-loaded when
None); ``field_name`` is now resolved against the YAML when the
caller passes None.

Boundary rounding — z-score precision honoured throughout
---------------------------------------------------------
``current_z_score``, every bespoke ``time_series[i].z_score``, AND
canonical ``time_series_zscore.rows[i].value`` pass
``decimals=z_round_decimals`` to ``safe_float`` so a YAML override
above 4 isn't silently truncated by ``safe_float``'s default of 4 —
day-one application of the P2 fix Codex caught on OIS curve_spread
(PR #63).  Same applies to the bps surface via
``delta_bps(decimals=bps_round_decimals)`` and to the percent
surface via ``safe_float(..., decimals=pct_round_decimals)``.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names embed the number; changing the convention
without renaming the wire fields would silently lie about what the
percentile is computed against.  The compute path raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions``.

Anchor discipline (preserved from legacy tool)
----------------------------------------------
- Date-based forwards anchor to the curve's as-of date (latest
  trade_date in fetched data) for validation, and to each trade
  date's own day for historical series points.
- Display window cutoff anchors to the forward series' latest date
  (the curve's as-of date), NOT date.today() — keeps display
  consistent across weekends / holidays when the DB lags wall-clock.

Extrapolation discipline (preserved from legacy tool)
-----------------------------------------------------
- Upfront: if the requested window is entirely outside the curve
  grid (both endpoints past the longest tenor, or both endpoints
  shorter than the shortest), reject with a clear error.
- Per-day: if the window is entirely outside that day's grid, skip
  the day (the forward would come entirely from flat extrapolation —
  noise, not signal).

Test seam
---------
``_fetch_full_curve`` and ``date`` are imported here at module
level; tests patch them via
``patch("rates_agent.ois.tools.forward_rate.compute.X")``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.forward_rate.schemas import (
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
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.
_FROZEN_TRAILING_WINDOW: int = 252


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

    def resolve(self, trade_date: date) -> Optional[Tuple[float, float]]:
        """Return ``(start_years, end_years)`` for a given trade date,
        or None if the window isn't valid from that day's perspective.
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
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the OIS forward_rate compute()
    needs from a ToolConfig.

    Raises NotImplementedError if ``trailing_range_window_days`` is
    set to anything other than 252 — see the wire-freeze rationale.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the output "
            f"field names (high_252d_pct, low_252d_pct, percentile_252d) "
            f"embed that number on the wire.  Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema rename + "
            f"frontend update documented in planned_extensions."
        )
    return {
        "z_window": config.convention_value("z_score_window_days"),
        "z_min_periods": config.convention_value("z_score_min_periods"),
        "z_ddof": config.convention_value("z_score_ddof"),
        "buffer_multiplier": config.convention_value("z_score_buffer_multiplier"),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "pct_round": config.convention_value("pct_round_decimals"),
        "bps_round": config.convention_value("bps_round_decimals"),
        "z_round": config.convention_value("z_score_round_decimals"),
        "window_years_round": config.convention_value("window_years_round_decimals"),
        "trailing_window": trailing,
    }


# ============================================================================
# FORWARD-RATE TIME SERIES
# ============================================================================

def _compute_forward_series(
    raw_df: pd.DataFrame,
    resolver: _WindowResolver,
    *,
    ffill_limit: int,
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

        # Per-day extrapolation guard — see module docstring.
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
    # Forward-fill small holiday gaps — YAML-driven (was hardcoded 5).
    series = series.ffill(limit=ffill_limit)
    return series


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_ois_forward_rate(
    engine: Engine,
    params: OISForwardRateInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the implied forward rate on an OIS curve over a window.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : OISForwardRateInput
        Validated input.  Must supply either (start_tenor, end_tenor)
        or (start_date, end_date).  ``field_name=None`` resolves
        against the YAML's ``default_swap_rate_field`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``OISForwardRateOutput``, or ``{"error": "..."}``
        on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    conv = _conventions_from_config(config)
    z_window = conv["z_window"]
    z_min_periods = conv["z_min_periods"]
    z_ddof = conv["z_ddof"]
    buffer_multiplier = conv["buffer_multiplier"]
    ffill_limit = conv["ffill_limit"]
    pct_round = conv["pct_round"]
    bps_round = conv["bps_round"]
    z_round = conv["z_round"]
    window_years_round = conv["window_years_round"]
    trailing_window = conv["trailing_window"]
    default_field_name = config.convention_value("default_swap_rate_field")

    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Build the window resolver and date buffer
    # ------------------------------------------------------------------
    try:
        resolver = _WindowResolver(params)
    except ValueError as exc:
        return {"error": str(exc)}

    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
    fetch_start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch the full OIS curve over the lookback + buffer window
    # ------------------------------------------------------------------
    raw_df = _fetch_full_curve(
        engine=engine,
        curve_family=params.curve_family,
        field_name=field_name_resolved,
        start_date=fetch_start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No OIS data found for curve_family='{params.curve_family}', "
                f"field='{field_name_resolved}' since "
                f"{fetch_start_date.isoformat()}.  "
                "Please verify the OIS curve family exists in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Validate date-window start_date vs curve as-of date
    # ------------------------------------------------------------------
    as_of_ts = pd.to_datetime(raw_df["trade_date"]).max()
    as_of_date: date = as_of_ts.date() if hasattr(as_of_ts, "date") else as_of_ts

    if resolver.mode == "date" and resolver.start_date_obj is not None:
        if resolver.start_date_obj < as_of_date:
            return {
                "error": (
                    f"start_date ({resolver.start_date_obj.isoformat()}) is "
                    f"before the curve's as-of date "
                    f"({as_of_date.isoformat()}).  The forward window must "
                    "begin on or after the most recent market data date.  "
                    "Please supply a start_date on or after "
                    f"{as_of_date.isoformat()}."
                )
            }

    # ------------------------------------------------------------------
    # 3b. Upfront guard: reject windows entirely outside the curve grid
    # ------------------------------------------------------------------
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
    forward_series = _compute_forward_series(
        raw_df, resolver, ffill_limit=ffill_limit,
    )

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
    # 5. Rolling z-score (config-driven)
    # ------------------------------------------------------------------
    z_series = rolling_zscore(
        forward_series,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round,
    )

    # ------------------------------------------------------------------
    # 6. Trim to the requested display window — anchored to the
    #    forward series' latest date (the curve's as-of date), NOT
    #    date.today().  Matches the legacy single-file tool exactly.
    # ------------------------------------------------------------------
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
    # delta_bps expects inputs already in bps; the series is in
    # percent, so multiply by 100 so curr/prev are compared on the
    # bps scale.  decimals= passes the YAML convention.
    daily_change = delta_bps(
        current_forward * 100,
        (
            display_series.iloc[-2] * 100
            if len(display_series) >= 2 else None
        ),
        decimals=bps_round,
    )

    high_252, low_252, percentile = trailing_high_low_percentile(
        forward_series, window=trailing_window, decimals=pct_round,
    )

    # Recover the spot legs from the latest trade date.
    latest_date = display_series.index[-1]
    latest_day = raw_df[
        pd.to_datetime(raw_df["trade_date"]) == latest_date
    ].copy()
    latest_day["field_value"] = pd.to_numeric(
        latest_day["field_value"], errors="coerce",
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
                interpolate_rate(years_grid, rates_pct, start_years_latest),
                decimals=pct_round,
            )
            end_spot_pct = safe_float(
                interpolate_rate(years_grid, rates_pct, end_years_latest),
                decimals=pct_round,
            )

    # Round start/end years for display — avoids spurious precision
    # from the ACT/360 vs ACT/365 day-count split.
    start_years_display = round(
        latest_window[0] if latest_window is not None else 0.0,
        window_years_round,
    )
    end_years_display = round(
        latest_window[1] if latest_window is not None else 0.0,
        window_years_round,
    )

    metrics = OISForwardRateCurrentMetrics(
        as_of_date=latest_date_py.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        forward_label=resolver.label(),
        start_years=start_years_display,
        end_years=end_years_display,
        forward_rate_pct=safe_float(current_forward, decimals=pct_round),
        daily_change_bps=daily_change,
        # Pass z_round so a YAML override above 4 isn't silently
        # truncated by safe_float's default of 4 — same boundary-
        # shadowing class as the OIS curve_spread P2 fix (PR #63).
        # Applied day one here.
        current_z_score=(
            safe_float(display_z.iloc[-1], decimals=z_round)
            if len(display_z) else None
        ),
        rolling_window_days=z_window,
        high_252d_pct=high_252,
        low_252d_pct=low_252,
        percentile_252d=percentile,
        start_spot_rate_pct=start_spot_pct,
        end_spot_rate_pct=end_spot_pct,
    )

    # ------------------------------------------------------------------
    # 8. Build time_series — bespoke wire-frozen shape (preserved for
    #    backward-compat) AND two canonical TimeSeries (PERCENT
    #    forward + Z_SCORE rolling) for the primitive-to-operator
    #    bridge.  All three built from the same display rows so they
    #    cannot drift.
    # ------------------------------------------------------------------
    ts_rows: List[OISForwardRateTimeSeriesRow] = []
    for idx in display_series.index:
        fwd = display_series.loc[idx]
        z = display_z.loc[idx] if idx in display_z.index else None
        ts_rows.append(
            OISForwardRateTimeSeriesRow(
                date=idx.strftime("%Y-%m-%d"),
                forward_rate_pct=round(float(fwd), pct_round),
                # Same boundary-rounding fix as current_z_score above.
                z_score=safe_float(z, decimals=z_round),
            )
        )
    canonical_forward = _build_canonical_forward_series(
        display_series,
        curve_family=params.curve_family,
        resolver_label=resolver.label(),
        pct_round=pct_round,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_z,
        curve_family=params.curve_family,
        resolver_label=resolver.label(),
        z_round=z_round,
    )

    output = OISForwardRateOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_forward=canonical_forward,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

def _series_name_slug(curve_family: str, label: str) -> str:
    """Return a series-name slug derived from the human-readable label.

    The label already encodes the full window context (e.g. "SOFR 1Y1Y",
    "SOFR 3M/6M", "SOFR 2026-12-01 to 2027-06-01"), so we lowercase it
    and replace spaces / slashes / hyphens with underscores to make it
    safe for downstream operator panels.
    """
    safe_label = (
        label.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )
    # Defensive: strip duplicate underscores that arise from "SOFR 1Y1Y"
    # already containing the curve.  We don't try to dedupe against
    # ``curve_family`` directly because the label uses a "short" form
    # (SOFR vs USD_SOFR_OIS); a second-pass cleanup is enough.
    while "__" in safe_label:
        safe_label = safe_label.replace("__", "_")
    return safe_label


def _build_canonical_forward_series(
    display_series: pd.Series,
    *,
    curve_family: str,
    resolver_label: str,
    pct_round: int,
) -> TimeSeries:
    """Convert the displayed forward-rate series into the canonical
    ``TimeSeries`` shape (closed-enum PERCENT units).

    Naming convention: ``<label_slug>_ois_forward``.  The
    ``_ois_forward`` suffix distinguishes from any future sovereign
    forward series and from OIS rate / spread series when they all
    end up in the same operator panel downstream.
    """
    slug = _series_name_slug(curve_family, resolver_label)
    series_name = f"{slug}_ois_forward"
    rows = [
        TimeSeriesRow(
            date=idx.strftime("%Y-%m-%d"),
            value=round(float(v), pct_round) if pd.notna(v) else None,
        )
        for idx, v in display_series.items()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Implied OIS forward rate for {resolver_label} over the "
            "displayed window."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_z: pd.Series,
    *,
    curve_family: str,
    resolver_label: str,
    z_round: int,
) -> TimeSeries:
    """Convert the displayed z-score series into the canonical
    ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention: ``<label_slug>_ois_forward_zscore``.  Values
    match ``time_series[i].z_score`` 1-to-1 — both pass through
    ``safe_float(..., decimals=z_round)``, so the canonical series
    and the bespoke shape cannot drift, AND so a YAML override above
    4 is honoured.
    """
    slug = _series_name_slug(curve_family, resolver_label)
    series_name = f"{slug}_ois_forward_zscore"
    rows = [
        TimeSeriesRow(
            date=idx.strftime("%Y-%m-%d"),
            value=safe_float(v, decimals=z_round),
        )
        for idx, v in display_z.items()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the OIS forward rate for "
            f"{resolver_label} vs its own trailing window."
        ),
        rows=rows,
    )
