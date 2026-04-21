"""
meeting_pricing.py — OIS-Implied Central-Bank Meeting Pricing
==============================================================

Translates an OIS curve into a meeting-by-meeting view of priced policy
rates, cumulative move from today, per-meeting implied jumps, and
implied probability of a 25bp move.

This is the flagship OIS tool — what a macro PM reads every morning.
"How many cuts are priced for the June FOMC?" / "Where's the terminal
rate?" / "What does SOFR price between Apr and Jul?" all funnel into
this single tool via the ``meeting_reference`` parameter.

Algorithm
---------
1. Resolve the list of meetings to price from the calendar provider,
   based on the user's ``meeting_reference`` ("next", "next:N", "+N",
   ISO date, or "all").
2. Build a timeline of window endpoints:

       [today, m1_date, m2_date, ..., mN_date, (mN+1_date or +1 yr)]

   Each consecutive pair (t_i, t_{i+1}) defines a "meeting window"
   during which the policy rate is expected to stay constant.
3. For each window, compute the forward OIS rate over that window via
   ``shared.analytics.curve_bootstrap.forward_rate_between``.  That
   forward rate is the market-implied average policy rate during the
   window.
4. ``meeting_move_bps[i]`` = (implied_rate[i] − implied_rate[i−1]) × 100
   ``cumulative_bps_priced[i]`` = (implied_rate[i] − current_spot) × 100
   ``prob_25bp[i]`` = ``meeting_move_bps[i]`` / 25 × 100

The current spot rate is pulled from the shortest OIS tenor available
on the curve (1W or 1M) as a proxy for today's overnight policy rate.

The forward-rate math lives in ``shared.analytics.curve_bootstrap``;
this module only orchestrates the calendar walk + assembly.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.ois.reference.calendar import (
    CENTRAL_BANK_TO_OIS_CURVE,
    CalendarProvider,
    DEFAULT_CALENDAR,
    Meeting,
    normalise_central_bank,
    resolve_meeting_reference,
)
from rates_agent.ois.tools.schemas import (
    OISMeetingPricingCurrentMetrics,
    OISMeetingPricingInput,
    OISMeetingPricingMeetingRow,
    OISMeetingPricingOutput,
)
from shared.analytics.curve_bootstrap import (
    forward_rate_between,
    sort_tenors_by_years,
    tenor_to_years,
)
from shared.analytics.spreads import safe_float


# ============================================================================
# CONSTANTS
# ============================================================================

# 25bp is the canonical policy-move unit across G10 central banks.
# Expressed in decimal terms for probability math.
_STANDARD_MOVE_BPS = 25.0

# How far beyond the last requested meeting to extend the terminal
# window, for computing the last meeting's implied policy rate.  One
# year is standard — matches how desks quote "terminal" rates.
_TERMINAL_TAIL_YEARS = 1.0


# ============================================================================
# DB FETCH — latest full curve snapshot
# ============================================================================
# Meeting pricing is a point-in-time query (no time series needed), so
# we fetch only the most recent curve snapshot.  That keeps this tool
# fast and cheap compared to forward_rate (which rolls through history).

_FETCH_LATEST_CURVE_SQL = text("""
    WITH latest AS (
        SELECT MAX(trade_date) AS d
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND field_name   = :field_name
    )
    SELECT
        v.trade_date,
        v.tenor,
        v.field_value
    FROM macro_data.v_market_data_daily_enriched v
    JOIN latest l ON v.trade_date = l.d
    WHERE v.curve_family = :curve_family
      AND v.field_name   = :field_name
      AND v.tenor IS NOT NULL
    ORDER BY v.tenor
""")


def _fetch_latest_curve_snapshot(
    engine: Engine,
    curve_family: str,
    field_name: str,
) -> pd.DataFrame:
    """Fetch the most recent full OIS curve snapshot."""
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_LATEST_CURVE_SQL,
            {"curve_family": curve_family, "field_name": field_name},
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_ois_meeting_pricing(
    engine: Engine,
    params: OISMeetingPricingInput,
    *,
    calendar: Optional[CalendarProvider] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Price a sequence of central-bank meetings from an OIS curve.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : OISMeetingPricingInput
        Validated input with central_bank, meeting_reference, and
        optional curve_family override.
    calendar : CalendarProvider, optional
        Source of meeting dates.  Defaults to the module-level
        YAML-backed provider; injectable for tests or alternative
        data sources.
    today : date, optional
        Override "today" for deterministic testing.  Production leaves
        this at None (uses ``date.today()``).

    Returns
    -------
    dict
        Serialized ``OISMeetingPricingOutput``.  On failure, returns a
        dict with an ``"error"`` key.
    """

    calendar = calendar or DEFAULT_CALENDAR
    today = today or date.today()

    # ------------------------------------------------------------------
    # 1. Resolve curve + central bank
    # ------------------------------------------------------------------
    cb_canonical = normalise_central_bank(params.central_bank)
    if cb_canonical not in CENTRAL_BANK_TO_OIS_CURVE:
        return {
            "error": (
                f"Unknown central bank '{params.central_bank}'.  Supported: "
                f"{sorted(set(CENTRAL_BANK_TO_OIS_CURVE.keys()))}."
            )
        }
    curve_family = (
        params.curve_family
        or CENTRAL_BANK_TO_OIS_CURVE[cb_canonical]
    )

    # ------------------------------------------------------------------
    # 2. Resolve meetings
    # ------------------------------------------------------------------
    try:
        meetings_iter = resolve_meeting_reference(
            calendar, cb_canonical, params.meeting_reference, today=today,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    meetings: list[Meeting] = list(meetings_iter)
    if not meetings:
        return {
            "error": (
                f"No upcoming {cb_canonical} meetings found for reference "
                f"'{params.meeting_reference}'."
            )
        }

    # ------------------------------------------------------------------
    # 3. Fetch latest curve snapshot
    # ------------------------------------------------------------------
    raw_df = _fetch_latest_curve_snapshot(
        engine=engine,
        curve_family=curve_family,
        field_name=params.field_name,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No OIS curve data found for {curve_family} "
                f"(field='{params.field_name}')."
            )
        }

    raw_df["field_value"] = pd.to_numeric(
        raw_df["field_value"], errors="coerce",
    )
    raw_df = raw_df.dropna(subset=["field_value"])

    ordered_tenors = sort_tenors_by_years(raw_df["tenor"].tolist())
    if len(ordered_tenors) < 2:
        return {
            "error": (
                f"Insufficient tenors on {curve_family} to build a forward "
                f"curve (found {len(ordered_tenors)})."
            )
        }

    rate_by_tenor = dict(zip(raw_df["tenor"], raw_df["field_value"]))
    curve_years: List[float] = [tenor_to_years(t) for t in ordered_tenors]
    curve_rates_decimal: List[float] = [
        float(rate_by_tenor[t]) / 100.0 for t in ordered_tenors
    ]
    as_of_date = pd.to_datetime(raw_df["trade_date"].iloc[0]).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # 4. Compute current spot (overnight proxy)
    # ------------------------------------------------------------------
    # Use the shortest tenor as a proxy for today's overnight rate.
    current_spot_decimal = curve_rates_decimal[0]
    current_spot_pct = current_spot_decimal * 100.0

    # ------------------------------------------------------------------
    # 5. Walk the meeting timeline, computing forwards per window
    # ------------------------------------------------------------------
    # Timeline: [today=0, m1, m2, ..., mN, mN + terminal_tail]
    # Each window (t_i, t_{i+1}) yields the implied policy rate for
    # that period.  The "before-first-meeting" window starts at today.

    meeting_year_fractions: List[float] = [
        max(0.0, (m.meeting_date - today).days / 365.0) for m in meetings
    ]

    # Ensure meetings are chronological (belt-and-suspenders; calendar
    # should already sort, but a mixed reference could in principle
    # produce an out-of-order list).
    if meeting_year_fractions != sorted(meeting_year_fractions):
        # Re-sort meetings + year fractions together.
        paired = sorted(zip(meeting_year_fractions, meetings), key=lambda p: p[0])
        meeting_year_fractions = [p[0] for p in paired]
        meetings = [p[1] for p in paired]

    # Window boundaries: start today at 0, then each meeting date, plus
    # a terminal tail past the last meeting.
    boundaries = [0.0] + meeting_year_fractions + [
        meeting_year_fractions[-1] + _TERMINAL_TAIL_YEARS,
    ]

    implied_rates_decimal: List[Optional[float]] = []
    for i in range(len(boundaries) - 1):
        t_start = boundaries[i]
        t_end = boundaries[i + 1]
        if t_end <= t_start:
            implied_rates_decimal.append(None)
            continue
        try:
            fwd = forward_rate_between(
                curve_years, curve_rates_decimal, t_start, t_end,
            )
        except (ValueError, ZeroDivisionError):
            implied_rates_decimal.append(None)
            continue
        implied_rates_decimal.append(fwd)

    # implied_rates_decimal layout:
    #   [0]  = today → m1          ("pre-first-meeting" window; not a meeting move)
    #   [1]  = m1 → m2              (the rate after meeting 1 acts)
    #   [2]  = m2 → m3              (the rate after meeting 2 acts)
    #   ...
    #   [N]  = mN → mN + tail       (the rate after the last meeting acts)
    #
    # So meeting i's implied policy rate (post-action) is
    # implied_rates_decimal[i+1], and the move AT meeting i is
    # implied_rates_decimal[i+1] - implied_rates_decimal[i].

    meeting_rows: List[OISMeetingPricingMeetingRow] = []
    for i, m in enumerate(meetings):
        post_rate = implied_rates_decimal[i + 1]
        pre_rate = implied_rates_decimal[i]
        implied_pct = post_rate * 100.0 if post_rate is not None else None
        if post_rate is not None and pre_rate is not None:
            meeting_move_bps: Optional[float] = round(
                (post_rate - pre_rate) * 100.0 * 100.0, 2,
            )
        else:
            meeting_move_bps = None
        if implied_pct is not None:
            cumulative_bps = round(
                (implied_pct - current_spot_pct) * 100.0, 2,
            )
        else:
            cumulative_bps = None
        if meeting_move_bps is not None:
            prob_25 = round(meeting_move_bps / _STANDARD_MOVE_BPS * 100.0, 1)
        else:
            prob_25 = None

        meeting_rows.append(
            OISMeetingPricingMeetingRow(
                meeting_date=m.meeting_date.isoformat(),
                label=m.label,
                implied_policy_rate_pct=safe_float(implied_pct),
                cumulative_bps_priced=cumulative_bps,
                meeting_move_bps=meeting_move_bps,
                implied_probability_25bp_move=prob_25,
            )
        )

    # ------------------------------------------------------------------
    # 6. Build output
    # ------------------------------------------------------------------
    metrics = OISMeetingPricingCurrentMetrics(
        as_of_date=as_of_date,
        central_bank=cb_canonical,
        curve_family=curve_family,
        current_policy_rate_pct=safe_float(current_spot_pct),
        meetings=meeting_rows,
    )

    output = OISMeetingPricingOutput(current_metrics=metrics)
    return output.model_dump()
