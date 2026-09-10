"""Pydantic input/output schemas for the FX forward-curve tool.

For one G10 pair, produces a snapshot of the full forward-curve term
structure: every tenor in ``SUPPORTED_FORWARD_TENORS``
(1W / 1M / 3M / 6M / 12M) with its forward points, outright forward,
annualised carry, and rolling 252-day z-score / percentile / range on
the spot-unit forward points.

Closed-enum tenor set
---------------------
The ``tenor`` field on each row is ``Literal["1W", "1M", "3M", "6M",
"12M"]``. The matching source-of-truth is
``fx_agent/forwards/_shared.py::SUPPORTED_FORWARD_TENORS``. Adding a
new tenor = update the Literal here AND the constant AND the
``tenor_<n>_days`` convention in this tool's config.yaml.

Z-score / percentile / range methodology
----------------------------------------
Computed on the ``forward_points_spot_units`` series (raw points
divided by the JPY-aware divisor) rather than raw forward_points,
because raw points have wildly different scales between JPY and
non-JPY pairs (100 vs 10000 divisor) and would conflate units across
tenors / pairs.  Spot-unit scale is the trader-natural one.

Carry annualisation matches the ``calculate_fx_carry`` tool exactly
— same per-tenor day count, same JPY divisor — so a forward-curve
view and a cross-sectional carry view of the same pair / tenor are
guaranteed numerically consistent.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# Closed-enum tenor set for one row of the curve. Mirrors
# ``fx_agent/forwards/_shared.py::SUPPORTED_FORWARD_TENORS`` exactly.
FXForwardCurveTenor = Literal["1W", "1M", "3M", "6M", "12M"]


class FXForwardCurveInput(BaseModel):
    """Parameters for the FX forward-curve snapshot."""

    pair: str = Field(
        ...,
        description=(
            "FX pair as stored in ``instrument_master.attributes.pair``. "
            "G10 majors currently in the substrate: EURUSD, GBPUSD, "
            "USDJPY, AUDUSD, USDCAD, USDCHF. Pairs without forward "
            "points ingested (G10 crosses, EM, NDFs) raise a "
            "``ValueError`` from the compute layer."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched from the DB for the "
            "z-score / percentile / range calculations. Does NOT "
            "control the rolling z-score window (fixed at "
            "``z_score_window_days`` in config.yaml, currently 252) "
            "nor the trailing range window (fixed at "
            "``trailing_range_window_days``, locked at 252 in V1)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for BOTH the spot and the "
            "forward points. When ``None`` (default), falls through to "
            "``default_fx_spot_field`` / ``default_fx_forward_field`` "
            "in config.yaml (both PX_LAST). Pass an explicit field "
            "name to override per query."
        ),
    )


class FXForwardCurveRow(BaseModel):
    """One tenor's snapshot on the forward curve."""

    tenor: FXForwardCurveTenor
    tenor_days: int = Field(
        ...,
        description=(
            "Trading-day count for this tenor, read from "
            "``tenor_<n>_days`` in config.yaml. Drives the carry "
            "annualisation factor (annualization_days / tenor_days)."
        ),
    )
    spot: float
    spot_date: str = Field(..., description="Latest spot trade_date (YYYY-MM-DD).")
    forward_date: str = Field(
        ...,
        description=(
            "Latest forward-points trade_date for this tenor "
            "(YYYY-MM-DD). Typically equals ``spot_date`` for liquid "
            "G10 pairs; mismatch flags a stale tenor."
        ),
    )
    forward_points: float = Field(
        ...,
        description=(
            "Raw Bloomberg forward points at this tenor. JPY pairs are "
            "quoted in hundredths of a JPY spot unit; non-JPY G10 "
            "pairs are quoted in ten-thousandths of a spot unit. Use "
            "``forward_points_spot_units`` for any computation that "
            "compares across pairs."
        ),
    )
    forward_points_spot_units: float = Field(
        ...,
        description=(
            "Forward points converted into spot units using the "
            "JPY-aware divisor. Same convention as the "
            "``calculate_fx_carry`` tool — see "
            "``fx_agent/forwards/_shared.py::points_to_spot_units``."
        ),
    )
    outright_forward: float = Field(
        ...,
        description="``spot + forward_points_spot_units``.",
    )
    carry_bps_spot: float = Field(
        ...,
        description=(
            "Carry in basis points of spot: "
            "``(forward_points_spot_units / spot) * 10000``."
        ),
    )
    carry_annualized_pct: float = Field(
        ...,
        description=(
            "Carry annualised to a trading-year basis: "
            "``(forward_points_spot_units / spot) * (annualization_days "
            "/ tenor_days) * 100``."
        ),
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of ``forward_points_spot_units`` over the "
            "trailing ``z_score_window_days`` (252). ``None`` when the "
            "trailing window has fewer than ``z_score_min_periods`` "
            "observations."
        ),
    )
    high_252d: Optional[float] = Field(
        None,
        description=(
            "Highest ``forward_points_spot_units`` over the trailing "
            "``trailing_range_window_days`` (252 in V1)."
        ),
    )
    low_252d: Optional[float] = Field(
        None,
        description=(
            "Lowest ``forward_points_spot_units`` over the trailing "
            "``trailing_range_window_days`` (252 in V1)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank (0-100) of the current "
            "``forward_points_spot_units`` within the trailing "
            "``trailing_range_window_days`` (252 in V1)."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of trading-day observations in the "
            "``lookback_days`` window after ffill-cleaning. Useful for "
            "sanity-checking that a sparse-tenor pair has enough "
            "history to support the z-score."
        ),
    )


class FXForwardCurveOutput(BaseModel):
    """Top-level response.

    ``rows`` are ordered short → long (1W, 1M, 3M, 6M, 12M) regardless
    of the natural DB row order, so a downstream chart can be rendered
    against the row index directly.
    """

    pair: str
    as_of_date: str = Field(
        ...,
        description=(
            "Latest spot trade_date observed across all tenors "
            "(YYYY-MM-DD). For a clean curve where every tenor has the "
            "same latest date, equals every row's ``spot_date``."
        ),
    )
    rows: list[FXForwardCurveRow]


__all__ = [
    "FXForwardCurveInput",
    "FXForwardCurveRow",
    "FXForwardCurveOutput",
    "FXForwardCurveTenor",
]
