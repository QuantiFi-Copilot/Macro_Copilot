"""Pydantic schemas for the OIS meeting-pricing tool."""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class OISMeetingPricingInput(BaseModel):
    """Parameters the LLM must extract to compute central-bank
    meeting-by-meeting pricing from an OIS curve.

    Examples the LLM should route to this tool:
      - "How many cuts is SOFR pricing for the June FOMC?"
      - "What's the terminal rate?"
      - "What's priced for the next ECB meeting?"
      - "Show me the next 4 FOMC meetings."

    One tool, many questions: use ``meeting_reference`` and
    ``num_meetings`` together rather than splitting terminal-rate /
    cuts-priced / next-meeting-move into separate tools.
    """

    central_bank: str = Field(
        ...,
        description=(
            "Central bank whose meetings to price.  Canonical values: "
            "'FED' (also accepts 'FOMC'), 'ECB', 'BOE', 'BOJ', 'RBA', "
            "'BOC'.  Case-insensitive.  The tool automatically selects "
            "the matching OIS curve unless curve_family is overridden."
        ),
    )
    meeting_reference: str = Field(
        default="next:4",
        description=(
            "Which meeting(s) to price.  Supported syntax: "
            "'next' (the single next meeting), 'next:N' (the next N "
            "meetings, e.g. 'next:6'), '+N' (the Nth meeting from "
            "today), 'YYYY-MM-DD' (the meeting on or nearest-after "
            "that date), 'all' (every upcoming meeting in the "
            "calendar).  Default 'next:4' is the standard morning-"
            "briefing view.  For SPECIFIC-meeting queries like "
            "'the June FOMC', pass the ISO date — the tool internally "
            "uses the full meeting timeline (including any meetings "
            "between today and the target) to isolate the requested "
            "meeting's implied move correctly."
        ),
    )
    curve_family: Optional[str] = Field(
        default=None,
        description=(
            "Override the OIS curve to use.  Normally left blank — the "
            "tool maps FED→USD_SOFR_OIS, ECB→EUR_ESTR_OIS, BOE→"
            "GBP_SONIA_OIS, BOJ→JPY_OIS, RBA→AUD_OIS, BOC→CAD_OIS.  "
            "Only set this if the user explicitly names a non-default "
            "curve (e.g. 'FED pricing via JPY OIS' — an unusual ask)."
        ),
    )
    field_name: str = Field(
        default="PX_LAST",
        description=(
            "Observation field to use.  Defaults to 'PX_LAST' (mid par "
            "swap rate).  Other valid: 'PX_BID', 'PX_ASK'."
        ),
    )


class OISMeetingPricingMeetingRow(BaseModel):
    """One meeting's priced rate and move."""
    meeting_date: str = Field(..., description="ISO date of the meeting.")
    label: str = Field(..., description="Human-readable tag, e.g. 'FOMC Jun 2026'.")
    is_scheduled: bool = Field(
        ...,
        description=(
            "True if this meeting date has been officially published by "
            "the central bank; False if it is our projection or "
            "approximation.  Projected dates are useful for morning-"
            "briefing views but should not be treated as authoritative."
        ),
    )
    implied_policy_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Average policy rate implied for the period from this "
            "meeting to the next meeting (percent).  This is the "
            "forward OIS rate covering that meeting window."
        ),
    )
    cumulative_bps_priced: Optional[float] = Field(
        None,
        description=(
            "Cumulative basis-points of policy move priced from today "
            "through the END of this meeting's window.  Signed: "
            "positive = hikes priced, negative = cuts priced.  "
            "Measured against ``current_spot_rate_pct``."
        ),
    )
    meeting_move_bps: Optional[float] = Field(
        None,
        description=(
            "Bps of policy move priced at THIS specific meeting (the "
            "jump from the previous meeting's implied rate to this "
            "meeting's implied rate).  Computed using the full "
            "intervening meeting timeline, so this isolates the move "
            "at this meeting alone — not contaminated by moves at "
            "meetings between today and this one."
        ),
    )
    implied_25bp_move_count: Optional[float] = Field(
        None,
        description=(
            "Number of standard 25bp moves priced at this meeting, "
            "signed by direction.  ``meeting_move_bps / 25``.  "
            "+1.0 = one full 25bp hike priced; -0.6 = 15bps of cut "
            "priced (60% of a full 25bp cut); +1.5 = 37.5bps of hike "
            "priced (1.5x a standard move).  "
            "NOT a probability in the formal sense — it's a normalised "
            "move-count that desks use as shorthand.  Divide by 100 "
            "and clip to [0, 1] if you need a single-move probability."
        ),
    )


class OISMeetingPricingCurrentMetrics(BaseModel):
    """Top-level meeting-pricing snapshot."""
    as_of_date: str
    central_bank: str
    curve_family: str
    current_spot_rate_pct: Optional[float] = Field(
        None,
        description=(
            "The shortest quoted OIS tenor (typically 1W or 1M) — used "
            "as a PROXY for today's overnight policy rate.  Note this "
            "is NOT exactly the target fed funds / deposit facility "
            "rate: it is the average SOFR (or equivalent) the market "
            "expects over the next 1-4 weeks, which may already price "
            "imminent policy moves.  Use ``cumulative_bps_priced`` on "
            "each meeting row to measure moves relative to this proxy."
        ),
    )
    projected_meeting_count: int = Field(
        0,
        description=(
            "Number of meetings in this response whose dates are our "
            "projections rather than officially published.  A non-zero "
            "value is a signal to the user that some outputs reflect "
            "estimated scheduling."
        ),
    )
    meetings: List[OISMeetingPricingMeetingRow]


class OISMeetingPricingOutput(BaseModel):
    """Top-level response the MCP server returns."""
    current_metrics: OISMeetingPricingCurrentMetrics
