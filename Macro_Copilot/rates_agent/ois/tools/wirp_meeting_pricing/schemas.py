"""Pydantic schemas for the wirp_meeting_pricing tool.

Per-meeting WIRP pricing snapshot for one central bank (FOMC / ECB
/ BOE / BOJ).  INGEST primitive — surfaces Bloomberg WIRP fields
verbatim per ADR 0009.

Output shape
------------
List of meeting snapshots — categorical / list-shaped output (the
natural shape of "N meeting snapshots").  NOT bridge-composable as
Series or Panel in V1.  Registered in
``rates_agent.workflows.WORKFLOW_INCOMPATIBLE_TOOLS`` with explicit
rationale, mirroring the get_otr_history / classify_curve_move
precedent.

PR8 framing
-----------
Two-knob cohesive central methodology surface (same pattern as
PR8's worked example for pca_yield_curve where the knobs together
define the model):

  - ``central_bank`` (instrument selector — which CB's meetings).
  - ``selection_mode`` enum (``next_n_meetings`` vs
    ``specific_meeting_date``).
  - Dependent: ``n_meetings`` is required when
    selection_mode='next_n_meetings'; ``meeting_date`` is required
    when selection_mode='specific_meeting_date'.

A ``@model_validator(mode='after')`` enforces the mode-specific
required fields so the validation error fires at the API boundary,
not inside compute.  ``extra='forbid'`` is set so a caller passing
an unrecognised field gets a loud rejection (same P2 lesson
applied to nfp_surprise after Codex's PR #188 review).

Validation
----------
- ``central_bank`` canonicalised to uppercase; checked against the
  YAML-locked ``supported_central_banks`` set in compute (not in
  Pydantic, so the supported set is the single source of truth in
  YAML).
- ``n_meetings`` bounded [1, 24].
- ``meeting_date`` is an ISO date string (Pydantic parses to date).
"""

from __future__ import annotations

import re
from datetime import date as _date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Central-bank canonical pattern — uppercase 3-4 letters per the
# ADR 0009 §2 region-prefix table (FOMC / ECB / BOE / BOJ).
_CENTRAL_BANK_PATTERN = re.compile(r"^[A-Z]{3,4}$")


def _bundled_default_n_meetings() -> int:
    """Read the default n_meetings from the bundled config.yaml.

    Same lazy-factory pattern as get_otr_history / otr_ofr_spread /
    cpi_surprise / nfp_surprise.  Loaded inside the factory to avoid
    a top-level import cycle through compute.
    """
    from rates_agent.ois.tools.wirp_meeting_pricing.compute import (
        CONFIG_PATH,
    )
    from shared.config import load_tool_config

    cfg = load_tool_config(CONFIG_PATH)
    return int(cfg.convention_value("default_n_meetings"))


class WirpMeetingPricingInput(BaseModel):
    """Parameters the LLM extracts to query WIRP pricing.

    Two-knob cohesive central surface (PR8):
      - ``central_bank`` (instrument selector).
      - ``selection_mode`` + mode-specific dependent field
        (``n_meetings`` for next-N, ``meeting_date`` for specific).

    ``extra='forbid'`` set so unrecognised inputs raise loudly
    rather than silently routing the caller to a different (or
    default) selection — same P2 protection applied to
    nfp_surprise after Codex's PR #188 review.
    """

    model_config = ConfigDict(extra="forbid")

    central_bank: str = Field(
        ...,
        description=(
            "Central bank whose WIRP pricing to query.  Convention: "
            "uppercase 3-4 letter code matching the YAML-locked "
            "``supported_central_banks`` set — currently 'FOMC' "
            "(United States), 'ECB' (European Central Bank), 'BOE' "
            "(Bank of England), or 'BOJ' (Bank of Japan).  Maps to "
            "ADR 0009 §2's region prefix internally (US0B / EZ0B / "
            "GB0B / JP0B) — callers do NOT supply the region prefix."
        ),
    )
    selection_mode: Literal["next_n_meetings", "specific_meeting_date"] = Field(
        default="next_n_meetings",
        description=(
            "How to choose meetings.  ``next_n_meetings`` returns "
            "the next ``n_meetings`` scheduled meetings from today "
            "forward (default mode).  ``specific_meeting_date`` "
            "returns exactly one meeting on the given "
            "``meeting_date`` (which can be past or future, within "
            "the WIRP horizon).  Two distinct desk access patterns "
            "for the same primitive concept — not a methodology "
            "branch (PR2: same output shape, same arithmetic — "
            "just a different meeting-selection rule)."
        ),
    )
    n_meetings: Optional[int] = Field(
        default=None,
        ge=1,
        le=24,
        description=(
            "Number of forward meetings to return.  Required when "
            "``selection_mode='next_n_meetings'``; must be None "
            "when ``selection_mode='specific_meeting_date'``.  "
            "When selection_mode is next_n_meetings AND n_meetings "
            "is None, the bundled YAML default "
            "(``default_n_meetings``, currently 6) is used by the "
            "compute layer."
        ),
    )
    meeting_date: Optional[_date] = Field(
        default=None,
        description=(
            "ISO-format date of the specific meeting to return.  "
            "Required when ``selection_mode='specific_meeting_date'``; "
            "must be None when ``selection_mode='next_n_meetings'``.  "
            "Must match a ``maturity_date`` value on a wirp_meeting "
            "instrument_master row for the requested central_bank "
            "— see ADR 0009 §1's typed-column mapping."
        ),
    )

    # =====================================================================
    # Canonicalisation — code-owned per PR7
    # =====================================================================

    @field_validator("central_bank", mode="before")
    @classmethod
    def _canonicalise_central_bank(cls, v: object) -> str:
        """Uppercase 3-4 letter central-bank code.  Lowercase input
        upcased; whitespace stripped; other shapes rejected loudly
        so the caller hits the API boundary rather than degrading
        to an empty SQL result at the compute layer."""
        if not isinstance(v, str):
            raise ValueError(
                f"central_bank must be a string (uppercase 3-4 letter "
                f"code: 'FOMC', 'ECB', 'BOE', 'BOJ'); got "
                f"{type(v).__name__}"
            )
        s = v.strip().upper()
        if not _CENTRAL_BANK_PATTERN.match(s):
            raise ValueError(
                f"central_bank={v!r} does not match the WIRP-playbook "
                f"convention (uppercase 3-4 letter code matching "
                f"ADR 0009 §2's region-prefix table: 'FOMC', 'ECB', "
                f"'BOE', 'BOJ').  See the supported_central_banks "
                f"convention in config.yaml + ADR 0009 §2."
            )
        return s

    # =====================================================================
    # Mode-specific required-field invariant — code-owned (PR7) +
    # API-boundary enforcement (P6 — no silent failure)
    # =====================================================================

    @model_validator(mode="after")
    def _validate_mode_specific_inputs(self) -> "WirpMeetingPricingInput":
        """Enforce the mode → required-field invariant per the
        cohesive-central-surface PR8 framing:

          - ``next_n_meetings``: meeting_date must be None
            (n_meetings may be None — the YAML default applies).
          - ``specific_meeting_date``: meeting_date is required,
            n_meetings must be None.

        Fires at validation time so an inconsistent input fails
        loudly before compute() runs (P11 — honest refusal on the
        method-specific parameter shape, same discipline as
        financing_rate's @model_validator).
        """
        if self.selection_mode == "next_n_meetings":
            if self.meeting_date is not None:
                raise ValueError(
                    "meeting_date must be None when "
                    "selection_mode='next_n_meetings'.  Either remove "
                    "the meeting_date input or switch to "
                    "selection_mode='specific_meeting_date'."
                )
        elif self.selection_mode == "specific_meeting_date":
            if self.meeting_date is None:
                raise ValueError(
                    "meeting_date is required when "
                    "selection_mode='specific_meeting_date'.  Provide "
                    "an ISO date string for the meeting to return."
                )
            if self.n_meetings is not None:
                raise ValueError(
                    "n_meetings must be None when "
                    "selection_mode='specific_meeting_date'.  Remove "
                    "n_meetings or switch to "
                    "selection_mode='next_n_meetings'."
                )
        # The Literal[...] type on selection_mode is exhaustively
        # checked by Pydantic before this validator runs; no else
        # branch needed.
        return self


class WirpMeetingSnapshot(BaseModel):
    """One meeting's WIRP snapshot.

    Mix of Bloomberg-ingested fields (P12 boundary — surfaced
    verbatim) and the desk-recognised IDENTITY-derived hike / cut /
    hold probabilities.  Identity columns
    (``vendor_ticker``, the four ``bloomberg_ticker_*`` values) are
    provenance so a consumer can trace each value back to the exact
    Bloomberg ticker.
    """

    central_bank: str = Field(
        ...,
        description="Central bank — 'FOMC', 'ECB', 'BOE', or 'BOJ'.",
    )
    meeting_date: str = Field(
        ...,
        description=(
            "Meeting date (ISO YYYY-MM-DD) — the ``maturity_date`` "
            "column on the synthetic ``wirp_meeting`` instrument."
        ),
    )
    meeting_token: Optional[str] = Field(
        None,
        description=(
            "Bloomberg meeting-month token (MMMYYYY, e.g. 'JUN2026') "
            "from ``instrument_master.attributes->>'wirp_meeting_token'``.  "
            "The token used to construct the four real Bloomberg "
            "tickers per ADR 0009 §3."
        ),
    )
    as_of_date: str = Field(
        ...,
        description=(
            "Latest ``trade_date`` in market_data_daily for this "
            "meeting's WIRP fields — typically yesterday's close.  "
            "Per-meeting because two meetings may have different "
            "as_of_dates (e.g. a past meeting's tickers may age out "
            "before the next one)."
        ),
    )

    # ---- Bloomberg-ingested fields (P12 boundary) ----
    #
    # All four are surfaced verbatim per ADR 0009 §1.  No identity-
    # derived "hike probability / hold probability / cut probability"
    # fields are emitted — see the docstring note on field naming
    # below for the Codex-review correction (PR #190).
    implied_policy_rate_pct: Optional[float] = Field(
        None,
        description=(
            "WIRP_IMPLIED_RATE — Bloomberg's implied post-meeting "
            "effective policy rate (Fed funds rate target / ECB depo "
            "rate / etc.) in percent.  Ingested verbatim per ADR "
            "0009 §1's P12 boundary."
        ),
    )
    cumulative_move_prob_pct: Optional[float] = Field(
        None,
        description=(
            "WIRP_MOVE_PROB — Bloomberg's CUMULATIVE signed "
            "probability of a 25bp move at this meeting, in percent.  "
            "Sign: + = hike-leaning, − = cut-leaning.  "
            "**CUMULATIVE** per rates_agent/playbooks/wirp.yml — the "
            "value CAN exceed ±100 when the market prices more than "
            "one 25bp move (observed live-DB range: -360.1 .. 548.0). "
            "It is NOT a single-event probability bounded in "
            "[0, 100], and a naive ``hike = max(p, 0)`` / "
            "``cut = max(-p, 0)`` / ``hold = 100 - |p|`` "
            "identity DOES NOT hold and produces impossible "
            "values (e.g. hold = −4.9% for BOE 2025-05-08).  This "
            "primitive therefore SURFACES the raw value verbatim "
            "and DOES NOT emit derived hike / hold / cut "
            "probability fields; a future primitive that maps the "
            "cumulative WIRP_NUM_MOVES + WIRP_MOVE_PROB joint "
            "distribution into desk-recognised step-by-step "
            "probabilities is documented in "
            "methodology.planned_extensions."
        ),
    )
    num_25bp_moves_priced: Optional[float] = Field(
        None,
        description=(
            "WIRP_NUM_MOVES — Bloomberg's signed count of 25bp moves "
            "priced into the meeting.  Fractional and beyond-±1 "
            "values are normal (observed live-DB range: -9.57 .. 6.729).  "
            "Surfaced verbatim per ADR 0009 §1."
        ),
    )
    rate_change_native: Optional[float] = Field(
        None,
        description=(
            "WIRP_RATE_CHANGE — Bloomberg's implied change in the "
            "rate vs the current effective rate.  Per ``wirp.yml``'s "
            "Stage-B verification: the unit is PERCENTAGE POINTS "
            "(NOT basis points) — confirmed by the probe's observed "
            "range -2.392 .. 1.013 (a bp-unit field would print "
            "values in the ±100s, not ±2).  The ``_native`` suffix "
            "is kept for backward-compat with the ADR 0009 §1 "
            "naming convention (Bloomberg's value is stored "
            "verbatim, no conversion applied)."
        ),
    )

    # ---- Provenance — exact Bloomberg tickers (ADR 0009 §1) ----
    vendor_ticker: str = Field(
        ...,
        description=(
            "Synthetic ``vendor_ticker`` of the WIRP meeting "
            "instrument: 'WIRP:{central_bank}:{meeting_date}' per "
            "ADR 0009 §1.  Used to dedupe meetings across runs."
        ),
    )
    bloomberg_ticker_implied_rate: Optional[str] = Field(
        None,
        description=(
            "Real Bloomberg ticker for the WIRP_IMPLIED_RATE field "
            "(e.g. 'US0BFR JUN2026 Index').  Per-field provenance "
            "stored in instrument_master.attributes per ADR 0009 §1."
        ),
    )
    bloomberg_ticker_move_prob: Optional[str] = Field(
        None,
        description="Real Bloomberg ticker for WIRP_MOVE_PROB (e.g. 'US0BPR JUN2026 Index').",
    )
    bloomberg_ticker_num_moves: Optional[str] = Field(
        None,
        description="Real Bloomberg ticker for WIRP_NUM_MOVES (e.g. 'US0BNM JUN2026 Index').",
    )
    bloomberg_ticker_rate_change: Optional[str] = Field(
        None,
        description="Real Bloomberg ticker for WIRP_RATE_CHANGE (e.g. 'US0BCH JUN2026 Index').",
    )


class WirpMeetingPricingCurrentMetrics(BaseModel):
    """Top-level snapshot summary — the next-up meeting's read.

    For ``next_n_meetings`` mode, this is the first meeting in the
    list (i.e. the next scheduled meeting).  For
    ``specific_meeting_date`` mode, it's the requested meeting.
    """

    central_bank: str
    selection_mode: str
    n_meetings_returned: int = Field(
        ...,
        ge=0,
        description=(
            "Count of meeting snapshots in the ``meetings`` list — "
            "may be less than the requested n_meetings if the WIRP "
            "horizon has fewer ingested meetings (honest absence)."
        ),
    )
    n_meetings_requested: Optional[int] = Field(
        None,
        description=(
            "Echo of the input's n_meetings (or the YAML default "
            "when next_n_meetings mode was used without explicit "
            "n_meetings).  None when selection_mode is "
            "specific_meeting_date."
        ),
    )
    requested_meeting_date: Optional[str] = Field(
        None,
        description=(
            "Echo of the input's meeting_date (ISO).  None when "
            "selection_mode is next_n_meetings."
        ),
    )

    # Snapshot of the FIRST meeting (next-up).
    next_meeting_date: Optional[str] = Field(
        None,
        description=(
            "Meeting date (ISO) of the first meeting in the returned "
            "list — the 'next up' meeting in ``next_n_meetings`` "
            "mode, or the requested meeting in "
            "``specific_meeting_date`` mode.  None when the meetings "
            "list is empty."
        ),
    )
    next_implied_policy_rate_pct: Optional[float] = Field(
        None,
        description=(
            "WIRP_IMPLIED_RATE at the next-up meeting (or the "
            "requested meeting), in percent.  Convenience field — "
            "mirrors ``meetings[0].implied_policy_rate_pct``."
        ),
    )
    next_cumulative_move_prob_pct: Optional[float] = Field(
        None,
        description=(
            "WIRP_MOVE_PROB at the next-up meeting — CUMULATIVE "
            "signed move probability (can exceed ±100 per the "
            "WirpMeetingSnapshot docstring).  Mirrors "
            "``meetings[0].cumulative_move_prob_pct``."
        ),
    )
    next_num_25bp_moves_priced: Optional[float] = Field(
        None,
        description=(
            "WIRP_NUM_MOVES at the next-up meeting — signed count of "
            "25bp moves priced.  Mirrors "
            "``meetings[0].num_25bp_moves_priced``."
        ),
    )
    next_rate_change_native: Optional[float] = Field(
        None,
        description=(
            "WIRP_RATE_CHANGE at the next-up meeting — implied rate "
            "change vs current effective rate, in percentage points "
            "(per wirp.yml's Stage-B verification).  Mirrors "
            "``meetings[0].rate_change_native``."
        ),
    )
    next_as_of_date: Optional[str] = Field(
        None,
        description="Snapshot date for the next-up meeting's WIRP fields.",
    )


class WirpMeetingPricingOutput(BaseModel):
    """Top-level response for the wirp_meeting_pricing tool.

    List-shaped categorical output.  Per ADR 0009 + the
    get_otr_history precedent, this primitive is NOT bridge-
    composable in V1 — registered in WORKFLOW_INCOMPATIBLE_TOOLS
    with rationale.

    ``methodology_note`` surfaces the MANDATORY P5 disclosure from
    the brief: source is Bloomberg WIRP via ADR 0009; nothing is
    recomputed; hike/cut/hold probs are identity-derived from
    Bloomberg's signed WIRP_MOVE_PROB.  Cannot be silently dropped
    by a future refactor (required field, like
    cpi_surprise / nfp_surprise / get_otr_history's
    methodology_note).
    """

    current_metrics: WirpMeetingPricingCurrentMetrics
    meetings: List[WirpMeetingSnapshot] = Field(
        default_factory=list,
        description=(
            "Per-meeting WIRP snapshots, ordered by meeting_date "
            "ascending.  May be the empty list when ``selection_mode``"
            "='next_n_meetings'`` and no meetings are inside the "
            "WIRP horizon (honest absence)."
        ),
    )
    methodology_note: str = Field(
        ...,
        description=(
            "Plain-language disclosure surfacing the MANDATORY P5 "
            "card per the brief: source is Bloomberg WIRP via "
            "ADR 0009; NOT recomputed from STIR futures or OIS; "
            "hike-probability anchored at consensus 25bp move; "
            "hike/cut/hold derived by identity from the signed "
            "WIRP_MOVE_PROB; rate_change unit is NATIVE Bloomberg "
            "(unit ambiguity per ADR 0009 §1)."
        ),
    )


__all__ = [
    "WirpMeetingPricingInput",
    "WirpMeetingSnapshot",
    "WirpMeetingPricingCurrentMetrics",
    "WirpMeetingPricingOutput",
]
