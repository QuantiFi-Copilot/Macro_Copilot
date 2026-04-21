"""Pydantic schemas for the OIS forward-rate tool."""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class OISForwardRateInput(BaseModel):
    """Parameters for computing an implied forward rate on an OIS curve.

    Two equivalent input modes are supported:

    1. **Tenor-based** (common case) — supply ``start_tenor`` and
       ``end_tenor`` (e.g. 1Y/2Y for "1Y1Y", or 5Y/10Y for "5Y5Y").
       The tool converts these to year fractions from the curve's
       as-of date and computes the forward in that window.

    2. **Date-based** — supply ``start_date`` and ``end_date`` in
       ISO-8601 form.  Used for ad-hoc custom windows like "forward
       between Dec 2026 and Jun 2027".  Year fractions are anchored to
       the curve's as-of date (the latest trade date in the data), not
       wall-clock today — this keeps the math consistent across
       weekends and holidays when the DB may lag the calendar.

    Exactly ONE of the two modes must be supplied; the validator
    enforces this.
    """

    curve_family: str = Field(
        ...,
        description=(
            "OIS curve family.  Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS', "
            "'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', 'CAD_OIS'."
        ),
    )

    # ------------------------------------------------------------------
    # MODE 1: tenor pair (start_tenor, end_tenor)
    # ------------------------------------------------------------------
    start_tenor: Optional[str] = Field(
        default=None,
        description=(
            "Start tenor of the forward window.  For '1Y1Y' use '1Y'; "
            "for '5Y5Y' use '5Y'; for '2Y1Y' use '2Y'.  Must be present "
            "on the curve.  Mutually exclusive with start_date."
        ),
    )
    end_tenor: Optional[str] = Field(
        default=None,
        description=(
            "End tenor of the forward window.  For '1Y1Y' use '2Y' "
            "(start=1Y + forward=1Y); for '5Y5Y' use '10Y'; for '2Y1Y' "
            "use '3Y'.  Must be present on the curve.  Mutually "
            "exclusive with end_date."
        ),
    )

    # ------------------------------------------------------------------
    # MODE 2: date window (start_date, end_date)
    # ------------------------------------------------------------------
    start_date: Optional[str] = Field(
        default=None,
        description=(
            "Start date of the forward window in ISO-8601 format "
            "(YYYY-MM-DD).  Mutually exclusive with start_tenor.  Must "
            "be on or after the curve's as-of date (the latest trade "
            "date in the market data).  Past dates are rejected with a "
            "clear error rather than silently clamped — the tool will "
            "name the required minimum date in the error message."
        ),
    )
    end_date: Optional[str] = Field(
        default=None,
        description=(
            "End date of the forward window in ISO-8601 format.  "
            "Must be strictly after start_date.  Mutually exclusive "
            "with end_tenor."
        ),
    )

    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "Calendar days of displayed history in the time_series "
            "output.  The rolling z-score window is always a fixed 252 "
            "trading days regardless of this value."
        ),
    )
    field_name: str = Field(
        default="PX_LAST",
        description=(
            "Observation field to use.  Defaults to 'PX_LAST' — the mid "
            "par swap rate.  Other valid: 'PX_BID', 'PX_ASK'."
        ),
    )

    @model_validator(mode="after")
    def _validate_window(self) -> "OISForwardRateInput":
        has_tenors = bool(self.start_tenor) and bool(self.end_tenor)
        has_dates = bool(self.start_date) and bool(self.end_date)
        partial_tenors = bool(self.start_tenor) ^ bool(self.end_tenor)
        partial_dates = bool(self.start_date) ^ bool(self.end_date)

        if partial_tenors:
            raise ValueError(
                "Both start_tenor and end_tenor must be supplied together."
            )
        if partial_dates:
            raise ValueError(
                "Both start_date and end_date must be supplied together."
            )
        if has_tenors and has_dates:
            raise ValueError(
                "Supply EITHER (start_tenor, end_tenor) OR "
                "(start_date, end_date), not both."
            )
        if not has_tenors and not has_dates:
            raise ValueError(
                "Supply (start_tenor, end_tenor) for tenor-based forwards "
                "like 1Y1Y, or (start_date, end_date) for a custom window."
            )
        return self


class OISForwardRateCurrentMetrics(BaseModel):
    """Snapshot metrics for the most recent trade date."""
    as_of_date: str
    curve_family: str
    forward_label: str = Field(..., description="Human-readable label, e.g. 'SOFR 1Y1Y'.")
    start_years: float = Field(..., description="Start of the forward window in years from the curve's as-of date.")
    end_years: float = Field(..., description="End of the forward window in years from the curve's as-of date.")
    forward_rate_pct: Optional[float] = Field(None, description="Implied forward rate (percent).")
    daily_change_bps: Optional[float] = Field(None, description="1-day change in the forward rate (bps).")
    current_z_score: Optional[float] = Field(None, description="Rolling 252-day z-score of the forward rate.")
    rolling_window_days: int = Field(..., description="Window used for the z-score calculation.")
    high_252d_pct: Optional[float] = Field(None, description="Trailing 252-day high (percent).")
    low_252d_pct: Optional[float] = Field(None, description="Trailing 252-day low (percent).")
    percentile_252d: Optional[float] = Field(None, description="Where the current forward sits in the 252d range (0-100).")
    start_spot_rate_pct: Optional[float] = Field(None, description="Par OIS rate at start_years (interpolated).")
    end_spot_rate_pct: Optional[float] = Field(None, description="Par OIS rate at end_years (interpolated).")


class OISForwardRateTimeSeriesRow(BaseModel):
    """Single row in the forward-rate time-series array."""
    date: str
    forward_rate_pct: float
    z_score: Optional[float] = None


class OISForwardRateOutput(BaseModel):
    """Top-level response the MCP server returns."""
    current_metrics: OISForwardRateCurrentMetrics
    time_series: List[OISForwardRateTimeSeriesRow]
