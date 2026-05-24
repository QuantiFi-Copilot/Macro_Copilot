"""Pydantic schemas for the get_otr_history tool.

Cash-bond on-the-run (OTR) transition log for one (country, tenor) sovereign
slot — built on top of the substrate ADRs 0003 (otr_history table) / 0005
(sovereign_cash_bonds.yml universe) / 0007 (resolver).

Output shape
------------
This primitive's output is NOT a canonical
``shared.schemas.time_series.TimeSeries`` payload.  Each transition is an
SCD2 window with categorical identity fields (CUSIP, ISIN, vendor_ticker,
maturity_date) plus an effective range — not a numerical observation per
date.  Per the worked-example table in
docs_revamped/02_components/primitive/README.md ("workflow-bridge artifact
shapes"), only ``Series`` and ``Panel`` outputs are bridge-composable in V1;
categorical / list-shaped outputs are valid primitives that are NOT
registered in ``rates_agent/workflows/__init__.py`` until the bridge gains
support for additional shapes.  This primitive is intentionally MCP-only
(matches the precedent of ``curve_move_classifier``, which is a categorical
single-observation primitive also absent from the workflow registry).  The
PR description explicitly states ``workflow_compatible: false``.

Validation layering
-------------------
- ``country`` and ``tenor`` are required instrument selectors with no
  Pydantic-layer enum (per PR1 + PR5 — these are scoped by what
  ``macro_data.otr_history`` contains, not by an in-code closed family
  the primitive maintains).  Cross-field invariants are mathematical
  truths (date ordering on the wire is sorted in ``compute.py``); none
  belong in YAML (PR7).
- ``lookback_days`` is the central methodology knob (PR8) — Pydantic
  default is read from ``config.yaml``'s ``default_lookback_days``
  convention via the lazy-lookup pattern established by
  ``curve_move_classifier``, so editing the YAML default actually
  changes the schema's runtime default (P10 — single source of truth).

Validators that encode invariants stay here in code; the schema has
no cross-field invariants beyond Pydantic's ``ge`` / ``le`` constraints
on ``lookback_days``.
"""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# Tenor pattern fixed by the ``tenor_canonicalisation`` convention
# (config.yaml + ADR 0007 §4 + 0005 §3) — integer-Y form matching the
# slot labels the resolver writes.  Compiled at module load so the
# validator path is O(1) per call.
_TENOR_PATTERN = re.compile(r"^[1-9][0-9]*Y$")

# Country pattern fixed by the same convention — uppercase ISO-3166-alpha-2
# (the typical case; the resolver does not write longer codes today).
# Lower-case input is canonicalised to upper rather than rejected, since
# the alias is unambiguous; non-letter input fails loudly.
_COUNTRY_PATTERN = re.compile(r"^[A-Z]{2,3}$")


def _bundled_default_lookback_days() -> int:
    """Read the default lookback from the bundled ``config.yaml``.

    Looked up lazily inside the field-default factory so circular-import
    risk is zero (the schema doesn't import compute or ToolConfig at
    module-load time; only the factory path touches them).  Cached by
    the underlying ``load_tool_config`` so this is a one-time cost.

    Raises loudly when ``config.yaml`` cannot be loaded or the
    ``default_lookback_days`` convention is missing — per P6 (no
    silent failure) and P10 (single source of truth).  A corrupt or
    missing YAML is a real bug; hiding it behind a fallback would
    create a second source of truth for the default and silently
    drift away from the documented methodology.
    """
    # Local imports to avoid a top-of-module cycle through compute.
    from rates_agent.sovereign_bonds.tools.get_otr_history.compute import (
        CONFIG_PATH,
    )
    from shared.config import load_tool_config

    cfg = load_tool_config(CONFIG_PATH)
    return int(cfg.convention_value("default_lookback_days"))


class OtrHistoryInput(BaseModel):
    """Parameters the LLM extracts to query a single OTR slot's history."""

    country: str = Field(
        ...,
        description=(
            "Sovereign country code as stored in macro_data.otr_history. "
            "Convention: uppercase ISO-3166-alpha-2 — e.g. 'US', 'DE', "
            "'GB', 'JP', 'FR', 'IT', 'ES', 'CA', 'AU'."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Canonical slot tenor as stored in macro_data.otr_history.  "
            "Convention: integer-Y, matching sovereign_cash_bonds.yml — "
            "'2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'."
        ),
    )
    lookback_days: int = Field(
        default_factory=_bundled_default_lookback_days,
        ge=30,
        le=3650,
        description=(
            "Calendar days of trailing OTR-transition history to display.  "
            "The bundled default is read from config.yaml's "
            "``default_lookback_days`` convention (365 calendar days "
            "= 1Y; aligns with the existing catalogue value for "
            "this shared key — PR13 cross-config consistency).  Per "
            "PR8, this is the single LLM-controlled central "
            "methodology knob for this primitive."
        ),
    )

    # =====================================================================
    # Canonicalisation invariants — code-owned per PR7 (invariants in code,
    # not YAML).  The CONVENTION the rule is documented under is
    # ``tenor_canonicalisation`` in config.yaml, source tag
    # ``adr_0007_otr_canonicalisation``.  The invariants below ENFORCE that
    # convention at the API boundary so mistyped inputs ("10y", "us ",
    # "USA1") fail loudly with a typed ValidationError, rather than
    # degrading silently to honest absence at the SQL layer (where a
    # mistyped input that doesn't match any row LOOKS like a legitimate
    # "no resolver coverage" result).
    # =====================================================================

    @field_validator("country", mode="before")
    @classmethod
    def _canonicalise_country(cls, v: object) -> str:
        """Uppercase ISO-3166-alpha-2 (or alpha-3); reject anything else.

        Per the ``tenor_canonicalisation`` convention (config.yaml).
        Lowercase input is upcased rather than rejected because the
        alias is unambiguous and rejecting it would be hostile.
        Non-letter or wrong-length input is rejected loudly (P6 — no
        silent failure).
        """
        if not isinstance(v, str):
            raise ValueError(
                f"country must be a string (uppercase ISO-3166-alpha-2/3); "
                f"got {type(v).__name__}"
            )
        s = v.strip().upper()
        if not _COUNTRY_PATTERN.match(s):
            raise ValueError(
                f"country={v!r} does not match the resolver's convention "
                f"(uppercase ISO-3166-alpha-2/3; e.g. 'US', 'DE', 'GB', "
                f"'JP', 'FR', 'IT', 'ES').  See the "
                f"``tenor_canonicalisation`` convention in config.yaml + "
                f"ADR 0007 §4 / ADR 0005 §3."
            )
        return s

    @field_validator("tenor", mode="before")
    @classmethod
    def _canonicalise_tenor(cls, v: object) -> str:
        """Integer-Y tenor matching sovereign_cash_bonds.yml slot labels.

        Per the ``tenor_canonicalisation`` convention (config.yaml).
        '10y' is upcased to '10Y' (unambiguous alias); anything other
        than the integer-Y pattern is rejected loudly so the SCD2 lookup
        cannot silently miss.
        """
        if not isinstance(v, str):
            raise ValueError(
                f"tenor must be a string (integer-Y form); "
                f"got {type(v).__name__}"
            )
        s = v.strip().upper()
        if not _TENOR_PATTERN.match(s):
            raise ValueError(
                f"tenor={v!r} does not match the resolver's convention "
                f"(integer-Y form matching sovereign_cash_bonds.yml; "
                f"e.g. '2Y', '5Y', '10Y', '30Y').  See the "
                f"``tenor_canonicalisation`` convention in config.yaml + "
                f"ADR 0007 §4 / ADR 0005 §3."
            )
        return s


class OtrHistoryTransitionRow(BaseModel):
    """One row of the SCD2 OTR history for a (country, tenor) slot.

    Carries the OTR bond's identity fields plus the effective window
    boundaries.  ``effective_to`` is ``None`` when the window is the
    currently-open one (the bond is currently on-the-run for the slot).
    """

    effective_from: str = Field(
        ...,
        description=(
            "ISO date (YYYY-MM-DD) the window opened on — the resolver's "
            "first confirmed observation of this bond as OTR for the slot "
            "(detection-date precision per TD #27b)."
        ),
    )
    effective_to: Optional[str] = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) the window closed on, or ``None`` when "
            "the window is currently open (the bond is currently OTR for "
            "the slot).  Preserved on the wire as None rather than coerced "
            "to today's date so consumers can recognise the still-open "
            "window."
        ),
    )
    otr_instrument_id: int = Field(
        ...,
        description=(
            "FK into macro_data.instrument_master for the bond that was "
            "on-the-run during this window."
        ),
    )
    cusip: Optional[str] = Field(
        default=None,
        description="CUSIP of the OTR bond (NULL for non-US sovereigns).",
    )
    isin: Optional[str] = Field(
        default=None,
        description="ISIN of the OTR bond (typically populated for ex-US sovereigns).",
    )
    vendor_ticker: Optional[str] = Field(
        default=None,
        description=(
            "Canonical vendor_ticker of the OTR bond — e.g. "
            "``/cusip/91282CKZ4`` or ``/isin/DE0001102648``."
        ),
    )
    maturity_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) of the OTR bond's maturity — the bond "
            "matures and becomes ineligible; the slot rolls to a fresh "
            "auction long before this."
        ),
    )


class OtrHistoryCurrentMetrics(BaseModel):
    """Snapshot of which sovereign cash bond is currently on-the-run.

    All identity fields are ``Optional`` because honest absence is the
    correct response when no OTR window currently covers the slot (e.g.
    the resolver has not yet run for it, or the slot has no history).
    Callers MUST handle the None branches explicitly (P6).
    """

    country: str = Field(..., description="The slot's country.")
    tenor: str = Field(..., description="The slot's tenor.")
    as_of_date: str = Field(
        ...,
        description=(
            "ISO date (YYYY-MM-DD) the snapshot was computed against — "
            "date.today() at the caller's wall clock.  Anchors the "
            "lookback window."
        ),
    )

    otr_instrument_id: Optional[int] = Field(
        default=None,
        description=(
            "FK into instrument_master for the currently-OTR bond, or "
            "``None`` when no OTR window is currently open for the slot "
            "(honest absence per P6 — never a fabricated mapping)."
        ),
    )
    cusip: Optional[str] = Field(
        default=None,
        description="CUSIP of the currently-OTR bond, or ``None`` (see otr_instrument_id).",
    )
    isin: Optional[str] = Field(
        default=None,
        description="ISIN of the currently-OTR bond, or ``None`` (see otr_instrument_id).",
    )
    vendor_ticker: Optional[str] = Field(
        default=None,
        description="vendor_ticker of the currently-OTR bond, or ``None``.",
    )
    maturity_date: Optional[str] = Field(
        default=None,
        description="Maturity ISO date of the currently-OTR bond, or ``None``.",
    )
    current_effective_from: Optional[str] = Field(
        default=None,
        description=(
            "ISO date the currently-open OTR window opened on — when the "
            "resolver first confirmed this bond as OTR for the slot.  "
            "``None`` when no window is currently open."
        ),
    )

    transition_count_in_window: int = Field(
        ...,
        ge=0,
        description=(
            "Count of SCD2 windows in the transitions list — the number "
            "of distinct bonds observed on-the-run for the slot over the "
            "lookback (including the currently-open one, if any)."
        ),
    )
    lookback_days: int = Field(
        ...,
        ge=30,
        description=(
            "Echo of the input ``lookback_days`` — surfaced on the "
            "snapshot so the methodology card can show the displayed "
            "window length without consulting the input separately."
        ),
    )


class OtrHistoryOutput(BaseModel):
    """Top-level response for the get_otr_history tool.

    ``methodology_note`` surfaces the resolver's forward-only and
    detection-date-precision limits (TD #27) at the user-facing layer
    per the PR10 "non-obvious-methodology" row — these constraints
    materially affect how callers interpret absence and roll-date
    accuracy, and are not derivable from the config alone.
    """

    current_metrics: OtrHistoryCurrentMetrics
    transitions: List[OtrHistoryTransitionRow] = Field(
        default_factory=list,
        description=(
            "Chronological list of OTR transitions (ascending by "
            "effective_from) whose effective window intersects the "
            "displayed lookback.  Empty list when honest absence "
            "applies (no resolver-observed windows for the slot in "
            "the window)."
        ),
    )
    methodology_note: str = Field(
        ...,
        description=(
            "Plain-language disclosure of the resolver's forward-only "
            "+ detection-date limits (TD #27) at the user-facing layer "
            "per PR10.  Callers consuming an absent snapshot use this "
            "to distinguish 'pre-resolver date, no data exists' from "
            "'data exists but the resolver returned None for this slot'."
        ),
    )


__all__ = [
    "OtrHistoryInput",
    "OtrHistoryTransitionRow",
    "OtrHistoryCurrentMetrics",
    "OtrHistoryOutput",
]
