"""Pydantic schemas for the otr_ofr_spread tool.

OTR/OFR (on-the-run vs first-off-the-run) yield spread for one
(country, tenor) sovereign cash-bond slot — built on the cash-bond
substrate landed by ADR 0003 + ADR 0005 + ADR 0007.

Output shape
------------
Snapshot + canonical ``TimeSeries`` — the curve_spread / yield_levels
archetype.  Per the worked-example table in
docs_revamped/02_components/primitive/README.md, ``Series`` and
``Panel`` are the two bridge-composable shapes in V1; this primitive
emits the ``Series`` shape via two canonical ``TimeSeries`` fields
(``time_series_spread`` units=BPS, ``time_series_zscore``
units=Z_SCORE) so it is workflow-composable.  The bespoke
``time_series`` array is retained for the frontend's existing
read pattern (consistent with curve_spread / yield_levels).

Validation layering
-------------------
- ``country`` and ``tenor`` are instrument selectors with no Pydantic-
  layer enum (per PR1 + PR5 — these are scoped by what
  ``macro_data.otr_history`` contains, not by an in-code closed family
  the primitive maintains).  Canonicalisation invariants (uppercase
  ISO-3166, integer-Y tenor) live in code as ``@field_validator`` per
  PR7 (invariants in code, not YAML).  The CONVENTION the rule is
  documented under is ``tenor_canonicalisation`` in config.yaml.
  Same validator shape as ``get_otr_history`` — the two primitives
  share the same (country, tenor) slot identity (P10).

- ``lookback_days`` is the central methodology knob (PR8) — controls
  the *display* window, not the z-score window (the z-score window is
  YAML-locked at 252 trading days, matching curve_spread / yield_levels
  / butterfly per PR13).

- ``field_name`` defaults to ``None``: the sentinel that resolves to
  the YAML's ``default_field_name`` convention at compute() time.
  Mirrors the yield_levels pattern; the curve_move_classifier
  wrapper-shadowing bug (commit b2605ee) is the failure mode this
  sentinel pattern pins.

PR14 — wire-format honesty
--------------------------
``rolling_window_days: int`` is included in ``current_metrics`` to
echo the z-score window used.  No output field name embeds the
window length (no ``high_252d_bps``-style methodology-encoded names),
so no NotImplementedError guard is needed on the z-score window
convention.  If a future field name embeds the window, add the guard
and update planned_extensions.
"""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from shared.schemas import TimeSeries


# Tenor pattern fixed by the ``tenor_canonicalisation`` convention
# (config.yaml + ADR 0007 §4 + ADR 0005 §3) — integer-Y form matching
# the slot labels the resolver writes.  Same regex as get_otr_history.
_TENOR_PATTERN = re.compile(r"^[1-9][0-9]*Y$")

# Country pattern fixed by the same convention — uppercase ISO-3166-alpha-2
# (and alpha-3 for the small number of resolvers that may write it).  Same
# regex as get_otr_history.
_COUNTRY_PATTERN = re.compile(r"^[A-Z]{2,3}$")


class OtrOfrSpreadInput(BaseModel):
    """Parameters the LLM extracts to query a single OTR/OFR slot."""

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
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of displayed history in the time_series "
            "output.  Defaults to 365 (1 year).  The z-score rolling "
            "window is always a fixed 252 trading days regardless of "
            "this value (config convention z_score_window_days)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for the cash-bond yield.  "
            "When None (default), the tool falls through to "
            "``default_field_name`` from config.yaml (currently "
            "'YLD_YTM_MID').  Pass an explicit field name to override "
            "per query.  LLM/HTTP wrappers MUST translate their wire-"
            "level sentinel (empty string for MCP, missing param for "
            "FastAPI) to None before constructing this input — "
            "otherwise the YAML default is silently shadowed.  See the "
            "curve_move_classifier wrapper-shadowing fix (commit "
            "b2605ee) for the canonical pattern."
        ),
    )

    # =====================================================================
    # Canonicalisation invariants — code-owned per PR7 (invariants in
    # code, not YAML).  Same validators as get_otr_history so the two
    # primitives share the (country, tenor) slot identity (P10 — single
    # source of truth for OTR slot canonicalisation).
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


class OtrOfrSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics returned for the most recent trade date.

    Identity fields (``otr_instrument_id`` / ``cusip`` etc.) carry the
    OTR and OFR bond identities at the snapshot date.  When the slot's
    most recent observed window has no prior window (first roll the
    resolver observed), ``ofr_*`` identity fields and ``ofr_yield_pct``
    are ``None`` — honest absence, never a fabricated identity.
    """

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    country: str
    tenor: str
    slot_label: str = Field(
        ...,
        description=(
            "Human-readable label for the (country, tenor) slot, "
            "e.g. 'US 10Y OTR/OFR'."
        ),
    )

    current_spread_bps: Optional[float] = Field(
        None,
        description=(
            "(OTR yield − OFR yield) × 100 at the as_of_date, in bps.  "
            "None when the prior window's OFR bond has no observation "
            "on this date (honest absence)."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None,
        description=(
            "1-day change in the OTR/OFR spread (current − previous "
            "trading day).  None if fewer than 2 observations in the "
            "display window or the previous-day spread is None."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of the OTR/OFR spread vs its own trailing "
            "252-trading-day window.  None during the rolling-window "
            "warmup."
        ),
    )
    rolling_window_days: int = Field(
        ...,
        description=(
            "Window used for the z-score calculation (matches "
            "config convention z_score_window_days)."
        ),
    )

    otr_yield_pct: Optional[float] = Field(
        None,
        description="Yield of the OTR bond at the as_of_date, in percent.",
    )
    ofr_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Yield of the OFR bond at the as_of_date, in percent.  "
            "None when no prior SCD2 window exists for the slot, or "
            "the prior bond has no observation that date."
        ),
    )

    otr_instrument_id: Optional[int] = Field(
        None,
        description=(
            "FK into macro_data.instrument_master for the OTR bond at "
            "the as_of_date.  None when no OTR window covers the date."
        ),
    )
    otr_cusip: Optional[str] = Field(
        None,
        description="CUSIP of the OTR bond (NULL for non-US sovereigns).",
    )
    otr_isin: Optional[str] = Field(
        None,
        description="ISIN of the OTR bond.",
    )

    ofr_instrument_id: Optional[int] = Field(
        None,
        description=(
            "FK into macro_data.instrument_master for the OFR bond at "
            "the as_of_date.  None when no prior SCD2 window exists."
        ),
    )
    ofr_cusip: Optional[str] = Field(
        None,
        description="CUSIP of the OFR bond (NULL for non-US sovereigns).",
    )
    ofr_isin: Optional[str] = Field(
        None,
        description="ISIN of the OFR bond.",
    )

    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Count of trading-day observations in the display time "
            "series — the length of ``time_series``."
        ),
    )


class OtrOfrSpreadTimeSeriesRow(BaseModel):
    """Single row in the OTR/OFR spread time-series array.

    ``spread_bps`` is ``None`` on dates where the OFR bond has no
    observation (the slot's first-ever observed window has no prior,
    or the prior bond has dropped out of market_data_daily).
    """

    date: str
    spread_bps: Optional[float] = None
    z_score: Optional[float] = None
    otr_yield_pct: Optional[float] = None
    ofr_yield_pct: Optional[float] = None


class OtrOfrSpreadOutput(BaseModel):
    """Top-level response for the otr_ofr_spread tool.

    Three time-series fields:
      - ``time_series`` — the bespoke wire shape (list of
        OtrOfrSpreadTimeSeriesRow), matching the curve_spread /
        yield_levels frontend read pattern;
      - ``time_series_spread`` — canonical TimeSeries (units=BPS) for
        the bridge to consume;
      - ``time_series_zscore`` — canonical TimeSeries (units=Z_SCORE)
        for the bridge to consume.

    All three are computed from the same display DataFrame and cannot
    drift — proven by the parity test in
    tests/fixtures/otr_ofr_spread_v1/.

    ``methodology_note`` surfaces TD #27 (forward-only + detection-
    date) at the user-facing layer per PR10's "non-obvious methodology"
    row — these constraints affect how callers interpret roll-date
    spreads and absent rows, and are not derivable from the config
    alone.
    """

    current_metrics: OtrOfrSpreadCurrentMetrics
    time_series: List[OtrOfrSpreadTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical OTR/OFR spread (otr_yield − ofr_yield, in BPS) "
            "over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name = "
            "'<country_lower>_<tenor_lower>_otr_ofr_spread'.  Values "
            "match ``time_series[i].spread_bps`` 1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the OTR/OFR spread vs its "
            "own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "'<country_lower>_<tenor_lower>_otr_ofr_zscore'.  Values "
            "match ``time_series[i].z_score`` 1-to-1 (None for rows in "
            "the rolling-window warmup)."
        ),
    )
    methodology_note: str = Field(
        ...,
        description=(
            "Plain-language disclosure of the resolver's forward-only "
            "+ detection-date limits (TD #27) at the user-facing layer "
            "per PR10.  Callers use this to interpret roll-day spread "
            "values that may lag the true auction date by ~1-2 days, "
            "and to distinguish 'pre-resolver date, no data exists' "
            "from 'OTR observed but no prior window yet'."
        ),
    )


__all__ = [
    "OtrOfrSpreadInput",
    "OtrOfrSpreadCurrentMetrics",
    "OtrOfrSpreadTimeSeriesRow",
    "OtrOfrSpreadOutput",
]
