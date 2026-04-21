"""
meeting_pricing.py — OIS-Implied Central-Bank Meeting Pricing
==============================================================

Translates an OIS curve into a meeting-by-meeting view of priced policy
rates, cumulative move from today, per-meeting implied jumps, and the
implied 25bp-move count (signed).

The flagship OIS tool — what a macro PM reads every morning.
"How many cuts are priced for the June FOMC?" / "Where's the terminal
rate?" / "What does SOFR price between Apr and Jul?" all funnel into
this single tool via the ``meeting_reference`` parameter.

Algorithm (with the boundary-isolation fix)
-------------------------------------------
To correctly isolate the move priced AT a specific meeting, the
timeline MUST include every intervening meeting between today and the
target.  Otherwise the "pre-target" rate gets contaminated by any
meetings that sit between today and the target — a query for "the
June FOMC move" on April 21 would otherwise bundle the April 29 move
into the pre-June window.

Steps:

1. Resolve the FULL set of upcoming meetings from the calendar.
2. Resolve the target meetings the user wants to see (the ``meeting_
   reference`` filter).
3. Build the boundary timeline using ALL meetings from today up to and
   including one meeting past the last target (for the post-target
   closing window).  If no meeting exists past the last target, use a
   1-year terminal tail.
4. Compute forward rates over every consecutive window using the
   shared ``forward_rate_between`` primitive.  The forward over
   ``(today, m_1)`` is the "pre-first-meeting" rate; the forward over
   ``(m_i, m_{i+1})`` is the average policy rate priced for the period
   *after* meeting m_i acts.
5. For each meeting in the boundary set, derive:
     - ``implied_policy_rate_pct``: forward over (this meeting, next)
     - ``cumulative_bps_priced``: (implied_rate - spot_rate) × 100
     - ``meeting_move_bps``: jump from previous forward to this one
     - ``implied_25bp_move_count``: ``meeting_move_bps / 25`` (signed)
6. Filter the output rows to just the meetings the user asked for.

Anchor + day-count discipline
------------------------------
Year fractions are computed from the curve's AS-OF date (the latest
market-data date in the DB), NOT ``date.today()``.  Over weekends or
holidays the DB is typically 1–3 days stale; anchoring to wall-clock
would introduce a systematic ~1bp mis-anchor error.

Day fractions use ``day_count_basis_for_curve`` — 360 for USD/EUR OIS,
365 for others.  This matters for precise forward math on ACT/360
curves like SOFR and ESTR.
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
    day_count_basis_for_curve,
    forward_rate_between,
    sort_tenors_by_years,
    tenor_to_years,
)
from shared.analytics.spreads import safe_float


# ============================================================================
# CONSTANTS
# ============================================================================

# 25bp is the canonical policy-move unit across G10 central banks.
_STANDARD_MOVE_BPS = 25.0

# How far beyond the last boundary meeting to extend the terminal
# window, for computing the post-last-meeting implied rate.  One year
# is the desk convention for terminal-rate framing.
_TERMINAL_TAIL_YEARS = 1.0


# ============================================================================
# DB FETCH — latest full curve snapshot
# ============================================================================

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
        YAML-backed provider; injectable for tests.
    today : date, optional
        Override "today" for deterministic testing.  Production leaves
        this at None (uses ``date.today()``).  Year-fraction math uses
        the curve's AS-OF date, not this value — this parameter only
        affects meeting-calendar selection ("what meetings are
        upcoming").

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
    # 2. Resolve the FULL upcoming meeting set + the target subset
    # ------------------------------------------------------------------
    # Full timeline is required for boundary math; target subset is the
    # output filter.  This is the critical fix vs the naive version that
    # built boundaries only from the target meetings — that approach
    # would bundle any intervening meetings' moves into the target's
    # "meeting_move_bps", which is wrong.
    all_upcoming = calendar.get_meetings(cb_canonical, from_date=today)
    if not all_upcoming:
        return {
            "error": (
                f"No upcoming {cb_canonical} meetings found in the "
                f"calendar (as of {today.isoformat()})."
            )
        }

    try:
        target_meetings_iter = resolve_meeting_reference(
            calendar, cb_canonical, params.meeting_reference, today=today,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    target_meetings: list[Meeting] = list(target_meetings_iter)
    if not target_meetings:
        return {
            "error": (
                f"No {cb_canonical} meetings matched reference "
                f"'{params.meeting_reference}'."
            )
        }

    target_dates: set[date] = {m.meeting_date for m in target_meetings}
    last_target_date = max(target_dates)

    # Boundary meetings: every meeting from today up to and including
    # the last target.  Sorted ascending (calendar returns them sorted).
    boundary_meetings: list[Meeting] = [
        m for m in all_upcoming if m.meeting_date <= last_target_date
    ]

    # One meeting past the last target, if any — used as the closing
    # boundary for the post-last-target window.  If none exists, fall
    # back to a 1-year terminal tail.
    next_after_last: Optional[Meeting] = next(
        (m for m in all_upcoming if m.meeting_date > last_target_date),
        None,
    )

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

    # ------------------------------------------------------------------
    # 4. Anchor year fractions to the curve's AS-OF date
    # ------------------------------------------------------------------
    # Use the latest trade date in the fetched snapshot — not wall-clock
    # today — so weekends / holidays don't introduce a 1-3 day anchor
    # mismatch.  Use the curve's own day-count convention (ACT/360 for
    # USD/EUR OIS, ACT/365 elsewhere).
    as_of_date_raw = pd.to_datetime(raw_df["trade_date"].iloc[0])
    as_of_date: date = as_of_date_raw.date() if hasattr(as_of_date_raw, "date") else as_of_date_raw
    as_of_iso = as_of_date.isoformat()
    day_count = day_count_basis_for_curve(curve_family)

    def years_from_as_of(target: date) -> float:
        """Year fraction from the curve's as-of date using the curve's
        day-count divisor.  Never negative (caller ensures target >=
        as_of)."""
        return max(0.0, (target - as_of_date).days / day_count)

    # Filter out any boundary meetings that fall before the as-of date
    # (can happen if the DB is very stale or the calendar includes
    # today's date but the DB hasn't caught up).  This guarantees
    # boundaries are strictly after as-of.
    boundary_meetings = [
        m for m in boundary_meetings if m.meeting_date > as_of_date
    ]
    if not boundary_meetings:
        return {
            "error": (
                f"All upcoming {cb_canonical} meetings are on or before "
                f"the curve's as-of date ({as_of_iso}).  The calendar "
                "may be out of date or the DB may be stale."
            )
        }

    # ------------------------------------------------------------------
    # 5. Current spot (overnight proxy) from the shortest OIS tenor
    # ------------------------------------------------------------------
    current_spot_decimal = curve_rates_decimal[0]
    current_spot_pct = current_spot_decimal * 100.0

    # ------------------------------------------------------------------
    # 6. Build boundaries and compute forwards per window
    # ------------------------------------------------------------------
    boundary_year_fractions: List[float] = [
        years_from_as_of(m.meeting_date) for m in boundary_meetings
    ]

    if next_after_last is not None and next_after_last.meeting_date > as_of_date:
        terminal_bound = years_from_as_of(next_after_last.meeting_date)
    else:
        terminal_bound = boundary_year_fractions[-1] + _TERMINAL_TAIL_YEARS

    # boundaries[0] = 0 (as-of)
    # boundaries[i] for 1..K = m_i's year fraction
    # boundaries[K+1] = terminal window end
    boundaries: List[float] = [0.0] + boundary_year_fractions + [terminal_bound]

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

    # Layout:
    #   implied_rates_decimal[0]   = (as-of, m_1)      pre-first-meeting
    #   implied_rates_decimal[1]   = (m_1,  m_2)       post-m_1 / pre-m_2
    #   ...
    #   implied_rates_decimal[K]   = (m_K,  terminal)  post-last-boundary-meeting
    #
    # For boundary meeting i (0-indexed in boundary_meetings), the
    # "post-meeting" rate is implied_rates_decimal[i+1], and the
    # "pre-meeting" rate is implied_rates_decimal[i].  Therefore:
    #   meeting_move_bps[i] = (post - pre) * 100 * 100
    #                       = (post - pre) in pct × 100 for bps

    # ------------------------------------------------------------------
    # 7. Build meeting rows for ALL boundary meetings, filter to targets
    # ------------------------------------------------------------------
    all_rows: List[tuple[Meeting, OISMeetingPricingMeetingRow]] = []
    for i, m in enumerate(boundary_meetings):
        post_rate = implied_rates_decimal[i + 1]
        pre_rate = implied_rates_decimal[i]

        implied_pct: Optional[float] = (
            post_rate * 100.0 if post_rate is not None else None
        )

        if post_rate is not None and pre_rate is not None:
            # (post - pre) is in decimal → × 100 → pct → × 100 → bps.
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
            # Signed move-count — +1.0 = a full 25bp hike, -0.6 = 15bps
            # of cut priced, +1.5 = 37.5bps of hike (1.5x a move).
            # Rounded to 3 decimals because this value is often small.
            move_count: Optional[float] = round(
                meeting_move_bps / _STANDARD_MOVE_BPS, 3,
            )
        else:
            move_count = None

        row = OISMeetingPricingMeetingRow(
            meeting_date=m.meeting_date.isoformat(),
            label=m.label,
            is_scheduled=m.scheduled,
            implied_policy_rate_pct=safe_float(implied_pct),
            cumulative_bps_priced=cumulative_bps,
            meeting_move_bps=meeting_move_bps,
            implied_25bp_move_count=move_count,
        )
        all_rows.append((m, row))

    # Filter to the user's requested targets, preserving chronological
    # order.  If the user asked for "all" or "next:N" where the targets
    # are a contiguous prefix, this is a no-op.  For specific-date
    # queries that had intervening meetings, this hides the intervening
    # rows so the user sees only what they asked about — but each
    # target's move_bps is still correctly isolated thanks to step 6.
    output_rows: List[OISMeetingPricingMeetingRow] = [
        row for (m, row) in all_rows if m.meeting_date in target_dates
    ]

    projected_count = sum(1 for row in output_rows if not row.is_scheduled)

    # ------------------------------------------------------------------
    # 8. Build output
    # ------------------------------------------------------------------
    metrics = OISMeetingPricingCurrentMetrics(
        as_of_date=as_of_iso,
        central_bank=cb_canonical,
        curve_family=curve_family,
        current_spot_rate_pct=safe_float(current_spot_pct),
        projected_meeting_count=projected_count,
        meetings=output_rows,
    )

    output = OISMeetingPricingOutput(current_metrics=metrics)
    return output.model_dump()
